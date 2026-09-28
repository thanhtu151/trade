# Reliability Fix Report

Audit baseline: `origin/main` at `f1236dc1461ab2164648864cf592c372b1b95b38`.

| Bug | Audit result | Root cause | Evidence |
|---|---|---|---|
| B1 | Confirmed | A cancelled workflow leaves the durable task row in `running`; no run reconciliation or deadline exists. | Run `36380193700`; `system_status.py:45-68`; state commit `36d30b7`. |
| B2 | Confirmed | Notification blindly compares current equity with the last snapshot, regardless of date or ledger epoch. | `notify.py:146-148`; state snapshot dated 2026-07-15. |
| B3 | Confirmed | Workflow slices `missed[:2]`; hung state is only reported and has no terminal-run reconciliation. | `.github/workflows/watchdog.yml:58-78`; issue #13. |
| B4 | Confirmed | Snapshot creation is invoked only by the Streamlit UI, never by scheduler EOD. | `auto_trader.py:781-823,2246`; `scheduler.py:634-730`. |
| B5 | Confirmed | A deliberate kill switch is represented as a healing critical error, making `heal` blocked. | `self_healing.py:224-226,394-410`; run `36320271990`. |
| B6 | Rejected (repo bug) | No repository code invokes or parses `gh variable list`; its default output is tabular. The observed JSON error came from treating non-JSON CLI output as JSON outside this repo. New workflow API parsing is schema-validated. | Repository-wide search at baseline; `gh variable list` output verified on 2026-09-28. |
| B7 | Confirmed | Kill-switch reasons share the `critical` collection even though CI later special-cases them. | `self_healing.py:224-226,453-456`. |
| B8 (found in audit) | Confirmed | Rebacktest updates `backtest_config.json` only in the ephemeral runner; the file was absent from `STATE_FILES`, so every result was discarded. | `backtester_pro.py:519-548`; baseline `.github/workflows/scheduler.yml:65-79`. |

## Implementation and verification

| Fix | Files | Reproduction/coverage | Verification | Remaining risk |
|---|---|---|---|---|
| B1 | `system_status.py`, `watchdog.py`, `watchdog.yml` | Cancelled run reconciliation and deadline test | Run `36380193700` simulation transitions to `cancelled`; retry selected | GitHub API outage stops watchdog safely before mutation. |
| B2 | `portfolio_snapshots.py`, `notify.py` | Prior-session and stale/epoch mismatch tests | Current state reports 77,018,415 VND and `N/A` because 2026-09-25 is missing | First comparable percentage becomes available after two successful EOD sessions. |
| B3 | `watchdog.py`, `watchdog.yml` | Ordered queue/deferred test | One task dispatched per serialized watchdog tick; deferred tasks shown in summary | GitHub scheduled-run delay still applies. |
| B4 | `scheduler.py`, `portfolio_snapshots.py` | EOD snapshot test, including unchanged prices | Snapshot is written only after a complete EOD valuation | A quote failure intentionally fails EOD and suppresses the snapshot. |
| B5/B7 | `self_healing.py`, `scheduler.py`, `notify.py` | Kill-switch inhibitor test | `heal` becomes success while trading remains disallowed | Old reports display legacy critical wording until the next audit rewrites schema. |
| B6 | `watchdog.py` | Invalid tabular CLI output test | Non-JSON and wrong-schema API responses fail with explicit errors | Rejected as an existing repo defect; hardened new API boundary anyway. |
| B8 | `scheduler.py`, `scheduler.yml` | Chunk/checkpoint/resume and persistence tests | Five tickers per invocation; aggregate config and checkpoint are persisted | Completion takes multiple watchdog ticks by design. |

Full suite: `125 passed, 7 skipped` using an E:-hosted pytest temp directory. Targeted reliability suite: `63 passed` before the final two persistence assertions were added.

Manual action required: merge the PR after review. Do not enable `TRADING_ENABLED` until operational review is complete. No ledger, trade, snapshot, repository variable, or secret was modified by this change.
