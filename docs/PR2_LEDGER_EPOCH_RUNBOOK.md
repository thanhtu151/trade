# Ledger epoch migration runbook

## Reconstructed reset migration

The one-time migration inserts an append-only `RESET` after the PVD sell at
`2026-07-09 10:50:55` and before the PVD buy at `2026-07-10 10:44:22`.
It does not synthesize trades and does not change cash or positions.

Run the `VN Trading Scheduler` workflow manually with:

- `task`: `migrate-ledger-epoch`
- `confirmation`: `MIGRATE_LEDGER_EPOCH_2026_07_09`
- `apply_migration`: leave `false` for the mandatory preview; set `true` only after reviewing it
- `reason`: empty (the reconstructed reason is fixed in code)

The workflow loads the `state` branch first, records `GITHUB_ACTOR`, performs
the journaled portfolio/ledger transaction, verifies zero drift, appends the
audit record, and only then persists state. Re-running the migration is rejected.
The CLI is also fail-safe: `--migrate-ledger-epoch` is a dry-run unless the
operator additionally passes `--apply`.

## Dry-run evidence against `origin/state` (`d9bb752`)

The read-only preview found the RESET insertion after ledger index 60 and
reported `post_migration_drift: 0.0`. It proposed exactly these open-position
unit conversions, leaving market value and cost basis unchanged:

| Symbol | Qty before → after | Avg before → after | Current before → after | Market value |
|---|---:|---:|---:|---:|
| STB | 268,300 → 268.3 | 76.8 → 76,800 | 76.9 → 76,900 | 20,632,270 |
| HCM | 39,900 → 39.9 | 24.8 → 24,800 | 25.1 → 25,100 | 1,001,490 |
| GMD | 268,500 → 268.5 | 76.5 → 76,500 | 76.3 → 76,300 | 20,486,550 |

For each converted position, top-level price/ATR fields and the corresponding
`plan.stop_loss`, `plan.initial_stop_loss`, `plan.target_price`, and `plan.atr`
are multiplied by 1,000. Quantity is divided by 1,000 as a float. Every complete
before/after object is recorded in `self_healing_audit.json`; historical trades
are never rewritten.

## Other state files containing prices

| File | Treatment |
|---|---|
| `paper_portfolio.json` | Normalize open positions in the same journaled transaction as RESET. |
| `tracked_positions.json` | Independent manually entered tracker; report only because intent/unit is ambiguous. |
| `intraday_alerts.json` | Immutable human-readable messages; do not parse or mutate; normal expiry removes them. |
| `portfolio_snapshots.json` | Historical aggregate cash/equity/market value, no per-position unit price; preserve. |
| `analysis_results.json` | Ephemeral historical analysis; preserve, while new provider data is normalized at the adapter boundary. |

`rebaseline` remains available only for unexplained drift. It requires a
non-empty `reason`; it must not be used for the reconstructed July reset.

## Current-epoch legacy price audit

The state snapshot investigated for PR2 contains 45 current-epoch trade events
whose price is below 1,000 VND. Historical records are intentionally unchanged.
New paper orders below 1,000 VND are blocked before portfolio mutation.

