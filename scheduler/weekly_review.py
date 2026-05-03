"""Weekly review: turn tracking data into coaching insights.

- Reads tracker state (tasks + habits).
- Computes completion rates and patterns for the last 7 days.
- Optionally uses the project's LLM generator to produce supportive feedback.

Primary entry point: `generate_weekly_review()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from scheduler.tracker import DEFAULT_TRACKER_PATH, _normalize_key, habit_streak, load_state

try:
    from modules.content_generator import generate_text
except Exception:  # pragma: no cover
    generate_text = None


@dataclass(frozen=True)
class WeeklyReview:
    week_end: str
    planned_tasks: int
    completed_tasks: int
    completion_rate: float
    habit_summary: dict[str, Any]
    insights: list[str]
    coach_message: str


def _date_range(end_day: date, days: int = 7) -> list[date]:
    start = end_day - timedelta(days=days - 1)
    return [start + timedelta(days=i) for i in range(days)]


def _safe_rate(completed: int, planned: int) -> float:
    if planned <= 0:
        return 0.0
    return completed / planned


def _summarize_habits(state: dict[str, Any], end_iso: str) -> dict[str, Any]:
    habits: dict[str, Any] = state.get("habits", {}) or {}
    summary: dict[str, Any] = {}
    for key, entry in habits.items():
        name = str(entry.get("name") or key)
        dates = list(entry.get("dates") or [])
        summary[name] = {
            "count": len(dates),
            "streak": habit_streak(name, as_of=end_iso),
        }
    return summary


def generate_weekly_review(
    *,
    end_day: str | None = None,
    path: str = DEFAULT_TRACKER_PATH,
    use_llm: bool = True,
) -> WeeklyReview:
    """Generate a weekly review ending on `end_day` (defaults to today).

    Intended to be run on Sundays, but it works for any end date.
    """

    end_dt = date.fromisoformat(end_day) if end_day else date.today()
    end_iso = end_dt.isoformat()
    days = _date_range(end_dt, days=7)

    state = load_state(path)
    tasks_by_day: dict[str, Any] = state.get("tasks", {}) or {}

    planned_total = 0
    completed_total = 0
    missed_tasks: dict[str, int] = {}

    for d in days:
        day_key = d.isoformat()
        day_tasks: dict[str, Any] = tasks_by_day.get(day_key) or {}
        planned_total += len(day_tasks)
        for obj in day_tasks.values():
            if bool(obj.get("completed")):
                completed_total += 1
            else:
                task_name = str(obj.get("task") or "").strip()
                if task_name:
                    missed_tasks[task_name] = missed_tasks.get(task_name, 0) + 1

    completion_rate = _safe_rate(completed_total, planned_total)

    # Simple insights.
    insights: list[str] = []
    if planned_total == 0:
        insights.append("No tracked tasks this week. Try initializing a daily plan to start tracking.")
    else:
        pct = int(round(completion_rate * 100))
        insights.append(f"You completed {completed_total}/{planned_total} tasks ({pct}%).")

        if completion_rate >= 0.8:
            insights.append("Strong consistency overall—keep the structure.")
        elif completion_rate >= 0.5:
            insights.append("Decent progress—tighten the plan by reducing overload on busy days.")
        else:
            insights.append("Low completion rate—try fewer tasks and schedule key work earlier.")

        if missed_tasks:
            top_missed = sorted(missed_tasks.items(), key=lambda x: x[1], reverse=True)[:3]
            missed_str = ", ".join(f"{name} ({count}x)" for name, count in top_missed)
            insights.append(f"Most missed: {missed_str}.")

    habit_summary = _summarize_habits(state, end_iso)

    coach_message = ""
    if use_llm and generate_text is not None:
        # Compact prompt for supportive, human tone.
        habit_lines = []
        for name, info in habit_summary.items():
            habit_lines.append(f"- {name}: streak {info.get('streak')}, total {info.get('count')}")
        habit_block = "\n".join(habit_lines) if habit_lines else "(no habits recorded)"

        prompt = (
            "You are a supportive weekly productivity coach. "
            "Write a short, human, non-robotic weekly review. "
            "Be kind but direct. Keep it to 4-6 sentences.\n\n"
            f"Week ending: {end_iso}\n"
            f"Tasks completed: {completed_total}/{planned_total} ({int(round(completion_rate*100))}%)\n"
            f"Habits:\n{habit_block}\n\n"
            "Give: (1) one praise, (2) one pattern you notice, (3) one concrete suggestion for next week."
        )

        try:
            coach_message = generate_text(prompt)
        except Exception:
            coach_message = ""

    if not coach_message:
        # Deterministic fallback.
        if planned_total == 0:
            coach_message = "This week had no tracked plan. Next week, start with 2–4 clear daily tasks and record completions to build momentum."
        else:
            coach_message = (
                "You made progress this week. "
                "Keep fixed commitments steady, and schedule your most important work earlier in the day. "
                "Aim to plan slightly less than you think you can do, then finish strong."
            )

    return WeeklyReview(
        week_end=end_iso,
        planned_tasks=planned_total,
        completed_tasks=completed_total,
        completion_rate=completion_rate,
        habit_summary=habit_summary,
        insights=insights,
        coach_message=coach_message,
    )


def format_weekly_review(review: WeeklyReview) -> str:
    """Render a weekly review to readable text."""

    lines: list[str] = []
    lines.append(f"Weekly review (ending {review.week_end})")
    lines.append("")
    for insight in review.insights:
        lines.append(f"- {insight}")

    if review.habit_summary:
        lines.append("")
        lines.append("Habits:")
        for name, info in sorted(review.habit_summary.items(), key=lambda x: _normalize_key(x[0])):
            lines.append(f"- {name}: streak {info.get('streak')}, total {info.get('count')}")

    lines.append("")
    lines.append(review.coach_message.strip())

    return "\n".join(lines).strip()
