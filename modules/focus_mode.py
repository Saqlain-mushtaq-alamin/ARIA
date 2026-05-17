"""Deep work mode — Pomodoro + flow scoring + session analytics."""
from __future__ import annotations

import json, os, sqlite3, time, threading
from datetime import datetime
from typing import Optional

from config.settings import get as get_setting

try:
    from voice.tts import speak
except Exception:
    def speak(msg: str) -> None:
        print(f"[focus_mode] {msg}")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "focus_sessions.db")
SCREEN_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "memory", "screen_state.json")

HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
_ARIA_BLOCK_TAG = "# ARIA-BLOCK"

_active = False
_flow_scores: list[float] = []
_activity_log: list[dict] = []
_stop_event = threading.Event()
_start_time: Optional[datetime] = None
_current_task: str = ""


def _init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, start_time TEXT, end_time TEXT,
            duration_min INTEGER, flow_score REAL,
            cycles_completed INTEGER, task TEXT, report TEXT
        )""")


def _read_screen() -> dict:
    try:
        # Try screen_reader summaries first
        from vision.screen_reader import get_recent_summaries
        summaries = get_recent_summaries(1)
        if summaries:
            s = summaries[-1]
            return {"app": s.app_hint, "activity": s.activity, "mood": s.emotional_hint}
    except Exception:
        pass
    try:
        if os.path.exists(SCREEN_STATE):
            with open(SCREEN_STATE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _compute_flow_score(window: list[dict]) -> float:
    """Score 0-100 based on consistency of activity in a 5-min window."""
    if not window:
        return 50.0
    apps = [w.get("app", "") for w in window]
    unique_apps = len(set(a for a in apps if a))
    stuck_flag = any(
        w.get("stuck") or w.get("mood", "").lower() in ("stuck", "frustrated")
        for w in window
    )
    # Single app, not stuck = flow. Multiple apps = distraction.
    base = max(0, 100 - (unique_apps - 1) * 25)
    if stuck_flag:
        base = max(0, base - 20)
    return float(base)


def block_urls(domains: list[str]) -> str:
    """Redirect domains to 127.0.0.1 via Windows hosts file."""
    import ctypes
    if not ctypes.windll.shell32.IsUserAnAdmin():
        return "Sir, I need Administrator rights to block websites. Run ARIA as Admin."
    try:
        with open(HOSTS_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n{_ARIA_BLOCK_TAG}\n")
            for domain in domains:
                f.write(f"127.0.0.1 {domain}\n127.0.0.1 www.{domain}\n")
        return f"Blocked {len(domains)} site(s)."
    except Exception as exc:
        return f"Could not block sites: {exc}"


def unblock_urls() -> str:
    """Remove all ARIA-added blocks from hosts file."""
    try:
        with open(HOSTS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        clean = []
        skip = False
        for line in lines:
            if _ARIA_BLOCK_TAG in line:
                skip = True
                continue
            if skip and line.strip() == "":
                skip = False
                continue
            if not skip:
                clean.append(line)
        with open(HOSTS_PATH, "w", encoding="utf-8") as f:
            f.writelines(clean)
        return "All website blocks removed, Sir."
    except Exception as exc:
        return f"Could not unblock sites: {exc}"


def _pomodoro_timer(work_min: int, task: str) -> None:
    global _flow_scores, _activity_log, _stop_event

    short_min = get_setting("focus_mode.short_break_minutes", 5)
    long_min = get_setting("focus_mode.long_break_minutes", 20)
    cycles_before_long = get_setting("focus_mode.cycles_before_long_break", 4)

    cycle = 0
    while not _stop_event.is_set():
        cycle += 1
        speak(f"Sir, starting Pomodoro cycle {cycle}. Focus time begins now.")
        window: list[dict] = []
        end_work = time.time() + work_min * 60

        while time.time() < end_work and not _stop_event.is_set():
            time.sleep(60)  # sample every minute
            state = _read_screen()
            window.append(state)
            _activity_log.append({
                **state, "cycle": cycle, "at": datetime.now().isoformat()
            })

            # Flow score every 5 samples
            if len(window) % 5 == 0:
                score = _compute_flow_score(window[-5:])
                _flow_scores.append(score)
                if score < 40:
                    speak("Sir, your focus score just dropped. You appear to be switching frequently.")

        if _stop_event.is_set():
            break

        # Break announcement
        is_long = cycle % cycles_before_long == 0
        break_min = long_min if is_long else short_min
        break_type = "long" if is_long else "short"
        speak(f"Pomodoro {cycle} complete, Sir. Take a {break_min}-minute {break_type} break.")

        # Wait for break
        break_end = time.time() + break_min * 60
        while time.time() < break_end and not _stop_event.is_set():
            time.sleep(1)

        if not _stop_event.is_set():
            speak("Break over, Sir. Back to work.")


def start_focus_mode(task: str = "deep work", work_min: int = 0) -> str:
    global _active, _stop_event, _flow_scores, _activity_log, _start_time, _current_task

    if _active:
        return "Sir, focus mode is already running."

    _init_db()
    _active = True
    _stop_event.clear()
    _flow_scores = []
    _activity_log = []
    _start_time = datetime.now()
    _current_task = task

    if work_min <= 0:
        work_min = get_setting("focus_mode.work_minutes", 25)

    short_min = get_setting("focus_mode.short_break_minutes", 5)
    block_domains = get_setting("focus_mode.block_domains", [])

    # Block distracting websites
    block_result = ""
    if block_domains:
        block_result = block_urls(block_domains)

    speak(f"Sir, deep work mode activated. Working on: {task}. All distractions blocked.")

    t = threading.Thread(
        target=_pomodoro_timer, args=(work_min, task), daemon=True
    )
    t.start()

    return (
        f"Deep work mode started, Sir.\n"
        f"Task: {task}\n"
        f"Pomodoro: {work_min} min work / {short_min} min break\n"
        f"{block_result}\n"
        f"Say 'stop focus mode' to end the session."
    )


def stop_focus_mode() -> str:
    global _active, _start_time, _current_task

    if not _active:
        return "Sir, focus mode is not currently running."

    _stop_event.set()
    _active = False
    unblock_urls()

    # Calculate session duration
    duration_min = 0
    if _start_time:
        duration_min = int((datetime.now() - _start_time).total_seconds() / 60)

    # Generate report
    avg_flow = sum(_flow_scores) / len(_flow_scores) if _flow_scores else 0
    report = _generate_session_report(avg_flow, _activity_log, _current_task, duration_min)

    # Save to DB
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO sessions(date, start_time, end_time, duration_min, flow_score, task, report) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    _start_time.strftime("%Y-%m-%d") if _start_time else "",
                    _start_time.isoformat() if _start_time else "",
                    datetime.now().isoformat(),
                    duration_min,
                    avg_flow,
                    _current_task,
                    report,
                )
            )
    except Exception:
        pass

    speak(f"Sir, deep work session complete. Your average flow score was "
          f"{avg_flow:.0f} out of 100.")
    return report


def _generate_session_report(flow_score: float, log: list, task: str, duration_min: int) -> str:
    apps_used = list({e.get('app', '?') for e in log if e.get('app')})
    cycles = max({e.get('cycle', 0) for e in log}, default=0)

    lines = [
        f"📊 Deep Work Session Report",
        f"━" * 40,
        f"  Task: {task}",
        f"  Duration: {duration_min} minutes",
        f"  Pomodoro cycles: {cycles}",
        f"  Flow score: {flow_score:.0f}/100",
        f"  Apps used: {', '.join(apps_used[:5]) if apps_used else 'N/A'}",
        f"━" * 40,
    ]

    if flow_score >= 80:
        lines.append("  Excellent focus, Sir. You were in the zone.")
    elif flow_score >= 60:
        lines.append("  Good session, Sir. Minor distractions detected.")
    elif flow_score >= 40:
        lines.append("  Average focus. Try closing unnecessary apps before your next session.")
    else:
        lines.append("  Low focus detected. Consider a shorter Pomodoro interval next time.")

    return "\n".join(lines)


def is_active() -> bool:
    return _active
