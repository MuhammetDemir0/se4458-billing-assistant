# Billing Documentation MCP Server

An MCP server built on the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
(`mcp` 1.30, `FastMCP`). It indexes the **Mobile Provider Billing Guide**
(`data/Mobile_Provider_Billing_Guide.md`, document ID `MOB-BILLING-2026-01`) and exposes one tool,
`search_billing_documentation`, which returns the most relevant guide sections **with their section
numbers**, so the AI agent can cite policy rules (for example "see Section 3.2").

The guide holds policy and pricing rules only. Live usage, bill totals and payments come from the
Billing REST API through the API Gateway. The agent reaches this server directly and privately over
MCP Streamable HTTP, not through the gateway.

```
AI agent ──(MCP Streamable HTTP, Bearer token, private network)──► mcp-server :8000/mcp
         ──(REST, API Gateway)────────────────────────────────────► billing-api
```

## Tool: `search_billing_documentation`

| Argument | Type | Default | Constraints |
|----------|------|---------|-------------|
| `query`  | string  | (required) | non-empty after trimming, ≤ 500 characters |
| `top_k`  | integer | `3` | 1–10 |

Input schema (as reported by `tools/list`):

```json
{
  "type": "object",
  "required": ["query"],
  "properties": {
    "query": {"type": "string", "description": "Natural-language question or keywords"},
    "top_k": {"type": "integer", "default": 3, "minimum": 1, "maximum": 10,
              "description": "Number of sections to return (1-10)"}
  }
}
```

Result (returned as `structuredContent`, plus the same JSON as a text content block):

```json
{
  "document": "Mobile_Provider_Billing_Guide.md",
  "document_id": "MOB-BILLING-2026-01",
  "query": "How much does extra internet cost?",
  "matches": [
    {
      "section": "8.1",
      "heading": "What is the cost of extra internet?",
      "parent_heading": "Frequently asked questions",
      "text": "Each additional 10 GB block beyond 20 GB costs $10. Partially used blocks are rounded up. See Section 3.2.",
      "score": 9.8476
    },
    {
      "section": "3.2",
      "heading": "Additional internet blocks",
      "parent_heading": "Internet pricing",
      "text": "Each additional **10 GB block** costs **$10**. ...",
      "score": 8.8226
    }
  ]
}
```

`matches` is ordered by score, best first. Only sections with a positive score are returned, so an
unrelated query gives an empty list. An empty query or an out-of-range `top_k` returns an MCP tool
error (`isError: true`).

## Indexing

Indexing runs **once, before any query**: `python -m app.indexer` writes `data/index.json`, and the
Docker image runs it at build time. At startup the server loads that file and only scores queries
against it. It never re-indexes per query. If the file is missing or out of date (the SHA-256 of the
guide changed), the server builds the index once at startup and logs a warning.

1. **Chunking** (`app/indexer.py`). The markdown is split on `##` and `###` headings. Each heading
   with body text becomes one chunk with `section` (`"3.2"`), `heading`, `parent_heading` (the
   enclosing `##` title) and `text`. `##` headings that only introduce subsections are not chunks
   of their own. The title and metadata before section 1 are skipped, and headings inside code
   blocks are ignored. The guide gives **25 chunks**: 1, 2.1–2.3, 3.1–3.3, 4.1–4.4, 5.1–5.3,
   6.1–6.3, 7.1–7.3, 8.1–8.4, 9.
2. **Text processing** (`app/text.py`). The tokenizer lowercases, strips punctuation, removes
   stopwords and applies a light suffix stemmer (`charges`/`charged`/`charge` → `charg`). Heading
   words are counted twice so title matches weigh more. Fenced code (the JSON example in section 9)
   is returned in the text but not indexed. Queries also get a small domain synonym expansion, for
   example `extra → additional`, `data → internet`, `late → due/overdue/unpaid`,
   `cost/price/fee → charge`, `part → partial`, `complaint → dispute` and `suspend → restrict`.
