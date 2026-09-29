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

## Promotion gate implementation

Prompt tiếp theo đã quyết định đưa cả ba writer về candidate store, nên stop G1 trước đó được giải quyết. Không candidate thật nào được persist/promote trong quá trình triển khai.

| G# | Kết quả | Bằng chứng | Commit |
|---|---|---|---|
| G1a | Đạt: một ownership module, atomic tmp+replace | `config_store.py:57,81,149,189,223,246` | `3f6ec8b` |
| G1b | Đạt: scheduler/dashboard pro/dashboard legacy chỉ ghi candidate | `scheduler.py:924`; `backtester_pro.py:559`; `backtester.py:458`; source-ownership test | `49e5297`, `f03e330` |
| G1c | Đạt: AST scan cấm literal active path ngoài `config_store.py` | `test_only_config_store_python_source_names_active_config` | `f5afdc3` |
| G1d | Đạt: dashboard báo “đã lưu ứng viên, chưa áp dụng”, hiển thị added/removed/sign/params; không có promote control | `dashboard_vn.py:5718-5730`; `test_dashboard_discloses_candidate_and_has_no_promotion_control` | `505464f` |
| G1e | Đạt: Actions trước merge đọc tracked active; state không có active. Sau merge chỉ manual promote persist active overlay | Flow bên dưới; HEAD/main active Git object cùng `f06c51cd…`; `origin/state` không có active tại audit | Report |
| G2 | Đạt: CLI/workflow promote + rollback, exact confirmation, backup, audit actor/time/run/hash | `config_store.py:223-262`; workflow options/steps; promote/rollback tests | `3e47e29` |
| G3 | Đạt: finite toàn cây, universe coverage, EV/status consistency, parameter ranges; invalid fail-closed | `config_store.py:81-146`; NaN/Inf/inconsistent-universe tests | `61d93ca` |
| G4 | Đạt: zero-trade stats được chuẩn hóa tại result boundary | `backtester_pro.py:33-57,277-298`; DXG/POW rerun; tests | `49e5297`, `92f3695` |
| G5 | Đạt: candidate chứa comparison và Discord summary; báo cáo 50 mã bên dưới | `config_store.py:149-207`; `notify.py:122-135` | `61e6b83` |
| G6 | Đạt | Code HEAD `f03e330`: `pytest -q --tb=short` → **141 passed, 7 skipped, 0 failed** (215.29s). Report-only HEAD `c228bc7`: cùng lệnh → **141 passed, 7 skipped, 0 failed** (211.16s). | — |
| G7 | Đạt trên copy/in-memory; không ghi state | Active SHA-256 trước/sau giống nhau; candidate valid/source scheduler | — |

### Writer/reader inventory và flow G1e

| Trước | Sau |
|---|---|
| Scheduler ghi active trong `scheduler.py` và `backtester_pro.py` | Scheduler gọi `write_candidate("scheduler", ...)` |
| Dashboard pro ghi active trong `backtester_pro.py` | Pro writer gọi `write_candidate("dashboard_pro", ...)` |
| Dashboard legacy ghi active trong `backtester.py` | Legacy writer gọi `write_candidate("dashboard_legacy", ...)` |
| Readers tự mở file hoặc dùng loader riêng | `auto_trader.py:174`, `backtester.py:23`, `backtester_pro.py:36`, `dashboard_vn.py:120,769`, scheduler loaders đều đi qua `load_active()` |

```text
Local dashboard ──backtest──> local candidate file (không commit/push/state-sync tự động)
GitHub scheduler ──rebacktest chunks/checkpoint──> candidate on runner ──state persistence──> state branch
Trading task ──state overlay──> load_active() ──> active only
Manual workflow_dispatch + exact confirmation
  ├─ promote: validate candidate → backup active → atomic active replace → audit → state persistence
  └─ rollback: backup → atomic active replace → audit → state persistence
```

Trước lần promote đầu tiên, Actions dùng tracked `backtest_config.json` từ commit main vì state branch không có file này. Sau promote thủ công, active được persist trên state branch và overlay vào runner ở bước Load state. Không có chiều đồng bộ từ máy local lên repo/state.

### Root cause NaN (G4)

Isolated rerun giữ nguyên 2 năm/optimize/grid cho thấy DXG chỉ có 24 OHLCV rows, POW 25 rows; cả hai sinh `0 trades`. `backtesting.py` trả NaN cho expectancy, win rate, profit factor, Sharpe/Sortino/Calmar/Kelly khi không có trade. Giá input không âm/zero/non-finite; stop condition “dữ liệu giá sai” không kích hoạt. Result boundary mới trả các metric không xác định là `0.0`, giữ `trades=0`, và thêm `status=insufficient_trades`. JSON serialize với `allow_nan=False`.

