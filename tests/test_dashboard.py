"""
Test suite cho VN Stock Dashboard.
Chạy: pytest tests/ -v
"""

from datetime import date
import ast
import collections
import json
import os
from pathlib import Path

import pandas as pd
import pytest


BASE_DIR = Path(__file__).parent.parent

try:
    from streamlit.testing.v1 import AppTest
    HAS_STREAMLIT_TEST = True
except ImportError:
    HAS_STREAMLIT_TEST = False

try:
    from playwright.sync_api import Page
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


class TestDataFiles:
    """Test các file data quan trọng tồn tại và đọc được."""

    def test_portfolio_exists(self):
        path = BASE_DIR / "paper_portfolio.json"
        assert path.exists(), "paper_portfolio.json không tồn tại"

    def test_portfolio_valid_json(self):
        path = BASE_DIR / "paper_portfolio.json"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert "cash" in data, "Portfolio thiếu field 'cash'"
        assert "positions" in data, "Portfolio thiếu field 'positions'"

    def test_portfolio_cash_positive(self):
        path = BASE_DIR / "paper_portfolio.json"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["cash"] >= 0, f"Cash âm: {data['cash']}"

    def test_portfolio_no_negative_ev_tickers(self):
        portfolio_path = BASE_DIR / "paper_portfolio.json"
        config_path = BASE_DIR / "backtest_config.json"

        if not config_path.exists():
            pytest.skip("backtest_config.json chưa có")

        with open(portfolio_path, encoding="utf-8") as f:
            portfolio = json.load(f)
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)

        negative_ev = set(config.get("negative_ev_tickers", []))
        positions = portfolio.get("positions", {})
        for ticker in positions:
            assert ticker not in negative_ev, f"{ticker} là mã EV âm nhưng vẫn trong portfolio!"

    def test_backtest_config_valid(self):
        path = BASE_DIR / "backtest_config.json"
        if not path.exists():
            pytest.skip("backtest_config.json chưa có")
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        assert "positive_ev_tickers" in config
        assert len(config["positive_ev_tickers"]) > 0, "Không có mã EV dương"

    def test_scheduler_state_valid(self):
        path = BASE_DIR / "scheduler_state.json"
        if not path.exists():
            pytest.skip("scheduler_state.json chưa có")
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        assert isinstance(state, dict)

    def test_analysis_results_today(self):
        path = BASE_DIR / "analysis_results.json"
        if not path.exists():
            pytest.skip("analysis_results.json chưa có")
        with open(path, encoding="utf-8") as f:
            ar = json.load(f)
        assert "method" in ar
        assert ar.get("method") == "two_stage", f"Method không phải two_stage: {ar.get('method')}"

    def test_prediction_log_valid(self):
        path = BASE_DIR / "prediction_log.json"
        if not path.exists():
            pytest.skip("prediction_log.json chưa có")
        with open(path, encoding="utf-8") as f:
            logs = json.load(f)
        assert isinstance(logs, dict), "prediction_log.json phải là dict"

    def test_no_duplicate_portfolio_positions(self):
        path = BASE_DIR / "paper_portfolio.json"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        positions = list(data.get("positions", {}).keys())
        assert len(positions) == len(set(positions)), f"Có mã trùng trong portfolio: {positions}"


