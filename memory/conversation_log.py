"""ARIA — Conversation Log (SQLite).

Two responsibilities:
  1. EXACT HISTORY  — every message stored verbatim with timestamps.
  2. CHAT SESSIONS  — Copilot-style named chat threads. Each session is an
                      independent conversation the user can switch between,
                      resume, rename, delete, or export.

Architecture
────────────
  interactions   — every user/assistant message turn (belongs to a session)
  sessions       — named chat threads (like Copilot chat tabs)
  context_links  — optional references between sessions (continue / branch from)

Session lifecycle
─────────────────
  create_session("Plan my exam week")   →  session_id
  set_active_session(session_id)        →  makes it the default for new messages
  log_interaction(user, assistant)      →  appended to the active session
  get_session_context(session_id, n=10) →  last N turns for LLM context injection
  list_sessions()                       →  all sessions, newest first
  resume_session(session_id)            →  switch active session + return context
  rename_session(session_id, new_name)  →  update display name
  delete_session(session_id)            →  soft-delete (marked, not erased)
  export_session(session_id)            →  JSON export of full conversation

Cross-session continuity
─────────────────────────
  When the agent detects a command refers to a previous session
  ("continue what we started yesterday about the essay"),
  get_relevant_session() does a text search across all sessions to find
  the most likely match and returns its context for injection.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Environment / path setup
# ─────────────────────────────────────────────────────────────────────────────

def _maybe_load_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
        load_dotenv(os.path.join("config", ".env"))
    except Exception:
        pass

_maybe_load_env()

DEFAULT_DB_PATH = os.getenv(
    "CONVERSATION_DB_PATH",
    os.path.join(os.path.dirname(__file__), "conversation.db"),
)

# In-process active session — shared across imports in the same process
_ACTIVE_SESSION_ID: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Interaction:
    id: int
    session_id: str
    timestamp: str
    role: str               # "user" | "assistant" | "system"
    text: str
    metadata: Dict[str, Any]

    # Backwards-compatible properties
    @property
    def user_text(self) -> str:
        return self.text if self.role == "user" else ""

    @property
    def assistant_text(self) -> str:
        return self.text if self.role == "assistant" else ""


@dataclass
class Session:
    session_id: str
    name: str
    created_at: str
    updated_at: str
    message_count: int
    is_deleted: bool
    continued_from: Optional[str]   # session_id this was branched from
    summary: str                    # auto-generated one-liner summary


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT 'New Chat',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0,
    continued_from  TEXT,
    summary         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL DEFAULT 'default',
    timestamp_utc   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'user',
    text            TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_interactions_session
    ON interactions(session_id, id);

CREATE INDEX IF NOT EXISTS idx_interactions_timestamp
    ON interactions(timestamp_utc);

CREATE INDEX IF NOT EXISTS idx_sessions_updated
    ON sessions(updated_at DESC);
"""


# ─────────────────────────────────────────────────────────────────────────────
# DB connection
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    _ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Create tables and indexes if they don't exist. Safe to call repeatedly."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)
    _ensure_default_session(db_path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Session management
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_default_session(db_path: str = DEFAULT_DB_PATH) -> str:
    """Make sure a 'default' session exists for backwards compatibility."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = 'default'"
        ).fetchone()
        if not row:
            now = _now_iso()
            conn.execute(
                "INSERT INTO sessions(session_id, name, created_at, updated_at) VALUES (?,?,?,?)",
                ("default", "General Chat", now, now),
            )
    return "default"


def create_session(
    name: str = "New Chat",
    continued_from: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Create a new named chat session and return its session_id.

    Args:
        name:           Display name for this chat (e.g. 'Exam planning').
        continued_from: Optional session_id to mark this as a continuation/branch.
        db_path:        Database path.

    Returns:
        New session_id (UUID string).
    """
    init_db(db_path)
    session_id = str(uuid.uuid4())
    now = _now_iso()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sessions(session_id, name, created_at, updated_at, continued_from)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, name.strip() or "New Chat", now, now, continued_from),
        )
    return session_id


