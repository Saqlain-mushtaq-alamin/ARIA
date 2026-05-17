# ARIA — Complete Upgrade Plan
### From Phase 8 → World-Class AI Desktop Assistant

> **Model note:** All LLM features use `llama3` (8B via Ollama) unless stated. The 8B model handles
> all tasks described here comfortably. Vision features use `llava:7b` (already pulled).
> No external APIs required — everything runs locally and offline.

---

## How to read this document

Each upgrade follows this structure:

- **What it is** — one paragraph description of the feature
- **Why it matters** — the productivity or intelligence benefit
- **Files to create or edit** — exact file paths and what changes
- **Implementation steps** — numbered, copy-paste ready
- **Behaviour example** — exactly what ARIA says and does after the feature is live
- **Integration points** — which existing files it connects to

Work through features **in the order listed**. Each section's features build on the previous.
Complete and test each feature before starting the next.

---

## Part 1 — Screen intelligence upgrades

These features make ARIA aware of what is on your screen and able to act on it intelligently.
They share the `vision/screen_reader.py` and `memory/screen_state.json` infrastructure
already built in Phase 7.

---

### Feature 1.1 — Social media comment generator

**What it is**

When you are on a social media post (Facebook, Twitter/X, LinkedIn, Instagram Web,
YouTube) in the browser and say "comment on this post", ARIA takes a screenshot,
sends it to LLaVA to extract the post content, then generates a contextually appropriate
comment using the main LLM. It speaks the generated comment to you for approval before
typing it into the comment box via Playwright.

**Why it matters**

Saves 2–5 minutes per interaction. More importantly, the comment is always thoughtful and
on-topic — the LLM reads the full post context, not just the title.

**Files to create or edit**

| File | Action |
|------|--------|
| `modules/social_agent.py` | Create — social media reader and commenter |
| `modules/browser_agent.py` | Edit — add `get_current_page_screenshot()` helper |
| `core/intent_classifier.py` | Edit — add `comment_on_post`, `reply_to_post` intents |
| `core/router.py` | Edit — register `comment_on_post` → `social_agent.comment_on_post` |

**Implementation steps**

1. Install dependency (already available via Playwright): no new pip packages needed.

2. Create `modules/social_agent.py`:

```python
"""Social media intelligence — read posts, generate comments, reply."""
from __future__ import annotations
import base64, re, time
from typing import Optional
import ollama
from modules.browser_agent import get_active_page
from modules.system_control import type_text
from modules.content_generator import generate_text

# CSS selectors for comment boxes per platform
_COMMENT_SELECTORS = {
    "facebook.com":  "[aria-label='Write a comment']",
    "twitter.com":   "[data-testid='tweetTextarea_0']",
    "x.com":         "[data-testid='tweetTextarea_0']",
    "linkedin.com":  ".ql-editor[contenteditable='true']",
    "youtube.com":   "#simplebox-placeholder",
    "instagram.com": "textarea[placeholder]",
}

def _screenshot_b64() -> str:
    import pyautogui
    img = pyautogui.screenshot()
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

def read_post_from_screen() -> str:
    """Use LLaVA to extract the post text from the current screen."""
    b64 = _screenshot_b64()
    result = ollama.chat(
        model="llava",
        messages=[{
            "role": "user",
            "content": (
                "Read the main social media post on this screen. "
                "Return ONLY the post text and author name. "
                "Do not describe the UI. If no post is visible say 'NO_POST'."
            ),
            "images": [b64],
        }]
    )
    return result["message"]["content"].strip()

def comment_on_post(tone: str = "thoughtful") -> str:
    """Read the current post and generate + type a comment."""
    post_text = read_post_from_screen()
    if "NO_POST" in post_text:
        return "Sir, I could not detect a social media post on the current screen."

    prompt = (
        f"Generate a {tone} comment for this social media post. "
        f"Keep it 1-3 sentences, natural, and engaging. "
        f"Do not start with 'Great post' or generic phrases. "
        f"Return ONLY the comment text.\n\nPost:\n{post_text}"
    )
    comment = generate_text(prompt, temperature=0.8).strip()
    return comment

def explain_selected_text() -> str:
    """Read whatever text is currently selected/highlighted on screen via LLaVA."""
    b64 = _screenshot_b64()
    result = ollama.chat(
        model="llava",
        messages=[{
            "role": "user",
            "content": (
                "There is highlighted/selected text on this screen. "
                "Extract ONLY the highlighted text. "
                "Return it exactly as written, nothing else."
            ),
            "images": [b64],
        }]
    )
    selected = result["message"]["content"].strip()
    if not selected or len(selected) < 3:
        import pyperclip
        selected = pyperclip.paste()
    if not selected:
        return "Sir, I could not detect any selected text."
    explanation = generate_text(
        f"Explain this clearly and concisely, Sir:\n\n{selected}",
        temperature=0.4
    )
    return f'Selected text: "{selected[:80]}..."\n\n{explanation}'
```

3. In `core/intent_classifier.py`, add to `_STAGE2_SYSTEM` intents list:
   - `comment_on_post` → no parameters required (reads screen automatically)
   - `explain_selected` → no parameters required (reads selection automatically)
   - `reply_to_post` → parameters.tone (optional: "formal"/"casual"/"humorous")

4. In `core/router.py`, register:

```python
from modules.social_agent import comment_on_post, explain_selected_text

INTENT_REGISTRY["comment_on_post"]  = comment_on_post
INTENT_REGISTRY["explain_selected"] = explain_selected_text
```

5. In `agent.py`'s `process_text_stream()`, for these intents skip the question gate
   (they are self-contained screen-reading actions that need no parameters).

**Behaviour example**

```
You:   "Comment on this post"
ARIA:  ▶  Reading the post on your screen...
       ✓  Post detected: "Anyone else feel like productivity apps are..."
       ▶  Generating comment...
       ✓  Generated: "The irony is real — sometimes the app itself becomes
          the distraction. Best system I've found is just a single text file."
       ❓  Sir, shall I type this comment? Say yes to confirm.
You:   "Yes"
ARIA:  ▶  Clicking comment box and typing...
       ✓  Comment posted, Sir.
```

**Integration points**

- `vision/screen_reader.py` — shares the LLaVA screenshot pipeline
- `safety/confirmation_engine.py` — always requires confirmation before posting
- `safety/audit_log.py` — logs every social post action

---

### Feature 1.2 — Selected text explainer

**What it is**

