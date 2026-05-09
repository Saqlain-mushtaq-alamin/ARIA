"""ARIA — Confirmation Engine.

Presents the right level of confirmation friction based on the risk level
returned by harm_classifier.assess_risk():

  SAFE      → No confirmation needed. Execute immediately.
  CONFIRM   → Show what will happen + 5-second countdown. Ctrl+C or 'n' cancels.
  DANGEROUS → Show strong warning + require user to TYPE the action name before executing.
  BLOCKED   → Never reaches this module (blocked upstream in harm_classifier).

The engine works in two modes:
  CLI mode    — interactive stdin prompts (used in terminal / development)
  Callback mode — caller provides ask_fn(question) → str for UI/voice integration
"""

from __future__ import annotations

import sys
import time
from typing import Any, Callable, Dict, Optional

from safety.harm_classifier import (
    CONFIRM,
    DANGEROUS,
    SAFE,
    BLOCKED,
    RiskAssessment,
    assess_risk,
)

# ─────────────────────────────────────────────────────────────────────────────
# Message templates
# ─────────────────────────────────────────────────────────────────────────────

_CONFIRM_HEADER = """
⚠️  Confirmation Required
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Action  : {intent}
  Reason  : {reason}
  Details : {detail_summary}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

_DANGEROUS_HEADER = """
🔴  HIGH-RISK ACTION — READ CAREFULLY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Action  : {intent}
  Risk    : {reason}
  Details : {detail_summary}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  To confirm, type the action name exactly: {confirm_word}
  To cancel, press Enter or type anything else.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

_COUNTDOWN_LINE = "  Proceeding in {n} seconds... (type 'n' + Enter to cancel)"
_CANCELLED = "  ✗ Action cancelled."
_CONFIRMED = "  ✓ Action confirmed."
_TYPED_WRONG = "  ✗ Confirmation text didn't match. Action cancelled."


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _detail_summary(assessment: RiskAssessment, max_items: int = 3) -> str:
    if not assessment.details:
        return "N/A"
    shown = assessment.details[:max_items]
    return "; ".join(shown)


def _confirm_word_for(intent: str) -> str:
    """The exact word the user must type to confirm a dangerous action."""
    # Use the last segment of the intent name, capitalised
    word = intent.strip().lower().replace("_", " ").split()[-1] if intent else "confirm"
    return word.upper()


# ─────────────────────────────────────────────────────────────────────────────
# Core confirmation logic
# ─────────────────────────────────────────────────────────────────────────────

