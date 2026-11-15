"""Question-driven retrieval of citable observations within one incident."""
from math import isfinite
from pathlib import Path
import re

import config
from models import Observation
from services.observations import search_observations

_STOP_WORDS = {
    "a", "about", "an", "and", "are", "can", "did", "do", "does", "for", "from",
    "cause", "explain", "happened", "has", "incident", "root", "how", "in", "is", "it", "me", "of", "on", "or", "show",
    "tell", "the", "this", "to", "was", "were", "what", "when", "where", "which",
    "why", "with",
}


def query_terms(question: str) -> list[str]:
    tokens = re.findall(r"[\w:-]+", question.lower())
    return list(dict.fromkeys(token for token in tokens if len(token) >= 2 and token not in _STOP_WORDS))[:12]


def semantic_retrieval_enabled() -> bool:
    return bool(config.GEMINI_API_KEY and config.PINECONE_API_KEY)


def retrieve_semantic(session_dir: Path, question: str, terms: list[str]) -> list[Observation]:
    """Resolve Pinecone hits back to exact records. Pinecone snippets never serve as citations."""
    from services.embedder import embed_query
    from services.pinecone_store import query

    vector = embed_query(question)
    if len(vector) != config.EMBEDDING_DIM or not any(vector) or not all(isfinite(value) for value in vector):
        return []

    hits = query(vector, top_k=5, namespace=session_dir.name)
    for_hit = {}
    for hit in hits:
        meta = hit.get("metadata") or {}
        digest, filename = meta.get("source_digest"), meta.get("source_file")
        if not isinstance(digest, str) or not isinstance(filename, str):
            continue
        start, end = meta.get("start_line", 1), meta.get("end_line", 2**31 - 1)
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        for observation in search_observations(session_dir, terms, 2, (digest, filename, start, end)):
            for_hit.setdefault(observation.id, observation)
        if len(for_hit) >= 5:
            break
    return list(for_hit.values())[:5]
