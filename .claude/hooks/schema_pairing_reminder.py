#!/usr/bin/env python3
"""PostToolUse hook: nudge about MT5 schema_version pairing.

Per CLAUDE.md: bumping the MT5 export schema means updating the .mq5
exporter and src/trading_journal/domain/models.py together, and expecting
a database reset (no migration path). This hook does not block anything —
it just prints a visible reminder when either side of that pairing is
touched, in case only one half was edited.
"""
import json
import sys

WATCHED = {
    "src/trading_journal/domain/models.py": (
        "Edited domain/models.py — if this changed MT5PositionExport or its "
        "schema_version, remember: the .mq5 exporter (mql5/TradingJournalExporter.mq5) "
        "needs the matching update, and a schema bump has no migration path "
        "(expect/communicate a local DB reset)."
    ),
    "mql5/TradingJournalExporter.mq5": (
        "Edited the MT5 exporter — if this changed the exported schema, remember: "
        "src/trading_journal/domain/models.py (MT5PositionExport, schema_version) "
        "needs the matching update on the Python side."
    ),
    "mql5/TradingJournalSync.mq5": (
        "Edited the MT5 sync EA — double-check this hasn't introduced any write/order "
        "path back to MT5; the bridge is read-only by design."
    ),
}


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path:
        sys.exit(0)

    for watched_suffix, message in WATCHED.items():
        if file_path.replace("\\", "/").endswith(watched_suffix):
            print(f"[reminder] {message}")
            break

    sys.exit(0)


if __name__ == "__main__":
    main()
