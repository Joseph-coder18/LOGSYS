"""Shared request models and multi-modal ingest helpers."""
from __future__ import annotations

import json
import logging
from hashlib import sha256
from math import isfinite
from pathlib import Path
import tempfile
from typing import Literal

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, Field, model_validator

from models import BaseChunk, IngestResult

logger = logging.getLogger("logmind.api")


def _run_in_threadpool(func, *args, **kwargs):
    import api.routes as _routes
    return _routes.run_in_threadpool(func, *args, **kwargs)

_LOG_EXTS = {".log", ".txt"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_METRICS_EXTS = {".csv", ".json"}

_MAX_FILES = 20
_MAX_FILE_BYTES = 5 * 1024 * 1024


class QueryRequest(BaseModel):
    query: str
    demo: bool = False


class SessionQueryRequest(QueryRequest):
    mode: Literal["grounded", "graph_rag"] = "grounded"
    service: str | None = Field(default=None, min_length=1, max_length=120)
    start: str | None = None
    end: str | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        from services.observations import normalize_timestamp
        for name in ("start", "end"):
            value = getattr(self, name)
            if value is not None:
                utc = normalize_timestamp(value)
                if utc is None:
                    raise ValueError("UTC scope requires dated timestamps with timezones.")
                setattr(self, name, utc)
        if self.start and self.end and self.start >= self.end:
            raise ValueError("Scope end must be after its start.")
        if self.mode == "graph_rag" and (self.service or self.start or self.end):
            raise ValueError("Scoped queries use grounded mode; the legacy baseline has no time filtering.")
        if self.demo:
            raise ValueError("Synthetic demo fallback is unavailable inside an incident. Upload the demo files explicitly.")
        return self


def _detect_modality(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in _LOG_EXTS:
        return "log"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _METRICS_EXTS:
        return "metrics"
    return "unknown"


def _add_file_to_graph(name: str, modality: str, chunks: list[BaseChunk], session_id: str | None) -> None:
    from services.graph_store import add_entity, add_relationship

    add_entity(name, "file", session_id=session_id, modality=modality)
    for key, entity_type in (("services", "service"), ("exceptions", "error"), ("databases", "database")):
        entities = set()
        for chunk in chunks:
            values = chunk.metadata.get(key, [])
            if isinstance(values, str):
                values = values.split(",")
            entities.update(value.strip() for value in values if value.strip())
        for entity in sorted(entities):
            add_entity(entity, entity_type, session_id=session_id)
            source, target = (entity, name) if entity_type == "error" else (name, entity)
            add_relationship(source, target, "observed_in", session_id=session_id)


def _index_chunks(chunks: list[BaseChunk], session_id: str | None, result: IngestResult) -> None:
    from config import EMBEDDING_DIM
    from services import embedder, pinecone_store

    try:
        embeddings = embedder.embed_batch([chunk.text for chunk in chunks])
        pairs = [
            (chunk, vector) for chunk, vector in zip(chunks, embeddings)
            if len(vector) == EMBEDDING_DIM and any(vector) and all(isfinite(value) for value in vector)
        ]
        if pairs:
            valid_chunks, valid_embeddings = zip(*pairs)
            info = pinecone_store.upsert_chunks(list(valid_chunks), list(valid_embeddings), namespace=session_id)
            result.chunks_indexed = info.get("upserted", 0)
            if info.get("message") and info.get("status") != "ready":
                result.warnings.append(info["message"])
    except Exception:
        logger.exception("Vector indexing failed for session %s", session_id)

    if result.chunks_indexed == len(chunks):
        result.indexing_status = "complete"
    else:
        result.indexing_status = "partial" if result.chunks_indexed else "failed"
        result.warnings.append(f"Indexed {result.chunks_indexed} of {len(chunks)} chunks. Retry the upload to index the remaining chunks.")


async def run_ingest(files: list[UploadFile], session_id: str | None = None) -> IngestResult:
    from config import GEMINI_API_KEY, PINECONE_API_KEY
    from services import image_analyzer, log_parser, metrics_parser
    from services.graph_store import get_stats
    from services.incident_context import parse_changes, save_dependencies
    from services.log_ingest import ingest_log_content
    from services.observations import save_observations
    from services.session_store import _session_dir, get_session, register_session_files

    import api.routes as _routes
    max_files = getattr(_routes, "_MAX_FILES", _MAX_FILES)
    max_file_bytes = getattr(_routes, "_MAX_FILE_BYTES", _MAX_FILE_BYTES)

    if len(files) > max_files:
        raise HTTPException(status_code=413, detail=f"Upload at most {max_files} files at once.")

    result = IngestResult()
    all_chunks = []
    for upload in files:
        name = upload.filename or ""
        if not name or len(name.encode("utf-8")) > 255 or any(char in name for char in ("/", "\\")) or any(ord(char) < 32 for char in name):
            result.errors.append("Skipped a file with an invalid filename.")
            continue
        modality = _detect_modality(name)
        if modality == "unknown":
            result.errors.append(f"Skipped {name}: unsupported file type.")
            continue
        try:
            content = await upload.read(max_file_bytes + 1)
            if len(content) > max_file_bytes:
                raise ValueError("File exceeds the 5 MiB upload limit.")
            if not content:
                raise ValueError("File is empty.")
            digest = sha256(content).hexdigest()
            if Path(name).suffix.lower() == ".json":
                document = json.loads(content.decode("utf-8-sig"))
                kind = document.get("kind") if isinstance(document, dict) else None
                if kind in ("change_events", "service_dependencies"):
                    if session_id is None:
                        raise ValueError("Upload operational changes and dependencies inside an incident.")
                    directory = _session_dir(session_id)
                    if kind == "change_events":
                        observations = parse_changes(document, name)
                        result.observations_added += save_observations(directory, digest, observations)
                    else:
                        save_dependencies(directory, document, name)
                    result.processed_files.append(name)
                    continue

            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / name
                path.write_bytes(content)
                if modality == "log":
                    chunks, observations = log_parser.parse_log(path)
                elif modality == "metrics":
                    chunks, observations = metrics_parser.parse_metrics(path)
                else:
                    cache = _session_dir(session_id) / "vision-cache" if session_id else None
                    chunk, observations = await _run_in_threadpool(image_analyzer.analyze_image, path, cache)
                    chunks = [chunk]
            for observation in observations:
                result.warnings.extend(f"{name} record {observation.source_position}: {warning}" for warning in observation.quality_warnings)
            if not chunks:
                raise ValueError("No usable telemetry found.")
            if session_id and get_session(session_id) is None:
                raise HTTPException(status_code=404, detail="Session no longer exists.")

            if session_id:
                result.observations_added += save_observations(_session_dir(session_id), digest, observations)
            _add_file_to_graph(name, modality, chunks, session_id)
            if modality == "log" and session_id is None:
                try:
                    ingest_log_content(content.decode("utf-8-sig", errors="replace"), session_id=session_id)
                except Exception as e:
                    logger.exception("Failed to ingest log relationships for %s: %s", name, e)
            for index, chunk in enumerate(chunks):
                if session_id and observations:
                    chunk.metadata["source_digest"] = digest
                chunk.chunk_id = sha256(f"{name}/{digest}/{index}".encode("utf-8")).hexdigest()
            all_chunks.extend(chunks)
            result.processed_files.append(name)
        except HTTPException:
            raise
        except (ValueError, OSError) as exc:
            result.errors.append(f"{name}: {exc}")
        except Exception:
            logger.exception("Error processing upload %s", name)
            result.errors.append(f"{name}: could not process the file.")

    result.files_processed = len(result.processed_files)
    if session_id and result.files_processed:
        from services.temporal_edges import build_temporal_edges
        try:
            await _run_in_threadpool(build_temporal_edges, session_id)
        except Exception:
            logger.exception("Could not refresh incident graph for %s", session_id)
            result.warnings.append("Telemetry was saved. Retry graph finalization to refresh temporal relationships.")
    stats = get_stats(session_id=session_id)
    result.graph_nodes, result.graph_edges = stats["nodes"], stats["edges"]
    if session_id:
        register_session_files(session_id, result.processed_files)

    if all_chunks:
        if GEMINI_API_KEY and PINECONE_API_KEY:
            await _run_in_threadpool(_index_chunks, all_chunks, session_id, result)
        else:
            result.warnings.append("Vector indexing is disabled because embedding or Pinecone credentials are missing.")

    if not result.files_processed:
        result.status = "failed"
    elif result.errors or result.indexing_status in {"partial", "failed"}:
        result.status = "partial"
    return result
