"""Compare pool-limit and CPU hypotheses against session evidence."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from models import EvidenceItem, Hypothesis, HypothesisCheck, NextCheck, Observation, VerifiedRCA
from services.incident_context import load_dependencies
from services.observations import evidence_revision, load_verification_observations

WINDOW = timedelta(minutes=5)
CPU_THRESHOLD = 80
MEMORY_THRESHOLD = 85
QUEUE_DEPTH_THRESHOLD = 100
ERROR_RATE_THRESHOLD = 0.05


@dataclass(frozen=True)
class MetricPlaybook:
    metric_key: str
    threshold: float
    hypothesis_label: str
    root_cause_prefix: str
    baseline_criterion: str
    baseline_found: str
    baseline_missing: str
    breach_criterion: str
    breach_found: str
    onset_criterion: str
    onset_found: str
    onset_missing: str
    onset_contradict: str
    downstream_criterion: str
    downstream_found: str
    downstream_missing: str
    spike_why: str
    baseline_why: str
    unsupported_summary: str
    next_check_supported: str
    next_check_baseline: str
    next_check_downstream: str
    recommendation_mitigation: str
    recommendation_prevention: str
    unit_suffix: str = "%"
    check_same_time_saturation: bool = False


_PLAYBOOKS: tuple[MetricPlaybook, ...] = (
    MetricPlaybook(
        metric_key="cpu_percent",
        threshold=CPU_THRESHOLD,
        hypothesis_label="CPU pressure",
        root_cause_prefix="CPU pressure",
        baseline_criterion="Pre-spike CPU baseline",
        baseline_found="CPU was below the rule threshold before the spike.",
        baseline_missing="A recent lower CPU sample is missing.",
        breach_criterion="Elevated CPU",
        breach_found=f"CPU reached at least {CPU_THRESHOLD}%.",
        onset_criterion="CPU onset precedes impact",
        onset_found="CPU pressure was recorded before the caller error.",
        onset_missing="A subsequent caller error is missing.",
        onset_contradict="Pool saturation or a caller error was already recorded before the observed CPU spike.",
        downstream_criterion="Downstream error",
        downstream_found="A declared caller subsequently reported an error.",
        downstream_missing="No error from a declared caller followed this CPU spike in the check window.",
        spike_why="Records the first elevated CPU measurement in this episode.",
        baseline_why="Shows CPU usage before the spike.",
        unsupported_summary="The recorded CPU spike does not meet all timing and dependency checks.",
        next_check_supported="Check CPU usage, request latency, and caller errors after addressing the pressure.",
        next_check_baseline="Check CPU samples before the first error or saturation to establish when CPU pressure started.",
        next_check_downstream="Check the dependency manifest and look for a caller error after the CPU spike.",
        recommendation_mitigation="Mitigation · Inspect hot CPU threads, recent releases, or autoscaling limits on {service}.",
        recommendation_prevention="Prevention · Alert on sustained CPU utilization above 80% before caller timeouts occur.",
    ),
    MetricPlaybook(
        metric_key="memory_percent",
        threshold=MEMORY_THRESHOLD,
        hypothesis_label="Memory pressure",
        root_cause_prefix="Memory pressure",
        baseline_criterion="Pre-spike memory baseline",
        baseline_found="Memory usage was below the rule threshold before the spike.",
        baseline_missing="A recent lower memory sample is missing.",
        breach_criterion="Elevated memory",
        breach_found=f"Memory reached at least {MEMORY_THRESHOLD}%.",
        onset_criterion="Memory onset precedes impact",
        onset_found="Memory pressure was recorded before the caller error.",
        onset_missing="A subsequent caller error is missing.",
        onset_contradict="Pool saturation or a caller error was already recorded before the observed memory spike.",
        downstream_criterion="Downstream error",
        downstream_found="A declared caller subsequently reported an error.",
        downstream_missing="No error from a declared caller followed this memory spike in the check window.",
        spike_why="Records the first elevated memory measurement in this episode.",
        baseline_why="Shows memory usage before the spike.",
        unsupported_summary="The recorded memory spike does not meet all timing and dependency checks.",
        next_check_supported="Check memory utilization, GC pauses/OOM kills, and caller errors after mitigation.",
        next_check_baseline="Check memory samples before the first error or saturation to establish when memory pressure started.",
        next_check_downstream="Check the dependency manifest and look for a caller error or OOM event after the memory spike.",
        recommendation_mitigation="Mitigation · Inspect heap growth, OOM kills, or memory limits on {service} and restart/scale affected instances.",
        recommendation_prevention="Prevention · Alert on memory utilization trending above 85% and track allocation rate.",
    ),
    MetricPlaybook(
        metric_key="queue_depth",
        threshold=QUEUE_DEPTH_THRESHOLD,
        hypothesis_label="Queue backlog saturation",
        root_cause_prefix="Queue backlog saturation",
        baseline_criterion="Pre-spike queue baseline",
        baseline_found="Queue depth was below the saturation threshold before the spike.",
        baseline_missing="A recent lower queue depth sample is missing.",
        breach_criterion="Elevated queue depth",
        breach_found=f"Queue depth reached at least {QUEUE_DEPTH_THRESHOLD}.",
        onset_criterion="Queue buildup precedes impact",
        onset_found="Queue backlog was recorded before the caller error.",
        onset_missing="A subsequent caller error is missing.",
        onset_contradict="Pool saturation or a caller error was already recorded before the observed queue buildup.",
        downstream_criterion="Downstream error",
        downstream_found="A declared caller subsequently reported an error.",
        downstream_missing="No error from a declared caller followed this queue backlog in the check window.",
        spike_why="Records the first saturated queue depth measurement in this episode.",
        baseline_why="Shows normal queue depth before the backlog.",
        unsupported_summary="The recorded queue backlog does not meet all timing and dependency checks.",
        next_check_supported="Check queue depth drain rate, consumer lag, and caller errors after mitigation.",
        next_check_baseline="Check queue depth samples before the first error to establish when the backlog started.",
        next_check_downstream="Check the dependency manifest and look for a caller timeout or error after the queue buildup.",
        recommendation_mitigation="Mitigation · Scale workers or clear stalled consumers draining the queue on {service}.",
        recommendation_prevention="Prevention · Alert on queue depth growth rate and consumer lag.",
        unit_suffix="",
    ),
    MetricPlaybook(
        metric_key="error_rate",
        threshold=ERROR_RATE_THRESHOLD,
        hypothesis_label="Error rate spike",
        root_cause_prefix="Error rate spike",
        baseline_criterion="Pre-spike error-rate baseline",
        baseline_found="Error rate was below the rule threshold before the spike.",
        baseline_missing="A recent lower error-rate sample is missing.",
        breach_criterion="Elevated error rate",
        breach_found=f"Error rate reached at least {ERROR_RATE_THRESHOLD}.",
        onset_criterion="Error spike precedes downstream impact",
        onset_found="The service error-rate spike was recorded before the caller error.",
        onset_missing="A subsequent caller error is missing.",
        onset_contradict="An upstream pool change, saturation, or caller error was already recorded before this error-rate spike.",
        downstream_criterion="Downstream error",
        downstream_found="A declared caller subsequently reported an error.",
        downstream_missing="No error from a declared caller followed this error-rate spike in the check window.",
        spike_why="Records the first elevated error-rate measurement in this episode.",
        baseline_why="Shows error rate before the spike.",
        unsupported_summary="The recorded error-rate spike does not meet all timing and dependency checks.",
        next_check_supported="Check service 5xx error rates and caller errors after mitigation.",
        next_check_baseline="Check error-rate samples before the first caller error to establish when failures began.",
        next_check_downstream="Check the dependency manifest and look for a caller 5xx/timeout after the error-rate spike.",
        recommendation_mitigation="Mitigation · Inspect failing endpoints, recent changes, and 5xx logs on {service}.",
        recommendation_prevention="Prevention · Alert on elevated 5xx error rate and correlate with deployment markers.",
        unit_suffix="",
        check_same_time_saturation=True,
    ),
)


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
        timestamp=event.timestamp,
        source_position=event.source_position,
        why=why,
    )


def _check(criterion: str, observation: Observation | None, found: str, missing: str) -> HypothesisCheck:
    return HypothesisCheck(
        criterion=criterion,
        outcome="supports" if observation else "missing",
        detail=found if observation else missing,
        observation_id=observation.id if observation else None,
    )


def _hypothesis(label: str, service: str, checks: list[HypothesisCheck]) -> Hypothesis:
    points = {"supports": 1, "contradicts": -1, "missing": 0}
    return Hypothesis(label=label, service=service, checks=checks, score=sum(points[check.outcome] for check in checks))


def _calc_confidence(hypothesis: Hypothesis, is_supported: bool, evidence_modalities: set[str]) -> tuple[str, float]:
    """Calculate exact confidence percentage (0.0 - 1.0) and qualitative tier."""
    checks = hypothesis.checks
    if not checks:
        return "low", 0.0
    passed = sum(1 for c in checks if c.outcome == "supports")
    contradicted = sum(1 for c in checks if c.outcome == "contradicts")
    total = len(checks)
    
    if is_supported:
        # 4/4 checks passed. Cross-modal presence adds stability.
        score = 0.90 + (0.06 if len(evidence_modalities) >= 2 else 0.0)
        return "high", round(min(0.98, score), 2)
    elif contradicted > 0:
        score = max(0.10, (passed - contradicted) / total * 0.50)
        return "low", round(score, 2)
    else:
        # Partial / insufficient evidence
        score = max(0.15, (passed / total) * 0.60)
        tier = "medium" if score >= 0.50 else "low"
        return tier, round(score, 2)


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
    trace = [{"agent": "Verification", "action": "Check pool telemetry",
              "status": "confirmed" if baseline and saturation else "missing", "result":
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
    trace.append({"agent": "Verification", "action": "Follow declared callers",
                  "status": "confirmed" if timeout else "missing", "result":
                  f"Ordered database-connection timeout {'found on ' + timeout.service if timeout else 'missing on a declared direct caller'}."})

    checks = [
        _check("Recorded limit reduction", change, "A dated configuration change reduced the pool limit.", ""),
        _check("Pre-change baseline", baseline, "The previous limit and lower latency are recorded.",
               "Upload baseline pool metrics for the service."),
        _check("Pool saturation and latency", saturation, "The pool reached its new limit as latency increased.",
               "No post-change metric confirms both the new limit and saturation with rising latency."),
        _check("Downstream timeout", timeout, "A declared caller reported a database connection timeout after saturation.",
               "An ordered database connection timeout on a declared caller is missing."),
    ]
    hypothesis = _hypothesis("Connection pool limit reduction", service, checks)
    modalities = {item.type for item in evidence}
    if not (baseline and saturation and timeout):
        tier, conf_score = _calc_confidence(hypothesis, False, modalities)
        anchor = datetime.fromisoformat(change.timestamp)
        if not baseline:
            next_check = NextCheck(
                instruction="Check active connections, configured pool capacity, and database latency before the change. Upload those samples if they are missing.",
                service=service, start=(anchor - WINDOW).isoformat(), end=change.timestamp,
            )
        elif not saturation:
            end = min(anchor + WINDOW, datetime.fromisoformat(next_change.timestamp)) if next_change else anchor + WINDOW
            next_check = NextCheck(
                instruction="Check active connections against the new pool limit and compare latency with the baseline. Upload post-change metrics if they are missing.",
                service=service, start=change.timestamp, end=end.isoformat(),
            )
        else:
            direct_callers = callers.get(service, [])
            next_check = NextCheck(
                instruction="Look for a database connection timeout on a direct caller after saturation." if direct_callers
                else "Upload the service dependency manifest and the direct caller’s database connection logs.",
                service=direct_callers[0] if len(direct_callers) == 1 else None,
                start=saturation.timestamp, end=(datetime.fromisoformat(saturation.timestamp) + WINDOW).isoformat(),
            )
        return VerifiedRCA(
            decision="insufficient_evidence", root_cause="Cause unresolved", next_check=next_check,
            confidence=tier, confidence_score=conf_score,
            summary="The pool limit reduction is recorded. Verification requires a recent pre-change baseline, saturation with rising latency, and a subsequent database-connection timeout on a declared caller.",
            evidence=evidence, agent_trace=trace, hypotheses=[hypothesis],
        )

    affected = list(dict.fromkeys([service, timeout.service] + ([gateway.service] if gateway else [])))
    propagation = f"{timeout.service} then reported a connection timeout"
    if gateway:
        propagation += f", and {gateway.service} returned a 504"
    tier, conf_score = _calc_confidence(hypothesis, True, modalities)
    return VerifiedRCA(
        decision="supported", suspected_origin=service, affected_services=affected,
        root_cause=f"Connection pool limit reduction on {service}",
        confidence=tier, confidence_score=conf_score,
        summary=f"The pool limit dropped from {limits.before} to {limits.after}. The pool reached the new limit as database latency rose. {propagation}.",
        evidence=evidence,
        next_check=NextCheck(
            instruction="Check whether connection acquisition waits and caller errors recover after reviewing the pool configuration.",
            service=service,
        ),
        recommendations=[
            f"Mitigation · Review restoring the previous pool limit of {limits.before} on {service}. Confirm database capacity and monitor connection waits and errors after the change.",
            "Prevention · Alert on active connections relative to the configured limit and track acquisition wait time.",
        ],
        agent_trace=trace, hypotheses=[hypothesis],
    )


def _check_metric_episode(
    playbook: MetricPlaybook,
    spike: Observation,
    metrics: list[Observation],
    caller_errors: list[Observation],
    pool_changes: list[Observation] | None = None,
) -> VerifiedRCA:
    start = datetime.fromisoformat(spike.timestamp)
    window_start = start - WINDOW
    key = playbook.metric_key
    baseline = next((event for event in reversed(metrics)
                     if window_start <= datetime.fromisoformat(event.timestamp) < start
                     and 0 <= event.metric_values.get(key, -1) < playbook.threshold), None)
    error = next(iter(_within(caller_errors, spike)), None)
    preceding_saturation = [
        event for event in metrics
        if 0 < event.metric_values.get("db_connections_limit", 0)
        <= event.metric_values.get("db_connections_active", 0)
        and event.metric_values.get("db_latency_ms", 0) >= 100
    ]
    if playbook.check_same_time_saturation:
        earlier_signal = next((event for event in preceding_saturation + (pool_changes or [])
                               if window_start <= datetime.fromisoformat(event.timestamp) <= start), None)
        if not earlier_signal:
            earlier_signal = next((event for event in caller_errors
                                   if window_start <= datetime.fromisoformat(event.timestamp) < start), None)
    else:
        earlier_signal = next((event for event in preceding_saturation + caller_errors
                               if window_start <= datetime.fromisoformat(event.timestamp) < start), None)

    timing = _check(playbook.onset_criterion, error, playbook.onset_found, playbook.onset_missing)
    if earlier_signal:
        timing = HypothesisCheck(
            criterion=timing.criterion, outcome="contradicts", observation_id=earlier_signal.id,
            detail=playbook.onset_contradict,
        )
    checks = [
        _check(playbook.baseline_criterion, baseline, playbook.baseline_found, playbook.baseline_missing),
        _check(playbook.breach_criterion, spike, playbook.breach_found, ""),
        timing,
        _check(playbook.downstream_criterion, error, playbook.downstream_found, playbook.downstream_missing),
    ]
    hypothesis = _hypothesis(playbook.hypothesis_label, spike.service, checks)
    evidence = [_evidence(spike, playbook.spike_why)]
    if baseline:
        evidence.insert(0, _evidence(baseline, playbook.baseline_why))
    if error:
        evidence.append(_evidence(error, "Records an error after the spike on a declared caller."))
    supported = all(check.outcome == "supports" for check in checks)
    modalities = {item.type for item in evidence}
    tier, conf_score = _calc_confidence(hypothesis, supported, modalities)
    if supported:
        next_check = NextCheck(instruction=playbook.next_check_supported, service=spike.service)
    elif earlier_signal or not baseline:
        next_check = NextCheck(
            instruction=playbook.next_check_baseline,
            service=spike.service, start=window_start.isoformat(), end=spike.timestamp,
        )
    else:
        next_check = NextCheck(
            instruction=playbook.next_check_downstream,
            service=spike.service, start=spike.timestamp, end=(start + WINDOW).isoformat(),
        )
    val = spike.metric_values[key]
    val_str = f"{val:g}{playbook.unit_suffix}"
    return VerifiedRCA(
        next_check=next_check,
        decision="supported" if supported else "insufficient_evidence",
        root_cause=f"{playbook.root_cause_prefix} on {spike.service}" if supported else "Cause unresolved",
        confidence=tier, confidence_score=conf_score,
        summary=(f"{playbook.root_cause_prefix} reached {val_str} before an error on {error.service}."
                 if supported else playbook.unsupported_summary),
        suspected_origin=spike.service if supported else None,
        affected_services=[spike.service, error.service] if supported else [],
        recommendations=[
            playbook.recommendation_mitigation.format(service=spike.service),
            playbook.recommendation_prevention,
        ] if supported else [],
        evidence=evidence, hypotheses=[hypothesis],
    )


def _metric_candidates(
    events: list[Observation],
    callers: dict[str, list[str]],
) -> list[VerifiedRCA]:
    errors: dict[str, list[Observation]] = {}
    pool_changes_by_service: dict[str, list[Observation]] = {}
    for event in events:
        if event.modality == "log":
            errors.setdefault(event.service, []).append(event)
        elif event.change_type == "config_change" and event.pool_limit_change and event.pool_limit_change.after < event.pool_limit_change.before:
            pool_changes_by_service.setdefault(event.service, []).append(event)

    candidates = []
    for playbook in _PLAYBOOKS:
        series: dict[str, list[Observation]] = {}
        for event in events:
            if event.modality == "metrics" and playbook.metric_key in event.metric_values:
                series.setdefault(event.service, []).append(event)

        for service, metrics in series.items():
            # When a pool-limit reduction is recorded on this service, elevated error_rate
            # is a downstream symptom of saturation rather than an independent hypothesis.
            if playbook.metric_key == "error_rate" and pool_changes_by_service.get(service):
                continue
            caller_errors = sorted(
                (event for caller in callers.get(service, ()) for event in errors.get(caller, ())),
                key=lambda event: event.timestamp,
            )
            previous_high = False
            for sample in metrics:
                high = sample.metric_values[playbook.metric_key] >= playbook.threshold
                if high and not previous_high:
                    candidates.append(
                        _check_metric_episode(
                            playbook,
                            sample,
                            metrics,
                            caller_errors,
                            pool_changes=pool_changes_by_service.get(service, ()),
                        )
                    )
                previous_high = high
    return candidates


def verify_incident(session_dir: Path, *, service: str | None = None, start: str | None = None, end: str | None = None) -> VerifiedRCA:
    revision = evidence_revision(session_dir)
    events = [event for event in load_verification_observations(session_dir)
              if (not start or event.timestamp >= start) and (not end or event.timestamp < end)]
    callers = {}
    for edge in load_dependencies(session_dir):
        callers.setdefault(edge["callee"], []).append(edge["caller"])

    candidates = []
    for event in events:
        limits = event.pool_limit_change
        if event.change_type == "config_change" and limits and limits.after < limits.before:
            candidates.append(_check_reduction(event, events, callers))

    candidates.extend(_metric_candidates(events, callers))
    if service:
        candidates = [candidate for candidate in candidates if candidate.hypotheses[0].service.lower() == service.lower()]
    candidates.sort(key=lambda candidate: candidate.hypotheses[0].score, reverse=True)
    viable = [candidate for candidate in candidates if candidate.decision == "supported"]
    if len(viable) == 1:
        result = viable[0]
    elif len(viable) > 1:
        by_id = {item.observation_id: item for candidate in viable for item in candidate.evidence}
        result = VerifiedRCA(
            decision="ambiguous", root_cause="Competing explanations",
            confidence="medium", confidence_score=0.50,
            summary="Multiple hypotheses meet the evidence checks. Compare the timeline and source records to distinguish the explanations.",
            evidence=list(by_id.values()),
            next_check=NextCheck(instruction="Compare when competing resource pressures or saturation began against the first caller error. Open the cited samples to check which signal started first."),
        )
    else:
        if candidates:
            result = candidates[0]
        else:
            from services.causal_ranker import rank_causal_origins
            deps = load_dependencies(session_dir)
            ranked = rank_causal_origins(session_dir, events=events, dependencies=deps)
            top = ranked[0] if ranked else None
            hint_service = top.service if top else None
            hint_note = (
                f" Unsupervised ranker suggests inspecting {top.service} first (score={top.score:.2f})."
                if top else ""
            )
            result = VerifiedRCA(
                decision="insufficient_evidence", root_cause="Cause unresolved",
                confidence="low", confidence_score=0.20,
                summary="The available observations do not establish a supported pool-limit, CPU, memory, queue, or error-rate diagnosis.",
                next_check=NextCheck(
                    instruction=f"Upload dated service metrics and caller errors with the dependency manifest. Include configuration changes when investigating a change to the pool limit.{hint_note}",
                    service=hint_service,
                ),
                agent_trace=[{
                    "agent": "Causal ranker",
                    "action": "Unsupervised origin ranking",
                    "status": "hint" if top else "none",
                    "result": f"Top candidate: {top.service} (score={top.score:.2f})." if top else "No anomalous services found.",
                }] if top else [],
            )
    result.evidence_revision = revision
    result.evidence.sort(key=lambda item: item.timestamp or "")
    result.hypotheses = [hypothesis for candidate in candidates for hypothesis in candidate.hypotheses]
    result.agent_trace.insert(0, {
        "agent": "Verification", "action": "Compare candidates",
        "metrics": {"observations": len(events), "hypotheses": len(candidates), "passed": len(viable)},
        "result": f"Loaded {len(events)} dated observations, checked {len(candidates)} hypotheses, and found {len(viable)} that passed every required check.",
    })
    return result
