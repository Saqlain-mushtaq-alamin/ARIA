"""
vision/calibrator.py
────────────────────────────────────────────────────────────
30-second calibration session that maps your hand's movement
range in camera space → screen coordinate space.

Run this FIRST before gesture_controller.py
    python vision/calibrator.py
"""

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.vision.core.vision_task_running_mode import VisionTaskRunningMode
import json
import time
import numpy as np
from pathlib import Path
from urllib.request import urlretrieve

# ── Config ────────────────────────────────────────────────
CALIBRATION_DURATION = 30          # seconds
OUTPUT_PATH = Path("vision/calibration_data.json")
LANDMARK_TIP = 8                   # INDEX_FINGER_TIP drives the cursor

# MediaPipe Tasks model (downloaded on-demand)
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
MODEL_PATH = Path("vision/models/hand_landmarker.task")

BaseOptions = mp_python.BaseOptions
HandLandmarker = vision.HandLandmarker
HandLandmarkerOptions = vision.HandLandmarkerOptions

# ── MediaPipe drawing helpers (Tasks API) ─────────────────
mp_hands = mp.tasks.vision.HandLandmarksConnections
mp_drawing = mp.tasks.vision.drawing_utils
mp_styles = mp.tasks.vision.drawing_styles


def _ensure_hand_landmarker_model() -> Path:
    """Ensure the hand landmarker `.task` model exists locally."""
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 0:
        return MODEL_PATH

    print(f"Downloading hand landmarker model to {MODEL_PATH} …")
    urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def _draw_corner_targets(frame: np.ndarray) -> None:
    """Draw visual guides at all four corners + edges."""
    h, w = frame.shape[:2]
    color = (80, 200, 255)
    radius = 25
    positions = [
        (40, 40), (w // 2, 40), (w - 40, 40),
        (40, h // 2), (w - 40, h // 2),
        (40, h - 40), (w // 2, h - 40), (w - 40, h - 40),
    ]
    for x, y in positions:
        cv2.circle(frame, (x, y), radius, color, 2)
        cv2.circle(frame, (x, y), 4, color, -1)
        # Cross-hair
        cv2.line(frame, (x - radius, y), (x + radius, y), color, 1)
        cv2.line(frame, (x, y - radius), (x, y + radius), color, 1)


def _draw_hud(frame: np.ndarray, elapsed: float, sample_count: int) -> None:
    """Draw calibration progress HUD."""
    h, w = frame.shape[:2]
    remaining = max(0, CALIBRATION_DURATION - elapsed)
    progress  = min(1.0, elapsed / CALIBRATION_DURATION)

    # Dark top bar
    cv2.rectangle(frame, (0, 0), (w, 70), (15, 15, 15), -1)

    # Progress bar background
    bar_x, bar_y, bar_w, bar_h = 10, 50, w - 20, 10
    cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h),
                  (50, 50, 50), -1)
    fill_w = int(bar_w * progress)
    bar_color = (0, 220, 150) if progress < 0.8 else (0, 180, 255)
    cv2.rectangle(frame, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h),
                  bar_color, -1)

    cv2.putText(frame, f"CALIBRATING  {remaining:.1f}s  |  {sample_count} samples",
                (10, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)

    # Centre instruction (fades after first 5 s)
    if elapsed < 5:
        alpha_text = "Move INDEX FINGER TIP to every corner & edge!"
        cv2.putText(frame, alpha_text, (w // 2 - 280, h // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 200), 2)


def run_calibration() -> dict | None:
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Cannot open webcam — check your camera index.")

    model_path = _ensure_hand_landmarker_model()
    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=VisionTaskRunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.75,
        min_hand_presence_confidence=0.75,
        min_tracking_confidence=0.75,
    )
    landmarker = HandLandmarker.create_from_options(options)

    x_coords: list[float] = []
    y_coords: list[float] = []
    t0 = time.time()

    print("\n╔══════════════════════════════════════════╗")
    print("║         HAND GESTURE — CALIBRATION       ║")
    print("╠══════════════════════════════════════════╣")
    print("║  Move your INDEX FINGER TIP (landmark 8) ║")
    print("║  to ALL corners, edges, and centre of    ║")
    print("║  your screen for 30 seconds.             ║")
    print("║  Press  Q  to abort.                     ║")
    print("╚══════════════════════════════════════════╝\n")

    while True:
        elapsed   = time.time() - t0
        remaining = CALIBRATION_DURATION - elapsed

        if remaining <= 0:
            break

        ret, frame = cap.read()
        if not ret:
            continue

        frame = cv2.flip(frame, 1)              # mirror for natural feel
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = int(elapsed * 1000)
        result = landmarker.detect_for_video(mp_image, timestamp_ms)

        if result.hand_landmarks:
            for hand_landmarks in result.hand_landmarks:
                mp_drawing.draw_landmarks(
                    frame,
                    hand_landmarks,
                    mp_hands.HAND_CONNECTIONS,
                    mp_styles.get_default_hand_landmarks_style(),
                    mp_styles.get_default_hand_connections_style(),
                )

                tip = hand_landmarks[LANDMARK_TIP]
                x_coords.append(tip.x)
                y_coords.append(tip.y)

                # Highlight landmark 8
                h, w = frame.shape[:2]
                cx, cy = int(tip.x * w), int(tip.y * h)
                cv2.circle(frame, (cx, cy), 14, (0, 230, 100), -1)
                cv2.circle(frame, (cx, cy), 18, (255, 255, 255), 2)
                cv2.putText(
                    frame,
                    "8",
                    (cx - 5, cy + 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 0, 0),
                    2,
                )

        _draw_corner_targets(frame)
        _draw_hud(frame, elapsed, len(x_coords))
        cv2.imshow("Hand Gesture — Calibration (press Q to quit)", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("Calibration aborted.")
            cap.release()
            cv2.destroyAllWindows()
            landmarker.close()
            return None

    cap.release()
    cv2.destroyAllWindows()
    landmarker.close()

    if len(x_coords) < 30:
        print("⚠  Too few samples collected. Please retry calibration.")
        return None

    # Use 5th–95th percentile to robustly ignore outliers
    calibration = {
        "x_min":   float(np.percentile(x_coords, 5)),
        "x_max":   float(np.percentile(x_coords, 95)),
        "y_min":   float(np.percentile(y_coords, 5)),
        "y_max":   float(np.percentile(y_coords, 95)),
        "samples": len(x_coords),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(calibration, f, indent=2)

    print("\n✅ Calibration complete!")
    print(f"   Samples : {len(x_coords)}")
    print(f"   X range : {calibration['x_min']:.4f} → {calibration['x_max']:.4f}")
    print(f"   Y range : {calibration['y_min']:.4f} → {calibration['y_max']:.4f}")
    print(f"   Saved   : {OUTPUT_PATH}\n")

    return calibration


if __name__ == "__main__":
    run_calibration()
