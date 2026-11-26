"""LogMind API Router aggregator."""
from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from api.ingest_helpers import _MAX_FILE_BYTES, _MAX_FILES, QueryRequest, SessionQueryRequest
from api.investigation import RemediateRequest, VerifyClaimRequest, router as investigation_router
from api.legacy import router as legacy_router
from api.sessions import router as sessions_router
from api.system import router as system_router

router = APIRouter()
router.include_router(system_router)
router.include_router(sessions_router)
router.include_router(investigation_router)
router.include_router(legacy_router)

__all__ = [
    "router",
    "run_in_threadpool",
    "_MAX_FILE_BYTES",
    "_MAX_FILES",
    "QueryRequest",
    "SessionQueryRequest",
    "VerifyClaimRequest",
    "RemediateRequest",
]
