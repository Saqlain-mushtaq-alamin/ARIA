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

from scheduler.optimizer import optimize_day
from scheduler.routine_generator import format_timeline
from scheduler.task_parser import parse_tasks


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
        return {"tasks": {}, "habits": {}, "plans": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"tasks": {}, "habits": {}, "plans": {}}
        data.setdefault("tasks", {})
        data.setdefault("habits", {})
        data.setdefault("plans", {})
        return data
    except Exception:
        return {"tasks": {}, "habits": {}, "plans": {}}


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


def save_plan(
    *,
    day: str | None = None,
    input_text: str | None = None,
    parsed_tasks: list[dict[str, Any]] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    path: str = DEFAULT_TRACKER_PATH,
) -> dict[str, Any]:
    """Persist a full daily plan (source tasks + optimized blocks)."""

    day_key = day or _today_iso()
    state = load_state(path)
    plans: dict[str, Any] = state.setdefault("plans", {})
    plans[day_key] = {
        "input": input_text or "",
        "tasks": list(parsed_tasks or []),
        "blocks": list(blocks or []),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    save_state(state, path)
    return state


def load_plan(day: str | None = None, *, path: str = DEFAULT_TRACKER_PATH) -> dict[str, Any] | None:
    day_key = day or _today_iso()
    state = load_state(path)
    plans: dict[str, Any] = state.get("plans", {}) or {}
    plan = plans.get(day_key)
    return plan if isinstance(plan, dict) else None


def has_plan(day: str | None = None, *, path: str = DEFAULT_TRACKER_PATH) -> bool:
    return load_plan(day, path=path) is not None


def create_schedule_from_text(
    text: str,
    *,
    day: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
    include_labels: bool = True,
    use_reference: bool = False,
) -> str:
    """Parse -> optimize -> store plan -> return formatted timeline.

    If use_reference=True and a plan already exists for the day, merge the new
    parsed tasks on top of the existing plan (new tasks override by name).
    """

    day_key = day or _today_iso()
    parsed = parse_tasks(text)
    if use_reference:
        existing = load_plan(day_key, path=path)
        existing_tasks = [t for t in (existing or {}).get("tasks", []) if isinstance(t, dict)]
        if existing_tasks:
            by_key: dict[str, dict[str, Any]] = {}
            # Keep only fixed commitments from the previous plan.
            for t in existing_tasks:
                if t.get("type") == "fixed" and t.get("time"):
                    k = _normalize_key(str(t.get("task") or ""))
                    if k:
                        by_key[k] = dict(t)
            for t in parsed:
                k = _normalize_key(str(t.get("task") or ""))
                if k:
                    by_key[k] = dict(t)
            parsed = list(by_key.values())
    blocks = optimize_day(parsed)
    save_plan(day=day_key, input_text=text, parsed_tasks=parsed, blocks=blocks, path=path)
    init_day(blocks, day=day_key, path=path)
    return format_timeline(blocks, include_labels=include_labels)


def get_schedule_blocks(day: str | None = None, *, path: str = DEFAULT_TRACKER_PATH) -> list[dict[str, Any]]:
    """Return the stored optimized blocks for the day, if available."""

    plan = load_plan(day, path=path)
    if plan:
        blocks_obj = plan.get("blocks")
        if isinstance(blocks_obj, list):
            return [b for b in blocks_obj if isinstance(b, dict)]

    # Fallback: reconstruct from stored day tasks.
    day_key = day or _today_iso()
    state = load_state(path)
    day_tasks: dict[str, Any] = (state.get("tasks") or {}).get(day_key) or {}
    blocks: list[dict[str, Any]] = []
    for obj in day_tasks.values():
        task = str(obj.get("task") or "").strip()
        start = obj.get("start")
        end = obj.get("end")
        if task and start and end:
            blocks.append({"task": task, "type": obj.get("type") or "flexible", "start": start, "end": end})
    blocks.sort(key=lambda b: (str(b.get("start") or "00:00"), str(b.get("end") or "00:00")))
    return blocks


def show_schedule(
    *,
    day: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
    include_labels: bool = True,
) -> str:
    blocks = get_schedule_blocks(day, path=path)
    if not blocks:
        return "No schedule found. Say: 'schedule my day: ...'"
    return format_timeline(blocks, include_labels=include_labels)


def whats_next(
    *,
    day: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
) -> str:
    blocks = get_schedule_blocks(day, path=path)
    if not blocks:
        return "No schedule found. Say: 'schedule my day: ...'"

    now = datetime.now()
    now_m = now.hour * 60 + now.minute

    def to_mins(hhmm: Any) -> int:
        try:
            if hhmm is None:
                return 0
            s = str(hhmm)
            if ":" not in s:
                return 0
            h, m = s.split(":", 1)
            return int(h) * 60 + int(m)
        except Exception:
            return 0

    # If we're currently inside a block, return it as "Now".
    for b in blocks:
        s = to_mins(b.get("start"))
        e = to_mins(b.get("end"))
        if s <= now_m < e:
            return f"Now: {b.get('start')} – {b.get('end')}  {b.get('task')}"

    # Otherwise, find next upcoming.
    upcoming = [b for b in blocks if to_mins(b.get("start")) > now_m]
    if not upcoming:
        return "You're done for the day (no more scheduled blocks)."

    next_b = sorted(upcoming, key=lambda b: to_mins(b.get("start")))[0]
    return f"Next: {next_b.get('start')} – {next_b.get('end')}  {next_b.get('task')}"


def edit_schedule(
    command: str,
    *,
    day: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
    include_labels: bool = True,
) -> str:
    """Edit the stored plan using a simple command language.

    Supported:
      - add <task> [at <time>]
      - remove <task>
      - move <task> to <time>
      - reschedule <task> to <time>

    After edits, the day is re-optimized and stored.
    """

    day_key = day or _today_iso()
    plan = load_plan(day_key, path=path)
    if not plan:
        return "No schedule found to edit. First say: 'schedule my day: ...'"

    tasks = [t for t in (plan.get("tasks") or []) if isinstance(t, dict)]
    raw = str(command or "").strip()
    if not raw:
        return "Tell me what to change. Example: 'edit schedule: move gym to 7pm'"

    cleaned = raw.strip()
    cleaned = re.sub(r"^edit\s+schedule\s*[:\-]?\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^update\s+schedule\s*[:\-]?\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^change\s+schedule\s*[:\-]?\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(
        r"^edit\s+(the\s+)?(day\s+)?(plan|schedule)\s*[:\-]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"^update\s+(the\s+)?(day\s+)?(plan|schedule)\s*[:\-]?\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^change\s+(the\s+)?(day\s+)?(plan|schedule)\s*[:\-]?\s*", "", cleaned, flags=re.IGNORECASE).strip()
    lowered = cleaned.lower().strip()

    def parse_time(text: str) -> str | None:
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
        if m:
            h = int(m.group(1))
            mm = int(m.group(2) or 0)
            ap = m.group(3)
            if h == 12:
                h = 0
            if ap == "pm":
                h += 12
            return f"{h:02d}:{mm:02d}"
        # Support dot time like 9.00
        m = re.search(r"\b(\d{1,2})[\.:](\d{2})\b", text)
        if m:
            h = int(m.group(1))
            mm = int(m.group(2))
            if not (0 <= h <= 23 and 0 <= mm <= 59):
                return None
            if h <= 12:
                if re.search(r"\b(morning|am)\b", text):
                    h = 0 if h == 12 else h
                elif re.search(r"\b(evening|night|pm)\b", text):
                    h = 12 if h == 12 else h + 12
            return f"{h:02d}:{mm:02d}"
        m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
        if m:
            h = int(m.group(1))
            mm = int(m.group(2))
            if 0 <= h <= 23 and 0 <= mm <= 59:
                return f"{h:02d}:{mm:02d}"
        return None

    def duration_minutes(t: dict[str, Any]) -> int:
        dur = str(t.get("duration") or "").strip().lower()
        m = re.fullmatch(r"(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?", dur)
        if m:
            hours = int(m.group("h") or 0)
            mins = int(m.group("m") or 0)
            total = hours * 60 + mins
            if total > 0:
                return total

        # If we have explicit start/end, compute duration.
        start = t.get("time") or t.get("start")
        end = t.get("end")
        if start and end and isinstance(start, str) and isinstance(end, str) and ":" in start and ":" in end:
            try:
                sh, sm = start.split(":")
                eh, em = end.split(":")
                s = int(sh) * 60 + int(sm)
                e = int(eh) * 60 + int(em)
                if e > s:
                    return e - s
            except Exception:
                pass

        return 60

    def hhmm_to_minutes(hhmm: str) -> int:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)

    def fixed_interval(t: dict[str, Any]) -> tuple[int, int] | None:
        if t.get("type") != "fixed":
            return None
        start = t.get("time") or t.get("start")
        if not start:
            return None
        try:
            s = hhmm_to_minutes(str(start))
        except Exception:
            return None
        dur = duration_minutes(t)
        return (s, s + dur)

    def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
        return a[0] < b[1] and b[0] < a[1]

    def nearest_free_start(
        desired_start: int,
        dur: int,
        busy: list[tuple[int, int]],
        *,
        day_start: int = 6 * 60,
        day_end: int = 22 * 60,
        step: int = 15,
    ) -> int | None:
        """Find the nearest non-overlapping start time in step-minute increments."""

        def ok(s: int) -> bool:
            if s < day_start or s + dur > day_end:
                return False
            interval = (s, s + dur)
            return all(not overlaps(interval, b) for b in busy)

        if ok(desired_start):
            return desired_start

        max_radius = day_end - day_start
        for r in range(step, max_radius + step, step):
            # Try after then before (keeps schedule moving forward).
            for cand in (desired_start + r, desired_start - r):
                if ok(cand):
                    return cand
        return None

    def find_task_index(name: str) -> int | None:
        key = _normalize_key(name)
        if not key:
            return None

        stop = {
            "the",
            "a",
            "an",
            "my",
            "today",
            "this",
            "task",
            "plan",
            "schedule",
        }
        key_tokens = [t for t in key.split() if t and t not in stop]

        best: int | None = None
        best_score = -1
        for i, t in enumerate(tasks):
            tname = _normalize_key(str(t.get("task") or ""))
            if not tname:
                continue
            if key == tname:
                return i

            # Substring match.
            if key in tname or tname in key:
                score = 1000 + min(len(key), len(tname))
                if score > best_score:
                    best_score = score
                    best = i
                continue

            # Token overlap for noisy voice transcripts.
            t_tokens = [tt for tt in tname.split() if tt and tt not in stop]
            overlap = len(set(key_tokens) & set(t_tokens))
            if overlap:
                score = overlap * 10 + min(len(key), len(tname))
                if score > best_score:
                    best_score = score
                    best = i

        return best

    if lowered.startswith("add "):
        frag = cleaned[4:].strip()
        new_tasks = parse_tasks(frag)
        if not new_tasks:
            return "Couldn't parse what to add. Example: 'edit schedule: add study 1 chapter'"
        tasks.extend(new_tasks)
    elif lowered.startswith("remove "):
        name = cleaned[7:].strip()
        idx = find_task_index(name)
        if idx is None:
            return f"Couldn't find '{name}' in today's plan."
        tasks.pop(idx)
    elif lowered.startswith(("move ", "reschedule ", "make ", "set ", "put ")):
        m = re.search(r"^(move|reschedule|make|set|put)\s+(.+?)\s+(?:to|at|for)\s+(.+)$", lowered)
        if m:
            task_name = m.group(2).strip()
            time_str = parse_time(m.group(3))
        else:
            # Allow: "move gym 7pm" (no preposition)
            m2 = re.search(
                r"^(move|reschedule|make|set|put)\s+(.+?)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}[\.:]\d{2}|\d{1,2}:\d{2})\b",
                lowered,
            )
            if not m2:
                return "Try: 'edit schedule: move gym to 7pm'"
            task_name = m2.group(2).strip()
            time_str = parse_time(m2.group(3))
        if not time_str:
            return "I couldn't read the new time. Example: '7pm' or '19:00'."
        idx = find_task_index(task_name)
        if idx is None:
            return f"Couldn't find '{task_name}' in today's plan."

        # Prepare tentative move.
        candidate = dict(tasks[idx])
        candidate["type"] = "fixed"
        candidate["time"] = time_str
        candidate.pop("start", None)
        candidate.pop("end", None)

        cand_interval = fixed_interval(candidate)
        if cand_interval is not None:
            # Build busy intervals excluding the moved task itself.
            busy: list[tuple[int, int]] = []
            fixed_indices: list[int] = []
            for j, other in enumerate(tasks):
                if j == idx:
                    continue
                other_interval = fixed_interval(other)
                if other_interval is None:
                    continue
                busy.append(other_interval)
                fixed_indices.append(j)

            # If the candidate conflicts, aggressively relocate the other fixed task(s).
            conflicts = []
            for j in fixed_indices:
                other_interval = fixed_interval(tasks[j])
                if other_interval is not None and overlaps(cand_interval, other_interval):
                    conflicts.append(j)

            # Reserve the moved task time first.
            busy_with_candidate = busy + [cand_interval]

            for j in conflicts:
                other = dict(tasks[j])
                other_interval = fixed_interval(other)
                if other_interval is None:
                    continue

                dur = duration_minutes(other)
                original_start = other_interval[0]
                new_start = nearest_free_start(original_start, dur, busy_with_candidate)
                if new_start is None:
                    other_name = str(other.get("task") or "another task").strip() or "another task"
                    return f"Couldn't find a free slot to move '{other_name}'."

                other["type"] = "fixed"
                other["time"] = f"{new_start // 60:02d}:{new_start % 60:02d}"
                other.pop("start", None)
                other.pop("end", None)
                tasks[j] = other
                # Update busy list with the moved interval.
                busy_with_candidate.append((new_start, new_start + dur))

        tasks[idx] = candidate
    else:
        return "Supported edits: add/remove/move. Examples: 'edit schedule: add study 1 chapter', 'edit schedule: remove gym', 'edit schedule: move gym to 7pm'."

    blocks = optimize_day(tasks)
    save_plan(day=day_key, input_text=str(plan.get("input") or ""), parsed_tasks=tasks, blocks=blocks, path=path)
    init_day(blocks, day=day_key, path=path)
    return format_timeline(blocks, include_labels=include_labels)


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


def upsert_day_task(
    task_name: str,
    *,
    start: str | None = None,
    end: str | None = None,
    completed: bool = False,
    task_type: str = "fixed",
    day: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
) -> dict[str, Any]:
    """Create/update a task entry for the day.

    This is intended for UI callers (scheduler view) that want a lightweight
    persistence mechanism without re-optimizing the whole plan.
    """

    day_key = day or _today_iso()
    state = load_state(path)
    tasks_by_day: dict[str, Any] = state.setdefault("tasks", {})
    day_tasks: dict[str, Any] = tasks_by_day.setdefault(day_key, {})

    raw = str(task_name or "").strip()
    key = _normalize_key(raw) or "task"

    entry = day_tasks.get(key)
    if not isinstance(entry, dict):
        entry = {
            "task": raw or key,
            "type": task_type,
            "start": start,
            "end": end,
            "completed": False,
            "completed_at": None,
        }

    entry["task"] = raw or entry.get("task") or key
    entry["type"] = task_type
    entry["start"] = start
    entry["end"] = end
    entry["completed"] = bool(completed)
    if completed and not entry.get("completed_at"):
        entry["completed_at"] = datetime.utcnow().isoformat() + "Z"
    if not completed:
        entry["completed_at"] = None

    day_tasks[key] = entry
    save_state(state, path)
    return state


def delete_day_task(
    task_id_or_name: str,
    *,
    day: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
) -> dict[str, Any]:
    """Delete a task entry for the day (by key or task text)."""

    day_key = day or _today_iso()
    state = load_state(path)
    day_tasks: dict[str, Any] = (state.get("tasks") or {}).get(day_key) or {}

    raw = str(task_id_or_name or "").strip()
    key = _normalize_key(raw)

    # Try direct key first.
    if key in day_tasks:
        day_tasks.pop(key, None)
        save_state(state, path)
        return state

    # Otherwise, try matching by stored display name.
    for k, obj in list(day_tasks.items()):
        if _normalize_key(str((obj or {}).get("task") or "")) == key:
            day_tasks.pop(k, None)
            save_state(state, path)
            return state

    return state


def set_day_task_completed(
    task_name: str,
    *,
    completed: bool,
    day: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
) -> dict[str, Any]:
    """Mark a task complete/incomplete for the day."""

    if completed:
        return mark_task_complete(task_name, day=day, path=path)

    day_key = day or _today_iso()
    state = load_state(path)
    day_tasks: dict[str, Any] = (state.get("tasks") or {}).setdefault(day_key, {})
    raw = str(task_name or "").strip()
    key = _normalize_key(raw)

    obj = day_tasks.get(key)
    if not isinstance(obj, dict):
        # Nothing to un-complete; create a stub.
        day_tasks[key] = {
            "task": raw or key,
            "type": "flexible",
            "start": None,
            "end": None,
            "completed": False,
            "completed_at": None,
        }
    else:
        obj["completed"] = False
        obj["completed_at"] = None

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


def apply_tired_postpone_rule(
    emotion_state: dict[str, Any],
    *,
    path: str = DEFAULT_TRACKER_PATH,
    mode: str = "anytime_sustained",
    night_start_hour_local: int = 21,
    night_end_hour_local: int = 6,
    tasks_to_move: int = 2,
    trigger_states: tuple[str, ...] = ("tired", "stressed", "frustrated"),
    min_trigger_score: float = 35.0,
    sustained_samples: int = 3,
    sustained_within_minutes: int = 25,
    cooldown_minutes: int = 45,
) -> str | None:
    """Night-time rest automation based on webcam vibe.

        Rule (minimal + deterministic):
        - mode='night': trigger only in the night window (default 21:00–06:00)
        - mode='anytime_sustained': trigger anytime, but only if the signal stays
            high for N consecutive samples (default 3) within a time window.

        Trigger condition:
        - vibe indicates one of trigger_states (tired/stressed/frustrated)
            via dominant/last state OR a score >= min_trigger_score
    - Then move up to `tasks_to_move` last incomplete tasks from today's plan
      to tomorrow and return a gentle message.

    Cooldown:
    - Uses `state['meta']['rest_rule_last_trigger_utc']` to avoid repeating.

    Returns a short message when a change was applied, else None.
    """

    mode_norm = str(mode or "").strip().lower()
    if mode_norm not in {"night", "anytime_sustained"}:
        mode_norm = "anytime_sustained"

    if mode_norm == "night":
        try:
            hour = datetime.now().hour
        except Exception:
            return None

        start_h = int(night_start_hour_local)
        end_h = int(night_end_hour_local)
        if start_h == end_h:
            # Degenerate window: treat as disabled.
            return None

        # Night window can span midnight.
        in_night = hour >= start_h or hour < end_h if start_h > end_h else start_h <= hour < end_h
        if not in_night:
            return None

    vibe = (emotion_state or {})
    vibe_state = str(vibe.get("window_dominant_state") or vibe.get("last_state") or "").strip().lower()

    last_scores = vibe.get("last_scores")
    scores: dict[str, float] = {}
    if isinstance(last_scores, dict):
        for k, v in last_scores.items():
            try:
                scores[str(k).strip().lower()] = float(v)
            except Exception:
                continue

    triggers = tuple(s.strip().lower() for s in (trigger_states or ()))
    if not triggers:
        return None

    def is_hit(state_name: str, score_map: dict[str, float]) -> bool:
        sname = str(state_name or "").strip().lower()
        if sname in triggers:
            return True
        return any(score_map.get(s, 0.0) >= float(min_trigger_score) for s in triggers)

    # Option C: require sustained signal over N consecutive recent samples.
    if mode_norm == "anytime_sustained":
        recent = vibe.get("recent_samples")
        if not isinstance(recent, list):
            return None

        n = max(1, int(sustained_samples))
        if len(recent) < n:
            return None

        # Take the last N samples and ensure each is a hit.
        last_n = recent[-n:]

        # Also ensure they're recent enough (within sustained_within_minutes).
        try:
            newest_ts = str(last_n[-1].get("timestamp_utc") or "")
            newest_dt = datetime.fromisoformat(newest_ts.replace("Z", "+00:00"))
        except Exception:
            return None

        for obj in last_n:
            if not isinstance(obj, dict):
                return None
            try:
                ts = str(obj.get("timestamp_utc") or "")
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                return None

            if newest_dt - dt > timedelta(minutes=int(sustained_within_minutes)):
                return None

            sname = str(obj.get("dominant_state") or "")
            smap: dict[str, float] = {}
            raw_scores = obj.get("scores")
            if isinstance(raw_scores, dict):
                for k, v in raw_scores.items():
                    try:
                        smap[str(k).strip().lower()] = float(v)
                    except Exception:
                        continue

            if not is_hit(sname, smap):
                return None
    else:
        # Night mode: single-sample trigger is OK.
        if not is_hit(vibe_state, scores):
            return None

    state = load_state(path)

    # Cooldown to avoid spam.
    meta: dict[str, Any] = state.setdefault("meta", {})
    last_trigger = meta.get("rest_rule_last_trigger_utc")
    if isinstance(last_trigger, str) and last_trigger:
        try:
            last_dt = datetime.fromisoformat(last_trigger.replace("Z", "+00:00"))
            now_dt = datetime.utcnow().replace(tzinfo=last_dt.tzinfo)
            if now_dt - last_dt < timedelta(minutes=int(cooldown_minutes)):
                return None
        except Exception:
            pass

    today_key = _today_iso()
    tomorrow_key = (date.today() + timedelta(days=1)).isoformat()

    plans: dict[str, Any] = state.setdefault("plans", {})
    plan_today = plans.get(today_key)
    if not isinstance(plan_today, dict):
        # No schedule yet, but still encourage rest.
        meta["rest_rule_last_trigger_utc"] = datetime.utcnow().isoformat() + "Z"
        save_state(state, path)
        return "You look like you need a break. Want to pause for a few minutes and breathe?"

    blocks_today = [b for b in (plan_today.get("blocks") or []) if isinstance(b, dict)]
    if not blocks_today:
        meta["rest_rule_last_trigger_utc"] = datetime.utcnow().isoformat() + "Z"
        save_state(state, path)
        return "You look like you need a break. Want to pause for a few minutes and breathe?"

    tasks_by_day: dict[str, Any] = state.setdefault("tasks", {})
    day_tasks: dict[str, Any] = tasks_by_day.setdefault(today_key, {})

    # Collect last incomplete tasks by walking blocks backwards.
    to_move_keys: list[str] = []
    seen: set[str] = set()
    for b in reversed(blocks_today):
        task_name = str(b.get("task") or "").strip()
        if not task_name:
            continue
        key = _normalize_key(task_name)
        if not key or key in seen:
            continue
        seen.add(key)

        completed = False
        if key in day_tasks:
            completed = bool(day_tasks[key].get("completed"))

        if not completed:
            to_move_keys.append(key)
            if len(to_move_keys) >= int(tasks_to_move):
                break

    if not to_move_keys:
        meta["rest_rule_last_trigger_utc"] = datetime.utcnow().isoformat() + "Z"
        save_state(state, path)
        return "You look like you need a break. Want to pause for a few minutes and breathe?"

    # Remove from today's plan blocks and day tasks.
    def keep_block(b: dict[str, Any]) -> bool:
        name = str(b.get("task") or "").strip()
        if not name:
            return True
        return _normalize_key(name) not in set(to_move_keys)

    new_blocks_today = [b for b in blocks_today if keep_block(b)]
    plan_today["blocks"] = new_blocks_today

    # Also remove from today's task definitions list (plan source-of-truth).
    today_task_defs = [t for t in (plan_today.get("tasks") or []) if isinstance(t, dict)]
    plan_today["tasks"] = [
        t for t in today_task_defs if _normalize_key(str(t.get("task") or "")) not in set(to_move_keys)
    ]

    # Remove the tasks from today's tracked tasks (lower remaining count).
    for k in to_move_keys:
        try:
            day_tasks.pop(k, None)
        except Exception:
            pass

    # Add to tomorrow plan as flexible tasks and re-optimize tomorrow.
    plan_tomorrow = plans.get(tomorrow_key)
    if not isinstance(plan_tomorrow, dict):
        plan_tomorrow = {"input": "", "tasks": [], "blocks": [], "updated_at": datetime.utcnow().isoformat() + "Z"}
        plans[tomorrow_key] = plan_tomorrow

    tomorrow_tasks = [t for t in (plan_tomorrow.get("tasks") or []) if isinstance(t, dict)]
    existing_keys = {_normalize_key(str(t.get("task") or "")) for t in tomorrow_tasks}

    # Pull the original task definitions from the pre-filtered snapshot when available.
    today_tasks_def = [t for t in today_task_defs if isinstance(t, dict)]
    by_key: dict[str, dict[str, Any]] = {}
    for t in today_tasks_def:
        k = _normalize_key(str(t.get("task") or ""))
        if k:
            by_key[k] = dict(t)

    moved = 0
    for k in to_move_keys:
        if k in existing_keys:
            continue
        tdef = dict(by_key.get(k) or {})
        task_name = str(tdef.get("task") or "").strip()
        if not task_name:
            # fallback to key as readable task
            task_name = k
        # Make it flexible so optimizer places it in morning work windows.
        tdef["task"] = task_name
        tdef["type"] = "flexible"
        tdef.pop("time", None)
        tdef.pop("start", None)
        tdef.pop("end", None)
        # If deadline was today, bump to tomorrow.
        if str(tdef.get("deadline") or "").strip().lower() == "today":
            tdef["deadline"] = "tomorrow"
        tomorrow_tasks.append(tdef)
        existing_keys.add(k)
        moved += 1

    existing_blocks_tomorrow = [b for b in (plan_tomorrow.get("blocks") or []) if isinstance(b, dict)]
    if moved <= 0 and existing_blocks_tomorrow:
        # Nothing new to add; keep existing schedule for tomorrow.
        new_blocks_tomorrow = existing_blocks_tomorrow
    else:
        new_blocks_tomorrow = optimize_day(tomorrow_tasks)
        plan_tomorrow["tasks"] = tomorrow_tasks
        plan_tomorrow["blocks"] = new_blocks_tomorrow
        plan_tomorrow["updated_at"] = datetime.utcnow().isoformat() + "Z"

    # Update tomorrow's day task tracker entries in the SAME state object.
    tomorrow_day_tasks: dict[str, Any] = tasks_by_day.setdefault(tomorrow_key, {})
    for b in new_blocks_tomorrow:
        task = str(b.get("task") or "").strip()
        if not task:
            continue
        task_key = _normalize_key(task)
        if not task_key:
            continue
        tomorrow_day_tasks.setdefault(
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

    meta["rest_rule_last_trigger_utc"] = datetime.utcnow().isoformat() + "Z"
    save_state(state, path)
    if moved > 0:
        # Gentle, requested phrasing.
        return (
            f"You look {vibe_state or 'a bit overwhelmed'}. "
            f"Let’s take a short rest — I moved the last {moved} task(s) to tomorrow morning."
        )
    return None
