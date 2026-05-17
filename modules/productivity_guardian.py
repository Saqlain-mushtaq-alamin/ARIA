"""Productivity guardian — detects schedule violations and alerts Sir."""
from __future__ import annotations

import json, os, threading, time
from datetime import datetime
from typing import Optional

from config.settings import get as get_setting

SCREEN_STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "memory", "screen_state.json"
)

_distraction_streak = 0
_last_alert_time: Optional[float] = None
_stop_event = threading.Event()


def _read_screen_state() -> dict:
    try:
        path = os.path.normpath(SCREEN_STATE_PATH)
        if not os.path.exists(path):
            # Try reading from screen_reader summaries
            try:
                from vision.screen_reader import get_recent_summaries
                summaries = get_recent_summaries(1)
                if summaries:
                    s = summaries[-1]
                    return {"app": s.app_hint, "activity": s.activity, "mood": s.emotional_hint}
            except Exception:
                pass
            return {}
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_scheduled_task() -> Optional[str]:
    try:
        from scheduler.tracker import get_current_scheduled_task
        return get_current_scheduled_task()
    except Exception:
        return None


def _is_distraction(screen_state: dict) -> bool:
    distraction_apps = get_setting("productivity_guardian.distraction_apps", [
        "instagram", "facebook", "twitter", "tiktok", "reddit",
        "youtube", "netflix", "twitch", "snapchat", "discord",
        "whatsapp web", "telegram web",
    ])
    activity = str(screen_state.get("activity", "")).lower()
    app = str(screen_state.get("app", "")).lower()
    return any(d in activity or d in app for d in distraction_apps)


def _should_be_working(scheduled_task: Optional[str]) -> bool:
    if not scheduled_task:
        return False
    task_lower = scheduled_task.lower()
    work_keywords = ["study", "exam", "assignment", "coding", "project",
                     "class", "lecture", "read", "write", "practice",
                     "work", "homework", "research", "review"]
    return any(kw in task_lower for kw in work_keywords)


def _fire_alert(scheduled_task: str, distraction: str) -> None:
    global _last_alert_time
    threshold = get_setting("productivity_guardian.distraction_threshold_minutes", 3)

    now = datetime.now()
    hour = now.hour
    urgency = "Sir" if hour < 22 else "Sir, it is getting late"

    message = (
        f"{urgency}, your schedule says you should be working on "
        f"'{scheduled_task}' right now, but I can see you have been "
        f"on {distraction} for the last {threshold} minutes. "
        f"Shall I close it and open your study materials?"
    )

    try:
        from voice.tts import speak
        speak(message)
    except Exception:
        pass

    try:
        from safety.audit_log import log_action, OUTCOME_SUCCESS
        log_action(
            intent="productivity_alert",
            parameters={"task": scheduled_task, "distraction": distraction},
            risk_level="info",
            outcome=OUTCOME_SUCCESS,
            result_summary=message[:100],
        )
    except Exception:
        pass

    _last_alert_time = time.time()
    print(f"[productivity_guardian] Alert: {message[:100]}")


def guardian_loop() -> None:
    """Main loop — run as a daemon thread from main.py."""
    global _distraction_streak

    while not _stop_event.is_set():
        check_interval = get_setting("productivity_guardian.check_interval_seconds", 60)
        threshold_min = get_setting("productivity_guardian.distraction_threshold_minutes", 3)
        cooldown_min = get_setting("productivity_guardian.cooldown_after_alert_minutes", 10)

        # Sleep in small chunks for responsiveness
        for _ in range(int(check_interval)):
            if _stop_event.is_set():
                return
            time.sleep(1)

        if not get_setting("productivity_guardian.enabled", True):
            continue

        try:
            screen = _read_screen_state()
            task = _get_scheduled_task()

            if _is_distraction(screen) and _should_be_working(task):
                _distraction_streak += 1
                cooldown_ok = (
                    _last_alert_time is None or
                    time.time() - _last_alert_time > cooldown_min * 60
                )
                if _distraction_streak >= threshold_min and cooldown_ok:
                    app_name = screen.get("app", "a distraction site")
                    _fire_alert(task, app_name)
            else:
                _distraction_streak = 0
        except Exception:
            pass


def start_guardian() -> threading.Thread:
    _stop_event.clear()
    t = threading.Thread(target=guardian_loop, name="productivity-guardian", daemon=True)
    t.start()
    print("[productivity_guardian] Guardian started.")
    return t


def stop_guardian() -> None:
    _stop_event.set()