3. **Ranking** (`app/bm25.py`). The ranker is a small self-written Okapi BM25 (`k1 = 1.5`,
   `b = 0.75`, IDF `ln(1 + (N − n + 0.5)/(n + 0.5))`) with no external search libraries. Ties are
   broken by document order, so results are deterministic.

### Retrieval demos

| Query | Top sections |
|-------|--------------|
| How much does extra internet cost? | 8.1, 3.2, 2.2 |
| Can I pay only part of my bill? | 8.3, 5.1, 5.3 |
| When is my bill due and what happens if I pay late? | 6.2, 6.3, 6.1 |
| How can I dispute a charge? | 8.4, 7.1, 7.2 |

## Security

* `POST/GET /mcp` requires `Authorization: Bearer <MCP_TOKEN>`. Any other value gets
  `401` with `WWW-Authenticate: Bearer`. The check is an ASGI middleware (`app/auth.py`) that uses
  a constant-time comparison.
* `GET /health` is public, for container and load-balancer health checks. It returns
  `{"status":"ok","document_id":"MOB-BILLING-2026-01","sections":25,"tool":"search_billing_documentation"}`.
* If `MCP_TOKEN` is not set, the server logs a warning and accepts unauthenticated requests. Use
  this for local development only and always set the token in deployment (for example from AWS
  Secrets Manager into the ECS task).
* The container runs as a non-root user. The transport is stateless (`stateless_http=True`), so
  any replica can serve any request.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `MCP_TOKEN` | (unset) | Required bearer token |
| `MCP_HOST` | `0.0.0.0` | Bind address |
| `MCP_PORT` | `8000` | Port |
| `MCP_INDEX_PATH` | `data/index.json` | Prebuilt index file |
| `MCP_DOCUMENT_PATH` | `data/Mobile_Provider_Billing_Guide.md` | Source guide |
| `LOG_LEVEL` | `INFO` | Log level. Each search is logged with its query and the returned sections |

## Running locally

Requires Python 3.12+.

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows (Git Bash); use .venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt
python -m app.indexer             # build data/index.json
MCP_TOKEN=dev-secret python -m app.server             # Streamable HTTP on :8000/mcp
python -m app.server --stdio      # or: stdio transport for local MCP clients
```

Demo with the real MCP Streamable HTTP client (lists the tools and runs the three demo queries, printing JSON):

```bash
MCP_TOKEN=dev-secret python demo_client.py --url http://localhost:8000/mcp
```

## Docker

```bash
docker build -t billing-mcp-server .
docker run --rm -p 8000:8000 -e MCP_TOKEN=dev-secret billing-mcp-server
```

The image (`python:3.12-slim`) builds the index during `docker build`, runs as user `app`,
listens on port 8000 and has a `HEALTHCHECK` on `/health`.

## Tests

```bash
pytest                       # or: pytest --cov=app --cov-report=term-missing
```

| File | Covers |
|------|--------|
| `tests/test_indexer.py` | Section ids and order, headings, parent headings, chunk text, CLI, load/stale rebuild |
| `tests/test_bm25.py` | IDF, term-frequency ranking, top-k, ties, serialisation |
| `tests/test_text.py` | Tokenizer, stemmer, synonym expansion |
| `tests/test_retrieval.py` | The three demo queries, plus dispute queries and an unrelated query |
| `tests/test_server.py` | End-to-end tool calls through the SDK's in-memory client session, input validation, HTTP auth (missing, wrong or malformed token → 401), public `/health` |

## Layout

```
app/
  text.py      tokenizer, stemmer, synonyms
  bm25.py      BM25 ranking
  indexer.py   chunking, index build CLI, BillingIndex (load + search)
  auth.py      bearer-token ASGI middleware
  server.py    FastMCP server, tool, /health, HTTP/stdio entry point
data/Mobile_Provider_Billing_Guide.md
demo_client.py
tests/
Dockerfile
```