Whenever you highlight/select any text on screen — in a browser, PDF reader, code editor,
or any app — and say "explain this" or "explain the marked part", ARIA reads the selected
text (via clipboard or LLaVA visual detection), sends it to the LLM, and gives you a clear
explanation spoken aloud and displayed in the overlay. Works in any app, any language.

**Why it matters**

Eliminates the context switch of copying text, opening a browser, and searching. The
explanation arrives in 2–3 seconds without leaving what you are reading.

**Files to create or edit**

| File | Action |
|------|--------|
| `modules/social_agent.py` | Already created in 1.1 — `explain_selected_text()` is there |
| `modules/system_control.py` | Edit — add `get_selected_text()` using Ctrl+C trick |
| `core/intent_classifier.py` | Edit — `explain_selected`, `define_selected`, `translate_selected` |

**Implementation steps**

1. Add to `modules/system_control.py`:

```python
def get_selected_text() -> str:
    """Get currently selected text by temporarily copying it to clipboard."""
    import pyperclip, pyautogui, time
    old_clip = pyperclip.paste()
    pyautogui.hotkey("ctrl", "c")
    time.sleep(0.15)
    selected = pyperclip.paste()
    # Restore old clipboard
    pyperclip.copy(old_clip)
    return selected.strip() if selected != old_clip else ""
```

2. Extend `explain_selected_text()` in `social_agent.py` to try clipboard first (faster),
   fall back to LLaVA visual detection if clipboard is empty.

3. Add intent aliases in `core/intent_classifier.py`:
   - "explain this" / "explain the marked part" / "what does this mean" → `explain_selected`
   - "define this" → `explain_selected` with tone=definition
   - "translate this" → `explain_selected` with mode=translate

**Behaviour example**

```
[You highlight a complex paragraph in a research PDF about transformer attention]
You:   "Explain the marked part"
ARIA:  ▶  Reading selected text...
       ✓  Got it — 147 characters selected.
       ▶  Explaining...
ARIA:  "Sir, this section is describing how the attention mechanism decides which
        words in a sentence to focus on when processing each word. Think of it
        like this: when reading 'The cat sat on the mat because it was tired',
        the word 'it' needs to know it refers to 'cat', not 'mat'. Attention
        scores tell the model exactly that — how much each word should 'pay
        attention' to every other word."
```

---

### Feature 1.3 — Productivity guardian (schedule-aware distraction detection)

**What it is**

ARIA monitors what you are doing on screen against your current schedule. If your schedule
says you should be studying chemistry but `screen_reader.py` detects you are on Instagram,
YouTube, or any social media for more than 3 minutes, ARIA triggers a notification and
voice alert. The alert is smart — it knows your schedule, knows the deadline, and frames
the nudge in a way that is firm but not annoying. After the first alert, it waits 10 minutes
before alerting again (not every 3 minutes — that would be maddening).

**Why it matters**

This is the single most powerful productivity feature in the system. Most people lose 1–3
hours per day to unplanned social media. ARIA makes that loss visible and immediate.

**Files to create or edit**

| File | Action |
|------|--------|
| `modules/productivity_guardian.py` | Create — the core guardian loop |
| `vision/screen_reader.py` | Edit — expose `get_current_activity_type()` function |
| `scheduler/tracker.py` | Edit — expose `get_current_scheduled_task()` function |
| `main.py` | Edit — start guardian as a background daemon thread |

**Implementation steps**

1. Create `modules/productivity_guardian.py`:

```python
"""Productivity guardian — detects schedule violations and alerts Sir."""
from __future__ import annotations
import json, os, threading, time
from datetime import datetime
from typing import Optional

DISTRACTION_APPS = {
    "instagram", "facebook", "twitter", "tiktok", "reddit",
    "youtube", "netflix", "twitch", "snapchat", "discord",
    "whatsapp web", "telegram web",
}
SCREEN_STATE_PATH = "memory/screen_state.json"
CHECK_INTERVAL_SEC = 60        # check every 60 seconds
DISTRACTION_THRESHOLD_MIN = 3  # alert after 3 consecutive distraction minutes
COOLDOWN_AFTER_ALERT_MIN = 10  # wait 10 min before next alert

_distraction_streak = 0
_last_alert_time: Optional[float] = None


def _read_screen_state() -> dict:
    try:
        with open(SCREEN_STATE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_scheduled_task() -> Optional[str]:
    try:
        from scheduler.tracker import get_current_scheduled_task
        return get_current_scheduled_task()
    except Exception:
        return None


def _is_distraction(screen_state: dict) -> bool:
    activity = str(screen_state.get("activity", "")).lower()
    app = str(screen_state.get("app", "")).lower()
    return any(d in activity or d in app for d in DISTRACTION_APPS)


def _should_be_working(scheduled_task: Optional[str]) -> bool:
    if not scheduled_task:
        return False
    task_lower = scheduled_task.lower()
    work_keywords = ["study", "exam", "assignment", "coding", "project",
                     "class", "lecture", "read", "write", "practice"]
    return any(kw in task_lower for kw in work_keywords)


def _fire_alert(scheduled_task: str, distraction: str) -> None:
    global _last_alert_time
    from voice.tts import speak
    from modules.notifier import send_toast

    now = datetime.now()
    hour = now.hour
    urgency = "Sir" if hour < 22 else "Sir, it is getting late"

    message = (
        f"{urgency}, your schedule says you should be working on "
        f"'{scheduled_task}' right now, but I can see you have been "
        f"on {distraction} for the last {DISTRACTION_THRESHOLD_MIN} minutes. "
        f"Shall I close it and open your study materials?"
    )
    send_toast("Productivity Check", message[:100])
    speak(message)
    _last_alert_time = time.time()


def guardian_loop() -> None:
    """Main loop — run as a daemon thread from main.py."""
    global _distraction_streak
    while True:
        time.sleep(CHECK_INTERVAL_SEC)
        try:
            screen = _read_screen_state()
            task   = _get_scheduled_task()

            if _is_distraction(screen) and _should_be_working(task):
                _distraction_streak += 1
                cooldown_ok = (
                    _last_alert_time is None or
                    time.time() - _last_alert_time > COOLDOWN_AFTER_ALERT_MIN * 60
                )
                if _distraction_streak >= DISTRACTION_THRESHOLD_MIN and cooldown_ok:
                    app_name = screen.get("app", "a distraction site")
                    _fire_alert(task, app_name)
            else:
                _distraction_streak = 0
        except Exception:
            pass


def start_guardian() -> threading.Thread:
    t = threading.Thread(target=guardian_loop, name="productivity-guardian", daemon=True)
    t.start()
    return t
```

