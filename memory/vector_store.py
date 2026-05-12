"""ARIA — Semantic Memory (ChromaDB + sentence-transformers).

This module gives ARIA long-term memory that works like a human brain:
it remembers the *meaning* of past conversations, not just exact words.

Key capabilities
────────────────
  store_memory()         — Embed and store any text with rich metadata
  search_memory()        — Find semantically similar past memories
  store_interaction()    — Auto-embed both sides of a conversation turn
  get_context_for()      — One-call: get relevant past memories for an LLM prompt
  detect_continuation()  — Detect if a new prompt continues a past topic
  get_topic_memories()   — Retrieve all memories about a specific topic
  forget_memory()        — Remove a specific memory by ID
  forget_session()       — Remove all memories from a session
  get_memory_summary()   — Stats and overview of stored memories

Session awareness
──────────────────
  Every stored memory carries its session_id. When searching, you can:
    - Search only the current session (recent context)
    - Search all sessions (cross-session recall)
  This lets the agent say "Last Tuesday in your Exam Planning chat,
  you mentioned needing 3 chapters by Friday."

Continuity detection
─────────────────────
  detect_continuation(new_prompt) searches recent memories and scores
  how likely the new prompt is continuing a previous topic. If score > 0.75,
  the agent is told to load that session's context.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast


MetadataValue = str | int | float | bool
MetadataDict = Dict[str, MetadataValue]


# ─────────────────────────────────────────────────────────────────────────────
# Environment setup
# ─────────────────────────────────────────────────────────────────────────────

def _maybe_load_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
        load_dotenv(os.path.join("config", ".env"))
    except Exception:
        pass

_maybe_load_env()

DEFAULT_COLLECTION     = os.getenv("CHROMA_COLLECTION", "aria_memory")
DEFAULT_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
DEFAULT_CHROMA_DIR     = os.getenv(
    "CHROMA_DIR",
    os.path.join(os.path.dirname(__file__), "chroma_db"),
)

# Minimum cosine similarity score to include in results (0.0 – 1.0)
DEFAULT_SCORE_THRESHOLD = 0.30

# Similarity score above which we flag as a likely continuation
CONTINUATION_THRESHOLD = 0.72


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MemoryMatch:
    """A single semantic memory match returned by search."""
    id: str
    text: str
    metadata: Dict[str, Any]
    distance: float
    score: float             # 1.0 - distance (higher = more similar)

    @property
    def session_id(self) -> str:
        return str(self.metadata.get("session_id", ""))

    @property
    def role(self) -> str:
        return str(self.metadata.get("role", ""))

    @property
    def timestamp(self) -> str:
        return str(self.metadata.get("timestamp", ""))

    @property
    def topic(self) -> str:
        return str(self.metadata.get("topic", ""))

    def as_context_line(self) -> str:
        """Format this memory as a single context line for LLM injection."""
        ts = self.timestamp[:10]
        role = self.role or "unknown"
        session = self.metadata.get("session_name", self.session_id[:8])
        score_pct = int(self.score * 100)
        return f"[{ts} | {session} | {role} | relevance {score_pct}%] {self.text[:200]}"


@dataclass
class ContinuationSignal:
    """Result of detect_continuation() — tells agent if a topic continues."""
    is_continuation: bool
    score: float
    best_match: Optional[MemoryMatch]
    suggested_session_id: Optional[str]
    context_hint: str        # Human-readable hint for the agent's narration


# ─────────────────────────────────────────────────────────────────────────────
# Lazy singletons
# ─────────────────────────────────────────────────────────────────────────────

_CLIENT = None
_COLLECTION = None
_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers is required: pip install sentence-transformers"
        ) from exc
    _MODEL = SentenceTransformer(DEFAULT_EMBEDDING_MODEL)
    return _MODEL


def _get_collection():
    global _CLIENT, _COLLECTION
    if _COLLECTION is not None:
        return _COLLECTION
    try:
        import chromadb  # type: ignore
    except ImportError as exc:
        raise ImportError("chromadb is required: pip install chromadb") from exc
    os.makedirs(DEFAULT_CHROMA_DIR, exist_ok=True)
    _CLIENT = chromadb.PersistentClient(path=DEFAULT_CHROMA_DIR)
    _COLLECTION = _CLIENT.get_or_create_collection(
        name=DEFAULT_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    return _COLLECTION


def _embed(texts: List[str]) -> List[Sequence[float]]:
    """Embed a list of texts and return list of float vectors."""
    model = _get_model()
    embeddings = model.encode(texts, show_progress_bar=False)
    return [cast(Sequence[float], e.tolist()) for e in embeddings]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Metadata normalisation
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_metadata(metadata: Optional[Dict[str, Any]]) -> MetadataDict:
    """ChromaDB requires all metadata values to be str/int/float/bool — no None."""
    meta: Dict[str, Any] = dict(metadata or {})
    meta.setdefault("timestamp", _now_iso())
    normalized: MetadataDict = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            normalized[str(k)] = v
        else:
            normalized[str(k)] = str(v)
    return normalized


def _to_scalar_str(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Core store / search
# ─────────────────────────────────────────────────────────────────────────────

def store_memory(
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    memory_id: Optional[str] = None,
) -> str:
    """Embed and store a memory string.

    Args:
        text:      The content to remember.
        metadata:  Dict of extra info: session_id, role, topic, source, etc.
        memory_id: Optional custom ID. Auto-generated if not provided.

    Returns:
        The memory ID (UUID string).
    """
    if not str(text or "").strip():
        raise ValueError("text is required")

    collection = _get_collection()
    mid = memory_id or str(uuid.uuid4())
    meta = _normalize_metadata(metadata)
    [embedding] = _embed([str(text).strip()])

    collection.add(
        ids=[mid],
        documents=[str(text).strip()],
        metadatas=[meta],
        embeddings=[embedding],
    )
    return mid


def store_memories_batch(
    items: List[Tuple[str, Optional[Dict[str, Any]]]],
) -> List[str]:
    """Store multiple memories in one batch call (much faster than individual calls).

    Args:
        items: List of (text, metadata) tuples.

    Returns:
        List of memory IDs.
    """
    if not items:
        return []

    texts = [str(t[0]).strip() for t in items if str(t[0] or "").strip()]
    if not texts:
        return []

    collection = _get_collection()
    ids = [str(uuid.uuid4()) for _ in texts]
    metas = [_normalize_metadata(t[1]) for t in items if str(t[0] or "").strip()]
    embeddings = _embed(texts)

    collection.add(
        ids=ids,
        documents=texts,
        metadatas=cast(Any, metas),
        embeddings=cast(Any, embeddings),
    )
    return ids


def search_memory(
    query: str,
    top_k: int = 5,
    session_id: Optional[str] = None,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    role_filter: Optional[str] = None,
    topic_filter: Optional[str] = None,
) -> List[MemoryMatch]:
    """Semantic search across stored memories.

    Args:
        query:           What to search for (natural language).
        top_k:           Max results to return.
        session_id:      If set, restrict to memories from this session only.
        score_threshold: Minimum similarity score (0–1) to include.
        role_filter:     Filter by role ('user', 'assistant', 'system').
        topic_filter:    Filter by topic tag.

    Returns:
        List of MemoryMatch objects, highest score first.
    """
    if not str(query or "").strip():
        raise ValueError("query is required")

    collection = _get_collection()
    k = max(1, int(top_k))

    [embedding] = _embed([str(query).strip()])

    # Build ChromaDB where clause
    where: Optional[Dict] = None
    conditions = []
    if session_id:
        conditions.append({"session_id": {"$eq": session_id}})
    if role_filter:
        conditions.append({"role": {"$eq": role_filter}})
    if topic_filter:
        conditions.append({"topic": {"$eq": topic_filter}})

    if len(conditions) == 1:
        where = conditions[0]
    elif len(conditions) > 1:
        where = {"$and": conditions}

    query_kwargs: Dict[str, Any] = {
        "query_embeddings": [embedding],
        "n_results": min(k * 2, 50),   # over-fetch then filter by score
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        query_kwargs["where"] = where

    result = collection.query(**query_kwargs)

    ids   = (result.get("ids")        or [[]])[0]
    docs  = (result.get("documents")  or [[]])[0]
    metas = (result.get("metadatas")  or [[]])[0]
    dists = (result.get("distances")  or [[]])[0]

    matches: List[MemoryMatch] = []
    for mid, doc, meta, dist in zip(ids, docs, metas, dists):
        dist_f = float(dist) if dist is not None else 1.0
        score  = max(0.0, 1.0 - dist_f)
        if score < score_threshold:
            continue
        matches.append(MemoryMatch(
            id=str(mid),
            text=str(doc or ""),
            metadata=dict(meta or {}),
            distance=dist_f,
            score=score,
        ))

    # Sort by score descending, trim to top_k
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:k]


# ─────────────────────────────────────────────────────────────────────────────
# High-level helpers for the agent
# ─────────────────────────────────────────────────────────────────────────────

def store_interaction(
    user_text: str,
    assistant_text: str,
    session_id: str = "default",
    session_name: str = "",
    topic: str = "",
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Embed and store both sides of a conversation turn.

    Call this right after log_interaction() in agent.py so every turn
    is searchable semantically.

    Args:
        user_text:       What the user said.
        assistant_text:  What ARIA replied.
        session_id:      The current session ID.
        session_name:    Human-readable session name (stored in metadata).
        topic:           Optional topic tag (e.g. 'exam', 'gym', 'coding').
        extra_metadata:  Any additional metadata fields.

    Returns:
        Tuple of (user_memory_id, assistant_memory_id).
    """
    now = _now_iso()
    base_meta = dict(extra_metadata or {})
    base_meta.update({
        "session_id":   session_id,
        "session_name": session_name or session_id[:8],
        "timestamp":    now,
        "topic":        topic or "",
    })

    user_id = store_memory(
        text=str(user_text).strip(),
        metadata={**base_meta, "role": "user"},
    )
    asst_id = store_memory(
        text=str(assistant_text).strip(),
        metadata={**base_meta, "role": "assistant"},
    )
    return user_id, asst_id


