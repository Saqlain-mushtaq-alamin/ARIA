"""Route intents to system control actions.

Supports all original intents plus:
  - System: shutdown, restart, lock_screen, sleep, toggle_wifi,
            toggle_bluetooth, toggle_airplane, screenshot, set_brightness
  - Files:  open_file, save_file, create_file, delete_file,
            list_directory, move_file, copy_file
  - Web/info: get_weather, get_news, search_papers, get_stock
  - Multi-step: dispatches an ordered list of steps in sequence
  - Conversational: routed directly to the LLM for a text reply
"""

from __future__ import annotations

import subprocess
import ctypes
import os
import shutil
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from modules import browser_agent, system_control
from scheduler.tracker import create_schedule_from_text, edit_schedule, show_schedule, whats_next
from safety.harm_classifier import assess_risk, DANGEROUS, BLOCKED
from safety.recycle_buffer import safe_delete

# ── Upgrade modules (lazy-loaded to avoid circular imports) ──────────────────
def _social_comment(tone: str = "thoughtful", **_: Any) -> str:
    from modules.social_agent import comment_on_post
    return comment_on_post(tone=tone)

def _explain_selected(**_: Any) -> str:
    from modules.social_agent import explain_selected_text
    return explain_selected_text()

def _start_focus(task: str = "deep work", work_minutes: int = 0, **_: Any) -> str:
    from modules.focus_mode import start_focus_mode
    return start_focus_mode(task=task, work_min=work_minutes)

def _stop_focus(**_: Any) -> str:
    from modules.focus_mode import stop_focus_mode
    return stop_focus_mode()

def _decompose_goal(goal: str = "", deadline: str = "", **_: Any) -> str:
    from scheduler.goal_decomposer import decompose_goal
    return decompose_goal(goal=goal, deadline_str=deadline)

def _show_settings(section: str = "", **_: Any) -> str:
    from config.settings import format_settings
    return format_settings(section=section or None)

def _change_setting(key: str = "", value: Any = None, **_: Any) -> str:
    from config.settings import set_value
    if not key:
        return "Please specify which setting to change."
    return set_value(key, value)

def _query_knowledge(topic: str = "", **_: Any) -> str:
    from memory.knowledge_graph import query_formatted
    return query_formatted(topic)

def _show_habits(**_: Any) -> str:
    from memory.habit_tracker import get_all_habits_summary
    return get_all_habits_summary()

def _mark_habit(habit_name: str = "", **_: Any) -> str:
    from memory.habit_tracker import mark_habit_done
    return mark_habit_done(habit_name)

def _show_profile(**_: Any) -> str:
    from memory.user_profile import get_profile_summary
    return get_profile_summary()

def _download(
    target: str = "",
    mode: str = "auto",
    output_name: str = "",
    downloads_dir: str = "",
    open_folder: bool = False,
    open_file: bool = False,
    allow_large: bool = False,
    strict: bool = False,
    **_: Any,
) -> str:
    from modules.downloader import download_command
    return download_command(
        target=target,
        mode=mode,
        output_name=output_name or None,
        downloads_dir=downloads_dir or None,
        open_folder=bool(open_folder),
        open_file=bool(open_file),
        allow_large=bool(allow_large),
        strict=bool(strict),
    )

# ── Messenger module (lazy-loaded) ───────────────────────────────────────
def _send_message(platform: str = "", recipient: str = "", message: str = "",
                  ask_fn: Optional[Callable] = None, **kw: Any) -> str:
    from modules.messenger import send_message
    return send_message(platform=platform, recipient=recipient, message=message,
                        ask_fn=ask_fn, **kw)

def _read_messages(platform: str = "", limit: int = 10, **kw: Any) -> str:
    from modules.messenger import read_unread_messages
    return read_unread_messages(platform=platform, limit=limit, **kw)

# ── Media control module (lazy-loaded) ───────────────────────────────────
def _media_play_pause(app: str = "", **kw: Any) -> str:
    from modules.media_control import media_play_pause
    return media_play_pause(app=app, **kw)

def _media_next(app: str = "", **kw: Any) -> str:
    from modules.media_control import media_next
    return media_next(app=app, **kw)

def _media_prev(app: str = "", **kw: Any) -> str:
    from modules.media_control import media_prev
    return media_prev(app=app, **kw)

def _media_volume(level: int = 50, app: str = "", **kw: Any) -> str:
    from modules.media_control import media_volume
    return media_volume(level=level, app=app, **kw)

def _media_search(query: str = "", app: str = "", **kw: Any) -> str:
    from modules.media_control import media_search
    return media_search(query=query, app=app, **kw)

def _media_stop(**kw: Any) -> str:
    from modules.media_control import media_stop
    return media_stop(**kw)