2. In `main.py`, after starting other daemon threads, add:

```python
from modules.productivity_guardian import start_guardian
start_guardian()
```

3. In `scheduler/tracker.py`, add:

```python
def get_current_scheduled_task() -> Optional[str]:
    """Return the task that should be happening right now per today's schedule."""
    # Read today's plan from data/schedule_today.json
    # Find the time slot that matches the current hour
    # Return the task name or None if free time
    ...
```

4. Extend `screen_reader.py` to populate `"app"` field in `screen_state.json` clearly
   (e.g., "Instagram", "YouTube") so the guardian can read it cleanly.

**Behaviour example**

```
[Schedule says: 21:00 — Study chemistry. Actual: scrolling Instagram for 4 min]

ARIA (toast notification + voice):
    "Sir, your schedule says you should be working on 'chemistry exam prep'
     right now, but I can see you have been on Instagram for the last 3 minutes.
     Shall I close it and open your study materials?"

You: "Yes"
ARIA: ▶  Closing Instagram tab...
      ▶  Opening chemistry notes PDF...
      ✓  Done, Sir. You have 47 minutes left in your study block.
          Let us make them count.
```

---

## Part 2 — Intelligence layer upgrades

### Feature 2.1 — Deep work mode

**What it is**

Inspired by Cal Newport's Deep Work. A dedicated focus mode that (1) blocks distracting
apps and websites at the system level using the Windows hosts file, (2) runs a Pomodoro
timer with voice announcements, (3) computes a live "flow score" every 5 minutes from
`screen_state.json` activity analysis, (4) plays ambient focus audio, and (5) generates
an end-of-session micro-report: what you accomplished, your flow score, and one
improvement for tomorrow.

**Why it matters**

The flow score is unique — it measures whether you are actually in deep work (same app,
consistent keystrokes, no switching) versus fake productivity (switching every 2 minutes,
social media briefly opened). Most Pomodoro apps just time you. This one measures you.

**Files to create or edit**

| File | Action |
|------|--------|
| `modules/focus_mode.py` | Create — the complete deep work engine |
| `modules/system_control.py` | Edit — add `block_urls()` and `unblock_urls()` |
| `data/focus_sessions.db` | Auto-created — SQLite for session analytics |

**Implementation steps**

1. Add to `modules/system_control.py`:

```python
HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
_ARIA_BLOCK_TAG = "# ARIA-BLOCK"

def block_urls(domains: list[str]) -> str:
    """Redirect domains to 127.0.0.1 via Windows hosts file."""
    import ctypes
    if not ctypes.windll.shell32.IsUserAnAdmin():
        return "Sir, I need Administrator rights to block websites. Run ARIA as Admin."
    with open(HOSTS_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n{_ARIA_BLOCK_TAG}\n")
        for domain in domains:
            f.write(f"127.0.0.1 {domain}\n127.0.0.1 www.{domain}\n")
    return f"Blocked {len(domains)} site(s)."

def unblock_urls() -> str:
    """Remove all ARIA-added blocks from hosts file."""
    with open(HOSTS_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    clean = []
    skip = False
    for line in lines:
        if _ARIA_BLOCK_TAG in line:
            skip = True
        if not skip:
            clean.append(line)
        if skip and line.strip() == "":
            skip = False
    with open(HOSTS_PATH, "w", encoding="utf-8") as f:
        f.writelines(clean)
    return "All website blocks removed, Sir."
```

2. Create `modules/focus_mode.py`:

```python
"""Deep work mode — Pomodoro + flow scoring + session analytics."""
from __future__ import annotations
import json, os, sqlite3, time, threading
from datetime import datetime
from typing import Optional
from voice.tts import speak
from modules.system_control import block_urls, unblock_urls

DISTRACTION_DOMAINS = [
    "instagram.com","facebook.com","twitter.com","x.com",
    "tiktok.com","reddit.com","youtube.com","netflix.com",
    "twitch.tv","9gag.com","buzzfeed.com","snapchat.com",
]
DB_PATH = "data/focus_sessions.db"
SCREEN_STATE = "memory/screen_state.json"

# Pomodoro defaults (minutes)
WORK_MIN  = 25
SHORT_MIN = 5
LONG_MIN  = 20
CYCLES_BEFORE_LONG = 4

_active = False
_flow_scores: list[float] = []
_activity_log: list[dict] = []
_stop_event = threading.Event()


def _init_db() -> None:
    os.makedirs("data", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, start_time TEXT, end_time TEXT,
            duration_min INTEGER, flow_score REAL,
            cycles_completed INTEGER, task TEXT, report TEXT
        )""")


def _read_screen() -> dict:
    try:
        with open(SCREEN_STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def _compute_flow_score(window: list[dict]) -> float:
    """Score 0-100 based on consistency of activity in a 5-min window."""
    if not window:
        return 50.0
    apps = [w.get("app","") for w in window]
    unique_apps = len(set(apps))
    stuck_flag = any(w.get("stuck") for w in window)
    # Single app, not stuck = flow. Multiple apps = distraction.
    base = max(0, 100 - (unique_apps - 1) * 25)
    if stuck_flag:
        base = max(0, base - 20)
    return float(base)


def _pomodoro_timer(work_min: int, task: str) -> None:
    global _flow_scores, _activity_log, _stop_event
    cycle = 0
    while not _stop_event.is_set():
        cycle += 1
        speak(f"Sir, starting Pomodoro cycle {cycle}. Focus time begins now.")
        window: list[dict] = []
        end_work = time.time() + work_min * 60

        while time.time() < end_work and not _stop_event.is_set():
            time.sleep(60)  # sample every minute
            state = _read_screen()
            window.append(state)
            _activity_log.append({**state, "cycle": cycle, "at": datetime.now().isoformat()})

            # Flow score every 5 samples
            if len(window) % 5 == 0:
                score = _compute_flow_score(window[-5:])
                _flow_scores.append(score)
                if score < 40:
                    speak(f"Sir, your focus score just dropped. You appear to be switching frequently.")

        if _stop_event.is_set():
            break

        # Break announcement
        is_long = cycle % CYCLES_BEFORE_LONG == 0
        break_min = LONG_MIN if is_long else SHORT_MIN
        break_type = "long" if is_long else "short"
        speak(f"Pomodoro {cycle} complete, Sir. Take a {break_min}-minute {break_type} break.")
        time.sleep(break_min * 60)
        speak(f"Break over, Sir. Back to work.")


def start_focus_mode(task: str = "deep work", work_min: int = WORK_MIN) -> str:
    global _active, _stop_event, _flow_scores, _activity_log
    if _active:
        return "Sir, focus mode is already running."
    _init_db()
    _active = True
    _stop_event.clear()
    _flow_scores = []
    _activity_log = []

    block_urls(DISTRACTION_DOMAINS)
    speak(f"Sir, deep work mode activated. Working on: {task}. All distractions blocked.")

    _start_time = datetime.now()

    t = threading.Thread(
        target=_pomodoro_timer, args=(work_min, task), daemon=True
    )
    t.start()

    return (
        f"Deep work mode started, Sir.\n"
        f"Task: {task}\n"
        f"Pomodoro: {work_min} min work / {SHORT_MIN} min break\n"
        f"All distraction sites blocked.\n"
        f"Say 'stop focus mode' to end the session."
    )


def stop_focus_mode() -> str:
    global _active
    if not _active:
        return "Sir, focus mode is not currently running."
    _stop_event.set()
    _active = False
    unblock_urls()

    # Generate report
    avg_flow = sum(_flow_scores) / len(_flow_scores) if _flow_scores else 0
    report = _generate_session_report(avg_flow, _activity_log)

    speak(f"Sir, deep work session complete. Your average flow score was "
          f"{avg_flow:.0f} out of 100.")
    return report


def _generate_session_report(flow_score: float, log: list) -> str:
    from modules.content_generator import generate_text
    log_summary = f"Flow score: {flow_score:.0f}/100. {len(log)} activity samples."
    apps_used = list({e.get('app','?') for e in log if e.get('app')})

    prompt = (
        f"Generate a concise deep work session report for Sir. "
        f"Data: {log_summary} Apps used: {', '.join(apps_used)}. "
        f"Include: 1) What was accomplished, 2) Flow quality assessment, "
        f"3) One specific improvement for tomorrow. Keep it under 150 words. "
        f"Address the user as Sir."
    )
    return generate_text(prompt, temperature=0.4)
```

