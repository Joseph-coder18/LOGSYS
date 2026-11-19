"""Project source observations into the incident graph using their persisted IDs."""
from datetime import datetime
from hashlib import sha256
import json
import re
from uuid import NAMESPACE_URL, uuid5

import networkx as nx

from models import Observation
from services.graph_store import _persist, _resolve_graph
from services.log_parser import _parse_line
from services.observations import database_path, iter_observations, normalize_timestamp
from services.session_store import _session_dir, session_graph_lock

_OWNER = "observation_store"
_LEGACY_NODE = re.compile(r"^log-.+-[0-9a-f]{8}$")
_TYPES = {"log": "ObservationLog", "metrics": "Metric", "image": "Screenshot", "change": "ConfigChange"}
_IDENTIFIERS = re.compile(r"(?<![\w-])(correlation_id|request_id|target_service)\s*[:=]\s*[\"']?([\w.:-]+)")
_RESOURCE = re.compile(r"\b(?:[\w-]+-db|mysql|postgres(?:ql)?|redis|mongodb|sqlite|elasticsearch|cassandra|dynamodb|node-\d+)\b", re.I)


def _source_metadata(excerpt: str) -> dict[str, str]:
    try:
        document = json.loads(excerpt)
    except (ValueError, TypeError):
        return dict(_IDENTIFIERS.findall(excerpt))
    if not isinstance(document, dict):
        return {}
    return {
        key: str(document[key]).strip()
        for key in ("correlation_id", "request_id", "target_service")
        if isinstance(document.get(key), (str, int)) and not isinstance(document[key], bool)
        and str(document[key]).strip()
    }


def _entity_id(graph: nx.DiGraph, name: str, entity_type: str) -> str:
    node_id = name
    allowed = {"service", "database"} if entity_type in {"service", "database"} else {entity_type}
    while node_id in graph and graph.nodes[node_id].get("type") not in allowed:
        node_id = f"{entity_type}:{node_id}"
    if node_id not in graph:
        graph.add_node(node_id, type=entity_type)
    return node_id


def _add_observation(graph: nx.DiGraph, observation: Observation) -> None:
    utc = normalize_timestamp(observation.timestamp or observation.interval_start)
    timestamp = datetime.fromisoformat(utc).timestamp() if utc else None
    node_type = _TYPES[observation.modality]
    if observation.change_type == "deployment":
        node_type = "Deployment"
    attrs = observation.model_dump(exclude={"id"})
    attrs.update(
        type=node_type, observation_id=observation.id, managed_by=_OWNER,
        timestamp=timestamp, utc_timestamp=normalize_timestamp(observation.timestamp), raw_timestamp=observation.original_timestamp,
        anomaly=bool(observation.anomalies), **_source_metadata(observation.excerpt),
    )
    graph.add_node(observation.id, **attrs)
    source = _entity_id(graph, observation.source_file, "file")
    graph.add_edge(source, observation.id, rel="observed_in", managed_by=_OWNER,
                   source_file=observation.source_file, source_position=observation.source_position)
    if observation.modality == "log":
        for resource in set(_RESOURCE.findall(observation.summary)) - {observation.service}:
            entity_type = "service" if resource.lower().startswith("node-") else "database"
            target = _entity_id(graph, resource, entity_type)
            graph.add_edge(observation.id, target, rel="mentions", managed_by=_OWNER,
                           source_file=observation.source_file, source_position=observation.source_position)
    if observation.service:
        service = _entity_id(graph, observation.service, "service")
        graph.nodes[service]["service"] = observation.service
        graph.add_edge(service, observation.id, rel="has_observation", managed_by=_OWNER)
        target = attrs.get("target_service")
        if target and target != observation.service:
            target_id = _entity_id(graph, target, "service")
            graph.nodes[target_id]["service"] = target
            graph.add_edge(service, target_id, rel="depends_on", managed_by=_OWNER,
                           source_file=observation.source_file, source_position=observation.source_position)


def sync_observation_nodes(graph: nx.DiGraph, session_id: str) -> None:
    """Rebuild owned nodes, including records uploaded before this graph existed."""
    directory = _session_dir(session_id)
    if not database_path(directory).exists():
        return
    legacy = {
        node: attrs for node, attrs in graph.nodes(data=True)
        if _LEGACY_NODE.fullmatch(str(node)) and attrs.get("type") in {"ObservationLog", "Deployment", "ConfigChange"}
    }
    legacy_services = {attrs.get("service") for attrs in legacy.values()}
    graph.remove_edges_from([
        (source, target) for source, target, attrs in graph.edges(data=True)
        if attrs.get("managed_by") == _OWNER or (
            source in legacy_services and attrs.get("rel") in {"depends_on", "propagates_to"}
            and "timestamp" in attrs and not attrs.get("source_file")
        )
    ])
    owned = [node for node, attrs in graph.nodes(data=True) if attrs.get("managed_by") == _OWNER]
    graph.remove_nodes_from([*legacy, *owned])
    for observation in iter_observations(directory):
        _add_observation(graph, observation)


def ingest_log_content(text: str, session_id: str | None = None) -> None:
    """Retain the legacy log entry point with deterministic record identities."""
    if not text.strip():
        return
    digest = sha256(text.encode()).hexdigest()
    with session_graph_lock(session_id):
        graph, _ = _resolve_graph(session_id)
        for position, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            parsed = _parse_line(line)
            original = str(parsed["timestamp"]) if parsed.get("timestamp") else None
            observation = Observation(
                id=str(uuid5(NAMESPACE_URL, f"legacy-log/{session_id}/{digest}/{position}")),
                modality="log", source_file="inline.log", source_position=position,
                service=parsed.get("service") or None, severity=parsed.get("severity"),
                timestamp=normalize_timestamp(original), original_timestamp=original,
                summary=str(parsed.get("message", line))[:500], excerpt=line,
            )
            _add_observation(graph, observation)
        _persist(graph, session_id)


def ingest_log_line(line: str, session_id: str | None = None) -> None:
    ingest_log_content(line, session_id=session_id)
