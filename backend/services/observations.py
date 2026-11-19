"""Session-local observations and timestamp normalization."""
from contextlib import closing
from collections.abc import Iterator
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path
from hashlib import sha256
import json
from math import ceil
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from models import ActivityBucket, IncidentOverview, Observation, ObservationCounts, ObservationFilter, ObservationPage


def database_path(session_dir: Path) -> Path:
    return session_dir / "observations.sqlite3"


def iter_observations(session_dir: Path) -> Iterator[Observation]:
    """Stream the complete source records for rebuilding the incident graph."""
    path = database_path(session_dir)
    if not path.exists():
        return
    with closing(sqlite3.connect(path)) as db:
        if not _has_observations(db):
            return
        for (payload,) in db.execute("SELECT payload FROM observations ORDER BY timestamp, id"):
            yield Observation.model_validate_json(payload)


def normalize_timestamp(value: str | None) -> str | None:
    """Keep timezone-free timestamps out of the UTC ordering."""
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.strip())
    except ValueError:
        try:
            timestamp = datetime.strptime(value, "%d/%b/%Y:%H:%M:%S %z")
        except ValueError:
            return None
    if timestamp.utcoffset() is None:
        return None
    return timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds")


def save_observations(
    session_dir: Path, source_digest: str, observations: list[Observation],
) -> int:
    if not observations:
        return 0
    with closing(sqlite3.connect(database_path(session_dir), timeout=30)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, timestamp TEXT, payload TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS observations_time ON observations (timestamp, id)")
        db.execute("CREATE TABLE IF NOT EXISTS observation_sources (observation_id TEXT PRIMARY KEY, source_digest TEXT NOT NULL, source_file TEXT NOT NULL, source_position INTEGER NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS observation_source_lookup ON observation_sources (source_digest, source_file, source_position)")
        before = {row[0] for row in db.execute("SELECT id FROM observations")}
        existing_panels = defaultdict(list)
        if any(event.modality == "image" and event.source_label for event in observations):
            for (payload,) in db.execute("SELECT payload FROM observations JOIN observation_sources ON observation_id=observations.id WHERE source_digest=? ORDER BY source_position, observations.id", (source_digest,)):
                event = Observation.model_validate_json(payload)
                if event.modality == "image" and event.source_label:
                    existing_panels[(event.source_file, event.source_label.casefold())].append(event)
        incoming_labels = Counter((event.source_file, (event.source_label or "").casefold()) for event in observations)
        used = set()
        occurrence = Counter()
        rows = []
        for observation in observations:
            observation.timestamp = normalize_timestamp(observation.timestamp)
            observation.interval_start = normalize_timestamp(observation.interval_start)
            observation.interval_end = normalize_timestamp(observation.interval_end)
            identity = f"{session_dir.name}/{source_digest}/{observation.source_file}/{observation.source_position}"
            if observation.modality == "image" and observation.source_label:
                label = (observation.source_file, observation.source_label.casefold())
                candidates = [event for event in existing_panels[label] if event.id not in used]
                same_service = [event for event in candidates if event.service == observation.service]
                exact = [event for event in same_service if event.summary == observation.summary]
                match = exact[0] if exact else same_service[0] if len(same_service) == 1 and incoming_labels[label] == 1 else None
                key = (*label, observation.service, observation.summary)
                occurrence[key] += 1
                if match is None and len(candidates) == 1 and incoming_labels[label] == 1:
                    match = candidates[0]
                if match:
                    # Panel order is model output, not a reliable source identity. Retain
                    # the original panel locator when enriching or reordering extraction.
                    observation.id, observation.source_position = match.id, match.source_position
                    used.add(match.id)
                else:
                    identity = f"{session_dir.name}/{source_digest}/panel/{json.dumps(key)}/{occurrence[key]}"
                    observation.id = str(uuid5(NAMESPACE_URL, identity))
            else:
                observation.id = str(uuid5(NAMESPACE_URL, identity))
            observation.provenance_group = f"stream:{observation.stream_id}" if observation.stream_id else f"file:{source_digest}"
            rows.append((observation.id, observation.timestamp, observation.model_dump_json()))
        db.executemany("INSERT INTO observations VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET timestamp=excluded.timestamp, payload=excluded.payload WHERE observations.payload != excluded.payload", rows)
        db.executemany("INSERT OR IGNORE INTO observation_sources VALUES (?, ?, ?, ?)",
                       [(event.id, source_digest, event.source_file, event.source_position) for event in observations])
        return len({row[0] for row in rows} - before)


_SERVICE = "json_extract(payload, '$.service')"
_TIMELINE_FILTERS = {
    "all": "1",
    "anomalies": "json_array_length(payload, '$.anomalies') > 0",
    "changes": "json_extract(payload, '$.modality') = 'change'",
    "errors": "json_extract(payload, '$.severity') IN ('ERROR', 'CRITICAL', 'FATAL')",
}


_COUNT_COLUMNS = ", ".join(f"COUNT(CASE WHEN {condition} THEN 1 END)" for condition in _TIMELINE_FILTERS.values())


def load_observations(
    session_dir: Path,
    offset: int,
    limit: int,
    category: ObservationFilter = "all",
    service: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> ObservationPage:
    path = database_path(session_dir)
    if not path.exists():
        return ObservationPage(observations=[])

    filters = []
    parameters = []
    for condition, value in ((f"{_SERVICE} = ?", service), ("coalesce(json_extract(payload, '$.interval_end'), timestamp) >= ?", start), ("coalesce(json_extract(payload, '$.interval_start'), timestamp) < ?", end)):
        if value is not None:
            filters.append(condition)
            parameters.append(value)
    scope = " AND ".join(filters) or "1"
    with closing(sqlite3.connect(path)) as db:
        db.execute("BEGIN")
        if not _has_observations(db):
            return ObservationPage(observations=[])
        rows = db.execute(
            f"""
            SELECT payload FROM observations
            WHERE {scope} AND {_TIMELINE_FILTERS[category]}
            ORDER BY timestamp ASC NULLS LAST, id LIMIT ? OFFSET ?
            """,
            (*parameters, limit + 1, offset),
        ).fetchall()
        counts = db.execute(
            f"SELECT {_COUNT_COLUMNS} FROM observations WHERE {scope}", parameters
        ).fetchone()
        services = db.execute(
            f"SELECT DISTINCT {_SERVICE} AS service FROM observations WHERE service <> '' ORDER BY service"
        ).fetchall()
    return ObservationPage(
        observations=[Observation.model_validate_json(row[0]) for row in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
        counts=ObservationCounts(**dict(zip(_TIMELINE_FILTERS, counts))),
        services=[row[0] for row in services],
    )



def _revision(db: sqlite3.Connection, observation_count: int) -> str:
    has_dependencies = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dependencies'"
    ).fetchone()
    edges = db.execute("SELECT * FROM dependencies ORDER BY caller, callee").fetchall() if has_dependencies else []
    payload_hash = sha256()
    if _has_observations(db):
        for (payload,) in db.execute("SELECT payload FROM observations ORDER BY id"):
            payload_hash.update(payload.encode())
    return sha256(json.dumps([observation_count, edges, payload_hash.hexdigest()]).encode()).hexdigest()


def evidence_revision(session_dir: Path) -> str:
    path = database_path(session_dir)
    if not path.exists():
        return sha256(json.dumps([0, [], sha256().hexdigest()]).encode()).hexdigest()
    with closing(sqlite3.connect(path)) as db:
        db.execute("BEGIN")
        count = db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] if _has_observations(db) else 0
        return _revision(db, count)


def incident_overview(session_dir: Path) -> IncidentOverview:
    path = database_path(session_dir)
    if not path.exists():
        return IncidentOverview(revision=evidence_revision(session_dir))
    with closing(sqlite3.connect(path)) as db:
        db.execute("BEGIN")
        if not _has_observations(db):
            return IncidentOverview(revision=_revision(db, 0))
        row = db.execute(
            f"SELECT MIN(timestamp), MAX(timestamp), "
            "SUM(CASE WHEN timestamp IS NULL AND json_extract(payload, '$.interval_start') IS NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN timestamp IS NULL AND json_extract(payload, '$.interval_start') IS NOT NULL THEN 1 ELSE 0 END), "
            "MIN(coalesce(timestamp, json_extract(payload, '$.interval_start'))), "
            f"MAX(coalesce(timestamp, json_extract(payload, '$.interval_end'))), {_COUNT_COLUMNS} FROM observations"
        ).fetchone()
        first, last, undated, intervals, coverage_first, coverage_last, *counts = row
        overview = IncidentOverview(
            revision=_revision(db, counts[0]),
            first_at=coverage_first, last_at=coverage_last, undated=undated or 0, intervals=intervals or 0,
            counts=ObservationCounts(**dict(zip(_TIMELINE_FILTERS, counts))),
            modalities=dict(db.execute(
                "SELECT json_extract(payload, '$.modality') AS modality, COUNT(*) FROM observations GROUP BY modality"
            )),
            services=[row[0] for row in db.execute(
                f"SELECT DISTINCT {_SERVICE} AS service FROM observations WHERE service <> '' ORDER BY service"
            )],
        )
        if first is None:
            return overview
        first_second = int(datetime.fromisoformat(first).replace(microsecond=0).timestamp())
        last_second = int(datetime.fromisoformat(last).replace(microsecond=0).timestamp())
        span = last_second - first_second + 1
        step = next((step for step in (1, 5, 15, 30, 60, 300, 900, 3600, 21600, 86400)
                     if ceil(span / step) <= 16), ceil(span / 16 / 86400) * 86400)
        buckets = dict(
            (row[0], ObservationCounts(**dict(zip(_TIMELINE_FILTERS, row[1:]))))
            for row in db.execute(
                f"""SELECT CAST((unixepoch(substr(timestamp, 1, 19) || 'Z') - ?) / ? AS INTEGER) AS bucket, {_COUNT_COLUMNS}
                    FROM observations WHERE timestamp IS NOT NULL GROUP BY bucket""",
                (first_second, step),
            )
        )

    def utc(second: int) -> str:
        return datetime.fromtimestamp(second, timezone.utc).isoformat(timespec="seconds")

    overview.activity = [
        ActivityBucket(
            start=utc(first_second + index * step),
            end=utc(first_second + (index + 1) * step),
            counts=buckets.get(index, ObservationCounts()),
        )
        for index in range(ceil(span / step))
    ]
    return overview


def _has_observations(db: sqlite3.Connection) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'observations'"
    ).fetchone() is not None


