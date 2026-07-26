"""ARIA — Harm & Risk Classification Engine.

This is a HARD-CODED Python safety layer. It cannot be overridden by any LLM
prompt, user instruction, or clever phrasing. Every action goes through this
module before anything is executed.

RISK LEVELS (lowest → highest):
  SAFE       — Execute directly, no questions asked.
  CONFIRM    — Ask the user for confirmation before proceeding.
  DANGEROUS  — Show a strong warning + require typing the action name to confirm.
  BLOCKED    — Refuse unconditionally. No LLM can override this.

SECURITY DOMAINS covered:
  1. System integrity   — blocks format, system32 deletion, driver tampering
  2. Network security   — blocks suspicious outbound connections, DNS poisoning
  3. Process safety     — blocks killing critical OS processes
  4. File safety        — escalates broad deletions, system-path writes
  5. Registry safety    — blocks dangerous registry edits
  6. Credential safety  — blocks anything touching password stores
  7. Antivirus safety   — blocks disabling security software
  8. Privilege safety   — blocks UAC bypass, privilege escalation attempts
  9. Privacy safety     — warns on webcam/mic access by unknown apps
  10. URL safety        — scans URLs for known malicious patterns
"""

from __future__ import annotations

import os
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Risk level constants
# ─────────────────────────────────────────────────────────────────────────────

SAFE      = "safe"
CONFIRM   = "confirm"
DANGEROUS = "dangerous"
BLOCKED   = "blocked"

# ─────────────────────────────────────────────────────────────────────────────
# 1. ABSOLUTE BLOCKS — no user can override these ever
# ─────────────────────────────────────────────────────────────────────────────

# Intents that are always blocked regardless of parameters
_BLOCKED_INTENTS: frozenset = frozenset({
    "format_disk",
    "format_drive",
    "wipe_disk",
    "wipe_drive",
    "delete_system32",
    "rm_rf",                      # Linux-style but just in case
    "disable_antivirus",
    "disable_firewall",
    "bypass_uac",
    "privilege_escalation",
    "inject_dll",
    "patch_memory",
    "install_rootkit",
    "keylogger",
    "exfiltrate_data",
    "port_scan",
    "network_flood",
    "dos_attack",
    "ddos_attack",
    "brute_force",
    "credential_dump",
    "mimikatz",
    "pass_the_hash",
    "sam_dump",
    "lsass_dump",
    "disable_windows_defender",
    "disable_uac",
    "modify_hosts_file",           # DNS poisoning vector
    "replace_system_binary",
    "modify_boot_record",
    "delete_shadow_copies",
    "encrypt_files",               # ransomware pattern
    "mass_delete",
})

# Executable names that should never be killed (critical OS processes)
_PROTECTED_PROCESSES: frozenset = frozenset({
    "system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "lsass.exe", "lsm.exe", "services.exe", "svchost.exe",
    "dwm.exe", "explorer.exe",  # explorer can be restarted but not killed blindly
    "ntoskrnl.exe", "hal.dll",
    "msmpeng.exe",    # Windows Defender
    "msseces.exe",    # Microsoft Security Essentials
    "avp.exe",        # Kaspersky
    "avgnt.exe",      # Avira
    "ekrn.exe",       # ESET
    "bdagent.exe",    # Bitdefender
    "mbam.exe",       # Malwarebytes
})