### Candidate comparison G5 (isolated, không persist)

Validation errors: `[]`. Positive universe: 31→37; added: BMP, DGC, DGW, GVR, HAH, HVN, KBC, MSN, MWG, NKG, PLX, REE, VJC; removed: BID, DXG, POW, PVS, SHS, TCB, VIC. EV sign/eligibility changes: 20. Candidate parameters: ATR stop {0.8:14, 1.0:12, 1.2:8, 1.5:16}; ATR target {1.5:7, 2.0:9, 2.5:14, 3.0:20}; confluence {3:24, 4:26}. Overfit note is embedded in every comparison: optimized/evaluated on the same historical sample, requiring human out-of-sample review before promotion.

| Mã | EV active | EV candidate | Trades active→candidate | Candidate status |
|---|---:|---:|---:|---|
| ACB | 0.219 | 0.727 | 14→22 | ok |
| BID | 1.616 | 0.000 | 19→2 | insufficient_trades |
| BMP | -0.096 | 1.286 | 20→25 | ok |
| CMG | -0.181 | -0.318 | 11→27 | ok |
| CTG | 1.017 | 2.686 | 18→9 | ok |
| DCM | 0.235 | 0.202 | 17→16 | ok |
| DGC | -1.080 | 0.328 | 10→6 | ok |
| DGW | -1.189 | 0.724 | 13→17 | ok |
| DPM | 0.343 | 4.993 | 17→22 | ok |
| DXG | 0.196 | 0.000 | 13→0 | insufficient_trades |
| FPT | -0.964 | -0.319 | 14→6 | ok |
| FRT | 0.566 | 0.699 | 16→25 | ok |
| GAS | 1.594 | 1.566 | 21→6 | ok |
| GMD | 0.901 | 4.714 | 14→6 | ok |
| GVR | -1.026 | 1.698 | 12→23 | ok |
| HAH | -1.322 | 0.270 | 15→7 | ok |
| HCM | 0.411 | 1.376 | 16→22 | ok |
| HDB | 0.707 | 1.968 | 18→25 | ok |
| HPG | -0.119 | -0.073 | 14→32 | ok |
| HSG | -1.372 | -0.301 | 9→26 | ok |
| HVN | -0.079 | 2.000 | 20→6 | ok |
| KBC | -0.977 | 2.466 | 14→9 | ok |
| KDH | 1.141 | 1.561 | 13→9 | ok |
| MBB | 0.902 | 0.127 | 20→26 | ok |
| MSN | -0.809 | 0.109 | 12→9 | ok |
| MWG | -0.297 | 3.681 | 12→5 | ok |
| NKG | -0.792 | 3.259 | 10→5 | ok |
| NVL | 1.900 | 4.162 | 7→5 | ok |
| PC1 | 2.754 | 2.207 | 15→14 | ok |
| PLX | -0.448 | 2.493 | 19→34 | ok |
| POW | 1.798 | 0.000 | 14→0 | insufficient_trades |
| PVD | 0.746 | 1.425 | 13→32 | ok |
| PVS | 0.887 | -0.104 | 13→24 | ok |
| REE | -0.800 | 10.108 | 15→9 | ok |
| SAB | -0.264 | -1.215 | 17→21 | ok |
| SHS | 0.324 | -8.325 | 14→8 | ok |
| SSI | -0.012 | -0.633 | 14→14 | ok |
| STB | 0.685 | 4.224 | 17→7 | ok |
| TCB | 0.330 | 0.000 | 12→4 | insufficient_trades |
| VCB | 0.376 | 0.490 | 23→43 | ok |
| VCI | 0.997 | 1.691 | 10→7 | ok |
| VHM | 1.130 | 4.566 | 21→19 | ok |
| VIB | 0.800 | 2.867 | 15→8 | ok |
| VIC | 3.314 | 0.000 | 21→2 | insufficient_trades |
| VJC | -0.178 | 2.801 | 23→12 | ok |
| VND | 2.158 | 7.871 | 14→6 | ok |
| VNM | 0.614 | 0.603 | 16→10 | ok |
| VPB | 0.738 | 3.791 | 19→12 | ok |
| VRE | 2.516 | 3.883 | 16→9 | ok |
| VSC | 1.266 | 9.023 | 10→7 | ok |