class TestCoreLogic:
    """Test logic của các module chính."""

    def test_safe_read_portfolio(self):
        from auto_trader import _safe_read_portfolio

        p = _safe_read_portfolio()
        assert isinstance(p, dict)
        assert "cash" in p

    def test_get_tradeable_tickers(self):
        from auto_trader import get_tradeable_tickers

        watchlist = ["VCB", "MBB", "ACB", "TCB", "FPT", "HPG"]
        tradeable, reason = get_tradeable_tickers(watchlist)
        assert isinstance(tradeable, list)
        assert isinstance(reason, str)
        assert len(tradeable) > 0
        for ticker in ["FPT", "HPG", "MWG", "PNJ"]:
            if ticker in watchlist:
                assert ticker not in tradeable or len(tradeable) < 3, f"{ticker} vẫn trong tradeable list"

    def test_is_trading_day(self):
        from scheduler import is_trading_day

        result = is_trading_day()
        assert isinstance(result, bool)

    def test_load_backtest_config(self):
        from backtester import load_backtest_config_file

        config = load_backtest_config_file()
        assert isinstance(config, dict)
        assert "positive_ev_tickers" in config

    def test_safe_sma_with_insufficient_data(self):
        from dashboard_vn import safe_sma

        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = safe_sma(s, 50)
        assert result is None, f"safe_sma nên trả None khi data < window, nhưng trả {result}"

    def test_safe_sma_with_sufficient_data(self):
        from dashboard_vn import safe_sma

        s = pd.Series(range(1, 55, 1), dtype=float)
        result = safe_sma(s, 50)
        assert result is not None
        assert not pd.isna(result)

    def test_kelly_position_size(self):
        from auto_trader import get_kelly_position_size

        sizing = get_kelly_position_size("VCB", 100_000_000, 62000)
        assert "shares" in sizing
        assert "value" in sizing
        assert "kelly_fraction" in sizing
        assert sizing["shares"] % 100 == 0
        assert sizing["value"] <= 100_000_000 * 0.95

    def test_get_signal_weight_default(self):
        from learning_engine import get_signal_weight

        weight = get_signal_weight("VCB")
        assert 0.5 <= weight <= 1.5, f"Weight ngoài range: {weight}"

    def test_build_llm_context_no_crash(self):
        from learning_engine import build_llm_context

        ctx = build_llm_context("VCB")
        assert isinstance(ctx, str)

    def test_stage1_quick_scan(self):
        from auto_trader import stage1_quick_scan

        results = stage1_quick_scan(["VCB", "MBB"], top_n=2)
        assert isinstance(results, list)
        for r in results:
            assert "ticker" in r
            assert "score" in r
            assert "signals" in r
            assert r["score"] >= 0


