# Multi-user web deployment (optional)

This optional deployment mode lets several people share one server, each with a completely isolated journal. Their MT5 terminals push closed trades directly to that server instead of relying on a local CSV that a remote host cannot read.

Recommended host: an Oracle Cloud "Always Free" VM (a real always-on box, no sleep/cold-start, genuinely free). Any Linux VM with a public IP works the same way.

## How it fits together

Two processes run on the VM, both reading/writing the same per-user SQLite files under one data directory:

- The Streamlit app, in multi-user mode (`TRADING_JOURNAL_MULTIUSER_MODE=1`) — the login screen and dashboard.
- The FastAPI ingestion service (`trading_journal.ingestion_api`) — the `/ingest` endpoint the MT5 EA pushes to.

Caddy sits in front of both, terminating HTTPS and routing `/ingest` and `/health` to the ingestion service, everything else to Streamlit.

Each user gets their own SQLite file (`<data dir>/users/<username>/trading_journal.db`) — nothing is shared between users, and no code path needs a `user_id` column.

## Two ways to run it — pick one

Both deploy the same two processes behind Caddy; choose the one you'd rather operate.

- **systemd (recommended)** — runs the app straight on the host from a venv, managed by systemd, with Caddy installed as a normal package. Fewer moving parts on a single always-on VM. `make deploy-systemd`.
- **Docker** — the same processes in containers via `docker compose`, useful if you value reproducible builds or expect to move hosts. `make deploy-docker`. Details in `deploy/README-docker.md`.

Neither option changes the local single-user source-development mode.

## Shared prerequisites (both options)

1. Provision the VM (an Oracle Ampere/arm64 Always Free instance works well) and clone this repo to e.g. `/opt/trade-compass`. For systemd you also need Python 3.12+; for Docker you need Docker Engine + the Compose plugin.
2. Open ports **80 and 443** to the internet — on Oracle Cloud, in the Security List/NSG **and** in the instance's own `iptables` (Oracle's Ubuntu images ship persisted firewall rules that silently drop traffic even when the cloud firewall allows it).
3. Create the signing secret both paths read from `deploy/.env`:
   ```
   cp deploy/.env.example deploy/.env
   # put a long random value on the TRADING_JOURNAL_MULTIUSER_COOKIE_KEY line:
   openssl rand -hex 32
   ```
   This key signs the "stay logged in" cookie — keep it secret and stable across restarts. `deploy/.env` is git- and docker-ignored. If it is missing or empty, both deploy paths refuse to start rather than fall back to an insecure default.

## Option A — systemd (recommended)

1. Deploy and start both services (installs the `[multiuser,ingestion]` deps, creates the data directory, renders the unit files from `deploy/*.service` with your paths and cookie key, then enables them):
   ```
   make deploy-systemd
   ```
   Overridable: `make deploy-systemd DATA_DIR=/srv/journal SERVICE_USER=journal`. Uses `sudo` for the privileged steps; run as a user with sudo rights (or set `SUDO=` if already root). The data directory defaults to `/var/lib/trade-compass`, outside the checkout so `git pull` never touches it.
2. Create the first account and issue its MT5 token:
   ```
   make web-user USER_NAME=alice NAME="Alice" EMAIL=alice@example.com
   make web-token USER_NAME=alice
   ```
   Accounts are admin-created only (no signup/verification/reset). Re-run `web-user` with the same username to change a password. The token is shown once — copy it now; only its hash is stored.
3. Install Caddy as a package and point `deploy/Caddyfile` at your real domain (replace `your-domain.example.com`), then `sudo systemctl enable --now caddy` (or `caddy run --config deploy/Caddyfile`). Caddy fetches the Let's Encrypt certificate automatically.

To stop the services (data is preserved): `make deploy-systemd-down`.

## Option B — Docker

1. Edit `deploy/Caddyfile.docker`, replacing `your-domain.example.com` with your domain, then build and start web + ingestion + Caddy:
   ```
   make deploy-docker
   ```
2. Create the first account and issue its MT5 token (run as one-offs against the shared volume):
   ```
   docker compose -f deploy/docker-compose.yml run --rm \
     web python scripts/add_web_user.py alice --name "Alice" --email alice@example.com
   docker compose -f deploy/docker-compose.yml run --rm \
     web python scripts/add_ingestion_token.py alice
   ```

To stop (data volume is preserved): `make deploy-docker-down`. See `deploy/README-docker.md` for volumes, backups, and arm64 notes.

## MT5 EA configuration

1. Compile and attach `mql5/TradingJournalSync.mq5` in MetaEditor. The local CSV export remains available; remote ingestion is additive.
2. Set the EA's `BackendUrl` input to `https://your-domain.example.com/ingest` and `ApiToken` to the token issued when you created the account (`make web-token` / the `add_ingestion_token.py` run above).
3. In MT5, go to **Tools → Options → Expert Advisors → Allow WebRequest for listed URL** and add `https://your-domain.example.com`. This is a one-time, manual step per terminal — MT5 gives no way to do it programmatically, and `WebRequest` calls to a non-whitelisted URL always fail.
4. The EA's on-chart status comment shows local closed/open status, terminal connection, and remote retry state. Closed positions push on `InpSafetyExportSeconds` (default 60s), while temporary live snapshots push independently on `InpLiveExportSeconds` (default 10s). Network calls are synchronous but timer-only, limited to three seconds, batched for completed positions, and exponentially backed off after failures. They can delay this read-only exporter's own event queue, but not a separate trading EA.
5. A local ack-ledger (`<CommonFilesSubfolder>\<login>_backend_acked.txt`) tracks which positions the backend has confirmed with a 2xx response, so a dropped connection or backend outage just retries next cycle without ever creating duplicates.

## Operating notes

- Log in as the admin-created user at your domain; the login screen is deliberately in a fixed language (no per-user preference is known yet at that point) — everything after login still respects each user's own language setting as normal.
- Losing the ingestion token means re-running `make web-token USER_NAME=<user>` (systemd) or the `add_ingestion_token.py` compose command (Docker); the old token keeps working until you revoke it (not automated today — remove its entry from `ingestion_tokens.yaml` by hand if needed).
- **Back up the data directory** — it holds `users.yaml` (login credentials), `ingestion_tokens.yaml`, and every user's own SQLite file. On systemd that is `DATA_DIR` (default `/var/lib/trade-compass`); on Docker it is the `tc-data` named volume (`docker run --rm -v tc-data:/data -v "$PWD":/backup alpine tar czf /backup/tc-data.tgz -C /data .`).
- **SQLite concurrency:** in this mode two processes (web + ingestion) write the same per-user database, so the app runs those databases in WAL mode. Keep the data directory / volume on **local disk** — never NFS or other network storage, where SQLite's file locking is unsafe.
