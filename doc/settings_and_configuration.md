# ARIA Settings & Configuration Guide

> **Module Location:** `config/settings.py`  
> **Storage File:** `config/aria_settings.json` & `config/.env`  

---

## 1. Centralized Settings Architecture

ARIA uses a unified configuration engine (`config/settings.py`) that bridges JSON settings, environment variables, and runtime natural language updates.

### Key Characteristics:
1. **Bootstrap Precedence:** Settings load before any subsystem reads environment variables (`_apply_settings_to_env()`).
2. **Dot-Notation Access:** Query or modify nested values using simple string paths (e.g., `get("voice.mode")` or `set_value("llm.model", "mistral")`).
3. **Automatic Schema Fallback:** If `aria_settings.json` is missing or corrupted, default settings are automatically populated and written to disk.
4. **Environment Variable Synchronization:** Setting changes dynamically sync to `os.environ` so legacy system components reflect updates immediately.

---

## 2. Configuration Sections Reference

Below is the complete reference of all configurable settings in ARIA:

### 2.1 LLM & AI Engine (`llm`)
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `llm.model` | string | `"llama3"` | Main local LLM model name (used via Ollama). |
| `llm.vision_model` | string | `"llava"` | Multimodal vision model name for post comments and image analysis. |
| `llm.temperature` | float | `0.7` | Temperature parameter for generation creativity. |
| `llm.ollama_url` | string | `"http://localhost:11434"` | Local Ollama REST API endpoint. |

### 2.2 Voice & Audio (`voice`)
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `voice.mode` | string | `"wakeword"` | Voice trigger mode (`"wakeword"`, `"always"`, or `"off"`). |
| `voice.stt_model` | string | `"base"` | Whisper Speech-To-Text model size (`tiny`, `base`, `small`, `medium`). |
| `voice.session_seconds` | float | `25.0` | Active listening session window after wake word trigger. |

### 2.3 Emotion & Vibe Detector (`emotion`)
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `emotion.enabled` | boolean | `true` | Enables/disables webcam background vibe sampling. |
| `emotion.interval` | float | `60.0` | Frame capture and emotion classification interval (seconds). |
| `emotion.camera_index` | integer | `0` | Direct index of the target webcam device. |
| `emotion.rule_mode` | string | `"anytime_sustained"` | Auto-rescheduling rule behavior when stress/fatigue is detected. |

### 2.4 Cognitive Load Monitor (`cognitive_monitor`)
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `cognitive_monitor.enabled` | boolean | `true` | Tracks typing dynamics (key latency, backspace count). |
| `cognitive_monitor.update_interval` | float | `5.0` | Window refresh interval for cognitive metrics. |
| `cognitive_monitor.alert_threshold` | float | `75.0` | Score threshold indicating high typing strain. |

### 2.5 Screen Reader (`screen_reader`)
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `screen_reader.enabled` | boolean | `true` | Periodically reads active screen for context injection. |
| `screen_reader.interval` | float | `60.0` | Screenshot & OCR interval (seconds). |
| `screen_reader.stuck_minutes` | integer | `15` | Minutes on identical screen context before surfacing help hints. |

### 2.6 Productivity Guardian & Focus Mode (`productivity_guardian` & `focus_mode`)
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `productivity_guardian.enabled` | boolean | `true` | Background window monitoring for distraction detection. |
| `productivity_guardian.distraction_threshold` | integer | `3` | Allowed distraction warnings before proactive alert. |
| `focus_mode.work_minutes` | integer | `25` | Pomodoro deep work duration (minutes). |
| `focus_mode.break_minutes` | integer | `5` | Pomodoro rest break duration (minutes). |

### 2.7 Autonomous Planner (`autonomous_planner`)
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `autonomous_planner.enabled` | boolean | `true` | Enables scheduled morning briefing & evening review. |
| `autonomous_planner.morning_briefing_time` | string | `"08:00"` | Daily time to deliver task agenda & weather overview. |
| `autonomous_planner.evening_review_time` | string | `"20:00"` | Daily time to trigger habit review and task audit. |

### 2.8 Security & Risk Control (`security`)
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `security.confirmation_for_dangerous` | boolean | `true` | Enforces explicit confirmation prompt for destructive actions. |
| `security.audit_log` | boolean | `true` | Logs all executed intents and safety verdicts to JSON-L file. |

---

## 3. Natural Language Setting Controls

Users can query or update settings at runtime using spoken voice commands or text input.

### Example Commands:
- **View All Settings:**  
  > *"show settings"*
- **View Specific Section:**  
  > *"show settings emotion"*  
  > *"show settings voice"*
- **Change Settings Dynamically:**  
  > *"turn off emotion detector"*  
  > *"set screen reader interval to 120 seconds"*  
  > *"switch model to mistral"*  
  > *"enable productivity guardian"*  
  > *"change morning briefing time to 07:00"*  

---

## 4. Setting Verification & Audit Log

Every setting modification updates `config/aria_settings.json` and records a detailed entry in the audit log:

```json
{
  "timestamp": "2026-07-26T16:00:00Z",
  "action": "setting_change",
  "key": "llm.model",
  "old_value": "llama3",
  "new_value": "mistral",
  "user": "System Admin"
}
```