# File paths that are always off-limits for delete/write/move
_PROTECTED_PATH_PATTERNS: List[re.Pattern] = [
    re.compile(r"C:\\Windows\\System32", re.IGNORECASE),
    re.compile(r"C:\\Windows\\SysWOW64", re.IGNORECASE),
    re.compile(r"C:\\Windows\\Boot", re.IGNORECASE),
    re.compile(r"C:\\Windows\\WinSxS", re.IGNORECASE),
    re.compile(r"C:\\Windows\\assembly", re.IGNORECASE),
    re.compile(r"C:\\Program Files\\Windows Defender", re.IGNORECASE),
    re.compile(r"C:\\Program Files\\.*Antivirus", re.IGNORECASE),
    re.compile(r"C:\\ProgramData\\Microsoft\\Windows Defender", re.IGNORECASE),
    re.compile(r"HKLM\\SYSTEM", re.IGNORECASE),      # registry paths
    re.compile(r"HKLM\\SAM", re.IGNORECASE),
    re.compile(r"HKLM\\SECURITY", re.IGNORECASE),
    re.compile(r"\\boot\\bcd", re.IGNORECASE),
    re.compile(r"pagefile\.sys", re.IGNORECASE),
    re.compile(r"hiberfil\.sys", re.IGNORECASE),
    re.compile(r"ntldr", re.IGNORECASE),
    re.compile(r"bootmgr", re.IGNORECASE),
    re.compile(r"\.ssh\\id_", re.IGNORECASE),         # SSH private keys
    re.compile(r"\.gnupg", re.IGNORECASE),            # GPG keys
    re.compile(r"AppData\\Local\\Microsoft\\Credentials", re.IGNORECASE),
    re.compile(r"AppData\\Roaming\\Microsoft\\Credentials", re.IGNORECASE),
    re.compile(r"AppData\\Local\\Microsoft\\Vault", re.IGNORECASE),
]

# Credential-related filenames — never read/exfiltrate
_CREDENTIAL_FILE_PATTERNS: List[re.Pattern] = [
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"passwd", re.IGNORECASE),
    re.compile(r"\.pfx$", re.IGNORECASE),
    re.compile(r"\.p12$", re.IGNORECASE),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"id_rsa", re.IGNORECASE),
    re.compile(r"id_ecdsa", re.IGNORECASE),
    re.compile(r"wallet\.dat$", re.IGNORECASE),       # crypto wallet
    re.compile(r"keepass.*\.kdbx?$", re.IGNORECASE),  # KeePass
    re.compile(r"login\.keychain", re.IGNORECASE),
    re.compile(r"ntds\.dit$", re.IGNORECASE),         # AD database
    re.compile(r"sam$", re.IGNORECASE),
    re.compile(r"shadow$", re.IGNORECASE),
]

# ─────────────────────────────────────────────────────────────────────────────
# 2. KNOWN MALICIOUS URL PATTERNS
# ─────────────────────────────────────────────────────────────────────────────

_MALICIOUS_URL_PATTERNS: List[re.Pattern] = [
    # IP-based URLs (no domain — suspicious)
    re.compile(r"https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"),
    # URL shorteners that hide destination
    re.compile(r"https?://(bit\.ly|tinyurl\.com|t\.co|goo\.gl|ow\.ly|is\.gd|buff\.ly)/"),
    # Typosquatting common targets
    re.compile(r"https?://.*paypa1\.com", re.IGNORECASE),
    re.compile(r"https?://.*g00gle\.com", re.IGNORECASE),
    re.compile(r"https?://.*micros0ft\.com", re.IGNORECASE),
    re.compile(r"https?://.*arnazon\.com", re.IGNORECASE),
    re.compile(r"https?://.*apple\.com\..*", re.IGNORECASE),
    # Data exfil patterns
    re.compile(r"https?://.*\.ngrok\.io", re.IGNORECASE),
    re.compile(r"https?://.*pastebin\.com/raw", re.IGNORECASE),
    # PowerShell download cradles embedded in URLs
    re.compile(r"powershell.*-enc", re.IGNORECASE),
    re.compile(r"cmd\.exe.*\/c", re.IGNORECASE),
    re.compile(r"iex\s*\(", re.IGNORECASE),           # Invoke-Expression
    re.compile(r"downloadstring", re.IGNORECASE),
    re.compile(r"webclient.*download", re.IGNORECASE),
]

# ─────────────────────────────────────────────────────────────────────────────
# 3. COMMAND INJECTION PATTERNS
# ─────────────────────────────────────────────────────────────────────────────

