"""ARIA — Habit Tracker.

Tracks repeating behaviours over time to give ARIA a genuine understanding
of the user's daily life. Detects patterns, spots anomalies, and feeds both
the Smart Scheduler and the Proactive Advisor with real intelligence.

What it tracks automatically:
  ─────────────────────────────
  • Daily habits    — gym, study sessions, breaks, meals, coding, media
  • Streaks         — consecutive days a habit was maintained
  • Miss detection  — when a habit was skipped (and how often)
  • Time patterns   — what time a habit usually happens, and how long it lasts
  • Anomalies       — "You usually study at 9am but haven't opened anything yet at 11am"
  • Context pairing — "You always open Spotify when you start coding"

Storage: memory/habits.db  (SQLite — robust, queryable, fast)

Integration:
  ────────────
  # In agent.py — after every action dispatch:
  from memory.habit_tracker import record_event, get_today_summary

  record_event("gym", status="done")
  record_event("study", metadata={"subject": "chemistry", "duration_min": 45})

  # For the proactive advisor (runs every 30 min):
  from memory.habit_tracker import get_advisor_nudges
  nudges = get_advisor_nudges()   →  list of strings ARIA should say proactively

  # For the scheduler:
  from memory.habit_tracker import get_habit_schedule_hints
  hints = get_habit_schedule_hints()  →  dict of habit → typical_time
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(__file__), "habits.db"
)

# Minimum occurrences to consider something a "habit"
_HABIT_MIN_OCCURRENCES = 3

# How many days to look back for pattern detection
_PATTERN_WINDOW_DAYS = 14

# Days without a habit before it's considered broken
_STREAK_BREAK_DAYS = 2


# ─────────────────────────────────────────────────────────────────────────────
# Known habit categories (auto-detected from intent names and app names)
# ─────────────────────────────────────────────────────────────────────────────

_HABIT_TRIGGERS: Dict[str, str] = {
    # app names → habit category
    "spotify":          "music",
    "youtube music":    "music",
    "vlc":              "media",
    "youtube":          "media",
    "netflix":          "media",
    "code":             "coding",
    "vscode":           "coding",
    "visual studio":    "coding",
    "notepad++":        "coding",
    "sublime":          "coding",
    "word":             "studying",
    "pdf":              "studying",
    "chrome":           "browsing",
    "firefox":          "browsing",
    "telegram":         "social",
    "discord":          "social",
    "whatsapp":         "social",
    "messenger":        "social",
    "zoom":             "meeting",
    "teams":            "meeting",
    "gym":              "gym",
    "exercise":         "gym",
    "workout":          "gym",
    "sleep":            "sleep",
    "lock_screen":      "break",
}

# Intent names → habit category
_INTENT_HABITS: Dict[str, str] = {
    "search_papers":     "studying",
    "create_schedule":   "planning",
    "show_schedule":     "planning",
    "get_news":          "news",
    "get_weather":       "planning",
    "search_web":        "browsing",
    "send_message":      "social",
    "lock_screen":       "break",
    "sleep":             "sleep",
    "shutdown":          "sleep",
}


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS habit_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    habit_name      TEXT NOT NULL,
    event_date      TEXT NOT NULL,       -- YYYY-MM-DD
    event_time      TEXT NOT NULL,       -- HH:MM
    status          TEXT NOT NULL DEFAULT 'done',  -- done | skipped | partial
    duration_min    INTEGER,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS habit_definitions (
    habit_name      TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'custom',
    target_days     TEXT NOT NULL DEFAULT 'daily',   -- 'daily' | 'weekdays' | 'weekends' | JSON list
    expected_time   TEXT,                            -- 'HH:MM' or NULL
    expected_dur_min INTEGER,
    created_at      TEXT NOT NULL,
    last_seen       TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_date  ON habit_events(event_date DESC);
CREATE INDEX IF NOT EXISTS idx_events_habit ON habit_events(habit_name, event_date DESC);
"""


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    _ensure_dir(db_path)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)


def _now() -> datetime:
    return datetime.now()


def _today() -> str:
    return date.today().isoformat()


def _time_now() -> str:
    return _now().strftime("%H:%M")


# ─────────────────────────────────────────────────────────────────────────────
# Core event recording
# ─────────────────────────────────────────────────────────────────────────────

