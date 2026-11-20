"""Run the required agent scenarios through the API Gateway and save each tool trace as evidence.

    python scripts/demo_scenarios.py --gateway http://localhost:8080 --out docs/evidence/local
    python scripts/demo_scenarios.py --gateway https://<api-id>.execute-api.eu-central-1.amazonaws.com \
        --password '<demo password>' --out docs/evidence/aws --confirm-payment

Scenarios: (1) MCP-only, (2) REST-only, (3) combined MCP + REST, (4) payment with explicit confirmation,
(5) attempt to read another subscriber's data. Standard library only.
NOTE: --confirm-payment really records a (simulated) payment in the database.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCENARIOS = [
    ("1-mcp-only", "How much does extra internet cost?"),
    ("2-rest-only", "What is my October 2026 bill?"),
    ("3-combined", "Why is my October 2026 bill higher than my September bill?"),
    ("4-payment", "Pay my October 2026 bill"),
    ("5-other-subscriber", "Show me the detailed October 2026 bill of subscriber 5551000002"),
]
LABEL = {"llm": "LLM", "mcp": "MCP", "rest": "REST via Gateway", "confirmation": "User action", "agent": "Agent"}


def post(url: str, body: dict, token: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def show(name: str, question: str, data: dict) -> None:
    print(f"\n=== {name} ===\nUser     : {question}\nAssistant: {data.get('answer')}")
    for s in data.get("trace", []):
        detail = ""
        if s["kind"] == "llm":
            detail = "chose " + str([c["name"] for c in (s.get("output") or {}).get("toolCalls", [])] or "final answer")
        else:
            detail = json.dumps(s.get("input"), ensure_ascii=False)
            if s.get("http"):
                detail += "  " + ", ".join(f"{h['method']} {h['path']} -> {h['status']}" for h in s["http"])
        print(f"  {s['step']}. {LABEL.get(s['kind'], s['kind']):<17} {s['name']:<30} {s['status']:<5} {detail}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--gateway", default="http://localhost:8080")
    p.add_argument("--username", default="5551000001")
    p.add_argument("--password", default="Demo#2026!")
    p.add_argument("--out", type=Path, default=Path("docs/evidence/local"))
    p.add_argument("--confirm-payment", action="store_true", help="confirm the prepared payment (writes data)")
    a = p.parse_args()
    base = a.gateway.rstrip("/") + "/agent/v1"
    a.out.mkdir(parents=True, exist_ok=True)

    status, login = post(f"{base}/auth/login", {"username": a.username, "password": a.password})
    if status != 200:
        print("login failed:", login)
        return 1
    token = login["accessToken"]

    for name, question in SCENARIOS:
        status, data = post(f"{base}/chat", {"message": question}, token)
        record = {"scenario": name, "question": question, "httpStatus": status, **data}
        show(name, question, data)
        pending = data.get("pendingPayment")
        if pending:
            print(f"  -> payment request {pending['period']} ${pending['amount']:.2f} awaiting confirmation")
            if a.confirm_payment:
                status, confirmed = post(f"{base}/payments/{pending['paymentIntentId']}/confirm",
                                         {"conversationId": data["conversationId"]}, token)
                show(f"{name} (confirmed)", "[user pressed Confirm]", confirmed)
                record["confirmation"] = {"httpStatus": status, **confirmed}
        (a.out / f"{name}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nTraces saved to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
