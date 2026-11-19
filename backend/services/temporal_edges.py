"""
LogMind-X – Temporal Edge Generator (Objective 1)

Runs after a session's logs are fully ingested.  Reads all graph nodes that
carry a *timestamp* attr (added by the new log_ingest.py) and emits four
categories of time-aware edges into the session's NetworkX DiGraph:

  precedes      – event A occurred strictly before event B on the same service
  propagates_to – explicit downstream call recorded via target_service in logs
  supports      – two nodes from DIFFERENT modalities share the same
                   correlation_id or request_id and occurred within SUPPORT_WINDOW_S
  contradicts   – an ERROR-severity log node and a *non-anomalous* metric node
                  co-exist for the same service within CONTRADICT_WINDOW_S

Call build_temporal_edges(session_id) → returns edge-count summary dict.
"""
from __future__ import annotations

import logging
from itertools import combinations
from typing import Any

from services.graph_store import _resolve_graph, _persist

logger = logging.getLogger("logmind.temporal_edges")

# ── Tuneable windows (seconds) ────────────────────────────────────────────────
SUPPORT_WINDOW_S    = 30.0   # max gap to call two observations "supporting"
CONTRADICT_WINDOW_S = 60.0   # max gap to flag a contradiction


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(attrs: dict) -> float | None:
    """Return the node's numeric timestamp or None."""
    v = attrs.get("timestamp")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _service(attrs: dict) -> str:
    return attrs.get("service", "")


def _modality(attrs: dict) -> str:
    return attrs.get("modality", "")


def _corr_key(attrs: dict) -> str | None:
    """Return correlation_id, request_id, or None — the shared identity key."""
    return attrs.get("correlation_id") or attrs.get("request_id")


# ── Main builder ──────────────────────────────────────────────────────────────

def build_temporal_edges(session_id: str) -> dict[str, int]:
    """
    Load the session graph, derive temporal edges, persist, and return counts.
    Safe to call multiple times — adds edges idempotently (skips if rel already
    exists between the same pair).
    """
    graph, is_session = _resolve_graph(session_id)

    counts = {"precedes": 0, "propagates_to": 0, "supports": 0, "contradicts": 0}

    # ── 1. propagates_to ─────────────────────────────────────────────────────
    # Promote existing depends_on edges that have a timestamp to propagates_to
    # so the hypothesis engine can distinguish structural from causal flow.
    for src, dst, attrs in list(graph.edges(data=True)):
        if attrs.get("rel") == "depends_on" and "timestamp" in attrs:
            graph[src][dst]["rel"] = "propagates_to"
            counts["propagates_to"] += 1

    # Collect only nodes that have a timestamp (observation-type nodes)
    timed_nodes: list[tuple[str, dict]] = [
        (nid, attrs)
        for nid, attrs in graph.nodes(data=True)
        if _ts(attrs) is not None
    ]

    if not timed_nodes:
        logger.info("No timed nodes found for session %s — skipping observation-level temporal edges.", session_id)
        _persist(graph, session_id)
        return counts

    # Sort all timed nodes globally by timestamp
    timed_nodes.sort(key=lambda x: _ts(x[1]))  # type: ignore[arg-type]

    # ── 2. precedes ──────────────────────────────────────────────────────────
    # For each service, connect consecutive observation events in time order.
    by_service: dict[str, list[tuple[str, dict]]] = {}
    for nid, attrs in timed_nodes:
        svc = _service(attrs)
        if svc:
            by_service.setdefault(svc, []).append((nid, attrs))

    for svc, nodes in by_service.items():
        nodes.sort(key=lambda x: _ts(x[1]))  # type: ignore[arg-type]
        for i in range(len(nodes) - 1):
            src, _ = nodes[i]
            dst, _ = nodes[i + 1]
            if not graph.has_edge(src, dst):
                graph.add_edge(src, dst, rel="precedes",
                               timestamp=_ts(nodes[i][1]),
                               service=svc)
                counts["precedes"] += 1

    # ── 3. supports ──────────────────────────────────────────────────────────
    # Two nodes from different modalities that share the same correlation_id or
    # request_id and occurred within SUPPORT_WINDOW_S of each other.
    for (aid, a_attrs), (bid, b_attrs) in combinations(timed_nodes, 2):
        if _modality(a_attrs) == _modality(b_attrs):
            continue
        a_key = _corr_key(a_attrs)
        b_key = _corr_key(b_attrs)
        if not a_key or a_key != b_key:
            continue
        gap = abs((_ts(a_attrs) or 0) - (_ts(b_attrs) or 0))
        if gap > SUPPORT_WINDOW_S:
            continue
        # Orient: earlier → later
        if (_ts(a_attrs) or 0) <= (_ts(b_attrs) or 0):
            src, dst = aid, bid
        else:
            src, dst = bid, aid

        if graph.has_edge(dst, src) and graph[dst][src].get("rel") == "precedes":
            graph.remove_edge(dst, src)
            counts["precedes"] = max(0, counts["precedes"] - 1)
        elif graph.has_edge(src, dst) and graph[src][dst].get("rel") == "precedes":
            counts["precedes"] = max(0, counts["precedes"] - 1)

        if not graph.has_edge(src, dst) or graph[src][dst].get("rel") != "supports":
            graph.add_edge(src, dst, rel="supports",
                           correlation_id=a_key,
                           gap_s=round(gap, 2))
            counts["supports"] += 1

    # ── 4. contradicts ───────────────────────────────────────────────────────
    # An ERROR-severity log node co-exists with a *normal* (non-anomalous)
    # metric node for the same service within CONTRADICT_WINDOW_S.
    error_nodes  = [(nid, attrs) for nid, attrs in timed_nodes
                    if attrs.get("severity") in ("ERROR", "CRITICAL")
                    and attrs.get("modality") == "log"]
    metric_nodes = [(nid, attrs) for nid, attrs in timed_nodes
                    if attrs.get("modality") == "metrics"
                    and not attrs.get("anomaly", False)]

    for (eid, e_attrs) in error_nodes:
        for (mid, m_attrs) in metric_nodes:
            if _service(e_attrs) != _service(m_attrs):
                continue
            gap = abs((_ts(e_attrs) or 0) - (_ts(m_attrs) or 0))
            if gap > CONTRADICT_WINDOW_S:
                continue

            if graph.has_edge(mid, eid) and graph[mid][eid].get("rel") == "precedes":
                graph.remove_edge(mid, eid)
                counts["precedes"] = max(0, counts["precedes"] - 1)
            elif graph.has_edge(eid, mid) and graph[eid][mid].get("rel") == "precedes":
                counts["precedes"] = max(0, counts["precedes"] - 1)

            if not graph.has_edge(eid, mid) or graph[eid][mid].get("rel") != "contradicts":
                graph.add_edge(eid, mid, rel="contradicts",
                               service=_service(e_attrs),
                               gap_s=round(gap, 2))
                counts["contradicts"] += 1

    _persist(graph, session_id)
    logger.info(
        "Temporal edges built for session %s: %s",
        session_id, counts,
    )
    return counts

