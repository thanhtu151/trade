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

## Pre-merge review

Review branch: `fix/reliability-20260928`. PR: https://github.com/thanhtu151/trade/pull/14.

| R# | Kết quả | Bằng chứng (file:line / test / SHA) | Có sửa code? |
|---|---|---|---|
| R1 | Chưa hoàn tất trên HEAD mới do R7 kích hoạt STOP CONDITION | Full suite gần nhất trên `e4207eeb08c6dd4bc972e51f39501edb6a202515`: `pytest -q --tb=short` → 125 passed, 7 skipped, 0 failed. HEAD review có thêm R5/R8 nên chưa được tuyên bố merge-ready. | Không |
| R2 | 7 skip đã phân loại; không liên quan reliability cluster | `pytest -q --tb=short -rs tests/test_dashboard.py` → 30 passed, 7 skipped. Tất cả mang marker `network`; danh sách bên dưới. | Không |
| R3 | Branch/PR/commit inventory đã ghi nhận | PR #14; commit list bên dưới. | Không |
| R4 | EOD rồi learning luôn đứng trước resumable rebacktest | `test_multi_tick_queue_finishes_eod_learning_before_resuming_rebacktest`; `watchdog.py:140-151`. 50 ticker / chunk 5 = 10 tick; có EOD+learning = 12 tick. Cron 15 phút, lần cuối bắt đầu phút 165; runtime chunk trung bình đo được ~58 giây, hoàn tất khoảng 166 phút (2 giờ 46 phút). | Không |
| R5 | Dry-run state thật đạt; schema legacy được bổ sung | Dry-run run `36380193700` cho `running → cancelled`, failure 0→1, plan dispatch `rebacktest`; `test_legacy_running_row_without_run_id_reconciles_by_task_and_start_time`; commit `0d153a2`. | Có |
| R6 | Không có đường heal tự bật trading | `trading_safety.py:25-65,115-132`; `scheduler.py:345-355,941-953`; `self_healing.py:440-451`; `test_heal_success_cannot_bypass_active_environment_kill_switch`. Chỉ explicit `enable-trading` với confirmation mới xóa persisted switch; env `TRADING_ENABLED=false` vẫn chặn độc lập. | Không |
| R7 | **STOP: thay đổi hành vi lớn; không áp dụng config** | Isolated local run 50/50 ticker, 581.659 giây. Positive universe 31→37 nhưng membership đổi 20 mã; chi tiết bên dưới. Không ghi repo/state. | Không |
| R8 | EOD đã có retry/backoff cho cả exception và response thiếu dữ liệu | `scheduler.py:636-652`; test fail trước rồi pass `test_eod_market_data_retries_incomplete_response_with_backoff`; 3 attempts, backoff 1s/2s (jitter production), snapshot vẫn chỉ ghi sau valuation hoàn chỉnh; commit `119e16f`. | Có |

### Skip còn lại

| Test | Lý do | Liên quan review? |
|---|---|---|
| `TestAutoTrader::test_stage1_quick_scan` | Gọi market-data API thật | Không; signal scan |
| `TestStreamlitApp::{test_app_loads_without_exception,test_no_nan_in_metrics,test_sidebar_has_morning_briefing,test_portfolio_equity_positive}` | Streamlit integration dùng network thật | Không; UI integration |
| `TestPerformance::test_cache_hit_faster_than_api` | So sánh request API thật với cache | Không trực tiếp; data-provider performance, không phải EOD retry/snapshot |
| `TestPerformance::test_stage1_scan_speed` | Scan nhiều mã qua network thật | Không; signal scan performance |

Không bỏ skip: chúng cần external provider/network và không cover watchdog, EOD orchestration/snapshot, rebacktest state machine hay self-healing.

### Commit inventory

| SHA | Bug/review item |
|---|---|
| `53dbec1` | B1/B3 reconcile và catch-up queue |
| `cec88bf` | B2/B4/B8 snapshot và rebacktest persistence |
| `a29d037` | B5/B7 kill-switch inhibitor |
| `42e123c` | B1–B8 regression tests |
| `e4207ee` | verification report |
| `0d153a2` | R5 legacy state reconciliation |
| `119e16f` | R8 EOD retry/backoff |
| `ce1fc07` | R4/R5/R6/R8 review tests |

### R5 dry-run output (copy/in-memory only)

```text
mode: dry-run; no state write
before: state=running, run_id=36380193700, failures=0, no deadline_at
reconciled: [rebacktest]
after: state=cancelled, run_id=36380193700, failures=1, started_at removed
decision: missed=[rebacktest], hung=[], failing=[], disable_trade=false
plan: dispatch=rebacktest, deferred=[]
```

### R7 backtest config diff (không áp dụng)

