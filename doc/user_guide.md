# ARIA User Operating & Command Manual

Welcome to **ARIA** (Autonomous Reactive & Intelligent Assistant). This guide explains how to interact with ARIA using voice commands, desktop UI overlays, keyboard shortcuts, and gesture controls.

---

## 1. Quick Start & Execution Modes

### 1.1 Running with Desktop UI (Default)
To launch ARIA with the full PySide6 glassmorphic interface, floating HUD overlay, and system tray icon:
```bash
python main.py
```

### 1.2 Running in Headless Mode (Terminal & Voice Only)
If running on a server or without a display environment:
```bash
python main.py --headless
```

---

## 2. Voice & Wake Word Interaction

- **Wake Word:** Say **"Hey ARIA"** to activate voice capture.
- **Session Timeout:** After activation, ARIA remains listening for follow-up commands for **25 seconds** (configurable in settings).
- **Voice Feedback:** ARIA replies vocally using offline TTS.

---

## 3. Voice & Text Command Cheat Sheet

### 3.1 App Launching & System Control
- *"Open Notepad"*
- *"Close Chrome"*
- *"Set volume to 40%"*
- *"Take a screenshot"*
- *"Lock screen"*
- *"Turn on Wi-Fi"* / *"Turn off Bluetooth"*

### 3.2 File Operations
- *"Open folder Downloads"*
- *"List files in Desktop"*
- *"Create file report.txt with content 'Project updates'"*
- *"Move file notes.txt to Documents"*
- *"Delete file temp.txt"* (Moves file safely to Recycle Buffer)

### 3.3 Deep Work & Focus Mode
- *"Start focus mode for 45 minutes on coding"* (Blocks distracting websites, starts Pomodoro timer)
- *"Stop focus mode"*

### 3.4 Research & Web Search
- *"Search web for latest AI news"*
- *"Find papers on transformer architecture on ArXiv"*
- *"Get stock price for AAPL"*
- *"Check weather in New York"*

### 3.5 Productivity, Scheduling & Goals
- *"Plan my day with morning workout at 7am and coding session at 10am"*
- *"What's next on my schedule?"*
- *"Decompose goal 'Build ARIA release v2' with deadline 2026-08-01"*

### 3.6 Habits & Profile Management
- *"Show my habits"*
- *"Mark habit exercise done"*
- *"Show my profile"*

### 3.7 Settings Management
- *"Show settings"*
- *"Turn off emotion detector"*
- *"Switch model to mistral"*
- *"Change morning briefing time to 07:30"*

### 3.8 Media Control & Messaging
- *"Play music"* / *"Pause music"*
- *"Skip track"*
- *"Send message to Alex saying 'Meeting starts in 10 minutes'"*

---

## 4. Special Features

### 4.1 Kinetic Mode (Gesture Control)
Activate touchless webcam hand gesture control:
- **Voice Command:** *"Activate kinetic mode"*
- **Controls:** Hand movements adjust volume, scroll pages, or switch application windows. Press **Q** in the webcam window to stop.

### 4.2 Highlight & Explain Text
Select any text in any document or web page, then invoke:
- **Command:** *"Explain this selected text"*
- **Action:** ARIA extracts text from the Windows clipboard, analyzes context, and displays/narrates an instant summary.

### 4.3 Social Media Comment Generation
- **Command:** *"Generate thoughtful comment on post"*
- **Action:** Captures active social post on screen via vision model (LLaVA) and drafts a tailored reply.

---

## 5. Security & Risk Confirmations

When requesting high-risk operations (e.g. file deletion, host file modifications, system shutdown):
1. ARIA pauses execution.
2. A confirmation prompt pops up in the HUD / CLI asking:  
   > *Are you sure you want to proceed with: [Action Details]? (yes/no)*
3. Reply **"yes"** or **"confirm"** to execute, or **"no"** to cancel.
