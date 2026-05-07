"""Wake word listener running in a background thread.

Notes:
- Avoid heavy downloads/model init at import time.
- Never let exceptions in the callback kill the listener loop.
"""

from __future__ import annotations

from typing import Callable, Any
import threading
import time
import os

import numpy as np
import pyaudio


_OWW_MODEL: Any | None = None


def _get_model() -> Any:
    global _OWW_MODEL
    if _OWW_MODEL is not None:
        return _OWW_MODEL

    from openwakeword.model import Model  # type: ignore
    from openwakeword.utils import download_models  # type: ignore

    # Download pre-trained models (one-time, no account needed)
    download_models()

    model_name = os.getenv("WAKEWORD_MODEL", "hey_jarvis").strip() or "hey_jarvis"
    _OWW_MODEL = Model(
        wakeword_models=[model_name],
        inference_framework=os.getenv("WAKEWORD_FRAMEWORK", "onnx"),
    )
    return _OWW_MODEL


def listen_for_wake_word(callback: Callable[[int], None]) -> None:
    """Continuously listen for the wake word and trigger the callback."""
    if os.getenv("WAKEWORD_DISABLED", "0").strip().lower() in {"1", "true", "yes", "on"}:
        print("Wake word listener disabled (WAKEWORD_DISABLED=1).")
        return

    try:
        oww_model = _get_model()
    except Exception as exc:
        print(f"Wake word model init failed: {exc}")
        return

    audio = pyaudio.PyAudio()
    device_indices: list[int] = []
    try:
        default_info = audio.get_default_input_device_info()
        device_indices.append(int(default_info.get("index", 0)))
    except OSError:
        pass

    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if int(info.get("maxInputChannels", 0)) > 0 and i not in device_indices:
            device_indices.append(i)

    if not device_indices:
        raise RuntimeError("No input audio device found")

    mic_stream = None
    selected_device_index: int | None = None
    last_error: Exception | None = None
    for device_index in device_indices:
        try:
            mic_stream = audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=1280,
            )
            selected_device_index = int(device_index)
            break
        except OSError as exc:
            last_error = exc

    if mic_stream is None:
        raise RuntimeError(
            "Failed to open any input device."
        ) from last_error

    if selected_device_index is None:
        raise RuntimeError("Failed to resolve selected input device")

    print(f"Listening for wake word (device={selected_device_index})...")

    # Debounce / hysteresis.
    # Defaults tuned to be easy to trigger; override via env vars if noisy.
    trigger_threshold = float(os.getenv("WAKEWORD_TRIGGER_THRESHOLD", "0.35"))
    reset_threshold = float(os.getenv("WAKEWORD_RESET_THRESHOLD", "0.15"))
    required_hits = int(os.getenv("WAKEWORD_REQUIRED_HITS", "1"))
    cooldown_seconds = float(os.getenv("WAKEWORD_COOLDOWN_SECONDS", "1.0"))

    debug = os.getenv("WAKEWORD_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
    debug_every_s = float(os.getenv("WAKEWORD_DEBUG_EVERY_SECONDS", "1.0"))
    last_debug = 0.0

    consecutive_hits = 0
    armed = True
    last_trigger_time = 0.0

    while True:
        raw = mic_stream.read(1280, exception_on_overflow=False)
        audio_chunk = np.frombuffer(raw, dtype=np.int16)
        prediction = oww_model.predict(audio_chunk)
        scores = prediction[0] if isinstance(prediction, tuple) else prediction

        if debug:
            now_dbg = time.time()
            if now_dbg - last_debug >= debug_every_s:
                last_debug = now_dbg
                try:
                    # Print the loudness and top wake score so users can tune thresholds.
                    rms = float(np.sqrt(np.mean(audio_chunk.astype(np.float32) ** 2))) if audio_chunk.size else 0.0
                    best_name, best_score = max(scores.items(), key=lambda kv: float(kv[1]))
                    print(f"[wakeword] rms={rms:.1f} best={best_name}:{float(best_score):.3f}")
                except Exception:
                    pass

        for model_name, score in scores.items():
            now = time.time()
            if now - last_trigger_time < cooldown_seconds:
                continue

            if score >= trigger_threshold and armed:
                consecutive_hits += 1
            elif score <= reset_threshold:
                consecutive_hits = 0
                armed = True

            if armed and consecutive_hits >= required_hits:
                armed = False
                consecutive_hits = 0
                last_trigger_time = now
                print(f"Wake word detected! ({score:.2f})")
                mic_stream.stop_stream()
                try:
                    callback(selected_device_index)
                except Exception as exc:
                    print(f"Wake word callback failed: {exc}")
                finally:
                    try:
                        mic_stream.start_stream()
                    except Exception:
                        return


def start_wake_word_listener(callback: Callable[[int], None]) -> threading.Thread:
    """Start the wake word listener in a background thread."""
    thread = threading.Thread(
        target=listen_for_wake_word,
        args=(callback,),
        daemon=True,
    )
    thread.start()
    return thread