def observation_by_id(session_dir: Path, observation_id: str) -> Observation | None:
    path = database_path(session_dir)
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as db:
        if not _has_observations(db):
            return None
        row = db.execute("SELECT payload FROM observations WHERE id = ?", (observation_id,)).fetchone()
    return Observation.model_validate_json(row[0]) if row else None


_IMPACT_FIELDS = {
    "id", "timestamp", "summary", "source_file", "source_position",
    "modality", "severity", "metric_values", "anomaly_fields", "anomalies",
}


def recent_signals(session_dir: Path, services: list[str]) -> dict[str, list[dict]]:
    """Select the latest three notable observations and display them in time order."""
    path = database_path(session_dir)
    if not path.exists():
        return {}

    with closing(sqlite3.connect(path)) as db:
        if not _has_observations(db):
            return {}
        placeholders = ", ".join("?" for _ in services)
        rows = db.execute(
            f"""
            WITH candidates AS (
                SELECT id, timestamp, payload, {_SERVICE} AS service,
                       ROW_NUMBER() OVER (
                           PARTITION BY json_extract(payload, '$.service')
                           ORDER BY timestamp DESC NULLS LAST, id
                       ) AS rank
                FROM observations
                WHERE json_extract(payload, '$.service') IN ({placeholders})
                  AND (
                      json_extract(payload, '$.modality') = 'change'
                      OR json_extract(payload, '$.severity') IN ('ERROR', 'CRITICAL', 'FATAL', 'WARN', 'WARNING')
                      OR json_array_length(payload, '$.anomalies') > 0
                  )
            )
            SELECT payload FROM candidates WHERE rank <= 3
            ORDER BY service, timestamp ASC NULLS LAST, id
            """,
            services,
        )
        signals = {}
        for (payload,) in rows:
            event = Observation.model_validate_json(payload)
            signals.setdefault(event.service, []).append(event.model_dump(include=_IMPACT_FIELDS))
    return signals