def get_context_for(
    prompt: str,
    top_k: int = 5,
    current_session_id: Optional[str] = None,
    include_cross_session: bool = True,
    format_as_string: bool = False,
) -> List[MemoryMatch] | str:
    """Get the most relevant past memories for a new prompt.

    This is the primary function called by agent.py before every LLM call.
    It searches both the current session (recent context) and all past
    sessions (long-term memory) and returns the most relevant memories.

    Args:
        prompt:                 The new user input.
        top_k:                  Total memories to return.
        current_session_id:     Current session (if set, recent results weighted higher).
        include_cross_session:  Also search all other sessions.
        format_as_string:       If True, return formatted string instead of list.

    Returns:
        List of MemoryMatch objects, or formatted string if format_as_string=True.
    """
    results: List[MemoryMatch] = []

    # Search current session first (higher relevance for in-session context)
    if current_session_id:
        session_results = search_memory(
            query=prompt,
            top_k=top_k,
            session_id=current_session_id,
            score_threshold=0.25,
        )
        results.extend(session_results)

    # Cross-session search for long-term memory
    if include_cross_session:
        cross_results = search_memory(
            query=prompt,
            top_k=top_k,
            session_id=None,       # search all
            score_threshold=0.45,  # higher threshold for cross-session
        )
        # Merge: avoid duplicates, keep highest score
        seen_ids = {m.id for m in results}
        for m in cross_results:
            if m.id not in seen_ids:
                results.append(m)
                seen_ids.add(m.id)

    # Sort by score, take top_k
    results.sort(key=lambda m: m.score, reverse=True)
    results = results[:top_k]

    if not format_as_string:
        return results

    if not results:
        return ""

    lines = ["Relevant past context:"]
    for m in results:
        lines.append("  " + m.as_context_line())
    return "\n".join(lines)