# ── Notifier module (lazy-loaded) ────────────────────────────────────────
def _send_notification(title: str = "ARIA", message: str = "", **kw: Any) -> str:
    from modules.notifier import send_toast
    return send_toast(title=title, message=message, **kw)

def _notify_reminder(task: str = "", time_str: str = "", **kw: Any) -> str:
    from modules.notifier import notify_reminder
    return notify_reminder(task=task, time_str=time_str, **kw)

_GESTURE_CONTROLLER_PROC: Optional[subprocess.Popen] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helper: resolve common path aliases
# ─────────────────────────────────────────────────────────────────────────────

_PATH_ALIASES: Dict[str, str] = {
    "desktop": str(Path.home() / "Desktop"),
    "downloads": str(Path.home() / "Downloads"),
    "documents": str(Path.home() / "Documents"),
    "pictures": str(Path.home() / "Pictures"),
    "music": str(Path.home() / "Music"),
    "videos": str(Path.home() / "Videos"),
    "home": str(Path.home()),
}


def _resolve_path(raw: str) -> str:
    """Expand aliases, env vars, and ~ in a path string."""
    if not raw:
        return raw
    lower = raw.strip().lower()
    for alias, real in _PATH_ALIASES.items():
        if lower == alias or lower.startswith(alias + "/") or lower.startswith(alias + "\\"):
            raw = real + raw[len(alias):]
            break
    return str(Path(os.path.expandvars(os.path.expanduser(raw))))


# ─────────────────────────────────────────────────────────────────────────────
# System power / connectivity handlers
# ─────────────────────────────────────────────────────────────────────────────

def _shutdown(**_: Any) -> str:
    subprocess.run(["shutdown", "/s", "/t", "5"], check=False)
    return "Shutting down in 5 seconds. Run 'shutdown /a' to cancel."


def _restart(**_: Any) -> str:
    subprocess.run(["shutdown", "/r", "/t", "5"], check=False)
    return "Restarting in 5 seconds. Run 'shutdown /a' to cancel."


def _lock_screen(**_: Any) -> str:
    ctypes.windll.user32.LockWorkStation()
    return "Screen locked."


