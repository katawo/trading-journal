#!/usr/bin/env bash
# Stop every running Trade Compass / Trading Journal desktop process (main
# install, dev "make bundle" output, or an old downloaded build all share the
# same ~/.local/share/trading-journal data directory) and clear any stale
# instance lock, so the next launch starts clean.
set -euo pipefail

DATA_DIR="${TRADE_COMPASS_DATA_DIR:-$HOME/.local/share/trading-journal}"
LOCK_FILE="$DATA_DIR/desktop.lock"
NAMES=(TradeCompass TradingJournal)
GRACE_SECONDS=5

pattern=$(IFS='|'; echo "${NAMES[*]}")
mapfile -t pids < <(pgrep -x "$pattern" 2>/dev/null || true)

if [ "${#pids[@]}" -eq 0 ]; then
    echo "No running Trade Compass / Trading Journal processes found."
else
    echo "Stopping: ${pids[*]}"
    kill "${pids[@]}" 2>/dev/null || true

    waited=0
    while [ "$waited" -lt "$GRACE_SECONDS" ]; do
        mapfile -t pids < <(pgrep -x "$pattern" 2>/dev/null || true)
        [ "${#pids[@]}" -eq 0 ] && break
        sleep 1
        waited=$((waited + 1))
    done

    mapfile -t pids < <(pgrep -x "$pattern" 2>/dev/null || true)
    if [ "${#pids[@]}" -gt 0 ]; then
        echo "Still alive after ${GRACE_SECONDS}s, forcing: ${pids[*]}"
        kill -9 "${pids[@]}" 2>/dev/null || true
    fi
fi

if [ -f "$LOCK_FILE" ]; then
    rm -f "$LOCK_FILE"
    echo "Removed lock file: $LOCK_FILE"
fi

echo "Done. Safe to relaunch."
