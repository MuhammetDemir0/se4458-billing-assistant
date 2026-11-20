# Mobile Provider Billing Assistant

SE 4458 Software Architecture & Design of Modern Large Scale Systems, Midterm 1, Group 2 (AI Billing Support Assistant).

A conversational assistant for mobile subscribers. It answers questions about billing rules, extra data charges,
current bills, detailed usage and payments. A Gemini LLM decides which tools to use; the agent runs them:

* **MCP server**: searches the indexed *Mobile Provider Billing Guide* and returns excerpts with section numbers.
* **Billing REST API**: live usage, bills and payments in PostgreSQL. The agent always reaches it **through the API Gateway**.
* **Both** when a question mixes live data with policy, e.g. "Why is my October bill higher?".

| Item | Link |
| --- | --- |
| Source code | `https://github.com/<your-account>/<repo>` |
| API Gateway base URL | `https://<api-id>.execute-api.eu-central-1.amazonaws.com` (from `terraform output gateway_url`) |
| Web chat (agent UI) | `<gateway>/agent/chat` |
| Swagger UI | `<gateway>/api/v1/docs` · OpenAPI file: [docs/openapi.yaml](docs/openapi.yaml) |
| Demo video | `<link to the video>` |

> Demo accounts, test data and step-by-step reproduction: [Reproducing the results](#14-reproducing-the-results).

## Contents

1. [Architecture](#1-architecture)
2. [Service-oriented design](#2-service-oriented-design)
3. [Technology choices](#3-technology-choices)
4. [Billing REST API](#4-billing-rest-api)
5. [Data model (ER diagram)](#5-data-model-er-diagram)
6. [Authentication and authorization](#6-authentication-and-authorization)
7. [API Gateway](#7-api-gateway)
8. [MCP server and indexing](#8-mcp-server-and-indexing)
9. [AI agent and tool calling](#9-ai-agent-and-tool-calling)
10. [Demonstration scenarios and evidence](#10-demonstration-scenarios-and-evidence)
11. [Testing](#11-testing)
12. [Running locally](#12-running-locally)
13. [Deploying to AWS](#13-deploying-to-aws)
14. [Reproducing the results](#14-reproducing-the-results)
15. [LLM cost](#15-llm-cost)
16. [Assumptions](#16-assumptions)
17. [Limitations and future work](#17-limitations-and-future-work)

---

## 1. Architecture

```mermaid
flowchart TB
    U[User<br/>web chat / CLI] -->|HTTPS| GW

    subgraph AWS["AWS eu-central-1"]
        GW["API Gateway (HTTP API)<br/>/api/v1/* , /agent/*<br/>JWT authorizer · throttling"]
        COG[(Amazon Cognito<br/>user pool)]
        subgraph VPC["VPC"]
            subgraph PUB["public subnets · inbound only from VPC link / agent"]
                AG["Agent service<br/>(ECS Fargate)"]
                API["Billing REST API<br/>(ECS Fargate)"]
                MCP["MCP server<br/>(ECS Fargate)"]
            end
            subgraph PRIV["private subnets · no internet route"]
                DB[(RDS PostgreSQL)]
            end
        end
        SM[(Secrets Manager)]
    end

    LLM[Google Gemini API]

    GW -->|VPC link · Cloud Map| API
    GW -->|VPC link · Cloud Map| AG
    GW -. validates JWT .-> COG
    AG <-->|prompts / tool selection| LLM
    AG -->|"REST tool calls (user's JWT)"| GW
    AG -->|"MCP client · Streamable HTTP<br/>private DNS, bearer token"| MCP
    API --> DB
    SM -. injected at start .-> API & AG & MCP
```

What the diagram enforces:

* **Nothing is exposed directly.** The only public entry point is the API Gateway. ECS tasks accept inbound
  traffic only from the gateway's VPC link security group (Billing API, agent) or from the agent (MCP server).
  The database sits in private subnets and accepts connections only from the Billing API.
* **The LLM never calls a backend.** It only selects tools. The agent executes them and returns the results.
* **REST path and MCP path are separate.** REST calls go agent → API Gateway → Billing API. MCP calls go agent
  (MCP client) → MCP server over a private network, not through the gateway.
* **The policy document is indexed before any question** (at container build time), never per question.

Request flow for a combined question:

```mermaid
sequenceDiagram
    actor S as Subscriber
    participant G as API Gateway
    participant A as Agent
    participant L as Gemini
    participant M as MCP server
    participant B as Billing API
    S->>G: POST /agent/v1/chat (Bearer JWT)
    G->>G: JWT authorizer (Cognito)
    G->>A: forward
    A->>L: question + tool declarations
    L-->>A: call get_bill_details(10, 2026) + search_billing_documentation("extra data charges")
    A->>G: GET /api/v1/subscribers/{me}/bills/2026/10/details (user's JWT)
    G->>B: forward (authorized)
    B-->>A: usage 35 GB, charges
    A->>M: call_tool search_billing_documentation
    M-->>A: §3.2 Additional internet blocks
    A->>L: tool results
    L-->>A: grounded answer with section references
    A-->>S: answer + tool trace
```

## 2. Service-oriented design

The system is split into **three independently deployable services**, each with its own contract, Dockerfile,
dependencies and tests. They communicate only through network contracts; no service reads another's database or code.

| Service | Contract | Responsibility | Talks to |
| --- | --- | --- | --- |
| `services/billing-api` | REST, OpenAPI 3 (`/api/v1`) | usage, bill calculation, bills, payments | PostgreSQL |
| `services/mcp-server` | MCP tool `search_billing_documentation` | indexed billing-guide search | nothing (owns its index) |
| `services/agent` | REST (`/agent/v1`) | conversation, LLM tool calling, payment confirmation | Gemini, MCP server, API Gateway |

Inside the Billing API the code follows the layered design from the lecture ("Ideal Class Diagram": controller →
service interface → service → data access, DTO separate from model, dependency injection):

```mermaid
flowchart LR
    C["Controllers<br/>api/v1/routers/*.py"] -->|DTOs<br/>api/v1/schemas.py| I["Service interfaces<br/>services/interfaces.py<br/>IUsageService · IBillingService · IPaymentService"]
    I -.implemented by.-> S["Services<br/>usage_service · billing_service · payment_service"]
    S --> D["Domain<br/>domain/models.py · domain/pricing.py"]
    S --> RI["Repository interfaces + Unit of Work<br/>repositories/interfaces.py"]
    RI -.implemented by.-> R["SQLAlchemy repositories<br/>repositories/sqlalchemy_repositories.py"]
    R --> O["ORM models<br/>db/orm_models.py"] --> PG[(PostgreSQL)]
```

| Lecture concept | Where it is |
| --- | --- |
| Controller (`CourseControllerWebAPI`) | `app/api/v1/routers/` — HTTP only: parse, authorize, call service, map to DTO |
| Service interface (`ICourseService`) | `app/services/interfaces.py` (`typing.Protocol`) |
| Service (`CourseService`) | `app/services/*_service.py` — business rules, no HTTP or SQL |
| Data access (`CourseAccess`) | `app/repositories/` — `IBillRepository`, … + PostgreSQL implementation |
| DTO vs model (`CourseDTO` / `CourseModel`) | `app/api/v1/schemas.py` (DTO) · `app/domain/models.py` (entity) · `app/db/orm_models.py` (table) |
| Dependency injection | `app/api/deps.py` — FastAPI `Depends` builds UoW → service per request; tests swap in fakes |

The benefit is visible in the tests: the whole business layer runs against in-memory repositories
(`tests/fakes.py`) with no database, and the same services run unchanged against PostgreSQL in production.
The agent follows the same idea: `LLMClient` and `ToolProvider` are interfaces with Gemini/MCP/REST
implementations and scripted fakes for tests.

```
services/
  billing-api/   app/{api,core,domain,services,repositories,db}  migrations/  tests/{unit,api,integration}
  mcp-server/    app/{server,indexer,bm25,text,auth}.py  data/Mobile_Provider_Billing_Guide.md  tests/
  agent/         app/{main,orchestrator,prompts,payments,conversations,identity,trace,cli}.py
                 app/llm/{base,gemini,scripted}.py  app/tools/{base,mcp_tools,billing_tools}.py  tests/
gateway/kong/kong.yml        local equivalent of the AWS gateway
infra/terraform/             AWS infrastructure as code
scripts/                     deploy, seed, demo users, smoke test, demo scenarios
docs/                        openapi.yaml, evidence/, screenshots/
```

## 3. Technology choices

| Concern | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12 for all services | official MCP SDK is most mature in Python; one language for three services |
| REST framework | FastAPI | typed contracts, automatic OpenAPI/Swagger, dependency injection |
| Database | PostgreSQL 16 (Amazon RDS) | server database, transactions, row locking, `NUMERIC` money |
| ORM / migrations | SQLAlchemy 2 + Alembic | versioned schema; migrations run on start-up behind an advisory lock |
| MCP | official `mcp` SDK (FastMCP), Streamable HTTP | real MCP server and client, stateless HTTP fits containers |
| LLM | Google Gemini 3.6 Flash + 3.5 Flash-Lite fallback (`google-genai`) | reliable function calling, free tier; chosen by testing (see §15) |
| Identity | Amazon Cognito (OAuth 2.0 / OIDC JWT) | managed users and groups, JWKS for verification |
| Gateway | AWS API Gateway HTTP API (local: Kong) | JWT authorizer, throttling, private VPC-link integration |
| Compute | Amazon ECS Fargate + Cloud Map | each service is a container, no servers to manage |
| Infrastructure | Terraform | reproducible, reviewable, one command to create or destroy |

## 4. Billing REST API

All operations are versioned under `/api/v1` (a v2 would be a new router package and a new gateway prefix).
Interactive documentation: **Swagger UI at `/api/v1/docs`**, spec at `/api/v1/openapi.json` and [docs/openapi.yaml](docs/openapi.yaml).

| Operation | Method and path | Request | Response | Auth (spec) | Paging (spec) |
| --- | --- | --- | --- | --- | --- |
| Add Usage | `POST /api/v1/subscribers/{subscriberNo}/usage` | `{month, year, usageType: PHONE\|INTERNET}` | transaction status + record | YES (`billing-admin`) | NO |
| Calculate Bill | `POST /api/v1/subscribers/{subscriberNo}/bills/{year}/{month}/calculate` | – | transaction status + bill | YES (`billing-admin`) | NO |
| Query Bill | `GET /api/v1/subscribers/{subscriberNo}/bills/{year}/{month}` | – | total, paid, remaining, status | **NO** | NO |
| Query Bill Detailed | `GET /api/v1/subscribers/{subscriberNo}/bills/{year}/{month}/details?page&pageSize` | – | summary + minutes, MB/GB, charge breakdown, paged usage records | YES (owner or admin) | **YES** |
| Pay Bill | `POST /api/v1/subscribers/{subscriberNo}/bills/{year}/{month}/payments` | `{amount, transactionId}` | payment status, paid amount, remaining balance | YES (owner) | NO |
| Health | `GET /api/v1/health` | – | service + database status | NO | – |

Business rules (Billing Guide §2–§5), implemented in the pure function `app/domain/pricing.py`:

* One Add Usage call records **10 minutes** (PHONE) or **1 MB** (INTERNET).
* Phone = `$10 × ceil(max(0, minutes − 1000) / 1000)`. Internet = `$50 + $10 × ceil(max(0, MB − 20000) / 10000)`, **1 GB = 1000 MB**. No taxes or fees.
  Guide example: 1,350 min + 35 GB = $10 + $70 = **$80**.
* Calculate Bill stores the result. It is idempotent: recalculating keeps payments already made; it is rejected (409) if the new total would be below the amount already paid.
* Pay Bill records a simulated partial or full payment (no card processing). It rejects: duplicate `transactionId` (409), amount above the remaining balance (422), payment on a fully paid bill (422), more than two decimals (400). The bill row is locked with `SELECT … FOR UPDATE` before any check, so two concurrent payments cannot overpay, and a retried request with the same `transactionId` gets 409 even when both arrive at the same moment; the `UNIQUE` constraint is the final guard and is mapped to 409, never 500 (all tested against PostgreSQL).
* Status: `UNPAID` → `PARTIALLY_PAID` → `PAID`.

Errors use RFC 7807 `application/problem+json` with a machine-readable `code` and the request id:

```json
{
  "type": "https://errors.billing.example/subscriber-forbidden",
  "title": "Access denied",
  "status": 403,
  "detail": "You can only access your own subscriber account.",
  "code": "SUBSCRIBER_FORBIDDEN",
  "instance": "/api/v1/subscribers/5551000002/bills/2026/10/details",
  "requestId": "b0c1f7c2-1d8e-4f43-9f0e-5a8f3f4f2a11"
}
```

| HTTP | Codes |
| --- | --- |
| 400 | `VALIDATION_ERROR` (with `errors[]` per field) |
| 401 | `UNAUTHORIZED`, `INVALID_TOKEN`, `TOKEN_EXPIRED` |
| 403 | `ADMIN_REQUIRED`, `SUBSCRIBER_FORBIDDEN` |
| 404 | `SUBSCRIBER_NOT_FOUND`, `BILL_NOT_FOUND` (bill not calculated yet) |
| 409 | `DUPLICATE_TRANSACTION`, `TOTAL_BELOW_PAID_AMOUNT`, `DUPLICATE_RECORD` |
| 422 | `AMOUNT_EXCEEDS_BALANCE`, `BILL_ALREADY_PAID` |

## 5. Data model (ER diagram)

```mermaid
erDiagram
    SUBSCRIBERS ||--o{ USAGE_RECORDS : records
    SUBSCRIBERS ||--o{ BILLS : "is billed"
    BILLS ||--o{ PAYMENTS : "is paid by"

    SUBSCRIBERS {
        varchar subscriber_no PK "10 digits"
        varchar full_name
        timestamptz created_at
    }
    USAGE_RECORDS {
        int id PK
        varchar subscriber_no FK
        int year
        int month "1-12"
        varchar usage_type "PHONE | INTERNET"
        int quantity "10 or 1"
        varchar unit "MINUTE | MB"
        timestamptz recorded_at
    }
    BILLS {
        int id PK
        varchar subscriber_no FK
        int year
        int month
        int phone_minutes
        int internet_mb
        numeric phone_charge "12,2"
        numeric internet_charge "12,2"
        numeric total "12,2"
        numeric paid_amount "0..total"
        varchar status "UNPAID | PARTIALLY_PAID | PAID"
        timestamptz calculated_at
    }
    PAYMENTS {
        int id PK
        int bill_id FK
        varchar transaction_id UK "idempotency key"
        numeric amount "> 0"
        timestamptz created_at
    }
```

Constraints in the database (not only in code): `UNIQUE(subscriber_no, year, month)` on bills,
`UNIQUE(transaction_id)` on payments, `CHECK 0 <= paid_amount <= total`, `CHECK amount > 0`, `CHECK month 1–12`,
and an index on `usage_records(subscriber_no, year, month)`. Schema: `services/billing-api/migrations/`.

## 6. Authentication and authorization

* **Identity provider:** Amazon Cognito user pool, OAuth 2.0 / OIDC **JWT access tokens** (RS256, 60 min).
  Username = subscriber number. Groups = roles: `subscriber`, `billing-admin`. No public sign-up.
* **Gateway:** a JWT authorizer validates issuer, signature, expiry and client on every protected route.
* **Service (defense in depth):** the Billing API verifies the token again (Cognito JWKS) and applies the rules:
  * Query Bill Detailed: the token's subscriber must equal `{subscriberNo}`, or the caller is `billing-admin`.
  * Pay Bill: only the account owner.
  * Add Usage, Calculate Bill: `billing-admin` only (operations of the provider's systems, not of subscribers).
* **The agent can never read another user's private data because a number was typed in the prompt.**
  The subscriber number for every REST call comes from the signed-in user's token; the agent's tool schemas do not
  even have a subscriber parameter, extra arguments invented by the model are ignored, and the API would answer 403 anyway.
  (Tested: `test_subscriber_number_in_prompt_or_args_is_ignored`, `test_query_bill_detailed_other_subscriber_forbidden`.)
* **Explicit confirmation before payment:** see [§9](#9-ai-agent-and-tool-calling). The LLM has no tool that pays.
* Query Bill is public **as required by the specification**. It returns only the summary; the gateway throttles it.
  The agent still uses only the signed-in subscriber's number for it.
* Secrets live in AWS Secrets Manager. The Gemini key and MCP token are injected by ECS at start-up.
  The RDS master password is generated **and rotated** by RDS (every 7 days by default) and never appears in code or
  Terraform state. Because a value injected once would break at the first rotation, the Billing API reads the secret
  when it opens a new connection (cached 5 min, refreshed and retried once if PostgreSQL rejects it —
  `app/db/credentials.py`, tested against a real PostgreSQL). Its task role may read only that one secret. Database
  connections use TLS (`sslmode=require`).
* **Fail-safe configuration:** both services refuse to start with `AUTH_MODE=local` outside a local environment, so the
  public development secret can never protect a deployed service.

## 7. API Gateway

**AWS API Gateway (HTTP API)**, `$default` stage, private integrations through a **VPC link** to AWS Cloud Map
services (ECS tasks have no public endpoint). Gateway URL: see the table at the top (`terraform output gateway_url`).

| Public route (gateway) | Target service | Protection | Throttling (rate/burst) |
| --- | --- | --- | --- |
| `GET /api/v1/subscribers/{subscriberNo}/bills/{year}/{month}` | billing-api | public (spec) | 5 / 10 |
| `GET /api/v1/subscribers/{subscriberNo}/bills/{year}/{month}/details` | billing-api | **JWT** | 10 / 20 |
| `POST /api/v1/subscribers/{subscriberNo}/bills/{year}/{month}/payments` | billing-api | **JWT** | 10 / 20 |
| `POST /api/v1/subscribers/{subscriberNo}/bills/{year}/{month}/calculate` | billing-api | **JWT** (+ admin in service) | 10 / 20 |
| `POST /api/v1/subscribers/{subscriberNo}/usage` | billing-api | **JWT** (+ admin in service) | 10 / 20 |
| `GET /api/v1/health`, `/api/v1/docs`, `/api/v1/openapi.json` | billing-api | public | 10 / 20 |
| `GET /agent`, `GET /agent/chat`, `GET /agent/v1/health` | agent | public | 10 / 20 |
| `POST /agent/v1/auth/login` | agent | public | 2 / 5 |
| `POST /agent/v1/chat` | agent | **JWT** | 10 / 20 |
| `POST /agent/v1/payments/{intentId}/{action}` | agent | **JWT** | 10 / 20 |

The paths are passed to the services unchanged. Any route not in the table returns `404` from the gateway.
Group 2's specification defines no per-user quota, so throttling here is protective; it is applied consistently on the
stage and tightened on unauthenticated routes. Access logs (request id, route, status, user) go to CloudWatch.

Example error responses from the gateway:

```http
GET /api/v1/subscribers/5551000001/bills/2026/10/details          (no token)
HTTP/1.1 401 Unauthorized
{"message":"Unauthorized"}

GET /api/v1/subscribers/5551000002/bills/2026/10/details          (token of 5551000001)
HTTP/1.1 403 Forbidden            -> application/problem+json, code SUBSCRIBER_FORBIDDEN (see §4)

(over the throttle)
HTTP/1.1 429 Too Many Requests
{"message":"Too Many Requests"}
```

Locally the same routes, JWT checks and rate limits are configured in Kong DB-less
([gateway/kong/kong.yml](gateway/kong/kong.yml)), and Docker networks mirror the security groups.
Evidence: [docs/evidence/gateway-smoke-local.json](docs/evidence/gateway-smoke-local.json),
[docs/evidence/gateway-401-example-local.txt](docs/evidence/gateway-401-example-local.txt), `docs/evidence/aws/`.

## 8. MCP server and indexing

A real MCP server built with the official Python SDK (`FastMCP`), Streamable HTTP transport (stateless),
`http://mcp.billing.internal:8000/mcp` inside the VPC, protected by a bearer token. Also runs over stdio
(`python -m app.server --stdio`) for MCP Inspector. Details: [services/mcp-server/README.md](services/mcp-server/README.md).

**Tool schema**

```json
{
  "name": "search_billing_documentation",
  "description": "Search the Mobile Provider Billing Guide for policy and pricing passages. Returns the most relevant sections with section number, heading, parent heading, text and score.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "Natural-language question or keywords"},
      "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3}
    },
    "required": ["query"]
  }
}
```

Result: `{document, document_id: "MOB-BILLING-2026-01", query, matches: [{section, heading, parent_heading, text, score}]}`.

**Indexing approach** (full-text, no embeddings needed for a 9-section document):

1. **Chunking:** the guide is split on `##` / `###` headings. Each section with body text becomes one chunk carrying
   its section number (`3.2`), heading and parent heading, so every result can be cited.
2. **Normalisation:** lowercase, alphanumeric tokens, stopword removal, light suffix stemming; heading words are
   weighted higher than body words.
3. **Query expansion:** a small synonym map (extra↔additional, data/GB→internet, late→due/overdue, cost→charge, …).
4. **Ranking:** Okapi **BM25** (k1 = 1.5, b = 0.75), implemented in `app/bm25.py` (~50 lines, no search library).
5. **Index once:** `python -m app.indexer` builds `data/index.json` **at Docker build time**; the server loads it at
   start-up and only scores queries. Nothing is re-indexed per question.

**Three distinct retrieval tests** (run with the agent's MCP client: [docs/evidence/mcp-retrieval-local.txt](docs/evidence/mcp-retrieval-local.txt);
also automated in `services/mcp-server/tests/test_retrieval.py`):

| # | Query | Top sections returned |
| --- | --- | --- |
| 1 | How much does extra internet cost? | 8.1 What is the cost of extra internet? · **3.2 Additional internet blocks** · 2.2 |
| 2 | Can I pay only part of my bill? | 8.3 Can I pay only part of my bill? · **5.1 Full and partial payments** · 5.3 |
| 3 | When is my bill due and what happens if I pay late? | 6.2 Late payment reminder · 6.3 Service restriction · **6.1 Due date** |

## 9. AI agent and tool calling

The agent (`services/agent/app/orchestrator.py`) runs a plain, readable tool-calling loop (no agent framework):

1. The question, a system prompt (today's date, the signed-in subscriber, grounding rules) and the **tool
   declarations** are sent to Gemini. Automatic function calling is disabled.
2. Gemini returns function calls (tool name + JSON arguments) or a final answer. **Routing is decided by the LLM**,
   not by keywords.
3. The agent executes each call: MCP tools with the MCP client, REST tools with the Billing API client **via the API
   Gateway** using the user's access token and the request's `X-Request-ID`.
4. Results go back as function responses; the loop repeats (max 5 rounds) until Gemini answers with text, citing
   guide sections such as "(Billing Guide §3.2)".

| Tool offered to the LLM | Source | Backend call |
| --- | --- | --- |
| `search_billing_documentation(query, top_k)` | discovered from the MCP server with `list_tools` | MCP `call_tool` |
| `get_bill(month, year)` | REST | `GET /api/v1/subscribers/{me}/bills/{y}/{m}` |
| `get_bill_details(month, year, page, page_size)` | REST | `GET …/details` |
| `prepare_bill_payment(month, year, amount?)` | agent | `GET` bill, then creates a payment request — **does not pay** |

**Explicit payment confirmation, enforced in code.** There is no "pay" tool for the LLM. `prepare_bill_payment`
validates the amount against the live balance and creates a 5-minute payment intent showing subscriber, period, amount
and current balance. The Pay Bill API is called only when the user presses **Confirm** in the web chat (or types `YES`
in the CLI), which calls `POST /agent/v1/payments/{intentId}/confirm`. The intent belongs to that user only, can be
used once, and its id is sent as the `transactionId`, so a retry can never pay twice. A question like "Can I pay $40?"
can therefore never move money (tests: `test_payment_requires_explicit_confirmation`, `test_confirmation_cannot_be_replayed`).
Following guide §5.2, such questions are answered as information (policy + live balance) with an offer to prepare the
payment; only an explicit instruction ("Pay $20 of my October bill") creates a payment request.

**Tool trace.** Every answer carries a trace (shown next to the chat, printed by the CLI, logged as JSON):

```json
[
  {"step": 1, "kind": "llm",  "name": "gemini-3.6-flash", "output": {"toolCalls": [{"name": "get_bill_details", "args": {"month": 10, "year": 2026}}, {"name": "search_billing_documentation", "args": {"query": "extra data charges"}}]}},
  {"step": 2, "kind": "rest", "name": "get_bill_details", "input": {"month": 10, "year": 2026},
   "http": [{"method": "GET", "path": "/api/v1/subscribers/5551000001/bills/2026/10/details", "status": 200}],
   "output": {"billTotal": 80.0, "charges": {"internet": {"gbUsed": 35.0, "extraBlocks": 2, "amount": 70.0}}}},
  {"step": 3, "kind": "mcp",  "name": "search_billing_documentation", "input": {"query": "extra data charges"},
   "output": {"matches": [{"section": "3.2", "heading": "Additional internet blocks"}]}},
  {"step": 4, "kind": "llm",  "name": "gemini-3.6-flash", "output": {"text": "Your internet usage was 35 GB …"}}
]
```

Other safeguards: tool output is treated as data (prompt-injection rule in the system prompt), conversation history is
kept server-side per user (a client cannot forge earlier tool results), invalid arguments and API errors are returned
to the model so it can explain them ("your November bill has not been calculated yet"), and LLM outages return a
friendly message without corrupting the conversation.

## 10. Demonstration scenarios and evidence

| # | Scenario | Example question | Tools chosen | Evidence |
| --- | --- | --- | --- | --- |
| 1 | MCP only | How much does extra internet cost? | `search_billing_documentation` | `docs/evidence/aws/1-mcp-only.json`, screenshot |
| 2 | REST only | What is my October 2026 bill? | `get_bill` | `docs/evidence/aws/2-rest-only.json`, screenshot |
| 3 | MCP + REST | Why is my October bill higher than September? | `get_bill_details` (Oct, Sep) + `search_billing_documentation` | `docs/evidence/aws/3-combined.json`, screenshot |
| 4 | Confirmed payment | Pay my October 2026 bill → **Confirm** | `prepare_bill_payment`, then user confirmation → Pay Bill API | `docs/evidence/aws/4-payment.json`, screenshot |
| 5 | Security | Show the detailed bill of subscriber 5551000002 | own account only | `docs/evidence/aws/5-other-subscriber.json` |
| – | 3 MCP retrieval tests | see §8 | – | `docs/evidence/mcp-retrieval-*.txt` |
| – | Protected gateway route | 401 without token, 403 for another subscriber | – | `docs/evidence/gateway-smoke-*.json` |
| – | Unit tests | – | – | [docs/evidence/test-run.txt](docs/evidence/test-run.txt), CI |

Screenshots are in [docs/screenshots/](docs/screenshots/). The traces are produced by
`python scripts/demo_scenarios.py --gateway <url> --password <pw> --out docs/evidence/aws --confirm-payment`.
The same scenarios run against the local stack are in [docs/evidence/local/](docs/evidence/local/).

## 11. Testing

All tests are automatic and need **no cloud and no live LLM** (pytest; the LLM is replaced by a scripted fake, the
gateway by `httpx.MockTransport`, the database by in-memory repositories; an in-process MCP server is used for the MCP client).

| Service | Tests | What is verified |
| --- | --- | --- |
| billing-api | 71 unit/API + 6 integration | pricing boundaries (1000/1001/2001 min, 20/20.001/30/35 GB, zero usage), every operation success + failure (400/401/403/404/409/422), ownership rules, token expiry/signature/type, paging, recalculation, duplicate transaction, overpayment, versioned docs, rotating DB credentials, fail-safe auth config; integration (real PostgreSQL): Alembic migration, SQL aggregation, **concurrent payments with row locking**, concurrent retry with the same transaction id → 409 (never 500), reused id on another bill, password-rotation reconnect |
| mcp-server | 41 | chunking and section ids, tokenizer/synonyms, BM25 ranking, three retrieval queries, tool validation, bearer-token middleware, real MCP client/server round trip |
| agent | 37 | MCP-only / REST-only / combined routing, gateway URL + user token on REST calls, other subscriber number ignored, payment needs confirmation, no replay, other user cannot confirm, amount validation, retry after failure, API errors to LLM, round limit, LLM outage, fallback model on 503/504 with signature-safe history, auth endpoints, Gemini conversions |

Run them (per service):

```bash
cd services/billing-api            # or services/mcp-server, services/agent
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v --cov=app
```

Integration tests of the Billing API run when a PostgreSQL URL is given (they wipe that database):

```bash
docker compose up -d postgres
TEST_DATABASE_URL=postgresql+psycopg://billing:billing@127.0.0.1:5432/billing_test pytest -m integration
```

Latest run: **155 passed** (billing-api 77, mcp-server 41, agent 37), coverage 91% / 93% / 87% —
full output in [docs/evidence/test-run.txt](docs/evidence/test-run.txt). GitHub Actions
([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs lint, all tests (with a PostgreSQL service container) and
`terraform validate` on every push.

## 12. Running locally

Requirements: Docker Desktop, Python 3.12+ (only for tests/scripts), a Gemini API key from Google AI Studio.

```bash
cp .env.example .env                      # set GEMINI_API_KEY
docker compose up -d --build              # postgres, billing-api, mcp-server, agent, kong
docker compose run --rm billing-api seed  # demo data
python scripts/smoke_test.py              # 11 gateway checks
python scripts/demo_scenarios.py --out docs/evidence/local --confirm-payment
docker compose exec agent python -m app.mcp_demo   # three MCP retrieval tests
```

Open `http://localhost:8080/agent/chat` (local users: `5551000001` / `Demo#2026!`, `5551000002` / `Demo#2026!`;
these exist only in local mode). Swagger: `http://localhost:8080/api/v1/docs`. Only the gateway port is published;
the services are not reachable directly. Local deployment exists for development; the graded deployment is AWS.

## 13. Deploying to AWS

Prerequisites: AWS account with an IAM Identity Center/IAM user (not root), AWS CLI v2 (`aws configure sso` or
`aws configure`), Terraform ≥ 1.6, Docker Desktop.

```powershell
cd infra/terraform
copy terraform.example.tfvars terraform.tfvars        # set alert_email
terraform init
terraform apply                                       # ~10 min (RDS is the slowest)
cd ../..
./scripts/set_gemini_key.ps1                          # stores the key in Secrets Manager (hidden input)
./scripts/deploy.ps1                                  # build, push to ECR, roll ECS services
./scripts/create_demo_users.ps1                       # Cognito users with passwords you choose
./scripts/seed.ps1                                    # demo data via a one-off ECS task inside the VPC
python scripts/smoke_test.py --gateway (terraform -chdir=infra/terraform output -raw gateway_url) --password "<pw>"
```

What Terraform creates: VPC (2 public + 2 private subnets), security groups, RDS PostgreSQL 16 (db.t4g.micro,
encrypted, private), ECR repositories, ECS cluster with 3 Fargate services (0.25 vCPU / 0.5 GB), Cloud Map namespace,
API Gateway HTTP API with VPC link, JWT authorizer, routes and throttling, Cognito user pool, Secrets Manager secrets,
least-privilege IAM roles (separate role for the Billing API), CloudWatch log groups and an AWS Budgets alert.
ECS deployments use the deployment circuit breaker: a new version that cannot become healthy is rolled back automatically.

**Cost** (eu-central-1, approximate): Fargate 3 tasks ≈ $0.90/day, public IPv4 ≈ $0.36/day, RDS db.t4g.micro ≈
$0.40/day (free tier if eligible), API Gateway/Cloud Map/Secrets/Logs a few cents → **≈ $1.5/day**.
Pause without deleting: `terraform apply -var desired_count=0`. Delete everything: `terraform destroy`.

## 14. Reproducing the results

**Demo accounts on AWS** (fictitious data only; valid until the environment is destroyed after grading):

| Username | Password | Role |
| --- | --- | --- |
| 5551000001 | `<demo-subscriber-password>` | subscriber (main demo account) |
| 5551000002 | `<demo-subscriber-password>` | subscriber (second account) |
| ops-admin | `<demo-admin-password>` | billing-admin |

1. Open `<gateway>/agent/chat` and sign in as **5551000001**.
2. Ask the four questions of [§10](#10-demonstration-scenarios-and-evidence) and watch the trace panel.
   For the payment, press **Confirm**; the next "What is my October bill?" shows the new balance.
3. Ask "Show the detailed bill of subscriber 5551000002" — the assistant only shows the signed-in account.
4. Gateway checks without the UI: `python scripts/smoke_test.py --gateway <gateway> --password <pw>`.
5. Swagger: `<gateway>/api/v1/docs` → **Authorize** with an access token
   (`POST <gateway>/agent/v1/auth/login` returns one).

Test data (loaded by `seed`; all fictitious):

| Subscriber | Period | Usage | Stored bill | State |
| --- | --- | --- | --- | --- |
| 5551000001 | 2026-09 | 800 min, 18 GB | $50 | paid |
| 5551000001 | 2026-10 | 1,350 min, 35 GB | $80 ($10 phone + $70 internet) | unpaid — "why higher?" and payment demo |
| 5551000001 | 2026-11 | 300 min, 2 GB | not calculated | shows "bill not calculated yet" |
| 5551000002 | 2026-10 | 2,100 min, 12 GB | $70 | used for the cross-account denial |
| ops-admin | – | – | – | `billing-admin`: Add Usage / Calculate Bill in Swagger |

To create new usage as an operator: sign in as `ops-admin`, call Add Usage, then Calculate Bill.

## 15. LLM cost

* **Model choice.** Candidate models available to the project's API key were tested with the agent's real tool
  declarations (policy question and "why is my bill higher?" question):

  | Model | Result (Nov 2026, free tier) |
  | --- | --- |
  | gemini-2.5-flash | no longer available to new API keys (404) |
  | gemini-3.8-flash, gemini-3.7-flash | correct API, but repeatedly overloaded (503/504) |
  | **gemini-3.6-flash** (primary) | correct tools; for "higher" it fetched Oct + Sep details **and** searched the guide |
  | **gemini-3.5-flash-lite** (fallback) | correct tools, ~0.6 s, no thinking tokens |
  | gemini-4 preview models | no free-tier quota (429) or timeouts |

  Both are configurable (`GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL`, Terraform `gemini_model`, `gemini_fallback_model`).
* **Resilience.** Each LLM call has a 20 s timeout (below the 29 s gateway limit). If the primary model answers 429/5xx
  or times out, the same request goes to the fallback model and the agent stays on it for two minutes (Gemini 3
  models expect their own thought signatures on the current turn's function calls). The trace shows which model answered.
* **Cost.** The **Gemini API free tier** covers the demo, so the expected LLM cost is **$0**. Free-tier requests are
  rate-limited per minute and per day; the agent reports rate limits politely instead of failing.
* A typical question uses 2 LLM calls (tool selection + answer) with ~1.5–3k input and ~200–500 output tokens. On the
  paid tier this is roughly a tenth of a cent per question (see Google's current price list).
* Cost controls: max 5 tool rounds per question, `max_output_tokens`, trimmed history, large tool results
  (e.g. 35,000 usage records) are paged and shortened before they reach the model.
* **Testable without cost:** all automated tests use a scripted LLM; no key is needed for `pytest`.

## 16. Assumptions

**Billing and rounding**
* One Add Usage call = 10 minutes or 1 MB, as specified; demo usage is bulk-loaded by the seed script.
* Partial blocks are always charged as full blocks (guide §2.2, §3.2); all arithmetic is integer/Decimal, money has 2 decimals.
* A month with no usage still costs the $50 internet base (guide §3.1).
* Billing period = calendar month; usage can be recorded for any month 1–12 of years 2000–2100.
* Recalculation is allowed and keeps payments; a total lower than the paid amount is rejected (409).
* Overpayment is not supported: amount must be ≤ remaining balance.

**Policy interpretation**
* The guide is the only policy source; the assistant never presents an estimate as a stored bill (guide §4.4) and does not
  promise dispute outcomes (§7.3). There is no Dispute API (§7.2), so disputes are only explained.
* Due dates, reminders and restrictions are explained from the guide; they are not computed or enforced by the API.

**API behaviour and errors**
* Validation errors return 400; business-rule violations 422; duplicates/conflicts 409; missing bill 404.
* Query Bill returns 404 until Calculate Bill has been run for that month.
* Paging applies to the usage records of Query Bill Detailed (`page` ≥ 1, `pageSize` 1–100, default 20).

**Identity**
* Username = subscriber number. Subscribers read and pay only their own bills; `billing-admin` represents the mobile
  provider's back-office systems (Add Usage, Calculate Bill) and may read any detail but cannot pay for others.
* Query Bill has no authentication because the specification says so.

**Deployment**
* AWS eu-central-1, single-AZ RDS and one task per service to keep cost low; ECS tasks run in public subnets with
  inbound traffic restricted by security groups instead of NAT gateways (cost decision, documented in `network.tf`).
* Agent conversation state and payment intents are in memory of a single agent task.
* Terraform state is local and git-ignored.
* The Billing API connects as the RDS master user to keep the setup small; production would add a dedicated
  application role with only DML rights (migrations keep the owner role).

## 17. Limitations and future work

* **Event-driven usage ingestion.** In a real operator, usage events arrive in huge volumes. Add Usage would publish to
  a queue (Amazon SQS/Kafka) and a consumer would aggregate them, decoupling producers from the database
  (lecture: event and message driven architecture). Kept synchronous here because the specification defines a REST call
  per event and the volume is tiny.
* Multiple agent tasks would need shared state (Redis/DynamoDB with TTL) for conversations and payment intents.
* Production hardening: Multi-AZ RDS, private subnets with NAT or VPC endpoints, AWS WAF in front of the gateway,
  custom domain and TLS certificate, alarms on 5xx and latency.
* Embedding-based retrieval could be added if the documentation grows; BM25 is sufficient for one short guide.
