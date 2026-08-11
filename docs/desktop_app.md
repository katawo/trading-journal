# Desktop application

The desktop release keeps the Trading Journal on the same computer as MT5. It is not a hosted PWA: the packaged application starts a loopback-only Streamlit service, runs the MT5 sync worker locally, and opens the existing journal UI in your default browser.

## Install and start

1. Download the portable archive for your operating system from the GitHub release page.
2. Extract it to a permanent folder; do not run it from inside the archive.
3. Start `TradingJournal` on Linux or `TradingJournal.exe` on Windows.
4. The journal opens at a local `http://127.0.0.1:<port>` address. Keep the desktop application running while you want automatic MT5 imports.

No Python, web server, Cloud account, MT5 password, or broker password is required by the portable bundle.

## MT5 sync

1. Compile and attach `mql5/TradingJournalSync.mq5` to one chart in each MT5 terminal.
2. The EA writes `trading_journal/<MT5-login>_positions.csv` under MT5 Common Files. It exports after closed-deal events and every 60 seconds as a safety refresh.
3. Add each account in **Settings → MT5 Accounts**. Leave the custom export path empty to use the detected Windows or Linux/Wine Common Files location.
4. The desktop worker checks configured exports every five seconds. **Sync MT5 now** sends an immediate local request and the result appears within one second.

The worker validates schema v4, account login, broker server, and currency before importing. Invalid files only appear as a sync error; they never overwrite journal data.

## Data and backup

All durable state remains local:

- Windows: `%LOCALAPPDATA%\TradingJournal`
- Linux: `$XDG_DATA_HOME/trading-journal`, or `~/.local/share/trading-journal`

This directory contains `trading_journal.db`, the current sync status, and `desktop.log`. Back up the directory while the application is closed to preserve accounts, positions, logical-trade grouping, reviews, policies, strategies, and three-pillar evidence. Updating the portable bundle does not change this directory.

Use **Settings → Quit desktop journal** before replacing the application bundle or copying a backup. To recover from a failed launch, inspect `desktop.log`; deleting only `mt5-sync-status.json` is safe, but deleting the SQLite database permanently removes the local journal.

## Development and releases

For source development, run `make setup` then `make desktop`. Run `make bundle` to build a portable folder and archive for the current operating system. GitHub Actions builds separate Windows and Linux archives whenever a `v*` tag is pushed.
