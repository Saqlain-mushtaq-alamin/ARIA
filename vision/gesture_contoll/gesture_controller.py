"""
vision/gesture_controller.py
────────────────────────────────────────────────────────────
Main webcam loop. Reads hand landmarks from MediaPipe,
classifies gestures (via gesture_mapper), and fires
system actions (cursor move, click, scroll, navigate)
via pyautogui.

Run after calibration:
    python vision/gesture_controller.py

Key design decisions:
  • All distances are normalised by hand_size → scale-invariant.
  • EMA cursor smoothing removes jitter without adding lag.
  • Hysteresis on every pinch (engage < release threshold).
  • Scroll uses relative position delta, not absolute coords.
  • Swipe is detected from palm velocity (landmark 9, MIDDLE_MCP).
  • State machine prevents simultaneous conflicting gestures.
"""

from __future__ import annotations
import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.vision.core.vision_task_running_mode import VisionTaskRunningMode
import pyautogui
import json
import time
import math
import numpy as np
from collections import deque
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from urllib.request import urlretrieve

# Local imports (support both script-run and package import)
try:
    from .gesture_mapper import GESTURES, Thresholds, print_gesture_map
except ImportError:  # pragma: no cover
    from gesture_mapper import GESTURES, Thresholds, print_gesture_map

# ══════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════

CALIBRATION_PATH = Path("vision/calibration_data.json")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
MODEL_PATH = Path("vision/models/hand_landmarker.task")
WEBCAM_INDEX     = 0
FRAME_W, FRAME_H = 1280, 720     # request from camera (may fall back)
FAIL_SAFE        = True          # pyautogui corner-of-screen abort

BaseOptions = mp_python.BaseOptions
HandLandmarker = vision.HandLandmarker
HandLandmarkerOptions = vision.HandLandmarkerOptions

mp_hands = mp.tasks.vision.HandLandmarksConnections
mp_drawing = mp.tasks.vision.drawing_utils


def _ensure_hand_landmarker_model() -> Path:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 0:
        return MODEL_PATH

    print(f"Downloading hand landmarker model to {MODEL_PATH} …")
    urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH

# MediaPipe landmark indices (named for readability)
LM = {
    "WRIST":      0,
    "THUMB_TIP":  4,
    "IDX_MCP":    5,
    "IDX_PIP":    6,
    "IDX_TIP":    8,
    "MID_MCP":    9,
    "MID_TIP":    12,
    "RNG_PIP":    14,
    "RNG_TIP":    16,
    "PINKY_MCP":  17,
    "PINKY_TIP":  20,
}

# ══════════════════════════════════════════════════════════
#  GEOMETRY HELPERS
# ══════════════════════════════════════════════════════════

def dist(a, b) -> float:
    """Euclidean distance between two MediaPipe landmarks."""
    return math.hypot(a.x - b.x, a.y - b.y)

def midpoint_xy(a, b) -> tuple[float, float]:
    return ((a.x + b.x) / 2, (a.y + b.y) / 2)

def hand_size(lms) -> float:
    """
    Reference length = distance from WRIST(0) to MIDDLE_MCP(9).
    Used to normalise all pinch distances so they are
    scale-invariant (i.e. independent of hand-camera distance).
    """
    return max(dist(lms[0], lms[9]), 1e-6)

def norm_dist(lms, a: int, b: int) -> float:
    """Normalised distance between two landmarks."""
    return dist(lms[a], lms[b]) / hand_size(lms)

# ══════════════════════════════════════════════════════════
#  CALIBRATION LOADER
# ══════════════════════════════════════════════════════════

@dataclass
class CalibrationMap:
    x_min: float = 0.15
    x_max: float = 0.85
    y_min: float = 0.10
    y_max: float = 0.90

    @classmethod
    def load(cls, path: Path) -> "CalibrationMap":
        if path.exists():
            with open(path) as f:
                d = json.load(f)
            print(f"✅ Calibration loaded from {path}")
            return cls(d["x_min"], d["x_max"], d["y_min"], d["y_max"])
        print(f"⚠  No calibration file found at {path}. Using defaults.")
        print("   Run  python vision/calibrator.py  for better accuracy.\n")
        return cls()

    def to_screen(self, nx: float, ny: float,
                  sw: int, sh: int) -> tuple[float, float]:
        """Map normalised [0,1] hand coords → screen pixels."""
        sx = (nx - self.x_min) / max(self.x_max - self.x_min, 1e-6)
        sy = (ny - self.y_min) / max(self.y_max - self.y_min, 1e-6)
        sx = max(0.0, min(1.0, sx))
        sy = max(0.0, min(1.0, sy))
        return sx * sw, sy * sh


