"""Simple day optimizer.

Takes structured tasks (from `scheduler.task_parser`) and produces an ordered plan
for the day:

- Fixed tasks are locked to their times.
- Flexible tasks are inserted into gaps between fixed tasks.
- Flexible tasks are prioritized by (deadline first) and then (effort aligned to
  time-of-day energy).

This is intentionally heuristic and lightweight; it can be swapped later for a
more advanced solver.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _minutes_to_hhmm(minutes: int) -> str:
    minutes = max(0, minutes)
    h = (minutes // 60) % 24
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def _parse_duration_to_minutes(duration: str | None) -> int | None:
    if not duration:
        return None
    s = str(duration).strip().lower()

    # Formats supported: "2h", "45m", "1h30m".
    m = re.fullmatch(r"(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?", s)
    if not m:
        return None
    hours = int(m.group("h") or 0)
    mins = int(m.group("m") or 0)
    total = hours * 60 + mins
    return total if total > 0 else None


def _deadline_rank(deadline: str | None) -> int:
    if not deadline:
        return 10_000
    d = str(deadline).strip().lower()
    if d == "today":
        return 0
    if d == "tomorrow":
        return 1
    weekdays = {
        "monday": 2,
        "tuesday": 3,
        "wednesday": 4,
        "thursday": 5,
        "friday": 6,
        "saturday": 7,
        "sunday": 8,
    }
    return weekdays.get(d, 9)


def _effort_bucket(task: dict[str, Any]) -> str:
    name = str(task.get("task", "")).lower()
    dur = _parse_duration_to_minutes(task.get("duration")) or 60

    heavy_keywords = ("study", "assignment", "project", "report", "write", "coding", "chapter", "chapters")
    light_keywords = ("email", "call", "messages", "clean", "laundry", "errand", "groceries")

    if any(k in name for k in heavy_keywords) or dur >= 120:
        return "heavy"
    if any(k in name for k in light_keywords) or dur <= 45:
        return "light"
    return "medium"


def _energy_for_time(mins: int) -> str:
    # Simple day energy curve.
    if 8 * 60 <= mins < 12 * 60:
        return "high"
    if 12 * 60 <= mins < 17 * 60:
        return "medium"
    return "low"


def _preferred_effort(energy: str) -> str:
    if energy == "high":
        return "heavy"
    if energy == "medium":
        return "medium"
    return "light"


@dataclass(frozen=True)
class OptimizerConfig:
    day_start: str = "06:00"
    day_end: str = "22:00"
    default_flexible_minutes: int = 60


def optimize_day(
    tasks: list[dict[str, Any]],
    *,
    config: OptimizerConfig | None = None,
) -> list[dict[str, Any]]:
    """Return an optimized schedule as a list of blocks.

    Each returned block has:
    - task: str
    - type: "fixed" | "flexible"
    - start: "HH:MM"
    - end: "HH:MM"

    Notes:
    - Fixed tasks are placed first.
    - Flexible tasks are scheduled into remaining free intervals.
    - Tasks may be split across multiple gaps if needed.
    """

    cfg = config or OptimizerConfig()
    day_start_m = _hhmm_to_minutes(cfg.day_start)
    day_end_m = _hhmm_to_minutes(cfg.day_end)

    fixed: list[dict[str, Any]] = []
    flexible: list[dict[str, Any]] = []

    for t in tasks:
        if t.get("type") == "fixed" and t.get("time"):
            fixed.append(dict(t))
        else:
            flexible.append(dict(t))

    # Build fixed blocks.
    fixed_blocks: list[tuple[int, int, dict[str, Any]]] = []
    for t in fixed:
        start = _hhmm_to_minutes(str(t["time"]))
        dur = _parse_duration_to_minutes(t.get("duration")) or 60
        end = start + dur
        fixed_blocks.append((start, end, t))

    fixed_blocks.sort(key=lambda x: x[0])

    # Clip fixed blocks to the day window and drop those outside.
    clipped_fixed: list[tuple[int, int, dict[str, Any]]] = []
    for start, end, t in fixed_blocks:
        if end <= day_start_m or start >= day_end_m:
            continue
        clipped_fixed.append((max(day_start_m, start), min(day_end_m, end), t))

    # Compute free intervals between fixed blocks.
    free: list[tuple[int, int]] = []
    cursor = day_start_m
    for start, end, _ in clipped_fixed:
        if start > cursor:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < day_end_m:
        free.append((cursor, day_end_m))

    # Prefer scheduling flexible tasks in the "middle" of the day first:
    # gaps between fixed blocks, then pre-gap (before first fixed), then post-gap.
    if len(clipped_fixed) >= 1:
        first_fixed_start = clipped_fixed[0][0]
        last_fixed_end = clipped_fixed[-1][1]

        def free_rank(interval: tuple[int, int]) -> tuple[int, int]:
            start, _end = interval
            if start >= first_fixed_start and start < last_fixed_end:
                return (0, start)  # between fixed blocks
            if start < first_fixed_start:
                return (1, start)  # before first fixed
            return (2, start)  # after last fixed

        free.sort(key=free_rank)

    # Sort flexible by deadline first, then longer (so big tasks get a chance).
    flexible.sort(
        key=lambda t: (
            _deadline_rank(t.get("deadline")),
            -(_parse_duration_to_minutes(t.get("duration")) or cfg.default_flexible_minutes),
        )
    )

    # Greedy fill.
    plan_blocks: list[tuple[int, int, dict[str, Any]]] = []

    def pick_task(current_time: int) -> int | None:
        if not flexible:
            return None
        energy = _energy_for_time(current_time)
        preferred = _preferred_effort(energy)

        # 1) Try to match preferred effort bucket.
        for idx, t in enumerate(flexible):
            if _effort_bucket(t) == preferred:
                return idx
        # 2) Otherwise earliest deadline.
        return 0

    for gap_start, gap_end in free:
        t_cursor = gap_start
        while t_cursor < gap_end and flexible:
            idx = pick_task(t_cursor)
            if idx is None:
                break
            task = flexible[idx]
            remaining = gap_end - t_cursor
            dur = _parse_duration_to_minutes(task.get("duration")) or cfg.default_flexible_minutes
            block_minutes = min(dur, remaining)

            planned_task = dict(task)
            planned_task["type"] = "flexible"
            plan_blocks.append((t_cursor, t_cursor + block_minutes, planned_task))

            # Reduce or remove task.
            if block_minutes >= dur:
                flexible.pop(idx)
            else:
                # Keep the remainder as a shorter task with updated duration.
                flexible[idx] = dict(task)
                flexible[idx]["duration"] = _minutes_to_duration_str(dur - block_minutes)

            t_cursor += block_minutes

    # Compose final schedule: fixed blocks + planned flexible blocks.
    all_blocks: list[tuple[int, int, dict[str, Any]]] = []
    for start, end, t in clipped_fixed:
        all_blocks.append((start, end, {"task": t.get("task"), "type": "fixed"}))
    all_blocks.extend(plan_blocks)

    all_blocks.sort(key=lambda x: x[0])

    return [
        {
            "task": str(t.get("task", "")).strip(),
            "type": str(t.get("type", "flexible")),
            "start": _minutes_to_hhmm(start),
            "end": _minutes_to_hhmm(end),
        }
        for start, end, t in all_blocks
        if str(t.get("task", "")).strip()
    ]


def _minutes_to_duration_str(minutes: int) -> str:
    if minutes <= 0:
        return "0m"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h{mins}m"