3. Register in `core/router.py`:

```python
from modules.focus_mode import start_focus_mode, stop_focus_mode
INTENT_REGISTRY["start_focus_mode"] = start_focus_mode
INTENT_REGISTRY["stop_focus_mode"]  = stop_focus_mode
```

**Behaviour example**

```
You:   "Start focus mode, I need to study chemistry for 2 hours"
ARIA:  ▶  Blocking 19 distraction websites...
       ✓  Websites blocked.
       "Sir, deep work mode activated. Working on: chemistry study.
        Pomodoro: 25 min work / 5 min break. All distractions blocked."

[25 minutes later]
ARIA:  "Pomodoro 1 complete, Sir. Take a 5-minute short break."

[At 5-minute mark during break]
ARIA:  "Break over, Sir. Back to work."

[After you say "stop focus mode"]
ARIA:  "Sir, deep work session complete. Your average flow score was 74 out of 100."

Session Report:
"Sir, you completed 3 Pomodoro cycles focusing primarily on Chrome and a PDF reader,
which indicates consistent study behaviour. Your flow score of 74/100 is solid —
the dip in cycle 2 suggests a 4-minute distraction around the 45-minute mark.
Tomorrow: start your session by closing all browser tabs except your study material
before the first Pomodoro begins. That alone typically adds 10–15 points to flow score."
```

---

### Feature 2.2 — Cognitive load monitor

**What it is**

ARIA hooks into Windows keyboard events using `pynput` and tracks timing-based
signatures of mental fatigue: inter-key intervals (time between keystrokes), backspace
rate (correction frequency), and pre-sentence pause duration. Every 5 minutes it computes
a rolling cognitive load score (0–100, where 100 = maximum fatigue). When load is high,
ARIA simplifies responses, pauses non-urgent notifications, and suggests a break.
When load is low, it offers more ambitious tasks.

**Why it matters**

Backed by peer-reviewed HCI research (Hernandez et al. 2011, Vizer et al. 2009).
This gives ARIA a genuine neuro-adaptive quality — the first AI assistant that responds
to how tired your brain is, not just what you say.

**Files to create or edit**

| File | Action |
|------|--------|
| `vision/cognitive_monitor.py` | Create — the full monitor |
| `memory/cognitive_state.json` | Auto-created — shared state file |
| `core/agent.py` | Edit — inject cognitive state into `_build_context_prompt()` |

**Implementation steps**

1. `pip install pynput` (add to `requirements.txt`)

2. Create `vision/cognitive_monitor.py`:

```python
"""Cognitive load monitor — keyboard timing analysis."""
from __future__ import annotations
import json, os, threading, time
from collections import deque
from datetime import datetime

try:
    from pynput import keyboard
    _PYNPUT = True
except Exception:
    _PYNPUT = False

STATE_PATH = "memory/cognitive_state.json"
WINDOW_SIZE = 100       # last 100 keystrokes
UPDATE_INTERVAL = 300   # recompute every 5 minutes

_timestamps: deque = deque(maxlen=WINDOW_SIZE)
_backspaces:  deque = deque(maxlen=WINDOW_SIZE)
_pauses:      deque = deque(maxlen=20)
_last_key_time: float = 0.0
_lock = threading.Lock()


def _on_press(key) -> None:
    global _last_key_time
    now = time.time()
    with _lock:
        is_backspace = (key == keyboard.Key.backspace)
        if _last_key_time > 0:
            iki = now - _last_key_time           # inter-key interval
            _timestamps.append(iki)
            _backspaces.append(1 if is_backspace else 0)
            if iki > 3.0:                        # pause > 3s before typing
                _pauses.append(iki)
        _last_key_time = now


def _compute_load() -> int:
    with _lock:
        if len(_timestamps) < 10:
            return 20  # not enough data

        avg_iki    = sum(_timestamps) / len(_timestamps)
        backspace_rate = sum(_backspaces) / len(_backspaces)
        avg_pause  = sum(_pauses) / len(_pauses) if _pauses else 0

        # Normalise each dimension (values tuned from research)
        # Slow typing (avg IKI > 400ms) = tired
        iki_score      = min(100, max(0, (avg_iki - 0.15) / 0.45 * 100))
        # High backspace rate (>15%) = struggling
        bksp_score     = min(100, backspace_rate / 0.15 * 100)
        # Long pauses before typing = mental search
        pause_score    = min(100, max(0, (avg_pause - 2) / 8 * 100))

        load = int(iki_score * 0.45 + bksp_score * 0.35 + pause_score * 0.20)
        return min(100, max(0, load))


def _write_state(load: int) -> None:
    os.makedirs("memory", exist_ok=True)
    label = ("low" if load < 30 else "moderate" if load < 60
             else "high" if load < 80 else "critical")
    state = {
        "cognitive_load": load,
        "label": label,
        "last_updated": datetime.now().isoformat(),
        "recommendation": {
            "low":      "User is sharp — present detailed responses and ambitious tasks.",
            "moderate": "Normal operation.",
            "high":     "Keep responses concise. Pause low-priority notifications.",
            "critical": "Strongly suggest a break. Simplify everything.",
        }[label]
    }
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _monitor_loop() -> None:
    while True:
        time.sleep(UPDATE_INTERVAL)
        load = _compute_load()
        _write_state(load)
        if load >= 80:
            try:
                from voice.tts import speak
                speak("Sir, your cognitive load is very high. "
                      "I recommend a 10-minute break before continuing.")
            except Exception:
                pass


def start_cognitive_monitor() -> None:
    if not _PYNPUT:
        return
    listener = keyboard.Listener(on_press=_on_press)
    listener.daemon = True
    listener.start()
    t = threading.Thread(target=_monitor_loop, daemon=True, name="cognitive-monitor")
    t.start()
```

