"""Cognitive load monitor — keyboard timing analysis."""
from __future__ import annotations

import json, os, threading, time
from collections import deque
from datetime import datetime

from config.settings import get as get_setting

try:
    from pynput import keyboard
    _PYNPUT = True
except Exception:
    _PYNPUT = False

STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "memory", "cognitive_state.json"
)

_timestamps: deque = deque(maxlen=100)
_backspaces: deque = deque(maxlen=100)
_pauses: deque = deque(maxlen=20)
_last_key_time: float = 0.0
_lock = threading.Lock()
_stop_event = threading.Event()


def _on_press(key) -> None:
    global _last_key_time
    now = time.time()
    with _lock:
        is_backspace = (key == keyboard.Key.backspace) if _PYNPUT else False
        if _last_key_time > 0:
            iki = now - _last_key_time           # inter-key interval
            _timestamps.append(iki)
            _backspaces.append(1 if is_backspace else 0)
            if iki > 3.0:                        # pause > 3s before typing
                _pauses.append(iki)
        _last_key_time = now


def _compute_load() -> int:
    with _lock:
        if len(_timestamps) < 10:
            return 20  # not enough data

        avg_iki = sum(_timestamps) / len(_timestamps)
        backspace_rate = sum(_backspaces) / len(_backspaces)
        avg_pause = sum(_pauses) / len(_pauses) if _pauses else 0

        # Normalise each dimension (values tuned from research)
        # Slow typing (avg IKI > 400ms) = tired
        iki_score = min(100, max(0, (avg_iki - 0.15) / 0.45 * 100))
        # High backspace rate (>15%) = struggling
        bksp_score = min(100, backspace_rate / 0.15 * 100)
        # Long pauses before typing = mental search
        pause_score = min(100, max(0, (avg_pause - 2) / 8 * 100))

        load = int(iki_score * 0.45 + bksp_score * 0.35 + pause_score * 0.20)
        return min(100, max(0, load))


def _write_state(load: int) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    label = ("low" if load < 30 else "moderate" if load < 60
             else "high" if load < 80 else "critical")
    state = {
        "cognitive_load": load,
        "label": label,
        "last_updated": datetime.now().isoformat(),
        "recommendation": {
            "low":      "User is sharp — present detailed responses and ambitious tasks.",
            "moderate": "Normal operation.",
            "high":     "Keep responses concise. Pause low-priority notifications.",
            "critical": "Strongly suggest a break. Simplify everything.",
        }[label]
    }
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def get_cognitive_state() -> dict:
    """Return the current cognitive state dict."""
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"cognitive_load": 20, "label": "low", "recommendation": "Normal operation."}


def _monitor_loop() -> None:
    while not _stop_event.is_set():
        interval = get_setting("cognitive_monitor.update_interval_seconds", 300)
        # Sleep in small chunks for responsiveness
        for _ in range(int(interval)):
            if _stop_event.is_set():
                return
            time.sleep(1)

        if not get_setting("cognitive_monitor.enabled", True):
            continue

        load = _compute_load()
        _write_state(load)

        alert_threshold = get_setting("cognitive_monitor.alert_threshold", 80)
        if load >= alert_threshold:
            try:
                from voice.tts import speak
                speak("Sir, your cognitive load is very high. "
                      "I recommend a 10-minute break before continuing.")
            except Exception:
                pass


def start_cognitive_monitor() -> None:
    """Start the cognitive load monitor (keyboard listener + scoring loop)."""
    if not _PYNPUT:
        print("[cognitive_monitor] pynput not available — monitor disabled.")
        return

    if not get_setting("cognitive_monitor.enabled", True):
        print("[cognitive_monitor] Disabled in settings.")
        return

    _stop_event.clear()

    listener = keyboard.Listener(on_press=_on_press)
    listener.daemon = True
    listener.start()

    t = threading.Thread(target=_monitor_loop, daemon=True, name="cognitive-monitor")
    t.start()
    print("[cognitive_monitor] Started.")


def stop_cognitive_monitor() -> None:
    _stop_event.set()
