# Order schema: PM → Trader → Broker → Fill

The schema is implemented in [`tca/schema.py`](../tca/schema.py). Each table is a DataFrame with one row per object. Quantities are **unsigned whole shares**, and `side` carries the direction. Timestamps are tz-naive America/New_York wall-clock time. Columns marked `?` are optional.

```
pm_orders 1 ──< trader_orders 1 ──< broker_orders 1 ──< fills
 (portfolio)      (desk strategy)      (child @ MIC)       (execution)
```

## pm_orders: what the portfolio manager wants

| column | type | notes |
|---|---|---|
| `pm_order_id` | str | Blotter `order_id` (24-hex, deterministic from decision date, PERMNO and reason) |
| `portfolio_id` | str | `BMLL_SP500_10B` |
| `decision_date`, `decision_ts` | date, ts | Close of the decision session (16:00) |
| `trade_date` | date | Session the order must complete in: the reference Friday for rebalances |
| `permno`, `ticker?` | int, str | CRSP id; point-in-time ticker for BMLL lookup |
| `side` | BUY/SELL | |
| `quantity` | int | Shares, in the trade-date share basis (split-adjusted) |
| `decision_price` | float | Decision-session close, same share basis |
| `reason` | INITIAL/REBALANCE/INFLOW/OUTFLOW | |
| `benchmark` | CLOSE/ARRIVAL/VWAP | CLOSE for index trades (the index rebalances at the close); ARRIVAL for flows |
| `urgency` | LOW/MEDIUM/HIGH | HIGH for redemptions |
| `notional_usd` | float | quantity × decision_price |

## trader_orders: how the desk works it

| column | type | notes |
|---|---|---|
| `trader_order_id`, `pm_order_id`, `permno` | str, str, int | |
| `strategy` | CLOSE_AUCTION/VWAP/ARRIVAL_IS | |
| `side`, `quantity`, `trade_date` | | Σ quantity over a PM order ≤ PM quantity |
| `start_ts`, `end_ts` | ts | Working window (MOC: 15:50–16:00) |
| `max_participation` | float (0, 1] | Of expected volume in the window, known ex ante |
| `limit_price?` | float | |
| `primary_mic` | str | Listing venue; MOC orders go here |
| `parent_strategy`, `is_spill` | str, bool | Desk strategy, and whether this leg holds the overflow above the participation cap |

## broker_orders: child orders routed to a venue

| column | type | notes |
|---|---|---|
| `child_order_id`, `trader_order_id` | str | |
| `mic` | str | Venue. `XADF` = FINRA TRF (off-exchange / dark) |
| `order_type` | MOC/LOC/LIMIT/MARKET/MIDPOINT_PEG | Dark children use MIDPOINT_PEG, lit children use collared LIMIT |
| `tif` | AT_CLOSE/DAY/IOC/GTT | MOC/LOC must be AT_CLOSE; GTT = good till the bucket ends |
| `side`, `quantity`, `limit_price?` | | Σ quantity over a trader order = trader quantity |
| `submit_ts`, `bucket_start`, `bucket_end` | ts | Bucket lies inside the trader window |

## fills

| column | type | notes |
|---|---|---|
| `fill_id`, `child_order_id`, `mic`, `fill_ts` | | |
| `side`, `quantity`, `price` | | Σ quantity over a child order ≤ child quantity |
| `liquidity` | AUCTION/ADD/REMOVE/MIDPOINT/DARK | |
| `fee_usd`, `impact_bps?` | float | Modelled access fee and impact |

## Invariants checked by `validate` and `validate_links`

- Keys are unique and non-null, and every row references an existing parent.
- A child has the same side as its parent.
- Quantity is conserved down the chain: children never exceed their parent.
- Enumerations, positive quantities and prices, and time windows are well formed.
- MOC and LOC orders are `AT_CLOSE`.

## How orders are generated ([`tca/order_generator.py`](../tca/order_generator.py))

1. **PM to trader.** The order is assigned a strategy, capped at a share of *expected* volume (ADV × historical auction share, known ex ante). Any excess becomes a second trader leg: excess on the close goes to VWAP, and excess on VWAP or arrival goes to the close.
2. **Trader to broker.** The window is cut into half-hour buckets weighted by the intraday volume curve (front-loaded for ARRIVAL_IS). Each bucket is then split across MICs in proportion to each venue's share of continuous volume in BMLL `venue_days`. Shares are allocated by largest remainder, so children always sum exactly to their parent.
