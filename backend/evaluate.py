"""Run labelled retrieval and diagnosis checks, with opt-in live provider comparisons."""
import argparse
import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
import socket
import sys
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

import config
from agents import rca_agent
from agents.rca_agent import RCAAgent
from agents.retrieval_agent import RetrievalAgent
from main import app
from models import ApprovedClaim, Observation, ObservationCitation
from services import grounded_chat, image_analyzer, pinecone_store, session_store
from services.grounded_chat import answer_incident
from services.incident_context import save_dependencies
from services.incident_retrieval import QUESTION_RECORD_LIMIT, query_terms, retrieve_question_records
from services.observations import observation_by_id, save_observations
from services.text_generation import configured_provider
from services.verification import verify_incident


class ProviderCalls:
    """Measure and optionally pace this benchmark's sequential text and vision calls."""

    def __init__(self, gap_seconds: float):
        self.gap_seconds = gap_seconds
        self.last_started = None
        self.events: list[dict] = []

    def wrap(self, kind, call):
        def run(*args, **kwargs):
            waited = 0.0
            if self.last_started is not None:
                delay = self.gap_seconds - (perf_counter() - self.last_started)
                if delay > 0:
                    before_wait = perf_counter()
                    sleep(delay)
                    waited = perf_counter() - before_wait
            self.last_started = start = perf_counter()
            event = {"kind": kind, "status": "returned", "pacing_wait_ms": round(waited * 1000, 3)}
            try:
                return call(*args, **kwargs)
            except Exception as exc:
                code = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
                event["status"] = "rate_limited" if code == 429 else "provider_error"
                if isinstance(code, int):
                    event["http_status"] = code
                raise
            finally:
                event["call_ms"] = round((perf_counter() - start) * 1000, 3)
                self.events.append(event)
        return run


@dataclass(frozen=True)
class Question:
    name: str
    seed: str
    source: str
    prompt: str
    observed: str
    expected: str
    noise: str
    relationship: str


# Development fixtures. Labels are used by scoring, never written as labels into telemetry.
QUESTIONS = (
    Question("pool-dependency", "checkout-api", "orders-db",
             "What is the connection limit for the dependency of checkout-api?",
             "Connection pool limit: 24 connections", r"\b24\b", "Connection limit check completed", "dependency"),
    Question("cpu-dependency", "ingest-gateway", "image-worker",
             "What CPU percentage did the dependency of ingest-gateway reach?",
             "CPU reached 92 percent", r"\b92\b", "CPU percentage check completed", "dependency"),
    Question("rollout-dependency", "account-api", "identity-worker",
             "Which deployment version did the dependency of account-api report?",
             "Deployment version v2.7.4", r"(?<![\d.])2\.7\.4(?!\d|\.\d)", "Deployment version check completed", "dependency"),
    Question("caller-error", "message-broker", "notification-api",
             "What error code did a caller of message-broker report?",
             "Error code 503", r"\b503\b", "Error code monitor responding", "caller"),
    Question("direct-cpu", "export-worker", "export-worker",
             "What CPU percentage did export-worker reach?",
             "CPU reached 91 percent", r"\b91\b", "Health probe passed", "direct"),
    Question("direct-rollout", "search-api", "search-api",
             "Which deployment version did search-api report?",
             "Deployment version v4.6.1", r"(?<![\d.])4\.6\.1(?!\d|\.\d)", "Health probe passed", "direct"),
)


def build_case(directory: Path, case: Question) -> str:
    directory.mkdir()
    events = [
        Observation(modality="log", source_file="source.log", source_position=1, service=case.source,
                    timestamp="2024-02-01T12:00:00.000000+00:00",
                    summary=case.observed, excerpt=case.observed)
    ]
    for index in range(1, 7):
        events.append(Observation(
            modality="log", source_file="source.log", source_position=index + 1, service=case.seed,
            timestamp=f"2024-02-01T12:00:{index:02}.000000+00:00",
            summary=case.noise, excerpt=case.noise,
        ))
    digest = sha256("\n".join(event.excerpt for event in events).encode()).hexdigest()
    save_observations(directory, digest, events)
    if case.relationship != "direct":
        caller, callee = (case.seed, case.source) if case.relationship == "dependency" else (case.source, case.seed)
        save_dependencies(directory, {
            "kind": "service_dependencies", "dependencies": [{"caller": caller, "callee": callee}],
        }, "dependencies.json")
    return events[0].id


def score_answer(claims: list[ApprovedClaim], pattern: str, support_id: str) -> dict:
    # This is an exact-fact/source check. It cannot judge every additional claim in the reply.
    hit = any(re.search(pattern, claim.text, re.I)
              and any(citation.observation_id == support_id for citation in claim.citations)
              for claim in claims)
    return {"fact_and_source_match": hit, "claims": [claim.model_dump() for claim in claims]}