def detect_continuation(
    new_prompt: str,
    recent_turns: int = 3,
    current_session_id: Optional[str] = None,
) -> ContinuationSignal:
    """Detect if a new prompt is continuing a topic from a previous session.

    This enables ARIA to say:
    "This looks related to your 'Exam Planning' chat from Tuesday.
    Should I continue from there?"

    Args:
        new_prompt:          The user's new input.
        recent_turns:        How many recent messages to check within current session.
        current_session_id:  Exclude from cross-session search (already active).

    Returns:
        ContinuationSignal with is_continuation flag and details.
    """
    if not str(new_prompt or "").strip():
        return ContinuationSignal(
            is_continuation=False, score=0.0,
            best_match=None, suggested_session_id=None, context_hint=""
        )

    # Search cross-session (exclude current session)
    all_matches = search_memory(
        query=new_prompt,
        top_k=5,
        session_id=None,
        score_threshold=CONTINUATION_THRESHOLD,
    )

    # Filter out current session results
    if current_session_id:
        all_matches = [m for m in all_matches if m.session_id != current_session_id]

    if not all_matches:
        return ContinuationSignal(
            is_continuation=False, score=0.0,
            best_match=None, suggested_session_id=None, context_hint=""
        )

    best = all_matches[0]
    session_name = best.metadata.get("session_name", "a previous chat")
    ts = best.timestamp[:10]
    score_pct = int(best.score * 100)

    hint = (
        f"This seems related to '{session_name}' ({ts}, {score_pct}% match).\n"
        f"  Previous context: \"{best.text[:100]}...\"\n"
        f"  Should I load that session's context?"
    )

    return ContinuationSignal(
        is_continuation=True,
        score=best.score,
        best_match=best,
        suggested_session_id=best.session_id,
        context_hint=hint,
    )


def get_topic_memories(
    topic: str,
    top_k: int = 10,
    session_id: Optional[str] = None,
) -> List[MemoryMatch]:
    """Retrieve all memories tagged with a specific topic.

    Useful for: "What have we discussed about my exams?" or
    "Show me everything about the gym schedule."

    Args:
        topic:      Topic string to filter by.
        top_k:      Max results.
        session_id: Optional session restriction.

    Returns:
        List of MemoryMatch objects.
    """
    return search_memory(
        query=topic,
        top_k=top_k,
        session_id=session_id,
        topic_filter=topic,
        score_threshold=0.0,   # topic_filter is already exact — don't score-filter
    )