### G7 dry-run

```text
mode: dry-run copy; no state write
state: running(run 36380193700) → cancelled; plan dispatch=rebacktest
rebacktest: 5/5 mock-equivalent chunk completed
active sha256 before: 5377994531340a050d3e0fea099e2f5bf2156e1a4b86f1d487b182cd2eaf48d6
active sha256 after:  5377994531340a050d3e0fea099e2f5bf2156e1a4b86f1d487b182cd2eaf48d6
candidate: source=scheduler, status=valid, tickers=[VCB,MBB,ACB,TCB,STB]
checkpoint exists: false
```

### Kết luận promotion gate

**SẴN SÀNG MERGE về mặt reliability gate.** Rebacktest sau merge chỉ tạo candidate; config thay đổi lớn nêu trên vẫn không ảnh hưởng trading cho tới khi operator review và dispatch promote với exact confirmation. Không candidate/config nào đã được promote trong quá trình này.

## Data integrity

Audit date: 2026-09-28. Branch `fix/data-integrity-20260928` được tạo từ PR #14 HEAD `ff40624` vì GitHub vẫn báo PR #14 `OPEN`, `mergedAt=null`; `origin/main` tại audit là `f1236dc`. Không merge, state write, cache deletion, active-config change hay workflow dispatch nào được thực hiện.

| D# | Kết quả | Bằng chứng | Commit |
|---|---|---|---|
| D1 | Audit cache isolated đủ 50 mã; 49/50 dưới 90% so với 523 phiên tham chiếu VCI. Fresh audit toàn bộ dừng theo D4 | Bảng dưới; `E:\Tradeclone-review-backtest\cache\vnstock` | Report only |
| D2 | Xác nhận root cause: fallback MSN trả dữ liệu cắt cụt nhưng response không rỗng bị coi là success và được cache | `data_fetcher.py:157-190,210-249`; `source_manager.py:11-12,39-76`; fresh response mẫu dưới | Report only |
| D3 | Chưa sửa | D4 kích hoạt STOP CONDITION trước phase sửa/test | — |
| D4 | **STOP CONDITION**: auto trader đã giao dịch POW và DXG qua dữ liệu không có quality gate | `origin/state:paper_trades.json`; 12 entries, gồm 4 BUY `two_stage_scheduler`; bảng dưới | Report only |
| D5 | Chưa thực hiện | Dừng tại D4 | — |
| D6 | Chưa thực hiện | Dừng tại D4 | — |
| D7 | Chưa thực hiện | Không chạy/persist rebacktest sau stop | — |
| D8 | Chưa thực hiện | Không tuyên bố merge-ready khi D4 đang mở | — |

### D1 — độ phủ cache 50 mã

Tham chiếu là 523 session dates từ fresh VCI/VCB cho cùng request hai năm. `Missing` và `max gap` tính theo chuỗi session tham chiếu; đây là audit phát hiện truncation, không phải thay thế lịch HOSE chính thức. Cache hiện tại của repo chỉ có các file ngắn hạn rời rạc; bảng là cache 50 mã của isolated G4 run, không ghi vào repo/state.

