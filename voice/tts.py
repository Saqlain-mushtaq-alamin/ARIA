"""Text-to-speech using Piper CLI."""

from __future__ import annotations

import os
import platform
import subprocess
import tempfile


def speak(text: str, model_path: str | None = None, piper_exe: str | None = None) -> None:
    """Generate speech with Piper and play it back."""
    if not text.strip():
        return

    model = model_path or os.getenv("PIPER_MODEL")
    if not model:
        raise ValueError("PIPER_MODEL is not set to a Piper model path")

    exe = piper_exe or os.getenv("PIPER_EXE", "piper")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    try:
        subprocess.run(
            [exe, "--model", model, "--output_file", wav_path],
            input=text,
            text=True,
            check=True,
        )

        if platform.system() == "Windows":
            import winsound

            winsound.PlaySound(wav_path, winsound.SND_FILENAME)
        else:
            raise RuntimeError("Playback not implemented for this OS")
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)
