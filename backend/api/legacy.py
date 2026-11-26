"""Global (backward-compatible) endpoints without session isolation."""
import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from api.ingest_helpers import QueryRequest, run_ingest
from models import IngestResult, RCAOutput

logger = logging.getLogger("logmind.api")
router = APIRouter()


@router.post("/ingest", response_model=IngestResult, tags=["Pipeline"])
async def ingest_files(files: list[UploadFile] = File(...)):
    """Global ingest — adds to the shared knowledge graph (backward-compat)."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    return await run_ingest(files, session_id=None)


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
            answer=f"An error occurred: {exc}",
            root_cause="Analysis failed — check server logs.",
            confidence="low",
            agent_trace=[{"agent": "orchestrator", "action": "error", "result": str(exc)}],
        )


@router.get("/graph", tags=["Pipeline"])
async def get_graph():
    """Global knowledge graph (backward-compat)."""
    from services.graph_store import get_graph_json
    return JSONResponse(content=get_graph_json(session_id=None))

