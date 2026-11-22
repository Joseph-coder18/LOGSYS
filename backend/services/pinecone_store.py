"""
LogMind – Pinecone Vector Store (v3+ API, compatible with pinecone >= 3.x)
Handles index creation, batch upsert, and semantic search.
Initializes lazily so the app starts cleanly even without API keys.
"""
from __future__ import annotations

import logging
from threading import Lock
from typing import Any, Optional

from models import BaseChunk
from config import PINECONE_API_KEY, PINECONE_INDEX_NAME, EMBEDDING_DIM

logger = logging.getLogger("logmind.pinecone_store")

# ── Module state ──────────────────────────────────────────────────────────────
_connection_lock = Lock()
_index = None          # Pinecone Index object
_init_failure: dict = {}
_initialized = False   # True once we've successfully connected


def _get_index():
    with _connection_lock:
        return _initialize_index()


def _initialize_index():
    """Lazy-init: connect to Pinecone and return the Index object."""
    global _index, _initialized, _init_failure

    if _initialized:
        return _index

    if not PINECONE_API_KEY:
        logger.warning("PINECONE_API_KEY not set — vector store disabled.")
        _initialized = True
        return None

    try:
        from pinecone import Pinecone, ServerlessSpec

        pc = Pinecone(api_key=PINECONE_API_KEY, timeout=10.0)

        # Create index if it doesn't exist
        existing = [idx.name for idx in pc.list_indexes()]
        if PINECONE_INDEX_NAME not in existing:
            logger.info("Creating Pinecone index '%s' (dim=%d) …", PINECONE_INDEX_NAME, EMBEDDING_DIM)
            pc.create_index(
                name=PINECONE_INDEX_NAME,
                dimension=EMBEDDING_DIM,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
                timeout=30,
            )

        description = pc.describe_index(PINECONE_INDEX_NAME)
        actual = getattr(description, "dimension", None)
        if actual is None and hasattr(description, "schema") and hasattr(description.schema, "fields"):
            # Pinecone SDK >= 10.0 schema lookup fallback
            for field in getattr(description.schema.fields, "values", lambda: [])():
                if hasattr(field, "dimension"):
                    actual = field.dimension
                    break
        if actual is not None and actual != EMBEDDING_DIM:
            _init_failure = {"status": "dimension_mismatch", "expected_dimension": EMBEDDING_DIM, "actual_dimension": actual,
                             "message": f"Index dimension is {actual}; embeddings require {EMBEDDING_DIM}. Choose a compatible index. Existing data was not changed."}
            return None
        _index = pc.Index(PINECONE_INDEX_NAME)
        _init_failure = {}
        _initialized = True
        logger.info("Pinecone index '%s' ready.", PINECONE_INDEX_NAME)

    except Exception as exc:
        _init_failure = {"status": "error", "message": "Could not connect to the configured vector index.", "error_type": type(exc).__name__}
        logger.error("Pinecone init failed (%s)", type(exc).__name__)

    return _index


# ── Public API ────────────────────────────────────────────────────────────────

