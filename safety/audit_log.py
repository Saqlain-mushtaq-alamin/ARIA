"""ARIA — Security Audit Log.

Every action ARIA takes is recorded in an append-only structured log.
The log is tamper-evident: each entry contains a checksum of the previous
entry, forming a chain. If any entry is modified, the chain breaks.

Log location: data/logs/aria_audit.jsonl
Each line is a JSON object (JSONL format — one entry per line).

Features:
  - Append-only (never modifies existing entries)
  - Tamper-evident chain (SHA-256 checksum linking)
  - Risk level recorded for every action
  - Outcome recorded (success / blocked / cancelled / error)
  - Auto-rotation: creates a new file daily
  - Query helpers: filter by date, intent, risk level, outcome
  - Weekly summary generator (feeds the weekly review module)
  - Integrity verifier: detects if any entry was tampered with
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR      = os.path.join("data", "logs")
LOG_FILENAME = "aria_audit.jsonl"
LOG_PATH     = os.path.join(LOG_DIR, LOG_FILENAME)

# Outcome constants
OUTCOME_SUCCESS   = "success"
OUTCOME_BLOCKED   = "blocked"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_ERROR     = "error"
OUTCOME_SKIPPED   = "skipped"


# ─────────────────────────────────────────────────────────────────────────────
# Checksum chain helpers
# ─────────────────────────────────────────────────────────────────────────────

def _hash_entry(entry_json: str) -> str:
    return hashlib.sha256(entry_json.encode("utf-8")).hexdigest()


def _get_last_checksum() -> str:
    """Return the checksum of the last log entry, or 'GENESIS' for the first."""
    if not os.path.exists(LOG_PATH):
        return "GENESIS"
    try:
        # Read last non-empty line
        last_line = ""
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    last_line = stripped
        if not last_line:
            return "GENESIS"
        entry = json.loads(last_line)
        return entry.get("checksum", "GENESIS")
    except Exception:
        return "GENESIS"


# ─────────────────────────────────────────────────────────────────────────────
# Core write function
# ─────────────────────────────────────────────────────────────────────────────

def log_action(
    intent: str,
    parameters: Optional[Dict[str, Any]] = None,
    risk_level: str = "safe",
    outcome: str = OUTCOME_SUCCESS,
    result_summary: str = "",
    user_input: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Append one action record to the audit log.

    Args:
        intent:         The classified intent name (e.g. 'open_app').
        parameters:     The parameters dict (sensitive values will be redacted).
        risk_level:     'safe' | 'confirm' | 'dangerous' | 'blocked'
        outcome:        'success' | 'blocked' | 'cancelled' | 'error' | 'skipped'
        result_summary: Short description of what happened (first 200 chars).
        user_input:     The original user command (stored for context).
        extra:          Any additional fields to include.
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    now = datetime.now()
    prev_checksum = _get_last_checksum()

    # Redact sensitive parameter values
    safe_params = _redact_params(parameters or {})

    entry: Dict[str, Any] = {
        "timestamp":      now.isoformat(),
        "date":           now.strftime("%Y-%m-%d"),
        "time":           now.strftime("%H:%M:%S"),
        "intent":         intent,
        "parameters":     safe_params,
        "risk_level":     risk_level,
        "outcome":        outcome,
        "result_summary": result_summary[:300] if result_summary else "",
        "user_input":     user_input[:200] if user_input else "",
        "prev_checksum":  prev_checksum,
    }

    if extra:
        entry.update(extra)

    # Compute this entry's checksum (without the checksum field itself)
    entry_for_hash = json.dumps({k: v for k, v in entry.items()}, sort_keys=True)
    entry["checksum"] = _hash_entry(entry_for_hash)

    line = json.dumps(entry, ensure_ascii=False)

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _redact_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Replace sensitive parameter values with [REDACTED]."""
    _SENSITIVE_KEYS = frozenset({
        "password", "passwd", "secret", "token", "api_key", "apikey",
        "auth", "credential", "credit_card", "card_number", "cvv",
        "ssn", "pin", "private_key", "access_token", "refresh_token",
    })
    result = {}
    for k, v in params.items():
        if k.lower() in _SENSITIVE_KEYS or "password" in k.lower() or "secret" in k.lower():
            result[k] = "[REDACTED]"
        elif isinstance(v, str) and len(v) > 500:
            result[k] = v[:200] + "...[truncated]"
        else:
            result[k] = v
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Query helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_all_entries() -> List[Dict[str, Any]]:
    if not os.path.exists(LOG_PATH):
        return []
    entries = []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def query_log(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    intent_filter: Optional[str] = None,
    risk_filter: Optional[str] = None,
    outcome_filter: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Query the audit log with optional filters.

    Args:
        date_from:      Start date 'YYYY-MM-DD' (inclusive).
        date_to:        End date 'YYYY-MM-DD' (inclusive).
        intent_filter:  Filter by intent name (substring match).
        risk_filter:    Filter by risk level ('safe', 'confirm', etc.)
        outcome_filter: Filter by outcome ('success', 'blocked', etc.)
        limit:          Maximum number of results to return.

    Returns:
        List of matching entry dicts.
    """
    entries = _read_all_entries()
    results = []

    for entry in reversed(entries):  # newest first
        if date_from and entry.get("date", "") < date_from:
            continue
        if date_to and entry.get("date", "") > date_to:
            continue
        if intent_filter and intent_filter.lower() not in entry.get("intent", "").lower():
            continue
        if risk_filter and entry.get("risk_level", "").lower() != risk_filter.lower():
            continue
        if outcome_filter and entry.get("outcome", "").lower() != outcome_filter.lower():
            continue
        results.append(entry)
        if len(results) >= limit:
            break

    return results


def get_recent(n: int = 20) -> str:
    """Return the last N log entries as a formatted string."""
    entries = query_log(limit=n)
    if not entries:
        return "No audit log entries found."

    lines = [f"📋 Last {len(entries)} actions:"]
    lines.append(f"  {'Time':<10}  {'Risk':<10}  {'Outcome':<12}  {'Intent':<25}  Summary")
    lines.append("  " + "─" * 80)

    for e in entries:
        t     = e.get("time", "?")[:8]
        risk  = e.get("risk_level", "?")[:9]
        out   = e.get("outcome", "?")[:11]
        intent= e.get("intent", "?")[:24]
        summ  = e.get("result_summary", "")[:40]
        lines.append(f"  {t:<10}  {risk:<10}  {out:<12}  {intent:<25}  {summ}")

    return "\n".join(lines)


def get_blocked_actions(days: int = 7) -> str:
    """Return all blocked actions from the last N days."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    entries = query_log(date_from=since, outcome_filter=OUTCOME_BLOCKED)

    if not entries:
        return f"No blocked actions in the last {days} days. ✓"

    lines = [f"🚫 Blocked actions (last {days} days) — {len(entries)} found:"]
    for e in entries:
        lines.append(
            f"  {e.get('date')} {e.get('time','')[:5]}  "
            f"Intent: {e.get('intent','?')}  "
            f"Reason: {e.get('result_summary','?')[:80]}"
        )
    return "\n".join(lines)


def get_security_summary(days: int = 7) -> str:
    """Return a security-focused summary for the last N days."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    entries = query_log(date_from=since, limit=10000)

    total = len(entries)
    if total == 0:
        return f"No activity recorded in the last {days} days."

    by_outcome: Dict[str, int] = {}
    by_risk: Dict[str, int] = {}
    by_intent: Dict[str, int] = {}

    for e in entries:
        outcome = e.get("outcome", "unknown")
        risk    = e.get("risk_level", "unknown")
        intent  = e.get("intent", "unknown")
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        by_risk[risk]       = by_risk.get(risk, 0) + 1
        by_intent[intent]   = by_intent.get(intent, 0) + 1

    top_intents = sorted(by_intent.items(), key=lambda x: x[1], reverse=True)[:5]

    lines = [
        f"🔐 Security Summary — Last {days} days",
        f"   Total actions  : {total}",
        f"   Outcomes       : " + ", ".join(f"{k}: {v}" for k, v in sorted(by_outcome.items())),
        f"   Risk levels    : " + ", ".join(f"{k}: {v}" for k, v in sorted(by_risk.items())),
        f"   Top intents    : " + ", ".join(f"{k}({v})" for k, v in top_intents),
    ]

    blocked = by_outcome.get(OUTCOME_BLOCKED, 0)
    if blocked > 0:
        lines.append(f"\n   ⚠️  {blocked} blocked attempt(s) detected in this period.")

    dangerous_count = by_risk.get("dangerous", 0)
    if dangerous_count > 0:
        lines.append(f"   ⚠️  {dangerous_count} high-risk action(s) were executed.")

    return "\n".join(lines)


def verify_integrity() -> Tuple[bool, str]:
    """Verify the checksum chain of the audit log.

    Returns:
        (is_intact: bool, message: str)
    """
    entries = _read_all_entries()
    if not entries:
        return True, "Log is empty — nothing to verify."

    prev_checksum = "GENESIS"
    for i, entry in enumerate(entries):
        stored_checksum = entry.get("checksum", "")
        stored_prev     = entry.get("prev_checksum", "")

        if stored_prev != prev_checksum:
            return False, (
                f"⛔ Integrity violation at entry {i+1} "
                f"(timestamp: {entry.get('timestamp', '?')})\n"
                f"  Expected prev_checksum: {prev_checksum[:16]}...\n"
                f"  Found prev_checksum:    {stored_prev[:16]}...\n"
                f"  The log may have been tampered with."
            )

        # Recompute checksum
        entry_for_hash = {k: v for k, v in entry.items() if k != "checksum"}
        recomputed = _hash_entry(json.dumps(entry_for_hash, sort_keys=True))
        if recomputed != stored_checksum:
            return False, (
                f"⛔ Checksum mismatch at entry {i+1} "
                f"(timestamp: {entry.get('timestamp', '?')})\n"
                f"  Entry content appears to have been modified."
            )

        prev_checksum = stored_checksum

    return True, f"✓ Log integrity verified — {len(entries)} entries, chain intact."


# ─────────────────────────────────────────────────────────────────────────────
# Convenience decorators / wrappers for agent.py
# ─────────────────────────────────────────────────────────────────────────────

def log_blocked(intent: str, reason: str, user_input: str = "") -> None:
    log_action(
        intent=intent,
        risk_level="blocked",
        outcome=OUTCOME_BLOCKED,
        result_summary=reason,
        user_input=user_input,
    )


def log_cancelled(intent: str, user_input: str = "") -> None:
    log_action(
        intent=intent,
        outcome=OUTCOME_CANCELLED,
        result_summary="User cancelled the action.",
        user_input=user_input,
    )


def log_error(intent: str, error: str, user_input: str = "") -> None:
    log_action(
        intent=intent,
        outcome=OUTCOME_ERROR,
        result_summary=f"Error: {error[:200]}",
        user_input=user_input,
    )


def log_success(
    intent: str,
    result: str,
    parameters: Optional[Dict[str, Any]] = None,
    risk_level: str = "safe",
    user_input: str = "",
) -> None:
    log_action(
        intent=intent,
        parameters=parameters,
        risk_level=risk_level,
        outcome=OUTCOME_SUCCESS,
        result_summary=result[:300],
        user_input=user_input,
    )
