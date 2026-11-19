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
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, model_validator

from models import GroundedReply, BaseChunk, IngestResult, RCAOutput, Session, CreateSessionRequest, Observation, ObservationFilter, ObservationPage, IncidentOverview, VerifiedRCA, TemporalEdgeCounts, ClaimVerification, RemediationResult, NextStepResult

logger = logging.getLogger("logmind.api")

router = APIRouter()

# ── Request models ────────────────────────────────────────────────────────────
class QueryRequest(BaseModel):
    query: str
    demo: bool = False


class SessionQueryRequest(QueryRequest):
    mode: Literal["grounded", "graph_rag"] = "grounded"
    service: str | None = Field(default=None, min_length=1, max_length=120)
    start: str | None = None
    end: str | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        from services.observations import normalize_timestamp
        for name in ("start", "end"):
            value = getattr(self, name)
            if value is not None:
                utc = normalize_timestamp(value)
                if utc is None:
                    raise ValueError("UTC scope requires dated timestamps with timezones.")
                setattr(self, name, utc)
        if self.start and self.end and self.start >= self.end:
            raise ValueError("Scope end must be after its start.")
        if self.mode == "graph_rag" and (self.service or self.start or self.end):
            raise ValueError("Scoped queries use grounded mode; the legacy baseline has no time filtering.")
        if self.demo:
            raise ValueError("Synthetic demo fallback is unavailable inside an incident. Upload the demo files explicitly.")
        return self


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
            if info.get("message") and info.get("status") != "ready":
                result.warnings.append(info["message"])
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
    from services.log_ingest import ingest_log_content
    from services.observations import save_observations
    from services.session_store import _session_dir, get_session, register_session_files

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
                    cache = _session_dir(session_id) / "vision-cache" if session_id else None
                    chunk, observations = await run_in_threadpool(image_analyzer.analyze_image, path, cache)
                    chunks = [chunk]
            for observation in observations:
                result.warnings.extend(f"{name} record {observation.source_position}: {warning}" for warning in observation.quality_warnings)
            if not chunks:
                raise ValueError("No usable telemetry found.")
            if session_id and get_session(session_id) is None:
                raise HTTPException(status_code=404, detail="Session no longer exists.")

            if session_id:
                result.observations_added += save_observations(_session_dir(session_id), digest, observations)
            _add_file_to_graph(name, modality, chunks, session_id)
            if modality == "log" and session_id is None:
                try:
                    ingest_log_content(content.decode("utf-8-sig", errors="replace"), session_id=session_id)
                except Exception as e:
                    logger.exception("Failed to ingest log relationships for %s: %s", name, e)
            for index, chunk in enumerate(chunks):
                if session_id and observations:
                    chunk.metadata["source_digest"] = digest
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
    if session_id and result.files_processed:
        from services.temporal_edges import build_temporal_edges
        try:
            await run_in_threadpool(build_temporal_edges, session_id)
        except Exception:
            logger.exception("Could not refresh incident graph for %s", session_id)
            result.warnings.append("Telemetry was saved. Retry graph finalization to refresh temporal relationships.")
    stats = get_stats(session_id=session_id)
    result.graph_nodes, result.graph_edges = stats["nodes"], stats["edges"]
    if session_id:
        register_session_files(session_id, result.processed_files)

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


@router.get("/sessions/{session_id}/overview", response_model=IncidentOverview, tags=["Sessions"])
async def session_overview(session_id: str):
    from services.observations import incident_overview
    from services.session_store import _session_dir, get_session

    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return incident_overview(_session_dir(session_id))


@router.get("/sessions/{session_id}/observations", response_model=ObservationPage, tags=["Sessions"])
async def session_observations(
    session_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=250),
    category: ObservationFilter = "all",
    service: str | None = Query(None, min_length=1),
    start: str | None = None,
    end: str | None = None,
):
    """List source-linked telemetry in UTC order, followed by undated observations."""
    from services.observations import load_observations, normalize_timestamp
    from services.session_store import _session_dir, get_session

    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    start_utc, end_utc = normalize_timestamp(start), normalize_timestamp(end)
    if (start is not None and start_utc is None) or (end is not None and end_utc is None):
        raise HTTPException(status_code=422, detail="Time filters require timestamps with timezones.")
    if start_utc and end_utc and start_utc >= end_utc:
        raise HTTPException(status_code=422, detail="The end of the time range must be after the start.")
    return load_observations(_session_dir(session_id), offset, limit, category, service, start_utc, end_utc)


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


@router.get("/sessions/{session_id}/verification/latest", response_model=VerifiedRCA | None, tags=["Sessions"])
async def latest_session_verification(session_id: str):
    from services.session_store import get_session, get_saved_verification

    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return get_saved_verification(session_id)