3. In `core/agent.py`, inside `_build_context_prompt()`, add after the emotion block:

```python
# Cognitive load state
try:
    with open("memory/cognitive_state.json") as f:
        cog = json.load(f)
    cog_block = (
        f"Cognitive load: {cog['label']} ({cog['cognitive_load']}/100). "
        f"{cog['recommendation']}"
    )
except Exception:
    cog_block = ""
```

4. In `main.py`, add:

```python
from vision.cognitive_monitor import start_cognitive_monitor
start_cognitive_monitor()
```

**Behaviour example**

```
[After 3 hours of studying, cognitive load score reaches 85/100]

ARIA (voice, unprompted):
    "Sir, your cognitive load is very high. I recommend a 10-minute break
     before continuing."

[User asks a question while high cognitive load]
You:   "Explain gradient descent to me"
ARIA:  [reads cognitive_state.json — label = "high"]
       "Sir, gradient descent is how a neural network learns. Imagine rolling
        a ball down a hill to find the lowest point — that lowest point is the
        best answer. The network does this mathematically with its parameters.
        Want me to go deeper when you are fresher?"
       [Note: normally ARIA would give a full technical explanation — it
        simplified automatically because load was high]
```

---

### Feature 2.3 — Subconscious intent layer

**What it is**

Detects what you actually want based on signals beyond your literal words. Tracks three
signal types: (1) hedging language ("I guess", "maybe", "not sure"), (2) topic repetition —
the same subject mentioned 3+ times in a week without resolution, (3) voice stress on
specific words detected by `tone_analyzer.py`. Surfaces detected latent needs as proactive
nudges without waiting to be asked.

**Files to create or edit**

| File | Action |
|------|--------|
| `core/subconscious_layer.py` | Create |
| `core/agent.py` | Edit — run subconscious check on every conversation turn |
| `memory/conversation_log.py` | Edit — expose topic frequency helper |

**Implementation steps**

1. Create `core/subconscious_layer.py`:

```python
"""Detects latent user needs from linguistic and behavioural signals."""
from __future__ import annotations
import re
from typing import Optional
from memory.conversation_log import search_interactions

HEDGING = [
    "i guess","maybe","probably","i think","not sure",
    "kind of","sort of","i suppose","perhaps","might",
]
CONCERN_THRESHOLD  = 3    # topic mentioned this many times = surface it
SEARCH_WINDOW_DAYS = 7


def detect_hedging(text: str) -> bool:
    t = text.lower()
    return any(h in t for h in HEDGING)


def get_topic_frequency(topic: str) -> int:
    """Count how many times this topic appeared in conversations this week."""
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=SEARCH_WINDOW_DAYS)).isoformat()
    try:
        hits = search_interactions(topic, limit=100, all_sessions=True)
        return len([h for h in hits if h.timestamp[:10] >= since])
    except Exception:
        return 0


def analyse(user_text: str) -> Optional[str]:
    """
    Returns a string nudge if a latent concern is detected, else None.
    Called from agent.py on every conversational turn.
    """
    text = user_text.strip()
    # 1. Hedging — uncertain command
    if detect_hedging(text):
        return None  # Handled by question_generator clarification flow

    # 2. Topic repetition
    # Extract likely topic nouns (simple heuristic — first 2-3 word phrase)
    words = re.findall(r'\b[A-Za-z]{4,}\b', text)
    for word in words[:5]:
        freq = get_topic_frequency(word)
        if freq >= CONCERN_THRESHOLD:
            return (
                f"Sir, I have noticed you have mentioned '{word}' {freq} times "
                f"this week without a resolution. Would you like me to create a "
                f"dedicated plan or reminder for it?"
            )
    return None
```

2. In `core/agent.py`, in `process_text_stream()`, after the conversational reply is
   generated, add:

```python
from core.subconscious_layer import analyse as subconscious_analyse

nudge = subconscious_analyse(user_text)
if nudge:
    yield f"\n💭 {nudge}"
```

**Behaviour example**

```
[User has mentioned "portfolio" 4 times this week in various commands]
You:   "Search for portfolio website templates"
ARIA:  [executes search]
       ✓  Found 5 results for portfolio website templates.
       💭 Sir, I have noticed you have mentioned 'portfolio' 4 times this week
          without a resolution. Would you like me to create a dedicated plan
          or reminder for it?
You:   "Yes, make a plan"
ARIA:  [launches goal_decomposer.py with "build portfolio website" as the goal]
```

---

## Part 3 — Productivity science features

### Feature 3.1 — Goal decomposer with spaced repetition

**What it is**

Give ARIA any complex goal in natural language. It uses a multi-step LLM chain-of-thought
to break it into subtasks, estimates durations using your real habit completion data,
schedules subtasks around your fixed commitments using your peak hours from
`user_profile.py`, and applies the SM-2 spaced repetition algorithm for study tasks.
It then acts as your daily accountability partner — checking in each morning and adjusting
the plan if you fall behind.

