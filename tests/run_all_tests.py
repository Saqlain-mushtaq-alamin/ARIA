"""
tests/run_all_tests.py
=======================
Master test runner — runs all ARIA test phases and generates full report.

Usage:
    python tests/run_all_tests.py

All phases run in order:
  Phase 1 — Intent Classification
  Phase 2 — Voice Pipeline
  Phase 3 — System Control (mocked)
  Phase 4 — Browser & Web Data
  Phase 5 — Memory System
  Phase 6 — Scheduler & Goals
  Safety  — Security & Audit
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── Import all test modules (their @test decorators auto-register) ────────────
print("\n[ARIA Test Suite] Loading test modules...")

import tests.test_phase1   # Phase 1: Intent Classification
import tests.test_phase2   # Phase 2: Voice Pipeline
import tests.test_phase3   # Phase 3: System Control
import tests.test_phase4   # Phase 4: Browser & Web
import tests.test_phase5   # Phase 5: Memory
import tests.test_phase6   # Phase 6: Scheduler
import tests.test_safety   # Safety & Audit

# ── Run everything ────────────────────────────────────────────────────────────
from tests.test_runner import run_all

results = run_all()  # No phase filter = run all registered tests

# Exit with non-zero code if any tests failed
failed = sum(1 for r in results if r["status"] == "FAIL")
if failed:
    print(f"\n[ARIA] {failed} test(s) failed. Check tests/results/test_report_LATEST.txt for details.")
    sys.exit(1)
else:
    print("\n[ARIA] All tests passed! Sir, ARIA is ready for live use. ✓")
    sys.exit(0)