def set_active_session(session_id: str) -> None:
    """Set the in-process active session. Subsequent log_interaction() calls use this."""
    global _ACTIVE_SESSION_ID
    _ACTIVE_SESSION_ID = session_id


def get_active_session(db_path: str = DEFAULT_DB_PATH) -> str:
    """Return the current active session_id. Creates a default one if none set."""
    global _ACTIVE_SESSION_ID
    if _ACTIVE_SESSION_ID:
        return _ACTIVE_SESSION_ID
    init_db(db_path)
    return "default"


def rename_session(
    session_id: str,
    new_name: str,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Rename a chat session."""
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE sessions SET name = ?, updated_at = ? WHERE session_id = ?",
            (new_name.strip(), _now_iso(), session_id),
        )
    return f"Session renamed to '{new_name}'."


def delete_session(
    session_id: str,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Soft-delete a session (hidden from lists, data preserved)."""
    if session_id == "default":
        return "The default session cannot be deleted."
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE sessions SET is_deleted = 1, updated_at = ? WHERE session_id = ?",
            (_now_iso(), session_id),
        )
    global _ACTIVE_SESSION_ID
    if _ACTIVE_SESSION_ID == session_id:
        _ACTIVE_SESSION_ID = "default"
    return f"Chat session deleted."


def list_sessions(
    include_deleted: bool = False,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Session]:
    """Return all sessions, newest first.

    Args:
        include_deleted: Include soft-deleted sessions.
        db_path:         Database path.

    Returns:
        List of Session objects.
    """
    init_db(db_path)
    where = "" if include_deleted else "WHERE s.is_deleted = 0"
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
                s.session_id,
                s.name,
                s.created_at,
                s.updated_at,
                s.is_deleted,
                s.continued_from,
                s.summary,
                COUNT(i.id) AS msg_count
            FROM sessions s
            LEFT JOIN interactions i ON i.session_id = s.session_id
            {where}
            GROUP BY s.session_id
            ORDER BY s.updated_at DESC
            """,
        ).fetchall()

    sessions = []
    for row in rows:
        sid, name, created, updated, deleted, cont_from, summary, count = row
        sessions.append(Session(
            session_id=str(sid),
            name=str(name),
            created_at=str(created),
            updated_at=str(updated),
            message_count=int(count),
            is_deleted=bool(deleted),
            continued_from=cont_from,
            summary=str(summary or ""),
        ))
    return sessions


def list_sessions_formatted(db_path: str = DEFAULT_DB_PATH) -> str:
    """Return a human-readable list of sessions (like Copilot sidebar)."""
    sessions = list_sessions(db_path=db_path)
    if not sessions:
        return "No chat sessions found."

    active = get_active_session(db_path)
    lines = ["💬 Chat Sessions:"]
    lines.append(f"  {'#':<3}  {'Name':<35}  {'Messages':<10}  {'Last active'}")
    lines.append("  " + "─" * 70)

    for i, s in enumerate(sessions, 1):
        marker = " ← active" if s.session_id == active else ""
        updated = s.updated_at[:16].replace("T", " ")
        cont = f" (continued from another chat)" if s.continued_from else ""
        lines.append(
            f"  {i:<3}  {s.name:<35}  {s.message_count:<10}  {updated}{marker}{cont}"
        )
        lines.append(f"       ID: {s.session_id}")
        if s.summary:
            lines.append(f"       ↳ {s.summary}")

    lines.append(f"\n  Use: resume_session('<session_id>') to switch.")
    return "\n".join(lines)


def resume_session(
    session_id: str,
    context_turns: int = 10,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Switch to a session and return its recent context for LLM injection.

    This is the key function for cross-session continuity. Call this when
    the user says "continue what we started about X".

    Args:
        session_id:    The session to resume.
        context_turns: How many recent message pairs to return.
        db_path:       Database path.

    Returns:
        Dict with keys:
          session    — Session object
          context    — List of {"role": ..., "content": ...} dicts for LLM
          summary    — Session summary string
    """
    init_db(db_path)
    set_active_session(session_id)

    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT session_id, name, created_at, updated_at, is_deleted, continued_from, summary "
            "FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

    if not row:
        raise ValueError(f"Session not found: {session_id}")

    session = Session(
        session_id=row[0], name=row[1], created_at=row[2],
        updated_at=row[3], message_count=0, is_deleted=bool(row[4]),
        continued_from=row[5], summary=row[6] or "",
    )

    context = get_session_context(session_id, turns=context_turns, db_path=db_path)

    return {
        "session": session,
        "context": context,
        "summary": session.summary,
        "message": (
            f"✓ Resumed: '{session.name}'\n"
            f"  {len(context)} recent messages loaded into context."
        ),
    }


