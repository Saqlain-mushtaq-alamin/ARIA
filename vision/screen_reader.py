"""
vision/screen_reader.py
=======================
Screen context awareness using LLaVA (via Ollama) running locally.

What it does
------------
• Every 60 s (configurable) takes a screenshot
• Sends it to LLaVA:7b via the local Ollama REST API
• Extracts an activity summary — the *image is never saved to disk*
• Keeps a rolling window of recent summaries
• Feeds `get_screen_context_block()` → injected into `_build_context_prompt()`
• Detects stuck/frustration patterns and triggers proactive suggestions

Priority / synchronisation
---------------------------
The main LLM (Ollama/Mistral/etc.) and LLaVA share the same GPU/CPU.
To avoid thrashing hardware, LLaVA ALWAYS yields to the main LLM:

    1. `agent.py` calls `notify_llm_start()` before every Ollama call
    2. `agent.py` calls `notify_llm_done()`  after  every Ollama call
    3. The screen reader thread blocks on `_MAIN_LLM_BUSY` before its
       LLaVA inference call — it waits until the main LLM is idle

Quick start
-----------
    # In vision/screen_reader.py (this file) — no config needed
    # In agent.py — see "AGENT.PY WIRING" section at the bottom of this file.

    from vision.screen_reader import (
        start_screen_reader,
        get_screen_context_block,
        notify_llm_start,
        notify_llm_done,
        llm_busy_context,       # context-manager alternative
    )

Env-var knobs
-------------
    SCREEN_READER_DISABLED        1 → disable entirely (default: 0)
    SCREEN_READER_INTERVAL        seconds between captures (default: 60)
    SCREEN_READER_OLLAMA_URL      Ollama base URL (default: http://localhost:11434)
    SCREEN_READER_MODEL           vision model to use (default: llava:7b)
    SCREEN_READER_HISTORY         number of summaries to keep (default: 5)
    SCREEN_READER_STUCK_MINUTES   minutes on same activity before nudge (default: 20)
    SCREEN_READER_DEBUG           1 → verbose logging (default: 0)
"""

from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Deque, Generator, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Env-var helpers
# ─────────────────────────────────────────────────────────────────────────────

def _env(key: str, default: str) -> str:
    return os.environ.get(key, default).strip()

def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "")
    return v.strip().lower() in {"1", "true", "yes", "on"} if v else default

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

# ─────────────────────────────────────────────────────────────────────────────
# Main-LLM priority synchronisation
# ─────────────────────────────────────────────────────────────────────────────

# Set when the main LLM is actively inferring. Screen reader blocks on this.
_MAIN_LLM_BUSY = threading.Event()
_MAIN_LLM_BUSY.clear()   # starts as "not busy"

_LLM_BUSY_LOCK = threading.Lock()
_LLM_BUSY_COUNT = 0       # re-entrant counter so nested calls work


def notify_llm_start() -> None:
    """
    Call this in agent.py BEFORE every Ollama / LLM inference call.
    Thread-safe; re-entrant (nested calls are counted).
    """
    global _LLM_BUSY_COUNT
    with _LLM_BUSY_LOCK:
        _LLM_BUSY_COUNT += 1
        _MAIN_LLM_BUSY.set()


def notify_llm_done() -> None:
    """
    Call this in agent.py AFTER every Ollama / LLM inference call.
    Thread-safe; re-entrant.
    """
    global _LLM_BUSY_COUNT
    with _LLM_BUSY_LOCK:
        _LLM_BUSY_COUNT = max(0, _LLM_BUSY_COUNT - 1)
        if _LLM_BUSY_COUNT == 0:
            _MAIN_LLM_BUSY.clear()


@contextmanager
def llm_busy_context() -> Generator[None, None, None]:
    """
    Context manager alternative — wraps a block that uses the main LLM.

    Usage in agent.py::

        from vision.screen_reader import llm_busy_context

        with llm_busy_context():
            result = ollama_client.chat(...)   # LLaVA waits here
    """
    notify_llm_start()
    try:
        yield
    finally:
        notify_llm_done()


