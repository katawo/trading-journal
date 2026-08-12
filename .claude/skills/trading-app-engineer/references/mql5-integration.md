# MQL5 / MetaTrader Integration Reference

Use this whenever the repository has an `mql5/` directory, an Expert Advisor (EA) or indicator, or any bridge between MetaTrader 5 and the Python application.

## Where MQL5 Fits

MQL5 code (EAs, scripts, indicators, `.mq5`/`.mqh` files) usually plays one of these roles relative to the Python app:

- **Read-only data source** (the common case for journal/review apps): the EA exports completed trade/position history — typically to CSV — for the Python side to import. No orders are placed from Python, and no MT5 login/password is stored by the app. This is a deliberate safety property, not an accident — treat "read-only" as a hard constraint, not a placeholder for a future write path, unless the repo's own docs say otherwise.
- **Execution venue**: the EA places/manages orders in the MetaTrader terminal on instructions from the Python side.
- **Both**: a full round-trip bridge (Python sends an order intent, MQL5 executes and reports the fill back).

**Check the repo's domain-conventions doc (e.g. `CLAUDE.md`) first** to see which of these actually applies — do not assume execution capability exists just because MQL5 is present, and do not add a write-back/order path to a read-only bridge without an explicit request. Getting this wrong in either direction is a safety problem: silently assuming read-only when it isn't hides real risk; silently adding write capability to what was designed read-only defeats the safety property on purpose.

The correctness requirements differ by role: a pure data-import integration needs careful parsing, schema-versioning, and reconciliation (see below); an execution bridge additionally needs the full live-order safety treatment from `references/trading-engineering.md#live-execution-safety`.

## Treat It as an Adapter Boundary

The MQL5 side is an external system, architecturally no different from a broker API:

- Do not let raw MQL5 data structures, symbol names, or file formats leak into the Python domain layer. Translate at the infrastructure boundary into the same internal types used for other data sources.
- If both a Python domain model and an MQL5-side representation of "position" or "trade" exist, define explicitly which one is the source of truth, and how/when they reconcile.

## Common Divergences to Check Explicitly

These often differ silently between MetaTrader/MQL5 and a Python application, and are a frequent source of subtle bugs:

- **Symbol naming**: brokers frequently append suffixes (`EURUSD.a`, `EURUSDm`) that won't match a plain `EURUSD` used in Python. Normalize at the boundary.
- **Volume units**: MQL5 lot sizes vs. raw unit counts vs. contract multipliers — confirm the conversion, don't assume 1 lot = 100,000 units without checking the instrument.
- **Price precision/point value**: `Point`, `Digits`, and tick size in MQL5 don't always map 1:1 onto how Python stores/display prices.
- **Time**: MetaTrader server time is often not UTC and not the broker's or user's local time. Timestamps crossing the bridge need an explicit timezone conversion, not an assumed one.
- **Account currency vs. quote currency**: P&L reported by the terminal is usually in account currency already converted; don't re-convert it in Python without checking.
- **Order/deal/position IDs**: MetaTrader's ticket numbering and history semantics (deals vs. orders vs. positions) don't map directly onto a simpler Python order/position model — check which concept a given ID actually refers to.

## Data Exchange Mechanics

The most common transport for a read-only journal integration is a CSV (or similar flat-file) export written by the EA into the MetaTrader terminal's Common Files directory, polled or watched by a Python-side sync worker. Other transports (named pipes, sockets, a shared database) show up in execution-capable bridges. Whatever the transport, apply the same reliability rules as any external integration:

- Assume the MQL5 side may be offline, closed, or mid-write when Python reads. Guard against partial files and reconnect gracefully rather than crashing.
- If the transport is file-based, avoid reading a file while the EA is actively writing it; use an atomic write pattern (write-then-rename) or a lock/flag file if you control the EA side.
- Log both sides of any round trip (what Python sent, what MQL5 reported back) so a mismatch is diagnosable after the fact — this matters far more for a trading bridge than for a typical integration.

## Schema Versioning for Import Contracts

A read-only CSV import contract should carry an explicit schema version (e.g. a `schema_version` field on the imported-row model) so that:

- older exports remain parseable or are explicitly rejected rather than silently misread,
- adding a new exported field (e.g. entry SL/TP, initial risk, a pre-trade balance snapshot) is a version bump on both the `.mq5` exporter and the Python-side model together, not a Python-only change,
- a breaking schema change is treated as a real compatibility break — check whether the repo expects a database reset in that case (common when there's no migration path) rather than assuming old data must be preserved.

## Import Idempotency

For a read-only import path, the critical correctness property is idempotency, not order-safety: re-running the import (same file, overlapping export, app restart mid-sync) must not create duplicate trade records. Give each imported row a stable natural key (e.g. broker login + ticket/deal ID) and upsert or skip on conflict rather than blindly inserting. Cover this with a test that imports the same export twice and asserts row count is unchanged.

## Testing

MQL5 code itself typically isn't unit-testable the same way Python is. Push validation logic into Python wherever possible:

- Keep parsing/normalization of MQL5-exported data in a Python module with its own unit tests using recorded sample exports (including edge cases: partial fills, cancelled orders, symbol suffix variants).
- If you must modify `.mq5`/`.mqh` files, note in your summary that they require manual verification in the MetaTrader Strategy Tester or a demo account — this skill's automated test guidance does not cover MQL5 execution itself.

## Live Trading Safety (Execution-Capable Bridges Only)

This section applies only if the repo's own docs confirm the bridge can place or modify real orders — do not apply it to a read-only import integration, and do not treat its presence in this reference as license to add execution capability that wasn't requested.

If the bridge can place or modify real orders, everything in `references/trading-engineering.md#live-execution-safety` applies, plus:

- Make it obvious and explicit which MetaTrader account (demo vs. live) a given configuration points to. A wrong-account mistake here sends real orders.
- Do not add automatic retry on the Python side for an order that may have already reached the EA — a dropped acknowledgment does not mean the order wasn't placed.
