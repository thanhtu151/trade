# REPO MAP (tự sinh bởi research/tools/repo_map.py — chạy lại khi code đổi)

Định dạng: `hàm:dòng_bắt_đầu` (cấp module). Đọc bằng Read offset/limit quanh số dòng.

## auto_trader.py (2557 dòng) — (lỗi cú pháp)
test: test_dashboard.py, test_data_integrity.py, test_ledger_epochs.py, test_pr1_safety.py, test_price_sanity_gate.py

## backtester.py (493 dòng) — Backtesting engine: replay strategy on historical data.
load_backtest_config_file:20, generate_signals:29, apply_ensemble_filter:84, simulate_trades:167, calculate_metrics:259, run_backtest:327, update_backtest_config:408, run_portfolio_backtest:465
test: test_config_promotion.py, test_dashboard.py, test_rebacktest_optimizer.py

## backtester_pro.py (569 dòng) — Backtester Pro using the backtesting.py library.
load_backtest_config_file:33, _finite_float:42, finite_metric_record:50, _to_numeric_frame:63, _rolling_rsi:71, _macd_hist:80, _sma:89, _atr:93, _volume_ratio:108, _load_ensemble_signal_array:114, prepare_data:131, class VNConfluenceStrategy:173, class VNEnsembleStrategy:220, _stats_value:264, _build_result_from_stats:274, _legacy_fallback_backtest:298, run_backtest_pro:345, optimize_strategy:417, run_portfolio_backtest_pro:510
test: test_config_promotion.py, test_ledger_epochs.py, test_rebacktest_optimizer.py, test_reliability_fixes.py

## calendar_sync.py (158 dòng) — Refresh trading_calendar.json from market data and vnstock's holiday list.
announced_holidays:34, observed_sessions:54, build:69, write:101, alerts:113, sync:125
test: test_trading_calendar.py

## close_neg.py (31 dòng) — (lỗi cú pháp)

## cloud_bootstrap.py (118 dòng) — Cloud bootstrap for the Streamlit dashboard.
_secret:54, is_cloud_viewer:61, bridge_secrets:71, _download_state:91, sync_state:107

## config_review.py (198 dòng) — Automatic review and promotion of backtest config candidates.
_base:53, auto_promote_enabled:57, _params:61, ticker_evidence:69, review:89, criteria:130, _load_log:136, review_pending:144, _notify:177
test: test_config_review.py

## config_store.py (274 dòng) — Single ownership boundary for active and candidate backtest configuration.
_base:25, _atomic_bytes:29, _atomic_json:44, _load:49, load_active:57, load_candidate:62, _finite:67, _json_safe:71, validate:81, compare_configs:149, write_candidate:189, _digest:212, _append_audit:216, promote:224, rollback:247
test: test_config_promotion.py, test_config_review.py

## dashboard.py (154 dòng) — không docstring
get_technical_summary:61, ask_ollama:79

## dashboard_vn.py (6367 dòng) — (lỗi cú pháp)
test: test_dashboard.py, test_pr3_reliability.py

## data_fetcher.py (790 dòng) — Fetch and cache external data sources for feature engineering.
class InsufficientDataError:32, expected_sessions:46, assess_history_quality:54, _call_with_timeout:89, _history_via_worker:116, _cache_path:148, _load_cache:153, _save_cache:166, _flatten_yfinance_columns:174, _prepare_yfinance_cache:180, _yahoo_chart_close:187, _download_close:213, _records_to_frame:235, _normalize_vn_equity_frame:239, _vnstock_cache_path:252, _ttl_hours_for_vnstock:258, fetch_with_fallback:263, _serialize_frame_records:312, _remember_provenance:324, data_provenance:335, get_stock_data_cached:341, invalidate_stock_cache:421, get_cache_status:434, fetch_usdvnd:456, fetch_vix:484, _normalize_time_column:512, fetch_foreign_trading:524, fetch_vnindex:598, get_weekly_trend:635, get_news_sentiment_fast:695, get_news_sentiment_score:760
test: test_dashboard.py, test_data_integrity.py, test_ledger_epochs.py, test_macro_and_notify.py, test_pr1_safety.py, test_price_sanity_gate.py, test_reliability_fixes.py

