"""Wake word listener running in a background thread."""

from __future__ import annotations

from typing import Callable

import numpy as np
from openwakeword.model import Model
from openwakeword.utils import download_models
import pyaudio

# Download pre-trained models (one-time, no account needed)
download_models()

oww_model = Model(
    wakeword_models=["hey_aria"],
    inference_framework="onnx",
)


def listen_for_wake_word(callback: Callable[[], None]) -> None:
    """Continuously listen for the wake word and trigger the callback."""
    audio = pyaudio.PyAudio()
    mic_stream = audio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        input=True,
        frames_per_buffer=1280,
    )

    print("Listening for wake word...")
    while True:
        audio_chunk = np.frombuffer(mic_stream.read(1280), dtype=np.int16)
        prediction = oww_model.predict(audio_chunk)
        scores = prediction[0] if isinstance(prediction, tuple) else prediction
        for model_name, score in scores.items():
            if score > 0.5:
                print(f"Wake word detected! ({score:.2f})")
                mic_stream.stop_stream()
                callback()
                mic_stream.start_stream()
