"""ARIA — Media Control Module.

Controls Spotify, VLC, YouTube Music. Play, pause, skip, volume, search.
Uses pyautogui for desktop apps and Playwright for web players.
"""

from __future__ import annotations
import os, re, subprocess, time
from typing import Any, Optional

def _find_process(name: str) -> bool:
    """Check if a process is running (Windows)."""
    try:
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}"],
            capture_output=True, text=True, timeout=5)
        return name.lower() in r.stdout.lower()
    except Exception:
        return False

def _send_media_key(key: str) -> str:
    """Send a media key using pyautogui."""
    try:
        import pyautogui
        key_map = {
            "play_pause": "playpause", "next": "nexttrack",
            "prev": "prevtrack", "stop": "stop",
            "volume_up": "volumeup", "volume_down": "volumedown",
            "mute": "volumemute",
        }
        pyautogui.press(key_map.get(key, key))
        return f"Sent media key: {key}"
    except Exception as exc:
        return f"Failed to send media key: {exc}"

# ── Spotify ──────────────────────────────────────────────────────────────

def spotify_play_pause(**_: Any) -> str:
    """Toggle play/pause on Spotify."""
    if _find_process("Spotify.exe"):
        return _send_media_key("play_pause")
    return "Spotify is not running. Open it first."

def spotify_next(**_: Any) -> str:
    """Skip to next track on Spotify."""
    if _find_process("Spotify.exe"):
        return _send_media_key("next")
    return "Spotify is not running."

def spotify_prev(**_: Any) -> str:
    """Go to previous track on Spotify."""
    if _find_process("Spotify.exe"):
        return _send_media_key("prev")
    return "Spotify is not running."

def spotify_search(query: str = "", **_: Any) -> str:
    """Search for a track on Spotify."""
    if not query.strip():
        return "What should I search for on Spotify?"
    try:
        import pyautogui
        # Focus Spotify and open search
        subprocess.Popen(["cmd", "/c", "start", "spotify:"], shell=True)
        time.sleep(2)
        pyautogui.hotkey("ctrl", "l")  # Focus search bar
        time.sleep(0.5)
        pyautogui.typewrite(query, interval=0.03)
        time.sleep(0.5)
        pyautogui.press("enter")
        return f"Searching Spotify for: {query}"
    except Exception as exc:
        return f"Failed to search Spotify: {exc}"

# ── VLC ──────────────────────────────────────────────────────────────────

def vlc_play_pause(**_: Any) -> str:
    if _find_process("vlc.exe"):
        return _send_media_key("play_pause")
    return "VLC is not running."

def vlc_next(**_: Any) -> str:
    if _find_process("vlc.exe"):
        try:
            import pyautogui
            pyautogui.press("n")  # VLC next shortcut
            return "VLC: next track."
        except Exception as exc:
            return f"Failed: {exc}"
    return "VLC is not running."

def vlc_prev(**_: Any) -> str:
    if _find_process("vlc.exe"):
        try:
            import pyautogui
            pyautogui.press("p")  # VLC prev shortcut
            return "VLC: previous track."
        except Exception as exc:
            return f"Failed: {exc}"
    return "VLC is not running."

def vlc_volume(level: int = 50, **_: Any) -> str:
    """Set VLC volume (0-200, VLC allows >100)."""
    if not _find_process("vlc.exe"):
        return "VLC is not running."
    try:
        import pyautogui
        level = max(0, min(200, int(level)))
        # VLC CLI control
        subprocess.run(["vlc", "--intf", "rc", f"--volume={level * 2.56:.0f}"],
            timeout=3, capture_output=True)
        return f"VLC volume set to {level}%."
    except Exception:
        return _send_media_key("volume_up" if level > 50 else "volume_down")

# ── YouTube Music (web) ──────────────────────────────────────────────────

def youtube_music_play(query: str = "", **_: Any) -> str:
    """Play a song on YouTube Music in the browser."""
    if not query.strip():
        return _send_media_key("play_pause")
    try:
        import webbrowser
        q = query.strip().replace(" ", "+")
        url = f"https://music.youtube.com/search?q={q}"
        webbrowser.open(url)
        return f"Opening YouTube Music search for: {query}"
    except Exception as exc:
        return f"Failed: {exc}"

# ── Generic media controls ───────────────────────────────────────────────

def media_play_pause(app: str = "", **_: Any) -> str:
    """Play/pause any media player."""
    app = (app or "").strip().lower()
    if "spotify" in app:
        return spotify_play_pause()
    if "vlc" in app:
        return vlc_play_pause()
    # Generic media key for whatever is playing
    return _send_media_key("play_pause")

def media_next(app: str = "", **_: Any) -> str:
    app = (app or "").strip().lower()
    if "spotify" in app:
        return spotify_next()
    if "vlc" in app:
        return vlc_next()
    return _send_media_key("next")

def media_prev(app: str = "", **_: Any) -> str:
    app = (app or "").strip().lower()
    if "spotify" in app:
        return spotify_prev()
    if "vlc" in app:
        return vlc_prev()
    return _send_media_key("prev")

def media_volume(level: int = 50, app: str = "", **_: Any) -> str:
    """Set media volume."""
    app = (app or "").strip().lower()
    if "vlc" in app:
        return vlc_volume(level)
    # Fall back to system volume
    try:
        from modules.system_control import set_volume
        return set_volume(level)
    except Exception:
        return _send_media_key("volume_up" if level > 50 else "volume_down")

def media_search(query: str = "", app: str = "", **_: Any) -> str:
    """Search for a track on a media player."""
    app = (app or "").strip().lower()
    if "spotify" in app:
        return spotify_search(query)
    if "youtube" in app or "yt" in app:
        return youtube_music_play(query)
    # Default to YouTube Music
    if query.strip():
        return youtube_music_play(query)
    return "What should I search for? And on which player (Spotify, YouTube Music, VLC)?"

def media_stop(**_: Any) -> str:
    return _send_media_key("stop")

def media_mute(**_: Any) -> str:
    return _send_media_key("mute")