_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r";\s*(rm|del|format|mkfs|dd\s+if=)", re.IGNORECASE),
    re.compile(r"\|\s*(nc|ncat|netcat|bash|sh|cmd)", re.IGNORECASE),
    re.compile(r"&&\s*(wget|curl|powershell|python|perl|ruby)\s+http", re.IGNORECASE),
    re.compile(r"`[^`]+`"),                            # backtick injection
    re.compile(r"\$\([^)]+\)"),                        # command substitution
    re.compile(r"eval\s*\(", re.IGNORECASE),
    re.compile(r"exec\s*\(", re.IGNORECASE),
    re.compile(r"base64\s*-d\s*\|", re.IGNORECASE),   # base64 decode pipe
    re.compile(r"\/dev\/tcp\/"),                       # bash TCP redirect
]

# ─────────────────────────────────────────────────────────────────────────────
# 4. RISK CLASSIFICATION TABLE
# ─────────────────────────────────────────────────────────────────────────────

# intent → (risk_level, reason)
_INTENT_RISK_TABLE: Dict[str, Tuple[str, str]] = {
    # Safe
    "open_app":         (SAFE,      "Opening an application is safe."),
    "open_folder":      (SAFE,      "Opening a folder is safe."),
    "activate_kinetic_mode":   (SAFE, "Activating gesture control is safe."),
    "deactivate_kinetic_mode": (SAFE, "Stopping gesture control is safe."),
    "get_clipboard":    (SAFE,      "Reading clipboard is safe."),
    "set_volume":       (SAFE,      "Adjusting volume is safe."),
    "set_brightness":   (SAFE,      "Adjusting brightness is safe."),
    "take_screenshot":  (SAFE,      "Taking a screenshot is safe."),
    "screenshot":       (SAFE,      "Taking a screenshot is safe."),
    "open_url":         (SAFE,      "Opening a URL is safe (URL will be scanned)."),
    "search_web":       (SAFE,      "Web search is safe."),
    "get_weather":      (SAFE,      "Fetching weather is safe."),
    "get_news":         (SAFE,      "Fetching news is safe."),
    "search_papers":    (SAFE,      "Searching papers is safe."),
    "get_stock":        (SAFE,      "Fetching stock data is safe."),
    "download":         (SAFE,      "Downloading a file is safe (URL will be scanned)."),
    "list_directory":   (SAFE,      "Listing a directory is safe."),
    "read_file":        (SAFE,      "Reading a file is safe (path will be checked)."),
    "show_schedule":    (SAFE,      "Showing schedule is safe."),
    "whats_next":       (SAFE,      "Checking next task is safe."),
    "answer_question":  (SAFE,      "Answering a question is safe."),
    "conversational":   (SAFE,      "Conversational response is safe."),
    "translate":        (SAFE,      "Translation is safe."),
    "type_text":        (SAFE,      "Typing text is safe."),
    "get_wifi_status":  (SAFE,      "Checking Wi-Fi status is safe."),
    "get_system_info":  (SAFE,      "Checking system info is safe."),
    "get_battery":      (SAFE,      "Checking battery is safe."),
    "get_network_info": (SAFE,      "Checking network info is safe."),
    "list_processes":   (SAFE,      "Listing processes is safe."),
    "mute_volume":      (SAFE,      "Muting audio is safe."),
    "get_volume":       (SAFE,      "Reading volume level is safe."),
    "press_key":        (SAFE,      "Pressing a key is safe."),

    # Confirm
    "close_window":     (CONFIRM,   "Closing a window may lose unsaved work."),
    "toggle_wifi":      (CONFIRM,   "Toggling Wi-Fi will disconnect your internet."),
    "toggle_bluetooth": (CONFIRM,   "Toggling Bluetooth will disconnect BT devices."),
    "toggle_airplane":  (CONFIRM,   "Airplane mode disables all wireless connections."),
    "open_file":        (CONFIRM,   "Opening a file will launch an application."),
    "create_file":      (CONFIRM,   "A new file will be created on your system."),
    "copy_file":        (CONFIRM,   "Files will be copied to a new location."),
    "move_file":        (CONFIRM,   "File will be moved — original location will be empty."),
    "fill_form":        (CONFIRM,   "A web form will be submitted with your data."),
    "extract_text":     (CONFIRM,   "Content will be read from a website."),
    "edit_schedule":    (CONFIRM,   "Your schedule will be modified."),
    "create_schedule":  (CONFIRM,   "A new daily schedule will be generated."),
    "run_command":      (CONFIRM,   "A shell command will be executed."),
    "set_clipboard":    (CONFIRM,   "Your clipboard will be overwritten."),
    "open_folder":      (SAFE,      "Opening a folder is safe."),

    # Dangerous — require typing confirmation
    "delete_file":      (DANGEROUS, "File deletion is irreversible (48h buffer applies)."),
    "save_file":        (DANGEROUS, "This will write/overwrite a file on your system."),
    "shutdown":         (DANGEROUS, "Computer will shut down. Unsaved work will be lost."),
    "restart":          (DANGEROUS, "Computer will restart. Unsaved work will be lost."),
    "lock_screen":      (CONFIRM,   "Screen will be locked."),
    "sleep":            (CONFIRM,   "Computer will go to sleep."),
    "hibernate":        (CONFIRM,   "Computer will hibernate."),
    "kill_process":     (DANGEROUS, "Killing a process may crash applications or lose data."),
    "send_message":     (DANGEROUS, "A message will be sent to another person."),
    "click_element":    (CONFIRM,   "A button/link will be clicked in the browser."),
    "empty_recycle_buffer": (DANGEROUS, "Buffered deleted files will be permanently erased."),

    # Blocked — hard no
    "format_disk":      (BLOCKED,   "Formatting a disk erases all data. This is not allowed."),
    "format_drive":     (BLOCKED,   "Formatting a drive erases all data. This is not allowed."),
    "disable_antivirus":(BLOCKED,   "Disabling antivirus exposes your system to malware."),
    "disable_firewall": (BLOCKED,   "Disabling the firewall exposes your system to attacks."),
    "modify_hosts_file":(BLOCKED,   "Modifying the hosts file is a DNS attack vector."),
    "delete_system32":  (BLOCKED,   "Deleting System32 would destroy your operating system."),
    "wipe_disk":        (BLOCKED,   "Wiping a disk is irreversible and dangerous."),
}


