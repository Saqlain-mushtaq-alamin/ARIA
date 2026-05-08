"""
voice/audio_utils.py
====================
Central audio utilities for the ARIA voice pipeline.

Responsibilities
----------------
- Microphone selection with automatic fallback
- Ambient noise floor calibration
- Voice Activity Detection (energy + zero-crossing rate)
- Pre-roll audio buffering (catch the start of speech)
- Shared mic coordination (wake-word ↔ STT never clash)
- record_speech() — the one function every other module calls

Usage
-----
    from voice.audio_utils import record_speech, select_microphone

    device_index = select_microphone()
    wav_path = record_speech(device_index=device_index, output_path="/tmp/cmd.wav")
"""

from __future__ import annotations

import collections
import os
import threading
import time
import wave
from contextlib import contextmanager
from typing import Generator, Optional

import numpy as np
import pyaudio

# ---------------------------------------------------------------------------
# Constants / defaults (all overrideable via env-vars)
# ---------------------------------------------------------------------------

_RATE = 16_000          # samples/sec  (Whisper native rate)
_CHANNELS = 1
_FORMAT = pyaudio.paInt16
_CHUNK = 1_024          # frames per read (~64 ms at 16 kHz)

# Env-var knobs ------------------------------------------------------------- #
def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ[key])
    except (KeyError, ValueError):
        return default

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError):
        return default

def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "")
    if not v:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}

# ---------------------------------------------------------------------------
# Shared mic coordination
# ---------------------------------------------------------------------------

#: Held by the STT recording session; wake-word listener pauses while set.
_mic_busy = threading.Event()
_mic_busy.clear()          # not busy at startup

@contextmanager
def mic_session() -> Generator[None, None, None]:
    """Context manager that marks the mic as busy for the duration."""
    _mic_busy.set()
    try:
        yield
    finally:
        _mic_busy.clear()

def wait_for_mic_free(timeout: float = 10.0) -> bool:
    """Block until the mic is free.  Returns True if free, False on timeout."""
    deadline = time.time() + timeout
    while _mic_busy.is_set():
        if time.time() > deadline:
            return False
        time.sleep(0.05)
    return True

# ---------------------------------------------------------------------------
# Microphone selection
# ---------------------------------------------------------------------------

def list_input_devices() -> list[dict]:
    """Return a list of available input devices."""
    pa = pyaudio.PyAudio()
    devices = []
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0)) > 0:
                devices.append({
                    "index": i,
                    "name": info.get("name", f"device-{i}"),
                    "channels": int(info.get("maxInputChannels", 1)),
                    "rate": int(info.get("defaultSampleRate", _RATE)),
                })
    finally:
        pa.terminate()
    return devices


def select_microphone(preferred_index: Optional[int] = None) -> int:
    """
    Pick the best available input device index.

    Priority:
    1. ``preferred_index`` if given and valid
    2. System default input device
    3. First device with input channels
    """
    pa = pyaudio.PyAudio()
    try:
        # Honour an explicit caller preference.
        if preferred_index is not None:
            try:
                info = pa.get_device_info_by_index(preferred_index)
                if int(info.get("maxInputChannels", 0)) > 0:
                    return preferred_index
            except Exception:
                pass

        # Try system default.
        try:
            info = pa.get_default_input_device_info()
            idx = int(info.get("index", -1))
            if idx >= 0:
                return idx
        except OSError:
            pass

        # Scan all devices.
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0)) > 0:
                return i
    finally:
        pa.terminate()

    raise RuntimeError("No audio input device found.")

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _rms(chunk: bytes) -> float:
    """Root-mean-square energy of a raw PCM int16 chunk."""
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def _zcr(chunk: bytes) -> float:
    """Zero-crossing rate (0-1) of a raw PCM int16 chunk."""
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    if samples.size < 2:
        return 0.0
    signs = np.sign(samples)
    crossings = np.sum(np.abs(np.diff(signs))) / 2
    return float(crossings) / float(len(samples))

# ---------------------------------------------------------------------------
# Noise floor calibration
# ---------------------------------------------------------------------------

