"""Select a source record for narrowly scoped incident lookups."""
from pathlib import Path
import re

from models import Observation
from services.incident_retrieval import asks_about_dependency
from services.observations import highest_metric_reading, search_observations

_LOOKUP = re.compile(r"^\s*(?:what|which|how many|show me)\b", re.I)
_COMPLEX = re.compile(
    r"\b(?:and|or|why|when|before|after|first|last|earliest|latest|until|since|"
    r"during|between|around|compare|trend|total|sum|average|median|percentile|minimum|"
    r"currently|current|today|yesterday|window|rolling|expected|default|p\d{2}|caus\w*|impact|mitigat\w*|rollback|fix|should)\b"
    r"|\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}", re.I,
)
_PEAK = re.compile(r"\b(?:peak|maximum|max|highest)\b", re.I)
_LOOKUP_SCOPE_LIMIT = 200
PEAK_METRICS = {
    "db_connections_limit": ("connection limit", " connections"),
    "cpu_percent": ("CPU usage", "%"),
    "db_latency_ms": ("database latency", " ms"),
}
_FIELDS = (
    ("db_connections_limit", r"\b(?:connection|pool) limit\b|\bdb_connections_limit\b",
     r"(?:connection limit|pool limit|db_connections_limit)\s*[:=]?\s*(\d+)\b"),
    ("cpu_percent", r"\bcpu\s+(?:usage|utilization|percentage|percent)\b|\bcpu_percent\b",
     r"\bcpu(?: percentage| usage| utilization)?(?: reached| was| at)?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:%|percent\b)"),
    ("deployment_version", r"\b(?:deployment|deployed|release) version\b",
     r"\b(?:deployment|deployed|release) version\s*[:=]?\s*(v?\d+(?:\.\d+)+)(?!\d|\.\d)"),
    ("error_code", r"\b(?:http(?: status)?|error|status) code\b",
     r"\b(?:http(?: status)?|error|status) code\s*[:=]?\s*(\d{3})\b"),
    ("db_latency_ms", r"\b(?:database|db) latency\b|\bdb_latency_ms\b",
     r"\b(?:database|db) latency(?: reached| was| at)?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*ms\b"),
)


def find_lookup(
    directory: Path, question: str, terms: list[str], records: list[Observation], links: list[dict],
    *, start: str | None = None, end: str | None = None,
) -> tuple[Observation, str | None] | None:
    """Return a single observed fact, or a full-session peak and its source record."""
    if not _LOOKUP.search(question) or _COMPLEX.search(question):
        return None
    fields = [(field, selector, pattern) for field, selector, pattern in _FIELDS if re.search(selector, question, re.I)]
    if len(fields) != 1:
        return None
    field, selector, pattern = fields[0]
    names = set(terms)
    dependency = asks_about_dependency(names)
    if _PEAK.search(question):
        if dependency or field not in PEAK_METRICS:
            return None
        scoped_peak = re.fullmatch(
            rf"\s*what (?:was|is) (?:the )?(?:peak|maximum|max|highest) (?:recorded )?"
            rf"(?:{selector}) (?:for|on|of) ([\w:-]+)\s*\??\s*",
            question, re.I,
        )
        if not scoped_peak:
            return None
        peak = highest_metric_reading(directory, field, scoped_peak[1].lower(), start=start, end=end)
        return (peak, field) if peak else None

    scope = {link["retrieval_service"] for link in links} if dependency else {
        record.service.lower() for record in records if record.service and record.service.lower() in names
    }
    if len(scope) != 1:
        return None

    def values_in(record: Observation) -> set[str]:
        value = record.metric_values.get(field)
        if value is not None:
            return {str(value).removesuffix(".0")}
        return set(re.findall(pattern, record.summary, re.I))

    for record in records:
        if record.service and record.service.lower() in scope and len(values := values_in(record)) == 1:
            break
    else:
        return None

    scoped = search_observations(directory, [], _LOOKUP_SCOPE_LIMIT + 1, services=list(scope), start=start, end=end)
    if len(scoped) > _LOOKUP_SCOPE_LIMIT or any(values_in(event) - values for event in scoped):
        return None
    return record, None
