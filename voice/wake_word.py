"""
voice/wake_word.py
==================
Wake word listener using openWakeWord.

Core bug fixed
--------------
Previously the PyAudio stream was only *stopped* (not closed + terminated)
before handing control to the STT callback.  Because PyAudio held the device
open, the STT module's attempt to open the same device silently failed —
producing the "wake word fires but nothing transcribes" symptom.

Fix: fully close and terminate the PyAudio instance before calling the
callback, then re-create it from scratch after the callback returns.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Optional

import numpy as np
import pyaudio

from voice.audio_utils import mic_session, _mic_busy, select_microphone, _env_bool, _env_float, _env_int

# ---------------------------------------------------------------------------
# Model singleton
# ---------------------------------------------------------------------------

_OWW_MODEL: Any | None = None
_MODEL_LOCK = threading.Lock()


def _get_model() -> Any:
    global _OWW_MODEL
    with _MODEL_LOCK:
        if _OWW_MODEL is not None:
            return _OWW_MODEL

        from openwakeword.model import Model          # type: ignore
        from openwakeword.utils import download_models  # type: ignore

        download_models()

        model_env = os.getenv("WAKEWORD_MODEL", "hey_jarvis")
        model_names = [m.strip() for m in model_env.split(",") if m.strip()] or ["hey_jarvis"]

        _OWW_MODEL = Model(
            wakeword_models=model_names,
            inference_framework=os.getenv("WAKEWORD_FRAMEWORK", "onnx"),
        )
        return _OWW_MODEL

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CHUNK = 1_280   # frames per read — openWakeWord expects 80 ms @ 16 kHz
_RATE  = 16_000


def _open_mic(device_index: int) -> tuple[pyaudio.PyAudio, pyaudio.Stream]:
    """Open a fresh PyAudio instance + stream.  Raises on failure."""
    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=_RATE,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=_CHUNK,
        )
        return pa, stream
    except Exception:
        pa.terminate()
        raise


def _close_mic(pa: pyaudio.PyAudio, stream: pyaudio.Stream) -> None:
    """Cleanly close + terminate — fully releasing the device."""
    try:
        stream.stop_stream()
    except Exception:
        pass
    try:
        stream.close()
    except Exception:
        pass
    try:
        pa.terminate()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------

def listen_for_wake_word(
    callback: Callable[[int], None],
    *,
    stop_event: Optional[threading.Event] = None,
) -> None:
    """
    Continuously listen for the configured wake word and invoke *callback*.

    callback receives the device index so the STT layer can open the same mic.

    The mic stream is fully closed before callback() runs, so STT can open
    the device without conflict.  After callback() returns the stream is
    re-opened automatically.
    """
    if _env_bool("WAKEWORD_DISABLED", False):
        print("[wakeword] disabled via WAKEWORD_DISABLED env-var.")
        return

    # --- Load model -------------------------------------------------------
    try:
        oww_model = _get_model()
    except Exception as exc:
        print(f"[wakeword] model init failed: {exc}")
        return

    # --- Select device ----------------------------------------------------
    device_index = select_microphone()

    # --- Thresholds / hysteresis ------------------------------------------
    trigger_threshold = _env_float("WAKEWORD_TRIGGER_THRESHOLD", 0.25)
    reset_threshold   = _env_float("WAKEWORD_RESET_THRESHOLD",   0.10)
    required_hits     = _env_int ("WAKEWORD_REQUIRED_HITS",      2)
    cooldown_seconds  = _env_float("WAKEWORD_COOLDOWN_SECONDS",  1.5)
    debug             = _env_bool ("WAKEWORD_DEBUG",             False)
    debug_every_s     = _env_float("WAKEWORD_DEBUG_EVERY_SECONDS", 2.0)

    print(f"[wakeword] listening on device={device_index}  model={os.getenv('WAKEWORD_MODEL', 'hey_jarvis')}")
    print("[wakeword] say 'hey jarvis' clearly to activate.")

    consecutive_hits = 0
    armed            = True
    last_trigger     = 0.0
    last_debug       = 0.0

    while True:
        if stop_event is not None and stop_event.is_set():
            return
        # ---- Open mic (or re-open after a session) -----------------------
        try:
            pa, stream = _open_mic(device_index)
        except Exception as exc:
            print(f"[wakeword] failed to open mic, retrying in 2s: {exc}")
            time.sleep(2.0)
            continue

        # ---- Listening loop ----------------------------------------------
        triggered = False
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    return
                # Don't fight STT for the device.
                if _mic_busy.is_set():
                    time.sleep(0.05)
                    continue

                raw = stream.read(_CHUNK, exception_on_overflow=False)
                audio_chunk = np.frombuffer(raw, dtype=np.int16)
                prediction  = oww_model.predict(audio_chunk)
                scores      = prediction if isinstance(prediction, dict) else prediction

                # Debug logging.
                if debug:
                    now_dbg = time.time()
                    if now_dbg - last_debug >= debug_every_s:
                        last_debug = now_dbg
                        try:
                            rms = float(np.sqrt(np.mean(audio_chunk.astype(np.float32) ** 2)))
                            best_name, best_score = max(scores.items(), key=lambda kv: float(kv[1]))
                            print(f"[wakeword] rms={rms:.0f}  best={best_name}:{float(best_score):.3f}")
                        except Exception:
                            pass

                # Scoring / debounce.
                now = time.time()
                if now - last_trigger < cooldown_seconds:
                    continue

                for model_name, score in scores.items():
                    score_f = float(score)
                    if score_f >= trigger_threshold and armed:
                        consecutive_hits += 1
                    elif score_f <= reset_threshold:
                        consecutive_hits = 0
                        armed = True

                    if armed and consecutive_hits >= required_hits:
                        armed            = False
                        consecutive_hits = 0
                        last_trigger     = now
                        triggered        = True
                        print(f"[wakeword] 🎙️  Wake word detected! ({score_f:.2f})")
                        break

                if triggered:
                    break

        except Exception as exc:
            print(f"[wakeword] stream error: {exc}")

        finally:
            # CRITICAL: fully release the device before STT opens it.
            _close_mic(pa, stream)

        if stop_event is not None and stop_event.is_set():
            return

        if triggered:
            # Small gap so the OS fully releases the device.
            time.sleep(0.10)
            try:
                with mic_session():
                    callback(device_index)
            except Exception as exc:
                print(f"[wakeword] callback error: {exc}")
            # Brief pause before re-arming so the user doesn't re-trigger
            # immediately after the TTS response.
            time.sleep(0.50)
            armed            = True
            consecutive_hits = 0


def start_wake_word_listener(
    callback: Callable[[int], None],
    *,
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """Start the wake word listener in a daemon background thread."""
    thread = threading.Thread(
        target=listen_for_wake_word,
        args=(callback,),
        kwargs={"stop_event": stop_event},
        daemon=True,
        name="wakeword-listener",
    )
    thread.start()
    return thread