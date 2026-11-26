"""Session verification, grounded chat, graph, and LogMind-X routes."""
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.ingest_helpers import SessionQueryRequest, _run_in_threadpool
from models import (
    CausalRankingResult,
    ClaimVerification,
    GroundedReply,
    NextStepResult,
    RCAOutput,
    RemediationResult,
    TemporalEdgeCounts,
    VerifiedRCA,
)

logger = logging.getLogger("logmind.api")
router = APIRouter()


class VerifyClaimRequest(BaseModel):
    source_service: str
    target_service: str
    relation: str = "propagates_to"


class RemediateRequest(BaseModel):
    causal_node_id: str


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
    from services.session_store import _session_dir, auto_title_session, get_session, save_verification
    from services.verification import verify_incident

    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    session_dir = _session_dir(session_id)
    diagnosis = verify_incident(session_dir)
    if request.method == "POST" and diagnosis.evidence_revision == evidence_revision(session_dir):
        save_verification(session_id, diagnosis.model_dump())
        if diagnosis.decision == "supported" and diagnosis.root_cause:
            auto_title_session(session_id, diagnosis.root_cause, force_if_auto=True)
    return diagnosis


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
            result = await _run_in_threadpool(Orchestrator().run, request.query, session_id)
        else:
            from services.grounded_chat import answer_incident
            result = await answer_incident(
                _session_dir(session_id),
                request.query,
                get_session_chat(session_id),
                service=request.service,
                start=request.start,
                end=request.end,
            )
        if get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Session no longer exists.")

        append_session_chat(
            session_id,
            {"role": "user", "content": request.query},
            {"role": "assistant", "content": result.model_dump()},
            expected_generation=generation,
        )
        from services.session_store import auto_title_session
        auto_title_session(session_id, request.query)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Session query failed [%s]", session_id[:8])
        raise HTTPException(status_code=500, detail="Could not answer this incident question.") from exc


@router.get("/sessions/{session_id}/graph", tags=["Sessions"])
async def session_graph(session_id: str):
    """Return the session's knowledge graph as node/edge JSON."""
    from services.session_store import get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    from services.graph_store import get_graph_json
    return JSONResponse(content=get_graph_json(session_id=session_id))


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


@router.post("/sessions/{session_id}/finalize", response_model=TemporalEdgeCounts, tags=["LogMind-X"])
async def finalize_session(session_id: str):
    """Refresh source observations and temporal relationships, including older uploads."""
    from services.session_store import get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    try:
        from services.temporal_edges import build_temporal_edges
        counts = await _run_in_threadpool(build_temporal_edges, session_id)
        return TemporalEdgeCounts(**counts)
    except Exception as exc:
        logger.exception("Temporal edge building failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=f"Could not build temporal edges: {exc}") from exc


@router.post("/sessions/{session_id}/verify-claim", response_model=ClaimVerification, tags=["LogMind-X"])
async def verify_claim(session_id: str, request: VerifyClaimRequest):
    """Score a causal claim (serviceA -> serviceB) against the temporal evidence graph."""
    from services.session_store import get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    try:
        from services.hypothesis import verify_claim as _verify
        return await _run_in_threadpool(
            _verify, session_id, request.source_service, request.target_service, request.relation
        )
    except Exception as exc:
        logger.exception("Claim verification failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=f"Could not verify claim: {exc}") from exc


@router.get("/sessions/{session_id}/next-step", response_model=NextStepResult, tags=["LogMind-X"])
async def next_investigation_step(session_id: str):
    """Detect evidence deficits for this session and suggest the next modality to upload."""
    from services.session_store import get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    try:
        from services.investigation_policy import suggest_next
        return await _run_in_threadpool(suggest_next, session_id)
    except Exception as exc:
        logger.exception("Investigation policy failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=f"Could not compute next step: {exc}") from exc


@router.post("/sessions/{session_id}/remediate", response_model=RemediationResult, tags=["LogMind-X"])
async def remediate(session_id: str, request: RemediateRequest):
    """Simulate reversing/removing the suspected causal node and return safe, reversible actions."""
    from services.session_store import get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    try:
        from services.remediation import suggest_remediation
        return await _run_in_threadpool(suggest_remediation, session_id, request.causal_node_id)
    except Exception as exc:
        logger.exception("Remediation failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=f"Could not compute remediation: {exc}") from exc


@router.get("/sessions/{session_id}/causal-ranking", response_model=CausalRankingResult, tags=["LogMind-X"])
async def causal_ranking(session_id: str):
    """Unsupervised temporal-causal origin ranking across anomalous session services."""
    from services.session_store import _session_dir, get_session as _get
    if _get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    from services.causal_ranker import rank_causal_origins
    candidates = await _run_in_threadpool(rank_causal_origins, _session_dir(session_id))
    return CausalRankingResult(session_id=session_id, candidates=candidates)
