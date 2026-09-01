#!/usr/bin/env bash
# Stop every running Trade Compass / Trading Journal desktop process (main
# install, dev "make bundle" output, or an old downloaded build all share the
# same ~/.local/share/trading-journal data directory) and clear any stale
# instance lock, so the next launch starts clean.
set -euo pipefail

DATA_DIR="${TRADE_COMPASS_DATA_DIR:-$HOME/.local/share/trading-journal}"
LOCK_FILE="$DATA_DIR/desktop.lock"
RUNTIME_FILE="$DATA_DIR/desktop-runtime.json"
NAMES=(TradeCompass TradingJournal)
GRACE_SECONDS=5

# Bundled builds run as a named executable; a `make desktop` run from source is
# a plain python process, so match its module command line too or a leftover
# source supervisor stays invisible here while still owning the journal.
# Anchor on the "-m trading_journal.desktop" invocation rather than the bare
# module name: a looser pattern also matches any shell, editor, or grep whose
# own command line happens to mention the module, and this script kills what it
# matches.
SOURCE_PATTERN='-m[[:space:]]+trading_journal\.desktop'

# pgrep -f matches any substring of a command line, so a shell or editor that
# merely mentions the module matches too. Confirm each candidate really is the
# module invocation by inspecting its argv exactly before killing it.
is_desktop_process() {
    local pid="$1" argv
    [ "$pid" = "$$" ] && return 1
    [ "$pid" = "$PPID" ] && return 1
    mapfile -d '' -t argv < "/proc/$pid/cmdline" 2>/dev/null || return 1
    [ "${#argv[@]}" -gt 0 ] || return 1

    local base="${argv[0]##*/}"
    local name
    for name in "${NAMES[@]}"; do
        [ "$base" = "$name" ] && return 0
    done

    local index
    for index in "${!argv[@]}"; do
        if [ "${argv[$index]}" = "-m" ] && [ "${argv[$((index + 1))]:-}" = "trading_journal.desktop" ]; then
            return 0
        fi
    done
    return 1
}

running_pids() {
    local name_pattern candidate
    name_pattern=$(IFS='|'; echo "${NAMES[*]}")
    {
        pgrep -x -- "$name_pattern" 2>/dev/null || true
        # "--" is required: the pattern starts with "-m" and would otherwise be
        # parsed as pgrep options.
        pgrep -f -- "$SOURCE_PATTERN" 2>/dev/null || true
    } | sort -u | while read -r candidate; do
        is_desktop_process "$candidate" && echo "$candidate"
    done
}

mapfile -t pids < <(running_pids)

if [ "${#pids[@]}" -eq 0 ]; then
    echo "No running Trade Compass / Trading Journal processes found."
else
    echo "Stopping: ${pids[*]}"
    kill "${pids[@]}" 2>/dev/null || true

    waited=0
    while [ "$waited" -lt "$GRACE_SECONDS" ]; do
        mapfile -t pids < <(running_pids)
        [ "${#pids[@]}" -eq 0 ] && break
        sleep 1
        waited=$((waited + 1))
    done

    mapfile -t pids < <(running_pids)
    if [ "${#pids[@]}" -gt 0 ]; then
        echo "Still alive after ${GRACE_SECONDS}s, forcing: ${pids[*]}"
        kill -9 "${pids[@]}" 2>/dev/null || true
    fi
fi

# The lock itself is held by the kernel and released when its owner dies, so
# the file is only a leftover marker; the runtime record is what a new launch
# reads to find a running instance and must not outlive one.
for path in "$LOCK_FILE" "$RUNTIME_FILE"; do
    if [ -f "$path" ]; then
        rm -f "$path"
        echo "Removed: $path"
    fi
done

echo "Done. Safe to relaunch."
