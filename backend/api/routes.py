"""
LogMind API Router – Phase 4B: session-aware routes.

New endpoints:
  POST   /api/sessions                     create a session
  GET    /api/sessions                     list all sessions
  GET    /api/sessions/{id}                get session metadata
  DELETE /api/sessions/{id}                delete session
  POST   /api/sessions/{id}/ingest         ingest files to session
  POST   /api/sessions/{id}/query          query session
  GET    /api/sessions/{id}/graph          session knowledge graph
  GET    /api/sessions/{id}/chat           session chat history

Backward-compat (global graph, no session):
  POST   /api/ingest                       (unchanged)
  POST   /api/query                        (unchanged)
  GET    /api/graph                        (unchanged)
  GET    /api/health                       (unchanged)
"""
from __future__ import annotations

import json
import logging
from hashlib import sha256
from math import isfinite
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from models import BaseChunk, IngestResult, RCAOutput, Session, CreateSessionRequest, Observation, ObservationFilter, ObservationPage, VerifiedRCA

logger = logging.getLogger("logmind.api")

router = APIRouter()

# ── Request models ────────────────────────────────────────────────────────────
class QueryRequest(BaseModel):
    query: str


# ── File extension sets ───────────────────────────────────────────────────────
_LOG_EXTS     = {".log", ".txt"}
_IMAGE_EXTS   = {".png", ".jpg", ".jpeg", ".webp"}
_METRICS_EXTS = {".csv", ".json"}


def _detect_modality(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in _LOG_EXTS:     return "log"
    if ext in _IMAGE_EXTS:   return "image"
    if ext in _METRICS_EXTS: return "metrics"
    return "unknown"


# ── Internal ingest helpers ──────────────────────────────────────────────────

_MAX_FILES = 20
_MAX_FILE_BYTES = 5 * 1024 * 1024


def _add_file_to_graph(name: str, modality: str, chunks: list[BaseChunk], session_id: str | None) -> None:
    from services.graph_store import add_entity, add_relationship

    add_entity(name, "file", session_id=session_id, modality=modality)
    for key, entity_type in (("services", "service"), ("exceptions", "error"), ("databases", "database")):
        entities = set()
        for chunk in chunks:
            values = chunk.metadata.get(key, [])
            if isinstance(values, str):
                values = values.split(",")
            entities.update(value.strip() for value in values if value.strip())
        for entity in sorted(entities):
            add_entity(entity, entity_type, session_id=session_id)
            source, target = (entity, name) if entity_type == "error" else (name, entity)
            add_relationship(source, target, "observed_in", session_id=session_id)


def _index_chunks(chunks: list[BaseChunk], session_id: str | None, result: IngestResult) -> None:
    from config import EMBEDDING_DIM
    from services import embedder, pinecone_store

    try:
        embeddings = embedder.embed_batch([chunk.text for chunk in chunks])
        pairs = [
            (chunk, vector) for chunk, vector in zip(chunks, embeddings)
            if len(vector) == EMBEDDING_DIM and any(vector) and all(isfinite(value) for value in vector)
        ]
        if pairs:
            valid_chunks, valid_embeddings = zip(*pairs)
            info = pinecone_store.upsert_chunks(list(valid_chunks), list(valid_embeddings), namespace=session_id)
            result.chunks_indexed = info.get("upserted", 0)
    except Exception:
        logger.exception("Vector indexing failed for session %s", session_id)

    if result.chunks_indexed == len(chunks):
        result.indexing_status = "complete"
    else:
        result.indexing_status = "partial" if result.chunks_indexed else "failed"
        result.warnings.append(f"Indexed {result.chunks_indexed} of {len(chunks)} chunks. Retry the upload to index the remaining chunks.")


async def _run_ingest(files: list[UploadFile], session_id: str | None = None) -> IngestResult:
    from config import GEMINI_API_KEY, PINECONE_API_KEY
    from services import image_analyzer, log_parser, metrics_parser
    from services.graph_store import get_stats
    from services.incident_context import parse_changes, save_dependencies
    from services.observations import save_observations
    from services.session_store import _session_dir, get_session, update_session

    if len(files) > _MAX_FILES:
        raise HTTPException(status_code=413, detail=f"Upload at most {_MAX_FILES} files at once.")

    result = IngestResult()
    all_chunks = []
    for upload in files:
        name = upload.filename or ""
        if not name or len(name.encode("utf-8")) > 255 or any(char in name for char in ("/", "\\")) or any(ord(char) < 32 for char in name):
            result.errors.append("Skipped a file with an invalid filename.")
            continue
        modality = _detect_modality(name)
        if modality == "unknown":
            result.errors.append(f"Skipped {name}: unsupported file type.")
            continue
        try:
            content = await upload.read(_MAX_FILE_BYTES + 1)
            if len(content) > _MAX_FILE_BYTES:
                raise ValueError("File exceeds the 5 MiB upload limit.")
            if not content:
                raise ValueError("File is empty.")
            digest = sha256(content).hexdigest()
            if Path(name).suffix.lower() == ".json":
                document = json.loads(content.decode("utf-8-sig"))
                kind = document.get("kind") if isinstance(document, dict) else None
                if kind in ("change_events", "service_dependencies"):
                    if session_id is None:
                        raise ValueError("Upload operational changes and dependencies inside an incident.")
                    directory = _session_dir(session_id)
                    if kind == "change_events":
                        observations = parse_changes(document, name)
                        result.observations_added += save_observations(directory, digest, observations)
                    else:
                        save_dependencies(directory, document, name)
                    result.processed_files.append(name)
                    continue

            # Each file has its own generated directory, including files with the same name.
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / name
                path.write_bytes(content)
                if modality == "log":
                    chunks, observations = log_parser.parse_log(path)
                elif modality == "metrics":
                    chunks, observations = metrics_parser.parse_metrics(path)
                else:
                    chunk = await run_in_threadpool(image_analyzer.analyze_image, path)
                    if not chunk.text.strip() or chunk.text.startswith(("[No API key]", "[Vision unavailable]", "[Vision error]")):
                        raise ValueError("Screenshot analysis was unavailable. Check the vision provider settings and retry.")
                    chunks, observations = [chunk], []
            if not chunks:
                raise ValueError("No usable telemetry found.")
            if session_id and get_session(session_id) is None:
                raise HTTPException(status_code=404, detail="Session no longer exists.")

            if session_id:
                result.observations_added += save_observations(_session_dir(session_id), digest, observations)
            _add_file_to_graph(name, modality, chunks, session_id)
            for index, chunk in enumerate(chunks):
                chunk.chunk_id = sha256(f"{name}/{digest}/{index}".encode("utf-8")).hexdigest()
            all_chunks.extend(chunks)
            result.processed_files.append(name)
        except HTTPException:
            raise
        except (ValueError, OSError) as exc:
            result.errors.append(f"{name}: {exc}")
        except Exception:
            logger.exception("Error processing upload %s", name)
            result.errors.append(f"{name}: could not process the file.")

    result.files_processed = len(result.processed_files)
    stats = get_stats(session_id=session_id)
    result.graph_nodes, result.graph_edges = stats["nodes"], stats["edges"]
    if session_id and (meta := get_session(session_id)):
        update_session(
            session_id,
            files=list(dict.fromkeys(meta.get("files", []) + result.processed_files)),
            node_count=result.graph_nodes,
            edge_count=result.graph_edges,
        )

    if all_chunks:
        if GEMINI_API_KEY and PINECONE_API_KEY:
            await run_in_threadpool(_index_chunks, all_chunks, session_id, result)
        else:
            result.warnings.append("Vector indexing is disabled because embedding or Pinecone credentials are missing.")

    if not result.files_processed:
        result.status = "failed"
    elif result.errors or result.indexing_status in {"partial", "failed"}:
        result.status = "partial"
    return result


# ── Health ────────────────────────────────────────────────────────────────────
@router.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "service": "logmind-backend"}


