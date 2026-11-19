"""Explicit metric units, without guessing whether a bare value is a ratio."""
from math import isfinite
import re

IDENTITY_FIELDS = {"timestamp", "time", "ts", "date", "timestamps", "service", "service_name",
                   "component", "request_id", "correlation_id", "target_service", "stream_id"}
_NUMBER = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-Z%/]*)\s*$")
_ALIASES = {"cpu": "cpu_percent", "memory": "memory_percent"}
_UNITS = {"cpu_percent": "%", "memory_percent": "%", "error_rate": "ratio",
          "db_latency_ms": "ms", "api_latency_ms": "ms", "latency_ms": "ms",
          "db_connections_active": "count", "db_connections_limit": "count",
          "requests_per_sec": "requests/s"}


def normalize_metrics(record: dict) -> tuple[dict[str, float], dict[str, str], list[str]]:
    values, units, warnings = {}, {}, []
    conflicts = set()
    for original, raw in record.items():
        key = original.lower()
        if key in IDENTITY_FIELDS or isinstance(raw, bool) or raw is None:
            continue
        target = _ALIASES.get(key, key)
        factor = 1.0
        if key.endswith("_latency_s") or key == "latency_s":
            target, factor = key[:-1] + "ms", 1000.0
        elif key == "error_percent":
            target, factor = "error_rate", 0.01
        explicit = ""
        number = raw
        if isinstance(raw, str):
            match = _NUMBER.fullmatch(raw)
            if not match:
                warnings.append(f"{original}: unsupported numeric value or unit; retained in source excerpt.")
                continue
            number, explicit = match.groups()
        try:
            number = float(number)
        except (TypeError, ValueError):
            warnings.append(f"{original}: not a numeric reading; retained in source excerpt.")
            continue
        if not isfinite(number):
            warnings.append(f"{original}: non-finite reading excluded.")
            continue
        expected = _UNITS.get(target)
        explicit = {"percent": "%", "percentage": "%", "milliseconds": "ms", "seconds": "s",
                    "connections": "count", "fraction": "ratio"}.get(explicit.lower(), explicit.lower())
        if explicit:
            if explicit == expected:
                factor = 1.0
            elif explicit == "%" and target in {"cpu_percent", "memory_percent", "error_rate"}:
                factor = 0.01 if target == "error_rate" else 1.0
            elif explicit in {"ms", "s"} and expected == "ms":
                factor = 1000.0 if explicit == "s" else 1.0
            else:
                warnings.append(f"{original}: unit {explicit} is incompatible or ambiguous; reading excluded.")
                continue
        value = number * factor
        if (expected == "%" and not 0 <= value <= 100 or expected == "ratio" and not 0 <= value <= 1
                or expected in {"ms", "count", "requests/s"} and value < 0):
            warnings.append(f"{original}: reading outside the supported {expected} range; excluded.")
            continue
        if target in conflicts:
            continue
        if target in values and values[target] != value:
            warnings.append(f"{target}: conflicting aliases in one sample; reading excluded.")
            conflicts.add(target)
            values.pop(target, None)
            units.pop(target, None)
            continue
        values[target] = value
        if expected:
            units[target] = expected
    return values, units, warnings
