"""ARIA — Smart Notifier Module.

Desktop toast notifications and tray alerts with intelligence:
  - Reads incoming notifications aloud: "Sir, you have a new notification"
  - For SMS/email notifications: asks permission to reply
  - For other notifications: suggests what action to take
  - Used by proactive advisor and scheduler for non-intrusive reminders
"""

from __future__ import annotations
import json, os, re, threading, time, queue
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Toast notifications (desktop)
# ---------------------------------------------------------------------------

def send_toast(
    title: str = "ARIA",
    message: str = "",
    duration: int = 5,
    icon: str = "",
    **_: Any,
) -> str:
    """Send a desktop toast notification."""
    if not message.strip():
        return "No message to notify."
    try:
        # Windows 10/11 toast via PowerShell
        import subprocess
        ps_script = f'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = @"
<toast>
    <visual>
        <binding template="ToastGeneric">
            <text>{_escape_xml(title)}</text>
            <text>{_escape_xml(message[:250])}</text>
        </binding>
    </visual>
    <audio silent="true"/>
</toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("ARIA Assistant")
$notifier.Show($toast)
'''
        subprocess.run(["powershell", "-Command", ps_script],
            capture_output=True, timeout=10)
        return f"Notification sent: {title}"
    except Exception:
        # Fallback: use win10toast or plyer
        try:
            from plyer import notification as plyer_notify
            plyer_notify.notify(title=title, message=message[:250],
                timeout=duration, app_name="ARIA")
            return f"Notification sent: {title}"
        except Exception:
            try:
                # Last resort: simple message box
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, message[:250], title, 0x40)
                return f"Alert shown: {title}"
            except Exception as exc:
                return f"Failed to send notification: {exc}"


def _escape_xml(text: str) -> str:
    return (text or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;").replace("'","&apos;")


# ---------------------------------------------------------------------------
# Notification categories and smart suggestions
# ---------------------------------------------------------------------------

_NOTIFICATION_CATEGORIES = {
    "sms": {
        "keywords": ["sms", "text message", "imessage", "messages"],
        "action": "reply",
        "prompt": "Sir, you received a text message{from_text}. Would you like me to read it and draft a reply?",
    },
    "email": {
        "keywords": ["email", "gmail", "outlook", "mail", "inbox"],
        "action": "reply",
        "prompt": "Sir, you have a new email{from_text}. Shall I read it? I can help draft a reply if needed.",
    },
    "call": {
        "keywords": ["call", "phone", "incoming call", "missed call"],
        "action": "callback",
        "prompt": "Sir, you have a {type} call{from_text}. Would you like me to set a reminder to call back?",
    },
    "calendar": {
        "keywords": ["calendar", "event", "meeting", "appointment", "reminder"],
        "action": "acknowledge",
        "prompt": "Sir, you have an upcoming event: {content}. Should I adjust your schedule?",
    },
    "social": {
        "keywords": ["facebook", "instagram", "twitter", "whatsapp", "telegram",
                     "messenger", "snapchat", "linkedin", "discord"],
        "action": "view",
        "prompt": "Sir, you have a notification from {source}. Would you like to check it?",
    },
    "system": {
        "keywords": ["update", "battery", "storage", "security", "windows"],
        "action": "info",
        "prompt": "Sir, system notification: {content}. {suggestion}",
    },
}

def categorize_notification(title: str, body: str, source: str = "") -> Dict[str, Any]:
    """Categorize a notification and suggest an action."""
    combined = f"{title} {body} {source}".lower()
    for cat_name, cat_info in _NOTIFICATION_CATEGORIES.items():
        if any(kw in combined for kw in cat_info["keywords"]):
            return {"category": cat_name, "action": cat_info["action"],
                    "prompt_template": cat_info["prompt"]}
    return {"category": "general", "action": "info",
            "prompt_template": "Sir, you have a new notification: {content}"}


def format_notification_announcement(
    title: str, body: str, source: str = "", sender: str = "",
) -> Dict[str, str]:
    """Format a notification for voice announcement with smart suggestions."""
    cat = categorize_notification(title, body, source)
    from_text = f" from {sender}" if sender else ""
    content = f"{title}: {body[:100]}" if body else title
    suggestion = ""
    if cat["category"] == "system":
        if "update" in body.lower():
            suggestion = "I can schedule this update for later if you're busy."
        elif "battery" in body.lower():
            suggestion = "You might want to plug in your charger."
        elif "storage" in body.lower():
            suggestion = "I can help clean up temporary files."
    announcement = cat["prompt_template"].format(
        from_text=from_text, content=content, source=source,
        type="missed" if "missed" in body.lower() else "incoming",
        suggestion=suggestion)
    return {"announcement": announcement, "category": cat["category"],
            "action": cat["action"], "title": title, "body": body}


# ---------------------------------------------------------------------------
# Notification listener (Windows)
# ---------------------------------------------------------------------------

_NOTIFICATION_QUEUE: queue.Queue = queue.Queue(maxsize=100)
_LISTENER_RUNNING = False
_LISTENER_THREAD: Optional[threading.Thread] = None

def _notification_listener_loop(callback: Optional[Callable] = None) -> None:
    """Background loop that monitors Windows notification center."""
    global _LISTENER_RUNNING
    _LISTENER_RUNNING = True
    last_count = 0
    while _LISTENER_RUNNING:
        try:
            import subprocess
            # Query Windows Action Center for notification count
            ps = '''