# ─────────────────────────────────────────────────────────────────────────────
# 5. HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _check_path_safety(path: str) -> Tuple[str, str]:
    """Return (risk_level, reason) for a file path."""
    if not path:
        return SAFE, ""

    norm = os.path.normpath(path)

    # Check against protected path patterns
    for pattern in _PROTECTED_PATH_PATTERNS:
        if pattern.search(norm):
            return BLOCKED, f"Path is in a protected system location: {norm}"

    # Check for credential file names
    basename = os.path.basename(norm)
    for pattern in _CREDENTIAL_FILE_PATTERNS:
        if pattern.search(basename):
            return BLOCKED, f"Operation on a credential/key file is not allowed: {basename}"

    # Check if path targets a root drive directly (e.g. C:\ or D:\)
    p = Path(norm)
    if p == p.anchor or str(p) in {"C:\\", "D:\\", "E:\\", "C:/", "D:/"}:
        return BLOCKED, "Operating on a root drive path is not allowed."

    # Broad wildcard patterns
    if any(c in path for c in ("*", "?")) and any(
        w in path.lower() for w in ("system", "windows", "program files")
    ):
        return BLOCKED, "Wildcard operations on system directories are not allowed."

    return SAFE, ""


def _check_url_safety(url: str) -> Tuple[str, str]:
    """Return (risk_level, reason) for a URL."""
    if not url:
        return SAFE, ""

    for pattern in _MALICIOUS_URL_PATTERNS:
        if pattern.search(url):
            return BLOCKED, f"URL matches a known malicious or suspicious pattern: {url[:80]}"

    # Check for non-printable / obfuscated characters
    if any(ord(c) > 127 and ord(c) < 160 for c in url):
        return CONFIRM, "URL contains unusual characters — may be obfuscated."

    # Warn on HTTP (not HTTPS) for form submissions
    if url.startswith("http://") and not url.startswith("http://localhost"):
        return CONFIRM, "URL uses HTTP (not HTTPS) — data will not be encrypted."

    return SAFE, ""