class NoiseFloor:
    """
    Calibrates ambient noise energy over a short measurement window.

    Example
    -------
        nf = NoiseFloor()
        nf.calibrate(stream)
        threshold = nf.speech_threshold()
    """

    def __init__(self) -> None:
        self._median_rms: float = 0.0
        self._std_rms: float = 0.0
        self._calibrated: bool = False

    @property
    def calibrated(self) -> bool:
        return self._calibrated

    @property
    def floor(self) -> float:
        return self._median_rms

    def calibrate(
        self,
        stream: pyaudio.Stream,
        *,
        duration_seconds: float = 0.40,
        chunk: int = _CHUNK,
        rate: int = _RATE,
    ) -> None:
        """Collect ``duration_seconds`` of audio and compute the noise floor."""
        n_chunks = max(1, int(duration_seconds * rate / chunk))
        samples: list[float] = []
        for _ in range(n_chunks):
            data = stream.read(chunk, exception_on_overflow=False)
            samples.append(_rms(data))

        arr = np.array(samples, dtype=np.float32)
        self._median_rms = float(np.median(arr))
        self._std_rms = float(np.std(arr))
        self._calibrated = True

        if _env_bool("VOICE_DEBUG", False):
            print(
                f"[audio_utils] noise floor: median={self._median_rms:.1f}"
                f"  std={self._std_rms:.1f}"
            )

    def speech_threshold(
        self,
        *,
        factor: Optional[float] = None,
        floor: Optional[float] = None,
    ) -> float:
        """Return the RMS threshold above which speech is assumed."""
        f = factor if factor is not None else _env_float("VOICE_THRESHOLD_FACTOR", 2.6)
        fl = floor if floor is not None else _env_float("VOICE_THRESHOLD_FLOOR", 180.0)
        return max(fl, self._median_rms * f)

# ---------------------------------------------------------------------------
# Voice Activity Detector
# ---------------------------------------------------------------------------

class VoiceActivityDetector:
    """
    Simple energy + ZCR voice activity detector.

    Speech is detected when:
    - RMS energy > ``energy_threshold``
    - ZCR < ``zcr_ceiling``  (speech ZCR is typically lower than noise/silence)
    """

    def __init__(
        self,
        energy_threshold: float = 400.0,
        zcr_ceiling: float = 0.35,
    ) -> None:
        self.energy_threshold = energy_threshold
        self.zcr_ceiling = zcr_ceiling

    def is_speech(self, chunk: bytes) -> bool:
        energy = _rms(chunk)
        if energy < self.energy_threshold:
            return False
        zcr = _zcr(chunk)
        return zcr < self.zcr_ceiling

    def update_threshold(self, noise_floor: NoiseFloor) -> None:
        self.energy_threshold = noise_floor.speech_threshold()

# ---------------------------------------------------------------------------
# Pre-roll buffer
# ---------------------------------------------------------------------------

class PreRollBuffer:
    """
    Keeps the last N chunks so we never miss the beginning of an utterance.
    """

    def __init__(self, seconds: float = 0.35, chunk: int = _CHUNK, rate: int = _RATE) -> None:
        capacity = max(1, int(seconds * rate / chunk))
        self._buf: collections.deque[bytes] = collections.deque(maxlen=capacity)

    def push(self, chunk: bytes) -> None:
        self._buf.append(chunk)

    def drain(self) -> list[bytes]:
        frames = list(self._buf)
        self._buf.clear()
        return frames

# ---------------------------------------------------------------------------
# Core recording function
# ---------------------------------------------------------------------------

