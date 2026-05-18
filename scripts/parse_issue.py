#!/usr/bin/env python3
"""Parse a GitHub issue form body and export trade fields to GITHUB_ENV."""
import os
import re
import sys


def extract(body: str, label: str) -> str:
    m = re.search(rf"### {re.escape(label)}\s*\n+(.+?)(?=\n###|\Z)", body, re.DOTALL)
    if not m:
        return ""
    v = m.group(1).strip()
    return "" if v == "_No response_" else v


def main() -> None:
    body = os.environ.get("ISSUE_BODY", "")
    env_file = os.environ.get("GITHUB_ENV", "")

    if not env_file:
        print("ERROR: GITHUB_ENV not set", file=sys.stderr)
        sys.exit(1)

    action = extract(body, "Action").strip().upper()
    ticker = extract(body, "Ticker").strip().upper()
    qty = extract(body, "Quantity (shares)").strip()
    price = extract(body, "Price").strip()
    notes = extract(body, "Notes").strip()

    missing = [f for f, v in [("Action", action), ("Ticker", ticker),
                               ("Quantity", qty), ("Price", price)] if not v]
    if missing:
        print(f"ERROR: missing required fields: {missing}", file=sys.stderr)
        sys.exit(1)

    with open(env_file, "a") as f:
        f.write(f"TRADE_ACTION={action}\n")
        f.write(f"TRADE_TICKER={ticker}\n")
        f.write(f"TRADE_QTY={qty}\n")
        f.write(f"TRADE_PRICE={price}\n")
        f.write(f"TRADE_NOTES={notes}\n")

    print(f"Parsed: {action} {qty} {ticker} @ {price}"
          + (f"  notes={notes!r}" if notes else ""))


if __name__ == "__main__":
    main()
