"""
LogMind-X – Counterfactual Remediation Validator (Objective 4)

suggest_remediation(session_id, causal_node_id) → RemediationResult

Logic:
  1. Load the session graph.
  2. Count *symptom nodes* (severity ERROR/CRITICAL) reachable from causal_node_id.
  3. Clone the graph and remove the causal node.
  4. Count symptom nodes reachable in the modified graph.
  5. Compute confidence = (before - after) / max(before, 1).
  6. Derive a human-readable RemediationAction from the node's type attr.

All actions are labelled as (Simulated) — no real deployment changes are made.
"""
from __future__ import annotations

import logging
import uuid

import networkx as nx

from models import RemediationAction, RemediationResult
from services.graph_store import _resolve_graph

logger = logging.getLogger("logmind.remediation")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _symptom_nodes(graph: nx.DiGraph) -> set[str]:
    """Return node IDs with ERROR or CRITICAL severity."""
    return {
        n for n, d in graph.nodes(data=True)
        if d.get("severity") in ("ERROR", "CRITICAL")
    }


def _reachable_symptoms(graph: nx.DiGraph, source: str, symptom_set: set[str]) -> int:
    """Count symptom nodes reachable from source."""
    if source not in graph:
        return 0
    try:
        reachable = nx.descendants(graph, source) | {source}
    except nx.NetworkXError:
        reachable = {source}
    return len(reachable & symptom_set)


def _build_action(node_id: str, attrs: dict, confidence: float) -> RemediationAction:
    """Derive a remediation action from the node's type attribute."""
    node_type = attrs.get("type", "").lower()
    service   = attrs.get("service", node_id)
    summary   = attrs.get("summary", "")[:120]

    if "deployment" in node_type or "deploy" in str(summary).lower():
        return RemediationAction(
            action_type="rollback",
            description=f"(Simulated) Roll back the deployment for service '{service}'.",
            reversible=True,
            rollback_plan=(
                f"Re-deploy the previous stable version of '{service}'. "
                "Verify health checks and error rate before marking complete."
            ),
            expected_signal_change=(
                f"Error rate for '{service}' should drop within 2–5 minutes of rollback."
            ),
            confidence=round(confidence, 3),
        )
    elif "config" in node_type or "configchange" in node_type:
        return RemediationAction(
            action_type="revert_config",
            description=f"(Simulated) Revert the configuration change for service '{service}'.",
            reversible=True,
            rollback_plan=(
                f"Restore the previous configuration snapshot for '{service}'. "
                "Restart the service and confirm metrics stabilise."
            ),
            expected_signal_change=(
                f"Latency and error counts for '{service}' should return to baseline."
            ),
            confidence=round(confidence, 3),
        )
    elif "feature" in str(summary).lower() or "flag" in str(summary).lower():
        return RemediationAction(
            action_type="disable_flag",
            description=f"(Simulated) Disable the feature flag implicated by '{service}'.",
            reversible=True,
            rollback_plan=(
                "Toggle the flag back to its previous state via the feature-flag console. "
                "No code deployment required."
            ),
            expected_signal_change=(
                "Impact should be immediate once the flag is disabled and cached."
            ),
            confidence=round(confidence, 3),
        )
    else:
        return RemediationAction(
            action_type="generic",
            description=(
                f"(Simulated) Investigate and mitigate the root cause at node '{node_id}' "
                f"(service: '{service}')."
            ),
            reversible=True,
            rollback_plan=(
                f"Record the current state of '{service}' before making changes. "
                "Roll back by restoring the previous configuration or deployment."
            ),
            expected_signal_change=(
                f"Downstream error events from '{service}' should decrease."
            ),
            confidence=round(confidence, 3),
        )


# ── Public API ────────────────────────────────────────────────────────────────

def suggest_remediation(session_id: str, causal_node_id: str) -> RemediationResult:
    """
    Simulate removing the causal node and return safe, reversible actions.
    """
    graph, _ = _resolve_graph(session_id)

    if causal_node_id not in graph:
        # Try to find a node whose service matches
        matches = [
            n for n, d in graph.nodes(data=True)
            if d.get("service") == causal_node_id
        ]
        if matches:
            causal_node_id = matches[0]
        else:
            logger.warning("Causal node '%s' not found in session %s graph.", causal_node_id, session_id)
            return RemediationResult(
                causal_node_id=causal_node_id,
                actions=[],
                symptom_before=0,
                symptom_after=0,
            )

    node_attrs = dict(graph.nodes[causal_node_id])
    symptoms   = _symptom_nodes(graph)

    # Count reachable symptoms BEFORE removal
    before = _reachable_symptoms(graph, causal_node_id, symptoms)

    # Clone graph and remove the causal node
    sim_graph = graph.copy()
    sim_graph.remove_node(causal_node_id)

    # Count reachable symptoms AFTER removal (from each neighbour)
    # Use total symptom count in the modified graph as proxy
    after = len(_symptom_nodes(sim_graph))

    # Confidence = fraction of previously-reachable symptoms that are now unreachable
    if before > 0:
        confidence = max(0.0, min(1.0, (before - after) / before))
    else:
        # No direct reachable symptoms — use global reduction as a weaker signal
        total_before = len(symptoms)
        total_after  = len(_symptom_nodes(sim_graph))
        confidence   = max(0.0, (total_before - total_after) / max(total_before, 1))

    action = _build_action(causal_node_id, node_attrs, confidence)

    logger.info(
        "Remediation for session %s node %s: before=%d after=%d confidence=%.3f",
        session_id, causal_node_id, before, after, confidence,
    )
    return RemediationResult(
        causal_node_id=causal_node_id,
        actions=[action],
        symptom_before=before,
        symptom_after=after,
    )