def record_speech(
    output_path: str,
    *,
    device_index: Optional[int] = None,
    rate: int = _RATE,
    chunk: int = _CHUNK,
    # Silence / timing ---------------------------------------------------
    silence_seconds: Optional[float] = None,
    max_seconds: Optional[float] = None,
    min_speech_seconds: Optional[float] = None,
    pre_roll_seconds: Optional[float] = None,
    # Threshold -----------------------------------------------------------
    energy_threshold: Optional[float] = None,
    auto_threshold: Optional[bool] = None,
    threshold_factor: Optional[float] = None,
    threshold_floor: Optional[float] = None,
    # ZCR -----------------------------------------------------------------
    zcr_ceiling: Optional[float] = None,
) -> str:
    """
    Record audio until silence is detected and write a WAV file.

    Parameters
    ----------
    output_path:
        Destination ``.wav`` file path.
    device_index:
        PyAudio device index.  Auto-selected if ``None``.

    All timing / threshold parameters fall back to env-vars then built-in
    defaults so callers don't have to specify anything.

    Returns
    -------
    str
        The ``output_path`` that was written.
    """
    # Resolve parameters from env-vars if not given explicitly.
    _auto = auto_threshold if auto_threshold is not None else _env_bool("VOICE_AUTO_THRESHOLD", True)
    _silence_s = silence_seconds if silence_seconds is not None else _env_float("VOICE_SILENCE_SECONDS", 0.85)
    _max_s = max_seconds if max_seconds is not None else _env_float("VOICE_MAX_SECONDS", 12.0)
    _min_speech_s = min_speech_seconds if min_speech_seconds is not None else _env_float("VOICE_MIN_SPEECH_SECONDS", 0.4)
    _pre_roll_s = pre_roll_seconds if pre_roll_seconds is not None else _env_float("VOICE_PRE_ROLL_SECONDS", 0.35)
    _factor = threshold_factor if threshold_factor is not None else _env_float("VOICE_THRESHOLD_FACTOR", 2.6)
    _floor = threshold_floor if threshold_floor is not None else _env_float("VOICE_THRESHOLD_FLOOR", 180.0)
    _energy = energy_threshold if energy_threshold is not None else _env_float("VOICE_SILENCE_THRESHOLD", 400.0)
    _zcr = zcr_ceiling if zcr_ceiling is not None else _env_float("VOICE_ZCR_CEILING", 0.35)
    _debug = _env_bool("VOICE_DEBUG", False)

    # Chunk counts.
    silence_limit = max(1, int(_silence_s * rate / chunk))
    max_chunks = max(1, int(_max_s * rate / chunk))
    min_speech_chunks = max(0, int(_min_speech_s * rate / chunk))

    device_index = select_microphone(device_index)

    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(
            format=_FORMAT,
            channels=_CHANNELS,
            rate=rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=chunk,
        )
    except OSError as exc:
        pa.terminate()
        raise RuntimeError(f"Cannot open microphone (device={device_index}): {exc}") from exc

    noise_floor = NoiseFloor()
    pre_roll = PreRollBuffer(seconds=_pre_roll_s, chunk=chunk, rate=rate)

    # Calibrate noise floor (doubles as pre-roll warmup).
    if _auto:
        noise_floor.calibrate(stream, duration_seconds=0.40, chunk=chunk, rate=rate)
        vad = VoiceActivityDetector(
            energy_threshold=noise_floor.speech_threshold(factor=_factor, floor=_floor),
            zcr_ceiling=_zcr,
        )
    else:
        vad = VoiceActivityDetector(energy_threshold=_energy, zcr_ceiling=_zcr)

    if _debug:
        mode = "auto" if _auto else "fixed"
        print(
            f"[audio_utils] recording mode={mode}"
            f" threshold={vad.energy_threshold:.0f}"
            f" silence={_silence_s}s max={_max_s}s"
        )

    frames: list[bytes] = []
    silence_chunks = 0
    speech_chunks = 0
    recording = False

    try:
        for _ in range(max_chunks):
            data = stream.read(chunk, exception_on_overflow=False)

            if not recording:
                pre_roll.push(data)

            speaking = vad.is_speech(data)

            if speaking:
                if not recording:
                    recording = True
                    frames.extend(pre_roll.drain())  # include pre-roll
                silence_chunks = 0
                frames.append(data)
                speech_chunks += 1
            elif recording:
                frames.append(data)
                speech_chunks += 1
                silence_chunks += 1
                if speech_chunks >= min_speech_chunks and silence_chunks >= silence_limit:
                    break  # Natural end of utterance.
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        pa.terminate()

    # Write WAV.
    pa2 = pyaudio.PyAudio()
    sample_width = pa2.get_sample_size(_FORMAT)
    pa2.terminate()

    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(_CHANNELS)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(b"".join(frames))

    if _debug:
        duration = len(frames) * chunk / rate
        print(f"[audio_utils] wrote {duration:.2f}s → {output_path}")

    return output_path


def wav_duration(path: str) -> float:
    """Return WAV duration in seconds (0 on error)."""
    try:
        with wave.open(path, "rb") as wf:
            return wf.getnframes() / max(1, wf.getframerate())
    except Exception:
        return 0.0