# ══════════════════════════════════════════════════════════
#  GESTURE STATE MACHINE
# ══════════════════════════════════════════════════════════

class GestureState:
    """
    Tracks which gestures are active and enforces cooldowns/hysteresis.
    """

    def __init__(self) -> None:
        self._last_fire: dict[str, float] = {}

        # Pinch hysteresis: True while currently engaged
        self._pinch_engaged: dict[str, bool] = {
            "LEFT_CLICK":  False,
            "RIGHT_CLICK": False,
            "GO_BACK":     False,
            "GO_FORWARD":  False,
            "SCROLL_MODE": False,
        }

        # Scroll tracking
        self.scroll_ref_y: Optional[float] = None  # y-pos when scroll mode entered
        self.scroll_accumulator: float     = 0.0
        self.scroll_mode_engaged: bool     = False  # with hysteresis
        self.scroll_vel: float             = 0.0    # clicks/sec (signed)

        # Swipe tracking (deque of recent palm x-positions)
        self.palm_x_history: deque[float] = deque(maxlen=15)
        self.last_swipe_time: float       = 0.0

        # Click-drag state
        self.mouse_held: bool = False

    # ── Cooldown guard ────────────────────────────────────

    def can_fire(self, gesture_name: str) -> bool:
        cd = GESTURES[gesture_name].cooldown
        return time.time() - self._last_fire.get(gesture_name, 0) >= cd

    def mark_fired(self, gesture_name: str) -> None:
        self._last_fire[gesture_name] = time.time()

    # ── Pinch with hysteresis ─────────────────────────────

    def check_pinch(self, gesture_name: str, lms,
                    idx_a: int, idx_b: int) -> bool:
        """
        Returns True on the RISING EDGE of a pinch (engage event).
        Uses hysteresis: once engaged, requires PINCH_RELEASE to disengage.
        """
        d = norm_dist(lms, idx_a, idx_b)
        engaged = self._pinch_engaged[gesture_name]

        if not engaged and d < Thresholds.PINCH_ENGAGE:
            self._pinch_engaged[gesture_name] = True
            return True         # rising edge → action fires
        elif engaged and d > Thresholds.PINCH_RELEASE:
            self._pinch_engaged[gesture_name] = False

        return False            # still engaged (hold) or released

    def is_pinch_held(self, gesture_name: str) -> bool:
        return self._pinch_engaged.get(gesture_name, False)

    # ── Swipe detection ───────────────────────────────────

    def update_swipe(self, palm_x: float) -> Optional[str]:
        """
        Feed current palm x into history. Returns "SWIPE_LEFT",
        "SWIPE_RIGHT", or None.
        """
        self.palm_x_history.append(palm_x)

        if len(self.palm_x_history) < Thresholds.SWIPE_HOLD_FRAMES + 1:
            return None

        if time.time() - self.last_swipe_time < Thresholds.SWIPE_COOLDOWN:
            return None

        # Average velocity over recent frames
        recent = list(self.palm_x_history)
        n      = min(Thresholds.SWIPE_HOLD_FRAMES, len(recent))
        vx     = (recent[-1] - recent[-n]) / n    # normalised units/frame

        # Camera is mirrored: moving right in frame = moving left on screen
        if vx > Thresholds.SWIPE_VEL_THRESHOLD:
            self.last_swipe_time = time.time()
            self.palm_x_history.clear()
            return "SWIPE_LEFT"
        elif vx < -Thresholds.SWIPE_VEL_THRESHOLD:
            self.last_swipe_time = time.time()
            self.palm_x_history.clear()
            return "SWIPE_RIGHT"

        return None


# ══════════════════════════════════════════════════════════
#  HUD DRAWING
# ══════════════════════════════════════════════════════════

