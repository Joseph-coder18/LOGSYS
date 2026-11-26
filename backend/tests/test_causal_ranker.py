"""Tests for the Unsupervised Temporal-Causal Origin Ranker."""
from pathlib import Path

from models import Observation
from services.causal_ranker import rank_causal_origins


def _log(obs_id: str, service: str, ts: str, summary: str) -> Observation:
    return Observation(
        id=obs_id,
        modality="log",
        service=service,
        timestamp=ts,
        source_file="app.log",
        source_position=1,
        summary=summary,
        excerpt=summary,
    )


def test_unsupervised_ranker_localizes_unseen_multihop_origin():
    # Unseen failure mode (DNS stall on dns-resolver -> auth-service -> api-gateway)
    # with zero hardcoded playbook rules needed.
    events = [
        _log("e1", "dns-resolver", "2024-01-15T02:30:00+00:00", "DNS lookup error: upstream socket stalled"),
        _log("e2", "auth-service", "2024-01-15T02:30:08+00:00", "Upstream timeout resolving token issuer"),
        _log("e3", "api-gateway", "2024-01-15T02:30:12+00:00", "504 Gateway Timeout from auth-service"),
    ]
    deps = [
        {"caller": "api-gateway", "callee": "auth-service"},
        {"caller": "auth-service", "callee": "dns-resolver"},
    ]
    ranked = rank_causal_origins(Path("unused"), events=events, dependencies=deps)
    assert len(ranked) == 3
    assert ranked[0].service == "dns-resolver"
    assert ranked[0].downstream_affected == ["api-gateway", "auth-service"]
    assert ranked[0].score > ranked[1].score > ranked[2].score
    assert ranked[-1].service == "api-gateway"

