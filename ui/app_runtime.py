"""ui/app_runtime.py

Bootstraps the PyQt6 desktop UI (overlay + tray + chat + scheduler) and
connects it to the existing ARIA core (agent routing, voice, gestures,
tracker persistence).

This file intentionally contains only integration/glue code; UI rendering
lives in the individual ui/*.py modules.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from core.agent import process_text
from memory.conversation_log import log_interaction
from scheduler import tracker
from ui.chat_window import AriaChatWindow
from ui.overlay import AriaOverlay
from ui.scheduler import AriaSchedulerView, ScheduledTask
from ui.tray_icon import AriaTrayIcon
from voice.stt import listen_and_transcribe
from voice.tts import speak
from voice.wake_word import start_wake_word_listener


class _UiSignals(QObject):
    chat_add = pyqtSignal(str, str)  # role, text
    tray_state = pyqtSignal(str)  # idle|listening|processing|muted
    overlay_mic_state = pyqtSignal(str)  # AriaOverlay MIC_* value
    overlay_voice_enabled = pyqtSignal(bool)
    overlay_gesture_enabled = pyqtSignal(bool)
    overlay_task_text = pyqtSignal(str)
    notification = pyqtSignal(str, str)  # title, message
    refresh_tasks = pyqtSignal()
    scheduler_reload = pyqtSignal()


class GestureController:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.start()
        else:
            self.stop()

    def start(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return

            repo_root = Path(__file__).resolve().parents[1]
            script_path = repo_root / "vision" / "gesture_contoll" / "gesture_controller.py"
            if not script_path.exists():
                raise FileNotFoundError(
                    "Gesture controller not found at vision/gesture_contoll/gesture_controller.py"
                )

            self._proc = subprocess.Popen(
                [sys.executable, str(script_path)],
                cwd=str(repo_root),
            )

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None

        if proc is None:
            return

        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass


class VoiceController:
    """Wake-word voice controller that can be started/stopped from the UI."""

    def __init__(self, *, signals: _UiSignals) -> None:
        self._signals = signals
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._enabled = False
        self._lock = threading.Lock()

    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            if self._enabled == enabled:
                return
            self._enabled = enabled

        if enabled:
            self.start()
        else:
            self.stop()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._stop_event.clear()
                return
            self._stop_event.clear()

        self._signals.tray_state.emit("idle")
        self._signals.overlay_voice_enabled.emit(True)

        def _wake_word_callback(device_index: int) -> None:
            # Wake word fired; capture a command session.
            self._signals.tray_state.emit("listening")
            self._signals.overlay_mic_state.emit(AriaOverlay.MIC_LISTENING)

            session_seconds = float(os.getenv("VOICE_SESSION_SECONDS", "25"))
            max_empty = int(os.getenv("VOICE_SESSION_MAX_EMPTY", "2"))
            stt_model = os.getenv("VOICE_MODEL", "base").strip() or "base"

            deadline = time.time() + max(0.0, session_seconds)
            empty_count = 0

            while True:
                if self._stop_event.is_set() or not self.is_enabled():
                    return

                if session_seconds > 0 and time.time() > deadline:
                    return

                try:
                    text = listen_and_transcribe(model_name=stt_model, input_device_index=device_index)
                except Exception as exc:
                    self._signals.notification.emit("Voice", f"STT failed: {exc}")
                    return

                if not text:
                    empty_count += 1
                    if session_seconds > 0 and empty_count >= max_empty:
                        return
                    continue

                empty_count = 0
                deadline = time.time() + max(0.0, session_seconds)

                # Process command synchronously in this voice thread (already background)
                self._signals.tray_state.emit("processing")
                self._signals.overlay_mic_state.emit(AriaOverlay.MIC_PROCESSING)

                try:
                    response = process_text(text)
                except Exception as exc:
                    response = f"Error while processing: {exc}"

                self._signals.chat_add.emit("user", text)
                self._signals.chat_add.emit("aria", response)
                self._signals.refresh_tasks.emit()

                try:
                    log_interaction(text, response, metadata={"source": "voice"})
                except Exception:
                    pass

                try:
                    speak(response)
                except Exception:
                    pass

                self._signals.tray_state.emit("idle")
                self._signals.overlay_mic_state.emit(AriaOverlay.MIC_IDLE)

        self._thread = start_wake_word_listener(_wake_word_callback, stop_event=self._stop_event)

    def stop(self) -> None:
        self._stop_event.set()
        self._signals.tray_state.emit("muted")
        self._signals.overlay_voice_enabled.emit(False)
        self._signals.overlay_mic_state.emit(AriaOverlay.MIC_IDLE)


class AriaDesktopUi:
    def __init__(self) -> None:
        self._signals = _UiSignals()

        self.overlay = AriaOverlay()
        self.chat = AriaChatWindow()
        self.scheduler = AriaSchedulerView(load_demo=False)
        self.tray = AriaTrayIcon()

        self.voice = VoiceController(signals=self._signals)
        self.gesture = GestureController()

        self._wire_signals()

        # Startup UI state.
        self.overlay.show()
        self.tray.show()
        self.tray.show_notification("ARIA", "Assistant UI is online.")

        # Default: voice enabled unless explicitly disabled.
        voice_default = os.getenv("VOICE_UI_DEFAULT", "1").strip().lower() in {"1", "true", "yes", "on"}
        self.voice.set_enabled(voice_default)

        self._refresh_task_views()
        self._start_task_poll()

        # Optional: vision screen reader (LLaVA via Ollama) for stuck detection.
        self._start_screen_reader()

    # ── Wiring ───────────────────────────────────────────────────────────

    def _wire_signals(self) -> None:
        # UI → core
        self.overlay.command_submitted.connect(lambda t: self._handle_text(t, source="overlay"))
        self.chat.message_sent.connect(lambda t: self._handle_text(t, source="chat"))

        self.overlay.mic_toggled.connect(self._on_voice_toggle)
        self.tray.voice_toggled.connect(self._on_voice_toggle)

        self.overlay.gesture_toggled.connect(self._on_gesture_toggle)
        self.tray.gesture_toggled.connect(self._on_gesture_toggle)

        self.tray.open_chat.connect(self._show_chat)
        self.tray.open_scheduler.connect(self._show_scheduler)
        self.tray.show_overlay.connect(self._toggle_overlay)
        self.tray.quit_requested.connect(self._quit)

        # Scheduler → persistence
        self.scheduler.task_added.connect(self._on_scheduler_task_added)
        self.scheduler.task_updated.connect(self._on_scheduler_task_updated)
        self.scheduler.task_deleted.connect(self._on_scheduler_task_deleted)
        self.scheduler.task_completed.connect(self._on_scheduler_task_completed)

        # Background → UI
        self._signals.chat_add.connect(self.chat.add_message)
        self._signals.tray_state.connect(self.tray.set_state)
        self._signals.overlay_mic_state.connect(self.overlay.set_mic_state)
        self._signals.overlay_voice_enabled.connect(self.overlay.set_voice_enabled)
        self._signals.overlay_gesture_enabled.connect(self.overlay.set_gesture_enabled)
        self._signals.overlay_task_text.connect(self.overlay.set_task)
        self._signals.notification.connect(self.tray.show_notification)
        self._signals.refresh_tasks.connect(self._refresh_task_views)
        self._signals.scheduler_reload.connect(self._reload_scheduler_from_tracker)

    def _start_screen_reader(self) -> None:
        try:
            from vision.screen_reader import start_screen_reader
        except Exception:
            return

        def _proactive(msg: str) -> None:
            # Route proactive suggestions into the chat without stealing focus.
            try:
                self._signals.chat_add.emit("aria", msg)
            except Exception:
                pass
            # Also show a lightweight notification so it isn't missed.
            try:
                self._signals.notification.emit("ARIA", msg)
            except Exception:
                pass

        try:
            start_screen_reader(proactive_callback=_proactive)
        except Exception:
            # Keep UI resilient if dependencies (Pillow/mss/Ollama) are missing.
            return

    # ── UI actions ────────────────────────────────────────────────────────

    def _on_voice_toggle(self, enabled: bool) -> None:
        self.voice.set_enabled(enabled)
        self.tray.set_voice_enabled(enabled)
        self.overlay.set_voice_enabled(enabled)

    def _on_gesture_toggle(self, enabled: bool) -> None:
        try:
            self.gesture.set_enabled(enabled)
            self._signals.overlay_gesture_enabled.emit(enabled)
            self.tray.set_gesture_enabled(enabled)
        except Exception as exc:
            self._signals.notification.emit("Gesture", str(exc))
            self._signals.overlay_gesture_enabled.emit(False)
            self.tray.set_gesture_enabled(False)

    def _toggle_overlay(self) -> None:
        self.overlay.setVisible(not self.overlay.isVisible())

    def _show_chat(self) -> None:
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()

    def _show_scheduler(self) -> None:
        self._reload_scheduler_from_tracker()
        self.scheduler.show()
        self.scheduler.raise_()
        self.scheduler.activateWindow()

    def _quit(self) -> None:
        try:
            self.voice.stop()
        except Exception:
            pass
        try:
            self.gesture.stop()
        except Exception:
            pass
        QApplication.quit()

    # ── Agent execution ───────────────────────────────────────────────────

    def _handle_text(self, text: str, *, source: str) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return

        # If user uses overlay quick command, bring the chat panel up so they
        # can see the conversation like a messenger.
        if source == "overlay":
            self._show_chat()

        self._signals.tray_state.emit("processing")
        self._signals.overlay_mic_state.emit(AriaOverlay.MIC_PROCESSING)

        # Add the user's message immediately for non-chat sources.
        # (The chat window already renders the user's bubble before emitting
        # message_sent, so we must not duplicate it.)
        if source != "chat":
            self._signals.chat_add.emit("user", cleaned)
            try:
                self.chat.show_typing(0)
            except Exception:
                pass

        def _run() -> None:
            try:
                response = process_text(cleaned)
            except Exception as exc:
                response = f"Error while processing: {exc}"

            self._signals.chat_add.emit("aria", response)
            self._signals.tray_state.emit("idle" if self.voice.is_enabled() else "muted")
            self._signals.overlay_mic_state.emit(AriaOverlay.MIC_IDLE)
            self._signals.refresh_tasks.emit()

            try:
                log_interaction(cleaned, response, metadata={"source": source})
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True, name="aria-agent-text").start()

    # ── Tracker ↔ UI refresh ──────────────────────────────────────────────

    def _refresh_task_views(self) -> None:
        # Overlay task line: now/next summary
        try:
            summary = tracker.whats_next()
            self._signals.overlay_task_text.emit(summary)
        except Exception:
            pass

        # Chat sidebar tasks
        try:
            state = tracker.load_state()
            day_key = getattr(tracker, "_today_iso")()
            day_tasks = (state.get("tasks") or {}).get(day_key) or {}

            tasks_payload = []
            for key, obj in day_tasks.items():
                title = str(obj.get("task") or "").strip() or key
                done = bool(obj.get("completed"))
                tasks_payload.append((str(key), title, "normal", done))

            # Stable order: by start time when present
            def _sort_key(item: tuple[str, str, str, bool]):
                k = item[0]
                obj = day_tasks.get(k, {})
                return (str(obj.get("start") or "99:99"), item[1])

            tasks_payload.sort(key=_sort_key)
            self.chat.set_tasks(tasks_payload)
        except Exception:
            # Keep UI resilient; tasks are optional.
            pass

    def _start_task_poll(self) -> None:
        self._task_timer = QTimer()
        self._task_timer.setInterval(60_000)
        self._task_timer.timeout.connect(self._refresh_task_views)
        self._task_timer.start()

    def _reload_scheduler_from_tracker(self) -> None:
        try:
            blocks = tracker.get_schedule_blocks()
        except Exception:
            blocks = []

        tasks: list[ScheduledTask] = []
        for b in blocks:
            title = str(b.get("task") or "").strip()
            if not title:
                continue
            start_s = str(b.get("start") or "00:00")
            end_s = str(b.get("end") or start_s)

            try:
                sh, sm = start_s.split(":")
                eh, em = end_s.split(":")
                start_m = int(sh) * 60 + int(sm)
                end_m = int(eh) * 60 + int(em)
                dur = max(5, end_m - start_m)
            except Exception:
                start_m = 9 * 60
                dur = 30

            from PyQt6.QtCore import QTime

            qt_start = QTime(start_m // 60 % 24, start_m % 60)
            task_id = tracker._normalize_key(title) if hasattr(tracker, "_normalize_key") else title.lower()

            # Completion flag comes from day tasks when available
            done = False
            try:
                state = tracker.load_state()
                day_key = getattr(tracker, "_today_iso")()
                day_tasks = (state.get("tasks") or {}).get(day_key) or {}
                obj = day_tasks.get(task_id) or day_tasks.get(tracker._normalize_key(title))
                if isinstance(obj, dict):
                    done = bool(obj.get("completed"))
            except Exception:
                pass

            tasks.append(ScheduledTask(id=task_id, title=title, start=qt_start, duration=dur, done=done))

        self.scheduler.set_tasks(tasks)

    # ── Scheduler persistence handlers ─────────────────────────────────────

    def _on_scheduler_task_added(self, task: ScheduledTask) -> None:
        self._persist_scheduler_task(task)
        self._signals.refresh_tasks.emit()

    def _on_scheduler_task_updated(self, task_id: str, changes: dict) -> None:
        task = self.scheduler.get_task(task_id)
        if not task:
            return
        self._persist_scheduler_task(task)
        self._signals.refresh_tasks.emit()

    def _on_scheduler_task_deleted(self, task_id: str) -> None:
        try:
            t = self.scheduler.get_task(task_id)
            tracker.delete_day_task((t.title if t else task_id))
        except Exception:
            pass
        self._signals.refresh_tasks.emit()

    def _on_scheduler_task_completed(self, task_id: str) -> None:
        task = self.scheduler.get_task(task_id)
        if not task:
            return
        try:
            tracker.set_day_task_completed(task.title, completed=task.done)
        except Exception:
            # fallback: best-effort
            try:
                if task.done:
                    tracker.mark_task_complete(task.title)
            except Exception:
                pass
        self._signals.refresh_tasks.emit()

    def _persist_scheduler_task(self, task: ScheduledTask) -> None:
        from PyQt6.QtCore import QTime

        start = task.start.toString("HH:mm")
        total = task.start.hour() * 60 + task.start.minute() + int(task.duration)
        end = QTime(total // 60 % 24, total % 60).toString("HH:mm")
        try:
            tracker.upsert_day_task(task.title, start=start, end=end, completed=bool(task.done))
        except Exception:
            pass


def run_ui() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not AriaTrayIcon.isSystemTrayAvailable():
        print("System tray not available.")
        return 1

    _ = AriaDesktopUi()
    return app.exec()
