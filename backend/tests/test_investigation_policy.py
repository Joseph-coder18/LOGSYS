"""
Unit tests for investigation_policy – Phase 3 / Objective 3.
"""
from __future__ import annotations

import networkx as nx
import pytest
from unittest.mock import patch

from services.investigation_policy import detect_deficits, should_abstain, suggest_next

_FAKE_GRAPH: nx.DiGraph = nx.DiGraph()


def _fake_resolve(session_id):
    return _FAKE_GRAPH, True


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    _FAKE_GRAPH.clear()
    monkeypatch.setattr("services.investigation_policy._resolve_graph", _fake_resolve)
    yield
    _FAKE_GRAPH.clear()


def _add(nid, service, modality, severity="INFO"):
    _FAKE_GRAPH.add_node(nid, service=service, modality=modality,
                          severity=severity, type="ObservationLog", timestamp=1000.0)


def _call_suggest():
    with patch("services.graph_store._resolve_graph", side_effect=_fake_resolve):
        return suggest_next("s")


def _call_deficits():
    with patch("services.graph_store._resolve_graph", side_effect=_fake_resolve):
        return detect_deficits("s")


def _call_abstain():
    with patch("services.graph_store._resolve_graph", side_effect=_fake_resolve):
        return should_abstain("s")


# ── Deficit detection ──────────────────────────────────────────────────────────

def test_deficit_logs_only():
    """Only log modality present → metrics, change, image all missing."""
    _add("n1", "auth", "log")
    deficits = _call_deficits()
    assert "auth" in deficits
    assert "metrics" in deficits["auth"]
    assert "change"  in deficits["auth"]
    assert "image"   in deficits["auth"]
    assert "log"     not in deficits["auth"]


def test_no_deficit_when_all_present():
    """All four modalities present → no deficits."""
    for mod in ["log", "metrics", "change", "image"]:
        _add(f"n-{mod}", "auth", mod)
    deficits = _call_deficits()
    assert deficits.get("auth", []) == []


def test_deficit_metrics_only_missing():
    """log + change + image present → only metrics missing."""
    for mod in ["log", "change", "image"]:
        _add(f"n-{mod}", "auth", mod)
    deficits = _call_deficits()
    assert deficits.get("auth") == ["metrics"]


# ── Abstention ────────────────────────────────────────────────────────────────

def test_abstain_equal_scores():
    """Two services with identical error counts → abstain."""
    _add("n1", "auth",   "log", severity="ERROR")
    _add("n2", "orders", "log", severity="ERROR")
    abstain, reason = _call_abstain()
    assert abstain is True
    assert len(reason) > 0


def test_no_abstain_clear_winner():
    """One service has many errors, the other has none → no abstention."""
    for i in range(5):
        _add(f"n{i}", "auth", "log", severity="ERROR")
    _add("m1", "orders", "log", severity="INFO")
    abstain, _ = _call_abstain()
    assert abstain is False


def test_no_abstain_single_service():
    """Only one service → abstention requires at least 2 candidates."""
    _add("n1", "auth", "log", severity="ERROR")
    abstain, _ = _call_abstain()
    assert abstain is False


# ── suggest_next ──────────────────────────────────────────────────────────────

def test_suggest_next_returns_metrics_first():
    """When only logs are uploaded, metrics should be suggested first."""
    _add("n1", "auth", "log", severity="ERROR")
    result = _call_suggest()
    assert result.suggested_modality == "metrics"
    assert result.suggested_service  == "auth"
    assert result.abstain is False


def test_suggest_next_no_deficits():
    """All modalities present → no suggestion, no abstention."""
    for mod in ["log", "metrics", "change", "image"]:
        _add(f"n-{mod}", "auth", mod)
    result = _call_suggest()
    assert result.suggested_modality is None

