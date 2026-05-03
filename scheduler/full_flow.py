"""End-to-end scheduler flow.

Given a single natural-language input string:
1) Parse into structured tasks
2) Optimize into time blocks
3) Format into a readable timeline

This is useful for integration testing and future wiring into the agent/router.

Run:
  python -m scheduler.full_flow "I have class 9am, gym 6pm, need to finish 3 chemistry chapters and 1 coding assignment"
"""

from __future__ import annotations

import json
import sys
from typing import Any

from scheduler.optimizer import optimize_day
from scheduler.routine_generator import format_timeline
from scheduler.task_parser import parse_tasks


def plan_day_from_text(
    text: str,
    *,
    include_labels: bool = True,
) -> dict[str, Any]:
    tasks = parse_tasks(text)
    blocks = optimize_day(tasks)
    timeline = format_timeline(blocks, include_labels=include_labels)
    return {"input": text, "tasks": tasks, "blocks": blocks, "timeline": timeline}


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if not args:
        print("Usage: python -m scheduler.full_flow <text>")
        return 2

    text = " ".join(args).strip()
    result = plan_day_from_text(text)

    print("=== Parsed tasks ===")
    print(json.dumps(result["tasks"], ensure_ascii=False, indent=2))
    print("\n=== Optimized blocks ===")
    print(json.dumps(result["blocks"], ensure_ascii=False, indent=2))
    print("\n=== Timeline ===")
    print(result["timeline"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
