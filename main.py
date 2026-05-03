"""ARIA assistant entry point."""

import os
import sys
import threading
import time

from core.agent import process_text
from memory.conversation_log import log_interaction
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


def _handle_wake_word(device_index: int) -> None:
    # After the wake word triggers, keep listening for a short period so the
    # user doesn't need to say the wake word before every follow-up command.
    session_seconds = float(os.getenv("VOICE_SESSION_SECONDS", "25"))
    max_empty = int(os.getenv("VOICE_SESSION_MAX_EMPTY", "2"))

    if session_seconds <= 0:
        text = listen_and_transcribe(input_device_index=device_index)
        if not text:
            return
        if os.getenv("VOICE_DEBUG") == "1":
            print(f"Heard: {text}")
        response = process_text(text)
        print(response)
        try:
            log_interaction(text, response, metadata={"source": "voice"})
        except Exception as exc:
            print(f"Log failed: {exc}")
        try:
            speak(response)
        except Exception as exc:
            print(f"TTS failed: {exc}")
        return

    session_deadline = time.time() + max(0.0, session_seconds)
    empty_count = 0

    while True:
        if time.time() > session_deadline:
            return

        text = listen_and_transcribe(input_device_index=device_index)
        if not text:
            empty_count += 1
            if empty_count >= max_empty:
                return
            continue

        empty_count = 0
        if os.getenv("VOICE_DEBUG") == "1":
            print(f"Heard: {text}")

        response = process_text(text)
        print(response)
        try:
            log_interaction(text, response, metadata={"source": "voice"})
        except Exception as exc:
            print(f"Log failed: {exc}")
        try:
            speak(response)
        except Exception as exc:
            print(f"TTS failed: {exc}")

        # Extend the session after each successful command.
        session_deadline = time.time() + max(0.0, session_seconds)


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
            log_interaction(text, response, metadata={"source": "text"})
        except Exception as exc:
            print(f"Log failed: {exc}")
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
