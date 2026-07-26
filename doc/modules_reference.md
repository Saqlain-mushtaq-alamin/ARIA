# ARIA Complete Module Reference Manual

This document provides a comprehensive component reference for all sub-packages and python files in the ARIA codebase.

---

## 1. Directory Structure Overview

```
ARIA/
├── config/              # Centralized configuration & settings management
├── core/                # Core agent orchestrator, classifier, subconscious layer & router
├── modules/             # System tools, productivity engines, social agent & messaging
├── vision/              # Emotion detection, cognitive load monitoring, gesture & OCR reader
├── voice/               # Wake word listener, Speech-To-Text (STT) & Text-To-Speech (TTS)
├── ui/                  # PySide6 desktop HUD, chat window, floating overlay & system tray
├── memory/              # Vector database, SQLite logs, habit tracker & knowledge graph
├── safety/              # Harm classifier, risk assessment, confirmation engine & recycle buffer
├── scheduler/           # Goal decomposer, daily tracker, optimizer & autonomous planner
├── tests/               # Test suites covering Phase 1 through Phase 6
├── planing/             # Dev guides, test plans, and deployment plans
├── main.py              # Main entry point & daemon thread bootstrap
├── requirements.txt     # Complete Python dependencies
├── UPGRADE.md           # Upgrade release notes & history
└── upgrade_summary.md   # Architectural upgrade summary
```

---

## 2. Core Package (`core/`)

| File | Primary Classes / Functions | Description |
|------|-----------------------------|-------------|
| `agent.py` | `process_text()`, `_cli_ask()`, `inject_context()` | Orchestrates input processing, context aggregation (screen, emotion, cognitive load), system prompt creation, LLM calls, and safety gate dispatch. |
| `router.py` | `dispatch_intent()`, `INTENT_REGISTRY`, `INTENT_ALIASES` | Resolves mapped intents to Python functions. Manages power, wifi, volume, browser, media, scheduling, settings, and file operations. |
| `intent_classifier.py` | `classify_intent()`, `IntentResult` | Formats prompts for local LLMs to parse freeform user input into structured JSON intent payloads with arguments. |
| `command_parser.py` | `parse_multistep_command()`, `split_steps()` | Breaks down multi-step instructions (e.g. *"open Chrome and search for news then lock screen"*) into sequentially executed steps. |
| `question_generator.py` | `generate_clarification()` | Formulates clarification questions when user intent is ambiguous or missing required parameters. |
| `subconscious_layer.py` | `SubconsciousTracker`, `detect_unresolved_topics()` | Tracks repeated unresolved queries across user interactions to proactively surface contextual suggestions. |

---

## 3. Extension Modules (`modules/`)

| File | Primary Functions | Description |
|------|-------------------|-------------|
| `browser_agent.py` | `open_url()`, `search_web()`, `click_element()`, `fill_form()`, `extract_text()` | Automates web browser navigation using Selenium/Playwright or native default browser opening. |
| `social_agent.py` | `comment_on_post()`, `explain_selected_text()` | Uses vision models (LLaVA) or text LLMs to generate context-aware social post comments and explain highlighted screen text. |
| `focus_mode.py` | `start_focus_mode()`, `stop_focus_mode()` | Manages deep work sessions, blocking distraction domains via Windows hosts file (`C:\Windows\System32\drivers\etc\hosts`) and running Pomodoro timers. |
| `productivity_guardian.py` | `start_guardian()`, `check_distractions()` | Background daemon monitoring active window titles against configured distraction lists, prompting alerts when unproductive. |
| `system_control.py` | `open_app()`, `close_window()`, `set_volume()`, `get_clipboard()`, `type_text()`, `get_selected_text()` | Interacts with Windows API using `pywinauto`, `psutil`, `pyautogui`, and `ctypes`. |
| `downloader.py` | `download_command()`, `smart_download()` | Handles single and batch URL downloads with safety validation, speed optimization, and directory sorting. |
| `messenger.py` | `send_message()`, `read_unread_messages()` | Integrates with messaging platforms or local notifications to read and send messages. |
| `media_control.py` | `media_play_pause()`, `media_next()`, `media_prev()`, `media_volume()`, `media_search()` | Controls media playback across Windows apps (Spotify, Chrome, VLC) via virtual key events. |
| `notifier.py` | `send_toast()`, `notify_reminder()` | Displays native Windows 10/11 toast notifications. |
| `content_generator.py` | `generate_content()` | Generates text content, essays, code snippets, or email drafts using central settings parameters. |

---

## 4. Vision Engine (`vision/`)

| File / Folder | Primary Capabilities | Description |
|---------------|----------------------|-------------|
| `emotion_detector.py` | `start_emotion_detector_thread()`, `EmotionDetectorConfig` | Captures webcam frames periodically, uses DeepFace to identify emotions (tired, stressed, happy), and adjusts schedule automatically. |
| `cognitive_monitor.py` | `start_cognitive_monitor()`, `getCognitiveState()` | Monitors keyboard stroke intervals and backspace frequency using `pynput` to gauge typing fatigue and cognitive load. |
| `screen_reader.py` | `start_screen_reader()`, `capture_screen_context()` | Takes periodic screenshots, runs OCR via Tesseract/EasyOCR, and checks for workflow bottlenecks. |
| `gesture_contoll/` | `gesture_controller.py` | Real-time MediaPipe hand gesture recognition script for controlling volume, media, and window navigation. |

