"""Evaluate a pool-limit reduction against recorded telemetry and topology."""
from datetime import datetime, timedelta
from pathlib import Path

from models import EvidenceItem, Observation, VerifiedRCA
from services.incident_context import load_dependencies
from services.observations import load_verification_observations

WINDOW = timedelta(minutes=5)
CPU_THRESHOLD = 80


def _within(events: list[Observation], anchor: Observation) -> list[Observation]:
    start = datetime.fromisoformat(anchor.timestamp)
    end = start + WINDOW
    return [event for event in events if start < datetime.fromisoformat(event.timestamp) <= end]


def _evidence(event: Observation, why: str) -> EvidenceItem:
    return EvidenceItem(
        type=event.modality,
        source=event.source_file,
        snippet=event.summary,
        observation_id=event.id,
        source_position=event.source_position,
        why=why,
    )


def _check_reduction(
    change: Observation, events: list[Observation], callers: dict[str, list[str]],
) -> VerifiedRCA:
    service = change.service
    limits = change.pool_limit_change
    metrics = [event for event in events if event.modality == "metrics" and event.service == service]
    baseline_start = (datetime.fromisoformat(change.timestamp) - WINDOW).isoformat(timespec="microseconds")
    baseline = next((event for event in reversed(metrics)
                     if baseline_start <= event.timestamp < change.timestamp
                     and event.metric_values.get("db_connections_limit") == limits.before
                     and 0 <= event.metric_values.get("db_connections_active", -1) < limits.before
                     and 0 <= event.metric_values.get("db_latency_ms", -1)), None)
    subsequent = _within([event for event in events if event.service == service], change)
    next_change = next((event for event in subsequent if event.pool_limit_change), None)
    if next_change:
        subsequent = [event for event in subsequent if event.timestamp < next_change.timestamp]

    saturation = next((event for event in subsequent
                       if event.modality == "metrics"
                       and event.metric_values.get("db_connections_limit") == limits.after
                       and event.metric_values.get("db_connections_active") == limits.after
                       and baseline and event.metric_values.get("db_latency_ms", 0)
                       >= max(100, 2 * baseline.metric_values["db_latency_ms"])), None)

    evidence = []
    if baseline:
        evidence.append(_evidence(baseline, "Shows the previous pool limit and lower database latency."))
    evidence.append(_evidence(change, "Records the exact reduction in the connection pool limit."))
    trace = [{"agent": "Verification", "action": "Check pool telemetry", "result":
              f"Pre-change baseline {'found' if baseline else 'missing'}. Post-change saturation with higher latency {'found' if saturation else 'missing'}."}]
    if saturation:
        evidence.append(_evidence(saturation, "Shows the pool at its new limit as database latency rises."))

    # Require a database-connection error on a declared direct caller after saturation.
    errors = _within([event for event in events if event.modality == "log"], saturation) if saturation else []
    if next_change:
        errors = [event for event in errors if event.timestamp < next_change.timestamp]
    timeout = next((event for event in errors if event.service in callers.get(service, ())
                    and ("timeout" in event.summary.lower() or "timed out" in event.summary.lower())
                    and ("connection" in event.summary.lower() or "database" in event.summary.lower())), None)
    if timeout:
        evidence.append(_evidence(timeout, "A declared caller reported a database connection timeout after saturation."))
    gateway = next((event for event in errors if timeout and event.timestamp > timeout.timestamp
                    and event.service in callers.get(timeout.service, ()) and "504" in event.summary
                    and timeout.service.lower() in event.summary.lower()), None)
    if gateway:
        evidence.append(_evidence(gateway, "The caller’s caller subsequently reported a 504 mentioning the affected upstream service."))
    trace.append({"agent": "Verification", "action": "Follow declared callers", "result":
                  f"Ordered database-connection timeout {'found on ' + timeout.service if timeout else 'missing on a declared direct caller'}."})

    if not (baseline and saturation and timeout):
        return VerifiedRCA(
            decision="insufficient_evidence", root_cause="Cause unresolved",
            summary="The pool limit reduction is recorded. Verification requires a recent pre-change baseline, saturation with rising latency, and a subsequent database-connection timeout on a declared caller.",
            evidence=evidence, agent_trace=trace,
        )

    cpu_window_end = (datetime.fromisoformat(saturation.timestamp) + WINDOW).isoformat(timespec="microseconds")
    cpu = next((event for event in metrics if baseline.timestamp <= event.timestamp <= cpu_window_end
                and event.metric_values.get("cpu_percent", 0) >= CPU_THRESHOLD), None)
    if cpu:
        timing = "before or alongside" if cpu.timestamp <= saturation.timestamp else "after"
        trace.append({"agent": "Verification", "action": "Check CPU as an alternative", "result":
                      f"CPU reached {CPU_THRESHOLD}% {timing} the first observed pool saturation."})
        if cpu.timestamp <= saturation.timestamp:
            evidence.append(_evidence(cpu, "Early CPU pressure leaves a competing explanation open."))
            return VerifiedRCA(
                decision="ambiguous", root_cause="Competing explanations",
                summary="Pool saturation and CPU pressure both align with the onset. Review CPU telemetry and connection acquisition waits before choosing an origin.",
                evidence=evidence, agent_trace=trace,
            )

    affected = list(dict.fromkeys([service, timeout.service] + ([gateway.service] if gateway else [])))
    propagation = f"{timeout.service} then reported a connection timeout"
    if gateway:
        propagation += f", and {gateway.service} returned a 504"
    return VerifiedRCA(
        decision="supported", suspected_origin=service, affected_services=affected,
        root_cause=f"Connection pool limit reduction on {service}",
        summary=f"The pool limit dropped from {limits.before} to {limits.after}. The pool reached the new limit as database latency rose. {propagation}.",
        evidence=evidence,
        recommendations=[
            f"Mitigation · Review restoring the previous pool limit of {limits.before} on {service}. Confirm database capacity and monitor connection waits and errors after the change.",
            "Prevention · Alert on active connections relative to the configured limit and track acquisition wait time.",
        ],
        agent_trace=trace,
    )


def verify_incident(session_dir: Path) -> VerifiedRCA:
    events = load_verification_observations(session_dir)
    callers = {}
    for edge in load_dependencies(session_dir):
        callers.setdefault(edge["callee"], []).append(edge["caller"])
    checks = [
        _check_reduction(event, events, callers) for event in events
        if event.change_type == "config_change" and event.pool_limit_change
        and event.pool_limit_change.after < event.pool_limit_change.before
    ]
    viable = [check for check in checks if check.decision != "insufficient_evidence"]
    if len(viable) > 1:
        result = VerifiedRCA(
            decision="ambiguous", root_cause="Multiple changes match the evidence",
            summary="More than one pool limit reduction has a matching signal chain. Narrow the incident to one change window to identify an origin.",
            evidence=[item for check in viable[:2] for item in check.evidence if item.type == "change"],
        )
    elif viable or checks:
        result = (viable or checks)[-1]
    else:
        result = VerifiedRCA(
            decision="insufficient_evidence", root_cause="Cause unresolved",
            summary="Upload a dated pool limit reduction, service-level pool metrics, and error logs from its declared callers to run this check.",
        )
    result.agent_trace.insert(0, {"agent": "Verification", "action": "Read incident evidence", "result":
                                 f"Loaded {len(events)} dated service observations and found {len(checks)} pool-limit reductions to check."})
    return result
