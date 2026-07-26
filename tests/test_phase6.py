"""
tests/test_phase6.py  ─  Phase 6: Scheduler & Goal Decomposer
==============================================================
Tests create_schedule, show_schedule, whats_next, and goal decomposition.
"""
import tests.mock_layer  # MUST be first

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_runner import test, run_all


# ── Scheduler ─────────────────────────────────────────────────────────────────

@test("create_schedule_from_text creates a plan", "PHASE6", "scheduler")
def t_create_schedule():
    from scheduler.tracker import create_schedule_from_text
    result = create_schedule_from_text(
        "9am study chemistry, 11am exercise, 1pm lunch, 3pm read a book"
    )
    assert isinstance(result, str)
    assert len(result) > 5


@test("show_schedule returns plan or empty message", "PHASE6", "scheduler")
def t_show_schedule():
    from scheduler.tracker import show_schedule
    result = show_schedule()
    assert isinstance(result, str)


@test("whats_next returns next task or empty", "PHASE6", "scheduler")
def t_whats_next():
    from scheduler.tracker import whats_next
    result = whats_next()
    assert isinstance(result, str)


@test("has_plan returns bool", "PHASE6", "scheduler")
def t_has_plan():
    from scheduler.tracker import has_plan
    result = has_plan()
    assert isinstance(result, bool)


# ── Goal decomposer ───────────────────────────────────────────────────────────

@test("decompose_goal returns step list for 'learn Python'", "PHASE6", "goal")
def t_decompose_goal():
    from scheduler.goal_decomposer import decompose_goal
    result = decompose_goal(goal="learn Python", deadline_str="30 days")
    assert isinstance(result, str)
    assert len(result) > 10


@test("decompose_goal handles no deadline", "PHASE6", "goal")
def t_decompose_no_deadline():
    from scheduler.goal_decomposer import decompose_goal
    result = decompose_goal(goal="get fit")
    assert isinstance(result, str)


if __name__ == "__main__":
    run_all("PHASE6")