## debate_agents.py (336 dòng) — Bull vs Bear Debate Agents
_load_debate_log:18, _save_debate_log:27, _safe_float:33, get_past_decisions:40, bull_analyst:68, bear_analyst:118, portfolio_manager:169, run_debate:236, resolve_debate:281

## etf_core.py (238 dòng) — Paper-trading core: VN30 ETF with a 10-month trend filter.
_now:44, default_state:48, load_state:58, save_state:69, _bars:81, is_month_end:87, trend_on:100, _fill:110, process:135, sessions_all:178, summary:182, run_daily:193, _notify:217
test: test_etf_core.py

## financial_advisor.py (975 dòng) — financial_advisor.py — Chuyên gia Tài chính Cá nhân (Personal Financial Advisor).
load_profile:82, save_profile:91, _avg:135, compute_risk_profile:140, profile_summary_text:183, apply_guardrails:212, _load_audit:232, append_audit:241, ask_education:274, _emergency_current_months:331, build_financial_plan:339, _fmt_vnd:400, financial_plan_narrative:410, channels_narrative:471, _fetch_market_data_light:505, analyze_stock_educational:578, render_financial_advisor_section:638, _render_profile_form:725, _render_plan:780, _render_channels:842, _render_stock:912, _idx:957

## health_check.py (68 dòng) — Non-interactive health report; missing optional state degrades instead of crashing.
_read:14, collect_health:25

## learning_engine.py (475 dòng) — Self-learning engine for prediction tracking, LLM memory, and retraining.
_active_ledger_epoch:19, _load_predictions:28, _save_predictions:37, _load_prediction_history:42, _save_prediction_history:51, _load_memory:56, _save_memory:65, _latest_close:70, log_prediction:82, _sync_to_prediction_history:122, _update_history_outcome:176, resolve_predictions:195, calculate_accuracy_stats:242, build_llm_context:296, get_signal_weight:328, should_retrain:352, retrain_if_needed:377, should_retrain_lstm:404, retrain_lstm_if_needed:431, generate_performance_report:460
test: test_dashboard.py, test_ledger_epochs.py

## ledger_store.py (189 dòng) — Crash-safe storage and epoch helpers for the paper-trading ledger.
_json_bytes:22, _digest:26, _atomic_write:30, _ledger_lock:46, _validate_journal:69, _roll_forward:82, recover_pending_transaction:91, commit_portfolio_and_ledger:103, reset_events:139, current_epoch_id:143, current_epoch_start:148, events_in_current_epoch:155, make_reset_event:162, label_epochs:178
test: test_ledger_epochs.py

## llm_router.py (432 dòng) — LLM Router - multi-provider fallback for VN Stock Dashboard.
_valid_key:59, _split_keys:78, fetch_keys:82, _get_cloudflare_base_url:94, _provider_templates:101, _empty_usage:190, _load_usage:208, _save_usage:223, _messages:231, _parse_json_text:239, _call_provider:252, call_llm:275, call_llm_json:363, get_router_status:386

## market_data_adapter.py (74 dòng) — Single integration boundary for the proprietary vnstock/vnai packages.
class VendorPackageUnavailable:14, provider_availability:18, _vendor_class:31, quote:41, quote_history:47, trading:51, finance:57, vendor_status:63, vnstock_client:67, market_events:71
test: test_data_integrity.py, test_ledger_epochs.py

## notify.py (211 dòng) — Best-effort Discord notifications. This module is stdlib-only and never raises.
ict_today:21, _safe_warning:25, _retry_delay:29, send_embed:39, send_once:70, run_url:93, notify_task_failed:100, notify_task_blocked:104, notify_watchdog:111, notify_kill_switch:119, notify_backtest_candidate:124, notify_trade:138, format_vnd:147, build_eod_summary:151, notify_eod:183
test: test_macro_and_notify.py, test_notify.py, test_reliability_fixes.py

## portfolio_snapshots.py (93 dòng) — Durable end-of-session portfolio snapshots and comparable daily returns.
_atomic_write:15, load_snapshots:30, portfolio_equity:38, record_snapshot:46, previous_trading_day:67, daily_change:76
test: test_reliability_fixes.py

