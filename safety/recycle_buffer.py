"""ARIA — Recycle Buffer (Safe Delete Layer).

Every delete operation in ARIA goes through this module instead of directly
removing files. Files are moved to a hidden buffer folder first, giving the
user a 48-hour window to undo any accidental deletion.

Features:
  - Intercept-and-buffer: files moved to ~/.aria_recycle_buffer/ with timestamp prefix
  - List buffer: see what's buffered and when it expires
  - Restore: bring any buffered item back to its original location
  - Auto-purge: items older than 48 hours are permanently removed
  - Audit trail: every buffer operation is logged
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

BUFFER_DIR      = str(Path.home() / ".aria_recycle_buffer")
BUFFER_META     = os.path.join(BUFFER_DIR, "_manifest.json")
BUFFER_TTL_HOURS = 48


# ─────────────────────────────────────────────────────────────────────────────
# Manifest helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_manifest() -> Dict[str, dict]:
    """Load the buffer manifest (maps buffer_name → metadata)."""
    if not os.path.exists(BUFFER_META):
        return {}
    try:
        with open(BUFFER_META, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_manifest(manifest: Dict[str, dict]) -> None:
    os.makedirs(BUFFER_DIR, exist_ok=True)
    with open(BUFFER_META, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def _add_to_manifest(buffer_name: str, original_path: str, is_dir: bool) -> None:
    manifest = _load_manifest()
    manifest[buffer_name] = {
        "original_path": original_path,
        "buffer_path":   os.path.join(BUFFER_DIR, buffer_name),
        "deleted_at":    datetime.now().isoformat(),
        "expires_at":    (datetime.now() + timedelta(hours=BUFFER_TTL_HOURS)).isoformat(),
        "is_dir":        is_dir,
        "size_bytes":    _measure_size(os.path.join(BUFFER_DIR, buffer_name)),
    }
    _save_manifest(manifest)


def _measure_size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    if os.path.isdir(path):
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
        return total
    return 0


def _size_human(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes // 1024}KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes // (1024 ** 2)}MB"
    return f"{size_bytes // (1024 ** 3)}GB"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def safe_delete(path: str) -> str:
    """Move a file or folder into the recycle buffer instead of permanently deleting.

    Args:
        path: Path to the file or folder to delete.

    Returns:
        A status string describing what happened.
    """
    real = os.path.normpath(os.path.expandvars(os.path.expanduser(path.strip())))

    if not os.path.exists(real):
        return f"Not found: {real}"

    os.makedirs(BUFFER_DIR, exist_ok=True)

    # Build a unique buffer name: timestamp + original basename
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
    basename = os.path.basename(real)
    buffer_name = f"{ts}__{basename}"
    buffer_path = os.path.join(BUFFER_DIR, buffer_name)

    is_dir = os.path.isdir(real)

    try:
        shutil.move(real, buffer_path)
    except Exception as exc:
        return f"Failed to move '{real}' to buffer: {exc}"

    _add_to_manifest(buffer_name, real, is_dir)

    expires = (datetime.now() + timedelta(hours=BUFFER_TTL_HOURS)).strftime("%Y-%m-%d %H:%M")
    size = _measure_size(buffer_path)
    return (
        f"✓ Deleted: {real}\n"
        f"  ({_size_human(size)} moved to recycle buffer — expires {expires})\n"
        f"  Restore with: restore_deleted('{buffer_name}')"
    )


def list_buffer() -> str:
    """List all items currently in the recycle buffer."""
    manifest = _load_manifest()
    _purge_expired_from_manifest(manifest)

    if not manifest:
        return "Recycle buffer is empty."

    lines = [f"🗑️  Recycle Buffer — {len(manifest)} item(s):"]
    lines.append(f"  {'#':<3}  {'Name':<40}  {'Size':<8}  {'Expires'}")
    lines.append("  " + "─" * 70)

    now = datetime.now()
    for i, (buf_name, meta) in enumerate(sorted(manifest.items()), 1):
        orig = meta.get("original_path", "?")
        size = _size_human(meta.get("size_bytes", 0))
        expires_str = meta.get("expires_at", "?")
        try:
            exp_dt = datetime.fromisoformat(expires_str)
            mins_left = int((exp_dt - now).total_seconds() / 60)
            if mins_left > 120:
                exp_label = f"{mins_left // 60}h left"
            elif mins_left > 0:
                exp_label = f"{mins_left}m left"
            else:
                exp_label = "EXPIRED"
        except Exception:
            exp_label = expires_str[:16]

        short_name = os.path.basename(orig)
        lines.append(f"  {i:<3}  {short_name:<40}  {size:<8}  {exp_label}")
        lines.append(f"       ↳ Original: {orig}")
        lines.append(f"       ↳ Buffer ID: {buf_name}")

    lines.append(f"\n  Restore: restore_deleted('<buffer_id>')")
    lines.append(f"  Purge:   purge_buffer()")
    return "\n".join(lines)


def restore_deleted(buffer_name: str) -> str:
    """Restore a buffered item back to its original location.

    Args:
        buffer_name: The buffer ID shown in list_buffer() output.

    Returns:
        Status string.
    """
    manifest = _load_manifest()

    # Fuzzy match — allow passing original filename
    if buffer_name not in manifest:
        matches = [k for k in manifest if os.path.basename(manifest[k].get("original_path", "")) == buffer_name
                   or k.endswith("__" + buffer_name)]
        if len(matches) == 1:
            buffer_name = matches[0]
        elif len(matches) > 1:
            names = "\n".join(f"  {m}" for m in matches)
            return f"Multiple matches found. Specify the exact buffer ID:\n{names}"
        else:
            return f"Buffer item not found: '{buffer_name}'"

    meta = manifest[buffer_name]
    buffer_path = os.path.join(BUFFER_DIR, buffer_name)
    original_path = meta.get("original_path", "")

    if not os.path.exists(buffer_path):
        return f"Buffer item no longer exists (may have been purged): {buffer_name}"

    if not original_path:
        return f"No original path recorded for buffer item: {buffer_name}"

    # Create parent directory if needed
    parent = os.path.dirname(original_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    # If original location is already occupied, add a suffix
    target = original_path
    if os.path.exists(target):
        base, ext = os.path.splitext(target)
        ts = datetime.now().strftime("%H%M%S")
        target = f"{base}_restored_{ts}{ext}"

    try:
        shutil.move(buffer_path, target)
    except Exception as exc:
        return f"Restore failed: {exc}"

    # Remove from manifest
    del manifest[buffer_name]
    _save_manifest(manifest)

    suffix = " (renamed to avoid conflict)" if target != original_path else ""
    return f"✓ Restored: {target}{suffix}"


def purge_buffer(force_all: bool = False) -> str:
    """Permanently delete expired items from the buffer.

    Args:
        force_all: If True, delete ALL buffered items regardless of TTL.

    Returns:
        Status string with count of items purged.
    """
    manifest = _load_manifest()
    now = datetime.now()
    purged = 0
    errors = 0

    keys_to_purge = []
    for buf_name, meta in list(manifest.items()):
        if force_all:
            keys_to_purge.append(buf_name)
            continue
        expires_str = meta.get("expires_at", "")
        try:
            exp_dt = datetime.fromisoformat(expires_str)
            if now > exp_dt:
                keys_to_purge.append(buf_name)
        except Exception:
            keys_to_purge.append(buf_name)  # corrupt entry — purge

    for buf_name in keys_to_purge:
        buf_path = os.path.join(BUFFER_DIR, buf_name)
        try:
            if os.path.isdir(buf_path):
                shutil.rmtree(buf_path)
            elif os.path.exists(buf_path):
                os.remove(buf_path)
            del manifest[buf_name]
            purged += 1
        except Exception:
            errors += 1

    _save_manifest(manifest)

    scope = "all" if force_all else "expired"
    msg = f"✓ Purged {purged} {scope} item(s) from recycle buffer."
    if errors:
        msg += f" ({errors} item(s) could not be removed.)"
    if not keys_to_purge:
        msg = "Nothing to purge — buffer is clean."
    return msg


def _purge_expired_from_manifest(manifest: Dict[str, dict]) -> None:
    """In-place removal of expired entries from a manifest dict (no disk deletion)."""
    now = datetime.now()
    expired_keys = []
    for buf_name, meta in manifest.items():
        try:
            exp_dt = datetime.fromisoformat(meta.get("expires_at", ""))
            if now > exp_dt:
                expired_keys.append(buf_name)
        except Exception:
            pass
    for k in expired_keys:
        del manifest[k]


def auto_purge_on_startup() -> None:
    """Call this at ARIA startup to silently clean expired buffer items."""
    try:
        purge_buffer(force_all=False)
    except Exception:
        pass
