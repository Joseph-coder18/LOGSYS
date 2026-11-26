"""Tests for the post-generation Claim Auditor (zero-hallucination guard)."""
from models import ApprovedClaim, Observation, ObservationCitation
from services.claim_auditor import audit_claims


def _obs(obs_id: str, service: str, ts: str, summary: str, metric_values: dict | None = None) -> Observation:
    return Observation(
        id=obs_id,
        modality="metrics",
        service=service,
        timestamp=ts,
        source_file="demo_metrics.json",
        source_position=11,
        summary=summary,
        excerpt=summary,
        metric_values=metric_values or {},
    )


def test_audit_passes_for_exact_numbers_timestamps_and_service():
    obs = _obs(
        "o1",
        "user-db",
        "2024-01-15T02:31:00+00:00",
        "cpu_percent=72.4, db_latency_ms=30045",
        {"cpu_percent": 72.4, "db_latency_ms": 30045},
    )
    claim = ApprovedClaim(
        text="user-db latency peaked at 30,045 ms at 02:31:00 UTC.",
        citations=[ObservationCitation(observation_id="o1", label="demo_metrics.json · record 11")],
    )
    audited, score = audit_claims([claim], {"o1": obs}, {"user-db", "auth-service"})
    assert score == 1.0
    assert audited[0].audit_status == "verified"
    assert audited[0].audit_warnings == []


def test_audit_flags_hallucinated_number_and_timestamp():
    obs = _obs(
        "o1",
        "user-db",
        "2024-01-15T02:31:00+00:00",
        "cpu_percent=72.4, db_latency_ms=30045",
        {"cpu_percent": 72.4, "db_latency_ms": 30045},
    )
    # Classic LLM hallucination: 30,002 ms instead of 30,045 ms, and wrong timestamp 02:39:00
    bad_claim = ApprovedClaim(
        text="user-db latency peaked at 30,002 ms at 02:39:00 UTC.",
        citations=[ObservationCitation(observation_id="o1", label="demo_metrics.json · record 11")],
    )
    good_claim = ApprovedClaim(
        text="user-db CPU was 72.4% at 02:31:00 UTC.",
        citations=[ObservationCitation(observation_id="o1", label="demo_metrics.json · record 11")],
    )
    audited, score = audit_claims([bad_claim, good_claim], {"o1": obs}, {"user-db"})
    assert score == 0.5
    assert audited[0].audit_status == "flagged"
    assert any("30,002" in w for w in audited[0].audit_warnings)
    assert any("02:39:00" in w for w in audited[0].audit_warnings)
    assert audited[1].audit_status == "verified"


def test_audit_flags_ungrounded_service_mention():
    obs = _obs("o1", "user-db", "2024-01-15T02:31:00+00:00", "db_latency_ms=30045", {"db_latency_ms": 30045})
    claim = ApprovedClaim(
        text="auth-service caused db latency 30045.",
        citations=[ObservationCitation(observation_id="o1", label="demo_metrics.json · record 11")],
    )
    audited, score = audit_claims([claim], {"o1": obs}, {"user-db", "auth-service"})
    assert score == 0.0
    assert audited[0].audit_status == "flagged"
    assert any("auth-service" in w for w in audited[0].audit_warnings)

