"""Command-line chat client.  python -m app.cli --gateway https://<api-id>.execute-api.eu-central-1.amazonaws.com

It talks to the agent service through the API Gateway exactly like the web UI,
prints the tool trace after every answer, and asks you to type YES before a
prepared payment is executed. Use --save-trace DIR to store each trace as JSON
(evidence for the README).
"""

import argparse
import getpass
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

KIND_LABEL = {"llm": "LLM", "mcp": "MCP", "rest": "REST/GW", "confirmation": "USER", "agent": "AGENT"}


def print_trace(steps: list[dict]) -> None:
    print("  --- tool trace ---")
    for s in steps:
        print(f"  {s['step']:>2}. {KIND_LABEL.get(s['kind'], s['kind']):<8} {s['name']:<30} "
              f"{s['status']:<5} {s['latency_ms']:>7} ms")
        if s["kind"] != "llm" and s.get("input"):
            print(f"      input : {json.dumps(s['input'], ensure_ascii=False)}")
        for h in s.get("http") or []:
            print(f"      http  : {h['method']} {h['path']} -> {h['status']}")
        if s["kind"] == "llm":
            calls = [c["name"] for c in (s.get("output") or {}).get("toolCalls", [])]
            print(f"      chose : {calls or 'final answer'}")
        else:
            out = json.dumps(s.get("output"), ensure_ascii=False)
            print(f"      result: {out[:300]}{'...' if len(out) > 300 else ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Billing assistant CLI")
    parser.add_argument("--gateway", default="http://localhost:8080", help="API Gateway base URL")
    parser.add_argument("--username")
    parser.add_argument("--save-trace", type=Path, help="Directory to save each response as JSON")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.gateway.rstrip("/") + "/agent/v1", timeout=90)
    username = args.username or input("Subscriber number: ").strip()
    login = client.post("/auth/login", json={"username": username, "password": getpass.getpass("Password: ")})
    if login.status_code != 200:
        print("Login failed:", login.json().get("detail", login.text))
        return 1
    client.headers["Authorization"] = f"Bearer {login.json()['accessToken']}"
    conversation_id = None
    print("Signed in. Type a question, or 'exit'.")

    while True:
        try:
            question = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if question.lower() in {"exit", "quit"}:
            return 0
        if not question:
            continue
        response = client.post("/chat", json={"message": question, "conversationId": conversation_id})
        data = response.json()
        if response.status_code != 200:
            print("Error:", data.get("detail", data))
            continue
        conversation_id = data["conversationId"]
        print(f"\nAssistant: {data['answer']}")
        print_trace(data["trace"])
        save(args.save_trace, question, data)

        pending = data.get("pendingPayment")
        if pending:
            print(f"\n  Payment request: subscriber {pending['subscriberNo']}, period {pending['period']}, "
                  f"amount ${pending['amount']:.2f}, remaining balance ${pending['currentRemainingBalance']:.2f}")
            action = "confirm" if input("  Type YES to confirm, anything else to cancel: ").strip() == "YES" \
                else "cancel"
            result = client.post(f"/payments/{pending['paymentIntentId']}/{action}",
                                 json={"conversationId": conversation_id})
            body = result.json()
            print(f"\nAssistant: {body.get('answer', body.get('detail'))}")
            if result.status_code == 200:
                print_trace(body["trace"])
                save(args.save_trace, f"{action} payment", body)


def save(directory: Path | None, question: str, data: dict) -> None:
    if not directory:
        return
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
    path.write_text(json.dumps({"question": question, **data}, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
