"""
LogMind – Metrics Parser Service
Loads CSV / JSON time-series data, detects anomalies (spikes + threshold breaches),
and converts findings into text summaries for embedding.
"""
from __future__ import annotations

import csv
from collections import defaultdict, deque
import json
from statistics import mean, pstdev
import logging
from pathlib import Path
from typing import Any

from models import BaseChunk, Observation
from services.observations import normalize_timestamp
from services.metric_units import normalize_metrics

logger = logging.getLogger("logmind.metrics_parser")

# Absolute thresholds for common metrics
_THRESHOLDS: dict[str, float] = {
    "cpu_percent":     80.0,
    "cpu":             80.0,
    "memory_percent":  85.0,
    "memory":          85.0,
    "error_rate":       0.05,
    "db_latency_ms":  500.0,
    "api_latency_ms": 1000.0,
    "latency_ms":     1000.0,
}


# ── Loaders ───────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        raise ValueError("Expected an array or object of metric records.")
    # {"metrics": [...]} or {"data": [...]}
    for key in ("metrics", "data", "records", "rows"):
        if isinstance(raw.get(key), list):
            return raw[key]
    # Columnar format: {"cpu": [...], "timestamps": [...]}
    keys = list(raw.keys())
    ts_key = next((k for k in keys if "time" in k.lower()), None)
    if ts_key and isinstance(raw[ts_key], list):
        n = len(raw[ts_key])
        records = []
        for i in range(n):
            rec: dict[str, Any] = {}
            for k in keys:
                if isinstance(raw[k], list) and i < len(raw[k]):
                    rec[k] = raw[k][i]
            rec["timestamp"] = raw[ts_key][i]
            records.append(rec)
        return records
    return [raw]


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Expected a CSV header with unique column names.")
        records = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV record {len(records) + 1} has the wrong column count.")
            records.append(row)
        return records


_TIME_FIELDS = {"timestamp", "time", "ts", "date", "timestamps"}
_SERVICE_FIELDS = {"service", "service_name", "component"}


def _field(record: dict, names: set[str]) -> str | None:
    return next((str(value) for key, value in record.items() if key.lower() in names and value), None)


def _metric_values(record: dict) -> dict[str, float]:
    return normalize_metrics(record)[0]


def _detect_anomalies(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare against the previous valid samples for each service and signal."""
    prepared = []
    for index, record in enumerate(records):
        timestamp = _field(record, _TIME_FIELDS)
        prepared.append((normalize_timestamp(timestamp), index, timestamp, record))

    history = defaultdict(lambda: deque(maxlen=5))
    anomalies = []
    for utc, index, timestamp, record in sorted(prepared, key=lambda row: (row[0] is None, row[0], row[1])):
        service = _field(record, _SERVICE_FIELDS)
        for key, value in _metric_values(record).items():
            identity = (service, key, utc is not None)
            # At most one historical value per distinct instant. Equal-time readings
            # cannot establish earlier samples or inflate a sparse baseline.
            previous = [reading for instant, reading in history[identity] if utc and instant < utc]
            threshold = _THRESHOLDS.get(key.lower())
            reason = None
            if threshold is not None and value > threshold:
                reason = f"threshold breach (value={value:g}, threshold={threshold:g})"
            elif len(previous) >= 3:
                baseline = mean(previous)
                deviation = pstdev(previous)
                tolerance = max(abs(baseline) * 0.10, 1.0 if key != "error_rate" else 0.01)
                if abs(value - baseline) > max(2.5 * deviation, tolerance):
                    reason = f"deviation from preceding samples (mean={baseline:g})"
            if utc:
                samples = history[identity]
                if samples and samples[-1][0] == utc:
                    samples[-1] = (utc, value)
                else:
                    samples.append((utc, value))
            if reason:
                anomalies.append({
                    "row": index,
                    "field": key,
                    "value": value,
                    "timestamp": timestamp or f"row {index + 1}",
                    "reason": reason,
                })
    return anomalies


def _records_to_text(records: list[dict], anomalies: list[dict], source_file: str) -> str:
    """Convert metric records + anomaly list into a human-readable text summary."""
    lines = [f"Metrics file: {source_file}"]
    lines.append(f"Total data points: {len(records)}")

    if records:
        keys = [k for k in records[0].keys() if k.lower() not in ("timestamp", "time", "ts")]
        lines.append(f"Metrics tracked: {', '.join(keys)}")

        ts_key = next((k for k in records[0].keys() if "time" in k.lower()), None)
        if ts_key:
            first = records[0].get(ts_key, "")
            last  = records[-1].get(ts_key, "")
            lines.append(f"Time range: {first} to {last}")

    if anomalies:
        lines.append(f"\nAnomalies detected ({len(anomalies)}):")
        for a in anomalies:
            lines.append(f"  - [{a['timestamp']}] {a['field']} = {a['value']}: {a['reason']}")
    else:
        lines.append("\nNo significant anomalies detected.")

    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def parse_metrics_file(file_path: str | Path) -> list[BaseChunk]:
    """Return the legacy embedding chunks for a metrics file."""
    chunks, _ = parse_metrics(file_path)
    return chunks


def parse_metrics(file_path: str | Path) -> tuple[list[BaseChunk], list[Observation]]:
    path = Path(file_path)
    records = _load_csv(path) if path.suffix.lower() == ".csv" else _load_json(path)
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("Each metric record must be an object.")
    if not records or not any(_metric_values(record) for record in records):
        raise ValueError("No numeric metric samples found.")

    anomalies = _detect_anomalies(records)
    by_row = defaultdict(list)
    for item in anomalies:
        by_row[item["row"]].append(item)

    observations = []
    for index, record in enumerate(records):
        values, units, warnings = normalize_metrics(record)
        if not values and not warnings:
            continue
        original = _field(record, _TIME_FIELDS)
        flagged = by_row[index]
        observations.append(Observation(
            modality="metrics",
            source_file=path.name,
            source_position=index + 1,
            service=_field(record, _SERVICE_FIELDS),
            timestamp=normalize_timestamp(original),
            original_timestamp=original,
            summary=", ".join(f"{key}={value:g}" for key, value in values.items())[:500] or "No usable metric readings; inspect source warnings.",
            excerpt=json.dumps(record, ensure_ascii=False),
            anomalies=[f'{item["field"]}: {item["reason"]}' for item in flagged],
            metric_values=values,
            metric_units=units,
            quality_warnings=warnings,
            stream_id=str(record["stream_id"]) if isinstance(record.get("stream_id"), str) and record["stream_id"].strip() else None,
            anomaly_fields=[item["field"] for item in flagged],
        ))

    chunk = BaseChunk(
        source_file=path.name,
        modality="metrics",
        text=_records_to_text(records, anomalies, path.name),
        metadata={
            "modality": "metrics",
            "source_file": path.name,
            "record_count": len(records),
            "anomaly_count": len(anomalies),
            "anomaly_fields": sorted({item["field"] for item in anomalies}),
        },
    )
    return [chunk], observations
