"""Long-term semantic memory using ChromaDB + sentence-transformers.

Stores text + metadata as embeddings and enables meaning-based retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import os
import uuid


def _maybe_load_env() -> None:
    """Load env from `.env` and `config/.env` if python-dotenv is available."""
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    load_dotenv()
    load_dotenv(os.path.join("config", ".env"))


_maybe_load_env()


DEFAULT_COLLECTION = os.getenv("CHROMA_COLLECTION", "aria_memory")
DEFAULT_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
DEFAULT_CHROMA_DIR = os.getenv(
    "CHROMA_DIR",
    os.path.join(os.path.dirname(__file__), "chroma_db"),
)


@dataclass(frozen=True)
class MemoryMatch:
    id: str
    text: str
    metadata: Dict[str, Any]
    distance: float
    score: float


_CLIENT = None
_COLLECTION = None
_MODEL = None


def _normalize_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    meta: Dict[str, Any] = dict(metadata or {})
    meta.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    normalized: Dict[str, Any] = {}
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            normalized[str(key)] = value
        else:
            normalized[str(key)] = str(value)
    return normalized


def _get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL

    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception as exc:
        raise ImportError(
            "sentence-transformers is required. Install: pip install sentence-transformers"
        ) from exc

    _MODEL = SentenceTransformer(DEFAULT_EMBEDDING_MODEL)
    return _MODEL


def _get_collection():
    global _CLIENT, _COLLECTION
    if _COLLECTION is not None:
        return _COLLECTION

    try:
        import chromadb  # type: ignore
    except Exception as exc:
        raise ImportError("chromadb is required. Install: pip install chromadb") from exc

    os.makedirs(DEFAULT_CHROMA_DIR, exist_ok=True)
    _CLIENT = chromadb.PersistentClient(path=DEFAULT_CHROMA_DIR)

    # Use cosine distance for more intuitive semantic similarity.
    _COLLECTION = _CLIENT.get_or_create_collection(
        name=DEFAULT_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    return _COLLECTION


def store_memory(text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Store a memory string with metadata and return its id."""
    if text is None or not str(text).strip():
        raise ValueError("text is required")

    collection = _get_collection()
    model = _get_model()

    memory_id = str(uuid.uuid4())
    meta = _normalize_metadata(metadata)

    embedding = model.encode([str(text).strip()])[0]
    collection.add(
        ids=[memory_id],
        documents=[str(text).strip()],
        metadatas=[meta],
        embeddings=[embedding.tolist()],
    )

    return memory_id


def search_memory(query: str, top_k: int = 5) -> List[MemoryMatch]:
    """Search semantic memory and return top matches."""
    if query is None or not str(query).strip():
        raise ValueError("query is required")
    k = max(1, int(top_k))

    collection = _get_collection()
    model = _get_model()

    embedding = model.encode([str(query).strip()])[0]
    result = collection.query(
        query_embeddings=[embedding.tolist()],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )

    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]

    matches: List[MemoryMatch] = []
    for memory_id, doc, meta, dist in zip(ids, docs, metas, dists):
        distance = float(dist) if dist is not None else 0.0
        score = 1.0 - distance
        matches.append(
            MemoryMatch(
                id=str(memory_id),
                text=str(doc or ""),
                metadata=dict(meta or {}),
                distance=distance,
                score=score,
            )
        )

    return matches