@router.get("/sessions/{session_id}/verification", response_model=VerifiedRCA, tags=["Sessions"])
@router.post("/sessions/{session_id}/verification", response_model=VerifiedRCA, tags=["Sessions"])
async def session_verification(session_id: str, request: Request):
    from services.observations import evidence_revision
    from services.session_store import _session_dir, get_session, save_verification
    from services.verification import verify_incident

    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    session_dir = _session_dir(session_id)
    diagnosis = verify_incident(session_dir)
    if request.method == "POST" and diagnosis.evidence_revision == evidence_revision(session_dir):
        save_verification(session_id, diagnosis.model_dump())
    return diagnosis


# ── Session — Query ───────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/query", response_model=GroundedReply | RCAOutput, tags=["Sessions"])
async def session_query(session_id: str, request: SessionQueryRequest):
    """Answer from verified session evidence, with an opt-in Graph RAG baseline."""
    from services.session_store import _session_dir, append_session_chat, get_session, get_session_chat

    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    generation = get_session(session_id).get("chat_generation", 0)
    try:
        if request.mode == "graph_rag":
            from agents.orchestrator import Orchestrator

            result = await run_in_threadpool(Orchestrator().run, request.query, session_id)
        else:
            from services.grounded_chat import answer_incident

            result = await answer_incident(_session_dir(session_id), request.query, get_session_chat(session_id), service=request.service, start=request.start, end=request.end)
        if get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Session no longer exists.")

        append_session_chat(
            session_id,
            {"role": "user", "content": request.query},
            {"role": "assistant", "content": result.model_dump()},
            expected_generation=generation,
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Session query failed [%s]", session_id[:8])
        raise HTTPException(status_code=500, detail="Could not answer this incident question.") from exc


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
# LogMind-X endpoints (feature/logmind-x-full)
# ═══════════════════════════════════════════════════════════════════════════════

# ── Objective 1 – Temporal Evidence Graph ─────────────────────────────────────

@router.post("/sessions/{session_id}/finalize", response_model=TemporalEdgeCounts, tags=["LogMind-X"])
async def finalize_session(session_id: str):
    """
    Refresh source observations and temporal relationships, including older uploads.
    Return the total count of each temporal relation in the rebuilt graph.
    """
    from services.session_store import get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    try:
        from services.temporal_edges import build_temporal_edges
        counts = await run_in_threadpool(build_temporal_edges, session_id)
        return TemporalEdgeCounts(**counts)
    except Exception as exc:
        logger.exception("Temporal edge building failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=f"Could not build temporal edges: {exc}") from exc


# ── Objective 2 – Contradiction-Aware Hypothesis Verification ─────────────────

class VerifyClaimRequest(BaseModel):
    source_service: str
    target_service: str
    relation: str = "propagates_to"


@router.post("/sessions/{session_id}/verify-claim", response_model=ClaimVerification, tags=["LogMind-X"])
async def verify_claim(session_id: str, request: VerifyClaimRequest):
    """
    Score a causal claim (serviceA → serviceB) against the temporal evidence graph.
    Returns a gate decision (accept / downgrade / reject) and per-dimension scores.
    """
    from services.session_store import get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    try:
        from services.hypothesis import verify_claim as _verify
        result = await run_in_threadpool(
            _verify, session_id, request.source_service, request.target_service, request.relation
        )
        return result
    except Exception as exc:
        logger.exception("Claim verification failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=f"Could not verify claim: {exc}") from exc


# ── Objective 3 – Adaptive Investigation & Safe Abstention ────────────────────

@router.get("/sessions/{session_id}/next-step", response_model=NextStepResult, tags=["LogMind-X"])
async def next_investigation_step(session_id: str):
    """
    Detect evidence deficits for this session and suggest the next modality to upload.
    Returns abstain=True when competing hypotheses cannot be discriminated.
    """
    from services.session_store import get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    try:
        from services.investigation_policy import suggest_next
        result = await run_in_threadpool(suggest_next, session_id)
        return result
    except Exception as exc:
        logger.exception("Investigation policy failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=f"Could not compute next step: {exc}") from exc


# ── Objective 4 – Counterfactual Remediation Validator ───────────────────────

class RemediateRequest(BaseModel):
    causal_node_id: str


@router.post("/sessions/{session_id}/remediate", response_model=RemediationResult, tags=["LogMind-X"])
async def remediate(session_id: str, request: RemediateRequest):
    """
    Simulate reversing/removing the suspected causal node and return safe, reversible actions.
    All actions are labelled (Simulated) — no real deployment changes are made.
    """
    from services.session_store import get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    try:
        from services.remediation import suggest_remediation
        result = await run_in_threadpool(suggest_remediation, session_id, request.causal_node_id)
        return result
    except Exception as exc:
        logger.exception("Remediation failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=f"Could not compute remediation: {exc}") from exc


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
        return Orchestrator().run(request.query, session_id=None, demo=request.demo)
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
