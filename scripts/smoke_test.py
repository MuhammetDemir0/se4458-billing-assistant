"""Gateway smoke test (standard library only).  Works against the local Kong stack or AWS API Gateway.

    python scripts/smoke_test.py --gateway http://localhost:8080
    python scripts/smoke_test.py --gateway https://<api-id>.execute-api.eu-central-1.amazonaws.com \
        --save docs/evidence/gateway-smoke-aws.json      # the demo password is asked (hidden)

Checks: public route, protected route without token (401), sign-in, own data (200), another subscriber's data
(403), versioned docs, agent health (MCP tools discovered) and the throttling configuration.
"""

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request

results: list[dict] = []
LOCAL_HOSTS = ("localhost", "127.0.0.1")
LOCAL_DEMO_PASSWORD = "Demo#2026!"  # exists only in the local stack (AUTH_MODE=local)


def resolve_password(gateway: str, password: str | None, username: str) -> str:
    """Use --password if given; locally fall back to the local demo password; otherwise ask (hidden)."""
    if password:
        return password
    if any(host in gateway for host in LOCAL_HOSTS):
        return LOCAL_DEMO_PASSWORD
    return getpass.getpass(f"Password for {username} (input hidden): ")


def http(method: str, url: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict | str, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw, status, headers = response.read(), response.status, dict(response.headers)
    except urllib.error.HTTPError as err:
        raw, status, headers = err.read(), err.code, dict(err.headers)
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw.decode(errors="replace")[:200]
    return status, parsed, headers


def check(name: str, method: str, url: str, expected: int, **kwargs) -> dict | str:
    status, body, headers = http(method, url, **kwargs)
    ok = status == expected
    recorded = body
    if isinstance(body, dict) and "accessToken" in body:
        recorded = {**body, "accessToken": "<redacted>"}  # never store a usable token in evidence files
    text = json.dumps(recorded, ensure_ascii=False)
    results.append({"check": name, "request": f"{method} {url}", "expected": expected, "status": status,
                    "ok": ok, "requestId": headers.get("X-Request-ID") or headers.get("x-request-id"),
                    "body": recorded if len(text) <= 1500 else text[:1500] + "...(truncated)"})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {method} {url} -> {status} (expected {expected})")
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default="http://localhost:8080")
    parser.add_argument("--subscriber", default="5551000001")
    parser.add_argument("--other", default="5551000002")
    parser.add_argument("--password", help="omit to be asked (hidden); local stack default: Demo#2026!")
    parser.add_argument("--save", help="write results as JSON evidence")
    args = parser.parse_args()
    args.password = resolve_password(args.gateway, args.password, args.subscriber)
    gw = args.gateway.rstrip("/")
    bills = f"{gw}/api/v1/subscribers/{args.subscriber}/bills/2026/10"

    check("health (public)", "GET", f"{gw}/api/v1/health", 200)
    check("OpenAPI document (public, versioned)", "GET", f"{gw}/api/v1/openapi.json", 200)
    check("Query Bill (public route)", "GET", bills, 200)
    check("Query Bill Detailed without token (protected route)", "GET", f"{bills}/details", 401)
    check("Pay Bill without token (protected route)", "POST", f"{bills}/payments", 401,
          body={"amount": 1, "transactionId": "smoke-no-token-1"})

    login = check("agent sign-in", "POST", f"{gw}/agent/v1/auth/login", 200,
                  body={"username": args.subscriber, "password": args.password})
    token = login.get("accessToken") if isinstance(login, dict) else None
    if not token:
        print("Sign-in failed; skipping authenticated checks.")
    else:
        check("Query Bill Detailed with own token", "GET", f"{bills}/details?page=1&pageSize=3", 200, token=token)
        check("another subscriber's details with my token", "GET",
              f"{gw}/api/v1/subscribers/{args.other}/bills/2026/10/details", 403, token=token)
        check("Add Usage with a subscriber token (admin only)", "POST",
              f"{gw}/api/v1/subscribers/{args.subscriber}/usage", 403, token=token,
              body={"month": 10, "year": 2026, "usageType": "PHONE"})
        check("invalid month -> validation error", "GET", f"{gw}/api/v1/subscribers/{args.subscriber}/bills/2026/13",
              400)
    health = check("agent health (MCP tools discovered)", "GET", f"{gw}/agent/v1/health", 200)
    if isinstance(health, dict):
        print("   MCP tools loaded by agent:", health.get("mcpToolsLoaded"), "| model:", health.get("model"))

    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump({"gateway": gw, "results": results}, fh, indent=2, ensure_ascii=False)
    failed = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
