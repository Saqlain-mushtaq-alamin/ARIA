import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

from scheduler import tracker


class TestTiredPostponeRule(unittest.TestCase):
    def _make_emotion_state(self, *, state: str = "tired") -> dict:
        now = datetime.now(timezone.utc)
        recent = []
        for i in range(3):
            ts = (now - timedelta(minutes=2 - i)).isoformat().replace("+00:00", "Z")
            recent.append(
                {
                    "timestamp_utc": ts,
                    "dominant_state": state,
                    "scores": {state: 50.0},
                }
            )
        return {
            "window_dominant_state": state,
            "last_state": state,
            "last_scores": {state: 50.0},
            "recent_samples": recent,
        }

    def test_moves_last_two_incomplete_tasks_to_tomorrow(self) -> None:
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()

        tasks = ["Task A", "Task B", "Task C"]
        blocks = [
            {"task": tasks[0], "type": "flexible", "start": "09:00", "end": "10:00"},
            {"task": tasks[1], "type": "flexible", "start": "10:00", "end": "11:00"},
            {"task": tasks[2], "type": "flexible", "start": "11:00", "end": "12:00"},
        ]
        task_defs = [{"task": t, "type": "flexible", "duration": "60m"} for t in tasks]

        day_tasks = {
            tracker._normalize_key(tasks[0]): {
                "task": tasks[0],
                "type": "flexible",
                "start": "09:00",
                "end": "10:00",
                "completed": False,
                "completed_at": None,
            },
            tracker._normalize_key(tasks[1]): {
                "task": tasks[1],
                "type": "flexible",
                "start": "10:00",
                "end": "11:00",
                "completed": False,
                "completed_at": None,
            },
            tracker._normalize_key(tasks[2]): {
                "task": tasks[2],
                "type": "flexible",
                "start": "11:00",
                "end": "12:00",
                "completed": False,
                "completed_at": None,
            },
        }

        state = {
            "tasks": {today: day_tasks},
            "habits": {},
            "meta": {},
            "plans": {
                today: {
                    "input": "",
                    "tasks": task_defs,
                    "blocks": blocks,
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                }
            },
        }

        with tempfile.TemporaryDirectory() as td:
            path = td + "/task_tracker.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f)

            msg = tracker.apply_tired_postpone_rule(
                self._make_emotion_state(state="tired"),
                path=path,
                mode="anytime_sustained",
                tasks_to_move=2,
                sustained_samples=3,
                sustained_within_minutes=60,
                cooldown_minutes=0,
            )
            self.assertIsNotNone(msg)
            self.assertIn("moved the last 2", str(msg).lower())

            new_state = tracker.load_state(path)
            plan_today = new_state.get("plans", {}).get(today)
            self.assertIsInstance(plan_today, dict)

            # Today should keep only the first block/task.
            new_blocks_today = list(plan_today.get("blocks") or [])
            self.assertEqual([b.get("task") for b in new_blocks_today], [tasks[0]])

            # Tomorrow should exist and include moved tasks somewhere.
            plan_tomorrow = new_state.get("plans", {}).get(tomorrow)
            self.assertIsInstance(plan_tomorrow, dict)
            tomorrow_blocks = list(plan_tomorrow.get("blocks") or [])
            tomorrow_tasks = {str(b.get("task") or "") for b in tomorrow_blocks}
            self.assertIn(tasks[1], tomorrow_tasks)
            self.assertIn(tasks[2], tomorrow_tasks)

    def test_cooldown_prevents_repeat_trigger(self) -> None:
        today = date.today().isoformat()
        tasks = ["Task A", "Task B"]
        blocks = [
            {"task": tasks[0], "type": "flexible", "start": "09:00", "end": "10:00"},
            {"task": tasks[1], "type": "flexible", "start": "10:00", "end": "11:00"},
        ]
        task_defs = [{"task": t, "type": "flexible", "duration": "60m"} for t in tasks]
        day_tasks = {
            tracker._normalize_key(tasks[0]): {"task": tasks[0], "completed": False},
            tracker._normalize_key(tasks[1]): {"task": tasks[1], "completed": False},
        }
        state = {
            "tasks": {today: day_tasks},
            "habits": {},
            "meta": {},
            "plans": {today: {"input": "", "tasks": task_defs, "blocks": blocks, "updated_at": ""}},
        }

        with tempfile.TemporaryDirectory() as td:
            path = td + "/task_tracker.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f)

            emotion_state = self._make_emotion_state(state="tired")
            first = tracker.apply_tired_postpone_rule(
                emotion_state,
                path=path,
                mode="anytime_sustained",
                tasks_to_move=1,
                sustained_samples=3,
                sustained_within_minutes=60,
                cooldown_minutes=999,
            )
            self.assertIsNotNone(first)

            second = tracker.apply_tired_postpone_rule(
                emotion_state,
                path=path,
                mode="anytime_sustained",
                tasks_to_move=1,
                sustained_samples=3,
                sustained_within_minutes=60,
                cooldown_minutes=999,
            )
            self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
