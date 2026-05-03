"""System control actions for Windows."""

from __future__ import annotations

from typing import Iterable
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

    # Media Player naming varies by Windows version.
    # - "windows media player" launches the legacy desktop app.
    # - "media player" launches the UWP media app when available.
    "windows media player": "wmplayer.exe",
    "media player": "shell:AppsFolder\\Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic",
}


def _normalize_app_key(name: str) -> str:
    key = name.strip().lower().rstrip(" .,!?:;")
    if key.endswith(" app"):
        key = key[: -len(" app")].strip()
    if key.endswith(" application"):
        key = key[: -len(" application")].strip()
    return key


def _looks_like_special_target(target: str) -> bool:
    t = target.strip().lower()
    return (
        t.startswith("ms-settings:")
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

    # URI/shell targets (Settings, Clock, UWP apps) are best launched via `start`.
    if _looks_like_special_target(path):
        subprocess.Popen(["cmd", "/c", "start", "", path])
        return f"Opened {name}"

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

    return f"Opened {name}"


def close_window(name: str) -> str:
    """Close a running application by name."""
    if not name:
        raise ValueError("App name is required")

    exe = _resolve_launch_target(name)
    if _looks_like_special_target(exe):
        return f"Close is not supported for {name}"
    procs = list(_iter_matching_processes(exe))
    if not procs:
        return f"No running process found for {name}"

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

    return f"Closed {name}"


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
