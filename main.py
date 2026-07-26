"""ARIA assistant entry point."""

import argparse
import os
import sys
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

# ── Settings system (load FIRST, before anything reads env vars) ─────────────
try:
    from config.settings import apply_to_env as _apply_settings_to_env
    _apply_settings_to_env()
    print("[settings] Settings loaded and applied.")
except Exception as _exc:
    print(f"[settings] Could not load settings ({_exc}), using defaults.")

from core.agent import process_text, _cli_ask
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
    # Keep DeepFace weights inside the repo by default.
    try:
        repo_root = Path(__file__).resolve().parent
        default_deepface_home = repo_root / "vision" / "models" / "deepface"
        os.environ.setdefault("DEEPFACE_HOME", str(default_deepface_home))
    except Exception:
        pass

    for key in ("HF_HOME", "TRANSFORMERS_CACHE", "TORCH_HOME", "PIP_CACHE_DIR"):
        path = os.getenv(key)
        if not path:
            continue
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass

    # Also ensure DeepFace cache dir exists.
    try:
        df_home = os.getenv("DEEPFACE_HOME")
        if df_home:
            os.makedirs(df_home, exist_ok=True)
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
    - EMOTION_REQUIRE_FACE: '1' to require a detected face (default 1)
    - EMOTION_MIN_FRAME_STDDEV: reject blank/blocked frames (default 5.0)
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
            enforce_detection=os.getenv("EMOTION_REQUIRE_FACE", "1").strip().lower() in {"1", "true", "yes", "on"},
            min_frame_stddev=float(os.getenv("EMOTION_MIN_FRAME_STDDEV", "5.0")),
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
                    mode=os.getenv("EMOTION_RULE_MODE", "anytime_sustained"),
                    night_start_hour_local=int(os.getenv("EMOTION_RULE_NIGHT_START", "21")),
                    night_end_hour_local=int(os.getenv("EMOTION_RULE_NIGHT_END", "6")),
                    tasks_to_move=int(os.getenv("EMOTION_RULE_TASKS_TO_MOVE", "2")),
                    trigger_states=trigger_states,
                    min_trigger_score=float(os.getenv("EMOTION_RULE_MIN_SCORE", "35")),
                    sustained_samples=int(os.getenv("EMOTION_RULE_SUSTAINED_SAMPLES", "3")),
                    sustained_within_minutes=int(os.getenv("EMOTION_RULE_SUSTAINED_MINUTES", "25")),
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
    stt_model = os.getenv("VOICE_MODEL", "base").strip() or "base"

    if session_seconds <= 0:
        try:
            print("[voice] Listening for command...")
            text = listen_and_transcribe(model_name=stt_model, input_device_index=device_index)
        except Exception as exc:
            print(f"Voice STT failed: {exc}")
            return
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

        try:
            print("[voice] Listening for command...")
            text = listen_and_transcribe(model_name=stt_model, input_device_index=device_index)
        except Exception as exc:
            print(f"Voice STT failed: {exc}")
            return
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
        response = _try_activate_kinetic_mode(text) or process_text(text, ask_fn=_cli_ask)
        print(response)
        try:
            log_interaction(text, response, metadata={"source": "text"})
        except Exception as exc:
            print(f"Log failed: {exc}")
        try:
            speak(response)
        except Exception as exc:
            print(f"TTS failed: {exc}")


def _run_headless() -> None:
    """Legacy non-UI entrypoint (voice + stdin loop)."""

    voice_mode = os.getenv("VOICE_MODE", "wakeword").strip().lower()
    def _start_always_listen_thread() -> None:
        stt_model = os.getenv("VOICE_MODEL", "base").strip() or "base"

        def _always_listen_loop() -> None:
            print("Voice mode=always (listening for commands; Ctrl+C to stop).")
            while True:
                try:
                    print("[voice] Listening for command...")
                    text = listen_and_transcribe(model_name=stt_model, input_device_index=None)
                except Exception as exc:
                    print(f"Voice STT failed: {exc}")
                    time.sleep(1.0)
                    continue

                if not text:
                    continue

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

        threading.Thread(target=_always_listen_loop, daemon=True).start()

    if voice_mode in {"always", "continuous"}:
        _start_always_listen_thread()
    else:
        try:
            start_wake_word_listener(_handle_wake_word)
        except Exception as exc:
            print(f"Wake word listener failed to start: {exc}")
            print("Falling back to VOICE_MODE=always.")
            _start_always_listen_thread()

    threading.Thread(target=_text_input_loop, daemon=True).start()
    while True:
        time.sleep(0.5)


def _start_upgrade_daemons() -> None:
    """Start all upgrade-related background daemon threads."""
    try:
        from config.settings import get as get_setting
    except Exception:
        return

    # Cognitive monitor (keyboard timing analysis)
    if get_setting("cognitive_monitor.enabled", True):
        try:
            from vision.cognitive_monitor import start_cognitive_monitor
            start_cognitive_monitor()
        except Exception as exc:
            print(f"[cognitive_monitor] Failed to start: {exc}")

    # Productivity guardian (distraction detection)
    if get_setting("productivity_guardian.enabled", True):
        try:
            from modules.productivity_guardian import start_guardian
            start_guardian()
        except Exception as exc:
            print(f"[productivity_guardian] Failed to start: {exc}")

    # Autonomous planner (morning briefing + evening review)
    if get_setting("autonomous_planner.enabled", True):
        try:
            from scheduler.autonomous_planner import start_autonomous_planner
            start_autonomous_planner()
        except Exception as exc:
            print(f"[autonomous_planner] Failed to start: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ARIA assistant")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without PyQt UI (legacy terminal + voice loop).",
    )
    args = parser.parse_args(argv)

    _load_env()
    _ensure_cache_dirs()
    _start_emotion_detector()

    # ── Start upgrade daemon threads ─────────────────────────────────────
    _start_upgrade_daemons()

    headless = bool(args.headless) or os.getenv("ARIA_HEADLESS", "").strip().lower() in {"1", "true", "yes", "on"}
    if headless:
        try:
            from vision.screen_reader import start_screen_reader

            # In headless mode, speak proactive suggestions (when available).
            start_screen_reader(proactive_callback=speak)
        except Exception:
            pass
        _run_headless()
        return 0

    try:
        from ui.app_runtime import run_ui
    except Exception as exc:
        print(f"UI failed to import ({exc}). Falling back to --headless mode.")
        _run_headless()
        return 0

    return int(run_ui())


if __name__ == "__main__":
    raise SystemExit(main())
