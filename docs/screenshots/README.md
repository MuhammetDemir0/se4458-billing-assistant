# Screenshots

Captured from the AWS deployment (`https://zis2kljqp8.execute-api.eu-central-1.amazonaws.com`) on 2026-11-21.
The web chat shows the conversation on the left and the agent tool trace of the last request on the right.

| File | Shows |
| --- | --- |
| `01-mcp-only.png` | "How much does extra internet cost?" → LLM selects `search_billing_documentation` → MCP result → answer citing §3.2/§8.1. No REST call. |
| `02-rest-only.png` | "What is my October 2026 bill?" → LLM selects `get_bill` → `GET /api/v1/subscribers/5551000001/bills/2026/10 -> 200` through the API Gateway. |
| `03-combined.png` | "Why is my October 2026 bill higher than my September bill?" → one LLM turn selects `get_bill_details` (Oct), `get_bill_details` (Sep) and `search_billing_documentation`. |
| `04-payment-confirmation.png` | "Pay my October 2026 bill" → `prepare_bill_payment` only reads the bill (GET); a confirmation card shows subscriber, period, amount and balance. No payment yet. |
| `05-payment-confirmed.png` | After **Confirm**: user action, then `POST .../payments -> 201`; remaining balance $0, status PAID. |
| `06-other-subscriber.png` | Request for subscriber 5551000002's bill → only the signed-in account is used: the REST call goes to `/subscribers/5551000001/...`. |
| `07-swagger.png` | Swagger UI served through the gateway at `/api/v1/docs`; lock icons mark protected operations. |
| `08-gateway-401.png` | Query Bill Detailed without a token → `401 {"message":"Unauthorized"}` returned by API Gateway (`apigw-requestid` header). |
| `09a-aws-ecs.png` | AWS console: ECS cluster with the three Fargate services (billing-api, mcp-server, agent) running. |
| `09b-aws-apigateway-routes.png` | AWS console: API Gateway route `POST .../payments` with the JWT authorizer `cognito-jwt` (Cognito issuer, `Authorization` header). |
| `09c-aws-rds.png` | AWS console: RDS PostgreSQL `billing-assistant-db` in the private DB subnet group, *Publicly accessible: No*, own security group. |
| `10-ci.png` | GitHub Actions: all test jobs and `terraform validate` green. |
