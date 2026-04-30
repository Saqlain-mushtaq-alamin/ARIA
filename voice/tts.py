"""Text-to-speech using Piper CLI."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile

from dotenv import load_dotenv

_dotenv_loaded = False


def _ensure_env_loaded() -> None:
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    load_dotenv()
    load_dotenv(os.path.join("config", ".env"))
    _dotenv_loaded = True


def _ensure_model_available(model_path: str) -> None:
    if os.path.isfile(model_path):
        return

    voice_name = os.path.splitext(os.path.basename(model_path))[0]
    download_dir = os.path.dirname(model_path) or "."
    if os.getenv("PIPER_AUTO_DOWNLOAD") == "1":
        subprocess.run(
            [
                sys.executable,
                "-m",
                "piper.download_voices",
                "--download-dir",
                download_dir,
                voice_name,
            ],
            check=True,
        )
        if os.path.isfile(model_path):
            return

    raise ValueError(
        "Piper model not found: "
        f"{model_path}\n"
        "Download it with: "
        f"{sys.executable} -m piper.download_voices --download-dir \"{download_dir}\" \"{voice_name}\""
    )


def speak(text: str, model_path: str | None = None, piper_exe: str | None = None) -> None:
    """Generate speech with Piper and play it back."""
    if not text.strip():
        return

    _ensure_env_loaded()

    model = model_path or os.getenv("PIPER_MODEL")
    if not model:
        raise ValueError(
            "PIPER_MODEL is not set. Put it in .env or config/.env, or pass model_path."
        )

    _ensure_model_available(model)

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
