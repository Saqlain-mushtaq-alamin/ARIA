"""
tests/test_safety.py  ─  Safety & Security
===========================================
Tests BLOCKED / DANGEROUS / SAFE / CONFIRM classifications,
confirmation requirements, injection detection, audit log.
"""
import tests.mock_layer  # MUST be first

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_runner import test, run_all
from safety.harm_classifier import (
    assess_risk, SAFE, CONFIRM, DANGEROUS, BLOCKED,
)
from safety.confirmation_engine import requires_confirmation


# ── BLOCKED actions ────────────────────────────────────────────────────────────

@test("delete System32 notepad.exe is BLOCKED", "SAFETY", "blocked")
def t_block_sys32():
    r = assess_risk({
        "intent": "delete_file",
        "parameters": {"path": "C:\\Windows\\System32\\notepad.exe"}
    })
    assert r.level == BLOCKED, f"Expected BLOCKED, got {r.level}: {r.reason}"


@test("disable_antivirus is BLOCKED", "SAFETY", "blocked")
def t_block_av():
    r = assess_risk({"intent": "disable_antivirus", "parameters": {}})
    assert r.level == BLOCKED, f"Expected BLOCKED, got {r.level}: {r.reason}"


@test("Mass wildcard delete is DANGEROUS or BLOCKED", "SAFETY", "escalation")
def t_wildcard_delete():
    r = assess_risk({
        "intent": "delete_file",
        "parameters": {"path": "C:\\Users\\*\\Documents\\*"}
    })
    assert r.level in (DANGEROUS, BLOCKED), \
        f"Expected DANGEROUS or BLOCKED, got {r.level}: {r.reason}"


@test("Command injection in type_text is BLOCKED", "SAFETY", "injection")
def t_injection():
    r = assess_risk({
        "intent": "type_text",
        "parameters": {"text": "hello; rm -rf / && echo done"}
    })
    assert r.level == BLOCKED, f"Expected BLOCKED, got {r.level}: {r.reason}"


@test("SSH private key access is BLOCKED", "SAFETY", "credentials")
def t_ssh_key():
    r = assess_risk({
        "intent": "read_file",
        "parameters": {"path": "C:\\Users\\me\\.ssh\\id_rsa"}
    })
    assert r.level == BLOCKED, f"Expected BLOCKED, got {r.level}: {r.reason}"


@test("IP-based URL is flagged CONFIRM or BLOCKED", "SAFETY", "url")
def t_ip_url():
    from safety.harm_classifier import _check_url_safety
    level, reason = _check_url_safety("http://192.168.1.1/malicious")
    assert level in (CONFIRM, BLOCKED), f"Expected CONFIRM or BLOCKED, got {level!r}"


# ── CONFIRM actions (require user confirmation) ────────────────────────────────

@test("shutdown requires confirmation", "SAFETY", "confirm")
def t_confirm_shutdown():
    assert requires_confirmation({"intent": "shutdown", "parameters": {}}) is True


@test("delete file requires confirmation", "SAFETY", "confirm")
def t_confirm_delete():
    assert requires_confirmation({
        "intent": "delete_file",
        "parameters": {"path": "test.txt"}
    }) is True


@test("send message requires confirmation", "SAFETY", "confirm")
def t_confirm_message():
    assert requires_confirmation({
        "intent": "send_message",
        "parameters": {"contact": "John", "message": "hi"}
    }) is True


# ── SAFE actions ──────────────────────────────────────────────────────────────

@test("open_app notepad is SAFE", "SAFETY", "safe")
def t_safe_open_app():
    r = assess_risk({"intent": "open_app", "parameters": {"app_name": "notepad"}})
    assert r.level == SAFE, f"Expected SAFE, got {r.level}: {r.reason}"


@test("search_web is SAFE", "SAFETY", "safe")
def t_safe_search():
    r = assess_risk({"intent": "search_web", "parameters": {"query": "python tutorials"}})
    assert r.level == SAFE, f"Expected SAFE, got {r.level}: {r.reason}"


@test("set_volume is SAFE", "SAFETY", "safe")
def t_safe_volume():
    r = assess_risk({"intent": "set_volume", "parameters": {"level": 50}})
    assert r.level == SAFE, f"Expected SAFE, got {r.level}: {r.reason}"


# ── Audit log ─────────────────────────────────────────────────────────────────

@test("Audit log records every action", "SAFETY", "audit")
def t_audit_log():
    from safety.audit_log import log_success, query_log
    log_success("test_intent", "Test passed", risk_level="safe")
    entries = query_log(intent_filter="test_intent", limit=5)
    assert len(entries) > 0, "No entries found in audit log"


@test("Audit log integrity passes", "SAFETY", "audit")
def t_audit_integrity():
    from safety.audit_log import verify_integrity
    ok, msg = verify_integrity()
    assert ok, f"Integrity check failed: {msg}"


if __name__ == "__main__":
    run_all("SAFETY")
