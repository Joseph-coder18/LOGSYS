"""
Unit tests for temporal_edges.build_temporal_edges() – Phase 1 / Objective 1.

Runs against an in-memory graph (session_id=None) using monkeypatching so no
filesystem or Pinecone access is required.
"""
from __future__ import annotations

import sys
import os
import types

# ── Minimal stubs so imports don't fail without a full app environment ────────
for mod in ["config", "models"]:
    if mod not in sys.modules:
        m = types.ModuleType(mod)
        m.LOG_CHUNK_SIZE    = 20       # type: ignore[attr-defined]
        m.LOG_CHUNK_OVERLAP = 5        # type: ignore[attr-defined]
        sys.modules[mod] = m

import networkx as nx
import pytest

# Patch _resolve_graph and _persist so temporal_edges works without session storage
import services.graph_store as _gs

_FAKE_GRAPH: nx.DiGraph = nx.DiGraph()


def _fake_resolve(session_id):
    return _FAKE_GRAPH, session_id is not None


def _fake_persist(graph, session_id):
    pass  # no-op


_gs._resolve_graph = _fake_resolve  # type: ignore[attr-defined]
_gs._persist       = _fake_persist  # type: ignore[attr-defined]

from services.temporal_edges import build_temporal_edges, SUPPORT_WINDOW_S, CONTRADICT_WINDOW_S


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_graph():
    """Clear the fake graph before each test."""
    _FAKE_GRAPH.clear()
    yield
    _FAKE_GRAPH.clear()


def _add_obs(node_id, service, modality, ts, severity="INFO", corr_id=None, req_id=None):
    _FAKE_GRAPH.add_node(
        node_id,
        type="ObservationLog",
        service=service,
        modality=modality,
        timestamp=float(ts),
        severity=severity,
        correlation_id=corr_id,
        request_id=req_id,
    )


# ── Tests: precedes ───────────────────────────────────────────────────────────

def test_precedes_same_service():
    """Two observations on the same service → one 'precedes' edge."""
    _add_obs("n1", "auth", "log", 1000)
    _add_obs("n2", "auth", "log", 1005)
    counts = build_temporal_edges("test-session")
    assert counts["precedes"] == 1
    assert _FAKE_GRAPH.has_edge("n1", "n2")
    assert _FAKE_GRAPH["n1"]["n2"]["rel"] == "precedes"


def test_precedes_ordering():
    """Three events on same service → two 'precedes' edges in time order."""
    _add_obs("n1", "orders", "log", 100)
    _add_obs("n2", "orders", "log", 200)
    _add_obs("n3", "orders", "log", 300)
    counts = build_temporal_edges("test-session")
    assert counts["precedes"] == 2
    assert _FAKE_GRAPH.has_edge("n1", "n2")
    assert _FAKE_GRAPH.has_edge("n2", "n3")
    assert not _FAKE_GRAPH.has_edge("n1", "n3")  # only consecutive


def test_precedes_different_services():
    """Events on DIFFERENT services → no cross-service 'precedes'."""
    _add_obs("n1", "auth",   "log", 100)
    _add_obs("n2", "orders", "log", 200)
    counts = build_temporal_edges("test-session")
    assert counts["precedes"] == 0


# ── Tests: supports ───────────────────────────────────────────────────────────

def test_supports_cross_modal_same_corr_id():
    """Log + metric with same correlation_id within window → 'supports' edge."""
    _add_obs("n1", "auth", "log",     1000, corr_id="CORR-1")
    _add_obs("n2", "auth", "metrics", 1010, corr_id="CORR-1")
    counts = build_temporal_edges("test-session")
    assert counts["supports"] == 1
    # Edge should go earlier → later
    assert _FAKE_GRAPH.has_edge("n1", "n2")
    assert _FAKE_GRAPH["n1"]["n2"]["rel"] == "supports"


def test_supports_outside_window():
    """Same correlation_id but gap > SUPPORT_WINDOW_S → no 'supports' edge."""
    _add_obs("n1", "auth", "log",     1000, corr_id="CORR-2")
    _add_obs("n2", "auth", "metrics", 1000 + SUPPORT_WINDOW_S + 1, corr_id="CORR-2")
    counts = build_temporal_edges("test-session")
    assert counts["supports"] == 0


def test_supports_same_modality_skipped():
    """Two log nodes with same corr_id → NOT a 'supports' edge (same modality)."""
    _add_obs("n1", "auth", "log", 1000, corr_id="CORR-3")
    _add_obs("n2", "auth", "log", 1005, corr_id="CORR-3")
    counts = build_temporal_edges("test-session")
    assert counts["supports"] == 0


# ── Tests: contradicts ────────────────────────────────────────────────────────

def test_contradicts_error_log_vs_normal_metric():
    """ERROR log + non-anomalous metric on same service → 'contradicts' edge."""
    _add_obs("n1", "auth", "log",     1000, severity="ERROR")
    _FAKE_GRAPH.add_node("m1", type="ObservationLog", service="auth",
                          modality="metrics", timestamp=1020.0,
                          severity="INFO", anomaly=False)
    counts = build_temporal_edges("test-session")
    assert counts["contradicts"] == 1
    assert _FAKE_GRAPH.has_edge("n1", "m1")
    assert _FAKE_GRAPH["n1"]["m1"]["rel"] == "contradicts"


def test_contradicts_outside_window():
    """ERROR log and normal metric more than CONTRADICT_WINDOW_S apart → no edge."""
    _add_obs("n1", "auth", "log", 1000, severity="ERROR")
    _FAKE_GRAPH.add_node("m1", type="ObservationLog", service="auth",
                          modality="metrics",
                          timestamp=1000 + CONTRADICT_WINDOW_S + 1,
                          severity="INFO", anomaly=False)
    counts = build_temporal_edges("test-session")
    assert counts["contradicts"] == 0


def test_contradicts_different_services():
    """ERROR log on 'auth' + normal metric on 'orders' → no 'contradicts'."""
    _add_obs("n1", "auth",   "log",     1000, severity="ERROR")
    _FAKE_GRAPH.add_node("m1", type="ObservationLog", service="orders",
                          modality="metrics", timestamp=1010.0,
                          severity="INFO", anomaly=False)
    counts = build_temporal_edges("test-session")
    assert counts["contradicts"] == 0


# ── Tests: empty graph ────────────────────────────────────────────────────────

def test_empty_graph_returns_zero_counts():
    counts = build_temporal_edges("test-session")
    assert all(v == 0 for v in counts.values())