def record_event(
    habit_name: str,
    status: str = "done",
    duration_min: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    event_date: Optional[str] = None,
    event_time: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> int:
    """Record that a habit event occurred.

    Args:
        habit_name:   Name of the habit (e.g. 'gym', 'study', 'coding').
        status:       'done' | 'skipped' | 'partial'
        duration_min: How long it lasted (optional).
        metadata:     Extra data (subject, app_name, etc.)
        event_date:   Override date (YYYY-MM-DD). Defaults to today.
        event_time:   Override time (HH:MM). Defaults to now.
        db_path:      Database path.

    Returns:
        Inserted row ID.
    """
    init_db(db_path)
    name  = habit_name.strip().lower()
    d     = event_date or _today()
    t     = event_time or _time_now()
    meta  = json.dumps(metadata or {}, ensure_ascii=False)

    with _connect(db_path) as conn:
        row_id = conn.execute(
            """INSERT INTO habit_events(habit_name, event_date, event_time, status, duration_min, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, d, t, status, duration_min, meta),
        ).lastrowid

        # Upsert habit definition
        conn.execute(
            """INSERT INTO habit_definitions(habit_name, display_name, category, created_at, last_seen)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(habit_name) DO UPDATE SET last_seen = excluded.last_seen""",
            (name, habit_name.title(), _categorise(name), d, d),
        )

    if row_id is None:
        raise RuntimeError("Failed to insert habit event.")
    return int(row_id)


def record_from_intent(
    intent: str,
    parameters: Optional[Dict[str, Any]] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Auto-detect and record a habit from an agent intent dispatch.

    Call this in agent.py after every successful action.
    """
    params = parameters or {}
    habit  = None

    # Check intent → habit mapping
    if intent in _INTENT_HABITS:
        habit = _INTENT_HABITS[intent]

    # Check app_name → habit mapping
    if not habit and intent == "open_app":
        app = params.get("app_name", "").strip().lower()
        for trigger, category in _HABIT_TRIGGERS.items():
            if trigger in app:
                habit = category
                break

    if habit:
        record_event(
            habit_name=habit,
            metadata={"intent": intent, **params},
            db_path=db_path,
        )


def _categorise(habit_name: str) -> str:
    """Infer a category from a habit name."""
    name = habit_name.lower()
    for trigger, cat in _HABIT_TRIGGERS.items():
        if trigger in name:
            return cat
    return "custom"


# ─────────────────────────────────────────────────────────────────────────────
# Streak calculation
# ─────────────────────────────────────────────────────────────────────────────

def get_streak(
    habit_name: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Return current and longest streak for a habit.

    Returns:
        {
          "current": 5,          # consecutive days ending today
          "longest": 12,         # all-time best
          "last_done": "2025-03-10",
          "status": "active" | "broken" | "not_started"
        }
    """
    init_db(db_path)
    name = habit_name.strip().lower()

    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT DISTINCT event_date FROM habit_events
               WHERE habit_name = ? AND status = 'done'
               ORDER BY event_date DESC""",
            (name,),
        ).fetchall()

    if not rows:
        return {"current": 0, "longest": 0, "last_done": None, "status": "not_started"}

    done_dates = sorted({r[0] for r in rows}, reverse=True)
    last_done  = done_dates[0]
    today_str  = _today()
    yesterday  = (date.today() - timedelta(days=1)).isoformat()

    # Current streak: count backwards from today/yesterday
    current = 0
    check   = today_str if last_done == today_str else yesterday
    for d in done_dates:
        if d == check:
            current += 1
            prev = (date.fromisoformat(check) - timedelta(days=1)).isoformat()
            check = prev
        else:
            break

    # Longest streak: sliding window
    longest = 0
    run     = 1
    for i in range(1, len(done_dates)):
        d1 = date.fromisoformat(done_dates[i - 1])
        d2 = date.fromisoformat(done_dates[i])
        if (d1 - d2).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    longest = max(longest, current)

    days_since = (date.today() - date.fromisoformat(last_done)).days
    status = "active" if days_since <= 1 else "broken"

    return {
        "current":   current,
        "longest":   longest,
        "last_done": last_done,
        "status":    status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pattern detection
# ─────────────────────────────────────────────────────────────────────────────

def get_habit_patterns(
    habit_name: str,
    window_days: int = _PATTERN_WINDOW_DAYS,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Detect timing and duration patterns for a habit.

    Returns:
        {
          "typical_time":    "09:15",    # most common start time (±30 min)
          "typical_duration": 45,        # median duration in minutes
          "frequency":       "daily",    # daily / weekdays / weekends / N times/week
          "best_day":        "Monday",   # day of week with highest completion
          "completion_rate": 0.78,       # fraction of expected days completed
        }
    """
    init_db(db_path)
    name  = habit_name.strip().lower()
    since = (date.today() - timedelta(days=window_days)).isoformat()

    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT event_date, event_time, status, duration_min
               FROM habit_events
               WHERE habit_name = ? AND event_date >= ?
               ORDER BY event_date DESC""",
            (name, since),
        ).fetchall()

    if not rows:
        return {}

    done_rows = [(r[0], r[1], r[3]) for r in rows if r[2] == "done"]
    if not done_rows:
        return {}

    # Typical time — bucket into 30-min slots
    times = [t for _, t, _ in done_rows if t]
    if times:
        # Round to nearest 30 min and find mode
        def _round30(t: str) -> str:
            h, m = map(int, t.split(":"))
            m = 0 if m < 30 else 30
            return f"{h:02d}:{m:02d}"
        time_counts = Counter(_round30(t) for t in times)
        typical_time = time_counts.most_common(1)[0][0]
    else:
        typical_time = None

    # Typical duration
    durations = [d for _, _, d in done_rows if d is not None]
    typical_duration = int(sorted(durations)[len(durations) // 2]) if durations else None

    # Day of week analysis
    weekdays = [date.fromisoformat(d).strftime("%A") for d, _, _ in done_rows]
    best_day = Counter(weekdays).most_common(1)[0][0] if weekdays else None

    # Frequency classification
    unique_days   = len({d for d, _, _ in done_rows})
    completion_rate = unique_days / window_days
    if completion_rate >= 0.85:
        frequency = "daily"
    elif completion_rate >= 0.5:
        frequency = f"{round(completion_rate * 7):.0f}×/week"
    elif completion_rate >= 0.25:
        frequency = "a few times a week"
    else:
        frequency = "occasional"

    return {
        "typical_time":     typical_time,
        "typical_duration": typical_duration,
        "frequency":        frequency,
        "best_day":         best_day,
        "completion_rate":  round(completion_rate, 2),
    }


def detect_anomalies(
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, str]]:
    """Detect behavioural anomalies across all tracked habits.

    Returns a list of anomaly dicts the proactive advisor can act on.
    Examples:
      {"habit": "gym",   "type": "missed_today", "message": "Sir, you usually go to the gym around 18:00 but haven't yet."}
      {"habit": "study", "type": "streak_risk",  "message": "Sir, your 7-day study streak is at risk — nothing logged today."}
      {"habit": "coding","type": "unusual_gap",  "message": "Sir, you haven't coded in 3 days — that's unusual for you."}
    """
    init_db(db_path)
    anomalies: List[Dict[str, str]] = []
    today_str = _today()
    now_hour  = _now().hour

    with _connect(db_path) as conn:
        habits = conn.execute(
            "SELECT habit_name FROM habit_definitions WHERE last_seen >= ?",
            ((date.today() - timedelta(days=21)).isoformat(),),
        ).fetchall()

    for (habit_name,) in habits:
        patterns  = get_habit_patterns(habit_name, db_path=db_path)
        streak    = get_streak(habit_name, db_path=db_path)
        if not patterns:
            continue

        # Check if habit is expected today and not yet done
        typical_time = patterns.get("typical_time")
        freq = patterns.get("frequency", "")
        completion_rate = patterns.get("completion_rate", 0)

        # Only flag habits with at least 30% completion rate (established habits)
        if completion_rate < 0.30:
            continue

        # Check if already done today
        with _connect(db_path) as conn:
            done_today = conn.execute(
                "SELECT COUNT(*) FROM habit_events WHERE habit_name = ? AND event_date = ? AND status = 'done'",
                (habit_name, today_str),
            ).fetchone()[0]

        if done_today:
            continue  # all good

        # Missed today — is it past the typical time?
        if typical_time:
            exp_hour = int(typical_time.split(":")[0])
            if now_hour > exp_hour + 1:
                anomalies.append({
                    "habit":   habit_name,
                    "type":    "missed_today",
                    "message": (
                        f"Sir, you usually {habit_name} around {typical_time} "
                        f"but haven't yet today. Everything okay?"
                    ),
                    "severity": "medium",
                })

        # Streak at risk
        if streak["current"] >= 3:
            anomalies.append({
                "habit":   habit_name,
                "type":    "streak_risk",
                "message": (
                    f"Sir, your {streak['current']}-day {habit_name} streak is at risk — "
                    f"nothing logged today yet."
                ),
                "severity": "high",
            })

        # Unusual gap — last done more than 2x the typical gap
        last_done = streak.get("last_done")
        if last_done:
            days_since = (date.today() - date.fromisoformat(last_done)).days
            if freq == "daily" and days_since >= 3:
                anomalies.append({
                    "habit":   habit_name,
                    "type":    "unusual_gap",
                    "message": (
                        f"Sir, you haven't done {habit_name} in {days_since} days — "
                        f"that's unusual based on your pattern."
                    ),
                    "severity": "low",
                })

    return anomalies


# ─────────────────────────────────────────────────────────────────────────────
# Proactive advisor feed
# ─────────────────────────────────────────────────────────────────────────────

def get_advisor_nudges(
    db_path: str = DEFAULT_DB_PATH,
    max_nudges: int = 3,
) -> List[str]:
    """Return a list of proactive suggestion strings for the advisor.

    The proactive advisor background thread calls this every 30 minutes.
    Returns ready-to-speak sentences addressing the user as "Sir".

    Args:
        max_nudges: Maximum number of nudges to return (avoid being annoying).

    Returns:
        List of suggestion strings, highest severity first.
    """
    anomalies = detect_anomalies(db_path=db_path)
    if not anomalies:
        return []

    # Sort: high severity first
    order = {"high": 0, "medium": 1, "low": 2}
    anomalies.sort(key=lambda a: order.get(a.get("severity", "low"), 2))

    return [a["message"] for a in anomalies[:max_nudges]]


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler integration
# ─────────────────────────────────────────────────────────────────────────────

def get_habit_schedule_hints(
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Dict[str, Any]]:
    """Return scheduling hints for all established habits.

    The Smart Scheduler calls this to pre-fill fixed time blocks
    based on the user's real patterns.

    Returns:
        {
          "gym":   {"typical_time": "18:00", "duration_min": 60, "frequency": "daily"},
          "study": {"typical_time": "09:00", "duration_min": 90, "frequency": "daily"},
          ...
        }
    """
    init_db(db_path)
    with _connect(db_path) as conn:
        habits = conn.execute(
            "SELECT habit_name FROM habit_definitions"
        ).fetchall()

    hints: Dict[str, Dict[str, Any]] = {}
    for (name,) in habits:
        patterns = get_habit_patterns(name, db_path=db_path)
        if not patterns or patterns.get("completion_rate", 0) < 0.3:
            continue
        hints[name] = {
            "typical_time": patterns.get("typical_time"),
            "duration_min": patterns.get("typical_duration"),
            "frequency":    patterns.get("frequency"),
        }

    return hints


# ─────────────────────────────────────────────────────────────────────────────
# Summary views
# ─────────────────────────────────────────────────────────────────────────────

def get_today_summary(db_path: str = DEFAULT_DB_PATH) -> str:
    """Return a today's habit status summary addressed to Sir."""
    init_db(db_path)
    today_str = _today()

    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT h.habit_name, e.status, e.event_time, e.duration_min
               FROM habit_definitions h
               LEFT JOIN habit_events e ON e.habit_name = h.habit_name AND e.event_date = ?
               WHERE h.last_seen >= ?
               ORDER BY e.event_time ASC""",
            (today_str, (date.today() - timedelta(days=14)).isoformat()),
        ).fetchall()

    if not rows:
        return "No habits tracked yet today, Sir."

    done, pending = [], []
    for habit, status, t, dur in rows:
        if status == "done":
            dur_str = f" ({dur} min)" if dur else ""
            done.append(f"  ✓ {habit.title()}{dur_str} at {t or '?'}")
        elif status is None:
            pending.append(f"  ○ {habit.title()} — not yet")

    lines = [f"📋 Today's habits, Sir:"]
    if done:
        lines += done
    if pending:
        lines += pending
    if not done and not pending:
        lines.append("  Nothing tracked yet today.")

    return "\n".join(lines)


def get_weekly_habit_report(
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Generate a weekly habit performance report for the weekly review module."""
    init_db(db_path)
    since = (date.today() - timedelta(days=7)).isoformat()

    with _connect(db_path) as conn:
        habits = conn.execute(
            "SELECT DISTINCT habit_name FROM habit_events WHERE event_date >= ? AND status = 'done'",
            (since,),
        ).fetchall()

    if not habits:
        return "No habit data for this week, Sir."

    lines = ["📊 Weekly Habit Report — Sir"]
    lines.append(f"   Period: {since} → {_today()}\n")

    for (name,) in habits:
        streak   = get_streak(name, db_path=db_path)
        patterns = get_habit_patterns(name, window_days=7, db_path=db_path)
        rate     = patterns.get("completion_rate", 0)
        bar      = "█" * round(rate * 10) + "░" * (10 - round(rate * 10))
        streak_txt = f"🔥 {streak['current']}-day streak" if streak["current"] > 1 else ""

        lines.append(
            f"  {name.title():<18} [{bar}] {int(rate*100):>3}%  {streak_txt}"
        )

    lines.append(
        f"\nKeep it up, Sir. Consistency is what separates goals from achievements."
    )
    return "\n".join(lines)


def get_all_habits_summary(db_path: str = DEFAULT_DB_PATH) -> str:
    """Return an overview of all tracked habits with streaks and patterns."""
    init_db(db_path)
    with _connect(db_path) as conn:
        habits = conn.execute(
            "SELECT habit_name, display_name, last_seen FROM habit_definitions ORDER BY last_seen DESC"
        ).fetchall()

    if not habits:
        return "No habits tracked yet, Sir. I'll start learning your patterns automatically."

    lines = ["🧠 Habit Intelligence — Sir\n"]
    for name, display, last_seen in habits:
        streak   = get_streak(name, db_path=db_path)
        patterns = get_habit_patterns(name, db_path=db_path)
        if not patterns:
            continue
        rate     = int(patterns.get("completion_rate", 0) * 100)
        t        = patterns.get("typical_time") or "?"
        freq     = patterns.get("frequency") or "?"
        cur      = streak["current"]
        best     = streak["longest"]

        lines.append(f"  {display}")
        lines.append(f"    Frequency  : {freq} | Typical time : {t}")
        lines.append(f"    Completion : {rate}% | Current streak: {cur} days (best: {best})")
        lines.append("")

    return "\n".join(lines).rstrip()


def mark_habit_done(
    habit_name: str,
    duration_min: Optional[int] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Convenience function — mark a habit as done today."""
    record_event(habit_name, status="done", duration_min=duration_min, db_path=db_path)
    streak = get_streak(habit_name, db_path=db_path)
    cur = streak["current"]
    streak_msg = f" 🔥 {cur}-day streak!" if cur > 1 else ""
    return f"✓ {habit_name.title()} logged, Sir.{streak_msg}"


def mark_habit_skipped(
    habit_name: str,
    reason: str = "",
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Mark a habit as skipped today with an optional reason."""
    record_event(
        habit_name, status="skipped",
        metadata={"reason": reason},
        db_path=db_path,
    )
    return f"Noted, Sir — {habit_name} skipped today{(': ' + reason) if reason else ''}."


def get_all_habits_summary(db_path: str = DEFAULT_DB_PATH) -> str:
    """Return a formatted summary of all tracked habits with streaks."""
    try:
        conn = _get_conn(db_path)
        rows = conn.execute(
            "SELECT DISTINCT habit_name FROM events ORDER BY habit_name"
        ).fetchall()
    except Exception:
        return "Sir, I don't have any habit data yet."

    if not rows:
        return "Sir, no habits are being tracked yet. Say 'log habit gym' to start."

    lines = ["📊 Your Habits", "━" * 40]
    for (name,) in rows:
        streak = get_streak(name, db_path=db_path)
        cur = streak.get("current", 0)
        best = streak.get("best", 0)
        total = streak.get("total_count", 0)
        fire = "🔥 " if cur > 0 else "  "
        lines.append(
            f"  {fire}{name.title()}: {cur}-day streak "
            f"(best: {best}, total: {total})"
        )
    lines.append("━" * 40)
    return "\n".join(lines)