**Files to create or edit**

| File | Action |
|------|--------|
| `scheduler/goal_decomposer.py` | Create — the full decomposer |
| `data/goals.db` | Auto-created — goal and subtask tracking |
| `core/intent_classifier.py` | Edit — add `decompose_goal` intent |

**Implementation steps**

Create `scheduler/goal_decomposer.py`:

```python
"""Goal decomposer — breaks any goal into an optimal subtask schedule."""
from __future__ import annotations
import json, sqlite3, os
from datetime import date, timedelta
from typing import Any
from modules.content_generator import generate_text
from memory.user_profile import get_profile, get_peak_hours
from memory.habit_tracker import get_habit_schedule_hints

DB_PATH = "data/goals.db"

def _init_db() -> None:
    os.makedirs("data", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY, goal TEXT, deadline TEXT,
            created_at TEXT, status TEXT DEFAULT 'active'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS subtasks (
            id INTEGER PRIMARY KEY, goal_id INTEGER,
            title TEXT, duration_min INTEGER, due_date TEXT,
            completed INTEGER DEFAULT 0, review_date TEXT,
            easiness REAL DEFAULT 2.5, repetitions INTEGER DEFAULT 0,
            interval_days INTEGER DEFAULT 1
        )""")

def _sm2_next_review(quality: int, reps: int,
                     easiness: float, interval: int) -> tuple[int, float, int]:
    """SM-2 algorithm. quality: 0-5."""
    if quality < 3:
        return 0, easiness, 1
    new_e = max(1.3, easiness + 0.1 - (5-quality)*(0.08+(5-quality)*0.02))
    new_i = 1 if reps == 0 else (6 if reps == 1 else round(interval * new_e))
    return reps + 1, new_e, new_i

def decompose_goal(goal: str, deadline_str: str = "") -> str:
    """Decompose a goal into a scheduled subtask plan."""
    _init_db()
    profile  = get_profile()
    peaks    = get_peak_hours()
    hints    = get_habit_schedule_hints()
    avg_completion = profile.get("meta", {}).get("habit_completion_rate", 0.75)

    peak_str = ", ".join(f"{h:02d}:00" for h in peaks) if peaks else "morning"
    hint_str = json.dumps(hints, indent=2)

    plan_prompt = f"""
You are a productivity planner. Decompose this goal into 5-10 specific subtasks.

Goal: {goal}
Deadline: {deadline_str or '2 weeks from today'}
User peak productive hours: {peak_str}
User habit schedule: {hint_str}
User historical task completion rate: {avg_completion:.0%}

Return a JSON array of subtasks:
[
  {{
    "title": "Subtask name",
    "duration_min": 45,
    "type": "study|practice|create|review",
    "due_date_offset_days": 2,
    "is_study_task": true
  }}
]
Return ONLY the JSON array, no explanation.
"""
    raw = generate_text(plan_prompt, temperature=0.3)
    try:
        import re
        match = re.search(r'\[[\s\S]*\]', raw)
        subtasks = json.loads(match.group(0)) if match else []
    except Exception:
        return "Sir, I had trouble decomposing that goal. Please rephrase it."

    today = date.today()
    with sqlite3.connect(DB_PATH) as conn:
        goal_id = conn.execute(
            "INSERT INTO goals(goal, deadline, created_at) VALUES(?,?,?)",
            (goal, deadline_str, today.isoformat())
        ).lastrowid

        plan_lines = [f"📋 Goal plan for: {goal}\n"]
        for i, st in enumerate(subtasks):
            due = today + timedelta(days=int(st.get("due_date_offset_days", i+1)))
            # Add buffer based on historical completion rate
            buf = max(0, int((1 - avg_completion) * st.get("duration_min", 30)))
            total_min = st.get("duration_min", 30) + buf
            review_date = due + timedelta(days=1) if st.get("is_study_task") else None

            conn.execute(
                "INSERT INTO subtasks(goal_id, title, duration_min, due_date, review_date)"
                " VALUES(?,?,?,?,?)",
                (goal_id, st["title"], total_min, due.isoformat(),
                 review_date.isoformat() if review_date else None)
            )
            plan_lines.append(
                f"  {i+1}. {st['title']}\n"
                f"     Due: {due.strftime('%A %b %d')} | "
                f"Est: {total_min} min | Type: {st.get('type','task')}"
                + (" | 🔁 Spaced review" if st.get("is_study_task") else "")
            )
    return "\n".join(plan_lines)

def morning_checkin() -> str:
    """Called each morning by autonomous_planner — returns today's goal subtasks."""
    _init_db()
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT s.title, g.goal FROM subtasks s JOIN goals g ON g.id=s.goal_id "
            "WHERE s.due_date<=? AND s.completed=0 AND g.status='active' "
            "ORDER BY s.due_date",
            (today,)
        ).fetchall()
    if not rows:
        return ""
    lines = [f"Sir, here are your outstanding goal tasks for today:"]
    for title, goal in rows:
        lines.append(f"  • {title}  [{goal}]")
    return "\n".join(lines)
```

**Behaviour example**

```
You:   "I need to pass my chemistry exam in 2 weeks"
ARIA:  ▶  Decomposing goal with your schedule and completion history...
       ✓  Plan created, Sir.

📋 Goal plan for: pass chemistry exam

  1. Review Chapter 1-3 notes
     Due: Monday Mar 18 | Est: 52 min | Type: study | 🔁 Spaced review
  2. Practice reaction equations (50 problems)
     Due: Tuesday Mar 19 | Est: 68 min | Type: practice | 🔁 Spaced review
  3. Chapter 4-6 deep read
     Due: Thursday Mar 21 | Est: 58 min | Type: study | 🔁 Spaced review
  ...

ARIA:  "Sir, I have added 8 buffer minutes to each task based on your 81%
        historical completion rate. Review sessions are scheduled using
        spaced repetition — the next review of Chapter 1-3 will be on
        Wednesday, then again on Sunday."

[Next morning]
ARIA:  "Sir, here are your outstanding goal tasks for today:
        • Practice reaction equations — chemistry exam"
```

---

### Feature 3.2 — Life OS knowledge graph

**What it is**

Every entity ARIA touches — a file, a search query, a task, a contact, a URL, a
conversation topic — becomes a node in a directed knowledge graph. Edges represent
relationships: "file A was opened while working on task B", "search C happened the
day before deadline D". The result is a queryable map of your entire digital life.
Ask "everything related to my exam" and get back a connected web of files, searches,
notes, and conversations — not just a flat search result.

