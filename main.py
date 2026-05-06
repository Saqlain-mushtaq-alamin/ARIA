"""ARIA assistant entry point."""

import os
import sys
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from core.agent import process_text
from memory.conversation_log import log_interaction
from voice.stt import listen_and_transcribe
from voice.tts import speak
from voice.wake_word import start_wake_word_listener


_GESTURE_CONTROLLER_PROC: Optional[subprocess.Popen] = None
_EMOTION_DETECTOR = None


def _try_activate_kinetic_mode(text: str) -> Optional[str]:
    """Start gesture controller when the user asks for kinetic mode."""
    global _GESTURE_CONTROLLER_PROC

    if not text:
        return None

    lower = text.lower()
    wants_kinetic = (
        ("kinetic" in lower or "kinitic" in lower)
        and ("mode" in lower or "control" in lower)
        and any(k in lower for k in ("activate", "activite", "enable", "start", "on"))
    )
    if not wants_kinetic:
        return None

    # Clean up stale process handle.
    if _GESTURE_CONTROLLER_PROC is not None and _GESTURE_CONTROLLER_PROC.poll() is not None:
        _GESTURE_CONTROLLER_PROC = None

    if _GESTURE_CONTROLLER_PROC is not None:
        return "Kinetic mode is already running. Focus the webcam window; press Q to quit."

    repo_root = Path(__file__).resolve().parent
    script_path = repo_root / "vision" / "gesture_contoll" / "gesture_controller.py"
    if not script_path.exists():
        return "Gesture controller not found at vision/gesture_contoll/gesture_controller.py."

    try:
        _GESTURE_CONTROLLER_PROC = subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(repo_root),
        )
    except Exception as exc:
        return f"Failed to start kinetic mode: {exc}"

    return (
        "Kinetic mode activated. A webcam window should open now. "
        "Press Q in that window to stop gesture control."
    )


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


def _start_emotion_detector() -> None:
    """Start webcam-based emotion sampling in a background thread.

    Controlled via env vars:
    - EMOTION_DETECTOR: '1' to enable (default), '0' to disable
    - EMOTION_INTERVAL_SECONDS: sampling interval (default 60)
    - EMOTION_WINDOW_MINUTES: rolling summary window (default 60)
    - EMOTION_CAMERA_INDEX: webcam index (default 0)
    """
    global _EMOTION_DETECTOR

    if os.getenv("EMOTION_DETECTOR", "1").strip() not in {"1", "true", "yes", "on"}:
        return

    if _EMOTION_DETECTOR is not None:
        return

    try:
        from vision.emotion_detector import EmotionDetectorConfig, start_emotion_detector_thread
    except Exception as exc:
        print(f"Emotion detector import failed: {exc}")
        return

    try:
        cfg = EmotionDetectorConfig(
            camera_index=int(os.getenv("EMOTION_CAMERA_INDEX", "0")),
            interval_seconds=float(os.getenv("EMOTION_INTERVAL_SECONDS", "60")),
            window_minutes=int(os.getenv("EMOTION_WINDOW_MINUTES", "60")),
            log_samples=os.getenv("EMOTION_LOG_SAMPLES", "1").strip().lower() in {"1", "true", "yes", "on"},
            save_last_frame=os.getenv("EMOTION_SAVE_LAST_FRAME", "0").strip().lower() in {"1", "true", "yes", "on"},
        )

        def _on_vibe_update(_sample, state_payload):
            try:
                from scheduler.tracker import apply_tired_postpone_rule
            except Exception:
                return

            try:
                trigger_states = tuple(
                    s.strip().lower()
                    for s in os.getenv("EMOTION_RULE_STATES", "tired,stressed,frustrated").split(",")
                    if s.strip()
                )
                msg = apply_tired_postpone_rule(
                    state_payload,
                    night_start_hour_local=int(os.getenv("EMOTION_RULE_NIGHT_START", "21")),
                    night_end_hour_local=int(os.getenv("EMOTION_RULE_NIGHT_END", "6")),
                    tasks_to_move=int(os.getenv("EMOTION_RULE_TASKS_TO_MOVE", "2")),
                    trigger_states=trigger_states,
                    min_trigger_score=float(os.getenv("EMOTION_RULE_MIN_SCORE", "35")),
                    cooldown_minutes=int(os.getenv("EMOTION_RULE_COOLDOWN_MIN", "45")),
                )
            except Exception:
                msg = None

            if msg:
                print(f"[VibeRule] {msg}")
                if os.getenv("EMOTION_RULE_SPEAK", "0").strip().lower() in {"1", "true", "yes", "on"}:
                    try:
                        speak(msg)
                    except Exception:
                        pass

        _EMOTION_DETECTOR = start_emotion_detector_thread(config=cfg, on_update=_on_vibe_update)
        print("Emotion detector started (vibe sampling enabled).")
    except Exception as exc:
        print(f"Emotion detector failed to start: {exc}")


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
        response = _try_activate_kinetic_mode(text) or process_text(text)
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

        response = _try_activate_kinetic_mode(text) or process_text(text)
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
        response = _try_activate_kinetic_mode(text) or process_text(text)
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
    _start_emotion_detector()
    start_wake_word_listener(_handle_wake_word)
    threading.Thread(target=_text_input_loop, daemon=True).start()
    while True:
        time.sleep(0.5)


if __name__ == "__main__":
    main()
