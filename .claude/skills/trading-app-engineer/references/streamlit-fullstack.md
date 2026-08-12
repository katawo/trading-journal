# Streamlit Full-Stack Reference

## UI Boundaries

Keep page scripts thin. A Streamlit page should mostly:
1. read user input,
2. call an application/use-case function,
3. render the result.

Do not embed complex trading math or SQL directly in widget callbacks.

## Rerun Semantics

Assume the script can rerun frequently. Guard side effects carefully.

For writes or trade actions:
- tie the action to an explicit button/form submission,
- use a unique operation ID where duplicate execution matters,
- persist enough state to detect duplicate processing,
- do not rely only on transient session flags for critical idempotency.

## Session State

Use `st.session_state` for UI/session concerns such as selected symbols, active tabs, wizard progress, or temporary inputs.

Do not make it the only source of truth for durable trades, fills, account history, or configuration that must survive a restart.

## Caching

Cache expensive pure reads/calculations when inputs determine outputs.

Be cautious with:
- live prices,
- broker account state,
- mutable database connections,
- user-specific data,
- functions with side effects.

Make cache TTL and invalidation behavior explicit when freshness matters.

## Testing

Put business logic in normal Python modules so it can be tested without launching Streamlit.

Prioritize tests for:
- P&L,
- sizing/risk calculations,
- state transitions,
- storage adapters,
- parsers/normalizers,
- idempotent write behavior,
- web/desktop configuration selection.

## Linux Desktop Pattern

Reuse the same Streamlit entrypoint.

A desktop distribution typically contains:
- application Python environment or bundled executable,
- local launcher,
- loopback Streamlit server,
- browser or desktop webview shell,
- desktop-specific data/config directory,
- AppImage or `.deb` packaging metadata.

Keep the launcher responsible for choosing a free port, starting Streamlit, waiting for readiness, opening the window/browser, and shutting down cleanly.

Do not duplicate the Streamlit UI for desktop.

## Configuration and Secrets

Streamlit's own config lives in `.streamlit/` (`config.toml`, `secrets.toml`). Treat `secrets.toml` the same as any other credentials file:

- it must never be committed — confirm it is gitignored, not just assumed to be,
- broker/API keys belong there or in environment variables, not hardcoded in domain/application code,
- desktop distributions need their own path for this data (it should not silently read/write a repo-relative `.streamlit/` folder inside a packaged app).

## Deployment

For hosted web deployment, configure through environment/secrets and deployment files rather than modifying shared application logic.

Ensure local-path assumptions are abstracted. Hosted environments may have ephemeral or read-only filesystems.
