"""
Unit tests for temporal_edges.build_temporal_edges() – Phase 1 / Objective 1.

Runs against an in-memory graph (session_id=None) using monkeypatching so no
filesystem or Pinecone access is required.
"""
from __future__ import annotations

import networkx as nx
import pytest
from services.temporal_edges import build_temporal_edges, SUPPORT_WINDOW_S, CONTRADICT_WINDOW_S

_FAKE_GRAPH: nx.DiGraph = nx.DiGraph()


def _fake_resolve(session_id):
    return _FAKE_GRAPH, session_id is not None


def _fake_persist(graph, session_id):
    pass  # no-op


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_graph(monkeypatch):
    """Clear the fake graph before each test."""
    _FAKE_GRAPH.clear()
    monkeypatch.setattr("services.temporal_edges._resolve_graph", _fake_resolve)
    monkeypatch.setattr("services.temporal_edges._persist", _fake_persist)
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

def test_normal_metric_does_not_refute_an_unrelated_error():
    """A normal measurement does not contradict an unrelated error message."""
    _add_obs("n1", "auth", "log",     1000, severity="ERROR")
    _FAKE_GRAPH.add_node("m1", type="ObservationLog", service="auth",
                          modality="metrics", timestamp=1020.0,
                          severity="INFO", anomaly=False)
    counts = build_temporal_edges("test-session")
    assert counts["contradicts"] == 0


def test_conflicting_values_require_the_same_signal_and_instant():
    _add_obs("n1", "auth", "metrics", 1000)
    _add_obs("n2", "auth", "metrics", 1000)
    _FAKE_GRAPH.nodes["n1"]["metric_values"] = {"cpu_percent": 90}
    _FAKE_GRAPH.nodes["n2"]["metric_values"] = {"cpu_percent": 30}
    first = build_temporal_edges("test-session")
    assert first["contradicts"] == 1
    assert first["precedes"] == 0
    assert build_temporal_edges("test-session") == first
    _FAKE_GRAPH.nodes["n2"]["metric_values"] = {"db_latency_ms": 30}
    assert build_temporal_edges("test-session")["contradicts"] == 0


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


# ── Tests: empty graph & propagates_to ────────────────────────────────────────

def test_dependency_stays_structural_without_observed_request_flow():
    """A timestamp on a dependency cannot establish observed propagation."""
    _FAKE_GRAPH.add_node("auth", type="service", service="auth")
    _FAKE_GRAPH.add_node("orders", type="service", service="orders")
    _FAKE_GRAPH.add_edge("auth", "orders", rel="depends_on", timestamp=1000.0)

    counts = build_temporal_edges("test-session")
    assert counts["propagates_to"] == 0
    assert _FAKE_GRAPH["auth"]["orders"]["rel"] == "depends_on"


def test_observed_target_and_shared_request_link_events():
    _add_obs("caller", "auth", "log", 1000, req_id="r1")
    _add_obs("callee", "orders", "log", 1005, req_id="r1")
    _FAKE_GRAPH.nodes["caller"]["target_service"] = "orders"
    assert build_temporal_edges("test-session")["propagates_to"] == 1


def test_unknown_nonfinite_and_equal_times_cannot_establish_precedence():
    for node, timestamp in (("unknown", None), ("nan", float("nan")), ("infinite", float("inf"))):
        _FAKE_GRAPH.add_node(node, modality="log", service="auth", timestamp=timestamp)
    _add_obs("known1", "auth", "log", 1000)
    _add_obs("known2", "auth", "log", 1000)
    assert build_temporal_edges("test-session")["precedes"] == 0


def test_late_event_replaces_stale_adjacency():
    _add_obs("first", "auth", "log", 1000)
    _add_obs("last", "auth", "log", 1020)
    build_temporal_edges("test-session")
    _add_obs("middle", "auth", "log", 1010)
    assert build_temporal_edges("test-session")["precedes"] == 2
    assert not _FAKE_GRAPH.has_edge("first", "last")
    assert _FAKE_GRAPH.has_edge("first", "middle")
    assert _FAKE_GRAPH.has_edge("middle", "last")


def test_empty_graph_returns_zero_counts():
    counts = build_temporal_edges("test-session")
    assert all(v == 0 for v in counts.values())

