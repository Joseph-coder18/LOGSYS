"""Session-local observations and timestamp normalization."""
from contextlib import closing
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
    rows = []
    for observation in observations:
        identity = f"{session_dir.name}/{source_digest}/{observation.source_file}/{observation.source_position}"
        observation.id = str(uuid5(NAMESPACE_URL, identity))
        rows.append((observation.id, observation.timestamp, observation.model_dump_json()))

    with closing(sqlite3.connect(database_path(session_dir))) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS observations "
            "(id TEXT PRIMARY KEY, timestamp TEXT, payload TEXT NOT NULL)"
        )
        db.execute("CREATE INDEX IF NOT EXISTS observations_time ON observations (timestamp, id)")
        cursor = db.executemany("INSERT OR IGNORE INTO observations VALUES (?, ?, ?)", rows)
        db.execute("""
            CREATE TABLE IF NOT EXISTS observation_sources (
                observation_id TEXT PRIMARY KEY,
                source_digest TEXT NOT NULL,
                source_file TEXT NOT NULL,
                source_position INTEGER NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS observation_source_lookup ON observation_sources (source_digest, source_file, source_position)")
        db.executemany(
            "INSERT OR IGNORE INTO observation_sources VALUES (?, ?, ?, ?)",
            [(event.id, source_digest, event.source_file, event.source_position) for event in observations],
        )
        return cursor.rowcount


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
    for condition, value in ((f"{_SERVICE} = ?", service), ("timestamp >= ?", start), ("timestamp < ?", end)):
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
    return sha256(json.dumps([observation_count, edges]).encode()).hexdigest()


def evidence_revision(session_dir: Path) -> str:
    path = database_path(session_dir)
    if not path.exists():
        return sha256(b"[0, []]").hexdigest()
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
            f"SELECT MIN(timestamp), MAX(timestamp), COUNT(*) - COUNT(timestamp), {_COUNT_COLUMNS} FROM observations"
        ).fetchone()
        first, last, undated, *counts = row
        overview = IncidentOverview(
            revision=_revision(db, counts[0]),
            first_at=first, last_at=last, undated=undated,
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
        if source:
            has_sources = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'observation_sources'"
            ).fetchone()
            if not has_sources:
                return []
            scope = """
                JOIN observation_sources ON observation_id = observations.id
                WHERE source_digest = ? AND source_file = ? AND source_position BETWEEN ? AND ?
            """
        else:
            scope = ""
        rows = db.execute(
            f"""
            SELECT payload FROM (
                SELECT payload, timestamp, observations.id, ({relevance}) AS relevance
                FROM observations {scope}
            )
            WHERE relevance > 0 OR ?
            ORDER BY relevance DESC, timestamp DESC NULLS LAST, id LIMIT ?
            """,
            (*terms, *(source or ()), bool(source or not terms), limit),
        )
        return [Observation.model_validate_json(row[0]) for row in rows]
