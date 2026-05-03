"""Schedule presentation layer.

Converts optimized schedule blocks into a clean, human-readable timeline.

Input: list of blocks like:
  {"task": "Gym", "type": "fixed", "start": "18:00", "end": "19:00"}

Output (string):
  09:00 – 11:00  Class
  18:00 – 19:00  Gym

Primary entry point: `format_timeline(blocks)`.
"""

from __future__ import annotations

from typing import Any


def _label_for_task(task_name: str) -> str | None:
    name = (task_name or "").strip().lower()
    if not name:
        return None

    if any(k in name for k in ("gym", "workout", "run", "walk", "exercise", "lift")):
        return "health"
    if any(k in name for k in ("study", "chapter", "chapters", "homework", "assignment", "exam", "class", "lecture")):
        return "study"
    if any(k in name for k in ("email", "call", "meeting", "admin", "paperwork")):
        return "admin"

    return None


def _sort_key(block: dict[str, Any]) -> tuple[int, int]:
    start = str(block.get("start") or "00:00")
    end = str(block.get("end") or "00:00")

    def to_minutes(hhmm: str) -> int:
        try:
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return 0

    return (to_minutes(start), to_minutes(end))


def format_timeline(
    blocks: list[dict[str, Any]],
    *,
    include_labels: bool = False,
) -> str:
    """Format schedule blocks as a readable timeline string."""

    if not blocks:
        return "(no tasks scheduled)"

    normalized: list[dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        task = str(b.get("task") or "").strip()
        start = str(b.get("start") or "").strip()
        end = str(b.get("end") or "").strip()
        if not task or not start or not end:
            continue
        normalized.append({"task": task, "start": start, "end": end, "type": b.get("type")})

    normalized.sort(key=_sort_key)

    time_col_width = 13  # len("09:00 – 11:00")
    lines: list[str] = []
    for b in normalized:
        time_part = f"{b['start']} – {b['end']}"
        task_part = b["task"]
        if include_labels:
            label = _label_for_task(task_part)
            if label:
                task_part = f"[{label}] {task_part}"

        # Two spaces between columns for readability.
        lines.append(f"{time_part:<{time_col_width}}  {task_part}")

    return "\n".join(lines)


def format_timeline_lines(blocks: list[dict[str, Any]], *, include_labels: bool = False) -> list[str]:
    """Same as `format_timeline` but returns a list of lines."""

    return format_timeline(blocks, include_labels=include_labels).splitlines()
