"""Autonomous daily planner — morning briefing + evening review."""
from __future__ import annotations
import threading, time
from datetime import date

from config.settings import get as get_setting

try:
    from voice.tts import speak
except Exception:
    def speak(msg: str) -> None:
        print(f"[planner] {msg}")

_stop_event = threading.Event()

def _morning_briefing() -> None:
    from modules.content_generator import generate_text
    try:
        from memory.user_profile import get_address_form, get_peak_hours
        address = get_address_form()
        peaks = get_peak_hours()
    except Exception:
        address, peaks = "Sir", []
    try:
        from memory.habit_tracker import get_today_summary, get_advisor_nudges
        habits = get_today_summary()
        nudges = get_advisor_nudges()
    except Exception:
        habits, nudges = "", []
    try:
        from scheduler.goal_decomposer import morning_checkin
        goals = morning_checkin()
    except Exception:
        goals = ""

    today = date.today().strftime("%A, %B %d")
    prompt = (
        f"Generate a motivating morning briefing for {address} on {today}. "
        f"Peak hours today: {peaks}. "
        f"Today's habits: {habits}. "
        f"Goal tasks due: {goals}. "
        f"Advisor nudges: {'; '.join(nudges) if nudges else 'none'}. "
        f"Keep it under 120 words. Be energising. Address as {address}."
    )
    briefing = generate_text(prompt)
    speak(briefing)

def _evening_review() -> None:
    from modules.content_generator import generate_text
    try:
        from memory.user_profile import get_address_form
        address = get_address_form()
    except Exception:
        address = "Sir"
    try:
        from memory.habit_tracker import get_today_summary
        summary = get_today_summary()
    except Exception:
        summary = ""

    prompt = (
        f"Generate a brief evening review for {address}. "
        f"Today's habit completion: {summary}. "
        f"Keep it under 80 words. Be encouraging but honest. "
        f"End with one specific action for tomorrow. Address as {address}."
    )
    review = generate_text(prompt)
    speak(review)

def _parse_time(time_str: str) -> tuple[int, int]:
    parts = time_str.strip().split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0

def _planner_loop() -> None:
    from datetime import datetime
    last_morning = ""
    last_evening = ""
    while not _stop_event.is_set():
        time.sleep(30)  # check every 30 seconds
        if not get_setting("autonomous_planner.enabled", True):
            continue
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        morning_time = get_setting("autonomous_planner.morning_briefing_time", "06:00")
        evening_time = get_setting("autonomous_planner.evening_review_time", "22:00")
        mh, mm = _parse_time(morning_time)
        eh, em = _parse_time(evening_time)
        # Morning briefing
        if now.hour == mh and now.minute >= mm and now.minute < mm + 5 and last_morning != today_str:
            last_morning = today_str
            try:
                _morning_briefing()
            except Exception as e:
                print(f"[planner] Morning briefing error: {e}")
        # Evening review
        if now.hour == eh and now.minute >= em and now.minute < em + 5 and last_evening != today_str:
            last_evening = today_str
            try:
                _evening_review()
            except Exception as e:
                print(f"[planner] Evening review error: {e}")

def start_autonomous_planner() -> threading.Thread:
    _stop_event.clear()
    t = threading.Thread(target=_planner_loop, daemon=True, name="autonomous-planner")
    t.start()
    print("[planner] Autonomous planner started.")
    return t

def stop_autonomous_planner() -> None:
    _stop_event.set()