| Tham số | Cũ | Kết quả mới isolated | Ảnh hưởng |
|---|---|---|---|
| Positive-EV count | 31 | 37 | Universe mua thay đổi đáng kể |
| Added positive | — | BMP, DGC, DGW, GVR, HAH, HVN, KBC, MSN, MWG, NKG, PLX, REE, VJC | 13 mã trước đây bị loại sẽ trở thành ứng viên |
| Removed positive | BID, DXG, POW, PVS, SHS, TCB, VIC | — | 7 mã hiện được phép sẽ bị loại |
| ATR stop | Global `1.0` | 0.8:14, 1.0:12, 1.2:8, 1.5:16 | Stop distance chuyển sang per-ticker |
| ATR target | Global `2.0` | 1.5:7, 2.0:9, 2.5:14, 3.0:20 | Profit targets thay đổi lớn theo ticker |
| Min confluence | Global `4` | 3:24, 4:26 | 24 mã nới điều kiện entry |
| EV sign | Historical | 17 sign flips/invalid, gồm REE -0.800→+10.108; SHS +0.324→-8.325 | Thay đổi trực tiếp ranking/eligibility |
| Output validity | JSON hữu hạn | Có `NaN` cho DXG/POW | Cần điều tra trước khi cho phép persist |

Nguồn cũ: tracked `backtest_config.json` (`last_updated=2026-06-23`). Nguồn mới: isolated local run, 50 ticker, 2 năm, optimize=true, 581.659 giây. Không file mới nào được copy vào repo hoặc state branch.

### Kết luận pre-merge

**CHƯA SẴN SÀNG MERGE.** R7 kích hoạt STOP CONDITION vì config sinh ra làm thay đổi lớn trading universe/parameters và chứa giá trị `NaN`. Theo yêu cầu review, kết quả chỉ được báo cáo, không áp dụng và không đề xuất áp dụng. Full suite trên ba commit R5/R8/test mới cũng chưa chạy vì review dừng tại stop condition.

## Promotion gate

Audit date: 2026-09-28. Branch `fix/reliability-20260928`, starting HEAD `7aa8a59e101778a7f5b3ad5616afca40e0e91012`, PR #14.

| G# | Kết quả | Bằng chứng | Commit |
|---|---|---|---|
| G1 | **STOP CONDITION: fail** — có writer khác scheduler rebacktest ghi trực tiếp active config | `dashboard_vn.py:5703-5721` cho phép người dùng chạy cả pro và legacy portfolio backtest; `backtester_pro.py:485-546` ghi `backtest_config.json`; `backtester.py:412-477` ghi cùng active file. Trading readers: `auto_trader.py:31,172-205,899-903,1653-1658`; `scheduler.py:139-159,246-265,764-785`; `dashboard_vn.py:120,768-786`; `backtester.py:20-30`; `backtester_pro.py:33-43`. | Report-only commit; không sửa code |
| G2 | Không chạy do G1 stop | Promotion/rollback chưa được triển khai. | — |
| G3 | Không chạy do G1 stop | Candidate validation chưa được triển khai. | — |
| G4 | Không chạy do G1 stop | Root cause NaN chưa được điều tra trong lượt này. | — |
| G5 | Không chạy do G1 stop | Candidate comparison/Discord chưa được triển khai. | — |
| G6 | Không chạy do G1 stop | Không tuyên bố full-suite result cho HEAD này. | — |
| G7 | Không chạy do G1 stop | Không chạy rebacktest dry-run sau gate vì gate chưa tồn tại. | — |

### G1 writer inventory

| Luồng | Entry point | Writer active |
|---|---|---|
| Scheduled/watchdog rebacktest pro | `scheduler.py:837-930` | `backtester_pro.run_portfolio_backtest_pro()` và final aggregation trong scheduler đều ghi active |
| Scheduled fallback legacy | `scheduler.py:850-856` | `backtester.run_portfolio_backtest()` gọi `update_backtest_config()` |
| Dashboard portfolio backtest pro | Nút `btn_portfolio_bt`, `dashboard_vn.py:5703-5715` | `backtester_pro.py:519-544` |
| Dashboard portfolio backtest legacy | Nút `btn_portfolio_bt`, `dashboard_vn.py:5710-5721` | `backtester.py:412-477` |

Đây không chỉ là helper có thể gọi lý thuyết: nó là UI action trực tiếp. Vì vậy sửa riêng scheduler rebacktest sẽ không thỏa invariant “không có đường code tự ghi candidate sang active”. Theo STOP CONDITION, audit dừng trước khi sửa.

### Kết luận promotion gate

**CHƯA SẴN SÀNG MERGE.** G1 kích hoạt stop condition. Không candidate nào được tạo/promote, active `backtest_config.json` không bị sửa, state branch không bị ghi, và `TRADING_ENABLED` không thay đổi.