async def evaluate_question(root: Path, case: Question, live_model: bool, calls: list[dict], *, graph_first: bool) -> dict:
    directory = root / case.name
    support = build_case(directory, case)
    terms = query_terms(case.prompt)
    retrieval = {}
    for mode, graph in (("keyword", False), ("keyword_graph", True)):
        timings = []
        for _ in range(15):
            start = perf_counter()
            records, _, _, _ = retrieve_question_records(directory, terms, use_graph=graph)
            timings.append((perf_counter() - start) * 1000)
        rank = next((index for index, record in enumerate(records, 1) if record.id == support), None)
        retrieval[mode] = {
            "rank": rank, "records": len(records), "median_ms": round(median(timings), 3),
            "sources": [{
                "id": event.id, "service": event.service, "summary": event.summary,
                "file": event.source_file, "line": event.source_position,
            } for event in records],
        }
        if graph:
            extractive = [
                ApprovedClaim(text=event.summary, citations=[ObservationCitation(
                    observation_id=event.id, label=f"{event.source_file} · line {event.source_position}",
                )])
                for event in records
            ]

    answers = {"extractive": score_answer(extractive, case.expected, support)}
    # Hold evidence retrieval fixed for the generation ablation.
    with patch("services.grounded_chat.semantic_retrieval_enabled", return_value=False):
        arms = [("automatic", live_model), ("model_off", False)]
        if live_model:
            live_arms = [("model_on_graph_off", True), ("model_on_graph_on", True)]
            arms.extend(reversed(live_arms) if graph_first else live_arms)
        for arm, use_model in arms:
            call_start = len(calls)
            start = perf_counter()
            reply = await answer_incident(
                directory, case.prompt, use_model=use_model, use_graph=arm != "model_on_graph_off",
                route_lookups=arm == "automatic",
            )
            metrics = score_answer(reply.claims, case.expected, support)
            result = {
                **metrics, "decision": reply.decision, "mode": reply.answer_mode,
                "model_attempts": reply.agent_trace[-1]["metrics"]["model_calls"],
                "provider_calls": calls[call_start:],
                "latency_ms": round((perf_counter() - start) * 1000, 3),
            }
            if reply.answer_mode == "local" and use_model:
                result["effective_output"] = "fallback_or_abstention"
            answers[arm] = result
    return {
        "case": case.name, "question": case.prompt, "expected_observation": case.observed,
        "expected_service": case.source, "support_id": support, "retrieval": retrieval, "answers": answers,
    }


DEMO_FILES = ("demo_app.log", "demo_metrics.json", "context/demo_changes.json", "context/demo_dependencies.json")
DEMO_QUESTION = "What caused the auth-service outage around 02:31 UTC?"
DEMO_TIMES = (
    ("demo_metrics.json", 7, r"(?<![\d:])0?2:30(?::00)?(?![\d:])"),
    ("demo_app.log", 7, r"(?<![\d:])0?2:31:01(?![\d:])"),
)


async def ingest_demo(client: AsyncClient, files: tuple[str, ...]) -> tuple[str, dict]:
    created = await client.post("/api/sessions", json={"name": "Benchmark demo"})
    created.raise_for_status()
    session_id = created.json()["id"]
    payload = [("files", (Path(name).name, (config.DEMO_DIR / name).read_bytes())) for name in files]
    response = await client.post(f"/api/sessions/{session_id}/ingest", files=payload)
    response.raise_for_status()
    result = response.json()
    if result["files_processed"] != len(files):
        raise RuntimeError("Benchmark fixture ingestion was incomplete.")
    return session_id, result


async def evaluate_synthesis(directory: Path, live_model: bool, calls: list[dict]) -> dict:
    question = ("Did user-db reach the reduced pool limit before auth-service timed out? "
                "Give the UTC time of the first saturation sample and of the first caller timeout.")
    verdict = verify_incident(directory)
    locators = {(item.source, item.source_position): item.observation_id for item in verdict.evidence}
    times = [(pattern, locators[(file, position)]) for file, position, pattern in DEMO_TIMES]
    excerpts = []
    for item in verdict.evidence:
        event = observation_by_id(directory, item.observation_id)
        if event:
            excerpts.append(ApprovedClaim(
                text=f"{event.timestamp.replace('T', ' ')} · {event.summary}",
                citations=[ObservationCitation(observation_id=event.id, label=f"{item.source} · {item.source_position}")],
            ))
    arms = {"extractive": {
        "exact_times_with_source": sum(score_answer(excerpts, pattern, ref)["fact_and_source_match"] for pattern, ref in times),
        "model_attempts": 0, "claims": [claim.model_dump() for claim in excerpts],
        "ordering_and_additional_claims": "manual_review",
    }}
    with patch("services.grounded_chat.semantic_retrieval_enabled", return_value=False):
        for use_model in (False, True) if live_model else (False,):
            call_start = len(calls)
            start = perf_counter()
            reply = await answer_incident(directory, question, use_model=use_model)
            arms["model_on" if use_model else "model_off"] = {
                "exact_times_with_source": sum(score_answer(reply.claims, pattern, ref)["fact_and_source_match"] for pattern, ref in times),
                "latency_ms": round((perf_counter() - start) * 1000, 3),
                "model_attempts": reply.agent_trace[-1]["metrics"]["model_calls"],
                "provider_calls": calls[call_start:],
                "mode": reply.answer_mode,
                "claims": [claim.model_dump() for claim in reply.claims],
                "ordering_and_additional_claims": "manual_review",
            }
    return {"question": question, "expected_order": "saturation at 02:30:00 then timeout at 02:31:01 UTC", "arms": arms}


