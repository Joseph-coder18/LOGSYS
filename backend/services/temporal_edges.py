"""Derive chronological and source-linked relations from incident observations."""
from collections import defaultdict
from datetime import datetime
from bisect import bisect_right
import logging
from math import isclose, isfinite

import networkx as nx

from services.graph_store import _persist, _resolve_graph
from services.log_ingest import sync_observation_nodes
from services.session_store import session_graph_lock

logger = logging.getLogger("logmind.temporal_edges")

SUPPORT_WINDOW_S = 30.0
CONTRADICT_WINDOW_S = 0.0
VALUE_RELATIVE_TOLERANCE = 0.001
TEMPORAL_RELATIONS = {"precedes", "propagates_to", "supports", "contradicts"}


def edge_relations(attrs: dict) -> set[str]:
    """Read all relations retained on a pair in the persisted DiGraph."""
    return set(attrs.get("temporal_relations", {})) | {attrs.get("rel", "")}


def _ts(attrs: dict) -> float | None:
    value = attrs.get("timestamp")
    if isinstance(value, bool):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp if isfinite(timestamp) else None


def _end_ts(attrs: dict) -> float | None:
    if attrs.get("interval_end"):
        return datetime.fromisoformat(attrs["interval_end"]).timestamp()
    return _ts(attrs)


def _add_relation(graph: nx.DiGraph, source: str, target: str, relation: str, **details) -> None:
    if not graph.has_edge(source, target):
        graph.add_edge(source, target)
    attrs = graph[source][target]
    attrs.update(rel=relation, derived_by="temporal_edges")
    attrs.setdefault("temporal_relations", {})[relation] = details


def _build(graph: nx.DiGraph) -> dict[str, int]:
    # Recompute derived relations so retries and late-arriving events agree.
    graph.remove_edges_from([
        (source, target) for source, target, attrs in graph.edges(data=True)
        if attrs.get("derived_by") == "temporal_edges" or attrs.get("rel") in TEMPORAL_RELATIONS
    ])
    timed = sorted(
        ((node, attrs) for node, attrs in graph.nodes(data=True)
         if attrs.get("modality") and _ts(attrs) is not None),
        key=lambda item: (_ts(item[1]), str(item[0])),
    )
    by_service = defaultdict(list)
    by_identity = defaultdict(list)
    by_instant = defaultdict(list)
    for node, attrs in timed:
        if service := attrs.get("service"):
            by_service[service].append((node, attrs))
            by_instant[(service, _ts(attrs))].append((node, attrs))
        for key in ("correlation_id", "request_id"):
            if value := attrs.get(key):
                by_identity[(key, value)].append((node, attrs))

    # Intervals must finish before the next event begins. Overlap proves no order.
    for observations in by_service.values():
        starts = [_ts(attrs) for _, attrs in observations]
        for source, attrs in observations:
            position = bisect_right(starts, _end_ts(attrs))
            if position < len(observations):
                target, other = observations[position]
                _add_relation(graph, source, target, "precedes", gap_s=_ts(other) - _end_ts(attrs))

    # Shared request identities provide correlation and observed call direction.
    for (key, value), observations in by_identity.items():
        for index, (source, attrs) in enumerate(observations):
            for target, other in observations[index + 1:]:
                gap = _ts(other) - _ts(attrs)
                if gap > SUPPORT_WINDOW_S:
                    break
                details = {"identity_field": key, "identity_value": value, "gap_s": gap}
                if attrs.get("modality") != other.get("modality") and (not attrs.get("provenance_group") or attrs.get("provenance_group") != other.get("provenance_group")):
                    _add_relation(graph, source, target, "supports", **details)
                if _ts(other) > _end_ts(attrs) and attrs.get("target_service") and attrs["target_service"] == other.get("service"):
                    _add_relation(graph, source, target, "propagates_to", **details)

    # Conflicts require comparable readings of the same signal at the same instant.
    # A normal metric alone cannot refute an unrelated error message.
    for observations in by_instant.values():
        readings = [(node, attrs) for node, attrs in observations if attrs.get("metric_values") and not attrs.get("interval_start") and not any(value != "sample" for value in attrs.get("metric_aggregations", {}).values())]
        for index, (source, attrs) in enumerate(readings):
            for target, other in readings[index + 1:]:
                values, other_values = attrs["metric_values"], other["metric_values"]
                conflicts = sorted(
                    field for field in values.keys() & other_values.keys()
                    if not isclose(values[field], other_values[field], rel_tol=VALUE_RELATIVE_TOLERANCE, abs_tol=1e-6)
                )
                if conflicts:
                    _add_relation(graph, source, target, "contradicts", fields=conflicts, gap_s=0.0)

    counts = dict.fromkeys(sorted(TEMPORAL_RELATIONS), 0)
    for _, _, attrs in graph.edges(data=True):
        for relation in edge_relations(attrs) & TEMPORAL_RELATIONS:
            counts[relation] += 1
    return counts


def build_temporal_edges(session_id: str) -> dict[str, int]:
    """Refresh canonical nodes and return totals for the rebuilt temporal graph."""
    with session_graph_lock(session_id):
        graph, _ = _resolve_graph(session_id)
        if session_id is not None:
            sync_observation_nodes(graph, session_id)
        counts = _build(graph)
        _persist(graph, session_id)
    logger.info("Temporal relations rebuilt for session %s: %s", session_id, counts)
    return counts
