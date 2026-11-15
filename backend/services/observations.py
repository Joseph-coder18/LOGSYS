"""Session-local observations and timestamp normalization."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from models import Observation, ObservationCounts, ObservationFilter, ObservationPage


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
        return cursor.rowcount


_SERVICE = "json_extract(payload, '$.service')"
_TIMELINE_FILTERS = {
    "all": "1",
    "anomalies": "json_array_length(payload, '$.anomalies') > 0",
    "changes": "json_extract(payload, '$.modality') = 'change'",
    "errors": "json_extract(payload, '$.severity') IN ('ERROR', 'CRITICAL', 'FATAL')",
}


def load_observations(
    session_dir: Path,
    offset: int,
    limit: int,
    category: ObservationFilter = "all",
    service: str | None = None,
) -> ObservationPage:
    path = database_path(session_dir)
    if not path.exists():
        return ObservationPage(observations=[])

    service_filter = f"{_SERVICE} = ?" if service is not None else "1"
    parameters = [service] if service is not None else []
    with closing(sqlite3.connect(path)) as db:
        db.execute("BEGIN")
        if not _has_observations(db):
            return ObservationPage(observations=[])
        rows = db.execute(
            f"""
            SELECT payload FROM observations
            WHERE {service_filter} AND {_TIMELINE_FILTERS[category]}
            ORDER BY timestamp ASC NULLS LAST, id LIMIT ? OFFSET ?
            """,
            (*parameters, limit + 1, offset),
        ).fetchall()
        count_queries = ", ".join(f"COUNT(CASE WHEN {condition} THEN 1 END)" for condition in _TIMELINE_FILTERS.values())
        counts = db.execute(
            f"SELECT {count_queries} FROM observations WHERE {service_filter}", parameters
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


def recent_signals(session_dir: Path, services: list[str]) -> dict[str, list[dict]]:
    """Return up to three notable, source-linked observations per service."""
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
                SELECT id, timestamp,
                       json_extract(payload, '$.service') AS service,
                       json_extract(payload, '$.summary') AS summary,
                       json_extract(payload, '$.source_file') AS source_file,
                       json_extract(payload, '$.source_position') AS source_position,
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
            SELECT id, timestamp, service, summary, source_file, source_position
            FROM candidates WHERE rank <= 3 ORDER BY service, rank
            """,
            services,
        )
        signals = {}
        for observation_id, timestamp, service, summary, source_file, source_position in rows:
            signals.setdefault(service, []).append({
                "id": observation_id, "timestamp": timestamp, "summary": summary,
                "source_file": source_file, "source_position": source_position,
            })
    return signals