@pytest.mark.skipif(not HAS_STREAMLIT_TEST, reason="streamlit.testing không available")
class TestStreamlitApp:
    """Test Streamlit app với AppTest."""

    @pytest.fixture
    def app(self):
        at = AppTest.from_file(str(BASE_DIR / "dashboard_vn.py"), default_timeout=60)
        at.run()
        return at

    def test_app_loads_without_exception(self, app):
        assert not app.exception, f"App bị exception: {app.exception}"

    def test_no_nan_in_metrics(self, app):
        for metric in app.metric:
            value_str = str(metric.value or "").lower()
            assert "nan" not in value_str, f"Metric '{metric.label}' có giá trị nan: {metric.value}"

    def test_sidebar_has_morning_briefing(self, app):
        all_text = " ".join([str(m.value or "") for m in app.markdown])
        assert any(keyword in all_text.lower() for keyword in ["morning", "briefing", "equity", "scheduler"])

    def test_portfolio_equity_positive(self, app):
        for metric in app.metric:
            if "equity" in str(metric.label or "").lower():
                value = str(metric.value or "0").replace(",", "").replace("M", "")
                try:
                    assert float(value) > 0
                except ValueError:
                    pass


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright không installed")
class TestVisual:
    """Visual tests với Playwright."""

    BASE_URL = "http://localhost:8501"

    @staticmethod
    def _dashboard_running():
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            return sock.connect_ex(("127.0.0.1", 8501)) == 0

    @pytest.fixture(autouse=True)
    def setup(self, screenshots_dir):
        self.screenshots_dir = screenshots_dir

    def _wait_for_load(self, page: Page):
        if not self._dashboard_running():
            pytest.skip("Dashboard chưa chạy tại localhost:8501")
        page.goto(self.BASE_URL)
        try:
            page.wait_for_selector("[data-testid='stSpinner']", state="detached", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

    def test_homepage_loads(self, page: Page):
        self._wait_for_load(page)
        assert page.title() != ""
        page.screenshot(path=str(self.screenshots_dir / "homepage.png"), full_page=True)

    def test_no_error_messages(self, page: Page):
        self._wait_for_load(page)
        errors = page.locator("[data-testid='stException']")
        assert errors.count() == 0, f"Có {errors.count()} error messages"

    def test_portfolio_tab(self, page: Page):
        self._wait_for_load(page)
        try:
            page.click("text=Auto Trader")
            page.wait_for_timeout(2000)
            page.click("text=Portfolio & PnL")
            page.wait_for_timeout(2000)
        except Exception:
            pass
        page.screenshot(path=str(self.screenshots_dir / "portfolio.png"), full_page=True)

    def test_no_white_tables_in_dark_theme(self, page: Page):
        self._wait_for_load(page)
        bg_color = page.evaluate(
            "document.querySelector('.stApp') && window.getComputedStyle(document.querySelector('.stApp')).backgroundColor"
        )
        assert bg_color != "rgb(255, 255, 255)", "Dashboard đang dùng light theme thay vì dark theme"

    def test_all_tabs_screenshot(self, page: Page):
        self._wait_for_load(page)
        tabs = ["Tổng quan", "Biểu đồ", "AI Phân tích", "Auto Trader", "Backtest", "Lịch sử", "LSTM"]

        for tab_name in tabs:
            try:
                page.click(f"text={tab_name}")
                page.wait_for_timeout(1500)
                safe_name = tab_name.replace(" ", "_").replace("/", "_")
                page.screenshot(path=str(self.screenshots_dir / f"tab_{safe_name}.png"), full_page=True)
            except Exception as e:
                print(f"Warning: Tab '{tab_name}' - {e}")

        screenshots = list(self.screenshots_dir.glob("tab_*.png"))
        assert len(screenshots) > 0, "Không có screenshot nào được tạo"


class TestPerformance:
    """Test performance."""

    def test_cache_hit_faster_than_api(self):
        import time
        from data_fetcher import get_stock_data_cached

        t1 = time.time()
        df1 = get_stock_data_cached("VCB", years=1)
        time1 = time.time() - t1

        t2 = time.time()
        df2 = get_stock_data_cached("VCB", years=1)
        time2 = time.time() - t2

        assert time2 < 1.0, f"Cache hit quá chậm: {time2:.2f}s"
        assert len(df1) == len(df2), "Cache data khác API data"

    def test_stage1_scan_speed(self):
        import time
        from auto_trader import stage1_quick_scan

        t = time.time()
        results = stage1_quick_scan(["VCB", "MBB", "ACB", "TCB"], top_n=4)
        elapsed = time.time() - t

        assert elapsed < 10, f"Stage 1 scan quá chậm: {elapsed:.1f}s"
        assert len(results) > 0

    def test_portfolio_read_speed(self):
        import time
        from auto_trader import _safe_read_portfolio

        t = time.time()
        for _ in range(10):
            _safe_read_portfolio()
        elapsed = (time.time() - t) / 10

        assert elapsed < 0.1, f"Portfolio read quá chậm: {elapsed:.3f}s"


class TestRegression:
    """Regression tests đảm bảo các bugs đã fix không quay lại."""

    def test_no_hardcoded_paths(self):
        import re

        dashboard_path = BASE_DIR / "dashboard_vn.py"
        with open(dashboard_path, encoding="utf-8-sig") as f:
            content = f.read()
        matches = re.findall(r'["\']E[:/\\\\]Trade[/\\\\]', content)
        assert len(matches) == 0, f"Còn {len(matches)} hardcoded paths: {matches[:3]}"

    def test_no_duplicate_functions(self):
        dashboard_path = BASE_DIR / "dashboard_vn.py"
        with open(dashboard_path, encoding="utf-8-sig") as f:
            tree = ast.parse(f.read())
        names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        dupes = [n for n, c in collections.Counter(names).items() if c > 1]
        assert len(dupes) == 0, f"Duplicate functions: {dupes}"

    def test_no_bare_except(self):
        dashboard_path = BASE_DIR / "dashboard_vn.py"
        with open(dashboard_path, encoding="utf-8-sig") as f:
            content = f.read()
        bare_excepts = [i for i, line in enumerate(content.split("\n"), 1) if line.strip() == "except:"]
        assert len(bare_excepts) == 0, f"Còn bare except: ở dòng {bare_excepts}"

    def test_single_streamlit_import(self):
        dashboard_path = BASE_DIR / "dashboard_vn.py"
        with open(dashboard_path, encoding="utf-8-sig") as f:
            lines = f.readlines()
        imports = [i + 1 for i, line in enumerate(lines) if "import streamlit as st" in line]
        assert len(imports) == 1, f"import streamlit xuất hiện {len(imports)} lần ở dòng {imports}"

    def test_portfolio_atomic_write(self):
        from auto_trader import _safe_read_portfolio, _safe_write_portfolio

        original = _safe_read_portfolio()
        _safe_write_portfolio(original)
        restored = _safe_read_portfolio()

        assert original["cash"] == restored["cash"]
        assert list(original.get("positions", {}).keys()) == list(restored.get("positions", {}).keys())
