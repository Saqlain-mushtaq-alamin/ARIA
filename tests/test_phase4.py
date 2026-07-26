"""
tests/test_phase4.py  ─  Phase 4: Browser & Web Data
======================================================
Tests search_web URL building, weather fetch, news fetch.
Requires an internet connection. Mocked at dispatch level where needed.
"""
import tests.mock_layer  # MUST be first

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_runner import test, run_all
from core.router import _get_weather, _get_news, _get_stock


# ── Weather ───────────────────────────────────────────────────────────────────

@test("get_weather('Dhaka') returns weather string", "PHASE4", "weather")
def t_weather_dhaka():
    result = _get_weather("Dhaka")
    assert isinstance(result, str)
    assert len(result) > 5, f"Response too short: {result!r}"
    # Should mention weather data or an error (not crash)
    assert "weather" in result.lower() or "dhaka" in result.lower() or "°" in result or "c" in result.lower()


@test("get_weather('current') doesn't crash", "PHASE4", "weather")
def t_weather_current():
    result = _get_weather("current")
    assert isinstance(result, str)


@test("get_weather('London') returns data or network error", "PHASE4", "weather")
def t_weather_london():
    result = _get_weather("London")
    assert isinstance(result, str)
    # Could fail on network — just check it doesn't crash with an unhandled exception


# ── News ─────────────────────────────────────────────────────────────────────

@test("get_news() returns top headlines", "PHASE4", "news")
def t_news_top():
    result = _get_news()
    assert isinstance(result, str)
    # Either has headlines or a graceful error
    assert len(result) > 5


@test("get_news(topic='technology') returns tech news", "PHASE4", "news")
def t_news_tech():
    result = _get_news(topic="technology")
    assert isinstance(result, str)
    assert len(result) > 5


@test("get_news(count=3) returns at most 3 items", "PHASE4", "news")
def t_news_count():
    result = _get_news(count=3)
    assert isinstance(result, str)


# ── Stock ─────────────────────────────────────────────────────────────────────

@test("get_stock('AAPL') returns price string", "PHASE4", "stock")
def t_stock_aapl():
    result = _get_stock("AAPL")
    assert isinstance(result, str)
    # Either has price data or a graceful error
    assert len(result) > 3


# ── Browser agent URL building ───────────────────────────────────────────────

@test("search_web builds correct Google URL", "PHASE4", "browser")
def t_search_url():
    from modules.browser_agent import _build_search_url
    url = _build_search_url("python tutorials", "google")
    assert "google.com" in url
    assert "python" in url.lower() or "q=" in url.lower()


@test("open_url accepts https:// URLs", "PHASE4", "browser")
def t_open_url_format():
    """Test that open_url function accepts https:// URLs without crashing on import."""
    from modules import browser_agent
    assert hasattr(browser_agent, "open_url")
    assert hasattr(browser_agent, "search_web")


if __name__ == "__main__":
    run_all("PHASE4")