## reflection_manager.py (173 dòng) — không docstring
_active_ledger_epoch:10, class ReflectionManager:19
test: test_ledger_epochs.py

## runtime_reliability.py (143 dòng) — Bounded transient retry and in-process circuit breakers.
class CircuitOpenError:17, class CircuitBreaker:21, exponential_backoff:95, retry_transient:101, is_transient_network_error:132
test: test_ledger_epochs.py

## scheduler.py (1162 dòng) — Autonomous trading scheduler.
ict_now:49, ict_today:55, is_trading_day:59, acquire_single_instance_lock:63, load_state:87, save_state:95, already_ran_today:104, mark_ran_today:109, prefetch_stock_data:115, sync_trading_calendar:129, review_backtest_candidate:153, task_morning_prep:167, _technical_snapshot:246, _load_scan_watchlist:285, task_market_analysis:313, task_auto_trade:374, load_portfolio_direct:482, _save_portfolio_direct:492, _close_position_direct:498, _is_plausible_price:542, task_intraday_monitor:556, start_intraday_monitor:676, class IncompleteMarketData:694, fetch_eod_market_data:698, run_etf_core:713, task_eod_update:737, task_daily_learning:840, task_weekly_rebacktest:922, setup_schedule:1023, run_now:1040, catch_up_missed_tasks:1100, main:1139
test: test_config_promotion.py, test_dashboard.py, test_ledger_epochs.py, test_pr1_safety.py, test_pr3_reliability.py, test_price_sanity_gate.py, test_rebacktest_optimizer.py, test_reliability_fixes.py

## self_healing.py (669 dòng) — Deterministic self-healing and fail-safe checks for paper-trading state.
_now:30, _atomic_json_write:34, _load_json_with_recovery:51, snapshot_state:74, _trade_epoch:92, _deduplicate_trades:99, _finite_number:135, _event_signature:143, _repair_portfolio:148, run_self_healing:208, trading_permission:445, audit_exit_code:459, write_github_blocked_summary:465, trading_is_allowed:478, rebaseline:483, _normalize_open_positions:538, _state_price_file_report:559, migrate_reconstructed_reset:573
test: test_kill_switch_ci.py, test_ledger_epochs.py, test_pr1_safety.py, test_pr3_reliability.py, test_price_sanity_gate.py, test_reliability_fixes.py, test_self_healing.py

## source_manager.py (98 dòng) — Thread-safe vnstock source manager.
class _SourceManager:15, get_source:84, get_indicator:88, report_success:92, report_failure:96
test: test_data_integrity.py

## state_push.py (53 dòng) — Conflict-safe state-branch publisher: bounded retry, never merges state files.
class StateConflict:9, _git:13, publish:20
test: test_pr3_reliability.py

## system_status.py (85 dòng) — Durable per-task execution status for unattended scheduling and watchdogs.
utc_now:14, load_status:18, _write:31, update_task:46
test: test_pr3_reliability.py, test_reliability_fixes.py

## tools/test_datacenter_fetch.py (139 dòng) — Datacenter connectivity probe.
_record:31, show_egress_ip:38, _fetch_vnstock:65, test_vnstock_vci:79, test_vnstock_msn:84, test_yahoo:91, main:103

## trade_idempotency.py (108 dòng) — Persistent, cross-run idempotency reservations for paper-trade execution.
_locked:17, build_key:38, _write_atomic:45, reserve:58, transition:83, release:96

## trading_calendar.py (214 dòng) — VN exchange calendar implemented with Python's standard library only.
_jd_from_date:45, _date_from_jd:52, _new_moon:62, _new_moon_day:85, _sun_longitude:89, _lunar_month11:102, _leap_month_offset:111, lunar_to_solar:124, _weekdays:149, provisional_holidays:153, _load_file:172, year_status:186, closures:196, is_trading_day:205
test: test_data_integrity.py, test_trading_calendar.py

