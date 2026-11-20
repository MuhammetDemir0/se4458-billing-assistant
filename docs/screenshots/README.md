# Screenshots

Captured from the AWS deployment (web chat with the tool-trace panel visible):

| File | Shows |
| --- | --- |
| `01-mcp-only.png` | "How much does extra internet cost?" → MCP search only, answer cites §3.2 |
| `02-rest-only.png` | "What is my October 2026 bill?" → REST via gateway only |
| `03-combined.png` | "Why is my October 2026 bill higher than September?" → REST + MCP |
| `04-payment-confirmation.png` | "Pay my October 2026 bill" → confirmation card before any payment |
| `05-payment-confirmed.png` | after **Confirm**: Pay Bill API result, remaining balance 0 |
| `06-other-subscriber.png` | request for 5551000002's data → only own account |
| `07-swagger.png` | Swagger UI at `<gateway>/api/v1/docs` |
| `08-gateway-401.png` | protected route without token → 401 from API Gateway |
| `09-aws-console.png` | API Gateway routes / ECS services running |
| `10-tests.png` | `pytest` run (or the green GitHub Actions run) |
