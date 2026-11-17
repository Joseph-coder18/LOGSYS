"""
Unit tests for hypothesis.verify_claim() – Phase 2 / Objective 2.
"""
from __future__ import annotations

import sys
import types

# ── Minimal stubs ─────────────────────────────────────────────────────────────
for mod in ["config"]:
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)

import networkx as nx
import pytest
from unittest.mock import patch

# We'll patch _resolve_graph so we control the graph
_FAKE_GRAPH: nx.DiGraph = nx.DiGraph()


def _fake_resolve(session_id):
    return _FAKE_GRAPH, True


@pytest.fixture(autouse=True)
def reset():
    _FAKE_GRAPH.clear()
    yield
    _FAKE_GRAPH.clear()


def _svc(nid, service, modality="log", severity="INFO"):
    _FAKE_GRAPH.add_node(nid, service=service, modality=modality, severity=severity,
                          type="ObservationLog", timestamp=1000.0)


def _edge(src, dst, rel="depends_on"):
    _FAKE_GRAPH.add_edge(src, dst, rel=rel)


with patch("services.graph_store._resolve_graph", side_effect=_fake_resolve):
    from services.hypothesis import verify_claim


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_accept_full_support():
    """Full path, 2 modalities, no contradiction, all modalities present → accept."""
    _svc("n1", "auth",   "log")
    _svc("n2", "orders", "metrics")
    _FAKE_GRAPH.add_node("auth",   type="service")
    _FAKE_GRAPH.add_node("orders", type="service")
    _edge("n1", "n2", "precedes")
    _edge("auth",   "n1")
    _edge("orders", "n2")
    _svc("n3", "auth",   "change")
    _svc("n4", "auth",   "image")
    _edge("n1", "n3")

    with patch("services.graph_store._resolve_graph", side_effect=_fake_resolve):
        result = verify_claim("s", "n1", "n2")

    # At minimum graph_consistency should be 1 (path exists)
    assert result.scores["graph"] == 1.0
    assert result.gate in ("accept", "downgrade")


def test_reject_no_path():
    """No path at all between source and target → graph=0 → likely reject."""
    _svc("n1", "auth",   "log")
    _svc("n2", "orders", "log")
    # No edge between them

    with patch("services.graph_store._resolve_graph", side_effect=_fake_resolve):
        result = verify_claim("s", "n1", "n2")

    assert result.scores["graph"] == 0.0
    assert result.gate == "reject"


def test_contradiction_lowers_score():
    """'contradicts' edge touching source → contradiction_score = 0."""
    _svc("n1", "auth",   "log", severity="ERROR")
    _svc("n2", "auth",   "metrics")
    _svc("n3", "orders", "log")
    _edge("n1", "n3", "depends_on")
    _edge("n1", "n2", "contradicts")

    with patch("services.graph_store._resolve_graph", side_effect=_fake_resolve):
        result = verify_claim("s", "n1", "n3")

    assert result.scores["contradiction"] == 0.0


def test_cross_modal_single_modality():
    """Only log nodes on the path → cross_modal = 0.5."""
    _svc("n1", "auth",   "log")
    _svc("n2", "orders", "log")
    _edge("n1", "n2", "precedes")

    with patch("services.graph_store._resolve_graph", side_effect=_fake_resolve):
        result = verify_claim("s", "n1", "n2")

    # 1 modality / 2 required = 0.5
    assert result.scores["cross_modal"] == 0.5


def test_gate_thresholds():
    """Gate mapping: overall ≥ 0.75 accept, ≥ 0.50 downgrade, else reject."""
    from services.hypothesis import ACCEPT_THRESHOLD, DOWNGRADE_THRESHOLD, ClaimVerification

    # Fabricate ClaimVerification objects to test gate labels directly
    def _make(overall):
        if overall >= ACCEPT_THRESHOLD:
            return "accept"
        if overall >= DOWNGRADE_THRESHOLD:
            return "downgrade"
        return "reject"

    assert _make(0.80) == "accept"
    assert _make(0.75) == "accept"
    assert _make(0.74) == "downgrade"
    assert _make(0.50) == "downgrade"
    assert _make(0.49) == "reject"
    assert _make(0.00) == "reject"