async def evaluate_verifier(client: AsyncClient, live_model: bool, calls: list[dict]) -> tuple[list[dict], dict]:
    results = []
    synthesis = {}
    for name, files, origin in (
        ("full-demo", DEMO_FILES, "user-db"),
        ("missing-metrics", tuple(name for name in DEMO_FILES if name != "demo_metrics.json"), None),
        ("missing-topology", DEMO_FILES[:-1], None),
    ):
        session, _ = await ingest_demo(client, files)
        start = perf_counter()
        verdict = verify_incident(session_store._session_dir(session))
        elapsed = perf_counter() - start
        expected = "supported" if origin else "insufficient_evidence"
        results.append({
            "case": name, "decision": verdict.decision, "origin": verdict.suspected_origin,
            "expected_decision": expected, "expected_origin": origin,
            "matched": verdict.decision == expected and verdict.suspected_origin == origin,
            "evidence": [record.model_dump() for record in verdict.evidence],
            "latency_ms": round(elapsed * 1000, 3),
        })
        if name == "full-demo" and results[-1]["matched"]:
            synthesis = await evaluate_synthesis(session_store._session_dir(session), live_model, calls)
    return results, synthesis


async def evaluate_legacy(client: AsyncClient, calls: list[dict]) -> dict:
    session, ingest = await ingest_demo(client, DEMO_FILES)
    try:
        if ingest["indexing_status"] != "complete":
            return {"status": "indexing_unavailable", "chunks_indexed": ingest["chunks_indexed"]}
        for attempt in range(3):
            start = perf_counter()
            retrieved = await asyncio.to_thread(RetrievalAgent().run, {"query": DEMO_QUESTION, "session_id": session})
            latency_ms = round((perf_counter() - start) * 1000, 3)
            if retrieved["evidence"]:
                break
            if attempt < 2:
                await asyncio.sleep(2)
        if not retrieved["evidence"]:
            return {"status": "no_vector_matches"}
        arms = {}
        for name, graph in (("vector_only", []), ("legacy_graph_rag", retrieved["graph_nodes"])):
            call_start = len(calls)
            start = perf_counter()
            result = await asyncio.to_thread(RCAAgent().run, {
                "query": DEMO_QUESTION, "evidence": retrieved["evidence"], "graph_nodes": graph,
            })
            arms[name] = {
                "synthesis_ms": round((perf_counter() - start) * 1000, 3),
                "model_returned": any(step["action"] == "json_parse_ok" for step in result["trace"]),
                "provider_calls": calls[call_start:],
                "output": result["rca"].model_dump(),
            }
        call_start = len(calls)
        start = perf_counter()
        clean = await answer_incident(session_store._session_dir(session), DEMO_QUESTION)
        return {
            "status": "review_required", "question": DEMO_QUESTION, "expected_root_service": "user-db",
            "shared_retrieval_ms": latency_ms, "shared_vector_matches": len(retrieved["evidence"]),
            "shared_evidence": retrieved["evidence"],
            "arms": {**arms, "logmind_x": {
                "end_to_end_ms": round((perf_counter() - start) * 1000, 3),
                "model_attempts": clean.agent_trace[-1]["metrics"]["model_calls"],
                "provider_calls": calls[call_start:],
                "output": clean.model_dump(),
            }},
        }
    finally:
        if ingest["chunks_indexed"]:
            try:
                index = pinecone_store._get_index()
                if index is not None:
                    index.delete(delete_all=True, namespace=session)
            except Exception:
                print(f"Could not clean up synthetic benchmark Pinecone namespace {session}.", file=sys.stderr)


def provider_status(live: bool) -> dict:
    if not live:
        return {"text": "disabled", "vision": "disabled", "vector": "disabled"}

    def check(configured, hostname):
        if not configured:
            return "credentials_missing"
        try:
            socket.getaddrinfo(hostname, 443)
        except OSError:
            return "dns_unavailable"
        return "reachable_by_dns"

    provider = configured_provider()
    return {
        "text": check(provider, "api.groq.com" if provider == "Groq" else "generativelanguage.googleapis.com"),
        "vision": check(config.GEMINI_API_KEY, "generativelanguage.googleapis.com"),
        "vector": check(config.PINECONE_API_KEY, "api.pinecone.io"),
    }


