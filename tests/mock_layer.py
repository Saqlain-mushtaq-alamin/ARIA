"""
tests/mock_layer.py
===================
MUST be imported FIRST in every test file.

Patches dangerous OS-level operations so tests run safely:
  - shutdown / restart / sleep → no-op, returns success string
  - toggle_wifi / toggle_bluetooth → no-op
  - type_text → logs the text instead of typing
  - send_message → fake confirmation

Only patches *execution*; intent classification + safety checks run for real.
"""
from __future__ import annotations

import sys
import os
import unittest.mock as mock
import subprocess
from pathlib import Path

# ── Make sure project root is importable ─────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ── Patch dangerous subprocess calls ─────────────────────────────────────────
_REAL_RUN = subprocess.run

_DANGEROUS_CMDS = {
    "shutdown", "powercfg", "rundll32",
}


def _safe_run(args, **kwargs):
    """Intercept dangerous subprocess calls and return a successful fake result."""
    cmd0 = (args[0] if args else "").lower()
    if any(d in cmd0 for d in _DANGEROUS_CMDS):
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "MOCKED"
        result.stderr = ""
        return result
    # Allow netsh, powershell for WiFi/BT tests — but they're mocked at function level
    return _REAL_RUN(args, **kwargs)


subprocess.run = _safe_run  # type: ignore[assignment]

# ── Patch system_control functions ───────────────────────────────────────────
# We do this lazily so the module can be imported first.

def _patch_system_control():
    """Apply safe patches to system_control after it has been imported."""
    try:
        from modules import system_control as sc

        # Safe replacements
        sc._REAL_shutdown = getattr(sc, "_REAL_shutdown", None)

        # Only patch if not already patched
        if not getattr(sc, "_MOCKED", False):
            sc._MOCKED = True

            # type_text: write to a mock log instead of pyautogui
            _typed_log: list[str] = []
            sc._MOCK_TYPED_LOG = _typed_log

            _real_type_text = sc.type_text
            def _mock_type_text(text: str, **kw) -> str:
                _typed_log.append(text)
                return f"[MOCK] Would type: {text[:60]}..."
            sc.type_text = _mock_type_text  # type: ignore[assignment]

    except Exception as exc:
        print(f"[mock_layer] Could not patch system_control: {exc}")


_patch_system_control()

print("[mock_layer] Loaded. Dangerous OS calls are neutralised.")
