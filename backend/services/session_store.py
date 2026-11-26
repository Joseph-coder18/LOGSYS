"""
LogMind – Session Store (Phase 4A)

Each session is a fully isolated analysis context:
  - Its own NetworkX knowledge graph  (graph.gpickle)
  - Its own Pinecone namespace         (namespace = session_id)
  - Its own chat history               (chat_history.json)
  - Metadata persisted in meta.json and the top-level index.json

Storage layout:
  backend/data/sessions/
    index.json                  ← summary list of all sessions
    {session_id}/
      meta.json                 ← id, name, created_at, files, node_count, edge_count
      graph.gpickle             ← isolated NetworkX DiGraph
      chat_history.json         ← [{role, content, timestamp}, ...]
"""
from __future__ import annotations

import json
import logging
import pickle
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from functools import wraps
from services.storage_lock import storage_lock
from typing import Any

import networkx as nx

logger = logging.getLogger("logmind.session_store")

# ── Storage root ──────────────────────────────────────────────────────────────
_SESSIONS_DIR = Path(__file__).parent.parent / "data" / "sessions"
_INDEX_PATH   = _SESSIONS_DIR / "index.json"


def session_graph_lock(session_id: str | None):
    """Serialize graph mutations across threads and workers without locking metadata first."""
    return storage_lock(_SESSIONS_DIR / ".locks" / f"graph-{session_id or 'global'}.lock")