---

## 5. Voice System (`voice/`)

| File | Primary Functions | Description |
|------|-------------------|-------------|
| `wake_word.py` | `start_wake_word_listener()` | Runs `openwakeword` on microphone input stream to detect trigger phrases ("Hey ARIA") with minimal CPU overhead. |
| `stt.py` | `listen_and_transcribe()` | Records microphone input upon wake-word trigger and transcribes audio to text via local Whisper. |
| `tts.py` | `speak()` | Converts text responses to speech using offline engines (`pyttsx3`) or neural TTS (`Coqui-TTS`). |
| `audio_utils.py` | `record_audio()`, `normalize_audio()` | Provides audio preprocessing, noise suppression, and RMS volume detection utilities. |

---

## 6. UI Engine (`ui/`)

| File | Primary Components | Description |
|------|--------------------|-------------|
| `app_runtime.py` | `run_ui()`, `AriaApplication` | Initializes the PySide6 Application event loop, system tray integration, and overlay windows. |
| `chat_window.py` | `ChatWindow` | Main PySide6 desktop interface featuring glassmorphic theme, chat history, command input, and setting controls. |
| `overlay.py` | `FloatingOverlay`, `HUDWidget` | Transparent HUD displaying assistant status, active schedule, vibe indicator, and quick action bar. |
| `tray_icon.py` | `AriaTrayIcon` | System tray icon with context menu for fast mode switching, pausing daemons, or toggling HUD. |
| `scheduler.py` | `SchedulerUI` | Visual agenda and task tracker interface displaying scheduled blocks and focus timers. |

---

## 7. Memory & Data Storage (`memory/`)

| File | Primary Components | Description |
|------|--------------------|-------------|
| `vector_store.py` | `VectorStore`, `add_document()`, `similarity_search()` | ChromaDB integration for long-term semantic memory storage and retrieval. |
| `conversation_log.py` | `log_interaction()`, `get_history()` | SQLite-backed database (`conversation.db`) storing full text interaction histories, timestamps, and execution sources. |
| `user_profile.py` | `UserProfile`, `get_profile_summary()` | JSON store (`user_profile.json`) tracking persistent user preferences, identity attributes, and style rules. |
| `habit_tracker.py` | `HabitTracker`, `get_all_habits_summary()`, `mark_habit_done()` | Habit tracking system with streak counting and daily completion metrics. |
| `knowledge_graph.py` | `KnowledgeGraph`, `query_formatted()` | Entity-relationship graph linking applications, files, user tasks, and search topics. |

---

## 8. Safety & Policy (`safety/`)

| File | Primary Functions | Description |
|------|-------------------|-------------|
| `harm_classifier.py` | `assess_risk()`, `RiskLevel` | Evaluates command risk levels (`SAFE`, `CONFIRMATION_REQUIRED`, `BLOCKED`) based on rules and keyword heuristics. |
| `confirmation_engine.py` | `request_confirmation()` | Prompts user via GUI or CLI ask callback to confirm execution of high-risk actions. |
| `recycle_buffer.py` | `safe_delete()` | Intercepts file deletion calls and moves targeted files to Windows Recycle Bin or local backup buffer. |
| `audit_log.py` | `log_audit_event()` | Appends safety audit records to `data/audit_log.jsonl` for compliance and debugging. |

---

## 9. Scheduler & Planner (`scheduler/`)

| File | Primary Functions | Description |
|------|-------------------|-------------|
| `tracker.py` | `create_schedule_from_text()`, `show_schedule()`, `whats_next()`, `apply_tired_postpone_rule()` | Manages daily time slots, schedule persistence (`data/task_tracker.json`), and fatigue-based automatic rescheduling. |
| `goal_decomposer.py` | `decompose_goal()` | Breaks down broad goals into sub-tasks with SuperMemo-2 (SM-2) spaced repetition review dates. |
| `autonomous_planner.py` | `start_autonomous_planner()` | Runs background scheduler for morning briefing triggers and evening progress reviews. |
| `optimizer.py` | `optimize_schedule()` | Re-orders daily tasks based on user cognitive peak hours and priority levels. |
| `routine_generator.py` | `generate_daily_routine()` | Constructs template daily routines tailored to weekday/weekend settings. |
| `weekly_review.py` | `generate_weekly_report()` | Summarizes weekly productivity metrics, habit completion rates, and goal velocity. |

---

## 10. Configuration & Settings (`config/`)

| File | Primary Functions | Description |
|------|-------------------|-------------|
| `settings.py` | `get()`, `set_value()`, `apply_to_env()`, `format_settings()` | Central JSON-backed configuration manager with dot-notation dot access, default fallback, dynamic updating, and environment variable synchronization. |
| `aria_settings.json` | JSON Schema File | Persistent user settings file storing LLM models, voice modes, vision intervals, safety thresholds, and UI options. |
