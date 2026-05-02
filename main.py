"""ARIA assistant entry point."""

import os
import sys
import threading
import time

from core.agent import process_text
from voice.stt import listen_and_transcribe
from voice.tts import speak
from voice.wake_word import start_wake_word_listener


def _ensure_cache_dirs() -> None:
    for key in ("HF_HOME", "TRANSFORMERS_CACHE", "TORCH_HOME", "PIP_CACHE_DIR"):
        path = os.getenv(key)
        if not path:
            continue
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    load_dotenv()
    load_dotenv(os.path.join("config", ".env"))


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


def _text_input_loop() -> None:
    print("Text command ready. Type a command and press Enter.")
    while True:
        line = sys.stdin.readline()
        if not line:
            time.sleep(0.1)
            continue
        text = line.strip()
        if not text:
            continue
        response = process_text(text)
        print(response)
        try:
            speak(response)
        except Exception as exc:
            print(f"TTS failed: {exc}")


def main() -> None:
    _load_env()
    _ensure_cache_dirs()
    start_wake_word_listener(_handle_wake_word)
    threading.Thread(target=_text_input_loop, daemon=True).start()
    while True:
        time.sleep(0.5)


if __name__ == "__main__":
    main()
