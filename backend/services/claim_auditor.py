"""Post-generation deterministic claim auditor (zero-hallucination guard)."""
from __future__ import annotations

import re
from math import isclose

from models import ApprovedClaim, Observation

_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?|\b\d{2}:\d{2}(?::\d{2})?\b")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.-])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9_.-])")


def _observation_corpus(obs: Observation) -> tuple[list[float], str]:
    """Extract all numeric values and searchable text from a cited observation."""
    nums: list[float] = []
    for val in (obs.metric_values or {}).values():
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            nums.append(float(val))
    if obs.pool_limit_change:
        nums.append(float(obs.pool_limit_change.before))
        nums.append(float(obs.pool_limit_change.after))

    text_parts = [
        obs.service or "",
        obs.summary or "",
        obs.excerpt or "",
        obs.timestamp or "",
        obs.interval_start or "",
        obs.interval_end or "",
        obs.source_file or "",
    ]
    combined = " ".join(text_parts)
    for raw in _NUMBER_RE.findall(combined):
        try:
            nums.append(float(raw.replace(",", "")))
        except ValueError:
            pass
    return nums, combined.lower()


def _number_supported(value: float, cited_nums: list[float]) -> bool:
    return any(isclose(value, ref, rel_tol=1e-3, abs_tol=1e-6) for ref in cited_nums)


def audit_claims(
    claims: list[ApprovedClaim],
    obs_by_id: dict[str, Observation],
    known_services: set[str] | None = None,
) -> tuple[list[ApprovedClaim], float]:
    """
    Verify numbers, timestamps, and service mentions in each claim against its cited observations.
    Returns (audited_claims, audit_score) where audit_score is the fraction of verified claims.
    """
    if not claims:
        return [], 1.0

    audited: list[ApprovedClaim] = []
    verified_count = 0
    services_clean = {s.strip() for s in (known_services or set()) if s and len(s.strip()) >= 2}

    for claim in claims:
        warnings: list[str] = []
        cited_obs = [
            obs_by_id[c.observation_id]
            for c in claim.citations
            if c.observation_id in obs_by_id
        ]
        if not cited_obs:
            audited.append(
                claim.model_copy(update={
                    "audit_status": "unverified",
                    "audit_warnings": ["Claim has no cited source observations."],
                })
            )
            continue

        cited_nums: list[float] = []
        cited_text_parts: list[str] = []
        for obs in cited_obs:
            nums, text = _observation_corpus(obs)
            cited_nums.extend(nums)
            cited_text_parts.append(text)
        cited_corpus = " ".join(cited_text_parts)

        # 1. Check timestamps mentioned in the claim
        text_without_ts = claim.text
        for ts_match in _TIMESTAMP_RE.findall(claim.text):
            # Extract HH:MM(:SS) core so ISO vs space/UTC formatting still matches
            core = ts_match.split("T")[-1].split(" ")[-1].split("+")[0].rstrip("Z").split(".")[0]
            if core not in cited_corpus:
                warnings.append(f"Timestamp {ts_match} not found in cited records.")
            text_without_ts = text_without_ts.replace(ts_match, " ")

        # 2. Check numeric values mentioned in the claim
        for raw_num in _NUMBER_RE.findall(text_without_ts):
            try:
                num_val = float(raw_num.replace(",", ""))
            except ValueError:
                continue
            if not _number_supported(num_val, cited_nums):
                warnings.append(f"Number {raw_num} not found in cited records.")

        # 3. Check known service names mentioned in the claim
        lower_claim = claim.text.lower()
        for svc in sorted(services_clean):
            if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(svc.lower())}(?![A-Za-z0-9_-])", lower_claim):
                if svc.lower() not in cited_corpus:
                    warnings.append(f"Service '{svc}' not found in cited records.")

        if warnings:
            audited.append(
                claim.model_copy(update={
                    "audit_status": "flagged",
                    "audit_warnings": list(dict.fromkeys(warnings)),
                })
            )
        else:
            verified_count += 1
            audited.append(
                claim.model_copy(update={
                    "audit_status": "verified",
                    "audit_warnings": [],
                })
            )

    score = round(verified_count / len(claims), 2)
    return audited, score

