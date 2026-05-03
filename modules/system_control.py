"""System control actions for Windows."""

from __future__ import annotations

from typing import Iterable
import os
import shutil
import subprocess
import time
import warnings

import psutil
import pyautogui
import pyperclip
from pywinauto import Application


APP_ALIASES = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "firefox": "firefox.exe",
    "mozilla firefox": "firefox.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calculater": "calc.exe",
    "settings": "ms-settings:",
    "setting": "ms-settings:",
    "clock": "ms-clock:",
    "alarms": "ms-clock:",

    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "control panel": "control.exe",

    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",

    # Common desktop apps
    "telegram": "Telegram.exe",
    "telegram desktop": "Telegram.exe",
    "tor": "start:Tor Browser",
    "tor browser": "start:Tor Browser",

    # UWP apps
    "feedback hub": "shell:AppsFolder\\Microsoft.WindowsFeedbackHub_8wekyb3d8bbwe!App",

    # Media Player naming varies by Windows version.
    # - "windows media player" launches the legacy desktop app.
    # - "media player" launches the UWP media app when available.
    "windows media player": "wmplayer.exe",
    "media player": "shell:AppsFolder\\Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic",
}


def _normalize_app_key(name: str) -> str:
    key = name.strip().lower().rstrip(" .,!?:;")
    key = key.replace("_", " ").replace("-", " ")
    key = " ".join(key.split())
    if key.endswith(" app"):
        key = key[: -len(" app")].strip()
    if key.endswith(" application"):
        key = key[: -len(" application")].strip()
    return key


def _looks_like_special_target(target: str) -> bool:
    t = target.strip().lower()
    return (
        t.startswith("start:")
        or t.startswith("ms-settings:")
        or t.startswith("ms-clock:")
        or t.startswith("shell:")
        or t.startswith("http://")
        or t.startswith("https://")
    )


def _resolve_launch_target(name: str) -> str:
    key = _normalize_app_key(name)
    target = APP_ALIASES.get(key, name.strip())
    if _looks_like_special_target(target):
        return target
    if not target.lower().endswith(".exe") and "\\" not in target and "/" not in target:
        target = f"{target}.exe"
    return target


def _display_app_name(name: str) -> str:
    """Return a human-friendly app name for responses."""
    normalized = _normalize_app_key(name)
    return normalized or name.strip()


def _iter_matching_processes(exe_name: str) -> Iterable[psutil.Process]:
    exe_lower = exe_name.lower()
    for proc in psutil.process_iter(["pid", "name"]):
        name = proc.info.get("name")
        if name and name.lower() == exe_lower:
            yield proc


def open_app(name: str) -> str:
    """Launch an application by its friendly name."""
    if not name:
        raise ValueError("App name is required")

    target = _resolve_launch_target(name)
    resolved = shutil.which(target) if not _looks_like_special_target(target) else None
    path = resolved or target

    # Detach console shells so they don't attach to this process' console.
    base_exe = os.path.basename(str(path)).lower() if isinstance(path, str) else ""
    if base_exe in {"cmd.exe", "powershell.exe"}:
        subprocess.Popen(["cmd", "/c", "start", "", base_exe])
        return f"Opened {name}"

    # URI/shell targets (Settings, Clock, UWP apps) are best launched via `start`.
    display = _display_app_name(name)

    if _looks_like_special_target(path):
        if path.strip().lower().startswith("start:"):
            start_name = path.split(":", 1)[1].strip()
            subprocess.Popen(["cmd", "/c", "start", "", start_name])
            return f"Opened {display}"
        subprocess.Popen(["cmd", "/c", "start", "", path])
        return f"Opened {display}"

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"Application is not loaded correctly \(WaitForInputIdle failed\)",
                category=RuntimeWarning,
            )
            Application(backend="uia").start(path)
    except Exception:
        try:
            subprocess.Popen(path)
        except FileNotFoundError:
            subprocess.Popen(["cmd", "/c", "start", "", name])

    return f"Opened {display}"


def close_window(name: str) -> str:
    """Close a running application by name."""
    if not name:
        raise ValueError("App name is required")

    display = _display_app_name(name)
    exe = _resolve_launch_target(name)
    if _looks_like_special_target(exe):
        return f"Close is not supported for {display}"
    procs = list(_iter_matching_processes(exe))
    if not procs:
        return f"No running process found for {display}"

    for proc in procs:
        try:
            app = Application(backend="uia").connect(process=proc.pid)
            app.top_window().close()
        except Exception:
            pass

    deadline = time.time() + 2
    for proc in procs:
        while time.time() < deadline and proc.is_running():
            time.sleep(0.1)

    for proc in procs:
        if proc.is_running():
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

    return f"Closed {display}"


def set_volume(level: int) -> str:
    """Set the system volume using key presses (0-100)."""
    volume = int(level)
    if volume < 0 or volume > 100:
        raise ValueError("Volume must be between 0 and 100")

    steps = 50
    pyautogui.press("volumedown", presses=steps, interval=0.01)
    up_presses = round(steps * (volume / 100))
    if up_presses:
        pyautogui.press("volumeup", presses=up_presses, interval=0.01)

    return f"Volume set to {volume}"


def get_clipboard() -> str:
    """Return current clipboard text."""
    return pyperclip.paste()


def type_text(text: str) -> str:
    """Type text using the keyboard automation."""
    if text is None:
        raise ValueError("Text is required")

    pyautogui.write(text, interval=0.01)
    return "Typed text"
