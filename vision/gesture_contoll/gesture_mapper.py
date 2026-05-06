"""
vision/gesture_mapper.py
────────────────────────────────────────────────────────────
Single source-of-truth for every gesture, its landmark pair,
detection thresholds, cooldowns, and the system action it fires.

MediaPipe landmark reference (from the image):
  0  WRIST               5  INDEX_FINGER_MCP
  4  THUMB_TIP           6  INDEX_FINGER_PIP
  8  INDEX_FINGER_TIP    9  MIDDLE_FINGER_MCP
  12 MIDDLE_FINGER_TIP  13  RING_FINGER_MCP
  16 RING_FINGER_TIP    20  PINKY_TIP
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple
import pyautogui
import subprocess
import platform

# ═══════════════════════════════════════════════════════════
#  TUNABLE THRESHOLDS
# ═══════════════════════════════════════════════════════════

class Thresholds:
    # ── Pinch detection ──────────────────────────────────
    # Distances are normalised by hand_size (wrist→middle_MCP distance)
    # so they scale automatically with how far the hand is from the camera.

    PINCH_ENGAGE  = 0.08    # fraction of hand_size → gesture fires
    PINCH_RELEASE = 0.12    # hysteresis: gesture releases only above this

    # ── Scroll mode (index 8 + middle 12 pinched together) ──
    SCROLL_PINCH_THRESH = 0.07    # how close 8 & 12 must be
    SCROLL_SENSITIVITY  = 600     # lower = faster scroll
    SCROLL_DEADZONE     = 0.008   # ignore tiny jitter

    # ── Swipe (whole-hand lateral velocity) ──────────────
    SWIPE_VEL_THRESHOLD = 0.40    # normalised units/frame  (tune per user)
    SWIPE_HOLD_FRAMES   = 4       # frames velocity must be sustained
    SWIPE_COOLDOWN      = 0.90    # seconds between swipe events

    # ── Cursor ───────────────────────────────────────────
    CURSOR_EMA         = 0.20     # Exponential-Moving-Average weight
    #                               0.10 = buttery smooth, 0.40 = responsive
    CURSOR_DEADZONE_PX = 4        # pixel jitter suppression

    # ── Cooldowns (seconds) ──────────────────────────────
    LEFT_CLICK_COOLDOWN  = 0.38
    RIGHT_CLICK_COOLDOWN = 0.45
    GO_BACK_COOLDOWN     = 0.90


# ═══════════════════════════════════════════════════════════
#  GESTURE DEFINITIONS
# ═══════════════════════════════════════════════════════════

@dataclass
class Gesture:
    """Describes a single gesture: what triggers it and what it does."""
    name:        str
    description: str
    emoji:       str
    landmarks:   Tuple[int, ...]   # landmark indices involved
    action_type: str               # "click" | "move" | "scroll" | "navigate" | "hold"
    cooldown:    float = 0.0
    # Optional: override the execute() method via a callable
    _executor:   Optional[Callable] = field(default=None, repr=False)

    def execute(self, *args, **kwargs) -> None:
        if self._executor:
            self._executor(*args, **kwargs)


# ── Action helpers ────────────────────────────────────────

def _go_back() -> None:
    sys = platform.system()
    if sys == "Darwin":           # macOS
        pyautogui.hotkey("command", "[")
    elif sys == "Windows":
        pyautogui.hotkey("alt", "left")
    else:                         # Linux
        pyautogui.hotkey("alt", "left")

def _go_forward() -> None:
    sys = platform.system()
    if sys == "Darwin":
        pyautogui.hotkey("command", "]")
    elif sys == "Windows":
        pyautogui.hotkey("alt", "right")
    else:
        pyautogui.hotkey("alt", "right")

def _left_click() -> None:
    pyautogui.click(button="left")

def _right_click() -> None:
    pyautogui.click(button="right")

def _scroll_up(amount: int = 3) -> None:
    pyautogui.scroll(amount)

def _scroll_down(amount: int = 3) -> None:
    pyautogui.scroll(-amount)


# ── Gesture registry ─────────────────────────────────────

GESTURES: dict[str, Gesture] = {

    # ── 1. CURSOR MOVE ────────────────────────────────────
    "CURSOR_MOVE": Gesture(
        name        = "Cursor Move",
        description = "Index fingertip (8) position maps to screen cursor.",
        emoji       = "👆",
        landmarks   = (8,),
        action_type = "move",
        cooldown    = 0.0,
    ),

    # ── 2. LEFT CLICK ─────────────────────────────────────
    "LEFT_CLICK": Gesture(
        name        = "Left Click",
        description = "Thumb tip (4) pinches Middle finger tip (12).",
        emoji       = "🤏",
        landmarks   = (4, 12),
        action_type = "click",
        cooldown    = Thresholds.LEFT_CLICK_COOLDOWN,
        _executor   = lambda: _left_click(),
    ),

    # ── 3. RIGHT CLICK ────────────────────────────────────
    "RIGHT_CLICK": Gesture(
        name        = "Right Click",
        description = "Thumb tip (4) pinches Ring finger tip (16).",
        emoji       = "🤙",
        landmarks   = (4, 16),
        action_type = "click",
        cooldown    = Thresholds.RIGHT_CLICK_COOLDOWN,
        _executor   = lambda: _right_click(),
    ),

    # ── 4. SCROLL MODE ────────────────────────────────────
    "SCROLL_MODE": Gesture(
        name        = "Scroll (pinch Index+Middle, swipe up/down)",
        description = (
            "When Index tip (8) and Middle tip (12) are pinched together, "
            "vertical movement of that pair scrolls the page. "
            "Move UP to scroll up, DOWN to scroll down. "
            "Natural swipe — like on a phone screen."
        ),
        emoji       = "☝️🖕",
        landmarks   = (8, 12),
        action_type = "scroll",
        cooldown    = 0.0,
    ),

    # ── 5. CLICK + DRAG SCROLL (multi-select) ─────────────
    "CLICK_DRAG_SCROLL": Gesture(
        name        = "Click-Drag Scroll (multi-select)",
        description = (
            "Hold Left Click (thumb 4 ↔ middle 12 pinch HELD) and "
            "ALSO pinch Index+Middle tips together and swipe up/down. "
            "This holds mouseDown while scrolling — rare but powerful."
        ),
        emoji       = "🖱️↕",
        landmarks   = (4, 12, 8),
        action_type = "hold",
        cooldown    = 0.0,
    ),

    # ── 6. GO BACK ────────────────────────────────────────
    "GO_BACK": Gesture(
        name        = "Go Back",
        description = "Thumb tip (4) touches Index MCP (5). Browser/app back.",
        emoji       = "◀️",
        landmarks   = (4, 5),
        action_type = "navigate",
        cooldown    = Thresholds.GO_BACK_COOLDOWN,
        _executor   = lambda: _go_back(),
    ),

    # ── 7. SWIPE LEFT (go back / prev) ────────────────────
    "SWIPE_LEFT": Gesture(
        name        = "Swipe Left",
        description = "Whole hand moves left rapidly → browser back / previous.",
        emoji       = "👈",
        landmarks   = (0, 9),    # wrist + palm base for palm velocity
        action_type = "navigate",
        cooldown    = Thresholds.SWIPE_COOLDOWN,
        _executor   = lambda: _go_back(),
    ),

    # ── 8. SWIPE RIGHT (go forward / next) ────────────────
    "SWIPE_RIGHT": Gesture(
        name        = "Swipe Right",
        description = "Whole hand moves right rapidly → browser forward / next.",
        emoji       = "👉",
        landmarks   = (0, 9),
        action_type = "navigate",
        cooldown    = Thresholds.SWIPE_COOLDOWN,
        _executor   = lambda: _go_forward(),
    ),
}


# ── Convenience accessors ─────────────────────────────────

def get(name: str) -> Optional[Gesture]:
    return GESTURES.get(name)

def print_gesture_map() -> None:
    """Pretty-print the full gesture map to the terminal."""
    width = 60
    print("\n" + "═" * width)
    print("  GESTURE MAP")
    print("═" * width)
    for key, g in GESTURES.items():
        print(f"  {g.emoji}  {g.name}")
        print(f"     Landmarks : {g.landmarks}")
        print(f"     Action    : {g.action_type}  (cooldown {g.cooldown}s)")
        print(f"     {g.description[:55]}")
        print()
    print("═" * width + "\n")


if __name__ == "__main__":
    print_gesture_map()