| Mã | Rows | First | Last | Missing | Coverage | Max gap | <90% |
|---|---:|---|---|---:|---:|---:|---|
| ACB | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| BID | 365 | 2025-04-10 | 2026-09-25 | 172 | 67.1% | 158 | YES |
| BMP | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| CMG | 365 | 2025-04-14 | 2026-09-25 | 173 | 66.9% | 160 | YES |
| CTG | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| DCM | 365 | 2025-04-08 | 2026-09-28 | 175 | 66.5% | 156 | YES |
| DGC | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| DGW | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| DPM | 365 | 2025-04-14 | 2026-09-25 | 173 | 66.9% | 160 | YES |
| DXG | 24 | 2024-10-18 | 2026-09-18 | 500 | 4.4% | 41 | YES |
| FPT | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| FRT | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| GAS | 365 | 2024-12-16 | 2026-09-27 | 250 | 52.2% | 82 | YES |
| GMD | 365 | 2025-04-22 | 2026-09-27 | 243 | 53.5% | 166 | YES |
| GVR | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| HAH | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| HCM | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| HDB | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| HPG | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| HSG | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| HVN | 365 | 2025-04-22 | 2026-09-27 | 243 | 53.5% | 166 | YES |
| KBC | 365 | 2025-04-24 | 2026-09-25 | 173 | 66.9% | 168 | YES |
| KDH | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| MBB | 365 | 2025-04-22 | 2026-09-25 | 173 | 66.9% | 166 | YES |
| MSN | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| MWG | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| NKG | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| NVL | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| PC1 | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| PLX | 365 | 2025-03-31 | 2026-09-23 | 240 | 54.1% | 151 | YES |
| POW | 25 | 2026-08-24 | 2026-09-27 | 506 | 3.3% | 500 | YES |
| PVD | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| PVS | 365 | 2025-04-08 | 2026-09-28 | 175 | 66.5% | 156 | YES |
| REE | 351 | 2024-09-30 | 2026-09-25 | 189 | 63.9% | 27 | YES |
| SAB | 365 | 2025-04-17 | 2026-09-25 | 173 | 66.9% | 163 | YES |
| SHS | 365 | 2025-04-22 | 2026-09-25 | 173 | 66.9% | 166 | YES |
| SSI | 365 | 2025-03-25 | 2026-09-27 | 245 | 53.2% | 147 | YES |
| STB | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| TCB | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| VCB | 523 | 2024-08-20 | 2026-09-28 | 0 | 100.0% | 0 |  |
| VCI | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| VHM | 365 | 2025-04-15 | 2026-09-27 | 243 | 53.5% | 161 | YES |
| VIB | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| VIC | 365 | 2025-04-16 | 2026-09-25 | 174 | 66.7% | 162 | YES |
| VJC | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| VND | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| VNM | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| VPB | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| VRE | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |
| VSC | 365 | 2025-04-10 | 2026-09-28 | 158 | 69.8% | 158 | YES |

### D2 — root cause và fresh response

Request giữ nguyên `start=2024-09-28`, `end=2026-09-28`, `interval=1D`:

| Mã | VCI | MSN |
|---|---|---|
| VCB | 523 rows, 2024-08-20..2026-09-28 | 365 rows, 2025-04-10..2026-09-28 |
| DXG | 523 rows, 2024-08-20..2026-09-28 | 24 rows, 2024-10-18..2026-09-18 |
| POW | 523 rows, 2024-08-20..2026-09-28 | 25 rows, 2026-08-24..2026-09-27 |

VCI tự tính `countBack` theo business days và trả đủ. MSN response bị cap/cắt không đồng nhất. Sau một lỗi VCI, source manager chuyển sang MSN trong 10 phút; response MSN chỉ cần không rỗng là được `report_success`, ghi cache và dùng cho các mã tiếp theo. Cache không phải nguồn gốc ban đầu nhưng giữ lại response sai.

### D4 — giao dịch bị ảnh hưởng

`scan_symbol()` chỉ yêu cầu 55 dòng (`auto_trader.py:1076-1089`) và không kiểm tra coverage. BUY plan tiếp tục fetch 0.1 năm để tính ATR (`auto_trader.py:1346,1441`); close-position có thể fetch 45 ngày (`auto_trader.py:1643,1673`). Vì provider/source không được lưu cùng trade, không thể chứng minh hậu nghiệm từng call dùng VCI hay MSN; các lệnh tự động dưới đây nằm trong phạm vi bị ảnh hưởng và cần operator review.

| Time | Mã | Side | Qty | Price | Reason |
|---|---|---|---:|---:|---|
| 2026-06-19 09:14:22 | POW | BUY | 1,770,700 | 14.20 | two_stage_scheduler: BUY |
| 2026-06-19 10:48:47 | POW | SELL | 1,770,700 | 14.20 | Manual SELL |
| 2026-06-20 11:07:21 | POW | BUY | 225,184 | 89.02 | Manual BUY từ Two-Stage Analysis |
| 2026-06-21 14:49:58 | POW | SELL | 225,184 | 13.90 | Manual SELL |
| 2026-06-21 14:51:54 | POW | BUY | 2,295,752 | 13.90 | Manual BUY từ Two-Stage Analysis |
| 2026-06-22 09:47:17 | POW | SELL | 2,295,752 | 13.90 | Manual SELL |
| 2026-06-30 10:41:48 | POW | BUY | 2,657,200 | 14.80 | two_stage_scheduler: BUY |
| 2026-07-01 10:10:33 | POW | SELL | 2,657,200 | 14.70 | Manual SELL |
| 2026-07-23 12:21:23 | POW | BUY | 1,645,900 | 13.05 | two_stage_scheduler: BUY |
| 2026-08-31 22:45:09 | POW | SELL | 1,645,900 | 13.10 | stop_loss |
| 2026-09-16 14:06:18 | DXG | BUY | 1,904,600 | 10.40 | two_stage_scheduler: BUY |
| 2026-09-24 20:05:48 | DXG | SELL | 1,904,600 | 10.15 | stop_loss |

