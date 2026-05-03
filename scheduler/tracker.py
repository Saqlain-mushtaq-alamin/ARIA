"""Task completion + habit streak tracking.

This is a lightweight persistence layer for accountability.

- Tasks can be initialized from an optimized day plan (blocks).
- User utterances like "I finished my coding assignment" can mark tasks complete.
- Habits (e.g. gym) are tracked as sets of dates with computed streaks.

State is stored in a small JSON file by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import os
import re
from typing import Any


DEFAULT_TRACKER_PATH = os.getenv(
    "TASK_TRACKER_PATH",
    os.path.join(os.path.dirname(__file__), "..", "memory", "task_tracker.json"),
)


def _today_iso() -> str:
    return date.today().isoformat()


def _normalize_key(text: str) -> str:
    s = (text or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _atomic_write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_state(path: str = DEFAULT_TRACKER_PATH) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"tasks": {}, "habits": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"tasks": {}, "habits": {}}
        data.setdefault("tasks", {})
        data.setdefault("habits", {})
        return data
    except Exception:
        return {"tasks": {}, "habits": {}}


def save_state(state: dict[str, Any], path: str = DEFAULT_TRACKER_PATH) -> None:
    _atomic_write_json(path, state)


def _infer_habit(task_name: str) -> str | None:
    name = _normalize_key(task_name)
    if any(k in name for k in ("gym", "workout", "exercise", "run", "walk", "lifting")):
        return "gym"
    return None


def init_day(
    schedule_blocks: list[dict[str, Any]],
    *,
    day: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
) -> dict[str, Any]:
    """Store the planned tasks for the given day (defaults to today)."""

    day_key = day or _today_iso()
    state = load_state(path)
    tasks_by_day: dict[str, Any] = state.setdefault("tasks", {})

    day_tasks: dict[str, Any] = tasks_by_day.setdefault(day_key, {})

    for b in schedule_blocks or []:
        task = str(b.get("task") or "").strip()
        if not task:
            continue
        task_key = _normalize_key(task)
        day_tasks.setdefault(
            task_key,
            {
                "task": task,
                "type": b.get("type"),
                "start": b.get("start"),
                "end": b.get("end"),
                "completed": False,
                "completed_at": None,
            },
        )

        habit = _infer_habit(task)
        if habit:
            state.setdefault("habits", {}).setdefault(_normalize_key(habit), {"name": habit, "dates": []})

    save_state(state, path)
    return state


def _clean_completion_phrase(user_text: str) -> str:
    s = (user_text or "").strip().lower()
    s = re.sub(r"^i\s+", "", s)
    s = re.sub(r"^(just\s+)?(finished|completed|did|done\s+with|wrapped\s+up)\s+", "", s)
    s = re.sub(r"^(my|the)\s+", "", s)
    s = re.sub(r"\s+today\.?$", "", s)
    return s.strip()


def mark_task_complete(
    user_text_or_task: str,
    *,
    day: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
) -> dict[str, Any]:
    """Mark the best-matching task for the day as complete.

    If no planned task matches, this will create an entry under that day.
    """

    day_key = day or _today_iso()
    state = load_state(path)
    tasks_by_day: dict[str, Any] = state.setdefault("tasks", {})
    day_tasks: dict[str, Any] = tasks_by_day.setdefault(day_key, {})

    raw = str(user_text_or_task or "").strip()
    if not raw:
        return state

    candidate = _clean_completion_phrase(raw)
    cand_key = _normalize_key(candidate)

    # Exact match.
    if cand_key in day_tasks:
        day_tasks[cand_key]["completed"] = True
        day_tasks[cand_key]["completed_at"] = datetime.utcnow().isoformat() + "Z"
        habit = _infer_habit(day_tasks[cand_key].get("task", ""))
        if habit:
            record_habit(habit, day=day_key, path=path, state=state)
        save_state(state, path)
        return state

    # Fuzzy match by substring overlap.
    best_key: str | None = None
    best_score = 0
    for key, obj in day_tasks.items():
        name = _normalize_key(str(obj.get("task") or ""))
        if not name:
            continue
        if cand_key and (cand_key in name or name in cand_key):
            score = min(len(cand_key), len(name))
            if score > best_score:
                best_score = score
                best_key = key

    if best_key is None:
        # Create a new completion record if we didn't find one.
        best_key = cand_key or _normalize_key(raw)
        day_tasks[best_key] = {
            "task": candidate or raw,
            "type": "flexible",
            "start": None,
            "end": None,
            "completed": True,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }
    else:
        day_tasks[best_key]["completed"] = True
        day_tasks[best_key]["completed_at"] = datetime.utcnow().isoformat() + "Z"

    habit = _infer_habit(day_tasks[best_key].get("task", ""))
    if habit:
        record_habit(habit, day=day_key, path=path, state=state)

    save_state(state, path)
    return state


def record_habit(
    habit_name: str,
    *,
    day: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a habit occurrence for a given day (defaults to today)."""

    day_key = day or _today_iso()
    s = state or load_state(path)

    habits: dict[str, Any] = s.setdefault("habits", {})
    key = _normalize_key(habit_name)
    entry = habits.setdefault(key, {"name": habit_name, "dates": []})

    dates: list[str] = list(entry.get("dates") or [])
    if day_key not in dates:
        dates.append(day_key)
        dates.sort()
    entry["dates"] = dates

    save_state(s, path)
    return s


def habit_streak(
    habit_name: str,
    *,
    as_of: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
) -> int:
    """Return consecutive-day streak ending at `as_of` (defaults to today)."""

    as_of_day = date.fromisoformat(as_of or _today_iso())
    state = load_state(path)
    habits: dict[str, Any] = state.get("habits", {}) or {}
    entry = habits.get(_normalize_key(habit_name))
    if not entry:
        return 0

    raw_dates = entry.get("dates") or []
    try:
        date_set = {date.fromisoformat(d) for d in raw_dates}
    except Exception:
        return 0

    streak = 0
    cursor = as_of_day
    while cursor in date_set:
        streak += 1
        cursor = cursor - timedelta(days=1)

    return streak


@dataclass(frozen=True)
class DayStats:
    planned: int
    completed: int


def day_stats(day: str | None = None, *, path: str = DEFAULT_TRACKER_PATH) -> DayStats:
    day_key = day or _today_iso()
    state = load_state(path)
    day_tasks: dict[str, Any] = (state.get("tasks") or {}).get(day_key) or {}
    planned = len(day_tasks)
    completed = sum(1 for t in day_tasks.values() if bool(t.get("completed")))
    return DayStats(planned=planned, completed=completed)
