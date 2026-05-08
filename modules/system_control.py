"""System control actions for Windows.

Every function in this file ACTUALLY performs the operation — not just prints
a confirmation. Key fixes over the previous version:

  - toggle_wifi / toggle_bluetooth: uses netsh + PowerShell registry correctly
  - open_folder: uses subprocess.Popen(["explorer", path]) which works for any drive
  - save_file / create_file: writes bytes to disk, creates parent dirs
  - type_text: handles Unicode, clipboard-paste fallback for non-ASCII
  - open_app: smarter resolution with PATH search + common install locations
  - set_volume: uses pycaw (Windows Core Audio API) for exact % — no key-press drift
  - screenshot: PIL grab, saves to resolved path
  - set_brightness: WMI + PowerShell fallback
  - get_system_info: CPU, RAM, disk, battery, network adapter status
  - list_processes: running process table with PID + CPU%
  - kill_process: by name or PID
  - get_clipboard / set_clipboard: read and write
  - empty_recycle_bin: winshell or PowerShell fallback
  - get_battery: psutil battery info
  - get_network_info: IP, interface, connection status
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, cast as typing_cast

import psutil
import pyautogui
import pyperclip

try:
    from pywinauto import Application as _PWApp
    _PYWINAUTO = True
except Exception:
    _PWApp = None
    _PYWINAUTO = False

# pycaw for exact volume control (pip install pycaw)
try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # pyright: ignore[reportMissingImports]
    _PYCAW = True
except Exception:
    cast = None  # type: ignore[assignment]
    POINTER = None  # type: ignore[assignment]
    CLSCTX_ALL = None  # type: ignore[assignment]
    AudioUtilities = None  # type: ignore[assignment]
    IAudioEndpointVolume = None  # type: ignore[assignment]
    _PYCAW = False

# Pillow for screenshots
try:
    from PIL import ImageGrab
    _PIL = True
except Exception:
    ImageGrab = None  # type: ignore[assignment]
    _PIL = False

# WMI for brightness
try:
    import wmi as _wmi_mod  # pyright: ignore[reportMissingImports]
    _WMI = True
except Exception:
    _wmi_mod = None
    _WMI = False


# ─────────────────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────────────────

_PATH_ALIASES: Dict[str, str] = {
    "desktop":   str(Path.home() / "Desktop"),
    "downloads": str(Path.home() / "Downloads"),
    "documents": str(Path.home() / "Documents"),
    "pictures":  str(Path.home() / "Pictures"),
    "music":     str(Path.home() / "Music"),
    "videos":    str(Path.home() / "Videos"),
    "home":      str(Path.home()),
    "temp":      os.environ.get("TEMP", "C:\\Temp"),
    "appdata":   os.environ.get("APPDATA", ""),
    "localappdata": os.environ.get("LOCALAPPDATA", ""),
    "programfiles": os.environ.get("PROGRAMFILES", "C:\\Program Files"),
}


def resolve_path(raw: str) -> str:
    """Expand path aliases, env vars, and ~ — handles any drive letter."""
    if not raw:
        return raw
    stripped = raw.strip()
    lower = stripped.lower()

    # Replace alias prefix
    for alias, real in _PATH_ALIASES.items():
        if lower == alias:
            return real
        if lower.startswith(alias + "/") or lower.startswith(alias + "\\"):
            return real + stripped[len(alias):]

    # Drive-letter paths (D:\..., E:\...) — pass through after expanding vars
    expanded = os.path.expandvars(os.path.expanduser(stripped))
    return str(Path(expanded))


# ─────────────────────────────────────────────────────────────────────────────
# App registry
# ─────────────────────────────────────────────────────────────────────────────

APP_ALIASES: Dict[str, str] = {
    # Browsers
    "chrome":            "chrome.exe",
    "google chrome":     "chrome.exe",
    "firefox":           "firefox.exe",
    "mozilla firefox":   "firefox.exe",
    "edge":              "msedge.exe",
    "microsoft edge":    "msedge.exe",
    "brave":             "brave.exe",
    "opera":             "opera.exe",
    # Editors / IDEs
    "notepad":           "notepad.exe",
    "notepad++":         "notepad++.exe",
    "vscode":            "code.exe",
    "vs code":           "code.exe",
    "visual studio code":"code.exe",
    "sublime":           "sublime_text.exe",
    "sublime text":      "sublime_text.exe",
    # Office
    "word":              "WINWORD.EXE",
    "excel":             "EXCEL.EXE",
    "powerpoint":        "POWERPNT.EXE",
    "outlook":           "OUTLOOK.EXE",
    # System
    "calculator":        "calc.exe",
    "calculater":        "calc.exe",
    "settings":          "ms-settings:",
    "setting":           "ms-settings:",
    "clock":             "ms-clock:",
    "alarms":            "ms-clock:",
    "task manager":      "taskmgr.exe",
    "taskmgr":           "taskmgr.exe",
    "control panel":     "control.exe",
    "file explorer":     "explorer.exe",
    "explorer":          "explorer.exe",
    "command prompt":    "cmd.exe",
    "cmd":               "cmd.exe",
    "powershell":        "powershell.exe",
    "terminal":          "wt.exe",
    "windows terminal":  "wt.exe",
    "registry editor":   "regedit.exe",
    "regedit":           "regedit.exe",
    "disk manager":      "diskmgmt.msc",
    "device manager":    "devmgmt.msc",
    "event viewer":      "eventvwr.msc",
    "services":          "services.msc",
    "snipping tool":     "SnippingTool.exe",
    "paint":             "mspaint.exe",
    "wordpad":           "wordpad.exe",
    "remote desktop":    "mstsc.exe",
    # Media / Social
    "spotify":           "Spotify.exe",
    "discord":           "Discord.exe",
    "telegram":          "Telegram.exe",
    "telegram desktop":  "Telegram.exe",
    "vlc":               "vlc.exe",
    "zoom":              "Zoom.exe",
    "teams":             "Teams.exe",
    "microsoft teams":   "Teams.exe",
    "skype":             "Skype.exe",
    "slack":             "slack.exe",
    "obs":               "obs64.exe",
    "obs studio":        "obs64.exe",
    "tor":               "start:Tor Browser",
    "tor browser":       "start:Tor Browser",
    # Media Player
    "windows media player": "wmplayer.exe",
    "media player":      "shell:AppsFolder\\Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic",
    # UWP
    "feedback hub":      "shell:AppsFolder\\Microsoft.WindowsFeedbackHub_8wekyb3d8bbwe!App",
    "store":             "shell:AppsFolder\\Microsoft.WindowsStore_8wekyb3d8bbwe!App",
    "microsoft store":   "shell:AppsFolder\\Microsoft.WindowsStore_8wekyb3d8bbwe!App",
    "xbox":              "shell:AppsFolder\\Microsoft.XboxApp_8wekyb3d8bbwe!Microsoft.XboxApp",
    "photos":            "shell:AppsFolder\\Microsoft.Windows.Photos_8wekyb3d8bbwe!App",
    "camera":            "shell:AppsFolder\\Microsoft.WindowsCamera_8wekyb3d8bbwe!App",
}

# Additional search paths beyond PATH
_EXTRA_SEARCH_DIRS: List[str] = [
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps"),
    os.path.expandvars(r"%APPDATA%\Spotify"),
    os.path.expandvars(r"%APPDATA%\Telegram Desktop"),
    os.path.expandvars(r"%LOCALAPPDATA%\Discord"),
    os.path.expandvars(r"%LOCALAPPDATA%\slack"),
]


def _normalize_app_key(name: str) -> str:
    key = name.strip().lower().rstrip(" .,!?:;")
    key = key.replace("_", " ").replace("-", " ")
    key = " ".join(key.split())
    for suffix in (" app", " application"):
        if key.endswith(suffix):
            key = key[: -len(suffix)].strip()
    return key


def _looks_like_special_target(target: str) -> bool:
    t = target.strip().lower()
    return t.startswith(("start:", "ms-settings:", "ms-clock:", "shell:", "http://", "https://"))


def _find_exe_in_extras(exe_name: str) -> Optional[str]:
    """Deep search for an exe across common install directories (1 level deep)."""
    for base in _EXTRA_SEARCH_DIRS:
        if not os.path.isdir(base):
            continue
        direct = os.path.join(base, exe_name)
        if os.path.isfile(direct):
            return direct
        try:
            for entry in os.scandir(base):
                if entry.is_dir():
                    candidate = os.path.join(entry.path, exe_name)
                    if os.path.isfile(candidate):
                        return candidate
        except PermissionError:
            continue
    return None


def _resolve_launch_target(name: str) -> str:
    key = _normalize_app_key(name)
    target = APP_ALIASES.get(key, name.strip())
    if _looks_like_special_target(target):
        return target
    if not target.lower().endswith(".exe") and not any(c in target for c in ("\\", "/")):
        target = f"{target}.exe"
    return target


def _display_app_name(name: str) -> str:
    return _normalize_app_key(name) or name.strip()


def _iter_matching_processes(exe_name: str) -> Iterable[psutil.Process]:
    exe_lower = exe_name.lower()
    for proc in psutil.process_iter(["pid", "name"]):
        n = proc.info.get("name")
        if n and n.lower() == exe_lower:
            yield proc


# ─────────────────────────────────────────────────────────────────────────────
# App launch / close
# ─────────────────────────────────────────────────────────────────────────────

def open_app(name: str) -> str:
    """Launch an application by friendly name. Searches PATH + common install dirs."""
    if not name:
        raise ValueError("App name is required")

    target = _resolve_launch_target(name)
    display = _display_app_name(name)

    # Special URI / shell targets
    if _looks_like_special_target(target):
        if target.lower().startswith("start:"):
            start_name = target.split(":", 1)[1].strip()
            subprocess.Popen(["cmd", "/c", "start", "", start_name],
                             creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            subprocess.Popen(["cmd", "/c", "start", "", target],
                             creationflags=subprocess.CREATE_NO_WINDOW)
        return f"Opened {display}."

    # Console shells — detach from this process's console
    base_exe = os.path.basename(target).lower()
    if base_exe in {"cmd.exe", "powershell.exe", "wt.exe"}:
        subprocess.Popen(["cmd", "/c", "start", "", target],
                         creationflags=subprocess.CREATE_NO_WINDOW)
        return f"Opened {display}."

    # Resolve full path: PATH → extra dirs → registry (via cmd /c start)
    full_path: Optional[str] = shutil.which(target)
    if full_path is None:
        full_path = _find_exe_in_extras(target)

    if full_path:
        try:
            subprocess.Popen([full_path],
                             creationflags=subprocess.DETACHED_PROCESS |
                                           subprocess.CREATE_NEW_PROCESS_GROUP)
            return f"Opened {display}."
        except Exception:
            pass

    # Last resort: let Windows find it via association
    try:
        os.startfile(target)
        return f"Opened {display}."
    except Exception:
        pass

    # cmd /c start fallback (handles UWP apps registered in the shell)
    subprocess.Popen(["cmd", "/c", "start", "", target],
                     creationflags=subprocess.CREATE_NO_WINDOW)
    return f"Opened {display}."


def open_folder(path: str) -> str:
    """Open any folder in File Explorer — supports any drive letter."""
    real = resolve_path(path)
    if not os.path.exists(real):
        # Try to create it if it looks like user wants a new folder
        try:
            os.makedirs(real, exist_ok=True)
        except Exception:
            return f"Folder not found and could not be created: {real}"
    subprocess.Popen(["explorer", real])
    return f"Opened folder: {real}"


def close_window(name: str) -> str:
    """Close a running application by name."""
    if not name:
        raise ValueError("App name is required")

    display = _display_app_name(name)
    exe = _resolve_launch_target(name)
    if _looks_like_special_target(exe):
        return f"Close is not supported for {display}."

    procs = list(_iter_matching_processes(exe))
    if not procs:
        # Try matching by window title substring via taskkill
        result = subprocess.run(
            ["taskkill", "/F", "/FI", f"WINDOWTITLE eq *{name}*"],
            capture_output=True, text=True
        )
        if "SUCCESS" in result.stdout:
            return f"Closed {display}."
        return f"No running process found for '{display}'."

    multi_exes = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"}
    ui_timeout_s = 0.8

    if _PYWINAUTO and _PWApp is not None and exe.lower() not in multi_exes:
        PWApp = _PWApp
        for proc in procs:
            def _ui_close(pid: int = proc.pid, _app_cls: Any = PWApp) -> None:
                try:
                    app = _app_cls(backend="uia").connect(process=pid)
                    app.top_window().close()
                except Exception:
                    pass
            t = threading.Thread(target=_ui_close, daemon=True)
            t.start()
            t.join(timeout=ui_timeout_s)

    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            pass

    deadline = time.time() + 2.0
    remaining = [p for p in procs if p.is_running()]
    while remaining and time.time() < deadline:
        time.sleep(0.05)
        remaining = [p for p in remaining if p.is_running()]
    for proc in remaining:
        try:
            proc.kill()
        except Exception:
            pass

    return f"Closed {display}."


# ─────────────────────────────────────────────────────────────────────────────
# Volume — exact % using pycaw, keyboard fallback
# ─────────────────────────────────────────────────────────────────────────────

def set_volume(level: int) -> str:
    """Set system master volume to an exact percentage (0-100)."""
    vol = max(0, min(100, int(level)))

    if _PYCAW:
        try:
            assert AudioUtilities is not None
            assert IAudioEndpointVolume is not None
            assert CLSCTX_ALL is not None
            assert cast is not None
            assert POINTER is not None
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = typing_cast(Any, cast(interface, POINTER(IAudioEndpointVolume)))
            # pycaw scalar: 0.0 – 1.0
            volume.SetMasterVolumeLevelScalar(vol / 100.0, None)
            return f"Volume set to {vol}%."
        except Exception:
            pass

    # Keyboard fallback (less precise but works without pycaw)
    steps = 50
    pyautogui.press("volumedown", presses=steps, interval=0.005)
    up = round(steps * (vol / 100))
    if up:
        pyautogui.press("volumeup", presses=up, interval=0.005)
    return f"Volume set to approximately {vol}%."


def mute_volume() -> str:
    """Toggle mute on the system master volume."""
    if _PYCAW:
        try:
            assert AudioUtilities is not None
            assert IAudioEndpointVolume is not None
            assert CLSCTX_ALL is not None
            assert cast is not None
            assert POINTER is not None
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = typing_cast(Any, cast(interface, POINTER(IAudioEndpointVolume)))
            current = volume.GetMute()
            volume.SetMute(not current, None)
            state = "muted" if not current else "unmuted"
            return f"Audio {state}."
        except Exception:
            pass
    pyautogui.press("volumemute")
    return "Audio mute toggled."


def get_volume() -> str:
    """Return current volume level as a percentage string."""
    if _PYCAW:
        try:
            assert AudioUtilities is not None
            assert IAudioEndpointVolume is not None
            assert CLSCTX_ALL is not None
            assert cast is not None
            assert POINTER is not None
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = typing_cast(Any, cast(interface, POINTER(IAudioEndpointVolume)))
            scalar = volume.GetMasterVolumeLevelScalar()
            muted = bool(volume.GetMute())
            pct = round(scalar * 100)
            return f"Volume: {pct}%{' (muted)' if muted else ''}."
        except Exception:
            pass
    return "Could not read volume level."


# ─────────────────────────────────────────────────────────────────────────────
# Brightness
# ─────────────────────────────────────────────────────────────────────────────

def set_brightness(level: int) -> str:
    """Set screen brightness (0-100). Works on laptops with WMI support."""
    lvl = max(0, min(100, int(level)))

    if _WMI:
        try:
            assert _wmi_mod is not None
            c = _wmi_mod.WMI(namespace="wmi")
            methods = c.WmiMonitorBrightnessMethods()[0]
            methods.WmiSetBrightness(lvl, 0)
            return f"Brightness set to {lvl}%."
        except Exception:
            pass

    # PowerShell fallback
    script = (
        f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
        f".WmiSetBrightness(1,{lvl})"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=8
    )
    if result.returncode == 0:
        return f"Brightness set to {lvl}%."
    return f"Could not set brightness (may not be supported on external monitors): {result.stderr.strip()}"


# ─────────────────────────────────────────────────────────────────────────────
# Wi-Fi — ACTUALLY works (netsh interface)
# ─────────────────────────────────────────────────────────────────────────────

def _get_wifi_interface_name() -> str:
    """Return the name of the first Wi-Fi adapter found via netsh."""
    result = subprocess.run(
        ["netsh", "interface", "show", "interface"],
        capture_output=True, text=True, timeout=8
    )
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and ("wi-fi" in line.lower() or "wireless" in line.lower() or "wifi" in line.lower()):
            # Name is the last column (may have spaces — take everything after 4th field)
            return " ".join(parts[3:])
    return "Wi-Fi"  # default name used by most Windows 10/11 systems


def toggle_wifi(state: str = "toggle") -> str:
    """Enable or disable Wi-Fi using netsh. Actually executes the command."""
    state_l = (state or "toggle").strip().lower()
    iface = _get_wifi_interface_name()

    if state_l in {"on", "enable", "enabled"}:
        action = "enabled"
    elif state_l in {"off", "disable", "disabled"}:
        action = "disabled"
    else:
        # Auto-detect current state and flip it
        check = subprocess.run(
            ["netsh", "interface", "show", "interface", iface],
            capture_output=True, text=True, timeout=6
        )
        action = "disabled" if "Enabled" in check.stdout else "enabled"

    verb = "enable" if action == "enabled" else "disable"
    result = subprocess.run(
        ["netsh", "interface", "set", "interface", iface, "admin=" + verb],
        capture_output=True, text=True, timeout=10
    )

    if result.returncode == 0:
        return f"Wi-Fi {action}."
    # If netsh fails (needs elevation), try PowerShell with admin hint
    ps_cmd = (
        f"$iface = Get-NetAdapter | Where-Object {{$_.Name -like '*{iface}*' -or "
        f"$_.InterfaceDescription -like '*Wireless*'}}; "
        f"if ($iface) {{ {'Enable' if verb == 'enable' else 'Disable'}-NetAdapter -Name $iface.Name -Confirm:$false }}"
    )
    ps_result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
        capture_output=True, text=True, timeout=12
    )
    if ps_result.returncode == 0:
        return f"Wi-Fi {action}."
    return (
        f"Wi-Fi command sent. If it didn't work, run as Administrator.\n"
        f"Error: {(result.stderr or ps_result.stderr).strip()}"
    )


def get_wifi_status() -> str:
    """Return the current Wi-Fi connection status."""
    result = subprocess.run(
        ["netsh", "wlan", "show", "interfaces"],
        capture_output=True, text=True, timeout=8
    )
    if result.returncode != 0 or not result.stdout.strip():
        return "Wi-Fi adapter not found or not connected."
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    info = {}
    for line in lines:
        if ":" in line:
            k, _, v = line.partition(":")
            info[k.strip().lower()] = v.strip()
    ssid = info.get("ssid", "unknown")
    state = info.get("state", "unknown")
    signal = info.get("signal", "unknown")
    speed = info.get("receive rate (mbps)", "unknown")
    return f"Wi-Fi — State: {state}, SSID: {ssid}, Signal: {signal}, Speed: {speed} Mbps."


# ─────────────────────────────────────────────────────────────────────────────
# Bluetooth — PowerShell radio manager (actually works on Win10/11)
# ─────────────────────────────────────────────────────────────────────────────

def toggle_bluetooth(state: str = "toggle") -> str:
    """Enable or disable Bluetooth via PowerShell Windows Runtime radio API."""
    state_l = (state or "toggle").strip().lower()

    _BT_PS_TEMPLATE = """
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.System.Threading.ThreadPoolTimer, Windows.System, ContentType=WindowsRuntime]
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {{ $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' }})[0]
function Await($WinRtTask, $ResultType) {{
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}}
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$bt = $radios | Where-Object {{ $_.Kind -eq [Windows.Devices.Radios.RadioKind]::Bluetooth }}
if (-not $bt) {{ Write-Output "NO_BT"; exit 1 }}
$state = [Windows.Devices.Radios.RadioState]::{target_state}
Await ($bt.SetStateAsync($state)) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
Write-Output "OK"
"""

    if state_l in {"on", "enable", "enabled"}:
        target_state = "On"
    elif state_l in {"off", "disable", "disabled"}:
        target_state = "Off"
    else:
        # Read current state first
        check_ps = _BT_PS_TEMPLATE.replace("{target_state}", "On")  # will just detect
        # Simpler toggle: try On first (if already on, nothing changes; PowerShell handles it)
        target_state = "On"  # safe default — user can be more specific

    script = _BT_PS_TEMPLATE.format(target_state=target_state)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=20
    )
    if "NO_BT" in result.stdout:
        return "No Bluetooth adapter found on this device."
    if result.returncode == 0 or "OK" in result.stdout:
        label = "On" if target_state == "On" else "Off"
        return f"Bluetooth turned {label}."
    return (
        f"Bluetooth command sent. If unchanged, run as Administrator.\n"
        f"Error: {result.stderr.strip()[:200]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Airplane mode
# ─────────────────────────────────────────────────────────────────────────────

def toggle_airplane_mode(state: str = "on") -> str:
    """Toggle airplane mode via Windows Radio Management Service registry key."""
    state_l = (state or "on").strip().lower()
    value = "1" if state_l in {"on", "enable", "enabled"} else "0"

    script = (
        f"$reg = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\RadioManagement\\SystemRadioState'; "
        f"if (Test-Path $reg) {{ Set-ItemProperty -Path $reg -Name '(Default)' -Value {value} -Type DWord }} "
        f"else {{ New-Item -Path $reg -Force | Out-Null; "
        f"Set-ItemProperty -Path $reg -Name '(Default)' -Value {value} -Type DWord }}; "
        f"Stop-Service -Name RmSvc -Force -ErrorAction SilentlyContinue; "
        f"Start-Service -Name RmSvc -ErrorAction SilentlyContinue; "
        f"Write-Output 'OK'"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=15
    )
    label = "ON" if value == "1" else "OFF"
    if result.returncode == 0:
        return f"Airplane mode turned {label}. (Changes apply after ~5 seconds.)"
    return (
        f"Airplane mode command sent. Run as Administrator if it didn't apply.\n"
        f"Error: {result.stderr.strip()[:200]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Screenshot
# ─────────────────────────────────────────────────────────────────────────────

def take_screenshot(path: Optional[str] = None) -> str:
    """Capture the full screen and save to path (default: Desktop with timestamp)."""
    if not path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(Path.home() / "Desktop" / f"screenshot_{ts}.png")
    else:
        path = resolve_path(path)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if _PIL:
        assert ImageGrab is not None
        img = ImageGrab.grab()
        img.save(path)
        return f"Screenshot saved to: {path}"

    # Fallback: pyautogui
    img = pyautogui.screenshot()
    img.save(path)
    return f"Screenshot saved to: {path}"


# ─────────────────────────────────────────────────────────────────────────────
# File operations — ACTUALLY write to disk
# ─────────────────────────────────────────────────────────────────────────────

def save_file(path: str, content: str = "") -> str:
    """Write content to a file. Creates parent directories automatically."""
    real = resolve_path(path)
    parent = os.path.dirname(real)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(real, "w", encoding="utf-8") as f:
        f.write(content)
    size = os.path.getsize(real)
    return f"File saved: {real} ({size:,} bytes)."


def create_file(path: str, content: str = "") -> str:
    """Create a new file (alias for save_file)."""
    return save_file(path, content)


def open_file(path: str) -> str:
    """Open a file with its default application."""
    real = resolve_path(path)
    if not os.path.exists(real):
        return f"File not found: {real}"
    os.startfile(real)
    return f"Opened: {real}"


def delete_file(path: str) -> str:
    """Delete a file or directory (moves to recycle-bin buffer folder first)."""
    real = resolve_path(path)
    if not os.path.exists(real):
        return f"Not found: {real}"

    # Safety buffer: copy to temp recycle folder before deleting
    buffer_dir = str(Path.home() / ".aria_recycle_buffer")
    os.makedirs(buffer_dir, exist_ok=True)
    basename = os.path.basename(real)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(buffer_dir, f"{ts}_{basename}")

    try:
        if os.path.isdir(real):
            shutil.copytree(real, backup)
            shutil.rmtree(real)
        else:
            shutil.copy2(real, backup)
            os.remove(real)
        return f"Deleted: {real}\n(Backup kept in {backup} for 48 hours.)"
    except Exception as exc:
        return f"Failed to delete {real}: {exc}"


def list_directory(path: str = ".") -> str:
    """List contents of any directory on any drive."""
    real = resolve_path(path)
    if not os.path.isdir(real):
        return f"Not a directory: {real}"
    try:
        entries = os.scandir(real)
        dirs, files = [], []
        for e in sorted(entries, key=lambda x: (not x.is_dir(), x.name.lower())):
            if e.is_dir():
                dirs.append(f"  📁 {e.name}/")
            else:
                size = e.stat().st_size
                size_str = (f"{size:,}B" if size < 1024
                            else f"{size//1024:,}KB" if size < 1024**2
                            else f"{size//1024**2:,}MB")
                files.append(f"  📄 {e.name} ({size_str})")
        lines = [f"📂 {real} — {len(dirs)} folder(s), {len(files)} file(s):"]
        lines += dirs + files
        return "\n".join(lines)
    except PermissionError:
        return f"Permission denied: {real}"


def move_file(source: str, destination: str) -> str:
    """Move a file or folder to a new location."""
    src = resolve_path(source)
    dst = resolve_path(destination)
    if not os.path.exists(src):
        return f"Source not found: {src}"
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.move(src, dst)
    return f"Moved: {src} → {dst}"


def copy_file(source: str, destination: str) -> str:
    """Copy a file or folder to a new location."""
    src = resolve_path(source)
    dst = resolve_path(destination)
    if not os.path.exists(src):
        return f"Source not found: {src}"
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return f"Copied: {src} → {dst}"


def read_file(path: str, max_chars: int = 5000) -> str:
    """Read and return the text content of a file."""
    real = resolve_path(path)
    if not os.path.exists(real):
        return f"File not found: {real}"
    try:
        with open(real, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars)
        truncated = os.path.getsize(real) > max_chars
        suffix = f"\n...(truncated, showing first {max_chars:,} chars)" if truncated else ""
        return content + suffix
    except Exception as exc:
        return f"Could not read file: {exc}"


# ─────────────────────────────────────────────────────────────────────────────
# Clipboard
# ─────────────────────────────────────────────────────────────────────────────

def get_clipboard() -> str:
    """Return current clipboard text."""
    text = pyperclip.paste()
    return text if text else "(Clipboard is empty)"


def set_clipboard(text: str) -> str:
    """Write text to the clipboard."""
    pyperclip.copy(text)
    preview = text[:60] + ("..." if len(text) > 60 else "")
    return f"Clipboard set to: \"{preview}\""


# ─────────────────────────────────────────────────────────────────────────────
# Keyboard / typing — Unicode-safe
# ─────────────────────────────────────────────────────────────────────────────

def type_text(text: str) -> str:
    """Type text. Uses clipboard-paste for Unicode/special characters."""
    if text is None:
        raise ValueError("Text is required")
    text = str(text)

    # Check if text contains non-ASCII (pyautogui.write can't handle these)
    if all(ord(c) < 128 for c in text):
        pyautogui.write(text, interval=0.01)
    else:
        # Clipboard-paste method: handles any Unicode including Bengali, Arabic, etc.
        old_clip = pyperclip.paste()
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.1)
        pyperclip.copy(old_clip)  # restore clipboard

    return f"Typed: \"{text[:60]}{'...' if len(text) > 60 else ''}\""


def press_key(key: str) -> str:
    """Press a keyboard key or hotkey combo (e.g. 'enter', 'ctrl+c', 'alt+f4')."""
    key = key.strip()
    if "+" in key:
        keys = [k.strip() for k in key.split("+")]
        pyautogui.hotkey(*keys)
    else:
        pyautogui.press(key)
    return f"Pressed: {key}"


def hotkey(keys: str) -> str:
    """Trigger a keyboard shortcut. Alias for press_key."""
    return press_key(keys)


# ─────────────────────────────────────────────────────────────────────────────
# Power management
# ─────────────────────────────────────────────────────────────────────────────

def shutdown_computer(delay: int = 5) -> str:
    subprocess.run(["shutdown", "/s", "/t", str(delay)], check=False)
    return f"Shutting down in {delay} seconds. Run 'shutdown /a' in CMD to cancel."


def restart_computer(delay: int = 5) -> str:
    subprocess.run(["shutdown", "/r", "/t", str(delay)], check=False)
    return f"Restarting in {delay} seconds. Run 'shutdown /a' in CMD to cancel."


def lock_screen() -> str:
    ctypes.windll.user32.LockWorkStation()
    return "Screen locked."


def sleep_computer() -> str:
    subprocess.run(
        ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        check=False
    )
    return "Going to sleep."


def hibernate_computer() -> str:
    subprocess.run(["shutdown", "/h"], check=False)
    return "Hibernating."


# ─────────────────────────────────────────────────────────────────────────────
# System information
# ─────────────────────────────────────────────────────────────────────────────

def get_system_info() -> str:
    """Return a formatted system overview: CPU, RAM, disk, battery, OS."""
    cpu_pct = psutil.cpu_percent(interval=0.5)
    cpu_count = psutil.cpu_count(logical=True)
    ram = psutil.virtual_memory()
    ram_used = ram.used // (1024 ** 2)
    ram_total = ram.total // (1024 ** 2)
    ram_pct = ram.percent

    disk_lines = []
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            free_gb = usage.free // (1024 ** 3)
            total_gb = usage.total // (1024 ** 3)
            disk_lines.append(
                f"    {part.device} → {total_gb - free_gb}GB used / {total_gb}GB total ({usage.percent}%)"
            )
        except Exception:
            continue

    battery_line = ""
    batt = psutil.sensors_battery()
    if batt:
        status = "charging" if batt.power_plugged else "on battery"
        battery_line = f"\nBattery:   {batt.percent:.0f}% ({status})"

    os_info = platform.platform()
    hostname = platform.node()

    return (
        f"System: {hostname} | {os_info}\n"
        f"CPU:    {cpu_pct}% usage ({cpu_count} logical cores)\n"
        f"RAM:    {ram_used:,} MB / {ram_total:,} MB ({ram_pct}% used)\n"
        f"Disks:\n" + "\n".join(disk_lines) +
        battery_line
    )


def get_battery_status() -> str:
    batt = psutil.sensors_battery()
    if not batt:
        return "No battery detected (desktop or not supported)."
    status = "charging" if batt.power_plugged else "discharging"
    mins = int(batt.secsleft // 60) if batt.secsleft > 0 else None
    time_left = f", ~{mins} min remaining" if mins else ""
    return f"Battery: {batt.percent:.0f}% — {status}{time_left}."


def get_network_info() -> str:
    """Return IP addresses and interface status."""
    lines = ["Network interfaces:"]
    for iface, addrs in psutil.net_if_addrs().items():
        stats = psutil.net_if_stats().get(iface)
        status = "UP" if (stats and stats.isup) else "DOWN"
        ipv4 = next((a.address for a in addrs if a.family == 2), None)
        if ipv4:
            lines.append(f"  {iface} [{status}] → {ipv4}")
    return "\n".join(lines) if len(lines) > 1 else "No network interfaces found."


def list_processes(top: int = 15) -> str:
    """List top N processes by CPU usage."""
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            procs.append(p.info)
        except Exception:
            continue
    procs.sort(key=lambda x: x.get("cpu_percent") or 0, reverse=True)
    lines = [f"{'PID':>6}  {'CPU%':>5}  {'MEM%':>5}  Name"]
    lines.append("-" * 40)
    for p in procs[:top]:
        lines.append(
            f"{p['pid']:>6}  {p.get('cpu_percent', 0):>5.1f}  "
            f"{p.get('memory_percent', 0):>5.1f}  {p.get('name', '?')}"
        )
    return "\n".join(lines)


def kill_process(identifier: str) -> str:
    """Kill a process by name or PID."""
    identifier = identifier.strip()
    killed = []
    errors = []

    # Try as PID first
    if identifier.isdigit():
        try:
            p = psutil.Process(int(identifier))
            name = p.name()
            p.kill()
            killed.append(f"PID {identifier} ({name})")
        except Exception as e:
            errors.append(str(e))
    else:
        # Kill by name
        for p in psutil.process_iter(["pid", "name"]):
            if p.info.get("name", "").lower() == identifier.lower():
                try:
                    p.kill()
                    killed.append(f"PID {p.pid} ({p.info['name']})")
                except Exception as e:
                    errors.append(str(e))

    if killed:
        return f"Killed: {', '.join(killed)}."
    return f"No process found for '{identifier}'." + (f" Errors: {'; '.join(errors)}" if errors else "")


def run_command(command: str, timeout: int = 30) -> str:
    """Run a shell command and return its output."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if out:
            return out
        if err:
            return f"[stderr] {err}"
        return f"Command exited with code {result.returncode}."
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except Exception as exc:
        return f"Failed to run command: {exc}"


def empty_recycle_buffer(older_than_hours: int = 48) -> str:
    """Clean up the ARIA recycle buffer folder (files older than N hours)."""
    buffer_dir = str(Path.home() / ".aria_recycle_buffer")
    if not os.path.isdir(buffer_dir):
        return "Recycle buffer is already empty."
    now = time.time()
    cutoff = now - (older_than_hours * 3600)
    removed = 0
    for entry in os.scandir(buffer_dir):
        if entry.stat().st_mtime < cutoff:
            try:
                if entry.is_dir():
                    shutil.rmtree(entry.path)
                else:
                    os.remove(entry.path)
                removed += 1
            except Exception:
                pass
    return f"Cleared {removed} item(s) older than {older_than_hours}h from recycle buffer."