def get_relevant_session(
    query: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Find the most relevant session by searching message content.

    Used when user says "continue what we started about X" or
    "go back to the essay planning chat".

    Args:
        query:   The search phrase.
        db_path: Database path.

    Returns:
        resume_session() result dict for the best match, or None.
    """
    init_db(db_path)
    q = f"%{query.strip()}%"

    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT i.session_id, COUNT(*) AS hits, s.name
            FROM interactions i
            JOIN sessions s ON s.session_id = i.session_id
            WHERE (i.text LIKE ?) AND s.is_deleted = 0
            GROUP BY i.session_id
            ORDER BY hits DESC, s.updated_at DESC
            LIMIT 1
            """,
            (q,),
        ).fetchone()

    if not row:
        return None

    session_id, hits, name = row
    result = resume_session(session_id, db_path=db_path)
    result["match_hits"] = hits
    result["message"] = (
        f"✓ Found relevant session: '{name}' ({hits} matching message(s)).\n"
        f"  Resuming and loading context..."
    )
    return result


def _auto_name_session(
    session_id: str,
    first_user_text: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Auto-generate a session name from the first user message (like Copilot does)."""
    if not first_user_text.strip():
        return
    # Use first 50 chars of the first message as session name
    auto_name = first_user_text.strip()[:50]
    if len(first_user_text.strip()) > 50:
        auto_name += "..."
    with _connect(db_path) as conn:
        # Only rename if still default
        conn.execute(
            "UPDATE sessions SET name = ?, updated_at = ? "
            "WHERE session_id = ? AND name = 'New Chat'",
            (auto_name, _now_iso(), session_id),
        )


def update_session_summary(
    session_id: str,
    summary: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Store a one-line summary of the session (generated by LLM after each turn)."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE sessions SET summary = ?, updated_at = ? WHERE session_id = ?",
            (summary.strip()[:200], _now_iso(), session_id),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Message logging
# ─────────────────────────────────────────────────────────────────────────────

def log_interaction(
    user_text: str,
    assistant_text: str,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Tuple[int, int]:
    """Log one user/assistant exchange to the active (or specified) session.

    Backwards-compatible with original signature.

    Args:
        user_text:      The user's message.
        assistant_text: The assistant's response.
        metadata:       Optional metadata dict.
        session_id:     Session to log to. Defaults to active session.
        db_path:        Database path.

    Returns:
        Tuple of (user_message_id, assistant_message_id).
    """
    if not str(user_text or "").strip():
        raise ValueError("user_text is required")
    if assistant_text is None:
        assistant_text = ""

    init_db(db_path)

    sid = session_id or get_active_session(db_path)
    now = _now_iso()
    meta = dict(metadata or {})
    meta.setdefault("timestamp", now)
    meta_json = json.dumps(meta, ensure_ascii=False)

    with _connect(db_path) as conn:
        # Check if this is the first message in a 'New Chat' session → auto-name
        first_in_session = conn.execute(
            "SELECT COUNT(*) FROM interactions WHERE session_id = ?", (sid,)
        ).fetchone()[0] == 0

        user_id = conn.execute(
            "INSERT INTO interactions(session_id, timestamp_utc, role, text, metadata_json) "
            "VALUES (?, ?, 'user', ?, ?)",
            (sid, now, str(user_text).strip(), meta_json),
        ).lastrowid

        asst_id = conn.execute(
            "INSERT INTO interactions(session_id, timestamp_utc, role, text, metadata_json) "
            "VALUES (?, ?, 'assistant', ?, ?)",
            (sid, now, str(assistant_text), meta_json),
        ).lastrowid

        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (now, sid),
        )

    if first_in_session:
        _auto_name_session(sid, str(user_text), db_path)

    if user_id is None or asst_id is None:
        raise RuntimeError("Failed to log interaction (missing row id)")
    return int(user_id), int(asst_id)


def log_message(
    role: str,
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> int:
    """Log a single message (any role) to the active session.

    More flexible than log_interaction() — use for system messages,
    tool outputs, or cases where you log user and assistant separately.

    Args:
        role:       'user' | 'assistant' | 'system' | 'tool'
        text:       Message content.
        metadata:   Optional metadata.
        session_id: Target session (default: active).
        db_path:    Database path.

    Returns:
        Row id of the inserted message.
    """
    init_db(db_path)
    sid = session_id or get_active_session(db_path)
    now = _now_iso()
    meta = dict(metadata or {})
    meta.setdefault("timestamp", now)
    meta_json = json.dumps(meta, ensure_ascii=False)

    with _connect(db_path) as conn:
        row_id = conn.execute(
            "INSERT INTO interactions(session_id, timestamp_utc, role, text, metadata_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, now, str(role), str(text), meta_json),
        ).lastrowid
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (now, sid),
        )

    if row_id is None:
        raise RuntimeError("Failed to log message (missing row id)")
    return int(row_id)


# ─────────────────────────────────────────────────────────────────────────────
# Context retrieval
# ─────────────────────────────────────────────────────────────────────────────

def get_session_context(
    session_id: Optional[str] = None,
    turns: int = 10,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, str]]:
    """Return the last N message turns as LLM-ready context dicts.

    This is injected into every LLM call so the model knows what was
    discussed earlier in the session.

    Args:
        session_id: Target session (default: active).
        turns:      Number of complete user/assistant pairs to return.
        db_path:    Database path.

    Returns:
        List of {"role": "user"|"assistant", "content": "..."} dicts,
        oldest first (ready to pass directly to ollama.chat(messages=...)).
    """
    init_db(db_path)
    sid = session_id or get_active_session(db_path)
    limit = max(1, turns) * 2  # each turn = 2 rows

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT role, text FROM (
                SELECT role, text, id
                FROM interactions
                WHERE session_id = ? AND role IN ('user', 'assistant')
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
            """,
            (sid, limit),
        ).fetchall()

    return [{"role": row[0], "content": row[1]} for row in rows]