def forget_memory(memory_id: str) -> str:
    """Remove a specific memory by ID.

    Args:
        memory_id: The UUID of the memory to delete.

    Returns:
        Confirmation string.
    """
    collection = _get_collection()
    try:
        collection.delete(ids=[memory_id])
        return f"Memory {memory_id[:8]}... forgotten."
    except Exception as exc:
        return f"Could not forget memory: {exc}"


def forget_session(session_id: str) -> str:
    """Remove all memories from a specific session.

    Args:
        session_id: The session whose memories should be erased.

    Returns:
        Confirmation string with count of removed memories.
    """
    collection = _get_collection()
    try:
        results = collection.get(where={"session_id": {"$eq": session_id}})
        ids = results.get("ids") or []
        if not ids:
            return f"No memories found for session {session_id[:8]}."
        collection.delete(ids=ids)
        return f"Removed {len(ids)} memories from session {session_id[:8]}."
    except Exception as exc:
        return f"Could not forget session memories: {exc}"


def update_memory(
    memory_id: str,
    new_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Update the text of an existing memory (re-embeds it).

    Args:
        memory_id: ID of the memory to update.
        new_text:  New content.
        metadata:  Updated metadata (merged with existing).

    Returns:
        Confirmation string.
    """
    collection = _get_collection()

    # Fetch existing metadata
    try:
        existing = collection.get(ids=[memory_id], include=["metadatas"])
        old_meta = (existing.get("metadatas") or [{}])[0]
    except Exception:
        old_meta = {}

    merged_meta = {**old_meta, **(metadata or {})}
    merged_meta["updated_at"] = _now_iso()

    [embedding] = _embed([new_text.strip()])

    collection.update(
        ids=[memory_id],
        documents=[new_text.strip()],
        metadatas=[_normalize_metadata(merged_meta)],
        embeddings=[embedding],
    )
    return f"Memory {memory_id[:8]}... updated."


def get_memory_summary() -> str:
    """Return an overview of the memory store — count, sessions, date range."""
    collection = _get_collection()
    try:
        count = collection.count()
        if count == 0:
            return "Memory store is empty — no memories stored yet."

        # Sample recent memories to find date range and session list
        sample = collection.get(
            limit=min(count, 1000),
            include=["metadatas"],
        )
        metas = sample.get("metadatas") or []

        timestamps = [t for t in (_to_scalar_str(m.get("timestamp")) for m in metas) if t]
        sessions = list({s for s in (_to_scalar_str(m.get("session_id")) for m in metas) if s})
        topics = list({t for t in (_to_scalar_str(m.get("topic")) for m in metas) if t})

        oldest = min(timestamps)[:10] if timestamps else "?"
        newest = max(timestamps)[:10] if timestamps else "?"

        return (
            f"🧠 Semantic Memory Summary:\n"
            f"   Total memories   : {count:,}\n"
            f"   Unique sessions  : {len(sessions)}\n"
            f"   Topics tagged    : {len([t for t in topics if t])}\n"
            f"   Oldest memory    : {oldest}\n"
            f"   Newest memory    : {newest}\n"
            f"   Model            : {DEFAULT_EMBEDDING_MODEL}\n"
            f"   Storage path     : {DEFAULT_CHROMA_DIR}"
        )
    except Exception as exc:
        return f"Could not read memory summary: {exc}"


def search_all_sessions(
    query: str,
    top_k: int = 8,
) -> str:
    """Search across ALL sessions and return a formatted result string.

    Designed for user-facing queries like:
    "What did I ask about machine learning?"
    "When did we discuss the gym schedule?"

    Args:
        query:  The search question.
        top_k:  Max results.

    Returns:
        Formatted string with results grouped by session.
    """
    matches = search_memory(query, top_k=top_k, score_threshold=0.25)
    if not matches:
        return f"No memories found related to: '{query}'"

    lines = [f"🔍 Memory search: \"{query}\" — {len(matches)} result(s):"]
    lines.append("")

    # Group by session
    by_session: Dict[str, List[MemoryMatch]] = {}
    for m in matches:
        sid = m.session_id or "unknown"
        by_session.setdefault(sid, []).append(m)

    for sid, session_matches in by_session.items():
        session_name = session_matches[0].metadata.get("session_name", sid[:8])
        lines.append(f"  💬 {session_name}:")
        for m in session_matches:
            ts = m.timestamp[:10]
            role = m.role or "?"
            score_pct = int(m.score * 100)
            preview = m.text[:120].replace("\n", " ")
            lines.append(f"     [{ts} | {role} | {score_pct}%] {preview}")
        lines.append("")

    return "\n".join(lines).rstrip()
