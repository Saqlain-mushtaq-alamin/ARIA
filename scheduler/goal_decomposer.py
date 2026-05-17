"""Goal decomposer — breaks any goal into an optimal subtask schedule."""
from __future__ import annotations
import json, sqlite3, os, re
from datetime import date, timedelta
from typing import Any
from modules.content_generator import generate_text

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "goals.db")

def _init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY, goal TEXT, deadline TEXT,
            created_at TEXT, status TEXT DEFAULT 'active'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS subtasks (
            id INTEGER PRIMARY KEY, goal_id INTEGER,
            title TEXT, duration_min INTEGER, due_date TEXT,
            completed INTEGER DEFAULT 0, review_date TEXT,
            easiness REAL DEFAULT 2.5, repetitions INTEGER DEFAULT 0,
            interval_days INTEGER DEFAULT 1
        )""")

def _sm2_next_review(quality: int, reps: int, easiness: float, interval: int):
    if quality < 3:
        return 0, easiness, 1
    new_e = max(1.3, easiness + 0.1 - (5-quality)*(0.08+(5-quality)*0.02))
    new_i = 1 if reps == 0 else (6 if reps == 1 else round(interval * new_e))
    return reps + 1, new_e, new_i

def decompose_goal(goal: str, deadline_str: str = "") -> str:
    _init_db()
    try:
        from memory.user_profile import get_profile, get_peak_hours
        profile = get_profile()
        peaks = get_peak_hours()
        avg_completion = profile.get("meta", {}).get("habit_completion_rate", 0.75)
    except Exception:
        peaks = []
        avg_completion = 0.75

    try:
        from memory.habit_tracker import get_habit_schedule_hints
        hints = get_habit_schedule_hints()
        hint_str = json.dumps(hints, indent=2)
    except Exception:
        hint_str = "{}"

    peak_str = ", ".join(f"{h:02d}:00" for h in peaks) if peaks else "morning"
    plan_prompt = f"""You are a productivity planner. Decompose this goal into 5-10 specific subtasks.
Goal: {goal}
Deadline: {deadline_str or '2 weeks from today'}
User peak productive hours: {peak_str}
User historical task completion rate: {avg_completion:.0%}
Return a JSON array of subtasks:
[{{"title": "Subtask name", "duration_min": 45, "type": "study|practice|create|review", "due_date_offset_days": 2, "is_study_task": true}}]
Return ONLY the JSON array, no explanation."""

    raw = generate_text(plan_prompt)
    try:
        match = re.search(r'\[[\s\S]*\]', raw)
        subtasks = json.loads(match.group(0)) if match else []
    except Exception:
        return "Sir, I had trouble decomposing that goal. Please rephrase it."

    today = date.today()
    with sqlite3.connect(DB_PATH) as conn:
        goal_id = conn.execute(
            "INSERT INTO goals(goal, deadline, created_at) VALUES(?,?,?)",
            (goal, deadline_str, today.isoformat())
        ).lastrowid
        plan_lines = [f"📋 Goal plan for: {goal}\n"]
        for i, st in enumerate(subtasks):
            due = today + timedelta(days=int(st.get("due_date_offset_days", i+1)))
            buf = max(0, int((1 - avg_completion) * st.get("duration_min", 30)))
            total_min = st.get("duration_min", 30) + buf
            review_date = due + timedelta(days=1) if st.get("is_study_task") else None
            conn.execute(
                "INSERT INTO subtasks(goal_id, title, duration_min, due_date, review_date) VALUES(?,?,?,?,?)",
                (goal_id, st["title"], total_min, due.isoformat(),
                 review_date.isoformat() if review_date else None)
            )
            plan_lines.append(
                f"  {i+1}. {st['title']}\n"
                f"     Due: {due.strftime('%A %b %d')} | Est: {total_min} min | Type: {st.get('type','task')}"
                + (" | 🔁 Spaced review" if st.get("is_study_task") else "")
            )
    return "\n".join(plan_lines)

def morning_checkin() -> str:
    _init_db()
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT s.title, g.goal FROM subtasks s JOIN goals g ON g.id=s.goal_id "
            "WHERE s.due_date<=? AND s.completed=0 AND g.status='active' ORDER BY s.due_date",
            (today,)
        ).fetchall()
    if not rows:
        return ""
    lines = ["Sir, here are your outstanding goal tasks for today:"]
    for title, goal in rows:
        lines.append(f"  • {title}  [{goal}]")
    return "\n".join(lines)