def get_full_context_string(
    session_id: Optional[str] = None,
    turns: int = 10,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Return recent context as a readable string (for injecting into prompts)."""
    messages = get_session_context(session_id, turns=turns, db_path=db_path)
    if not messages:
        return ""
    lines = ["Recent conversation:"]
    for m in messages:
        role_label = "You" if m["role"] == "user" else "ARIA"
        lines.append(f"  {role_label}: {m['content'][:200]}")
    return "\n".join(lines)


def get_recent_interactions(
    limit: int = 20,
    session_id: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Interaction]:
    """Return recent interactions from a session. Backwards-compatible."""
    init_db(db_path)
    sid = session_id or get_active_session(db_path)
    n = max(1, int(limit))

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, timestamp_utc, role, text, metadata_json
            FROM interactions
            WHERE session_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (sid, n),
        ).fetchall()

    result = []
    for row in rows:
        try:
            meta = json.loads(row[5] or "{}")
        except Exception:
            meta = {}
        result.append(Interaction(
            id=int(row[0]), session_id=str(row[1]), timestamp=str(row[2]),
            role=str(row[3]), text=str(row[4]), metadata=meta,
        ))
    return result


def search_interactions(
    query: str,
    limit: int = 20,
    session_id: Optional[str] = None,
    all_sessions: bool = False,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Interaction]:
    """Search message text. Optionally search across all sessions.

    Args:
        query:       Search term.
        limit:       Max results.
        session_id:  Restrict to one session (default: active). Ignored if all_sessions=True.
        all_sessions: Search across all non-deleted sessions.
        db_path:     Database path.

    Returns:
        Matching Interaction objects.
    """
    if not str(query or "").strip():
        raise ValueError("query is required")

    init_db(db_path)
    q = f"%{str(query).strip()}%"
    n = max(1, int(limit))

    if all_sessions:
        sql = """
            SELECT i.id, i.session_id, i.timestamp_utc, i.role, i.text, i.metadata_json
            FROM interactions i
            JOIN sessions s ON s.session_id = i.session_id
            WHERE i.text LIKE ? AND s.is_deleted = 0
            ORDER BY i.id DESC LIMIT ?
        """
        params = (q, n)
    else:
        sid = session_id or get_active_session(db_path)
        sql = """
            SELECT id, session_id, timestamp_utc, role, text, metadata_json
            FROM interactions
            WHERE session_id = ? AND text LIKE ?
            ORDER BY id DESC LIMIT ?
        """
        params = (sid, q, n)

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    result = []
    for row in rows:
        try:
            meta = json.loads(row[5] or "{}")
        except Exception:
            meta = {}
        result.append(Interaction(
            id=int(row[0]), session_id=str(row[1]), timestamp=str(row[2]),
            role=str(row[3]), text=str(row[4]), metadata=meta,
        ))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Export / utility
# ─────────────────────────────────────────────────────────────────────────────

def export_session(
    session_id: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Export a full session as a formatted JSON string.

    Useful for saving conversations, sharing, or feeding into the weekly review.
    """
    init_db(db_path)
    sid = session_id or get_active_session(db_path)

    with _connect(db_path) as conn:
        session_row = conn.execute(
            "SELECT session_id, name, created_at, updated_at, summary FROM sessions WHERE session_id = ?",
            (sid,),
        ).fetchone()

        messages = conn.execute(
            "SELECT role, text, timestamp_utc, metadata_json FROM interactions "
            "WHERE session_id = ? ORDER BY id ASC",
            (sid,),
        ).fetchall()

    if not session_row:
        return json.dumps({"error": f"Session not found: {sid}"}, indent=2)

    export_data = {
        "session_id":   session_row[0],
        "name":         session_row[1],
        "created_at":   session_row[2],
        "updated_at":   session_row[3],
        "summary":      session_row[4],
        "message_count": len(messages),
        "messages": [
            {
                "role":      m[0],
                "text":      m[1],
                "timestamp": m[2],
                "metadata":  json.loads(m[3] or "{}"),
            }
            for m in messages
        ],
    }
    return json.dumps(export_data, indent=2, ensure_ascii=False)


def get_session_stats(db_path: str = DEFAULT_DB_PATH) -> str:
    """Return stats across all sessions — useful for the weekly review."""
    init_db(db_path)
    with _connect(db_path) as conn:
        total_sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE is_deleted = 0"
        ).fetchone()[0]
        total_messages = conn.execute(
            "SELECT COUNT(*) FROM interactions"
        ).fetchone()[0]
        oldest = conn.execute(
            "SELECT MIN(timestamp_utc) FROM interactions"
        ).fetchone()[0]
        newest = conn.execute(
            "SELECT MAX(timestamp_utc) FROM interactions"
        ).fetchone()[0]

    return (
        f"Memory stats:\n"
        f"  Sessions   : {total_sessions}\n"
        f"  Messages   : {total_messages}\n"
        f"  Oldest     : {(oldest or '?')[:16]}\n"
        f"  Newest     : {(newest or '?')[:16]}\n"
    )
