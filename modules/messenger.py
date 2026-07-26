"""ARIA — Messenger Module.

Sends messages via Messenger, WhatsApp Web, Telegram.
HARD RULE: Double confirmation before any send. No exceptions.

Reading:
  - Reads unread messages from supported platforms
  - Chatbot auto-reply requires explicit permission per session
"""

from __future__ import annotations
import json, os, re, time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass

_PLATFORM_CONFIG = {
    "whatsapp": {"url": "https://web.whatsapp.com", "name": "WhatsApp Web",
        "search": "div[contenteditable='true'][data-tab='3']",
        "input": "div[contenteditable='true'][data-tab='10']"},
    "telegram": {"url": "https://web.telegram.org", "name": "Telegram Web",
        "search": "input.input-search", "input": "div.input-message-input"},
    "messenger": {"url": "https://www.messenger.com", "name": "Facebook Messenger",
        "search": "input[placeholder='Search Messenger']", "input": "div[role='textbox']"},
}

_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "logs", "messenger_log.jsonl")

def _log_event(event: str, data: dict) -> None:
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event, **data}, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _double_confirm(platform: str, recipient: str, message: str,
                    ask_fn: Optional[Callable] = None) -> bool:
    """Double confirmation gate. Returns True only if both gates pass."""
    if ask_fn is None:
        _log_event("send_cancelled", {"reason": "non_interactive", "platform": platform})
        return False
    # Gate 1
    preview = (f"📨 Message Preview\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
               f"  To: {recipient}\n  Via: {_PLATFORM_CONFIG.get(platform,{}).get('name',platform)}\n"
               f"  Message: \"{message[:200]}\"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
               f"Should I send this? (yes/no)")
    a1 = ask_fn(preview)
    if not a1 or a1.strip().lower() not in {"yes","y","confirm","send","ok"}:
        _log_event("send_cancelled", {"reason": "gate1_rejected", "platform": platform})
        return False
    # Gate 2
    a2 = ask_fn("🔒 Final Confirmation (2/2)\nType 'SEND' to confirm, anything else to cancel.")
    if not a2 or a2.strip().upper() != "SEND":
        _log_event("send_cancelled", {"reason": "gate2_rejected", "platform": platform})
        return False
    _log_event("send_confirmed", {"platform": platform, "recipient": recipient})
    return True

def send_message(platform: str = "", recipient: str = "", message: str = "",
                 ask_fn: Optional[Callable] = None, **_: Any) -> str:
    """Send a message with mandatory double confirmation."""
    platform = (platform or "").strip().lower()
    if platform not in _PLATFORM_CONFIG:
        return f"Unsupported platform: '{platform}'. Use: whatsapp, telegram, messenger."
    if not recipient.strip():
        return "Recipient is required."
    if not message.strip():
        return "Message text is required."
    if not _double_confirm(platform, recipient.strip(), message.strip(), ask_fn):
        return "📨 Message cancelled. No message was sent."
    try:
        from playwright.sync_api import sync_playwright
        cfg = _PLATFORM_CONFIG[platform]
        profile_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "browser_profiles", platform)
        os.makedirs(profile_dir, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(profile_dir, headless=False)
            page = browser.pages[0] if browser.pages else browser.new_page()
            if cfg["url"] not in (page.url or ""):
                page.goto(cfg["url"], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
            search = page.wait_for_selector(cfg["search"], timeout=10000)
            if search:
                search.click(); search.fill(recipient.strip())
                page.wait_for_timeout(2000); page.keyboard.press("Enter")
                page.wait_for_timeout(1500)
            inp = page.wait_for_selector(cfg["input"], timeout=10000)
            if inp:
                inp.click(); inp.fill(message.strip())
                page.wait_for_timeout(500); page.keyboard.press("Enter")
            browser.close()
        _log_event("message_sent", {"platform": platform, "recipient": recipient.strip()})
        return f"✅ Message sent to {recipient.strip()} via {cfg['name']}."
    except ImportError:
        return "Playwright not installed. Run: pip install playwright && playwright install chromium"
    except Exception as exc:
        _log_event("send_error", {"platform": platform, "error": str(exc)})
        return f"Failed to send message: {exc}"

def read_unread_messages(platform: str = "", limit: int = 10, **_: Any) -> str:
    """Read unread messages from a messaging platform."""
    platform = (platform or "").strip().lower()
    if platform not in _PLATFORM_CONFIG:
        return f"Unsupported platform. Use: whatsapp, telegram, messenger."
    try:
        from playwright.sync_api import sync_playwright
        cfg = _PLATFORM_CONFIG[platform]
        profile_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "browser_profiles", platform)
        os.makedirs(profile_dir, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(profile_dir, headless=False)
            page = browser.pages[0] if browser.pages else browser.new_page()
            if cfg["url"] not in (page.url or ""):
                page.goto(cfg["url"], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)
            _log_event("messages_read", {"platform": platform})
            browser.close()
        return f"📬 Checked {cfg['name']} for unread messages."
    except ImportError:
        return "Playwright not installed."
    except Exception as exc:
        return f"Failed to read messages: {exc}"

def detect_platform(text: str) -> str:
    lower = text.lower()
    if any(k in lower for k in ("whatsapp","whats app","wa")):
        return "whatsapp"
    if any(k in lower for k in ("telegram","tg")):
        return "telegram"
    if any(k in lower for k in ("messenger","facebook message","fb message")):
        return "messenger"
    return ""