def recent_metric_samples(session_dir: Path, service: str) -> list[dict]:
    """Return the latest dated samples, including readings without anomalies."""
    path = database_path(session_dir)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as db:
        if not _has_observations(db):
            return []
        rows = db.execute(
            f"""
            SELECT payload FROM observations
            WHERE {_SERVICE} = ? AND timestamp IS NOT NULL
              AND json_extract(payload, '$.modality') = 'metrics'
            ORDER BY timestamp DESC, id DESC
            LIMIT 5
            """,
            (service,),
        ).fetchall()
    return [
        Observation.model_validate_json(payload).model_dump(include=_IMPACT_FIELDS)
        for (payload,) in reversed(rows)
    ]


def load_verification_observations(session_dir: Path) -> list[Observation]:
    """Read dated changes, metrics, and error logs in chronological order."""
    path = database_path(session_dir)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as db:
        if not _has_observations(db):
            return []
        rows = db.execute(
            f"""
            SELECT payload FROM observations
            WHERE timestamp IS NOT NULL AND {_SERVICE} IS NOT NULL
              AND ({_TIMELINE_FILTERS["changes"]}
                   OR json_extract(payload, '$.modality') = 'metrics'
                   OR {_TIMELINE_FILTERS["errors"]})
            ORDER BY timestamp, id
            """
        )
        return [Observation.model_validate_json(row[0]) for row in rows]



