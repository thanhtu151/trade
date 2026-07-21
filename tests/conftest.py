"""pytest configuration for VN Stock Dashboard."""

from pathlib import Path
import os
import sys

import pytest


BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("PYTHONUTF8", "1")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: mark test as slow")
    config.addinivalue_line("markers", "visual: mark test as visual/screenshot")


@pytest.fixture(scope="session")
def base_dir():
    return BASE_DIR


@pytest.fixture(scope="session")
def screenshots_dir(base_dir):
    path = base_dir / "tests" / "screenshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def playwright():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture
def browser(playwright, pytestconfig):
    browser = playwright.chromium.launch(headless=not pytestconfig.getoption("--headed"))
    yield browser
    browser.close()


@pytest.fixture
def context(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1600})
    yield context
    context.close()


@pytest.fixture
def page(context):
    page = context.new_page()
    yield page
    page.close()