def _sleep(**_: Any) -> str:
    subprocess.run(
        ["powercfg", "-h", "off"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        check=False,
    )
    return "Going to sleep."


def _toggle_wifi(state: str = "toggle", **_: Any) -> str:
    state = state.strip().lower()
    if state in {"on", "enable"}:
        subprocess.run(["netsh", "interface", "set", "interface", "Wi-Fi", "enabled"], check=False)
        return "Wi-Fi enabled."
    elif state in {"off", "disable"}:
        subprocess.run(["netsh", "interface", "set", "interface", "Wi-Fi", "disabled"], check=False)
        return "Wi-Fi disabled."
    else:
        # Toggle: check current status first
        result = subprocess.run(
            ["netsh", "interface", "show", "interface", "Wi-Fi"],
            capture_output=True, text=True
        )
        if "Enabled" in result.stdout:
            subprocess.run(["netsh", "interface", "set", "interface", "Wi-Fi", "disabled"], check=False)
            return "Wi-Fi toggled OFF."
        else:
            subprocess.run(["netsh", "interface", "set", "interface", "Wi-Fi", "enabled"], check=False)
            return "Wi-Fi toggled ON."


def _toggle_bluetooth(state: str = "toggle", **_: Any) -> str:
    """Toggle Bluetooth using Windows radio management via PowerShell."""
    state = state.strip().lower()
    ps_on = (
        "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
        "$asTask = [System.WindowsRuntimeSystemExtensions]::AsTask; "
        "$adapter = [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime]; "
        "$radios = $asTask.Invoke($adapter::GetRadiosAsync()).Result; "
        "$bt = $radios | Where-Object {$_.Kind -eq 'Bluetooth'}; "
        "if ($bt) { $asTask.Invoke($bt.SetStateAsync('On')).Wait() }"
    )
    ps_off = ps_on.replace("'On'", "'Off'")
    script = ps_on if state in {"on", "enable"} else ps_off
    subprocess.run(["powershell", "-Command", script], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    label = "enabled" if state in {"on", "enable"} else "disabled"
    return f"Bluetooth {label}."


def _toggle_airplane(state: str = "toggle", **_: Any) -> str:
    """Toggle airplane mode via PowerShell radio manager."""
    state = state.strip().lower()
    if state in {"on", "enable"}:
        value = "1"
    elif state in {"off", "disable"}:
        value = "0"
    else:
        value = "1"  # default toggle → on; caller can pass explicit state
    script = (
        f"$reg = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\RadioManagement\\SystemRadioState'; "
        f"Set-ItemProperty -Path $reg -Name '(Default)' -Value {value} -Type DWord; "
        f"Restart-Service -Name 'RmSvc' -Force -ErrorAction SilentlyContinue"
    )
    subprocess.run(["powershell", "-Command", script], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    label = "ON" if value == "1" else "OFF"
    return f"Airplane mode turned {label}. (A restart may be required for full effect.)"


def _screenshot(path: str | None = None, **_: Any) -> str:
    try:
        import pyautogui
        img = pyautogui.screenshot()
        if not path:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = str(Path.home() / "Desktop" / f"screenshot_{ts}.png")
        else:
            path = _resolve_path(path)
        img.save(path)
        return f"Screenshot saved to: {path}"
    except Exception as exc:
        return f"Screenshot failed: {exc}"


def _set_brightness(level: int | str = 50, **_: Any) -> str:
    try:
        level = max(0, min(100, int(level)))
        script = (
            f"$monitor = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods; "
            f"$monitor.WmiSetBrightness(1, {level})"
        )
        subprocess.run(["powershell", "-Command", script], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Brightness set to {level}%."
    except Exception as exc:
        return f"Failed to set brightness: {exc}"


def _open_folder(path: str, **_: Any) -> str:
    return system_control.open_folder(path)


def _activate_kinetic_mode(**_: Any) -> str:
    """Start gesture controller from core routing layer (works in UI and headless)."""
    global _GESTURE_CONTROLLER_PROC
    if _GESTURE_CONTROLLER_PROC is not None and _GESTURE_CONTROLLER_PROC.poll() is None:
        return "Kinetic mode is already running. Focus the webcam window and press Q to quit."

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "vision" / "gesture_contoll" / "gesture_controller.py"
    if not script_path.exists():
        return "Gesture controller not found at vision/gesture_contoll/gesture_controller.py."

    try:
        _GESTURE_CONTROLLER_PROC = subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(repo_root),
        )
        return "Kinetic mode activated. A webcam window should open now. Press Q in that window to stop."
    except Exception as exc:
        return f"Failed to activate kinetic mode: {exc}"


def _deactivate_kinetic_mode(**_: Any) -> str:
    global _GESTURE_CONTROLLER_PROC
    proc = _GESTURE_CONTROLLER_PROC
    _GESTURE_CONTROLLER_PROC = None
    if proc is None or proc.poll() is not None:
        return "Kinetic mode is not running."
    try:
        proc.terminate()
        return "Kinetic mode deactivated."
    except Exception as exc:
        return f"Failed to deactivate kinetic mode: {exc}"


# ─────────────────────────────────────────────────────────────────────────────
# File operation handlers
# ─────────────────────────────────────────────────────────────────────────────

def _open_file(path: str, **_: Any) -> str:
    real = _resolve_path(path)
    if not os.path.exists(real):
        return f"File not found: {real}"
    os.startfile(real)
    return f"Opened: {real}"


def _save_file(path: str, content: str = "", **_: Any) -> str:
    real = _resolve_path(path)
    os.makedirs(os.path.dirname(real) or ".", exist_ok=True)
    with open(real, "w", encoding="utf-8") as f:
        f.write(content)
    return f"File saved to: {real}"


def _create_file(path: str, content: str = "", **_: Any) -> str:
    return _save_file(path, content)


def _delete_file(path: str, **_: Any) -> str:
    real = _resolve_path(path)
    return safe_delete(real)


def _list_directory(path: str = ".", **_: Any) -> str:
    real = _resolve_path(path)
    if not os.path.isdir(real):
        return f"Not a directory: {real}"
    entries = os.listdir(real)
    if not entries:
        return f"Directory is empty: {real}"
    lines = []
    for e in sorted(entries):
        full = os.path.join(real, e)
        tag = "[DIR] " if os.path.isdir(full) else "[FILE]"
        lines.append(f"  {tag} {e}")
    return f"Contents of {real}:\n" + "\n".join(lines)


def _move_file(source: str, destination: str, **_: Any) -> str:
    src = _resolve_path(source)
    dst = _resolve_path(destination)
    if not os.path.exists(src):
        return f"Source not found: {src}"
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.move(src, dst)
    return f"Moved: {src} → {dst}"


def _copy_file(source: str, destination: str, **_: Any) -> str:
    src = _resolve_path(source)
    dst = _resolve_path(destination)
    if not os.path.exists(src):
        return f"Source not found: {src}"
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return f"Copied: {src} → {dst}"


# ─────────────────────────────────────────────────────────────────────────────
# Internet data retrieval handlers
# ─────────────────────────────────────────────────────────────────────────────

def _get_weather(location: str = "current", **_: Any) -> str:
    """Fetch weather using wttr.in (no API key needed)."""
    try:
        import urllib.request
        if location == "current" or not location.strip():
            loc = ""
        else:
            loc = location.strip().replace(" ", "+")
        url = f"https://wttr.in/{loc}?format=4"
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = resp.read().decode("utf-8").strip()
        return f"Weather: {data}"
    except Exception as exc:
        return f"Could not fetch weather: {exc}"


def _get_news(topic: str = "", count: int = 5, **_: Any) -> str:
    """Fetch top news headlines using NewsAPI (free tier) or Google News RSS."""
    try:
        import urllib.request
        import xml.etree.ElementTree as ET
        count = min(int(count), 10)
        if topic:
            query = topic.strip().replace(" ", "+")
            url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        else:
            url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
        with urllib.request.urlopen(url, timeout=10) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")[:count]
        if not items:
            return "No news found."
        lines = [f"Top {len(items)} headlines{' on ' + topic if topic else ''}:"]
        for i, item in enumerate(items, 1):
            title = item.findtext("title", "No title").strip()
            link = item.findtext("link", "").strip()
            lines.append(f"  {i}. {title}\n     {link}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not fetch news: {exc}"


def _search_papers(query: str, source: str = "arxiv", **_: Any) -> str:
    """Search research papers via ArXiv, Semantic Scholar, or PubMed."""
    try:
        import urllib.request, urllib.parse
        source = (source or "arxiv").strip().lower()

        if source == "arxiv":
            q = urllib.parse.quote(query)
            url = f"https://export.arxiv.org/api/query?search_query=all:{q}&start=0&max_results=5"
            with urllib.request.urlopen(url, timeout=10) as resp:
                xml_data = resp.read().decode("utf-8")
            # Parse Atom feed
            import xml.etree.ElementTree as ET
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            root = ET.fromstring(xml_data)
            entries = root.findall("atom:entry", ns)
            if not entries:
                return "No papers found on ArXiv."
            lines = [f"ArXiv papers on '{query}':"]
            for i, entry in enumerate(entries, 1):
                title = (entry.findtext("atom:title", "", ns) or "").replace("\n", " ").strip()
                link_el = entry.find("atom:id", ns)
                link = (link_el.text or "").strip() if link_el is not None else ""
                authors = [a.findtext("atom:name", "", ns) for a in entry.findall("atom:author", ns)]
                lines.append(f"  {i}. {title}")
                lines.append(f"     Authors: {', '.join(authors[:3])}")
                lines.append(f"     Link: {link}")
            return "\n".join(lines)

        elif source in {"scholar", "semantic"}:
            q = urllib.parse.quote(query)
            url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={q}&fields=title,authors,year,url&limit=5"
            req = urllib.request.Request(url, headers={"User-Agent": "ARIA-Assistant/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            papers = data.get("data", [])
            if not papers:
                return "No papers found on Semantic Scholar."
            lines = [f"Semantic Scholar papers on '{query}':"]
            for i, p in enumerate(papers, 1):
                title = p.get("title", "No title")
                year = p.get("year", "?")
                link = p.get("url", "")
                authors = [a.get("name", "") for a in p.get("authors", [])[:3]]
                lines.append(f"  {i}. {title} ({year})")
                lines.append(f"     Authors: {', '.join(authors)}")
                lines.append(f"     Link: {link}")
            return "\n".join(lines)

        elif source == "pubmed":
            q = urllib.parse.quote(query)
            search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={q}&retmax=5&retmode=json"
            with urllib.request.urlopen(search_url, timeout=10) as resp:
                search_data = json.loads(resp.read())
            ids = search_data.get("esearchresult", {}).get("idlist", [])
            if not ids:
                return "No PubMed papers found."
            id_str = ",".join(ids)
            fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={id_str}&retmode=json"
            with urllib.request.urlopen(fetch_url, timeout=10) as resp:
                fetch_data = json.loads(resp.read())
            result_obj = fetch_data.get("result", {})
            lines = [f"PubMed papers on '{query}':"]
            for uid in ids:
                paper = result_obj.get(uid, {})
                title = paper.get("title", "No title")
                authors_list = [a.get("name", "") for a in paper.get("authors", [])[:3]]
                pub_date = paper.get("pubdate", "?")
                lines.append(f"  - {title} ({pub_date})")
                lines.append(f"    Authors: {', '.join(authors_list)}")
                lines.append(f"    PubMed ID: {uid} → https://pubmed.ncbi.nlm.nih.gov/{uid}/")
            return "\n".join(lines)

        return f"Unknown source '{source}'. Use: arxiv, scholar, pubmed."
    except Exception as exc:
        return f"Paper search failed: {exc}"


import json as _json  # needed inside _search_papers


def _get_stock(symbol: str, **_: Any) -> str:
    """Fetch stock price via Yahoo Finance (no API key needed)."""
    try:
        import urllib.request
        symbol = symbol.strip().upper()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice", "N/A")
        currency = meta.get("currency", "USD")
        name = meta.get("shortName", symbol)
        change = meta.get("regularMarketChangePercent", 0)
        return f"{name} ({symbol}): {currency} {price:.2f}  ({change:+.2f}%)"
    except Exception as exc:
        return f"Could not fetch stock data: {exc}"


# ─────────────────────────────────────────────────────────────────────────────
# Intent registry
# ─────────────────────────────────────────────────────────────────────────────

INTENT_REGISTRY: Dict[str, Callable[..., Any]] = {
    # Original system control
    "open_app":         system_control.open_app,
    "close_window":     system_control.close_window,
    "set_volume":       system_control.set_volume,
    "get_clipboard":    system_control.get_clipboard,
    "type_text":        system_control.type_text,

    # New system control
    "shutdown":         _shutdown,
    "restart":          _restart,
    "lock_screen":      _lock_screen,
    "sleep":            _sleep,
    "toggle_wifi":      system_control.toggle_wifi,
    "toggle_bluetooth": system_control.toggle_bluetooth,
    "toggle_airplane":  _toggle_airplane,
    "screenshot":       _screenshot,
    "set_brightness":   system_control.set_brightness,
    "open_folder":      _open_folder,
    "activate_kinetic_mode": _activate_kinetic_mode,
    "deactivate_kinetic_mode": _deactivate_kinetic_mode,

    # File operations
    "open_file":        _open_file,
    "save_file":        _save_file,
    "create_file":      _create_file,
    "delete_file":      _delete_file,
    "list_directory":   _list_directory,
    "move_file":        _move_file,
    "copy_file":        _copy_file,

    # Browser
    "open_url":         browser_agent.open_url,
    "search_web":       browser_agent.search_web,
    "click_element":    browser_agent.click_element,
    "fill_form":        browser_agent.fill_form,
    "extract_text":     browser_agent.extract_text,

    # Internet data
    "get_weather":      _get_weather,
    "get_news":         _get_news,
    "search_papers":    _search_papers,
    "get_stock":        _get_stock,
    "download":         _download,

    # Scheduler
    "create_schedule":  create_schedule_from_text,
    "show_schedule":    show_schedule,
    "whats_next":       whats_next,
    "edit_schedule":    edit_schedule,

    # ── Upgrade features ─────────────────────────────────────────────────
    "comment_on_post":    _social_comment,
    "explain_selected":   _explain_selected,
    "start_focus_mode":   _start_focus,
    "stop_focus_mode":    _stop_focus,
    "decompose_goal":     _decompose_goal,
    "show_settings":      _show_settings,
    "change_setting":     _change_setting,
    "query_knowledge":    _query_knowledge,
    "show_habits":        _show_habits,
    "mark_habit":         _mark_habit,
    "show_profile":       _show_profile,

    # ── Messenger ────────────────────────────────────────────────────────
    "send_message":       _send_message,
    "read_messages":      _read_messages,

    # ── Media control ────────────────────────────────────────────────────
    "media_play_pause":   _media_play_pause,
    "media_next":         _media_next,
    "media_prev":         _media_prev,
    "media_volume":       _media_volume,
    "media_search":       _media_search,
    "media_stop":         _media_stop,

    # ── Notifier ─────────────────────────────────────────────────────────
    "send_notification":  _send_notification,
    "notify_reminder":    _notify_reminder,
}

INTENT_ALIASES: Dict[str, str] = {
    # App control
    "open":             "open_app",
    "open_application": "open_app",
    "launch":           "open_app",
    "launch_app":       "open_app",
    "start":            "open_app",
    "close":            "close_window",
    "close_app":        "close_window",
    "quit":             "close_window",
    "exit_app":         "close_window",
    # Volume
    "volume":           "set_volume",
    "volume_change":    "set_volume",
    "change_volume":    "set_volume",
    "set_sound":        "set_volume",
    # Clipboard
    "clipboard":        "get_clipboard",
    "read_clipboard":   "get_clipboard",
    # Typing
    "type":             "type_text",
    "typing":           "type_text",
    "write":            "type_text",
    # Power
    "shut_down":        "shutdown",
    "power_off":        "shutdown",
    "turn_off":         "shutdown",
    "reboot":           "restart",
    "lock":             "lock_screen",
    # Connectivity
    "wifi":             "toggle_wifi",
    "bluetooth":        "toggle_bluetooth",
    "airplane_mode":    "toggle_airplane",
    "flight_mode":      "toggle_airplane",
    "open_folder":      "open_folder",
    "folder_open":      "open_folder",
    "open_directory":   "open_folder",
    "activate_kinetic_mode": "activate_kinetic_mode",
    "start_kinetic_mode": "activate_kinetic_mode",
    "kinetic_mode_on":  "activate_kinetic_mode",
    "deactivate_kinetic_mode": "deactivate_kinetic_mode",
    "stop_kinetic_mode": "deactivate_kinetic_mode",
    "kinetic_mode_off": "deactivate_kinetic_mode",
    # Files
    "open_file":        "open_file",
    "file_open":        "open_file",
    "save":             "save_file",
    "write_file":       "save_file",
    "make_file":        "create_file",
    "new_file":         "create_file",
    "delete":           "delete_file",
    "remove_file":      "delete_file",
    "list":             "list_directory",
    "list_files":       "list_directory",
    "ls":               "list_directory",
    "move":             "move_file",
    "copy":             "copy_file",
    # Browser
    "browse":           "open_url",
    "go_to":            "open_url",
    "visit":            "open_url",
    "navigate":         "open_url",
    "search":           "search_web",
    "web_search":       "search_web",
    "google":           "search_web",
    "click":            "click_element",
    "form_fill":        "fill_form",
    "extract":          "extract_text",
    # Info
    "weather":          "get_weather",
    "news":             "get_news",
    "headlines":        "get_news",
    "papers":           "search_papers",
    "research":         "search_papers",
    "arxiv":            "search_papers",
    "stock":            "get_stock",
    "price":            "get_stock",
    "download":         "download",
    "download_file":    "download",
    "downloader":       "download",
    # Scheduler
    "schedule":         "create_schedule",
    "plan_day":         "create_schedule",
    "make_schedule":    "create_schedule",
    "show_plan":        "show_schedule",
    "show_timeline":    "show_schedule",
    "next_task":        "whats_next",
    "what_next":        "whats_next",
    "edit_plan":        "edit_schedule",
    # Upgrade features
    "comment":          "comment_on_post",
    "generate_comment": "comment_on_post",
    "social_comment":   "comment_on_post",
    "explain":          "explain_selected",
    "explain_text":     "explain_selected",
    "explain_this":     "explain_selected",
    "focus":            "start_focus_mode",
    "focus_mode":       "start_focus_mode",
    "deep_work":        "start_focus_mode",
    "start_pomodoro":   "start_focus_mode",
    "stop_focus":       "stop_focus_mode",
    "end_focus":        "stop_focus_mode",
    "break_goal":       "decompose_goal",
    "plan_goal":        "decompose_goal",
    "goal":             "decompose_goal",
    "settings":         "show_settings",
    "preferences":      "show_settings",
    "config":           "show_settings",
    "set_setting":      "change_setting",
    "update_setting":   "change_setting",
    "knowledge":        "query_knowledge",
    "knowledge_graph":  "query_knowledge",
    "habits":           "show_habits",
    "my_habits":        "show_habits",
    "habit_done":       "mark_habit",
    "log_habit":        "mark_habit",
    "profile":          "show_profile",
    "my_profile":       "show_profile",
    # Messenger
    "message":          "send_message",
    "text_message":     "send_message",
    "send_text":        "send_message",
    "send_msg":         "send_message",
    "read_message":     "read_messages",
    "check_messages":   "read_messages",
    "unread_messages":  "read_messages",
    # Media control
    "play":             "media_play_pause",
    "pause":            "media_play_pause",
    "play_pause":       "media_play_pause",
    "play_music":       "media_play_pause",
    "pause_music":      "media_play_pause",
    "next_track":       "media_next",
    "skip":             "media_next",
    "skip_track":       "media_next",
    "previous_track":   "media_prev",
    "prev_track":       "media_prev",
    "media_volume":     "media_volume",
    "music_volume":     "media_volume",
    "search_music":     "media_search",
    "find_song":        "media_search",
    "play_song":        "media_search",
    "stop_music":       "media_stop",
    # Notifier
    "notify":           "send_notification",
    "notification":     "send_notification",
    "toast":            "send_notification",
    "reminder":         "notify_reminder",
    "set_reminder":     "notify_reminder",
}


def _normalize_intent(intent: str) -> str:
    key = intent.strip().lower().replace(" ", "_").replace("-", "_")
    return INTENT_ALIASES.get(key, key)


# ─────────────────────────────────────────────────────────────────────────────
# Single-intent dispatch
# ─────────────────────────────────────────────────────────────────────────────

def dispatch_intent(payload: Dict[str, Any]) -> Any:
    """Dispatch a single intent payload to the appropriate handler function."""
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a dict")

    # ── Safety enforcement (hard gate) ────────────────────────────────────
    # NOTE: The agent/UI may also do safety checks, but the router is the
    # final choke-point to prevent bypass.
    assessment = assess_risk(payload)
    if assessment.level == BLOCKED:
        raise PermissionError(f"Blocked by safety policy: {assessment.reason}")
    if assessment.level == DANGEROUS and not bool(payload.get("confirmed", False)):
        raise PermissionError(
            f"Dangerous action requires explicit confirmation: {assessment.intent}"
        )

    intent = payload.get("intent")
    if not intent:
        raise ValueError("Missing intent")

    intent = _normalize_intent(str(intent))
    handler = INTENT_REGISTRY.get(intent)
    if handler is None:
        raise KeyError(f"Unknown intent: '{intent}'")

    parameters: Dict[str, Any] = payload.get("parameters") or {}
    if not isinstance(parameters, dict):
        parameters = {}

    # ── Intents with strict argument handling ───────────────────────────────

    if intent in {"open_app", "close_window"}:
        name = (parameters.get("app_name") or parameters.get("name") or payload.get("app") or "")
        if not name:
            raise ValueError(f"App name is required for {intent}")
        return handler(str(name))

    if intent == "set_volume":
        level = parameters.get("level") or parameters.get("volume") or parameters.get("value")
        if level is None:
            raise ValueError("Volume level is required")
        return handler(int(level))

    if intent == "type_text":
        text = parameters.get("text") or parameters.get("content") or ""
        if not str(text).strip():
            raise ValueError("Text is required for type_text")
        return handler(str(text))

    if intent == "get_clipboard":
        return handler()

    if intent == "open_url":
        url = parameters.get("url") or payload.get("url") or ""
        if not url:
            raise ValueError("URL is required")
        return handler(str(url),
                       headless=bool(parameters.get("headless", False)),
                       use_chrome=bool(parameters.get("use_chrome", True)))

    if intent == "search_web":
        query = parameters.get("query") or payload.get("query") or ""
        if not query:
            raise ValueError("Search query is required")
        return handler(str(query),
                       max_results=int(parameters.get("max_results", 5)),
                       engine=str(parameters.get("engine", "google")),
                       headless=bool(parameters.get("headless", False)),
                       use_chrome=bool(parameters.get("use_chrome", True)))

    if intent == "click_element":
        selector = parameters.get("selector") or payload.get("selector") or ""
        if not selector:
            raise ValueError("Selector is required")
        return handler(str(selector),
                       headless=bool(parameters.get("headless", False)),
                       use_chrome=bool(parameters.get("use_chrome", True)))

    if intent == "fill_form":
        form_data = parameters or {}
        return handler(form_data,
                       headless=bool(form_data.get("headless", False)),
                       use_chrome=bool(form_data.get("use_chrome", True)))

    if intent == "extract_text":
        url = parameters.get("url") or payload.get("url") or ""
        if not url:
            raise ValueError("URL is required")
        return handler(str(url),
                       max_chars=int(parameters.get("max_chars", 5000)),
                       headless=bool(parameters.get("headless", False)),
                       use_chrome=bool(parameters.get("use_chrome", True)))

    if intent == "toggle_wifi":
        return handler(state=str(parameters.get("state", "toggle")))

    if intent == "toggle_bluetooth":
        return handler(state=str(parameters.get("state", "toggle")))

    if intent == "toggle_airplane":
        return handler(state=str(parameters.get("state", "toggle")))

    if intent == "screenshot":
        return handler(path=parameters.get("path"))

    if intent == "set_brightness":
        level = parameters.get("level", 50)
        return handler(level=int(level))

    if intent == "open_file":
        path = parameters.get("path") or parameters.get("file") or ""
        if not path:
            raise ValueError("File path is required")
        return handler(path=str(path))

    if intent == "open_folder":
        path = parameters.get("path") or parameters.get("directory") or ""
        if not path:
            raise ValueError("Folder path is required")
        return handler(path=str(path))

    if intent in {"save_file", "create_file"}:
        path = parameters.get("path") or parameters.get("file") or ""
        if not path:
            raise ValueError("File path is required")
        content = str(parameters.get("content") or "")
        return handler(path=str(path), content=content)

    if intent == "delete_file":
        path = parameters.get("path") or parameters.get("file") or ""
        if not path:
            raise ValueError("File path is required")
        return handler(path=str(path))

    if intent == "list_directory":
        path = parameters.get("path") or parameters.get("directory") or "."
        return handler(path=str(path))

    if intent == "move_file":
        src = parameters.get("source") or parameters.get("from") or ""
        dst = parameters.get("destination") or parameters.get("to") or ""
        if not src or not dst:
            raise ValueError("Source and destination are required for move_file")
        return handler(source=str(src), destination=str(dst))

    if intent == "copy_file":
        src = parameters.get("source") or parameters.get("from") or ""
        dst = parameters.get("destination") or parameters.get("to") or ""
        if not src or not dst:
            raise ValueError("Source and destination are required for copy_file")
        return handler(source=str(src), destination=str(dst))

    if intent == "get_weather":
        location = parameters.get("location") or "current"
        return handler(location=str(location))

    if intent == "get_news":
        return handler(topic=str(parameters.get("topic") or ""),
                       count=int(parameters.get("count", 5)))

    if intent == "search_papers":
        query = parameters.get("query") or ""
        if not query:
            raise ValueError("Query is required for search_papers")
        return handler(query=str(query),
                       source=str(parameters.get("source") or "arxiv"))

    if intent == "get_stock":
        symbol = parameters.get("symbol") or ""
        if not symbol:
            raise ValueError("Stock symbol is required")
        return handler(symbol=str(symbol))

    if intent == "download":
        target = (
            parameters.get("url")
            or parameters.get("target")
            or parameters.get("query")
            or parameters.get("title")
            or parameters.get("link")
            or ""
        )
        if not str(target).strip():
            raise ValueError("Download target is required")
        return handler(
            target=str(target),
            mode=str(parameters.get("mode") or parameters.get("type") or parameters.get("kind") or "auto"),
            output_name=str(parameters.get("output_name") or parameters.get("output") or parameters.get("filename") or ""),
            downloads_dir=str(parameters.get("downloads_dir") or parameters.get("download_dir") or parameters.get("folder") or ""),
            open_folder=bool(parameters.get("open_folder", False)),
            open_file=bool(parameters.get("open_file", False)),
            allow_large=bool(parameters.get("allow_large", False)),
            strict=bool(parameters.get("strict", False)),
        )

    # ── Scheduler intents ───────────────────────────────────────────────────
    if intent == "create_schedule":
        text = parameters.get("text") or ""
        return handler(text, use_reference=bool(parameters.get("use_reference", False)))

    if intent in {"show_schedule", "whats_next"}:
        return handler()

    if intent == "edit_schedule":
        command = parameters.get("command") or ""
        return handler(command)

    # ── Upgrade feature intents ─────────────────────────────────────────────
    if intent == "comment_on_post":
        return handler(tone=str(parameters.get("tone", "thoughtful")))

    if intent == "explain_selected":
        return handler()

    if intent == "start_focus_mode":
        return handler(
            task=str(parameters.get("task", "deep work")),
            work_minutes=int(parameters.get("work_minutes", 0)),
        )

    if intent == "stop_focus_mode":
        return handler()

    if intent == "decompose_goal":
        return handler(
            goal=str(parameters.get("goal", "")),
            deadline=str(parameters.get("deadline", "")),
        )

    if intent == "show_settings":
        return handler(section=str(parameters.get("section", "")))

    if intent == "change_setting":
        return handler(
            key=str(parameters.get("key", "")),
            value=parameters.get("value"),
        )

    if intent == "query_knowledge":
        return handler(topic=str(parameters.get("topic", "")))

    if intent in {"show_habits", "show_profile"}:
        return handler()

    if intent == "mark_habit":
        return handler(habit_name=str(parameters.get("habit_name", "")))

    # ── Messenger intents ────────────────────────────────────────────────────
    if intent == "send_message":
        return handler(
            platform=str(parameters.get("platform", "")),
            recipient=str(parameters.get("recipient") or parameters.get("contact", "")),
            message=str(parameters.get("message") or parameters.get("text", "")),
            ask_fn=payload.get("ask_fn"),
        )

    if intent == "read_messages":
        return handler(
            platform=str(parameters.get("platform", "")),
            limit=int(parameters.get("limit", 10)),
        )

    # ── Media control intents ────────────────────────────────────────────────
    if intent in {"media_play_pause", "media_next", "media_prev", "media_stop"}:
        return handler(app=str(parameters.get("app", "")))

    if intent == "media_volume":
        return handler(
            level=int(parameters.get("level", 50)),
            app=str(parameters.get("app", "")),
        )

    if intent == "media_search":
        return handler(
            query=str(parameters.get("query") or parameters.get("track", "")),
            app=str(parameters.get("app", "")),
        )

    # ── Notifier intents ─────────────────────────────────────────────────────
    if intent == "send_notification":
        return handler(
            title=str(parameters.get("title", "ARIA")),
            message=str(parameters.get("message", "")),
        )

    if intent == "notify_reminder":
        return handler(
            task=str(parameters.get("task", "")),
            time_str=str(parameters.get("time", "")),
        )

    # ── Generic fallback: pass parameters as kwargs ─────────────────────────
    return handler(**parameters)


# ─────────────────────────────────────────────────────────────────────────────
# Multi-step dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def dispatch_multi_step(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Execute an ordered list of steps and return per-step results.

    Each result dict contains:
      - step_number (int)
      - intent (str)
      - status: "success" | "error"
      - result (str)
    """
    results = []
    for i, step in enumerate(steps, 1):
        intent_name = _normalize_intent(str(step.get("intent", "")))
        try:
            outcome = dispatch_intent(step)
            results.append({
                "step_number": i,
                "intent": intent_name,
                "status": "success",
                "result": str(outcome) if outcome is not None else "Done.",
            })
        except Exception as exc:
            results.append({
                "step_number": i,
                "intent": intent_name,
                "status": "error",
                "result": str(exc),
            })
    return results