def search_observations(
    session_dir: Path,
    terms: list[str],
    limit: int,
    source: tuple[str, str, int, int] | None = None,
    *,
    services: list[str] | None = None,
    start: str | None = None, end: str | None = None,
) -> list[Observation]:
    """Rank uploaded observations, optionally within a known vector chunk's source span."""
    path = database_path(session_dir)
    if not path.exists():
        return []

    haystack = "lower(coalesce(json_extract(payload, '$.service'), '') || ' ' || json_extract(payload, '$.summary') || ' ' || coalesce(timestamp, ''))"
    relevance = " + ".join(f"(instr({haystack}, ?) > 0)" for _ in terms) or "0"
    with closing(sqlite3.connect(path)) as db:
        if not _has_observations(db):
            return []
        filters = []
        parameters = []
        join = ""
        if source:
            has_sources = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'observation_sources'"
            ).fetchone()
            if not has_sources:
                return []
            join = "JOIN observation_sources ON observation_id = observations.id"
            filters.append("source_digest = ? AND source_file = ? AND source_position BETWEEN ? AND ?")
            parameters.extend(source)
        if services:
            filters.append(f"lower({_SERVICE}) IN ({', '.join('?' for _ in services)})")
            parameters.extend(service.lower() for service in services)
        for condition, value in (("coalesce(json_extract(payload, '$.interval_end'), timestamp) >= ?", start), ("coalesce(json_extract(payload, '$.interval_start'), timestamp) < ?", end)):
            if value is not None:
                filters.append(condition)
                parameters.append(value)
        where = "WHERE " + " AND ".join(filters) if filters else ""
        rows = db.execute(
            f"""
            SELECT payload FROM (
                SELECT payload, timestamp, observations.id, ({relevance}) AS relevance
                FROM observations {join} {where}
            )
            WHERE relevance > 0 OR ?
            ORDER BY relevance DESC, timestamp DESC NULLS LAST, id LIMIT ?
            """,
            (*terms, *parameters, bool(source or not terms), limit),
        )
        return [Observation.model_validate_json(row[0]) for row in rows]


def highest_metric_reading(session_dir: Path, metric: str, service: str, *, start: str | None = None, end: str | None = None) -> Observation | None:
    """Read the incident-wide peak from typed metrics for one explicitly named service."""
    path = database_path(session_dir)
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as db:
        if not _has_observations(db):
            return None
        field = f"$.metric_values.{metric}"
        row = db.execute(
            f"""
            SELECT payload FROM observations
            WHERE lower({_SERVICE}) = ? AND json_extract(payload, '$.modality') = 'metrics'
              AND json_type(payload, ?) IN ('integer', 'real')
              AND (? IS NULL OR timestamp >= ?) AND (? IS NULL OR timestamp < ?)
            ORDER BY json_extract(payload, ?) DESC, timestamp ASC NULLS LAST, id LIMIT 1
            """,
            (service, field, start, start, end, end, field),
        ).fetchone()
    return Observation.model_validate_json(row[0]) if row else None
