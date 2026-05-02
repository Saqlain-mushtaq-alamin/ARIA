"""Browser automation helpers using Playwright."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote_plus
import os

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_TIMEOUT_MS = 15000
SEARCH_ENGINE_URLS = {
    "duckduckgo": "https://duckduckgo.com/",
    "ddg": "https://duckduckgo.com/",
    "google": "https://www.google.com/",
}
_LAST_URL: Optional[str] = None
_PLAYWRIGHT = None
_PERSISTENT_CONTEXT = None
_PERSISTENT_PAGE = None
_PROFILE_DIR = os.path.join("data", "playwright_profile")


def _normalize_url(url: str) -> str:
    trimmed = url.strip()
    if not trimmed:
        raise ValueError("URL is required")
    if any(ch.isspace() for ch in trimmed):
        raise ValueError("URL must not contain spaces")
    if not (
        "." in trimmed
        or trimmed.startswith("localhost")
        or ":" in trimmed
    ):
        raise ValueError("URL must include a domain or host")
    if not trimmed.startswith(("http://", "https://")):
        return f"https://{trimmed}"
    return trimmed


def _wait_for_first_selector(page, selectors: Sequence[str]) -> str:
    for selector in selectors:
        try:
            page.wait_for_selector(selector, state="visible")
            return selector
        except PlaywrightTimeoutError:
            continue
    raise PlaywrightTimeoutError("No matching selector became visible")


def _ensure_persistent_page(use_chrome: bool = True):
    global _PLAYWRIGHT, _PERSISTENT_CONTEXT, _PERSISTENT_PAGE
    if _PLAYWRIGHT is None:
        _PLAYWRIGHT = sync_playwright().start()

    if _PERSISTENT_CONTEXT is None or _PERSISTENT_CONTEXT.is_closed():
        os.makedirs(_PROFILE_DIR, exist_ok=True)
        if use_chrome:
            try:
                _PERSISTENT_CONTEXT = _PLAYWRIGHT.chromium.launch_persistent_context(
                    _PROFILE_DIR,
                    channel="chrome",
                    headless=False,
                )
            except Exception:
                _PERSISTENT_CONTEXT = _PLAYWRIGHT.chromium.launch_persistent_context(
                    _PROFILE_DIR,
                    headless=False,
                )
        else:
            _PERSISTENT_CONTEXT = _PLAYWRIGHT.chromium.launch_persistent_context(
                _PROFILE_DIR,
                headless=False,
            )

    if _PERSISTENT_PAGE is None or _PERSISTENT_PAGE.is_closed():
        pages = _PERSISTENT_CONTEXT.pages
        _PERSISTENT_PAGE = pages[0] if pages else _PERSISTENT_CONTEXT.new_page()
        _PERSISTENT_PAGE.set_default_timeout(DEFAULT_TIMEOUT_MS)

    return _PERSISTENT_PAGE


def _with_page(
    url: str,
    action,
    headless: bool = False,
    use_chrome: bool = True,
    keep_open: bool = True,
):
    normalized = _normalize_url(url)
    global _LAST_URL
    _LAST_URL = normalized

    if keep_open and not headless:
        page = _ensure_persistent_page(use_chrome=use_chrome)
        page.goto(normalized, wait_until="domcontentloaded")
        return action(page)

    with sync_playwright() as playwright:
        if use_chrome:
            try:
                browser = playwright.chromium.launch(
                    headless=headless,
                    channel="chrome",
                )
            except Exception:
                browser = playwright.chromium.launch(headless=headless)
        else:
            browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        page.goto(normalized, wait_until="domcontentloaded")
        result = action(page)
        context.close()
        browser.close()
        return result


def open_url(
    url: str,
    headless: bool = False,
    use_chrome: bool = True,
) -> str:
    """Launch a browser and open a URL, returning the page title."""

    def action(page):
        return page.title()

    return str(_with_page(url, action, headless=headless, use_chrome=use_chrome)).strip()


def search_web(
    query: str,
    max_results: int = 5,
    engine: str = "duckduckgo",
    headless: bool = False,
    use_chrome: bool = True,
) -> List[Dict[str, str]]:
    """Search the web and return a list of result dicts with title and url."""
    if not query or not query.strip():
        raise ValueError("Search query is required")

    engine_key = engine.strip().lower()
    engine_url = SEARCH_ENGINE_URLS.get(engine_key)
    if not engine_url:
        raise ValueError(f"Unknown search engine: {engine}")

    def action(page):
        encoded = quote_plus(query.strip())
        if engine_key == "google":
            search_url = f"https://www.google.com/search?q={encoded}"
        else:
            search_url = f"https://duckduckgo.com/?q={encoded}"
        page.goto(search_url, wait_until="domcontentloaded")
        try:
            if engine_key == "google":
                page.wait_for_selector("#search a:has(h3)")
            else:
                page.wait_for_selector("a.result__a")
        except PlaywrightTimeoutError:
            return []

        results: List[Dict[str, str]] = []
        if engine_key == "google":
            anchors = page.query_selector_all("#search a:has(h3)")
            for anchor in anchors[:max_results]:
                title_node = anchor.query_selector("h3")
                title = (title_node.text_content() if title_node else "") or ""
                title = title.strip()
                href = anchor.get_attribute("href") or ""
                if title and href:
                    results.append({"title": title, "url": href})
        else:
            for anchor in page.query_selector_all("a.result__a")[:max_results]:
                title = (anchor.text_content() or "").strip()
                href = anchor.get_attribute("href") or ""
                if title and href:
                    results.append({"title": title, "url": href})
        return results

    return _with_page(engine_url, action, headless=headless, use_chrome=use_chrome)


def click_element(
    selector: str,
    headless: bool = False,
    use_chrome: bool = True,
) -> str:
    """Open the last visited URL and click the matching selector."""
    if not selector or not selector.strip():
        raise ValueError("Selector is required")
    if not _LAST_URL:
        raise RuntimeError("No previous URL available. Call open_url() first.")

    def action(page):
        page.click(selector)
        return f"Clicked {selector}"

    return _with_page(_LAST_URL, action, headless=headless, use_chrome=use_chrome)


def fill_form(
    data: Dict[str, Any],
    headless: bool = False,
    use_chrome: bool = True,
) -> str:
    """Fill a form using selectors and values.

    Expected data shape:
    {
        "url": "https://example.com/form",
        "fields": {"#email": "user@example.com", "#name": "Ada"},
        "submit": "button[type='submit']"  # optional
    }
    """
    if not isinstance(data, dict):
        raise ValueError("Form data must be a dict")
    url = data.get("url")
    fields = data.get("fields")
    if not url or not fields:
        raise ValueError("Form data must include url and fields")
    if not isinstance(fields, dict):
        raise ValueError("fields must be a dict of selector to value")

    def action(page):
        for selector, value in fields.items():
            page.fill(str(selector), str(value))
        submit_selector = data.get("submit")
        if submit_selector:
            page.click(str(submit_selector))
        return "Form filled"

    return _with_page(str(url), action, headless=headless, use_chrome=use_chrome)


def extract_text(
    url: str,
    max_chars: int = 5000,
    headless: bool = False,
    use_chrome: bool = True,
) -> str:
    """Extract text content from the given URL."""

    def action(page):
        text = page.inner_text("body")
        cleaned = " ".join(text.split())
        if max_chars and len(cleaned) > max_chars:
            return cleaned[:max_chars]
        return cleaned

    return _with_page(url, action, headless=headless, use_chrome=use_chrome)