| Ticker | Time | Side | Price | Epoch |
|---|---|---:|---:|---:|
| PVD | 2026-07-10 10:44:22 | BUY | 33.2 | 1 |
| TCB | 2026-07-15 09:24:07 | BUY | 32.0 | 1 |
| VHM | 2026-07-15 09:24:08 | BUY | 138.9 | 1 |
| PVD | 2026-07-15 09:24:09 | SELL | 20.45 | 1 |
| TCB | 2026-07-22 17:20:10 | SELL | 29.0 | 1 |
| VHM | 2026-07-22 17:20:12 | SELL | 126.9 | 1 |
| HDB | 2026-07-23 12:21:19 | BUY | 25.25 | 1 |
| VND | 2026-07-23 12:21:21 | BUY | 16.05 | 1 |
| POW | 2026-07-23 12:21:23 | BUY | 13.05 | 1 |
| STB | 2026-07-27 12:45:25 | BUY | 74.0 | 1 |
| VND | 2026-07-27 18:14:32 | SELL | 15.35 | 1 |
| VNM | 2026-07-29 12:18:10 | BUY | 60.3 | 1 |
| PVD | 2026-07-29 12:18:13 | BUY | 18.45 | 1 |
| STB | 2026-07-29 17:34:12 | SELL | 71.5 | 1 |
| GMD | 2026-08-03 12:38:01 | BUY | 76.8 | 1 |
| HDB | 2026-08-04 17:31:29 | SELL | 26.7 | 1 |
| HDB | 2026-08-05 12:11:24 | BUY | 26.45 | 1 |
| VNM | 2026-08-05 17:29:48 | SELL | 58.6 | 1 |
| VHM | 2026-08-06 12:13:16 | BUY | 78.0 | 1 |
| VHM | 2026-08-07 15:51:30 | SELL | 73.0 | 1 |
| FRT | 2026-08-12 11:01:48 | BUY | 145.3 | 1 |
| POW | 2026-08-31 22:45:09 | SELL | 13.1 | 1 |
| DPM | 2026-09-03 13:46:27 | BUY | 23.15 | 1 |
| GMD | 2026-09-07 20:52:42 | SELL | 76.8 | 1 |
| FRT | 2026-09-07 20:52:46 | SELL | 136.4 | 1 |
| DPM | 2026-09-07 20:52:48 | SELL | 22.3 | 1 |
| GMD | 2026-09-08 13:50:53 | BUY | 76.2 | 1 |
| STB | 2026-09-08 13:50:56 | BUY | 76.8 | 1 |
| DPM | 2026-09-08 13:50:58 | BUY | 22.6 | 1 |
| PVD | 2026-09-10 19:36:37 | SELL | 18.5 | 1 |
| VNM | 2026-09-11 13:55:39 | BUY | 60.5 | 1 |
| GMD | 2026-09-11 19:30:48 | SELL | 74.6 | 1 |
| GMD | 2026-09-14 14:28:29 | BUY | 73.8 | 1 |
| HDB | 2026-09-14 21:38:40 | SELL | 26.8 | 1 |
| DPM | 2026-09-14 21:38:44 | SELL | 22.05 | 1 |
| VNM | 2026-09-14 21:38:46 | SELL | 59.3 | 1 |
| PVD | 2026-09-16 14:06:16 | BUY | 19.8 | 1 |
| DXG | 2026-09-16 14:06:18 | BUY | 10.4 | 1 |
| HCM | 2026-09-16 14:06:20 | BUY | 24.8 | 1 |
| GMD | 2026-09-18 19:35:33 | SELL | 77.3 | 1 |
| PVD | 2026-09-18 19:35:35 | SELL | 19.2 | 1 |
| GMD | 2026-09-21 14:32:45 | BUY | 76.5 | 1 |
| VPB | 2026-09-21 14:32:48 | BUY | 28.0 | 1 |
| DXG | 2026-09-24 20:05:48 | SELL | 10.15 | 1 |
| VPB | 2026-09-24 20:05:54 | SELL | 22.1 | 1 |

The especially conspicuous HVN `4.83` and POW `89.02` events are in epoch 0.
They remain preserved as historical evidence and are not used for current-epoch
cash, learning, reflection, or default P&L statistics.

## Exchange calendar

The 2026 closure set follows the published HNX/HOSE calendar, including the
additional 2 January closure, 27 April Hung Kings closure, and 31 August–2
September National Day closure. An unconfigured year fails closed for trading;
operators must add the exchange-published calendar before that year starts.

- HNX: https://old.hnx.vn/vi-vn/chi-tiet-lich-nghi-gd-60021971.html?_page=1
- HOSE New Year update: https://staticfile.hsx.vn/Uploads/UploadDocuments/2426350/20251225_Thong%20bao%20%20ve%20%20viec%20cap%20nhat%20lich%20nghi%20giao%20dich%20Tet%20Duong%20lich%202026%20toan%20thi%20truong.pdf
