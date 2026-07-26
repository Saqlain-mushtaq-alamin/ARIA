import unittest

from core.command_parser import _parse_multistep_command


class TestMultistepParser(unittest.TestCase):
    def test_open_type_save_desktop(self) -> None:
        payload = _parse_multistep_command(
            "open notepad and type hello world then save it on desktop"
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload.get("intent"), "multi_step")
        steps = payload.get("steps") or []
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0]["intent"], "open_app")
        self.assertEqual(steps[0]["parameters"]["app_name"].lower(), "notepad")
        self.assertEqual(steps[1]["intent"], "type_text")
        self.assertEqual(steps[1]["parameters"]["text"], "hello world")
        self.assertEqual(steps[2]["intent"], "save_file")
        self.assertEqual(steps[2]["parameters"]["path"].lower(), "desktop\\note.txt")
        self.assertEqual(steps[2]["parameters"]["content"], "hello world")

    def test_open_write_save_as_on_desktop(self) -> None:
        payload = _parse_multistep_command(
            "open notepad and write a story about a lazy cat then save as cat.txt on desktop"
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload.get("intent"), "multi_step")
        steps = payload.get("steps") or []
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[1]["intent"], "type_text")
        self.assertTrue(steps[1]["parameters"].get("generate"))
        self.assertEqual(steps[2]["intent"], "save_file")
        self.assertEqual(steps[2]["parameters"]["path"].lower(), "desktop\\cat.txt")

    def test_save_filename_no_location(self) -> None:
        payload = _parse_multistep_command(
            "open notepad and type hello then save hello.txt"
        )
        self.assertIsNotNone(payload)
        steps = payload.get("steps") or []
        self.assertEqual(steps[2]["parameters"]["path"].lower(), "hello.txt")


if __name__ == "__main__":
    unittest.main()
