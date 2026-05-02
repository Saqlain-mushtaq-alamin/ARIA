"""Exact conversation history storage using SQLite.

Vector DB (Chroma) = fuzzy/semantic memory
SQLite = exact truth (what was said, when)

This module logs every user/assistant interaction with a timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import json
import os
import sqlite3


def _maybe_load_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    load_dotenv()
    load_dotenv(os.path.join("config", ".env"))


_maybe_load_env()


DEFAULT_DB_PATH = os.getenv(
    "CONVERSATION_DB_PATH",
    os.path.join(os.path.dirname(__file__), "conversation.db"),
)


@dataclass(frozen=True)
class Interaction:
    id: int
    timestamp: str
    user_text: str
    assistant_text: str
    metadata: Dict[str, Any]


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc    TEXT NOT NULL,
    user_text        TEXT NOT NULL,
    assistant_text   TEXT NOT NULL,
    metadata_json    TEXT NOT NULL DEFAULT '{}' 
);

CREATE INDEX IF NOT EXISTS idx_interactions_timestamp
    ON interactions(timestamp_utc);
"""


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _connect(db_path: str) -> sqlite3.Connection:
    _ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialize the SQLite schema if needed."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)


def log_interaction(
    user_text: str,
    assistant_text: str,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> int:
    """Log a user/assistant interaction and return the inserted row id."""
    if user_text is None or not str(user_text).strip():
        raise ValueError("user_text is required")
    if assistant_text is None:
        assistant_text = ""

    init_db(db_path=db_path)

    timestamp = datetime.now(timezone.utc).isoformat()
    meta: Dict[str, Any] = dict(metadata or {})
    meta.setdefault("timestamp", timestamp)

    # SQLite JSON1 is not guaranteed, so store as plain JSON text.
    metadata_json = json.dumps(meta, ensure_ascii=False)

    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO interactions(timestamp_utc, user_text, assistant_text, metadata_json)
            VALUES (?, ?, ?, ?)
            """,
            (timestamp, str(user_text).strip(), str(assistant_text), metadata_json),
        )
        return int(cur.lastrowid)


def get_recent_interactions(
    limit: int = 20,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Interaction]:
    """Return the most recent interactions."""
    init_db(db_path=db_path)
    n = max(1, int(limit))

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, timestamp_utc, user_text, assistant_text, metadata_json
            FROM interactions
            ORDER BY id DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()

    interactions: List[Interaction] = []
    for row_id, ts, user_text, assistant_text, metadata_json in rows:
        try:
            meta = json.loads(metadata_json or "{}")
        except Exception:
            meta = {}
        interactions.append(
            Interaction(
                id=int(row_id),
                timestamp=str(ts),
                user_text=str(user_text),
                assistant_text=str(assistant_text),
                metadata=dict(meta),
            )
        )

    return interactions


def search_interactions(
    query: str,
    limit: int = 20,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Interaction]:
    """Search exact text in user/assistant messages using LIKE."""
    if query is None or not str(query).strip():
        raise ValueError("query is required")

    init_db(db_path=db_path)
    n = max(1, int(limit))
    q = f"%{str(query).strip()}%"

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, timestamp_utc, user_text, assistant_text, metadata_json
            FROM interactions
            WHERE user_text LIKE ? OR assistant_text LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (q, q, n),
        ).fetchall()

    results: List[Interaction] = []
    for row_id, ts, user_text, assistant_text, metadata_json in rows:
        try:
            meta = json.loads(metadata_json or "{}")
        except Exception:
            meta = {}
        results.append(
            Interaction(
                id=int(row_id),
                timestamp=str(ts),
                user_text=str(user_text),
                assistant_text=str(assistant_text),
                metadata=dict(meta),
            )
        )

    return results
