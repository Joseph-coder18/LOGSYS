"""
LogMind-X – Contradiction-Aware Hypothesis Verification Engine (Objective 2)

verify_claim(session_id, source_service, target_service, relation)
    → ClaimVerification  (gate: "accept" | "downgrade" | "reject")

Five scoring dimensions (each 0.0 – 1.0):
  temporal_consistency  – a precedes/propagates_to path exists from source → target
  graph_consistency     – target is reachable from source via ANY path
  cross_modal_support   – ≥2 distinct modalities are present on nodes along the path
  contradiction_score   – 1.0 if no 'contradicts' edge touches either endpoint; 0.0 if one does
  evidence_completeness – fraction of expected modalities present for the source service

Gate thresholds:
  overall ≥ 0.75  → "accept"
  overall ≥ 0.50  → "downgrade"
  overall <  0.50 → "reject"
"""
from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from models import ClaimVerification, HypothesisCheck
from services.graph_store import _resolve_graph
from services.temporal_edges import edge_relations

logger = logging.getLogger("logmind.hypothesis")

ACCEPT_THRESHOLD    = 0.75
DOWNGRADE_THRESHOLD = 0.50

# Modalities we ideally want to see for a session
EXPECTED_MODALITIES = {"log", "metrics", "change", "image"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _service_nodes(graph: nx.DiGraph, service: str) -> list[str]:
    """Return all observation-node IDs whose 'service' attr matches."""
    return [n for n, d in graph.nodes(data=True) if d.get("service") == service]


def _nodes_on_paths(graph: nx.DiGraph, source: str, target: str) -> list[str]:
    """Return all node IDs that appear on any simple path from source to target."""
    result: set[str] = set()
    try:
        for path in nx.all_simple_paths(graph, source, target, cutoff=8):
            result.update(path)
    except (nx.NetworkXError, nx.NodeNotFound):
        pass
    return list(result)


# ── Scoring functions ─────────────────────────────────────────────────────────

def _score_temporal_consistency(
    graph: nx.DiGraph, source: str, target: str
) -> tuple[float, HypothesisCheck]:
    """Check that a 'precedes' or 'propagates_to' path exists source → target."""
    temporal_rels = {"precedes", "propagates_to"}
    temporal_edges = [
        (u, v) for u, v, d in graph.edges(data=True)
        if edge_relations(d) & temporal_rels
    ]
    temp_graph = nx.DiGraph()
    temp_graph.add_nodes_from(graph.nodes())
    temp_graph.add_edges_from(temporal_edges)

    has_path = nx.has_path(temp_graph, source, target) if (
        source in temp_graph and target in temp_graph
    ) else False

    score = 1.0 if has_path else 0.0
    check = HypothesisCheck(
        criterion="Temporal consistency",
        outcome="supports" if has_path else "missing",
        detail=(
            f"A 'precedes' or 'propagates_to' path exists from '{source}' to '{target}'."
            if has_path else
            f"No temporal path found from '{source}' to '{target}'. "
            "Upload more telemetry covering the time between these services."
        ),
    )
    return score, check


def _score_graph_consistency(
    graph: nx.DiGraph, source: str, target: str
) -> tuple[float, HypothesisCheck]:
    """Check that target is reachable from source via any path."""
    try:
        has_path = nx.has_path(graph, source, target)
    except (nx.NetworkXError, nx.NodeNotFound):
        has_path = False
    score = 1.0 if has_path else 0.0
    check = HypothesisCheck(
        criterion="Graph consistency",
        outcome="supports" if has_path else "missing",
        detail=(
            f"'{target}' is reachable from '{source}' in the knowledge graph."
            if has_path else
            f"No graph path found from '{source}' to '{target}'."
        ),
    )
    return score, check


def _score_cross_modal_support(
    graph: nx.DiGraph, source: str, target: str
) -> tuple[float, HypothesisCheck]:
    """Count distinct modalities present among nodes on the shortest path."""
    path_nodes = _nodes_on_paths(graph, source, target)
    modalities = {
        graph.nodes[n].get("modality")
        for n in path_nodes
        if graph.nodes[n].get("modality")
    }
    groups = {graph.nodes[node].get("provenance_group") or f"unclassified:{node}" for node in path_nodes if graph.nodes[node].get("modality")}
    support_count = min(len(modalities), len(groups))
    score = min(support_count / 2.0, 1.0)
    check = HypothesisCheck(
        criterion="Cross-modal support",
        outcome="supports" if support_count >= 2 else ("missing" if not modalities else "missing"),
        detail=(
            f"Found {len(modalities)} modality/modalities on path: {', '.join(sorted(modalities)) or 'none'}. "
            f"Across {len(groups)} source group(s). Shared telemetry streams count once."
        ),
    )
    return score, check


def _score_contradiction(
    graph: nx.DiGraph, source: str, target: str
) -> tuple[float, HypothesisCheck]:
    """Return 0 if any 'contradicts' edge touches the source or target, else 1."""
    contradicts_edges = [
        (u, v) for u, v, d in graph.edges(data=True)
        if "contradicts" in edge_relations(d)
    ]
    endpoint_nodes = set(_service_nodes(graph, source)) | set(_service_nodes(graph, target))
    endpoint_nodes.add(source)
    endpoint_nodes.add(target)

    hit = any(
        u in endpoint_nodes or v in endpoint_nodes
        for u, v in contradicts_edges
    )
    score = 0.0 if hit else 1.0
    check = HypothesisCheck(
        criterion="Contradiction check",
        outcome="contradicts" if hit else "supports",
        detail=(
            f"A 'contradicts' edge was found touching '{source}' or '{target}'. "
            "This weakens the causal claim."
            if hit else
            f"No contradicting evidence found for '{source}' or '{target}'."
        ),
    )
    return score, check


def _score_evidence_completeness(
    graph: nx.DiGraph, source: str
) -> tuple[float, HypothesisCheck]:
    """Fraction of EXPECTED_MODALITIES present for the source service's nodes."""
    source_modalities = {
        graph.nodes[n].get("modality")
        for n in _service_nodes(graph, source)
        if graph.nodes[n].get("modality")
    }
    # Also check session-level modalities (all nodes)
    all_modalities = {d.get("modality") for _, d in graph.nodes(data=True) if d.get("modality")}
    present = EXPECTED_MODALITIES & (source_modalities | all_modalities)
    score = len(present) / len(EXPECTED_MODALITIES)
    missing = EXPECTED_MODALITIES - present
    check = HypothesisCheck(
        criterion="Evidence completeness",
        outcome="supports" if score >= 0.5 else "missing",
        detail=(
            f"Present modalities: {', '.join(sorted(present)) or 'none'}. "
            + (f"Missing: {', '.join(sorted(missing))}." if missing else "All expected modalities present.")
        ),
    )
    return score, check


# ── Public API ────────────────────────────────────────────────────────────────

def verify_claim(
    session_id: str,
    source_service: str,
    target_service: str,
    relation: str = "propagates_to",
) -> ClaimVerification:
    """
    Score a causal claim and return a gate decision + per-dimension breakdown.
    """
    graph, _ = _resolve_graph(session_id)

    s_temporal,    c_temporal    = _score_temporal_consistency(graph, source_service, target_service)
    s_graph,       c_graph       = _score_graph_consistency(graph, source_service, target_service)
    s_cross,       c_cross       = _score_cross_modal_support(graph, source_service, target_service)
    s_contradict,  c_contradict  = _score_contradiction(graph, source_service, target_service)
    s_complete,    c_complete    = _score_evidence_completeness(graph, source_service)

    scores = {
        "temporal":     round(s_temporal,   3),
        "graph":        round(s_graph,      3),
        "cross_modal":  round(s_cross,      3),
        "contradiction": round(s_contradict, 3),
        "completeness": round(s_complete,   3),
    }
    overall = round(sum(scores.values()) / len(scores), 3)

    if overall >= ACCEPT_THRESHOLD:
        gate    = "accept"
        message = (
            f"Claim '{source_service} → {target_service}' is well-supported "
            f"(score {overall:.2f}). Proceeding with LLM explanation."
        )
    elif overall >= DOWNGRADE_THRESHOLD:
        gate    = "downgrade"
        message = (
            f"Claim '{source_service} → {target_service}' has partial support "
            f"(score {overall:.2f}). Explanation will be marked as uncertain."
        )
    else:
        gate    = "reject"
        message = (
            f"Claim '{source_service} → {target_service}' has insufficient evidence "
            f"(score {overall:.2f}). Upload more telemetry before drawing conclusions."
        )

    logger.info(
        "Claim verification [%s→%s] session=%s gate=%s overall=%.3f",
        source_service, target_service, session_id, gate, overall,
    )
    return ClaimVerification(
        gate=gate,
        scores=scores,
        overall=overall,
        message=message,
        checks=[c_temporal, c_graph, c_cross, c_contradict, c_complete],
    )

