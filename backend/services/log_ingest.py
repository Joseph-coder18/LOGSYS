"""
LogMind – Log Ingest Service (Phase LogMind-X)

Extended to persist timestamp, severity, modality, correlation_id, and
request_id attributes on each graph node — required by the temporal edge
generator (temporal_edges.py).
"""
import re
import logging
import uuid
from datetime import timezone, datetime
from typing import Any

from services.graph_store import _resolve_graph, _persist

logger = logging.getLogger("logmind.log_ingest")

# ── Regex patterns ────────────────────────────────────────────────────────────
SERVICE_RE  = re.compile(r'(?:service|component|module|app)[=:\s"\']+([a-zA-Z0-9_-]+)', re.IGNORECASE)
TARGET_RE   = re.compile(r'target_service[=:\s"\']+([a-zA-Z0-9_-]+)', re.IGNORECASE)
DB_RE       = re.compile(
    r'\b([a-zA-Z0-9_-]+-db|mysql|postgres(?:ql)?|redis|mongodb|sqlite|elasticsearch|cassandra|dynamodb)\b',
    re.IGNORECASE,
)
NODE_RE     = re.compile(r'\b(node-\d+)\b', re.IGNORECASE)
TS_RE       = re.compile(
    r'(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.,\d]*(?:Z|[+-]\d{2}:?\d{2})?)'
)
SEV_RE      = re.compile(r'\b(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL)\b', re.IGNORECASE)
CORR_RE     = re.compile(r'correlation_id\s*=\s*([A-Za-z0-9_-]+)', re.IGNORECASE)
REQ_RE      = re.compile(r'request_id\s*=\s*([A-Za-z0-9_-]+)', re.IGNORECASE)
CHANGE_RE   = re.compile(
    r'\b(deploy(?:ment|ed)?|restart(?:ed)?|config(?:uration)?\s+(?:change|update)|feature.flag)\b',
    re.IGNORECASE,
)


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _parse_ts(raw_ts: str) -> float:
    """Convert an ISO-ish timestamp string to a UTC unix float; fall back to now."""
    if not raw_ts:
        return _now_ts()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw_ts[:26], fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return _now_ts()


def ingest_log_content(text: str, session_id: str | None = None) -> None:
    """Parse log text and extract service nodes and dependencies into the knowledge graph.

    Each node now stores:
      - type         : "service" | "database" | "ObservationLog" | "Deployment" | "ConfigChange"
      - modality     : "log"
      - timestamp    : UTC unix float (from log line or current time)
      - severity     : "INFO" | "WARN" | "ERROR" | ...
      - correlation_id, request_id  (when present)
    These attrs are consumed by temporal_edges.build_temporal_edges().
    """
    if not text:
        return

    graph, _ = _resolve_graph(session_id)
    lines = text.splitlines()

    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue

        # ── Extract service ───────────────────────────────────────────────────
        service_match = SERVICE_RE.search(line_str)
        if not service_match:
            continue
        service = service_match.group(1).strip()
        if not service:
            continue

        # ── Shared attrs ──────────────────────────────────────────────────────
        ts_match   = TS_RE.search(line_str)
        raw_ts     = ts_match.group("ts") if ts_match else ""
        ts_float   = _parse_ts(raw_ts)
        sev_match  = SEV_RE.search(line_str)
        severity   = sev_match.group(1).upper() if sev_match else "INFO"
        corr_id    = (CORR_RE.search(line_str) or type("", (), {"group": lambda s, n: None})()).group(1)
        req_id     = (REQ_RE.search(line_str) or type("", (), {"group": lambda s, n: None})()).group(1)

        # ── Determine node type ───────────────────────────────────────────────
        change_match = CHANGE_RE.search(line_str)
        if change_match:
            keyword    = change_match.group(1).lower()
            node_type  = "Deployment" if "deploy" in keyword else "ConfigChange"
        else:
            node_type  = "ObservationLog"

        # ── Add/update service node ───────────────────────────────────────────
        node_id = f"log-{service}-{str(uuid.uuid4())[:8]}"
        graph.add_node(
            node_id,
            type=node_type,
            service=service,
            modality="log",
            timestamp=ts_float,
            raw_timestamp=raw_ts,
            severity=severity,
            correlation_id=corr_id,
            request_id=req_id,
            summary=line_str[:200],
        )
        # Keep the plain service node for graph connectivity
        if not graph.has_node(service):
            graph.add_node(service, type="service", service=service)
        graph.add_edge(service, node_id, rel="has_observation", timestamp=ts_float)

        # ── Extract target service calls (propagates_to) ──────────────────────
        for target in TARGET_RE.findall(line_str):
            target = target.strip()
            if target and target != service:
                if not graph.has_node(target):
                    graph.add_node(target, type="service", service=target)
                graph.add_edge(service, target, rel="depends_on", timestamp=ts_float)

        # ── Extract database interactions ─────────────────────────────────────
        for db in DB_RE.findall(line_str):
            db = db.strip()
            if db and db != service:
                if not graph.has_node(db):
                    graph.add_node(db, type="database", service=db)
                graph.add_edge(service, db, rel="depends_on", timestamp=ts_float)

        # ── Extract compute node interactions ─────────────────────────────────
        for node in NODE_RE.findall(line_str):
            node = node.strip()
            if node and node != service:
                if not graph.has_node(node):
                    graph.add_node(node, type="service", service=node)
                graph.add_edge(service, node, rel="depends_on", timestamp=ts_float)

    _persist(graph, session_id)
    logger.info(
        "Ingested log graph for session %s: %d nodes, %d edges",
        session_id, graph.number_of_nodes(), graph.number_of_edges(),
    )


def ingest_log_line(line: str, session_id: str | None = None) -> None:
    """Ingest a single line (backward compatibility)."""
    ingest_log_content(line, session_id=session_id)
