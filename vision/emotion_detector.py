"""Lightweight emotion detection via webcam + DeepFace.

Design goals:
- Do NOT run every frame (heavy). Sample on an interval (default ~60s).
- Keep a rolling window (default 60 minutes) and persist a compact summary.
- Publish results into ARIA's memory system when available.

This module is intentionally self-contained so it can run as a background
thread from `main.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional, Tuple
import json
import os
import time


# DeepFace + OpenCV are heavy imports; keep them local when possible.


SUPPORTED_STATES = (
    "happy",
    "sad",
    "angry",
    "neutral",
    "stressed",
    "tired",
    "frustrated",
)


@dataclass(frozen=True)
class EmotionDetectorConfig:
    camera_index: int = 0
    interval_seconds: float = 60.0
    window_minutes: int = 60
    detector_backend: str = "opencv"  # fastest lightweight option
    enforce_detection: bool = False

    # Observability
    log_samples: bool = True
    save_last_frame: bool = False
    last_frame_path: str = os.path.join("memory", "emotion_last_frame.jpg")

    # Output/state paths
    emotion_state_path: str = os.path.join("memory", "emotion_state.json")

    # Publishing
    store_snapshots_to_vector_memory: bool = True
    store_window_summaries_to_vector_memory: bool = True
    min_seconds_between_window_summaries: float = 300.0  # 5 minutes

    # State file content
    recent_samples_to_persist: int = 10


@dataclass(frozen=True)
class EmotionSample:
    timestamp_utc: str
    dominant_state: str
    scores: Dict[str, float]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _capture_frame(camera_index: int) -> Optional["Any"]:
    # Returns a single BGR frame (numpy array) or None.
    try:
        import cv2  # type: ignore
    except Exception:
        return None

    cap = None
    try:
        # CAP_DSHOW reduces startup delay on many Windows machines.
        cap = cv2.VideoCapture(int(camera_index), cv2.CAP_DSHOW)
        if not cap.isOpened():
            return None

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        # Warm a couple frames to let auto-exposure settle.
        for _ in range(2):
            cap.read()

        ok, frame = cap.read()
        if not ok:
            return None
        return frame
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass


def _maybe_save_frame(frame_bgr: "Any", path: str) -> None:
    try:
        import cv2  # type: ignore
    except Exception:
        return

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        cv2.imwrite(path, frame_bgr)
    except Exception:
        return


def _deepface_analyze_emotion(
    frame_bgr: "Any",
    detector_backend: str,
    enforce_detection: bool,
) -> Optional[Dict[str, float]]:
    # Returns DeepFace's base emotion scores dict (7-class) as floats.
    try:
        from deepface import DeepFace  # type: ignore
    except Exception:
        return None

    try:
        result_any: Any = DeepFace.analyze(
            img_path=frame_bgr,
            actions=["emotion"],
            enforce_detection=enforce_detection,
            detector_backend=detector_backend,
        )
    except Exception:
        return None

    # DeepFace may return a list when multiple faces are detected.
    payload: Any
    if isinstance(result_any, list):
        if not result_any:
            return None
        payload = result_any[0]
    else:
        payload = result_any

    if not isinstance(payload, dict):
        return None

    emotion = payload.get("emotion")
    if not isinstance(emotion, dict):
        return None

    scores: Dict[str, float] = {}
    for k, v in emotion.items():
        try:
            scores[str(k).lower()] = float(v)
        except Exception:
            continue

    return scores or None


def _normalize_scores(scores: Dict[str, float]) -> Dict[str, float]:
    # Ensure expected keys exist.
    base_keys = ("angry", "disgust", "fear", "happy", "sad", "surprise", "neutral")
    normalized: Dict[str, float] = {k: float(scores.get(k, 0.0) or 0.0) for k in base_keys}

    # Synthetic signals requested by the user.
    fear = normalized["fear"]
    angry = normalized["angry"]
    sad = normalized["sad"]
    neutral = normalized["neutral"]
    disgust = normalized["disgust"]

    # Heuristics (simple + explainable):
    # - stressed: mostly fear, some angry/sad
    # - frustrated: mostly angry + disgust
    # - tired: mostly neutral + sad
    stressed = 0.65 * fear + 0.25 * angry + 0.10 * sad
    frustrated = 0.70 * angry + 0.30 * disgust
    tired = 0.60 * neutral + 0.40 * sad

    normalized["stressed"] = float(stressed)
    normalized["frustrated"] = float(frustrated)
    normalized["tired"] = float(tired)

    # Map surprise into neutral-ish for state selection.
    normalized["surprise_neutral"] = float(0.6 * normalized["surprise"] + 0.4 * neutral)

    return normalized


def _pick_dominant_state(scores: Dict[str, float]) -> str:
    # Choose among the 7 supported states only.
    candidates = {k: float(scores.get(k, 0.0) or 0.0) for k in SUPPORTED_STATES}
    # If all-zero, fall back.
    best = max(candidates.items(), key=lambda kv: kv[1])
    return best[0] if best[1] > 0 else "neutral"


def _summarize_window(samples: Deque[EmotionSample]) -> Dict[str, Any]:
    counts: Dict[str, int] = {k: 0 for k in SUPPORTED_STATES}
    for s in samples:
        if s.dominant_state in counts:
            counts[s.dominant_state] += 1

    total = sum(counts.values())
    dist: Dict[str, float] = {}
    if total > 0:
        for k, c in counts.items():
            dist[k] = round((c / total) * 100.0, 2)
    else:
        dist = {k: 0.0 for k in SUPPORTED_STATES}

    dominant_state = max(dist.items(), key=lambda kv: kv[1])[0] if total > 0 else "neutral"
    last = samples[-1] if samples else None

    return {
        "samples": int(total),
        "window_distribution_percent": dist,
        "window_dominant_state": dominant_state,
        "last_state": getattr(last, "dominant_state", None),
        "last_scores": getattr(last, "scores", None),
        "last_timestamp_utc": getattr(last, "timestamp_utc", None),
    }


def _publish_vector_memory(text: str, metadata: Dict[str, Any]) -> None:
    try:
        from memory.vector_store import store_memory  # type: ignore
    except Exception:
        return

    try:
        store_memory(text=text, metadata=metadata)
    except Exception:
        return


class EmotionDetector:
    def __init__(
        self,
        config: Optional[EmotionDetectorConfig] = None,
        on_update: Optional[Callable[[EmotionSample, Dict[str, Any]], None]] = None,
    ) -> None:
        self.config = config or EmotionDetectorConfig()
        self._on_update = on_update

        self._samples: Deque[EmotionSample] = deque()
        self._stop = False
        self._last_window_summary_ts = 0.0

    def stop(self) -> None:
        self._stop = True

    def _prune(self) -> None:
        cutoff = _utc_now() - timedelta(minutes=int(self.config.window_minutes))
        while self._samples:
            try:
                ts = datetime.fromisoformat(self._samples[0].timestamp_utc)
            except Exception:
                self._samples.popleft()
                continue
            if ts >= cutoff:
                break
            self._samples.popleft()

    def _write_state_file(self) -> Dict[str, Any]:
        self._prune()
        summary = _summarize_window(self._samples)

        # Persist a small recent history so downstream rules can detect
        # sustained states (option C).
        recent_n = max(1, int(self.config.recent_samples_to_persist))
        recent = list(self._samples)[-recent_n:]
        recent_payload = [
            {
                "timestamp_utc": s.timestamp_utc,
                "dominant_state": s.dominant_state,
                "scores": dict(s.scores),
            }
            for s in recent
        ]

        payload: Dict[str, Any] = {
            "last_update_utc": _safe_iso(_utc_now()),
            "interval_seconds": float(self.config.interval_seconds),
            "window_minutes": int(self.config.window_minutes),
            "recent_samples": recent_payload,
            **summary,
        }
        _atomic_write_json(self.config.emotion_state_path, payload)
        return payload

    def run_once(self) -> Tuple[Optional[EmotionSample], Optional[Dict[str, Any]]]:
        frame = _capture_frame(self.config.camera_index)
        if frame is None:
            return None, None

        if self.config.save_last_frame:
            _maybe_save_frame(frame, self.config.last_frame_path)

        base = _deepface_analyze_emotion(
            frame,
            detector_backend=self.config.detector_backend,
            enforce_detection=self.config.enforce_detection,
        )
        if not base:
            return None, None

        scores = _normalize_scores(base)
        dominant_state = _pick_dominant_state(scores)

        sample = EmotionSample(
            timestamp_utc=_safe_iso(_utc_now()),
            dominant_state=dominant_state,
            scores={k: float(scores.get(k, 0.0) or 0.0) for k in SUPPORTED_STATES},
        )

        self._samples.append(sample)
        state_payload = self._write_state_file()

        if self.config.log_samples:
            print(
                f"[EmotionDetector] {sample.timestamp_utc} dominant={sample.dominant_state} scores={sample.scores}"
            )

        if self.config.store_snapshots_to_vector_memory:
            _publish_vector_memory(
                text=(
                    f"Vibe check: {sample.dominant_state}. "
                    f"Scores={sample.scores}."
                ),
                metadata={
                    "type": "emotion_snapshot",
                    "dominant_state": sample.dominant_state,
                    "timestamp": sample.timestamp_utc,
                    **{f"score_{k}": v for k, v in sample.scores.items()},
                },
            )

        now = time.time()
        if (
            self.config.store_window_summaries_to_vector_memory
            and now - self._last_window_summary_ts >= float(self.config.min_seconds_between_window_summaries)
        ):
            self._last_window_summary_ts = now
            summary_txt = (
                "Emotion trend (last "
                f"{int(self.config.window_minutes)}m): "
                f"dominant={state_payload.get('window_dominant_state')}, "
                f"distribution={state_payload.get('window_distribution_percent')}, "
                f"recent={state_payload.get('last_state')}."
            )
            _publish_vector_memory(
                text=summary_txt,
                metadata={
                    "type": "emotion_window_summary",
                    "timestamp": state_payload.get("last_update_utc"),
                    "window_minutes": int(self.config.window_minutes),
                    "window_dominant_state": state_payload.get("window_dominant_state"),
                    "window_samples": state_payload.get("samples"),
                },
            )

        if self._on_update is not None:
            try:
                self._on_update(sample, state_payload)
            except Exception:
                pass

        return sample, state_payload

    def loop_forever(self) -> None:
        # Keep the loop resilient: never crash the assistant.
        interval = max(5.0, float(self.config.interval_seconds))
        while not self._stop:
            started = time.time()
            try:
                self.run_once()
            except Exception:
                pass
            elapsed = time.time() - started
            time.sleep(max(0.0, interval - elapsed))


def start_emotion_detector_thread(
    config: Optional[EmotionDetectorConfig] = None,
    on_update: Optional[Callable[[EmotionSample, Dict[str, Any]], None]] = None,
) -> "Any":
    import threading

    detector = EmotionDetector(config=config, on_update=on_update)
    t = threading.Thread(target=detector.loop_forever, daemon=True)
    t.start()
    return detector
