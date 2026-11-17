"""
LogMind-X – Adaptive Investigation Policy (Objective 3)

suggest_next(session_id) → NextStepResult

Responsibilities:
  1. detect_deficits   – for each service, list which modalities are absent.
  2. suggest_next      – pick the missing modality with highest expected value.
  3. should_abstain    – return True when top-2 candidate scores are within
                         ABSTAIN_DELTA of each other and no new modality can
                         break the tie.

The function is intentionally telemetry-agnostic: it works even when only
logs are present (and will simply suggest adding metrics / change events).
"""
from __future__ import annotations

import logging
from collections import defaultdict

import networkx as nx

from models import NextStepResult
from services.graph_store import _resolve_graph

logger = logging.getLogger("logmind.investigation_policy")

# ── Configuration ─────────────────────────────────────────────────────────────
ABSTAIN_DELTA      = 0.05   # score gap below which we cannot discriminate
ALL_MODALITIES     = ["log", "metrics", "change", "image"]

# Priority order: metrics → change events → image (logs always present if ingested)
MODALITY_PRIORITY  = ["metrics", "change", "image", "log"]

MODALITY_LABELS = {
    "log":     "application logs",
    "metrics": "time-series metrics (CPU, latency, error-rate)",
    "change":  "change/deployment events",
    "image":   "dashboard screenshots",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _present_modalities(graph: nx.DiGraph, service: str) -> set[str]:
    """Return the set of modalities observed for a given service."""
    return {
        d.get("modality")
        for _, d in graph.nodes(data=True)
        if d.get("service") == service and d.get("modality")
    }


def _candidate_services(graph: nx.DiGraph) -> list[str]:
    """Services that have observation nodes (excluding raw service skeleton nodes)."""
    return list({
        d.get("service")
        for _, d in graph.nodes(data=True)
        if d.get("service") and d.get("type") not in ("service", "database")
    })


def _error_count(graph: nx.DiGraph, service: str) -> int:
    return sum(
        1 for _, d in graph.nodes(data=True)
        if d.get("service") == service and d.get("severity") in ("ERROR", "CRITICAL")
    )


# ── Deficit detection ─────────────────────────────────────────────────────────

def detect_deficits(session_id: str) -> dict[str, list[str]]:
    """
    For each service, return the list of modalities that are MISSING.
    Returns {} if no observation nodes exist yet.
    """
    graph, _ = _resolve_graph(session_id)
    services = _candidate_services(graph)
    deficits: dict[str, list[str]] = {}
    for svc in services:
        present = _present_modalities(graph, svc)
        missing = [m for m in ALL_MODALITIES if m not in present]
        if missing:
            deficits[svc] = missing
    return deficits


# ── Abstention check ──────────────────────────────────────────────────────────

def should_abstain(session_id: str) -> tuple[bool, str]:
    """
    Return (True, reason) when competing hypotheses cannot be discriminated.
    Uses error-node counts as a proxy for hypothesis scores.
    Returns (False, "") when one service clearly dominates.
    """
    graph, _ = _resolve_graph(session_id)
    services = _candidate_services(graph)

    if len(services) < 2:
        return False, ""

    scores = {svc: _error_count(graph, svc) for svc in services}
    ranked = sorted(scores.values(), reverse=True)

    if len(ranked) >= 2 and ranked[0] > 0:
        # Normalise
        top  = ranked[0]
        norm = {svc: cnt / top for svc, cnt in scores.items()}
        sorted_norm = sorted(norm.values(), reverse=True)
        gap = sorted_norm[0] - sorted_norm[1]
        if gap <= ABSTAIN_DELTA:
            top_svcs = [s for s, v in norm.items() if v >= sorted_norm[1]]
            reason = (
                f"Cannot discriminate between services: "
                f"{', '.join(sorted(top_svcs)[:3])}. "
                f"Score gap is only {gap:.3f} (threshold {ABSTAIN_DELTA}). "
                "Upload additional telemetry to break the tie."
            )
            return True, reason

    return False, ""


# ── Main policy ───────────────────────────────────────────────────────────────

def suggest_next(session_id: str) -> NextStepResult:
    """
    Determine the most valuable next investigation step for this session.
    """
    graph, _ = _resolve_graph(session_id)
    deficits = detect_deficits(session_id)

    # Check abstention first
    abstain, abstain_reason = should_abstain(session_id)

    if not deficits:
        # All modalities present for all services — check abstention only
        return NextStepResult(
            abstain=abstain,
            abstain_reason=abstain_reason,
            instruction=(
                abstain_reason if abstain else
                "All expected modalities are present. No further uploads required."
            ),
            deficits={},
        )

    # Pick highest-priority missing modality across the service with the most errors
    services = _candidate_services(graph)
    error_counts = {svc: _error_count(graph, svc) for svc in services if svc in deficits}

    if not error_counts:
        # Fall back to any service with deficits
        target_service = next(iter(deficits))
    else:
        target_service = max(error_counts, key=lambda s: error_counts[s])

    missing_for_target = deficits.get(target_service, [])
    suggested_modality = next(
        (m for m in MODALITY_PRIORITY if m in missing_for_target),
        missing_for_target[0] if missing_for_target else None,
    )

    if suggested_modality:
        label = MODALITY_LABELS.get(suggested_modality, suggested_modality)
        instruction = (
            f"Upload {label} for service '{target_service}'. "
            f"This service has the highest error activity "
            f"({error_counts.get(target_service, 0)} error events) "
            f"but is missing {label} telemetry."
        )
    else:
        instruction = "No specific suggestion — review existing evidence."

    logger.info(
        "Next step for session %s: modality=%s service=%s abstain=%s",
        session_id, suggested_modality, target_service, abstain,
    )
    return NextStepResult(
        abstain=abstain,
        abstain_reason=abstain_reason,
        suggested_modality=suggested_modality,
        suggested_service=target_service,
        instruction=instruction if not abstain else f"{instruction}\n\n⚠ {abstain_reason}",
        deficits=deficits,
    )

