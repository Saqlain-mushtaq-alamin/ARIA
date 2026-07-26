"""
tests/test_runner.py
====================
Shared test runner framework used by all ARIA test phases.

Usage:
    from tests.test_runner import test, run_all, PASSED, FAILED
"""
from __future__ import annotations

import io
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

# ── Force UTF-8 output on Windows to avoid UnicodeEncodeError ────────────────
try:
    if hasattr(sys.stdout, "buffer") and sys.stdout.encoding and \
            sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# ── ANSI colours ──────────────────────────────────────────────────────────────
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

PASSED  = "PASS"
FAILED  = "FAIL"
SKIPPED = "SKIP"

# ── Registry ──────────────────────────────────────────────────────────────────
_REGISTRY: List[dict] = []


def test(
    description: str,
    phase: str = "MISC",
    category: str = "",
    skip_reason: Optional[str] = None,
):
    """Decorator that registers a test function."""
    def _decorator(fn: Callable) -> Callable:
        _REGISTRY.append({
            "fn": fn,
            "description": description,
            "phase": phase,
            "category": category,
            "skip_reason": skip_reason,
        })
        return fn
    return _decorator


def _run_test(entry: dict) -> dict:
    """Execute a single test entry and return its result dict."""
    fn          = entry["fn"]
    desc        = entry["description"]
    phase       = entry["phase"]
    cat         = entry["category"]
    skip_reason = entry.get("skip_reason")

    result = {
        "description": desc,
        "phase": phase,
        "category": cat,
        "status": PASSED,
        "error": "",
        "duration_ms": 0,
    }

    if skip_reason:
        result["status"] = SKIPPED
        result["error"]  = skip_reason
        print(f"  {_YELLOW}[SKIP]{_RESET}  {desc}  ({skip_reason})")
        return result

    t0 = time.perf_counter()
    try:
        fn()
        elapsed = (time.perf_counter() - t0) * 1000
        result["duration_ms"] = elapsed
        print(f"  {_GREEN}[PASS]{_RESET}  {desc}  ({elapsed:.0f}ms)")
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        result["status"]      = FAILED
        result["error"]       = str(exc)
        result["duration_ms"] = elapsed
        tb = traceback.format_exc(limit=4)
        print(f"  {_RED}[FAIL]{_RESET}  {desc}")
        print(f"        {_YELLOW}{str(exc)}{_RESET}")
        for line in tb.strip().splitlines()[-4:]:
            print(f"        {line}")
    return result


def run_all(phase_filter: Optional[str] = None) -> List[dict]:
    """Run all registered tests; optionally filter by phase name."""
    results: List[dict] = []
    tests_to_run = [
        e for e in _REGISTRY
        if phase_filter is None or e["phase"] == phase_filter
    ]

    if not tests_to_run:
        print(f"No tests found{f' for phase {phase_filter}' if phase_filter else ''}.")
        return results

    total = len(tests_to_run)
    sep   = "=" * 60
    print(f"\n{_BOLD}{_CYAN}{sep}{_RESET}")
    print(f"{_BOLD}  ARIA Test Suite -- {total} tests"
          f"{f' (phase: {phase_filter})' if phase_filter else ''}{_RESET}")
    print(f"{_BOLD}{_CYAN}{sep}{_RESET}\n")

    # Group by phase
    phases_seen: List[str] = []
    for entry in tests_to_run:
        if entry["phase"] not in phases_seen:
            phases_seen.append(entry["phase"])

    for phase in phases_seen:
        phase_tests = [e for e in tests_to_run if e["phase"] == phase]
        print(f"\n{_BOLD}-- {phase} ({len(phase_tests)} tests) --{_RESET}")
        for entry in phase_tests:
            results.append(_run_test(entry))

    # Summary
    n_pass = sum(1 for r in results if r["status"] == PASSED)
    n_fail = sum(1 for r in results if r["status"] == FAILED)
    n_skip = sum(1 for r in results if r["status"] == SKIPPED)

    print(f"\n{_BOLD}{_CYAN}{sep}{_RESET}")
    print(
        f"{_BOLD}  Results: "
        f"{_GREEN}{n_pass} passed{_RESET}, "
        f"{_RED}{n_fail} failed{_RESET}, "
        f"{_YELLOW}{n_skip} skipped{_RESET}"
        f"  /  {total} total{_RESET}"
    )
    print(f"{_BOLD}{_CYAN}{sep}{_RESET}\n")

    _write_report(results)
    return results


def _write_report(results: List[dict]) -> None:
    """Write a UTF-8 test report to tests/results/."""
    try:
        repo_root   = Path(__file__).resolve().parents[1]
        results_dir = repo_root / "tests" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)

        ts           = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path  = results_dir / f"test_report_{ts}.txt"
        latest_path  = results_dir / "test_report_LATEST.txt"

        lines = [
            f"ARIA Test Report -- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
        ]
        for r in results:
            sym = "[PASS]" if r["status"] == PASSED else ("[SKIP]" if r["status"] == SKIPPED else "[FAIL]")
            lines.append(f"[{r['phase']:12}] {sym}  {r['description']}")
            if r["error"] and r["status"] == FAILED:
                lines.append(f"             Error: {r['error']}")

        n_pass = sum(1 for r in results if r["status"] == PASSED)
        n_fail = sum(1 for r in results if r["status"] == FAILED)
        n_skip = sum(1 for r in results if r["status"] == SKIPPED)
        lines += [
            "=" * 60,
            f"PASS: {n_pass}  FAIL: {n_fail}  SKIP: {n_skip}  TOTAL: {len(results)}",
        ]

        content = "\n".join(lines)
        report_path.write_text(content, encoding="utf-8")
        latest_path.write_text(content, encoding="utf-8")
        print(f"  Report saved -> {latest_path}")
    except Exception as exc:
        print(f"  [report] Could not write report: {exc}")