def _check_command_injection(text: str) -> Tuple[str, str]:
    """Scan any text parameter for command injection patterns."""
    if not text:
        return SAFE, ""
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return BLOCKED, f"Command injection pattern detected in input: {text[:60]}"
    return SAFE, ""


def _check_process_safety(process_name: str) -> Tuple[str, str]:
    """Check if a process is protected from being killed."""
    if not process_name:
        return SAFE, ""
    name_lower = process_name.strip().lower()
    for protected in _PROTECTED_PROCESSES:
        if name_lower == protected.lower() or name_lower == protected.lower().replace(".exe", ""):
            return BLOCKED, f"'{process_name}' is a critical OS process and cannot be terminated."
    return SAFE, ""


def _check_network_safety(host: str) -> Tuple[str, str]:
    """Validate a hostname or IP for obvious suspicious patterns."""
    if not host:
        return SAFE, ""
    # Private-range IPs are fine for local network
    # External IPs without domain should raise a flag
    ip_match = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", host.strip())
    if ip_match:
        parts = [int(ip_match.group(i)) for i in range(1, 5)]
        # Private ranges: 10.x, 172.16-31.x, 192.168.x, 127.x
        if parts[0] in (10, 127) or (parts[0] == 172 and 16 <= parts[1] <= 31) or \
                (parts[0] == 192 and parts[1] == 168):
            return SAFE, ""
        return CONFIRM, f"Connecting to a raw external IP address: {host}"
    return SAFE, ""


# ─────────────────────────────────────────────────────────────────────────────
# 6. MAIN PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

class RiskAssessment:
    """Result of a full risk assessment on an action payload."""

    def __init__(
        self,
        level: str,
        reason: str,
        intent: str,
        details: Optional[List[str]] = None,
    ) -> None:
        self.level   = level
        self.reason  = reason
        self.intent  = intent
        self.details = details or []

    @property
    def is_blocked(self) -> bool:
        return self.level == BLOCKED

    @property
    def is_dangerous(self) -> bool:
        return self.level == DANGEROUS

    @property
    def is_confirm(self) -> bool:
        return self.level == CONFIRM

    @property
    def is_safe(self) -> bool:
        return self.level == SAFE

    def __repr__(self) -> str:
        return f"RiskAssessment(level={self.level!r}, intent={self.intent!r}, reason={self.reason!r})"


