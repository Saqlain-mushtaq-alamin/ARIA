"""ARIA assistant entry point."""

import time

from core.agent import process_text, run_agent
from voice.stt import listen_and_transcribe
from voice.tts import speak
from voice.wake_word import start_wake_word_listener


def _handle_wake_word() -> None:
    text = listen_and_transcribe()
    if not text:
        print("No speech detected.")
        return
    response = process_text(text)
    print(response)
    try:
        speak(response)
    except Exception as exc:
        print(f"TTS failed: {exc}")


def main() -> None:
    start_wake_word_listener(_handle_wake_word)
    while True:
        time.sleep(0.5)


if __name__ == "__main__":
    main()