def _wait_for_main_llm_idle(timeout: float = 120.0) -> bool:
    """
    Block until the main LLM is idle.
    Returns True if idle, False if we timed out.
    """
    if not _MAIN_LLM_BUSY.is_set():
        return True
    deadline = time.monotonic() + timeout
    while _MAIN_LLM_BUSY.is_set():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.25)
    return True

# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScreenSummary:
    """One captured moment of screen activity."""
    timestamp_utc: str
    activity: str          # e.g. "coding in VS Code — Python file open"
    app_hint: str          # e.g. "VS Code"
    emotional_hint: str    # e.g. "focused", "frustrated", "idle"
    raw_llava: str         # full LLaVA response (for debugging)


# ─────────────────────────────────────────────────────────────────────────────
# Screenshot helpers (no disk write)
# ─────────────────────────────────────────────────────────────────────────────

def _capture_screenshot_b64() -> str:
    """
    Take a full-screen screenshot and return it as a base64-encoded PNG string.
    The raw bytes are NEVER written to disk.
    Supports Windows (mss / PIL), macOS (PIL), Linux (PIL/scrot via PIL).
    """
    try:
        import mss  # type: ignore
        import mss.tools  # type: ignore
        with mss.mss() as sct:
            monitor = sct.monitors[0]   # full virtual screen
            sct_img = sct.grab(monitor)
            # Convert to PNG bytes in-memory
            png_bytes = mss.tools.to_png(sct_img.rgb, sct_img.size)
            return base64.b64encode(png_bytes).decode("ascii")
    except ImportError:
        pass

    # Fallback: PIL/Pillow
    try:
        from PIL import ImageGrab  # type: ignore
        img = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        raise RuntimeError(f"Screenshot failed (install mss or Pillow): {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# LLaVA inference via Ollama REST API
# ─────────────────────────────────────────────────────────────────────────────

_ANALYSIS_PROMPT = """You are a desktop activity analyst. I'm showing you a screenshot of my computer screen.

Answer these 4 questions concisely (one line each):
1. ACTIVITY: What am I actively doing? (e.g. "writing Python code in VS Code", "browsing Reddit", "watching YouTube", "reading a PDF about ML")
2. APP: What is the primary application visible?
3. MOOD: Based on window layout and content, how does the work feel? (focused / distracted / stuck / idle / productive / relaxed)
4. DETAIL: Any specific detail useful for an AI assistant? (error messages, document title, search query, etc.)

Format your response EXACTLY as JSON:
{
  "activity": "...",
  "app": "...",
  "mood": "...",
  "detail": "..."
}

Be concise. Do not explain. Return only the JSON object."""


def _query_llava(image_b64: str, ollama_url: str, model: str) -> dict:
    """Send the screenshot to LLaVA via Ollama and parse the JSON response."""
    import urllib.request

    payload = {
        "model": model,
        "prompt": _ANALYSIS_PROMPT,
        "images": [image_b64],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,    # deterministic — we want facts, not creativity
            "num_predict": 200,    # keep responses short
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")

    outer = json.loads(raw)
    response_text = outer.get("response", "")

    # Strip markdown fences if present
    response_text = response_text.strip()
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]
    response_text = response_text.strip()

    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        # Graceful fallback — parse what we can
        return {
            "activity": response_text[:120] if response_text else "unknown",
            "app": "unknown",
            "mood": "unknown",
            "detail": "",
        }


def _analyse_screen(ollama_url: str, model: str, debug: bool) -> Optional[ScreenSummary]:
    """
    Full pipeline: screenshot → LLaVA → ScreenSummary.
    Image bytes are discarded immediately after the HTTP call.
    Returns None on any error.
    """
    try:
        if debug:
            print("[screen_reader] capturing screenshot...")
        image_b64 = _capture_screenshot_b64()

        if debug:
            print(f"[screen_reader] sending to {model} @ {ollama_url}...")
        result = _query_llava(image_b64, ollama_url, model)
        del image_b64   # explicitly free memory — no disk trace

        summary = ScreenSummary(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            activity=str(result.get("activity", "unknown")),
            app_hint=str(result.get("app", "unknown")),
            emotional_hint=str(result.get("mood", "unknown")),
            raw_llava=json.dumps(result),
        )

        if debug:
            print(
                f"[screen_reader] → activity='{summary.activity}'"
                f"  app='{summary.app_hint}'  mood='{summary.emotional_hint}'"
            )
        return summary

    except Exception as exc:
        if debug:
            print(f"[screen_reader] analysis failed: {exc}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Rolling context store
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _ScreenContextState:
    summaries: Deque[ScreenSummary] = field(default_factory=lambda: deque(maxlen=5))
    lock: threading.Lock = field(default_factory=threading.Lock)
    proactive_callback: Optional[Callable[[str], None]] = None
    last_nudge_time: float = 0.0
    nudge_cooldown_s: float = 600.0   # don't nudge more often than every 10 min


_STATE = _ScreenContextState()


def _update_maxlen(n: int) -> None:
    with _STATE.lock:
        old = list(_STATE.summaries)
        _STATE.summaries = deque(old, maxlen=n)


def get_recent_summaries(n: int = 3) -> list[ScreenSummary]:
    """Return the N most recent screen summaries (thread-safe)."""
    with _STATE.lock:
        return list(_STATE.summaries)[-n:]


def get_screen_context_block() -> str:
    """
    Return a compact context string for injection into `_build_context_prompt()`.

    Example output::

        Screen context (last 3 captures):
        • 21:04 UTC — coding in VS Code — Python file open (VS Code / focused)
        • 21:09 UTC — same activity — still in VS Code (VS Code / stuck)
        • 21:14 UTC — same activity — stuck on same error (VS Code / frustrated)

    Returns an empty string if no summaries are available yet.
    """
    summaries = get_recent_summaries(3)
    if not summaries:
        return ""

    lines = ["Screen context (last captures):"]
    for s in summaries:
        try:
            ts = datetime.fromisoformat(s.timestamp_utc).strftime("%H:%M UTC")
        except Exception:
            ts = s.timestamp_utc[:16]
        line = f"  • {ts} — {s.activity} ({s.app_hint} / {s.emotional_hint})"
        if s.raw_llava:
            try:
                detail = json.loads(s.raw_llava).get("detail", "")
                if detail and detail.lower() not in {"", "none", "n/a"}:
                    line += f" — {detail}"
            except Exception:
                pass
        lines.append(line)

    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# Proactive suggestion engine
# ─────────────────────────────────────────────────────────────────────────────

def _detect_stuck(summaries: list[ScreenSummary], stuck_minutes: int) -> Optional[str]:
    """
    Return a proactive suggestion string if the user seems stuck, else None.

    Rules (any one triggers):
    - Same app + "stuck" or "frustrated" mood for N consecutive summaries
    - Same app for > stuck_minutes worth of captures with mood != "focused"
    """
    if len(summaries) < 2:
        return None

    apps = [s.app_hint.lower() for s in summaries]
    moods = [s.emotional_hint.lower() for s in summaries]
    activities = [s.activity.lower() for s in summaries]

    # All on same app?
    dominant_app = apps[-1] if len(set(apps[-3:])) == 1 else None

    # Mood signals
    bad_moods = {"stuck", "frustrated", "confused", "distracted", "idle"}
    recent_bad = sum(1 for m in moods[-3:] if any(b in m for b in bad_moods))

    # Similar activity (rough dedup)
    similar_activity = (
        len(summaries) >= 3
        and _similarity(activities[-1], activities[-3]) > 0.6
    )

    if dominant_app and recent_bad >= 2:
        app_display = summaries[-1].app_hint
        activity_display = summaries[-1].activity
        return (
            f"I see you've been on {app_display} for a while ({activity_display}). "
            f"You seem {moods[-1]} — want me to help, take a break reminder, or explain an error?"
        )

    if similar_activity and len(summaries) >= 3:
        # Check elapsed time
        try:
            t0 = datetime.fromisoformat(summaries[-len(summaries)].timestamp_utc)
            t1 = datetime.fromisoformat(summaries[-1].timestamp_utc)
            elapsed_min = (t1 - t0).total_seconds() / 60
            if elapsed_min >= stuck_minutes:
                return (
                    f"You've been doing '{summaries[-1].activity}' for about "
                    f"{int(elapsed_min)} minutes. Want help or a break?"
                )
        except Exception:
            pass

    return None


def _similarity(a: str, b: str) -> float:
    """Very fast word-overlap similarity (0-1)."""
    wa = set(a.split())
    wb = set(b.split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


def _maybe_nudge(summaries: list[ScreenSummary], stuck_minutes: int) -> None:
    """Fire the proactive callback if the user needs a nudge."""
    if _STATE.proactive_callback is None:
        return
    now = time.monotonic()
    if now - _STATE.last_nudge_time < _STATE.nudge_cooldown_s:
        return
    suggestion = _detect_stuck(summaries, stuck_minutes)
    if suggestion:
        _STATE.last_nudge_time = now
        try:
            _STATE.proactive_callback(suggestion)
        except Exception as exc:
            print(f"[screen_reader] proactive callback error: {exc}")

# ─────────────────────────────────────────────────────────────────────────────
# Background thread
# ─────────────────────────────────────────────────────────────────────────────

_READER_THREAD: Optional[threading.Thread] = None
_STOP_EVENT = threading.Event()


def _reader_loop(
    interval: float,
    ollama_url: str,
    model: str,
    stuck_minutes: int,
    debug: bool,
) -> None:
    """Main loop: sleep → wait for LLM idle → capture → analyse → store."""
    print(
        f"[screen_reader] started — interval={interval}s"
        f"  model={model}  ollama={ollama_url}"
    )

    while not _STOP_EVENT.is_set():
        # Sleep in small chunks so we can respond to stop quickly.
        for _ in range(int(interval * 2)):
            if _STOP_EVENT.is_set():
                return
            time.sleep(0.5)

        # ── Priority gate: wait until the main LLM is idle ──────────────────
        if _MAIN_LLM_BUSY.is_set():
            if debug:
                print("[screen_reader] main LLM busy — waiting...")
            if not _wait_for_main_llm_idle(timeout=120.0):
                if debug:
                    print("[screen_reader] main LLM still busy after 120s, skipping cycle.")
                continue

        # ── Capture + analyse ─────────────────────────────────────────────────
        summary = _analyse_screen(ollama_url, model, debug)
        if summary is None:
            continue

        # ── Store ─────────────────────────────────────────────────────────────
        with _STATE.lock:
            _STATE.summaries.append(summary)
            recent = list(_STATE.summaries)

        # ── Proactive nudge ───────────────────────────────────────────────────
        _maybe_nudge(recent, stuck_minutes)

        # ── Optional: persist summary to conversation memory ──────────────────
        _persist_summary(summary, debug)


def _persist_summary(summary: ScreenSummary, debug: bool) -> None:
    """
    Optionally store the activity summary in the conversation log
    so it can be retrieved by the vector store later.
    """
    try:
        from memory.conversation_log import log_interaction
        log_interaction(
            user_text=f"[screen_reader] {summary.activity}",
            assistant_text="",
            metadata={
                "source": "screen_reader",
                "app": summary.app_hint,
                "mood": summary.emotional_hint,
                "timestamp": summary.timestamp_utc,
            },
        )
    except Exception:
        pass   # memory module not available — silently skip

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def start_screen_reader(
    proactive_callback: Optional[Callable[[str], None]] = None,
    nudge_cooldown_minutes: float = 10.0,
) -> Optional[threading.Thread]:
    """
    Start the screen reader background thread.

    Parameters
    ----------
    proactive_callback:
        Optional function called with a suggestion string whenever the
        screen reader detects the user is stuck.  In ARIA this should be::

            from voice.tts import speak
            start_screen_reader(proactive_callback=speak)

    nudge_cooldown_minutes:
        Minimum minutes between proactive nudges (default 10).

    Returns
    -------
    threading.Thread or None
        The daemon thread, or None if disabled via env-var.
    """
    global _READER_THREAD

    if _env_bool("SCREEN_READER_DISABLED", False):
        print("[screen_reader] disabled via SCREEN_READER_DISABLED=1.")
        return None

    if _READER_THREAD is not None and _READER_THREAD.is_alive():
        return _READER_THREAD   # already running

    _STOP_EVENT.clear()

    # Config
    interval      = _env_float("SCREEN_READER_INTERVAL",     60.0)
    ollama_url    = _env("SCREEN_READER_OLLAMA_URL",          "http://localhost:11434")
    model         = _env("SCREEN_READER_MODEL",               "llava:7b")
    history       = _env_int ("SCREEN_READER_HISTORY",        5)
    stuck_minutes = _env_int ("SCREEN_READER_STUCK_MINUTES",  20)
    debug         = _env_bool("SCREEN_READER_DEBUG",          False)

    _update_maxlen(history)

    if proactive_callback is not None:
        _STATE.proactive_callback = proactive_callback
    _STATE.nudge_cooldown_s = nudge_cooldown_minutes * 60.0

    _READER_THREAD = threading.Thread(
        target=_reader_loop,
        args=(interval, ollama_url, model, stuck_minutes, debug),
        daemon=True,
        name="screen-reader",
    )
    _READER_THREAD.start()
    return _READER_THREAD


def stop_screen_reader() -> None:
    """Signal the background thread to stop (it exits after the current cycle)."""
    _STOP_EVENT.set()


def is_screen_reader_running() -> bool:
    """Return True if the screen reader thread is alive."""
    return _READER_THREAD is not None and _READER_THREAD.is_alive()


# ─────────────────────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════
#  AGENT.PY WIRING — copy-paste guide
# ════════════════════════════════════════════════════════════════════════════
#
# STEP 1 — Add these imports to agent.py (top, after existing imports):
#
#     from vision.screen_reader import (
#         start_screen_reader,
#         get_screen_context_block,
#         llm_busy_context,
#     )
#
#
# STEP 2 — Inject screen context into _build_context_prompt():
#
#     def _build_context_prompt(prompt: str) -> str:
#         # ... existing vibe_block and memory_block code ...
#
#         # ADD THIS BLOCK (after memory_block, before parts = [...]):
#         screen_block = get_screen_context_block()
#
#         parts = [p for p in [vibe_block, memory_block, screen_block] if p]
#         if not parts:
#             return prompt
#         return "Context (use only if relevant):\n\n" + "\n\n".join(parts) + f"\n\nUser: {prompt}"
#
#
# STEP 3 — Wrap every LLM call with llm_busy_context():
#
#     In generate_text() (modules/content_generator.py) or wherever Ollama
#     is called, import and wrap:
#
#         from vision.screen_reader import llm_busy_context
#
#         def generate_text(prompt: str) -> str:
#             with llm_busy_context():
#                 # ... your existing ollama call ...
#                 result = ollama.chat(...)
#             return result
#
#
# STEP 4 — Start the screen reader in main.py:
#
#     from vision.screen_reader import start_screen_reader
#     from voice.tts import speak
#
#     def main() -> None:
#         _load_env()
#         _ensure_cache_dirs()
#         _start_emotion_detector()
#
#         # ADD THIS — screen reader with proactive voice suggestions:
#         start_screen_reader(proactive_callback=speak)
#
#         # ... rest of main unchanged ...
#
# ─────────────────────────────────────────────────────────────────────────────