### Kết luận data integrity

**CHƯA SẴN SÀNG MERGE.** D4 kích hoạt STOP CONDITION vì auto trader đã ghi giao dịch POW/DXG trong khi fetch path không có coverage gate. Theo yêu cầu, không tự sửa sau phát hiện này. D3/D5/D6/D7/D8 còn mở; không có candidate mới, promotion, active-config change hay state mutation.

## Data integrity — hoàn tất (29/09/2026)

Tiếp nối audit ở trên sau khi #14 đã merge. Branch được merge với `main` (#14, #16, #18), không rebase lại lịch sử.

| D# | Kết quả | Bằng chứng |
|---|---|---|
| D3 | **Sửa lại cổng chất lượng.** Commit `5e51aad` so độ phủ với *ngày trong tuần* ≥ 90%, nên từ chối cả dữ liệu VCI đầy đủ khi cửa sổ ngắn vắt qua ngày nghỉ. Ví dụ: EOD dùng 0,1 năm; ngày 28/09/2026 chỉ phủ 88,5% do nghỉ 31/8–2/9; sau Tết chỉ 81,5%; `years=0.02` với 1 ngày nghỉ chỉ 80%. Nếu merge nguyên bản thì EOD/auto trader sẽ hỏng. Nay số phiên kỳ vọng lấy từ `trading_calendar` (#18), cho thiếu tối đa `max(10%, 2 phiên)`, và vẫn yêu cầu dữ liệu bắt đầu đúng hạn. | Mô phỏng 50 mã × mọi ngày kết thúc 2025–2026 × cửa sổ 0,02–2 năm: **19.500 cửa sổ, 0 lần từ chối nhầm**. Mẫu cắt cụt kiểu MSN (365 dòng cuối, 1/21 dòng): **0 lần lọt**. Gọi thật: VCI VCB/DXG/POW 0,1 năm và 2 năm đều `ok` (phủ 99,4–100%); MSN DXG 24 dòng (4,6%) và POW 25 dòng (3,4%) bị từ chối. |
| D4 | **Đã review 12 lệnh POW/DXG.** Có 11/12 giá khớp nằm trong biên độ giá thật của phiên. Lệnh lỗi duy nhất là 2026-06-20 (thứ Bảy) POW BUY 225.184 @ 89,02, gấp 6,3 lần giá thị trường 13,85–14,20. Lệnh này gây lỗ ảo −16,9 triệu, thuộc **epoch 0** nên đã được cách ly bởi migration epoch. Epoch 1 (sổ hiện hành): POW +82.295, DXG −476.150. DXG dừng lỗ ở −2,4% sau 6 phiên; stop có thể đặt từ ATR trên 24 dòng cụt. Tổng ảnh hưởng ≈ −0,39 triệu (≈0,5% tài sản). | So với OHLC VCI trong snapshot `research/data/2026-09-29` |
| D5 | **Không sửa sổ lệnh.** Giá khớp của epoch 1 là giá thật. Lệnh sai giá nằm trong epoch 0 đã cách ly. Viết lại lịch sử sẽ làm hỏng chuỗi đối soát của self-healing mà không đem lại gì. | Quyết định vận hành |
| D6 | **Truy vết từ nay:** mỗi lệnh ghi `data_provenance` gồm nguồn, cách lấy (fresh/cache/stale), số dòng, độ phủ và khoảng ngày, lấy từ lần fetch gần nhất của mã đó. Không cần xóa cache thủ công: cache cũ thiếu metadata bị bỏ qua tự động, và `data_cache/` trên runner Actions không được persist. | `data_fetcher.data_provenance`, `auto_trader.log_trade`; test `test_trades_record_the_provenance_of_the_data_behind_them` |
| D7 | Không dispatch rebacktest thủ công. Theo #14, rebacktest chỉ tạo candidate. Lần chạy thứ Hai tới sẽ dùng dữ liệu đã qua cổng chất lượng; candidate cần được review trước khi promote. | — |
| D8 | **Sẵn sàng merge.** Full suite 175 passed, 12 skipped (playwright/dashboard/network), với `TestDataFiles` được deselect vì cần file runtime cục bộ. Hai test data-integrity trước đây fail khi máy không có vnstock nay đã mock `provider_availability`. | `pytest tests --deselect tests/test_dashboard.py::TestDataFiles` |
