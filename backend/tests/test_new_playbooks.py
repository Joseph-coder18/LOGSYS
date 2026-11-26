"""Tests for Memory, Queue depth, and Error-rate playbooks in verification.py."""
from pathlib import Path
from unittest.mock import patch

from models import Observation
from services.verification import verify_incident


def _obs(obs_id: str, service: str, ts: str, modality: str, **kwargs) -> Observation:
    return Observation(
        id=obs_id,
        modality=modality,
        service=service,
        timestamp=ts,
        source_file="test.json" if modality == "metrics" else "test.log",
        source_position=1,
        summary=kwargs.pop("summary", f"{service} {modality} event"),
        excerpt=kwargs.pop("excerpt", f"{service} {modality} event"),
        **kwargs,
    )


def _run_verify(events: list[Observation], deps: list[dict] | None = None):
    if deps is None:
        deps = [{"caller": "api-gateway", "callee": "worker-svc"}]
    with (
        patch("services.verification.evidence_revision", return_value="rev-test"),
        patch("services.verification.load_verification_observations", return_value=events),
        patch("services.verification.load_dependencies", return_value=deps),
    ):
        return verify_incident(Path("unused"))


def test_memory_pressure_supported():
    events = [
        _obs("m1", "worker-svc", "2024-01-15T02:29:00+00:00", "metrics", metric_values={"memory_percent": 54.0}),
        _obs("m2", "worker-svc", "2024-01-15T02:30:00+00:00", "metrics", metric_values={"memory_percent": 92.5}),
        _obs("l1", "api-gateway", "2024-01-15T02:31:00+00:00", "log", summary="502 Bad Gateway from worker-svc"),
    ]
    res = _run_verify(events)
    assert res.decision == "supported"
    assert res.suspected_origin == "worker-svc"
    assert "Memory pressure" in res.root_cause
    assert res.confidence_score >= 0.90


def test_queue_depth_saturation_supported():
    events = [
        _obs("q1", "worker-svc", "2024-01-15T02:29:00+00:00", "metrics", metric_values={"queue_depth": 12}),
        _obs("q2", "worker-svc", "2024-01-15T02:30:00+00:00", "metrics", metric_values={"queue_depth": 240}),
        _obs("l1", "api-gateway", "2024-01-15T02:31:00+00:00", "log", summary="504 Gateway Timeout calling worker-svc"),
    ]
    res = _run_verify(events)
    assert res.decision == "supported"
    assert res.suspected_origin == "worker-svc"
    assert "Queue backlog saturation" in res.root_cause
    assert res.confidence_score >= 0.90


def test_error_rate_spike_supported():
    events = [
        _obs("e1", "worker-svc", "2024-01-15T02:29:00+00:00", "metrics", metric_values={"error_rate": 0.005}),
        _obs("e2", "worker-svc", "2024-01-15T02:30:00+00:00", "metrics", metric_values={"error_rate": 0.35}),
        _obs("l1", "api-gateway", "2024-01-15T02:31:00+00:00", "log", summary="503 Service Unavailable from worker-svc"),
    ]
    res = _run_verify(events)
    assert res.decision == "supported"
    assert res.suspected_origin == "worker-svc"
    assert "Error rate spike" in res.root_cause


def test_memory_missing_caller_returns_insufficient():
    events = [
        _obs("m1", "worker-svc", "2024-01-15T02:29:00+00:00", "metrics", metric_values={"memory_percent": 50.0}),
        _obs("m2", "worker-svc", "2024-01-15T02:30:00+00:00", "metrics", metric_values={"memory_percent": 91.0}),
    ]
    res = _run_verify(events, deps=[])
    assert res.decision == "insufficient_evidence"
    assert res.confidence == "low"
    assert res.confidence_score < 0.50

