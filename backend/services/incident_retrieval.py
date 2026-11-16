"""Question-driven retrieval of citable observations within one incident."""
from math import isfinite
from pathlib import Path
import re

import config
from models import Observation
from services.incident_context import load_dependencies
from services.observations import search_observations

QUESTION_RECORD_LIMIT = 5
_DEPENDENCIES = {"dependency", "dependencies", "depends"}
_CALLERS = {"caller", "callers", "downstream", "dependent", "dependents"}
_STOP_WORDS = {
    "a", "about", "an", "and", "are", "can", "did", "do", "does", "for", "from",
    "cause", "explain", "happened", "has", "incident", "root", "how", "in", "is", "it", "me", "of", "on", "or", "show",
    "tell", "the", "this", "to", "was", "were", "what", "when", "where", "which",
    "why", "with",
}


def asks_about_dependency(terms: set[str]) -> bool:
    return bool(terms & (_DEPENDENCIES | _CALLERS))


def query_terms(question: str) -> list[str]:
    tokens = re.findall(r"[\w:-]+", question.lower())
    return list(dict.fromkeys(token for token in tokens if len(token) >= 2 and token not in _STOP_WORDS))[:12]


def retrieve_question_records(
    session_dir: Path, terms: list[str], *, use_graph: bool = True,
) -> tuple[list[Observation], int, int, list[dict]]:
    """Spend the same record budget with or without directed dependency expansion."""
    if not terms:
        return [], 0, 0, []
    keywords = search_observations(session_dir, terms, QUESTION_RECORD_LIMIT)
    words = set(terms)
    outward = bool(words & _DEPENDENCIES)
    inward = bool(words & _CALLERS)
    if not use_graph or outward == inward:
        return keywords, len(keywords), 0, []

    targets = {}
    links = []
    service_names = set()
    for edge in load_dependencies(session_dir):
        caller, callee = edge["caller"].lower(), edge["callee"].lower()
        service_names.update((caller, callee))
        seed, target = (caller, callee) if outward else (callee, caller)
        if seed in words:
            targets[target] = None
            links.append({**edge, "retrieval_service": target})
    if not targets:
        return keywords, len(keywords), 0, []
    signal_terms = [term for term in terms if term not in _DEPENDENCIES | _CALLERS | service_names]
    neighbors = search_observations(session_dir, signal_terms, 3, services=list(targets))
    records = list({event.id: event for event in [*neighbors, *keywords]}.values())[:QUESTION_RECORD_LIMIT]
    original_ids = {event.id for event in keywords}
    return records, len(keywords), sum(event.id not in original_ids for event in records), links[:8]


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