def _confirm_cli(
    assessment: RiskAssessment,
    countdown_seconds: int = 5,
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> bool:
    """Confirm an action in CLI/terminal mode.

    CONFIRM level: countdown timer, user can cancel by typing 'n'.
    DANGEROUS level: user must type the action name exactly.

    Returns True if confirmed, False if cancelled.
    """
    intent = assessment.intent
    reason = assessment.reason
    detail_summary = _detail_summary(assessment)

    if assessment.level == CONFIRM:
        print(_CONFIRM_HEADER.format(
            intent=intent.replace("_", " ").title(),
            reason=reason,
            detail_summary=detail_summary,
        ))

        # Non-blocking countdown with cancel check
        import threading
        cancelled = [False]
        confirmed = [False]

        def _input_watcher() -> None:
            try:
                ans = input("  [Press Enter to proceed, or type 'n' to cancel]: ").strip().lower()
                if ans in {"n", "no", "cancel", "nope", "nah"}:
                    cancelled[0] = True
                else:
                    confirmed[0] = True
            except (EOFError, KeyboardInterrupt):
                cancelled[0] = True

        watcher = threading.Thread(target=_input_watcher, daemon=True)
        watcher.start()

        for remaining in range(countdown_seconds, 0, -1):
            if cancelled[0]:
                print(_CANCELLED)
                return False
            if confirmed[0]:
                break
            sys.stdout.write(f"\r  Proceeding in {remaining}s... (type 'n' + Enter to cancel)  ")
            sys.stdout.flush()
            time.sleep(1)
            if cancel_checker and cancel_checker():
                cancelled[0] = True

        if cancelled[0]:
            print("\n" + _CANCELLED)
            return False

        print("\n" + _CONFIRMED)
        return True

    elif assessment.level == DANGEROUS:
        confirm_word = _confirm_word_for(intent)
        print(_DANGEROUS_HEADER.format(
            intent=intent.replace("_", " ").upper(),
            reason=reason,
            detail_summary=detail_summary,
            confirm_word=confirm_word,
        ))

        try:
            answer = input(f"  Type '{confirm_word}' to confirm: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print("\n" + _CANCELLED)
            return False

        if answer == confirm_word:
            print(_CONFIRMED)
            return True
        else:
            print(_TYPED_WRONG)
            return False

    # SAFE — should not reach here but allow it
    return True


def _confirm_callback(
    assessment: RiskAssessment,
    ask_fn: Callable[[str], Optional[str]],
    countdown_seconds: int = 5,
) -> bool:
    """Confirm an action using a caller-provided question function.

    ask_fn(question: str) → user's answer string | None

    Used when ARIA is running with a GUI, voice interface, or web frontend.
    """
    intent = assessment.intent
    reason = assessment.reason
    detail_summary = _detail_summary(assessment)

    if assessment.level == CONFIRM:
        question = (
            f"⚠️ {reason}\n"
            f"Action: {intent.replace('_', ' ').title()}\n"
            f"Should I proceed? (yes/no)"
        )
        answer = ask_fn(question)
        if answer and answer.strip().lower() in {"yes", "y", "ok", "sure", "go", "proceed", "confirm"}:
            return True
        return False

    elif assessment.level == DANGEROUS:
        confirm_word = _confirm_word_for(intent)
        question = (
            f"🔴 HIGH-RISK: {reason}\n"
            f"Action: {intent.replace('_', ' ').upper()}\n"
            f"This action cannot be undone. Type '{confirm_word}' to confirm, or anything else to cancel."
        )
        answer = ask_fn(question)
        if answer and answer.strip().upper() == confirm_word:
            return True
        return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def confirm_action(
    payload: Dict[str, Any],
    seconds: int = 5,
    ask_fn: Optional[Callable[[str], Optional[str]]] = None,
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> bool:
    """Main entry point. Determine if an action should proceed.

    Safe actions pass through immediately. Confirm/Dangerous actions
    present the appropriate friction to the user.

    Args:
        payload:        The action payload dict {"intent": ..., "parameters": ...}.
        seconds:        Countdown duration for CONFIRM-level actions.
        ask_fn:         Optional callback for non-CLI environments.
                        Signature: ask_fn(question: str) -> str | None
                        If None, uses CLI stdin.
        cancel_checker: Optional callable returning True if cancelled externally.

    Returns:
        True if the action should proceed, False if cancelled/blocked.
    """
    assessment = assess_risk(payload)

    # SAFE — no friction
    if assessment.is_safe:
        return True

    # BLOCKED — should have been caught earlier but double-check here
    if assessment.is_blocked:
        print(f"\n🚫 BLOCKED: {assessment.reason}")
        return False

    # CONFIRM or DANGEROUS
    if ask_fn is not None:
        return _confirm_callback(assessment, ask_fn, countdown_seconds=seconds)
    else:
        return _confirm_cli(assessment, countdown_seconds=seconds, cancel_checker=cancel_checker)


def requires_confirmation(payload: Dict[str, Any]) -> bool:
    """Return True if this payload needs any confirmation (CONFIRM or DANGEROUS)."""
    level = assess_risk(payload).level
    return level in {CONFIRM, DANGEROUS}


def get_confirmation_message(payload: Dict[str, Any]) -> str:
    """Return a human-readable confirmation prompt string without actually asking.

    Useful for generating the question text to display in a UI before calling confirm_action.
    """
    assessment = assess_risk(payload)
    intent = assessment.intent.replace("_", " ").title()
    reason = assessment.reason

    if assessment.is_safe:
        return ""

    if assessment.is_blocked:
        return f"🚫 This action is blocked: {reason}"

    if assessment.level == CONFIRM:
        return (
            f"⚠️ Confirmation needed\n"
            f"Action: {intent}\n"
            f"Reason: {reason}\n"
            f"Should I go ahead?"
        )

    if assessment.level == DANGEROUS:
        confirm_word = _confirm_word_for(assessment.intent)
        return (
            f"🔴 High-risk action\n"
            f"Action: {intent}\n"
            f"Risk: {reason}\n"
            f"To confirm, say or type '{confirm_word}' — otherwise say no."
        )

    return f"Confirm action '{intent}'?"


def explain_risk(payload: Dict[str, Any]) -> str:
    """Return a full risk explanation for the user — useful for the agent's narration."""
    assessment = assess_risk(payload)
    lines = [
        f"Risk Level : {assessment.level.upper()}",
        f"Intent     : {assessment.intent}",
        f"Reason     : {assessment.reason}",
    ]
    if assessment.details:
        lines.append("Details    :")
        for d in assessment.details:
            lines.append(f"  • {d}")
    return "\n".join(lines)
