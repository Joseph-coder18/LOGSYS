"""
LogMind – Shared Pydantic models used across all services.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


def _new_id() -> str:
    return str(uuid.uuid4())


class BaseChunk(BaseModel):
    """A single unit of indexed content — log window, metric summary, or image summary."""
    chunk_id: str = Field(default_factory=_new_id)
    source_file: str
    modality: str          # "log" | "metrics" | "image"
    text: str              # Text that will be embedded
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    status: str = "success"
    files_processed: int = 0
    chunks_indexed: int = 0
    graph_nodes: int = 0
    graph_edges: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    processed_files: list[str] = Field(default_factory=list)
    observations_added: int = 0
    indexing_status: Literal["skipped", "complete", "partial", "failed"] = "skipped"


class PoolLimitChange(BaseModel):
    before: int = Field(gt=0, strict=True)
    after: int = Field(gt=0, strict=True)


class Observation(BaseModel):
    id: str = ""
    modality: Literal["log", "metrics", "change", "image"]
    source_file: str
    source_position: int
    source_label: str | None = None
    service: str | None = None
    timestamp: str | None = None  # UTC ISO 8601, when the source supplies a timezone
    original_timestamp: str | None = None
    summary: str
    excerpt: str
    severity: str | None = None
    change_type: Literal["deployment", "restart", "config_change"] | None = None
    pool_limit_change: PoolLimitChange | None = None
    anomalies: list[str] = Field(default_factory=list)
    metric_values: dict[str, float] = Field(default_factory=dict)
    anomaly_fields: list[str] = Field(default_factory=list)
    metric_units: dict[str, str] = Field(default_factory=dict)
    metric_aggregations: dict[str, str] = Field(default_factory=dict)
    metric_readings: list[dict[str, Any]] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    interval_start: str | None = None
    interval_end: str | None = None
    stream_id: str | None = None
    provenance_group: str | None = None
    extraction_version: int | None = None


ObservationFilter = Literal["all", "anomalies", "changes", "errors"]


class ObservationCounts(BaseModel):
    all: int = 0
    anomalies: int = 0
    changes: int = 0
    errors: int = 0


class ObservationPage(BaseModel):
    observations: list[Observation]
    next_offset: int | None = None
    counts: ObservationCounts = Field(default_factory=ObservationCounts)
    services: list[str] = Field(default_factory=list)


class ActivityBucket(BaseModel):
    start: str
    end: str
    counts: ObservationCounts


class IncidentOverview(BaseModel):
    revision: str
    counts: ObservationCounts = Field(default_factory=ObservationCounts)
    modalities: dict[str, int] = Field(default_factory=dict)
    services: list[str] = Field(default_factory=list)
    first_at: str | None = None
    last_at: str | None = None
    undated: int = 0
    intervals: int = 0
    activity: list[ActivityBucket] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    type: str              # modality
    source: str            # filename
    snippet: str
    score: float = 0.0
    observation_id: str | None = None
    source_position: int | None = None
    why: str | None = None
    timestamp: str | None = None


class RCAOutput(BaseModel):
    root_cause: str = ""
    summary: str = ""
    timeline: list[str] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    confidence: str = "low"
    confidence_score: float = 0.0
    evidence: list[EvidenceItem] = Field(default_factory=list)
    agent_trace: list[dict[str, Any]] = Field(default_factory=list)
    answer: str = ""


class HypothesisCheck(BaseModel):
    criterion: str
    outcome: Literal["supports", "contradicts", "missing"]
    detail: str
    observation_id: str | None = None


class Hypothesis(BaseModel):
    label: str
    service: str
    score: int
    checks: list[HypothesisCheck]


class NextCheck(BaseModel):
    instruction: str
    service: str | None = None
    start: str | None = None
    end: str | None = None


class VerifiedRCA(RCAOutput):
    decision: Literal["supported", "ambiguous", "insufficient_evidence"]
    suspected_origin: str | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    next_check: NextCheck | None = None
    evidence_revision: str | None = None


class ObservationCitation(BaseModel):
    observation_id: str
    label: str


class ApprovedClaim(BaseModel):
    text: str
    citations: list[ObservationCitation] = Field(default_factory=list)


class GroundedReply(VerifiedRCA):
    query_scope: dict[str, str | None] | None = None
    claims: list[ApprovedClaim] = Field(default_factory=list)
    answer_mode: Literal["generated", "extractive", "local"] = "local"


# ── Session models (Phase 4A) ─────────────────────────────────────────────────

class Session(BaseModel):
    """Full session metadata."""
    id: str
    name: str
    created_at: str
    files: list[str] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0


class SessionList(BaseModel):
    sessions: list[Session]


class ChatEntry(BaseModel):
    """A single turn in a session's chat history."""
    role: str                   # "user" | "assistant"
    content: Any                # str for user, RCAOutput-dict for assistant
    timestamp: str = Field(default_factory=lambda: "")


class CreateSessionRequest(BaseModel):
    name: str | None = None     # optional custom name; auto-generated if omitted


# ── LogMind-X models (feature/logmind-x-full) ────────────────────────────────

class TemporalEdgeCounts(BaseModel):
    """Current relation totals after rebuilding POST /sessions/{id}/finalize."""
    precedes:      int = 0
    propagates_to: int = 0
    supports:      int = 0
    contradicts:   int = 0


class ClaimVerification(BaseModel):
    """Gate decision and per-dimension scores for a causal claim."""
    gate: Literal["accept", "downgrade", "reject"]
    scores: dict[str, float]   # keys: temporal, graph, cross_modal, contradiction, completeness
    overall: float
    message: str
    checks: list[HypothesisCheck] = Field(default_factory=list)


class RemediationAction(BaseModel):
    """A single safe, reversible remediation suggestion."""
    id:                    str = Field(default_factory=_new_id)
    action_type:           str   # "rollback" | "revert_config" | "disable_flag" | "generic"
    description:           str
    reversible:            bool = True
    rollback_plan:         str
    expected_signal_change: str
    confidence:            float = 0.0   # 0–1; fraction of symptom nodes reduced


class RemediationResult(BaseModel):
    """Returned by POST /sessions/{id}/remediate."""
    causal_node_id:  str
    actions:         list[RemediationAction] = Field(default_factory=list)
    symptom_before:  int = 0
    symptom_after:   int = 0


class NextStepResult(BaseModel):
    """Returned by GET /sessions/{id}/next-step."""
    abstain:           bool = False
    abstain_reason:    str = ""
    suggested_modality: str | None = None   # "log" | "metrics" | "change" | "image"
    suggested_service:  str | None = None
    instruction:       str = ""
    deficits:          dict[str, list[str]] = Field(default_factory=dict)

