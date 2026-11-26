"""Unsupervised temporal-causal origin ranker (playbook-independent)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from models import CausalOriginCandidate, Observation
from services.incident_context import load_dependencies
from services.observations import load_verification_observations

_ERROR_KEYWORDS = ("error", "timeout", "502", "503", "504", "exception", "fail", "oom", "killed", "refused")
_METRIC_THRESHOLDS = {
    "cpu_percent": 80.0,
    "memory_percent": 85.0,
    "error_rate": 0.05,
    "db_latency_ms": 500.0,
    "queue_depth": 100.0,
}


def _is_anomalous(event: Observation) -> bool:
    if event.modality == "change":
        return True
    if event.modality == "metrics":
        vals = event.metric_values or {}
        if any(vals.get(k, -1) >= t for k, t in _METRIC_THRESHOLDS.items()):
            return True
        if 0 < vals.get("db_connections_limit", 0) <= vals.get("db_connections_active", 0):
            return True
        if event.quality_warnings:
            return True
        return False
    if event.modality == "log":
        text = f"{event.summary} {event.excerpt}".lower()
        return any(kw in text for kw in _ERROR_KEYWORDS)
    if event.modality == "image":
        return bool(event.metric_readings or event.metric_values)
    return False


def _epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


def rank_causal_origins(
    session_dir: Path,
    events: list[Observation] | None = None,
    dependencies: list[dict] | None = None,
) -> list[CausalOriginCandidate]:
    """
    Rank candidate root-cause services using:
      S_s = 0.45 * TemporalPrecedence + 0.35 * TopologicalUpstreamness + 0.20 * CrossModalSupport
    """
    obs = events if events is not None else load_verification_observations(session_dir)
    deps = dependencies if dependencies is not None else load_dependencies(session_dir)

    anomalous_by_svc: dict[str, list[Observation]] = {}
    for event in obs:
        if not event.service:
            continue
        if _is_anomalous(event):
            anomalous_by_svc.setdefault(event.service, []).append(event)

    if not anomalous_by_svc:
        return []

    first_ts: dict[str, float] = {}
    first_iso: dict[str, str] = {}
    for svc, svc_events in anomalous_by_svc.items():
        timed = [(t, e.timestamp) for e in svc_events if (t := _epoch(e.timestamp)) is not None]
        if timed:
            t_val, iso_val = min(timed, key=lambda x: x[0])
            first_ts[svc] = t_val
            first_iso[svc] = iso_val

    t_min = min(first_ts.values()) if first_ts else 0.0
    t_max = max(first_ts.values()) if first_ts else 0.0
    span = t_max - t_min

    # impact_graph: callee -> caller (failure propagates from dependency to caller)
    impact_graph = nx.DiGraph()
    for edge in deps:
        caller, callee = edge.get("caller"), edge.get("callee")
        if caller and callee:
            impact_graph.add_edge(callee, caller)

    has_topology = impact_graph.number_of_edges() > 0
    candidates: list[CausalOriginCandidate] = []

    for svc, svc_events in anomalous_by_svc.items():
        # 1. Temporal precedence
        if svc in first_ts and span > 0:
            temporal_score = 1.0 - (first_ts[svc] - t_min) / span
        else:
            temporal_score = 1.0 if svc in first_ts else 0.3

        # 2. Topological upstreamness
        downstream_affected: list[str] = []
        if has_topology and svc in impact_graph:
            downstream_affected = sorted(
                node for node in nx.descendants(impact_graph, svc)
                if node in anomalous_by_svc and node != svc
            )
            earlier_upstream = [
                node for node in nx.ancestors(impact_graph, svc)
                if node in anomalous_by_svc and node != svc
                and (svc not in first_ts or node not in first_ts or first_ts[node] <= first_ts[svc])
            ]
            upstream_score = (len(downstream_affected) + 1.0) / (
                len(downstream_affected) + 1.0 + 2.0 * len(earlier_upstream)
            )
        else:
            upstream_score = 0.5

        # 3. Cross-modal corroboration
        modalities = sorted({e.modality for e in svc_events if e.modality})
        modal_score = min(1.0, len(modalities) / 3.0)

        score = round(0.45 * temporal_score + 0.35 * upstream_score + 0.20 * modal_score, 4)
        candidates.append(
            CausalOriginCandidate(
                service=svc,
                score=score,
                temporal_score=round(temporal_score, 4),
                upstream_score=round(upstream_score, 4),
                modal_score=round(modal_score, 4),
                first_anomaly_at=first_iso.get(svc),
                downstream_affected=downstream_affected,
                modalities=modalities,
                evidence_ids=[e.id for e in svc_events[:5]],
            )
        )

    candidates.sort(
        key=lambda c: (
            -c.score,
            -len(c.downstream_affected),
            first_ts.get(c.service, float("inf")),
            c.service,
        )
    )
    return candidates