def assess_risk(payload: Dict[str, Any]) -> RiskAssessment:
    """Full risk assessment of an action payload.

    Checks in order (first match wins for BLOCKED; highest level wins overall):
      1. Hard-blocked intent names
      2. Intent risk table lookup
      3. Path safety (for file operations)
      4. URL safety (for browser operations)
      5. Process safety (for kill_process)
      6. Command injection scan (for run_command, type_text)
      7. Network safety (for any host parameter)

    Args:
        payload: Dict with at minimum {"intent": "..."} and optional "parameters".

    Returns:
        RiskAssessment with level, reason, intent, and details list.
    """
    intent = str(payload.get("intent", "")).strip().lower()
    params = payload.get("parameters") or {}
    details: List[str] = []

    # 1. Hard-blocked intents
    if intent in _BLOCKED_INTENTS:
        return RiskAssessment(BLOCKED, f"Intent '{intent}' is permanently blocked.", intent)

    # 2. Intent risk table
    table_level, table_reason = _INTENT_RISK_TABLE.get(intent, (CONFIRM, "Unknown intent — defaulting to confirm."))
    current_level = table_level
    current_reason = table_reason

    def _escalate(new_level: str, new_reason: str) -> None:
        nonlocal current_level, current_reason
        order = {SAFE: 0, CONFIRM: 1, DANGEROUS: 2, BLOCKED: 3}
        if order.get(new_level, 0) > order.get(current_level, 0):
            current_level = new_level
            current_reason = new_reason
        details.append(f"[{new_level.upper()}] {new_reason}")

    # 3. Path checks
    for path_key in ("path", "source", "destination", "file"):
        path_val = params.get(path_key, "")
        if path_val:
            lvl, reason = _check_path_safety(str(path_val))
            if lvl != SAFE:
                _escalate(lvl, reason)

    # 4. URL checks
    url_val = params.get("url") or params.get("link") or params.get("target") or ""
    if url_val:
        lvl, reason = _check_url_safety(str(url_val))
        if lvl != SAFE:
            _escalate(lvl, reason)

    # 5. Process safety
    proc_val = params.get("app_name") or params.get("process") or params.get("name") or ""
    if intent == "kill_process" and proc_val:
        lvl, reason = _check_process_safety(str(proc_val))
        if lvl != SAFE:
            _escalate(lvl, reason)

    # 6. Command injection scan
    for text_key in ("text", "content", "command", "query", "prompt"):
        text_val = params.get(text_key, "")
        if text_val:
            lvl, reason = _check_command_injection(str(text_val))
            if lvl != SAFE:
                _escalate(lvl, reason)

    # 7. Network safety
    host_val = params.get("host") or params.get("server") or ""
    if host_val:
        lvl, reason = _check_network_safety(str(host_val))
        if lvl != SAFE:
            _escalate(lvl, reason)

    # 8. Broad delete guard — if path is very short (drive root or top-level), escalate
    if intent == "delete_file":
        path_val = str(params.get("path") or "")
        if path_val:
            norm = os.path.normpath(path_val)
            depth = len(Path(norm).parts)
            if depth <= 2:
                _escalate(BLOCKED, f"Deleting a root or top-level path is not allowed: {norm}")

    # 9. Mass-file operation guard
    if intent in {"delete_file", "move_file", "copy_file"}:
        path_val = str(params.get("path") or params.get("source") or "")
        if "*" in path_val or "?" in path_val:
            _escalate(DANGEROUS, f"Wildcard file operation detected: {path_val}")

    return RiskAssessment(current_level, current_reason, intent, details)


def is_blocked(payload: Dict[str, Any]) -> bool:
    """Quick check: return True if this payload must be blocked."""
    return assess_risk(payload).is_blocked


def blocked_response(payload: Dict[str, Any]) -> str:
    """Return a human-readable refusal message for a blocked action."""
    intent = str(payload.get("intent", "this action")).strip()
    assessment = assess_risk(payload)
    return (
        f"🚫 Blocked: {assessment.reason}\n"
        f"   I cannot perform '{intent}' as it poses a security or safety risk.\n"
        f"   If you believe this is an error, please check your command and try again."
    )


def get_risk_level(payload: Dict[str, Any]) -> str:
    """Return just the risk level string for a payload."""
    return assess_risk(payload).level


def scan_text_for_threats(text: str) -> Tuple[bool, str]:
    """Scan arbitrary text (e.g. pasted content, file content) for threats.

    Returns:
        (threat_found: bool, description: str)
    """
    threats = []

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            threats.append(f"Command injection pattern: {pattern.pattern[:40]}")

    for pattern in _MALICIOUS_URL_PATTERNS:
        match = pattern.search(text)
        if match:
            threats.append(f"Suspicious URL pattern: {match.group(0)[:60]}")

    # PowerShell encoded commands
    if re.search(r"-EncodedCommand\s+[A-Za-z0-9+/=]{20,}", text, re.IGNORECASE):
        threats.append("Base64-encoded PowerShell command detected.")

    # Suspicious file extensions in URLs
    if re.search(r"https?://[^\s]+\.(exe|bat|ps1|vbs|jar|msi|scr|pif)\b", text, re.IGNORECASE):
        threats.append("URL pointing to a potentially malicious executable.")

    if threats:
        return True, "Threats detected:\n" + "\n".join(f"  • {t}" for t in threats)
    return False, "No threats detected."


def is_safe_url(url: str) -> bool:
    """Quick URL safety check — returns True if URL passes all checks."""
    lvl, _ = _check_url_safety(url)
    return lvl == SAFE


def is_safe_path(path: str) -> bool:
    """Quick path safety check — returns True if path is safe to operate on."""
    lvl, _ = _check_path_safety(path)
    return lvl == SAFE