GESTURE_COLORS = {
    "IDLE":        (120, 120, 120),
    "LEFT_CLICK":  (0, 220, 100),
    "RIGHT_CLICK": (0, 120, 255),
    "SCROLL_MODE": (255, 180, 0),
    "GO_BACK":     (180, 0, 255),
    "GO_FORWARD":  (0, 180, 255),
    "SWIPE_LEFT":  (255, 60, 60),
    "SWIPE_RIGHT": (60, 60, 255),
    "CLICK_DRAG":  (0, 255, 220),
}

def draw_hud(frame: np.ndarray, active_gesture: str,
             fps: float, scroll_active: bool) -> None:
    h, w = frame.shape[:2]
    color = GESTURE_COLORS.get(active_gesture, (200, 200, 200))

    # ── Top bar ───────────────────────────────────────────
    cv2.rectangle(frame, (0, 0), (w, 52), (12, 12, 18), -1)

    # ── Active gesture label ──────────────────────────────
    label = active_gesture.replace("_", " ")
    cv2.putText(frame, label, (14, 36),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)

    # ── FPS ───────────────────────────────────────────────
    cv2.putText(frame, f"{fps:.0f} FPS", (w - 110, 36),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (160, 160, 160), 1)

    # ── Scroll indicator ──────────────────────────────────
    if scroll_active:
        cv2.putText(frame, "SCROLL MODE ↕", (w // 2 - 90, 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 180, 0), 2)

    # ── Gesture legend (bottom bar) ───────────────────────
    legends = [
        ("8→cursor", (200, 200, 200)),
        ("4+12=LClick", GESTURE_COLORS["LEFT_CLICK"]),
        ("4+16=RClick", GESTURE_COLORS["RIGHT_CLICK"]),
        ("8+12=AutoScroll", GESTURE_COLORS["SCROLL_MODE"]),
        ("4+17=Back", GESTURE_COLORS["GO_BACK"]),
        ("4+14=Fwd", GESTURE_COLORS["GO_FORWARD"]),
        ("Palm swipe=Nav", GESTURE_COLORS["SWIPE_LEFT"]),
    ]
    bar_y = h - 26
    cv2.rectangle(frame, (0, bar_y - 6), (w, h), (12, 12, 18), -1)
    x_cursor = 10
    for text, col in legends:
        cv2.putText(frame, text, (x_cursor, bar_y + 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, col, 1)
        x_cursor += len(text) * 9 + 14

    # ── Gesture indicator ring (top-right) ────────────────
    if active_gesture != "IDLE":
        cv2.circle(frame, (w - 28, 28), 16, color, -1)
        cv2.circle(frame, (w - 28, 28), 18, (255, 255, 255), 1)


# ══════════════════════════════════════════════════════════
#  MAIN CONTROLLER LOOP
# ══════════════════════════════════════════════════════════

def run_controller() -> None:
    # ── Setup ─────────────────────────────────────────────
    pyautogui.FAILSAFE = FAIL_SAFE
    pyautogui.PAUSE    = 0           # disable built-in pause for low latency

    screen_w, screen_h = pyautogui.size()
    calib = CalibrationMap.load(CALIBRATION_PATH)
    state = GestureState()

    print_gesture_map()
    print("🚀 Gesture Controller starting…")
    print("   Move index finger (8) to control cursor.")
    print("   Press  Q  in the webcam window to quit.\n")

    # ── MediaPipe Tasks HandLandmarker ────────────────────
    model_path = _ensure_hand_landmarker_model()
    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=VisionTaskRunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.72,
        min_hand_presence_confidence=0.72,
        min_tracking_confidence=0.72,
    )
    landmarker = HandLandmarker.create_from_options(options)

    # ── Camera ────────────────────────────────────────────
    cap = cv2.VideoCapture(WEBCAM_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    cap.set(cv2.CAP_PROP_FPS, 60)
    if not cap.isOpened():
        raise RuntimeError("Cannot open webcam.")

    # ── State variables ───────────────────────────────────
    cursor_x: float = screen_w / 2   # EMA smoothed cursor position
    cursor_y: float = screen_h / 2
    prev_cx:  float = cursor_x
    prev_cy:  float = cursor_y

    fps_deque: deque[float] = deque(maxlen=30)
    t_prev = time.time()
    stream_t0 = t_prev

    active_gesture = "IDLE"

    # ── Main loop ─────────────────────────────────────────
    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        frame = cv2.flip(frame, 1)    # mirror so it feels natural
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # ── FPS ───────────────────────────────────────────
        t_now = time.time()
        fps_deque.append(1.0 / max(t_now - t_prev, 1e-6))
        t_prev = t_now
        fps = float(np.mean(fps_deque))
        dt = 1.0 / max(fps, 1e-6)

        timestamp_ms = int((t_now - stream_t0) * 1000)
        result = landmarker.detect_for_video(mp_image, timestamp_ms)

        # ── No hand detected ──────────────────────────────
        if not result.hand_landmarks:
            active_gesture = "IDLE"
            state.scroll_ref_y = None
            if state.mouse_held:
                pyautogui.mouseUp()
                state.mouse_held = False
            draw_hud(frame, active_gesture, fps, False)
            cv2.imshow("Hand Gesture Controller  (Q to quit)", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            continue

        # ── Extract landmarks ─────────────────────────────
        hand_landmarks = result.hand_landmarks[0]
        lms = hand_landmarks

        mp_drawing.draw_landmarks(frame, hand_landmarks, mp_hands.HAND_CONNECTIONS)

        # ── 1. CURSOR MOVEMENT (index tip 8) ─────────────
        tip8 = lms[LM["IDX_TIP"]]
        tx_raw, ty_raw = calib.to_screen(tip8.x, tip8.y, screen_w, screen_h)

        # EMA smoothing
        alpha   = Thresholds.CURSOR_EMA
        cursor_x = alpha * tx_raw + (1 - alpha) * cursor_x
        cursor_y = alpha * ty_raw + (1 - alpha) * cursor_y

        # Deadzone: only move if above pixel threshold
        dx = cursor_x - prev_cx
        dy = cursor_y - prev_cy
        if abs(dx) > Thresholds.CURSOR_DEADZONE_PX or \
           abs(dy) > Thresholds.CURSOR_DEADZONE_PX:
            pyautogui.moveTo(cursor_x, cursor_y)
            prev_cx, prev_cy = cursor_x, cursor_y

        # ── 2. SCROLL MODE (landmark 8 + 12 pinched) ─────
        scroll_d = norm_dist(lms, LM["IDX_TIP"], LM["MID_TIP"])
        if not state.scroll_mode_engaged and scroll_d < Thresholds.SCROLL_PINCH_ENGAGE:
            state.scroll_mode_engaged = True
            state.scroll_ref_y = None
            state.scroll_accumulator = 0.0
            state.scroll_vel = 0.0
        elif state.scroll_mode_engaged and scroll_d > Thresholds.SCROLL_PINCH_RELEASE:
            state.scroll_mode_engaged = False
            state.scroll_ref_y = None
            state.scroll_accumulator = 0.0
            state.scroll_vel = 0.0

        scroll_active = state.scroll_mode_engaged

        if scroll_active:
            # Midpoint y of index+middle tip pair
            my = (lms[LM["IDX_TIP"]].y + lms[LM["MID_TIP"]].y) / 2

            # Anchor on entry. While held, scroll speed depends on how far the
            # hand is from the anchor (like “holding scroll” and moving).
            if state.scroll_ref_y is None:
                state.scroll_ref_y = my
                state.scroll_accumulator = 0.0
                state.scroll_vel = 0.0

            active_gesture = "SCROLL_MODE"

            # Convert offset to a scroll velocity (clicks/sec).
            offset_y = state.scroll_ref_y - my  # up = positive
            if abs(offset_y) <= Thresholds.SCROLL_DEADZONE:
                target_vel = 0.0
            else:
                target_vel = offset_y * Thresholds.SCROLL_CLICKS_PER_SEC

            # Clamp velocity.
            max_v = float(Thresholds.SCROLL_MAX_CLICKS_SEC)
            if target_vel > max_v:
                target_vel = max_v
            elif target_vel < -max_v:
                target_vel = -max_v

            # Smooth velocity to reduce jitter.
            a = float(Thresholds.SCROLL_VEL_EMA)
            state.scroll_vel = a * target_vel + (1.0 - a) * state.scroll_vel

            # Emit wheel scroll events continuously while in scroll mode.
            state.scroll_accumulator += state.scroll_vel * dt
            clicks = int(state.scroll_accumulator)
            if clicks != 0:
                pyautogui.scroll(clicks)
                state.scroll_accumulator -= clicks

        else:
            # Reset scroll state when not in scroll mode
            state.scroll_ref_y      = None
            state.scroll_accumulator = 0.0

            if state.mouse_held and not state.is_pinch_held("LEFT_CLICK"):
                pyautogui.mouseUp()
                state.mouse_held = False

            # ── 3. LEFT CLICK (thumb 4 + middle tip 12) ──
            if state.check_pinch("LEFT_CLICK", lms,
                                  LM["THUMB_TIP"], LM["MID_TIP"]):
                if state.can_fire("LEFT_CLICK"):
                    pyautogui.click(button="left")
                    state.mark_fired("LEFT_CLICK")
                    active_gesture = "LEFT_CLICK"
                    print(f"  🖱️  Left Click  @({cursor_x:.0f}, {cursor_y:.0f})")

            # ── 4. RIGHT CLICK (thumb 4 + ring tip 16) ───
            elif state.check_pinch("RIGHT_CLICK", lms,
                                    LM["THUMB_TIP"], LM["RNG_TIP"]):
                if state.can_fire("RIGHT_CLICK"):
                    pyautogui.click(button="right")
                    state.mark_fired("RIGHT_CLICK")
                    active_gesture = "RIGHT_CLICK"
                    print(f"  🖱️  Right Click @({cursor_x:.0f}, {cursor_y:.0f})")

            # ── 5. GO BACK (thumb 4 + index MCP 5) ───────
            elif state.check_pinch("GO_BACK", lms,
                                    LM["THUMB_TIP"], LM["PINKY_MCP"]):
                if state.can_fire("GO_BACK"):
                    GESTURES["GO_BACK"].execute()
                    state.mark_fired("GO_BACK")
                    active_gesture = "GO_BACK"
                    print("  ◀️  Go Back")

            # ── 6. GO FORWARD (thumb 4 + ring PIP 14) ────
            elif state.check_pinch("GO_FORWARD", lms,
                                    LM["THUMB_TIP"], LM["RNG_PIP"]):
                if state.can_fire("GO_FORWARD"):
                    GESTURES["GO_FORWARD"].execute()
                    state.mark_fired("GO_FORWARD")
                    active_gesture = "GO_FORWARD"
                    print("  ▶️  Go Forward")

            else:
                active_gesture = "IDLE"

        # ── 6. SWIPE (palm velocity: landmark 9) ──────────
        palm9 = lms[LM["MID_MCP"]]
        swipe = state.update_swipe(palm9.x)
        if swipe and state.can_fire(swipe):
            GESTURES[swipe].execute()
            state.mark_fired(swipe)
            active_gesture = swipe
            print(f"  {'👈' if swipe == 'SWIPE_LEFT' else '👉'}  {swipe}")

        # ── Draw ─────────────────────────────────────────
        # Highlight key landmarks
        fh, fw = frame.shape[:2]
        for idx, (col, r) in {
            LM["IDX_TIP"]:  ((0, 230, 100), 10),
            LM["THUMB_TIP"]:((255, 80, 80),  8),
            LM["MID_TIP"]:  ((255, 200, 0),  8),
            LM["RNG_TIP"]:  ((80, 80, 255),  8),
            LM["RNG_PIP"]:  ((0, 180, 255),  7),
            LM["PINKY_MCP"]:((180, 0, 255),  7),
        }.items():
            lm = lms[idx]
            cx, cy = int(lm.x * fw), int(lm.y * fh)
            cv2.circle(frame, (cx, cy), r, col, -1)
            cv2.putText(frame, str(idx), (cx + 4, cy - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, col, 1)

        draw_hud(frame, active_gesture, fps, scroll_active)
        cv2.imshow("Hand Gesture Controller  (Q to quit)", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    # ── Cleanup ───────────────────────────────────────────
    if state.mouse_held:
        pyautogui.mouseUp()
    cap.release()
    cv2.destroyAllWindows()
    landmarker.close()
    print("\n👋 Gesture Controller stopped.")


# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    run_controller()
