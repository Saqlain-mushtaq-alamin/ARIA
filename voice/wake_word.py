"""Wake word listener running in a background thread."""

from __future__ import annotations

from typing import Callable
import threading
import time
import os

import numpy as np
from openwakeword.model import Model
from openwakeword.utils import download_models
import pyaudio

# Download pre-trained models (one-time, no account needed)
download_models()

oww_model = Model(
    wakeword_models=["hey_jarvis"],
    inference_framework="onnx",
)


def listen_for_wake_word(callback: Callable[[int], None]) -> None:
    """Continuously listen for the wake word and trigger the callback."""
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

    print("Listening for wake word...")

    # Debounce / hysteresis to avoid missed detections and repeated triggers.
    trigger_threshold = float(os.getenv("WAKEWORD_TRIGGER_THRESHOLD", "0.45"))
    reset_threshold = float(os.getenv("WAKEWORD_RESET_THRESHOLD", "0.20"))
    required_hits = int(os.getenv("WAKEWORD_REQUIRED_HITS", "2"))
    cooldown_seconds = float(os.getenv("WAKEWORD_COOLDOWN_SECONDS", "1.2"))

    consecutive_hits = 0
    armed = True
    last_trigger_time = 0.0

    while True:
        audio_chunk = np.frombuffer(
            mic_stream.read(1280, exception_on_overflow=False),
            dtype=np.int16,
        )
        prediction = oww_model.predict(audio_chunk)
        scores = prediction[0] if isinstance(prediction, tuple) else prediction
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
                callback(selected_device_index)
                mic_stream.start_stream()


def start_wake_word_listener(callback: Callable[[int], None]) -> threading.Thread:
    """Start the wake word listener in a background thread."""
    thread = threading.Thread(
        target=listen_for_wake_word,
        args=(callback,),
        daemon=True,
    )
    thread.start()
    return thread