try {
    $listener = [Windows.UI.Notifications.Management.UserNotificationListener,Windows.UI.Notifications,ContentType=WindowsRuntime]::Current
    $notifs = $listener.GetNotificationsAsync([Windows.UI.Notifications.Management.NotificationKinds]::Toast).GetResults()
    $notifs.Count
} catch { 0 }
'''
            r = subprocess.run(["powershell", "-Command", ps],
                capture_output=True, text=True, timeout=10)
            count = int(r.stdout.strip() or "0")
            if count > last_count:
                new_count = count - last_count
                info = {"count": new_count, "timestamp": datetime.now(timezone.utc).isoformat()}
                _NOTIFICATION_QUEUE.put(info)
                if callback:
                    try:
                        callback(info)
                    except Exception:
                        pass
            last_count = count
        except Exception:
            pass
        time.sleep(10)  # Check every 10 seconds


def start_notification_listener(callback: Optional[Callable] = None) -> str:
    """Start monitoring for new desktop notifications."""
    global _LISTENER_THREAD, _LISTENER_RUNNING
    if _LISTENER_RUNNING:
        return "Notification listener is already running."
    _LISTENER_THREAD = threading.Thread(
        target=_notification_listener_loop, args=(callback,), daemon=True)
    _LISTENER_THREAD.start()
    return "Notification listener started."


def stop_notification_listener() -> str:
    global _LISTENER_RUNNING
    _LISTENER_RUNNING = False
    return "Notification listener stopped."


def get_pending_notifications() -> List[Dict[str, Any]]:
    """Get all pending notifications from the queue."""
    notifications = []
    while not _NOTIFICATION_QUEUE.empty():
        try:
            notifications.append(_NOTIFICATION_QUEUE.get_nowait())
        except queue.Empty:
            break
    return notifications


# ---------------------------------------------------------------------------
# Proactive notification handler (used by agent)
# ---------------------------------------------------------------------------

def handle_notification(
    title: str, body: str, source: str = "", sender: str = "",
    ask_fn: Optional[Callable] = None, speak_fn: Optional[Callable] = None,
) -> str:
    """Intelligently handle a notification.
    
    1. Announce it
    2. For SMS/email: ask permission to draft reply
    3. For others: suggest action
    """
    info = format_notification_announcement(title, body, source, sender)
    announcement = info["announcement"]
    
    # Speak the announcement
    if speak_fn:
        try:
            speak_fn(announcement)
        except Exception:
            pass
    
    # For reply-able notifications, ask permission
    if info["action"] == "reply" and ask_fn:
        answer = ask_fn(f"{announcement}\nShould I help draft a reply?")
        if answer and answer.strip().lower() in {"yes","y","sure","ok","please"}:
            return f"{announcement}\n\n📝 Ready to draft a reply. What should I say?"
    
    return announcement


# ---------------------------------------------------------------------------
# Scheduler/advisor notification shortcuts
# ---------------------------------------------------------------------------

def notify_reminder(task: str, time_str: str = "", **_: Any) -> str:
    """Send a task reminder notification."""
    msg = f"Reminder: {task}"
    if time_str:
        msg += f" (scheduled for {time_str})"
    return send_toast(title="⏰ ARIA Reminder", message=msg)

def notify_schedule_change(change: str, **_: Any) -> str:
    """Notify about a schedule change."""
    return send_toast(title="📅 Schedule Updated", message=change)

def notify_break(message: str = "Time for a short break!", **_: Any) -> str:
    """Suggest a break."""
    return send_toast(title="☕ Break Time", message=message)

def notify_achievement(message: str = "", **_: Any) -> str:
    """Celebrate an achievement."""
    return send_toast(title="🎉 Achievement!", message=message or "Great job!")
