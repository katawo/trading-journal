# Architecture Reference

## Preferred Starting Point

Use a modular monolith for a Streamlit trading application unless requirements clearly justify distributed services.

Suggested boundaries:

```text
UI -> application/use cases -> domain
                  -> ports/interfaces
infrastructure/adapters -> ports/interfaces
```

The domain must not import Streamlit, broker SDKs, database drivers, or cloud-specific packages.

## Persistence

Define repository interfaces around domain needs rather than mirroring database tables everywhere.

For desktop/local-first:
- SQLite is a strong default for transactional data.
- DuckDB is useful for analytical/local columnar workloads.
- Files/Parquet can be appropriate for immutable market datasets.

Hide storage choices behind adapters if web hosting may later use PostgreSQL or another remote store.

Use migrations for schema changes once persistent user data matters.

## Integration Boundaries

Wrap each external system:
- market data,
- broker/exchange,
- authentication,
- storage,
- notifications.

Translate vendor models into internal models at the boundary. This simplifies testing and replacement.

## No-Backend Constraint

A Streamlit process is already a server process, but do not add a separate application backend unless the capability needs it.

A no-separate-backend design can still have clean layers and adapters inside one Python process.

Reconsider a separate backend when there are:
- multiple independent clients,
- centralized multi-user data,
- strict security boundaries,
- long-running workloads unsuitable for Streamlit process lifecycle,
- asynchronous event processing,
- horizontal scaling with shared state,
- public API requirements.

## Configuration

Use one settings boundary for environment-dependent values. Keep secrets outside source control.

Typical differences between desktop and hosted targets:
- data directory,
- secrets source,
- browser/window launch behavior,
- database URL,
- logging destination,
- update mechanism.

Do not scatter platform checks throughout the application.

## Generated/Build Artifacts

Directories such as `build/`, `dist/`, `release/`, `*.egg-info/`, and pytest/tool caches are generated output, not source. Never hand-edit files inside them to "fix" something — fix the source and regenerate. Confirm they're gitignored; if one has been committed, flag it rather than treating it as intentional structure.

## Reliability

For external APIs:
- set timeouts,
- distinguish retryable from permanent failures,
- use bounded retries/backoff where safe,
- avoid retrying non-idempotent trading operations blindly,
- expose meaningful errors and logs.

For persistence:
- use transactions for multi-step state changes,
- enforce unique constraints for idempotency where useful,
- plan backups/export for local desktop data.
