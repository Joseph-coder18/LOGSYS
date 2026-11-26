"""Session CRUD, ingest, overview, observations, and impact routes."""
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from api.ingest_helpers import run_ingest
from models import (
    CreateSessionRequest,
    IncidentOverview,
    IngestResult,
    Observation,
    ObservationFilter,
    ObservationPage,
    Session,
)

router = APIRouter()


class RenameSessionRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


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


@router.patch("/sessions/{session_id}", response_model=Session, tags=["Sessions"])
async def rename_session(session_id: str, request: RenameSessionRequest):
    """Rename a session manually."""
    from services.session_store import rename_session as _rename
    meta = _rename(session_id, request.name, manual=True)
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


@router.post("/sessions/{session_id}/ingest", response_model=IngestResult, tags=["Sessions"])
async def session_ingest(session_id: str, files: list[UploadFile] = File(...)):
    """Ingest files into a specific session's knowledge graph."""
    from services.session_store import get_session as _get
    meta = _get(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    return await run_ingest(files, session_id=session_id)


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