def _state_locked(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with storage_lock(_SESSIONS_DIR / ".locks" / "state.lock"):
            return function(*args, **kwargs)
    return locked


# ── Internal helpers ──────────────────────────────────────────────────────────

def _session_dir(session_id: str) -> Path:
    return _SESSIONS_DIR / session_id


def _meta_path(session_id: str) -> Path:
    return _session_dir(session_id) / "meta.json"


def _graph_path(session_id: str) -> Path:
    return _session_dir(session_id) / "graph.gpickle"


def _chat_path(session_id: str) -> Path:
    return _session_dir(session_id) / "chat_history.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_index() -> list[dict]:
    """Rebuild the disposable summary index from atomically published metadata."""
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for path in _SESSIONS_DIR.glob("*/meta.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            entries.append({key: meta[key] for key in ("id", "name", "created_at", "files", "node_count", "edge_count")})
        except (OSError, ValueError, KeyError):
            logger.warning("Skipping unreadable incident metadata for %s", path.parent.name)
    entries.sort(key=lambda entry: entry["created_at"], reverse=True)
    _save_index(entries)
    return entries


def _save_index(entries: list[dict]) -> None:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _replace_json(_INDEX_PATH, entries)


def _load_meta(session_id: str) -> dict | None:
    p = _meta_path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


@_state_locked
def _save_meta(meta: dict) -> None:
    sid = meta["id"]
    _session_dir(sid).mkdir(parents=True, exist_ok=True)
    _replace_json(_meta_path(sid), meta)
    # Sync into index
    index = _load_index()
    summary = {
        "id":         meta["id"],
        "name":       meta["name"],
        "created_at": meta["created_at"],
        "files":      meta.get("files", []),
        "node_count": meta.get("node_count", 0),
        "edge_count": meta.get("edge_count", 0),
    }
    index = [e for e in index if e["id"] != meta["id"]]
    index.insert(0, summary)
    _save_index(index)


# ── Public CRUD ───────────────────────────────────────────────────────────────

@_state_locked
def create_session(name: str | None = None) -> dict:
    """
    Create a new isolated session.

    Returns:
        Session metadata dict.
    """
    session_id = str(uuid.uuid4())
    now        = _now_iso()
    clean_name = (name or "").strip()
    meta = {
        "id":           session_id,
        "name":         clean_name or "New Incident",
        "custom_named": bool(clean_name),
        "created_at":   now,
        "files":        [],
        "node_count":   0,
        "edge_count":   0,
    }
    _save_meta(meta)
    # Initialise an empty graph on disk
    save_session_graph(session_id, nx.DiGraph())
    # Initialise empty chat history
    _replace_json(_chat_path(session_id), [])
    logger.info("Session created: %s (%s)", session_id, meta["name"])
    return meta


@_state_locked
def get_session(session_id: str) -> dict | None:
    """Return session metadata or None if not found."""
    return _load_meta(session_id)


@_state_locked
def rename_session(session_id: str, new_name: str, *, manual: bool = True) -> dict | None:
    """Rename a session and mark it custom_named when edited manually."""
    meta = _load_meta(session_id)
    if meta is None:
        return None
    cleaned = " ".join(new_name.split()).strip()[:80]
    if not cleaned:
        return meta
    meta["name"] = cleaned
    if manual:
        meta["custom_named"] = True
    _save_meta(meta)
    return meta


@_state_locked
def auto_title_session(session_id: str, candidate: str, *, force_if_auto: bool = False) -> dict | None:
    """Update session title automatically when the user hasn't manually named it."""
    meta = _load_meta(session_id)
    if meta is None or meta.get("custom_named"):
        return meta
    current = meta.get("name", "")
    if not force_if_auto and current not in ("", "New Incident") and not current.startswith("Session "):
        return meta
    cleaned = " ".join(candidate.split()).strip()
    if not cleaned:
        return meta
    if len(cleaned) > 48:
        cleaned = cleaned[:45].rstrip() + "…"
    meta["name"] = cleaned
    _save_meta(meta)
    return meta


@_state_locked
def list_sessions() -> list[dict]:
    """Return all sessions sorted newest-first."""
    return _load_index()


@_state_locked
def update_session(session_id: str, **kwargs: Any) -> dict | None:
    """
    Update mutable fields on a session (name, files, node_count, edge_count).
    Returns updated metadata or None if session not found.
    """
    meta = _load_meta(session_id)
    if meta is None:
        logger.warning("update_session: session %s not found", session_id)
        return None
    for key, val in kwargs.items():
        meta[key] = val
    _save_meta(meta)
    return meta


@_state_locked
def delete_session(session_id: str) -> bool:
    """
    Delete a session and ALL its data (graph, chat history, metadata).
    Returns True on success, False if session not found.
    """
    if _load_meta(session_id) is None:
        return False

    # Remove directory tree
    import shutil
    shutil.rmtree(_session_dir(session_id), ignore_errors=True)

    # Remove from index
    index = [e for e in _load_index() if e["id"] != session_id]
    _save_index(index)
    logger.info("Session deleted: %s", session_id)
    return True


# ── Graph helpers ─────────────────────────────────────────────────────────────

def get_session_graph(session_id: str) -> nx.DiGraph:
    """
    Load and return the NetworkX DiGraph for this session.
    Returns an empty DiGraph if the session has no graph yet.
    """
    p = _graph_path(session_id)
    if p.exists():
        try:
            with open(p, "rb") as fh:
                return pickle.load(fh)
        except Exception as exc:
            logger.warning("Could not load session graph %s: %s", session_id, exc)
    return nx.DiGraph()


@_state_locked
def save_session_graph(session_id: str, graph: nx.DiGraph) -> None:
    """Persist the graph for a session and sync node/edge counts to meta."""
    if _load_meta(session_id) is None:
        return
    _session_dir(session_id).mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=_session_dir(session_id), delete=False) as temporary:
        path = Path(temporary.name)
        try:
            pickle.dump(graph, temporary)
        except Exception:
            path.unlink(missing_ok=True)
            raise
    try:
        path.replace(_graph_path(session_id))
    finally:
        path.unlink(missing_ok=True)
    # Sync counts
    meta = _load_meta(session_id)
    if meta:
        meta["node_count"] = graph.number_of_nodes()
        meta["edge_count"] = sum(len(attrs.get("temporal_relations") or {"rel": None}) for _, _, attrs in graph.edges(data=True))
        _save_meta(meta)
    logger.debug(
        "Session graph saved: %s | %d nodes, %d edges",
        session_id, graph.number_of_nodes(), graph.number_of_edges(),
    )


def get_session_graph_json(session_id: str) -> dict[str, Any]:
    """Return graph serialised to JSON-safe dict for the frontend."""
    from services.graph_store import serialize_graph
    return serialize_graph(get_session_graph(session_id))


# ── Chat history helpers ──────────────────────────────────────────────────────

@_state_locked
def get_session_chat(session_id: str) -> list[dict]:
    """Return the full chat history for a session (list of {role, content, timestamp})."""
    p = _chat_path(session_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


@_state_locked
def append_session_chat(session_id: str, entry: dict, *entries: dict, expected_generation: int | None = None) -> bool:
    """Append a chat exchange atomically, retaining compatibility with single entries."""
    meta = _load_meta(session_id)
    if meta is None or (expected_generation is not None and meta.get("chat_generation", 0) != expected_generation):
        return False
    path = _chat_path(session_id)
    history = get_session_chat(session_id)
    history.extend({"timestamp": _now_iso(), **item} for item in (entry, *entries))
    _replace_json(path, history)
    return True


def get_saved_verification(session_id: str) -> dict | None:
    try:
        return json.loads((_session_dir(session_id) / "verification.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


@_state_locked
def save_verification(session_id: str, diagnosis: dict) -> None:
    try:
        _replace_json(_session_dir(session_id) / "verification.json", diagnosis)
    except FileNotFoundError:
        # The incident may have been deleted while the verifier was running.
        pass


def _replace_json(path: Path, value: Any) -> None:
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary_path = Path(stream.name)
        try:
            json.dump(value, stream, indent=2, default=str)
            stream.close()
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)


@_state_locked
def clear_session_chat(session_id: str) -> None:
    """Wipe history and invalidate replies that started before the reset."""
    meta = _load_meta(session_id)
    if meta is None:
        return
    meta["chat_generation"] = meta.get("chat_generation", 0) + 1
    _save_meta(meta)
    _replace_json(_chat_path(session_id), [])


@_state_locked
def register_session_files(session_id: str, files: list[str]) -> dict | None:
    """Merge file registration under the same lock as metadata and graph counts."""
    meta = _load_meta(session_id)
    if meta is None:
        return None
    meta["files"] = list(dict.fromkeys([*meta.get("files", []), *files]))
    _save_meta(meta)
    return meta