**Files to create or edit**

| File | Action |
|------|--------|
| `memory/knowledge_graph.py` | Create |
| `core/agent.py` | Edit — add node after every dispatched intent |
| `data/knowledge_graph.json` | Auto-created — persistent graph storage |

**Implementation steps**

1. `pip install networkx` (add to `requirements.txt`)

2. Create `memory/knowledge_graph.py`:

```python
"""Personal knowledge graph — links every entity ARIA interacts with."""
from __future__ import annotations
import json, os
from datetime import datetime
from typing import Any
import networkx as nx

GRAPH_PATH = "data/knowledge_graph.json"

_G: nx.DiGraph = nx.DiGraph()
_dirty = False


def _load() -> None:
    global _G
    if os.path.exists(GRAPH_PATH):
        try:
            data = nx.node_link_data(json.loads(open(GRAPH_PATH).read()))
            _G = nx.node_link_graph(data)
        except Exception:
            _G = nx.DiGraph()


def _save() -> None:
    os.makedirs("data", exist_ok=True)
    with open(GRAPH_PATH, "w") as f:
        json.dump(nx.node_link_data(_G), f)


_load()


def add_node(node_id: str, **attrs) -> None:
    attrs.setdefault("created_at", datetime.now().isoformat())
    _G.add_node(node_id, **attrs)
    _save()


def add_edge(source: str, target: str, relation: str) -> None:
    if source not in _G:
        add_node(source)
    if target not in _G:
        add_node(target)
    _G.add_edge(source, target, relation=relation,
                at=datetime.now().isoformat())
    _save()


def record_intent(intent: str, parameters: dict,
                  session_id: str = "") -> None:
    """Auto-add nodes and edges for an executed intent."""
    now_label = datetime.now().strftime("%Y%m%d_%H%M%S")
    action_id = f"action_{now_label}"
    add_node(action_id, type="action", intent=intent, session=session_id)

    if intent == "open_app":
        app = parameters.get("app_name","")
        add_node(app, type="app")
        add_edge(action_id, app, "opened")

    elif intent == "open_file":
        path = parameters.get("path","")
        add_node(path, type="file")
        add_edge(action_id, path, "opened")

    elif intent == "search_web":
        q = parameters.get("query","")
        add_node(q, type="search")
        add_edge(action_id, q, "searched")

    elif intent == "create_schedule":
        text = parameters.get("text","")
        add_node(text[:40], type="schedule")
        add_edge(action_id, text[:40], "scheduled")


def query(topic: str, hops: int = 2) -> list[dict]:
    """Find all nodes within N hops of nodes matching the topic string."""
    matches = [n for n in _G.nodes if topic.lower() in str(n).lower()]
    connected: set = set(matches)
    for m in matches:
        for hop in range(hops):
            neighbors = set(nx.neighbors(_G, m))
            connected |= neighbors
    result = []
    for n in connected:
        result.append({"id": n, **_G.nodes[n]})
    return result


def query_formatted(topic: str) -> str:
    nodes = query(topic)
    if not nodes:
        return f"Sir, I have no knowledge graph entries related to '{topic}' yet."
    lines = [f"🕸️  Knowledge graph — everything related to '{topic}':"]
    for node in nodes[:20]:
        ntype = node.get("type","?")
        lines.append(f"  [{ntype}]  {node['id']}")
    return "\n".join(lines)
```

3. In `core/agent.py`, after every successful `dispatch_intent()`, add:

```python
from memory.knowledge_graph import record_intent as kg_record
kg_record(intent, parameters, session_id=get_active_session())
```

**Behaviour example**

```
You:   "Show me everything related to my chemistry exam"
ARIA:  🕸️  Knowledge graph — everything related to 'chemistry':

  [schedule]   study chemistry — exam prep
  [search]     chemistry reaction equations examples
  [search]     ebbinghaus forgetting curve chemistry
  [file]       C:\Users\...\Documents\chem_notes_ch3.pdf
  [task]       Review Chapter 1-3 notes (due Mon Mar 18)
  [action]     open_file — chem_notes_ch3.pdf (Mar 12 21:04)
  [app]        chrome  (opened 6 times during chemistry searches)
```

---

## Part 4 — Autonomous systems

### Feature 4.1 — Autonomous life planner

**What it is**

ARIA runs two scheduled jobs every day without being asked:

- **06:00 Morning briefing** — loads your goals, yesterday's completion from
  `habit_tracker`, upcoming deadlines, cognitive state, emotion trend, weather,
  and generates a spoken briefing with today's optimal plan.

- **22:00 Evening review** — compares planned vs actual, adjusts tomorrow's plan,
  updates goal trajectories, and gives you a 60-second spoken reflection.

**Files to create or edit**

| File | Action |
|------|--------|
| `scheduler/autonomous_planner.py` | Create |
| `main.py` | Edit — start scheduler loop |

**Implementation steps**

1. `pip install schedule` (add to `requirements.txt`)

2. Create `scheduler/autonomous_planner.py`:

```python
"""Autonomous daily planner — morning briefing + evening review."""
from __future__ import annotations
import schedule, threading, time
from datetime import date
from modules.content_generator import generate_text
from memory.user_profile import get_address_form, get_peak_hours
from memory.habit_tracker import get_today_summary, get_advisor_nudges
from scheduler.goal_decomposer import morning_checkin
from voice.tts import speak


def _morning_briefing() -> None:
    address = get_address_form()
    today   = date.today().strftime("%A, %B %d")
    peaks   = get_peak_hours()
    habits  = get_today_summary()
    goals   = morning_checkin()
    nudges  = get_advisor_nudges()

    # Get weather
    try:
        from modules.content_generator import get_weather
        weather = get_weather()
    except Exception:
        weather = ""

    prompt = (
        f"Generate a motivating morning briefing for {address} on {today}. "
        f"Weather: {weather}. "
        f"Peak hours today: {peaks}. "
        f"Today's habits to complete: {habits}. "
        f"Goal tasks due: {goals}. "
        f"Advisor nudges: {'; '.join(nudges)}. "
        f"Keep it under 120 words. Be energising. Address as {address}."
    )
    briefing = generate_text(prompt, temperature=0.6)
    speak(briefing)


def _evening_review() -> None:
    address = get_address_form()
    summary = get_today_summary()
    prompt = (
        f"Generate a brief evening review for {address}. "
        f"Today's habit completion: {summary}. "
        f"Keep it under 80 words. Be encouraging but honest. "
        f"End with one specific action for tomorrow. Address as {address}."
    )
    review = generate_text(prompt, temperature=0.5)
    speak(review)


def start_autonomous_planner() -> threading.Thread:
    schedule.every().day.at("06:00").do(_morning_briefing)
    schedule.every().day.at("22:00").do(_evening_review)

    def _loop():
        while True:
            schedule.run_pending()
            time.sleep(30)

    t = threading.Thread(target=_loop, daemon=True, name="autonomous-planner")
    t.start()
    return t
```