# ═══════════════════════════════════════════════════════════════════════════════
# Session management endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/sessions", response_model=Session, tags=["Sessions"])
async def create_session(request: CreateSessionRequest = CreateSessionRequest()):
    """Create a new isolated analysis session."""
    from services.session_store import create_session as _create
    meta = _create(name=request.name)
    return Session(**meta)


@router.get("/sessions", tags=["Sessions"])
async def list_sessions():
    """List all sessions (newest first)."""
    from services.session_store import list_sessions as _list
    return {"sessions": _list()}


@router.get("/sessions/{session_id}", response_model=Session, tags=["Sessions"])
async def get_session(session_id: str):
    """Get metadata for a single session."""
    from services.session_store import get_session as _get
    meta = _get(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return Session(**meta)


@router.delete("/sessions/{session_id}", tags=["Sessions"])
async def delete_session(session_id: str):
    """Delete a session and all its data (graph, chat, vectors not deleted from Pinecone)."""
    from services.session_store import delete_session as _delete, get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    _delete(session_id)
    return {"deleted": True, "session_id": session_id}


# ── Session — Ingest ──────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/ingest", response_model=IngestResult, tags=["Sessions"])
async def session_ingest(session_id: str, files: list[UploadFile] = File(...)):
    """
    Ingest files into a specific session's knowledge graph.
    All parsed chunks are stored with namespace=session_id in Pinecone.
    """
    from services.session_store import get_session as _get
    meta = _get(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    return await _run_ingest(files, session_id=session_id)


@router.get("/sessions/{session_id}/observations", response_model=ObservationPage, tags=["Sessions"])
async def session_observations(
    session_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=250),
    category: ObservationFilter = "all",
    service: str | None = Query(None, min_length=1),
):
    """List source-linked telemetry in UTC order, followed by undated observations."""
    from services.observations import load_observations
    from services.session_store import _session_dir, get_session

    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return load_observations(_session_dir(session_id), offset, limit, category, service)


@router.get("/sessions/{session_id}/observations/{observation_id}", response_model=Observation, tags=["Sessions"])
async def session_observation(session_id: str, observation_id: str):
    from services.observations import observation_by_id
    from services.session_store import _session_dir, get_session

    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    observation = observation_by_id(_session_dir(session_id), observation_id)
    if observation is None:
        raise HTTPException(status_code=404, detail="Observation not found in this session.")
    return observation


@router.get("/sessions/{session_id}/impact", tags=["Sessions"])
async def session_impact(session_id: str, service: str = Query(min_length=1, max_length=120)):
    from services.incident_context import potential_impact
    from services.session_store import _session_dir, get_session

    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return potential_impact(_session_dir(session_id), service)


@router.get("/sessions/{session_id}/verification", response_model=VerifiedRCA, tags=["Sessions"])
async def session_verification(session_id: str):
    from services.session_store import _session_dir, get_session
    from services.verification import verify_incident

    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return verify_incident(_session_dir(session_id))


# ── Session — Query ───────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/query", response_model=RCAOutput, tags=["Sessions"])
async def session_query(session_id: str, request: QueryRequest):
    """
    Run incident analysis scoped to a specific session.
    Only evidence from this session's files and graph is used.
    The Q&A is saved to the session's chat history.
    """
    from services.session_store import get_session as _get, append_session_chat
    meta = _get(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    logger.info("Session query [%s]: %s", session_id[:8], request.query)

    try:
        from agents.orchestrator import Orchestrator
        result = Orchestrator().run(request.query, session_id=session_id)

        # Persist Q&A to chat history
        append_session_chat(session_id, {"role": "user",      "content": request.query})
        append_session_chat(session_id, {"role": "assistant", "content": result.model_dump()})

        return result

    except Exception as exc:
        logger.error("Session query error [%s]: %s", session_id[:8], exc, exc_info=True)
        return RCAOutput(
            answer     = f"Analysis error: {exc}",
            root_cause = "Analysis failed — check server logs.",
            confidence = "low",
            agent_trace=[{"agent": "orchestrator", "action": "error", "result": str(exc)}],
        )


# ── Session — Graph ───────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/graph", tags=["Sessions"])
async def session_graph(session_id: str):
    """Return the session's knowledge graph as node/edge JSON."""
    from services.session_store import get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    from services.graph_store import get_graph_json
    return JSONResponse(content=get_graph_json(session_id=session_id))


# ── Session — Chat history ────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/chat", tags=["Sessions"])
async def session_chat_history(session_id: str):
    """Return the full chat history for a session."""
    from services.session_store import get_session as _get, get_session_chat
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return {"session_id": session_id, "history": get_session_chat(session_id)}


@router.delete("/sessions/{session_id}/chat", tags=["Sessions"])
async def clear_session_chat(session_id: str):
    """Clear the chat history for a session (keeps graph and files)."""
    from services.session_store import get_session as _get, clear_session_chat as _clear
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    _clear(session_id)
    return {"cleared": True, "session_id": session_id}


# ═══════════════════════════════════════════════════════════════════════════════
# Global (backward-compatible) endpoints — no session isolation
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/ingest", response_model=IngestResult, tags=["Pipeline"])
async def ingest_files(files: list[UploadFile] = File(...)):
    """Global ingest — adds to the shared knowledge graph (backward-compat)."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    return await _run_ingest(files, session_id=None)


@router.post("/query", response_model=RCAOutput, tags=["Pipeline"])
async def query_incident(request: QueryRequest):
    """Global query — uses the shared knowledge graph (backward-compat)."""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    logger.info("Global query: %s", request.query)
    try:
        from agents.orchestrator import Orchestrator
        return Orchestrator().run(request.query, session_id=None)
    except Exception as exc:
        logger.error("Orchestrator error: %s", exc, exc_info=True)
        return RCAOutput(
            answer     = f"An error occurred: {exc}",
            root_cause = "Analysis failed — check server logs.",
            confidence = "low",
            agent_trace=[{"agent": "orchestrator", "action": "error", "result": str(exc)}],
        )


@router.get("/graph", tags=["Pipeline"])
async def get_graph():
    """Global knowledge graph (backward-compat)."""
    from services.graph_store import get_graph_json
    return JSONResponse(content=get_graph_json(session_id=None))
