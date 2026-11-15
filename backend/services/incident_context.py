"""Operational context supplied by the investigator for a single session."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
from typing import Annotated, Literal

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from models import Observation
from services.observations import database_path, normalize_timestamp, recent_signals

ServiceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class ChangeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: ServiceName
    timestamp: str
    type: Literal["deployment", "restart", "config_change"]
    summary: Description


class ChangeFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["change_events"]
    events: list[ChangeEvent] = Field(min_length=1)


class Dependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caller: ServiceName
    callee: ServiceName


class DependencyFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["service_dependencies"]
    dependencies: list[Dependency] = Field(min_length=1, max_length=200)


def parse_changes(document: dict, filename: str) -> list[Observation]:
    changes = ChangeFile.model_validate(document)
    observations = []
    for position, event in enumerate(changes.events, 1):
        utc = normalize_timestamp(event.timestamp)
        if utc is None:
            raise ValueError(f"Change record {position} requires a timestamp with a timezone.")
        observations.append(Observation(
            modality="change",
            source_file=filename,
            source_position=position,
            service=event.service,
            timestamp=utc,
            original_timestamp=event.timestamp,
            change_type=event.type,
            summary=event.summary,
            excerpt=json.dumps(document["events"][position - 1], ensure_ascii=False),
        ))
    return observations


def save_dependencies(session_dir: Path, document: dict, filename: str) -> None:
    manifest = DependencyFile.model_validate(document)
    rows = []
    seen = set()
    for position, edge in enumerate(manifest.dependencies, 1):
        pair = (edge.caller, edge.callee)
        if edge.caller == edge.callee:
            raise ValueError(f"Dependency record {position} must connect two different services.")
        if pair in seen:
            raise ValueError(f"Dependency record {position} repeats an earlier relationship.")
        seen.add(pair)
        rows.append((*pair, filename, position))

    with closing(sqlite3.connect(database_path(session_dir))) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS dependencies (
                caller TEXT NOT NULL, callee TEXT NOT NULL,
                source_file TEXT NOT NULL, source_position INTEGER NOT NULL,
                PRIMARY KEY (caller, callee)
            )
        """)
        # The manifest describes the active topology as a whole.
        db.execute("DELETE FROM dependencies")
        db.executemany("INSERT INTO dependencies VALUES (?, ?, ?, ?)", rows)


def load_dependencies(session_dir: Path) -> list[dict]:
    path = database_path(session_dir)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dependencies'").fetchone() is None:
            return []
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM dependencies ORDER BY caller, callee")]


def add_topology(graph: dict, dependencies: list[dict]) -> dict:
    nodes = {node["id"]: node for node in graph["nodes"]}
    service_ids = {}

    for edge in dependencies:
        for service in (edge["caller"], edge["callee"]):
            if service in service_ids:
                continue
            node_id = service
            existing = nodes.get(node_id)
            if existing and (existing.get("type") not in {"service", "database"} or existing.get("service", node_id) != service):
                node_id = f"service:{node_id}"
                while node_id in nodes:
                    node_id = f"service:{node_id}"
            node = nodes.setdefault(node_id, {"id": node_id, "type": "service"})
            node["service"] = service
            service_ids[service] = node_id

    edges = [
        {
            "source": service_ids[edge["caller"]],
            "target": service_ids[edge["callee"]],
            "rel": "depends_on",
            "source_file": edge["source_file"],
            "source_position": edge["source_position"],
        }
        for edge in dependencies
    ]
    return {"nodes": list(nodes.values()), "edges": [*graph["edges"], *edges]}


def potential_impact(session_dir: Path, service: str) -> dict:
    dependencies = nx.DiGraph()
    dependencies.add_edges_from((edge["caller"], edge["callee"]) for edge in load_dependencies(session_dir))
    paths = nx.single_source_shortest_path(dependencies.reverse(copy=False), service) if service in dependencies else {}
    services = sorted((name for name in paths if name != service), key=lambda name: (len(paths[name]), name))
    signals = recent_signals(session_dir, [service, *services])
    return {
        "service": service,
        "observations": signals.get(service, []),
        "dependents": [
            {"service": name, "path": paths[name], "observations": signals.get(name, [])}
            for name in services
        ],
    }
