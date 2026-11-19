# LogMind — Multi-Modal Graph RAG Incident Assistant

> Upload logs, metrics, and dashboard screenshots. Ask natural-language questions. Get AI-powered root cause analysis backed by multi-modal evidence.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  React Frontend (Vite)                                          │
│  Upload Panel │ Query Panel │ Results Panel │ Graph Panel       │
└──────────────────────┬──────────────────────────────────────────┘
                       │ HTTP /api/*
┌──────────────────────▼──────────────────────────────────────────┐
│  FastAPI Backend                                                 │
│  ┌─────────────┐  ┌────────────────┐  ┌─────────────────────┐  │
│  │  Ingestion  │  │   Retrieval    │  │     RCA Agent       │  │
│  │   Agent     │  │    Agent       │  │  (LlmAgent + ADK)   │  │
│  └──────┬──────┘  └───────┬────────┘  └──────────┬──────────┘  │
│         │                 │                       │              │
│  ┌──────▼──────────────────▼───────────────────────▼──────────┐ │
│  │               Orchestrator Agent (ADK)                     │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  Services:  log_parser │ image_analyzer │ metrics_parser        │
│             embedder   │ pinecone_store │ graph_store           │
└──────────────────────────────────────────────────────────────────┘
         │ embed + upsert                │ NetworkX DiGraph
┌────────▼────────┐             ┌────────▼────────────────────────┐
│  Pinecone (v3)  │             │  graph.gpickle (persisted)      │
│  Vector Search  │             │  NetworkX Knowledge Graph       │
└─────────────────┘             └─────────────────────────────────┘
```

---

## Quick Start — Docker (Recommended)

```bash
# 1. Clone
git clone https://github.com/<your-org>/logmind.git
cd logmind

# 2. Configure
cp .env.example .env
# Edit .env — add your GEMINI_API_KEY, PINECONE_API_KEY, etc.

# 3. Run
docker-compose up --build

# Frontend: http://localhost:3000
# Backend API: http://localhost:8000
# API docs: http://localhost:8000/docs
```

---

## Manual Dev Setup

See [TESTING.md](TESTING.md) for the Linux walkthrough, demo fixtures, provider setup, and expected results.

### Backend

```bash
cd backend

# Windows PowerShell
python -m venv ..\.venv
..\.venv\Scripts\pip install -r requirements.txt

# Copy and fill in your API keys
cp ../.env.example ../.env

# Start (PowerShell)
$env:PYTHONPATH = $PWD; ..\.venv\Scripts\uvicorn main:app --port 8000 --reload
# OR use the --app-dir flag:
..\.venv\Scripts\uvicorn main:app --app-dir . --port 8000 --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:5173
# Vite proxies /api → http://localhost:8000 automatically
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/api/health` | Liveness probe |
| `POST` | `/api/ingest` | Upload files (multipart) for ingestion |
| `POST` | `/api/query`  | Submit natural-language incident question |
| `GET`  | `/api/graph`  | Get knowledge graph JSON for visualization |

Full interactive docs: `http://localhost:8000/docs`

---

## Supported Modalities

| Modality | Formats | Processing |
|----------|---------|------------|
| **Text Logs** | `.log`, `.txt` | Parsed → chunked → embedded → Pinecone |
| **Metrics** | `.csv`, `.json` | Spike detection → text summary → embedded |
| **Dashboard Screenshots** | `.png`, `.jpg` | Gemini Vision → text summary → embedded |

## Add incident changes and dependencies

Create a live incident and upload the demo logs and metrics from `backend/data/demo/`. Add the two JSON files from `backend/data/demo/context/` through the same Sources panel. This flow works without model or vector-store credentials.

- [Change events](backend/data/demo/context/demo_changes.json) use `"kind": "change_events"` and an `events` array. Each record has a `service`, a timezone-aware `timestamp`, a `summary`, and a `type` of `deployment`, `restart`, or `config_change`. Look for the change label in Timeline and expand the record to inspect its source.
- [Service dependencies](backend/data/demo/context/demo_dependencies.json) use `"kind": "service_dependencies"` and a `dependencies` array of `caller` and `callee` records. One manifest is active per incident. Uploading a new valid manifest replaces the previous topology.

Use the same service names in the manifest, change records, and telemetry. **Dependency graph** arrows point from callers to their targets. **Impact path** arrows follow the path from the suspected origin to its callers. Select `user-db` to inspect **Caller impact** and open the cited observations in the timeline.

## Verify the pool-limit scenario

In a fresh live incident, upload the demo logs, metrics, changes, and dependency manifest, then select **Verify root cause**. The check uses the change record's `pool_limit_change` with integer `before` and `after` values, the service's pool metrics, and its callers' error logs. In **Evidence sequence**, use **Open in timeline** to inspect the exact record.

The demo shows the limit falling from 100 to 20, the pool reaching 20 connections as latency rises, and the declared callers reporting errors afterward. To try an unresolved case with a targeted **Next check**, upload the same files into a second incident with `demo_metrics.json` omitted. Verification is local and requires no provider keys.

The Evidence tab shows a UTC activity chart and a breakdown of parsed records by source type. Select an activity bar to filter the Timeline to its time window. Use **Verify again** after adding evidence if the diagnosis is marked out of date.

**Ask a question** retrieves relevant observations from the selected incident. With a text model configured, it sends the question and selected observations to that provider so the model can write a reply. The verifier supplies the diagnosis status. The backend checks the model's status and resolves each citation to an uploaded timeline row. A reply that fails those checks falls back to the local evidence-backed response.

For live Gemini replies, copy `.env.example` to `.env` if needed, set `GEMINI_API_KEY`, and restart the backend. Add `PINECONE_API_KEY` to enable semantic retrieval alongside keyword retrieval. Upload fresh logs and metrics or re-upload them after enabling Pinecone so the vector entries point to exact source versions and positions. Existing timeline records are eligible for keyword retrieval without re-uploading.

Open a live incident, upload the telemetry you want to investigate, and ask a question about it. Try a follow-up such as “When did that happen?” and open a citation to preview the source beside the chat. **Model reply** identifies generated text, and **Local reply** identifies the fallback. **Agent trace** reports the records found by each retrieval method and how many were carried forward from the previous reply.

For baseline comparisons, pass `"mode": "graph_rag"` alongside `"query"` in the session query API. The global query API also runs the original Graph RAG pipeline.

---

## Environment Variables

See [`.env.example`](.env.example) for the full list. Required keys:

- `GEMINI_API_KEY` — for LLM + Vision + embeddings
- `PINECONE_API_KEY` — vector database
- `PINECONE_INDEX_NAME` — defaults to `logmind-index`

---

## Temporal Evidence Graph (Phase 1)

Phase 1 introduced temporal edge types (`precedes`, `propagates_to`, `supports`, `contradicts`) and UI visualisation. The backend now generates `TemporalEdgeCounts`, the API `/sessions/{id}/finalize` returns them, and the frontend colours these edges and provides a legend.

## Project Structure

```
logmind/
├── frontend/           React + Vite UI
├── backend/
│   ├── agents/         ADK agent implementations
│   ├── tools/          Agent tool functions
│   ├── services/       Core services (parsers, embedder, stores)
│   ├── api/            FastAPI routes
│   ├── data/demo/      Pre-baked synthetic demo files
│   └── main.py         App entry point
├── docker-compose.yml
├── .env.example
└── LITERATURE.md       Literature survey references
```

---

## Literature Survey

See [LITERATURE.md](LITERATURE.md) for references on:
- Graph RAG
- Agentic Workflows (ReAct)
- Multi-Modal Retrieval
- MCP (Model Context Protocol)
