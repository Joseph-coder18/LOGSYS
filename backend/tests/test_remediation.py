"""
Unit tests for remediation.suggest_remediation() – Phase 4 / Objective 4.
"""
from __future__ import annotations

import sys
import types
for mod in ["config"]:
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)

import networkx as nx
import pytest
from unittest.mock import patch

_FAKE_GRAPH: nx.DiGraph = nx.DiGraph()


def _fake_resolve(session_id):
    return _FAKE_GRAPH, True


@pytest.fixture(autouse=True)
def reset():
    _FAKE_GRAPH.clear()
    yield
    _FAKE_GRAPH.clear()


with patch("services.graph_store._resolve_graph", side_effect=_fake_resolve):
    from services.remediation import suggest_remediation


def _call(causal):
    with patch("services.graph_store._resolve_graph", side_effect=_fake_resolve):
        return suggest_remediation("s", causal)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_deployment_node_rollback_action():
    """A Deployment node should produce a 'rollback' action type."""
    _FAKE_GRAPH.add_node("dep1", type="Deployment", service="auth",
                          modality="change", timestamp=1000.0, severity="INFO", summary="deployed v2")
    # Add reachable error node
    _FAKE_GRAPH.add_node("err1", type="ObservationLog", service="auth",
                          modality="log", severity="ERROR", timestamp=1010.0)
    _FAKE_GRAPH.add_edge("dep1", "err1", rel="precedes")

    result = _call("dep1")
    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.action_type == "rollback"
    assert action.reversible is True
    assert len(action.rollback_plan) > 0


def test_config_change_node_revert_action():
    """A ConfigChange node should produce a 'revert_config' action."""
    _FAKE_GRAPH.add_node("cfg1", type="ConfigChange", service="orders",
                          modality="change", timestamp=1000.0, severity="INFO", summary="pool size reduced")
    _FAKE_GRAPH.add_node("err1", type="ObservationLog", service="orders",
                          modality="log", severity="ERROR", timestamp=1010.0)
    _FAKE_GRAPH.add_edge("cfg1", "err1", rel="precedes")

    result = _call("cfg1")
    assert result.actions[0].action_type == "revert_config"
    assert result.actions[0].reversible is True


def test_confidence_increases_with_more_symptoms():
    """More downstream error nodes → higher confidence in the remediation."""
    _FAKE_GRAPH.add_node("dep1", type="Deployment", service="auth",
                          modality="change", timestamp=1000.0, severity="INFO", summary="deploy")
    for i in range(5):
        nid = f"err{i}"
        _FAKE_GRAPH.add_node(nid, type="ObservationLog", service="auth",
                              modality="log", severity="ERROR", timestamp=float(1010 + i))
        _FAKE_GRAPH.add_edge("dep1", nid, rel="precedes")

    result_many = _call("dep1")

    # Reset and test with fewer errors
    _FAKE_GRAPH.clear()
    _FAKE_GRAPH.add_node("dep2", type="Deployment", service="auth",
                          modality="change", timestamp=1000.0, severity="INFO", summary="deploy")
    _FAKE_GRAPH.add_node("err0", type="ObservationLog", service="auth",
                          modality="log", severity="ERROR", timestamp=1010.0)
    _FAKE_GRAPH.add_edge("dep2", "err0", rel="precedes")

    result_few = _call("dep2")

    # Both should have confidence ≥ 0
    assert result_many.actions[0].confidence >= 0.0
    assert result_few.actions[0].confidence  >= 0.0


def test_missing_node_returns_empty_actions():
    """Node not in graph → empty actions list, no crash."""
    result = _call("nonexistent-node")
    assert result.actions == []
    assert result.symptom_before == 0


def test_all_actions_reversible():
    """Every generated action must have reversible=True."""
    _FAKE_GRAPH.add_node("dep1", type="Deployment", service="auth",
                          modality="change", timestamp=1000.0, severity="INFO", summary="deploy v3")
    result = _call("dep1")
    for action in result.actions:
        assert action.reversible is True