def upsert_chunks(
    chunks: list[BaseChunk],
    embeddings: list[list[float]],
    namespace: str | None = None,
) -> dict[str, int]:
    """
    Upsert BaseChunk objects with their embeddings.

    Args:
        chunks:     List of BaseChunk objects.
        embeddings: Corresponding embedding vectors (same length as chunks).
        namespace:  Pinecone namespace (session_id for session isolation).

    Returns:
        {"upserted": N}
    """
    index = _get_index()
    if index is None:
        logger.warning("Pinecone not available — skipping upsert of %d chunks.", len(chunks))
        return {"upserted": 0, **_unavailable_status()}

    if len(chunks) != len(embeddings):
        raise ValueError(f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) must match")

    vectors = []
    for chunk, emb in zip(chunks, embeddings):
        # Pinecone metadata values must be str, int, float, or bool (not lists)
        meta: dict[str, Any] = {}
        for k, v in chunk.metadata.items():
            if isinstance(v, (str, int, float, bool)):
                meta[k] = v
            elif isinstance(v, list):
                # Flatten lists to comma-separated string
                meta[k] = ", ".join(str(x) for x in v)
            else:
                meta[k] = str(v)
        meta["text_snippet"] = chunk.text[:500]  # store snippet for retrieval

        vectors.append({
            "id":     chunk.chunk_id,
            "values": emb,
            "metadata": meta,
        })

    # Upsert in batches of 100 (Pinecone limit)
    BATCH = 100
    total_upserted = 0
    upsert_kwargs: dict[str, Any] = {}
    if namespace:
        upsert_kwargs["namespace"] = namespace
    for i in range(0, len(vectors), BATCH):
        batch = vectors[i : i + BATCH]
        try:
            response = index.upsert(vectors=batch, **upsert_kwargs)
            acknowledged = getattr(response, "upserted_count", None)
            if acknowledged is None and isinstance(response, dict):
                acknowledged = response.get("upserted_count")
            if not isinstance(acknowledged, int) or isinstance(acknowledged, bool) or not 0 <= acknowledged <= len(batch):
                raise ValueError("Vector index returned no valid acknowledgement.")
            total_upserted += acknowledged
            logger.debug(
                "Upserted batch %d/%d (%d vectors) namespace=%s",
                i // BATCH + 1, (len(vectors) - 1) // BATCH + 1,
                len(batch), namespace or "global",
            )
        except Exception as exc:
            logger.error("Upsert batch failed: %s", exc)

    logger.info("Upserted %d vectors (namespace=%s).", total_upserted, namespace or "global")
    return {"upserted": total_upserted, "status": "ready" if total_upserted == len(chunks) else "partial", "message": "Some vector batches failed. Retry is safe because vector IDs are stable." if total_upserted != len(chunks) else "Indexing complete."}


def _unavailable_status() -> dict:
    if not PINECONE_API_KEY:
        return {"status": "disabled", "message": "Vector retrieval is disabled because credentials are missing."}
    return _init_failure or {"status": "error", "message": "Vector index is unavailable."}


def query_with_status(embedding: list[float], top_k: int = 5, filter_dict: Optional[dict] = None,
                      namespace: str | None = None) -> dict:
    """A successful empty search is distinct from a disabled or failed search."""
    index = _get_index()
    if index is None:
        return {"matches": [], **_unavailable_status()}
    if len(embedding) != EMBEDDING_DIM:
        return {"matches": [], "status": "dimension_mismatch", "expected_dimension": EMBEDDING_DIM,
                "actual_dimension": len(embedding), "message": "Query embedding has an incompatible dimension."}
    try:
        kwargs = {"vector": embedding, "top_k": top_k, "include_metadata": True}
        if filter_dict:
            kwargs["filter"] = filter_dict
        if namespace:
            kwargs["namespace"] = namespace
        response = index.query(**kwargs)
        matches = [{"id": match.id, "score": round(match.score, 4), "metadata": match.metadata or {},
                    "snippet": (match.metadata or {}).get("text_snippet", "")} for match in response.matches]
        return {"matches": matches, "status": "matched" if matches else "no_matches"}
    except Exception as exc:
        logger.warning("Vector retrieval failed (%s).", type(exc).__name__)
        return {"matches": [], "status": "error", "message": "Vector query failed. Local evidence remains available.", "error_type": type(exc).__name__}


def query(embedding: list[float], top_k: int = 5, filter_dict: Optional[dict] = None,
          namespace: str | None = None) -> list[dict[str, Any]]:
    """Compatibility interface; diagnostics-aware callers use query_with_status."""
    return query_with_status(embedding, top_k, filter_dict, namespace)["matches"]


def get_stats() -> dict[str, Any]:
    """Return index stats (vector count, etc.)."""
    index = _get_index()
    if index is None:
        return _unavailable_status()
    try:
        stats = index.describe_index_stats()
        return {
            "status": "ready",
            "total_vector_count": stats.total_vector_count,
            "dimension":          stats.dimension,
            "index_name":         PINECONE_INDEX_NAME,
        }
    except Exception as exc:
        return {"status": "error", "error_type": type(exc).__name__}