async def evaluate(live: bool, provider_gap_seconds: float = 0) -> dict:
    meter = ProviderCalls(provider_gap_seconds)
    with (
        patch.object(grounded_chat, "generate_text", meter.wrap("text", grounded_chat.generate_text)),
        patch.object(rca_agent, "generate_text", meter.wrap("text", rca_agent.generate_text)),
        patch.object(image_analyzer, "_read_screenshot", meter.wrap("vision", image_analyzer._read_screenshot)),
    ):
        return await _evaluate(live, meter)


async def _evaluate(live: bool, meter: ProviderCalls) -> dict:
    providers = provider_status(live)
    model = providers["text"] == "reachable_by_dns"
    with TemporaryDirectory(prefix="logsys-evaluation-") as scratch:
        root = Path(scratch)
        calls = meter.events
        retrieval_cases = [
            await evaluate_question(root, case, model, calls, graph_first=bool(index % 2))
            for index, case in enumerate(QUESTIONS)
        ]
        sessions = root / "sessions"
        with (
            patch.object(session_store, "_SESSIONS_DIR", sessions),
            patch.object(session_store, "_INDEX_PATH", sessions / "index.json"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                with patch.object(config, "PINECONE_API_KEY", ""):
                    verifier, synthesis = await evaluate_verifier(client, model, calls)
                    vision = {"status": providers["vision"]}
                    if providers["vision"] == "reachable_by_dns":
                        session = (await client.post("/api/sessions")).json()["id"]
                        image = config.DEMO_DIR / "demo_dashboard.png"
                        call_start = len(calls)
                        result = await client.post(f"/api/sessions/{session}/ingest", files={"files": (image.name, image.read_bytes())})
                        vision_calls = calls[call_start:]
                        status = "extracted" if result.json().get("observations_added") else "failed"
                        if any(call["status"] == "rate_limited" for call in vision_calls):
                            status = "rate_limited"
                        vision = {"status": status, "ingest": result.json(), "provider_calls": vision_calls}
                legacy = {"status": "not_run", "reason": providers}
                if model and providers["vector"] == "reachable_by_dns" and config.GEMINI_API_KEY:
                    legacy = await evaluate_legacy(client, calls)

    retrieval = {}
    for mode in ("keyword", "keyword_graph"):
        ranks = [row["retrieval"][mode]["rank"] for row in retrieval_cases]
        retrieval[mode] = {
            "recall_at_5": sum(rank is not None for rank in ranks) / len(ranks),
            "top_1": sum(rank == 1 for rank in ranks) / len(ranks),
            "mrr": sum(1 / rank for rank in ranks if rank) / len(ranks),
            "median_case_latency_ms": median(row["retrieval"][mode]["median_ms"] for row in retrieval_cases),
        }
    answers = {
        arm: {
            "fact_and_source_matches": sum(result["fact_and_source_match"] for result in results),
            "questions": len(results),
            "model_attempts": sum(result.get("model_attempts", 0) for result in results),
            "generated_replies": sum(result.get("mode") == "generated" for result in results),
            "source_backed_replies": sum(result.get("mode") == "extractive" for result in results),
            "rate_limited_calls": sum(
                call["status"] == "rate_limited" for result in results for call in result.get("provider_calls", [])
            ),
            "generated_fact_and_source_matches": sum(
                result.get("mode") == "generated" and result["fact_and_source_match"] for result in results
            ),
        }
        for arm in retrieval_cases[0]["answers"]
        if (results := [row["answers"][arm] for row in retrieval_cases])
    }
    return {
        "providers": providers, "model": config.LLM_MODEL if live else None,
        "question_record_limit": QUESTION_RECORD_LIMIT,
        "provider_gap_seconds": meter.gap_seconds,
        "retrieval": retrieval, "answers": answers, "questions": retrieval_cases,
        "verifier": verifier, "synthesis": synthesis, "vision": vision, "legacy_comparison": legacy,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Opt into configured providers and their call costs.")
    parser.add_argument("--provider-gap-seconds", type=float, default=0, help="Minimum seconds between starts of live text and vision calls.")
    args = parser.parse_args()
    if not isfinite(args.provider_gap_seconds) or args.provider_gap_seconds < 0:
        parser.error("--provider-gap-seconds must be finite and non-negative.")
    if args.provider_gap_seconds and not args.live:
        parser.error("--provider-gap-seconds requires --live.")
    print(json.dumps(asyncio.run(evaluate(args.live, args.provider_gap_seconds)), indent=2))