## trading_safety.py (193 dòng) — Fail-closed operational gates shared by every paper-trade entry point.
vietnam_now:24, kill_switch_reason:28, market_session_reason:47, operational_gate:70, price_sanity_reason:80, _atomic_json:111, _append_switch_audit:125, disable_trading:137, enable_trading:158
test: test_pr1_safety.py, test_pr3_reliability.py, test_price_sanity_gate.py, test_reliability_fixes.py

## train_ensemble.py (596 dòng) — Ensemble direction models: XGBoost + LightGBM + Random Forest + optional LSTM.
_build_horizon_target:31, build_features:41, build_features_extended:142, _ensure_training_data:230, _safe_auc:238, _fit_frame:247, train_xgboost:258, train_lightgbm:278, train_random_forest:297, walk_forward_validate_ensemble:314, _predict_pickle_model:445, ensemble_predict:465, train_all:575

## train_lstm.py (475 dòng) — không docstring
_build_horizon_target:29, save_state:39, load_symbols_from_args:45, get_data:64, compute_rsi:84, build_features:92, build_model:162, make_sequences:176, safe_auc:184, baseline_bullish_accuracy:193, walk_forward_validate:198, train_direction_model:288, train_symbol:357, lstm_predict:361

## vn_finance_kb.py (303 dòng) — vn_finance_kb.py — Kho tri thức Tài chính Việt Nam cho Chuyên gia Tài chính AI.
channels_for_band:223, grounding_context:229, _fmt_regulation:253, _fmt_tax:258, _fmt_crypto:272, _fmt_products:277, _fmt_personal_finance:289

## vn_live_data.py (190 dòng) — vn_live_data.py — Lấy dữ liệu thị trường VN biến động (giá vàng, lãi suất tiết kiệm)
_load_cache:39, _save_cache:54, _try_fetch_gold_btmc:87, get_gold_price:110, get_deposit_rates:152, market_snapshot_text:167

## vnstock_fetch_worker.py (38 dòng) — không docstring
main:9

## watchdog.py (248 dòng) — Read-only watchdog decision engine for scheduler state.
_parse:28, trade_session_open:35, dispatched_today:40, _blocked_today:50, decision_fingerprint:55, fingerprint_marker:65, latest_issue_fingerprint:69, trading_disabled_payload_enabled:76, reconcile_finished_runs:84, load_actions_runs:129, reconcile_status_file:141, plan_catchup:163, evaluate:171, load_from_state_ref:216
test: test_pr3_reliability.py, test_reliability_fixes.py

## workflow_alerts.py (30 dòng) — Deterministic deduplication helpers for scheduler workflow failure Issues.
failed_step_names:8, scheduler_failure_fingerprint:15, marker:21, latest_fingerprint:25
test: test_kill_switch_ci.py

## Test -> module (import/tên)
- test_config_promotion.py: backtester, backtester_pro, config_store, scheduler
- test_config_review.py: config_review, config_store
- test_dashboard.py: auto_trader, backtester, dashboard_vn, data_fetcher, learning_engine, scheduler
- test_data_integrity.py: auto_trader, data_fetcher, market_data_adapter, source_manager, trading_calendar
- test_etf_core.py: etf_core
- test_kill_switch_ci.py: self_healing, workflow_alerts
- test_ledger_epochs.py: auto_trader, backtester_pro, data_fetcher, learning_engine, ledger_store, market_data_adapter, reflection_manager, runtime_reliability, scheduler, self_healing
- test_macro_and_notify.py: data_fetcher, notify
- test_notify.py: notify
- test_pr1_safety.py: auto_trader, data_fetcher, scheduler, self_healing, trading_safety
- test_pr3_reliability.py: dashboard_vn, scheduler, self_healing, state_push, system_status, trading_safety, watchdog
- test_price_sanity_gate.py: auto_trader, data_fetcher, scheduler, self_healing, trading_safety
- test_rebacktest_optimizer.py: backtester, backtester_pro, scheduler
- test_reliability_fixes.py: backtester_pro, data_fetcher, notify, portfolio_snapshots, scheduler, self_healing, system_status, trading_safety, watchdog
- test_self_healing.py: self_healing
- test_trading_calendar.py: calendar_sync, trading_calendar
- test_workflow_shell.py: -
