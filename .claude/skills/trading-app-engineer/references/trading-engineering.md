# Trading Engineering Reference

## Core Model

Model the lifecycle explicitly:

```text
market data -> strategy/signal -> order intent -> risk validation -> order -> fill -> position -> P&L/equity
```

A signal is not a fill. An order is not a position. A requested quantity is not necessarily a filled quantity.

## Position and P&L Checks

For every implementation, identify:
- long/short sign convention,
- average-price method,
- partial fills,
- partial closes,
- scale-in/scale-out behavior,
- fees/commissions,
- realized versus unrealized P&L,
- quote currency and conversion,
- multiplier for futures/options/CFDs where applicable.

Test long and short cases separately. Include zero quantity, reversal, partial close, and fee cases.

## Position Sizing

When sizing by account risk, keep these concepts separate:
- account equity,
- allowed risk amount,
- entry price,
- stop price,
- per-unit risk,
- instrument multiplier,
- quantity step/lot size,
- maximum exposure/leverage constraints.

Reject invalid configurations such as zero stop distance, negative equity, non-finite prices, or quantity below venue minimums.

## Market Data

Track source, symbol normalization, timeframe, timezone, timestamp semantics, and whether bars are open or closed.

Do not use an incomplete current bar in a historical strategy unless that behavior is intentional and tested.

Normalize vendor data at the infrastructure boundary. Domain logic should consume stable internal types.

## Backtesting

Audit for:
- future-data leakage,
- look-ahead bias,
- same-bar impossible execution,
- missing spread/slippage/fees,
- survivorship bias,
- corporate actions where relevant,
- warm-up indicators,
- timezone/session alignment,
- data gaps and duplicate bars,
- unrealistic liquidity assumptions.

Separate signal timestamp from execution timestamp. State the fill model explicitly.

## Risk Management

Prefer layered controls:
1. input validation,
2. per-trade risk,
3. exposure/position limits,
4. daily/session limits,
5. account-level kill switch,
6. broker-side safeguards when available.

UI warnings are not enforcement. Put enforceable checks before execution-side adapters.

## Live Execution Safety

Default to read-only or paper-trading behavior during development.

For live-order features:
- require explicit user request,
- make environment obvious,
- prevent accidental duplicate orders across Streamlit reruns,
- use idempotency/client order IDs when supported,
- log intent and broker response,
- reconcile local state against broker state,
- handle partial fills and rejected/cancelled states,
- never assume network timeout means the broker did not receive the order.
