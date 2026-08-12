#!/usr/bin/env python3
"""PreToolUse hook: hard-block a small set of destructive bash patterns.

This is a belt-and-suspenders backstop behind the deny list in settings.json.
Reads the tool-call JSON on stdin; exit 2 + stderr message blocks the call
and surfaces the message back to the agent, exit 0 allows it through.
"""
import json
import re
import sys

DANGEROUS_PATTERNS = [
    r"\bmake\s+reset-db\b",
    r"\brm\s+-rf\b",
    r"\bgit\s+push\s+.*--force\b",
    r"\bgit\s+push\s+.*-f\b",
    r"DROP\s+TABLE",
    r">\s*data/trading_journal\.db",  # blind overwrite of the dev DB file
]


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # If we can't parse the payload, don't block — fail open.
        sys.exit(0)

    command = (payload.get("tool_input") or {}).get("command", "")
    if not command:
        sys.exit(0)

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            sys.stderr.write(
                f"Blocked: command matches a destructive pattern ({pattern}).\n"
                f"Command was: {command}\n"
                "If this is intentional, run it yourself directly in a terminal "
                "rather than through the agent.\n"
            )
            sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
