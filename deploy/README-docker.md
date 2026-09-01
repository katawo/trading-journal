# Docker deployment (multi-user web mode)

A containerised alternative to the systemd + host-Caddy setup in
`docs/multiuser_web_deploy.md`. Pick **one** of the two — they deploy the same
two processes (Streamlit web app + FastAPI ingestion endpoint) behind Caddy.

## What runs

`docker compose` starts three services from one shared image:

| Service     | Process                                   | Published? |
|-------------|-------------------------------------------|------------|
| `web`       | Streamlit app, `TRADING_JOURNAL_MULTIUSER_MODE=1` | no — internal only |
| `ingestion` | `uvicorn trading_journal.ingestion_api:app` | no — internal only |
| `caddy`     | HTTPS reverse proxy                       | yes — `80`, `443` |

Only Caddy is reachable from the host/network; `web` and `ingestion` are on the
internal compose network only. Caddy routes `/ingest` and `/health` to `ingestion`,
everything else to `web`.

Both `web` and `ingestion` mount the **same** `tc-data` volume at `/data` and
read/write the same per-user SQLite files (`/data/users/<username>/trading_journal.db`).
That volume is the only durable state — back it up regularly.

## Prerequisites

- Docker Engine + the Compose plugin.
- A domain pointed at the VM (for automatic HTTPS). For a local smoke test you
  can edit `deploy/Caddyfile.docker` to listen on `:80` and use `http://localhost`.
- On Oracle Cloud, open ports 80/443 in the Security List/NSG **and** in the
  instance's own `iptables` (Oracle's Ubuntu images ship persisted netfilter
  rules that silently drop traffic even when the cloud firewall allows it).

## Bring it up

```bash
cp deploy/.env.example deploy/.env
# set TRADING_JOURNAL_MULTIUSER_COOKIE_KEY to a long random secret:
#   openssl rand -hex 32
# edit deploy/Caddyfile.docker: replace your-domain.example.com

docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

The `web` service refuses to start if `TRADING_JOURNAL_MULTIUSER_COOKIE_KEY` is
unset — that secret signs the login cookie, so there is deliberately no insecure
default.

## Create accounts and MT5 tokens

Run the admin scripts as one-offs against the same data volume:

```bash
docker compose -f deploy/docker-compose.yml run --rm \
  web python scripts/add_web_user.py alice --name "Alice" --email alice@example.com

docker compose -f deploy/docker-compose.yml run --rm \
  web python scripts/add_ingestion_token.py alice
```

The ingestion token is printed once — copy it into the MT5 EA's `ApiToken`
input, and set `BackendUrl` to `https://your-domain.example.com/ingest`. The
one-time MT5 `WebRequest` URL whitelist step is unchanged — see
`docs/multiuser_web_deploy.md`.

## Operational notes

- **SQLite on a local volume only.** `tc-data` must stay a local Docker volume on
  the VM's disk. Never back it with NFS/network storage — SQLite file locking is
  unsafe there, and here two containers write the same files.
- **WAL:** because two processes write one SQLite file, the app must run the
  per-user databases in WAL mode. Local single-user mode stays single-writer.
- **`.[dev]` is intentionally not installed in the image.** If you add a test
  stage, pin `httpx` (not `httpx2`) first — see the note in `pyproject.toml`.
- **Health:** `docker compose ps` shows healthchecks; `curl https://<domain>/health`
  hits the ingestion service, `https://<domain>/` the app.
- **Certs persist** in the `caddy-data` volume; don't delete it casually or Caddy
  re-requests certificates and can hit Let's Encrypt rate limits.
- **arm64:** the base images are multi-arch, so `--build` works directly on an
  Oracle Ampere (aarch64) VM.
