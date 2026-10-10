"""Minimal payment operations client. Token is read from the environment only."""

import argparse
import json
import os
from urllib.request import Request, urlopen
from urllib.parse import urlencode


def call(base_url: str, token: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(
        base_url.rstrip("/") + path, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Payment administrator operations")
    parser.add_argument("command", choices=["failed", "reconcile", "permissions", "replay-event", "replay-session"])
    parser.add_argument("target", nargs="?")
    parser.add_argument("--base-url", default=os.getenv("PAYMENT_ADMIN_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--status", choices=["failed", "observed_pending"])
    parser.add_argument("--event-type")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    token = os.getenv("PAYMENT_ADMIN_TOKEN")
    if not token:
        parser.error("Set PAYMENT_ADMIN_TOKEN for this process")
    if args.command == "failed":
        query = urlencode({key: value for key, value in {
            "status": args.status, "event_type": args.event_type,
            "limit": args.limit, "offset": args.offset,
        }.items() if value is not None})
        result = call(args.base_url, token, "GET", "/admin/payments/failed-events?" + query)
    elif args.command == "reconcile":
        result = call(args.base_url, token, "GET", "/admin/payments/reconciliation")
    elif args.command == "permissions":
        result = call(args.base_url, token, "GET", "/admin/payments/backend-privileges")
    else:
        if not args.target:
            parser.error("Replay requires an Event or Session ID")
        key = "stripe_event_id" if args.command == "replay-event" else "checkout_session_id"
        result = call(args.base_url, token, "POST", "/admin/payments/replay", {key: args.target})
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