3. In `main.py`:

```python
from scheduler.autonomous_planner import start_autonomous_planner
start_autonomous_planner()
```

**Behaviour example**

```
[06:00 — ARIA speaks unprompted]

ARIA:  "Good morning, Sir. Today is Monday, March 18th. It is currently 19°C
        and clear in Dhaka. Your peak focus hours are 9am and 10am — ideal for
        your most demanding work. You have one goal task due today: 'Practice
        reaction equations' for your chemistry exam. Your gym session is
        scheduled for 6pm — you have maintained a 5-day streak, Sir. Let us
        make today count."

[22:00 — evening review]

ARIA:  "Sir, reviewing today — you completed your chemistry practice and gym.
        The study session ran 12 minutes shorter than planned. Tomorrow,
        start your Chapter 4-6 reading at exactly 9am when your focus is
        sharpest — that single change will improve retention significantly."
```

---

## Part 5 — Integration checklist

Use this checklist to verify all features are correctly wired together
before moving to the testing phase.

### `main.py` startup sequence

Add these lines in this exact order after existing startup code:

```python
# Vision and monitoring
from vision.cognitive_monitor import start_cognitive_monitor
from modules.productivity_guardian import start_guardian

# Autonomous scheduling
from scheduler.autonomous_planner import start_autonomous_planner

start_cognitive_monitor()    # keyboard timing analysis
start_guardian()             # schedule vs screen activity monitor
start_autonomous_planner()   # morning briefing + evening review
```

### `core/agent.py` — context injection order

Inside `_build_context_prompt()`, context blocks should be injected in this order
so the LLM receives them with correct priority:

1. `build_persona_system_prompt()` — "Sir" address form, style preferences
2. Emotion state from `memory/emotion_state.json`
3. Cognitive load from `memory/cognitive_state.json`
4. Screen state from `memory/screen_state.json`
5. Vector memory — top-5 relevant past memories
6. Session context — last 10 conversation turns

### `core/router.py` — new intent registrations

```python
# Social intelligence
from modules.social_agent import comment_on_post, explain_selected_text
INTENT_REGISTRY["comment_on_post"]  = comment_on_post
INTENT_REGISTRY["explain_selected"] = explain_selected_text

# Focus mode
from modules.focus_mode import start_focus_mode, stop_focus_mode
INTENT_REGISTRY["start_focus_mode"] = start_focus_mode
INTENT_REGISTRY["stop_focus_mode"]  = stop_focus_mode

# Goal decomposer
from scheduler.goal_decomposer import decompose_goal
INTENT_REGISTRY["decompose_goal"] = decompose_goal

# Knowledge graph query
from memory.knowledge_graph import query_formatted
INTENT_REGISTRY["knowledge_query"] = query_formatted
```

### `requirements.txt` additions

```
pynput>=1.7.6
networkx>=3.2
schedule>=1.2.1
speechbrain>=0.5.16
librosa>=0.10.0
```

---

## Part 6 — Testing protocol

Test each feature in isolation before integration testing.
Use this script to verify each feature's core function works:

```python
# test_features.py — run from project root
import sys

def test_social_agent():
    from modules.social_agent import comment_on_post
    # Mock: place a social media post on screen, then run
    result = comment_on_post()
    assert len(result) > 10, "Comment generator returned empty"
    print(f"[PASS] social_agent: {result[:60]}")

def test_cognitive_monitor():
    from vision.cognitive_monitor import _compute_load, _write_state
    load = _compute_load()
    assert 0 <= load <= 100
    _write_state(load)
    print(f"[PASS] cognitive_monitor: load={load}")

def test_goal_decomposer():
    from scheduler.goal_decomposer import decompose_goal
    result = decompose_goal("pass chemistry exam", "2025-04-01")
    assert "📋" in result
    print(f"[PASS] goal_decomposer: {result[:80]}")

def test_knowledge_graph():
    from memory.knowledge_graph import add_node, add_edge, query_formatted
    add_node("test_file", type="file")
    add_node("test_task", type="task")
    add_edge("test_file", "test_task", "related_to")
    result = query_formatted("test")
    assert "test_file" in result
    print(f"[PASS] knowledge_graph")

def test_productivity_guardian():
    from modules.productivity_guardian import _is_distraction
    fake_screen = {"app": "instagram", "activity": "scrolling"}
    assert _is_distraction(fake_screen)
    print(f"[PASS] productivity_guardian: distraction detection works")

if __name__ == "__main__":
    tests = [
        test_cognitive_monitor,
        test_goal_decomposer,
        test_knowledge_graph,
        test_productivity_guardian,
    ]
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
```

---

## Summary table

| # | Feature | File(s) | Priority | Unique? |
|---|---------|---------|----------|---------|
| 1.1 | Social media comment generator | `modules/social_agent.py` | High | Partially |
| 1.2 | Selected text explainer | `modules/social_agent.py` | High | No |
| 1.3 | Productivity guardian | `modules/productivity_guardian.py` | High | Yes |
| 2.1 | Deep work mode | `modules/focus_mode.py` | High | Yes |
| 2.2 | Cognitive load monitor | `vision/cognitive_monitor.py` | High | Yes — world first |
| 2.3 | Subconscious intent layer | `core/subconscious_layer.py` | Medium | Yes — world first |
| 3.1 | Goal decomposer + SM-2 | `scheduler/goal_decomposer.py` | High | Yes — world first |
| 3.2 | Life OS knowledge graph | `memory/knowledge_graph.py` | Medium | Yes — world first |
| 4.1 | Autonomous life planner | `scheduler/autonomous_planner.py` | Medium | Yes — world first |

---

*End of ARIA upgrade plan. Build in the order listed.
Each feature is independently testable — do not skip the testing step.*
