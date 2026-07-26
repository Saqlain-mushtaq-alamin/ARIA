"""
tests/test_phase3.py  ─  Phase 3: System Control (Mocked)
===========================================================
Tests open/close apps, volume, screenshot, file operations.
All OS operations are mocked — nothing actually runs on the system.
"""
import tests.mock_layer  # MUST be first

import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_runner import test, run_all
from modules import system_control


# ── App launch ────────────────────────────────────────────────────────────────

@test("open_app('notepad') returns success string", "PHASE3", "app_launch")
def t_open_notepad():
    result = system_control.open_app("notepad")
    assert isinstance(result, str), f"Expected string, got {type(result)}"
    assert result  # non-empty


@test("open_app('chrome') returns success string", "PHASE3", "app_launch")
def t_open_chrome():
    result = system_control.open_app("chrome")
    assert isinstance(result, str)


@test("APP_ALIASES contains notepad mapping", "PHASE3", "app_aliases")
def t_alias_notepad():
    assert "notepad" in system_control.APP_ALIASES
    assert system_control.APP_ALIASES["notepad"] == "notepad.exe"


@test("APP_ALIASES contains chrome mapping", "PHASE3", "app_aliases")
def t_alias_chrome():
    assert "chrome" in system_control.APP_ALIASES


@test("APP_ALIASES contains file explorer mapping", "PHASE3", "app_aliases")
def t_alias_explorer():
    assert "file explorer" in system_control.APP_ALIASES
    assert "fle explorere" in system_control.APP_ALIASES  # STT typo covered


# ── Volume ───────────────────────────────────────────────────────────────────

@test("set_volume(40) returns success string", "PHASE3", "volume")
def t_set_volume():
    result = system_control.set_volume(40)
    assert isinstance(result, str)
    assert "40" in result or "volume" in result.lower()


@test("set_volume(0) doesn't crash", "PHASE3", "volume")
def t_set_volume_zero():
    result = system_control.set_volume(0)
    assert isinstance(result, str)


@test("set_volume(100) doesn't crash", "PHASE3", "volume")
def t_set_volume_max():
    result = system_control.set_volume(100)
    assert isinstance(result, str)


# ── Screenshot ────────────────────────────────────────────────────────────────

@test("take_screenshot saves PNG file", "PHASE3", "screenshot")
def t_screenshot():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "test_screenshot.png")
        result = system_control.take_screenshot(path)
        assert isinstance(result, str)
        assert os.path.exists(path), f"Screenshot not created at {path}"


# ── File operations ───────────────────────────────────────────────────────────

@test("save_file creates file with content", "PHASE3", "file_ops")
def t_save_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "test_save.txt")
        result = system_control.save_file(path, "Hello ARIA!")
        assert os.path.exists(path)
        content = Path(path).read_text()
        assert "Hello ARIA!" in content


@test("save_file creates parent directories", "PHASE3", "file_ops")
def t_save_file_mkdir():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "subdir", "nested", "file.txt")
        system_control.save_file(path, "nested file")
        assert os.path.exists(path)


@test("create_file is alias for save_file", "PHASE3", "file_ops")
def t_create_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "created.txt")
        system_control.create_file(path, "created content")
        assert os.path.exists(path)


@test("list_directory returns formatted listing", "PHASE3", "file_ops")
def t_list_directory():
    result = system_control.list_directory(str(Path.home() / "Desktop"))
    assert isinstance(result, str)
    # Should contain path
    assert "Desktop" in result or "folder" in result.lower()


@test("resolve_path('desktop') → real Desktop path", "PHASE3", "path_resolve")
def t_resolve_desktop():
    resolved = system_control.resolve_path("desktop")
    expected = str(Path.home() / "Desktop")
    assert resolved.lower() == expected.lower(), f"Got: {resolved!r}"


@test("resolve_path('downloads') → real Downloads path", "PHASE3", "path_resolve")
def t_resolve_downloads():
    resolved = system_control.resolve_path("downloads")
    expected = str(Path.home() / "Downloads")
    assert resolved.lower() == expected.lower(), f"Got: {resolved!r}"


@test("delete_file moves to recycle buffer", "PHASE3", "file_ops")
def t_delete_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "to_delete.txt")
        Path(path).write_text("delete me")
        result = system_control.delete_file(path)
        assert isinstance(result, str)
        # File should be gone
        assert not os.path.exists(path)
        assert "backup" in result.lower() or "deleted" in result.lower()


if __name__ == "__main__":
    run_all("PHASE3")
