# Test LogMind locally

Run the local workflow first, then test Gemini chat and Pinecone retrieval. Use the bundled synthetic files for these checks. With providers enabled, selected telemetry is sent to Gemini, and indexable chunks are embedded and stored with excerpts in Pinecone.

## 1. Start the app on Pop!_OS or Linux

Open two terminals in the `LOGSYS` checkout. Stop any older backend or frontend for this project if it already occupies the same port.

**Terminal 1 · Backend with providers disabled**

```bash
test -x .venv/bin/python || python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt -c backend/requirements.lock
GEMINI_API_KEY= GROQ_API_KEY= PINECONE_API_KEY= .venv/bin/python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
```

The blank keys apply to this backend process. Any saved settings in `.env` remain available for the provider pass.

**Terminal 2 · Frontend**

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open [the dashboard](http://127.0.0.1:5173). The top bar should say **Backend connected**. If it says **Backend offline**, open [the backend health endpoint](http://127.0.0.1:8000/api/health) and [the same check through Vite](http://127.0.0.1:5173/api/health). Both should report `"status":"ok"`. The health check confirms that the server is running.

For a quick frontend check, open **Sample incident → Chat**, send a suggested question, follow a reference to Evidence or Timeline, and use **Reset chat** to clear the conversation.

## 2. Test the incident workflow without keys

Use **New incident** to open an uploadable incident. The **Sample incident** in the sidebar is a separate preview with canned chat prompts.

| File | Purpose |
| --- | --- |
| [demo_app.log](backend/data/demo/demo_app.log) | Log entries for the database, authentication service, and gateway |
| [demo_changes.json](backend/data/demo/context/demo_changes.json) | Timestamped pool-limit change |
| [demo_dependencies.json](backend/data/demo/context/demo_dependencies.json) | Declared service relationships |
| [demo_metrics.json](backend/data/demo/demo_metrics.json) | Baseline and saturation measurements, plus a later CPU spike |

1. Click **Add telemetry**. The file picker should open and the selected files should appear in **Sources**. Choose the first three files in the table and click **Upload files**. You should see three processed files and 14 new observations. With providers disabled, zero indexed chunks and the indexing notice are expected.
2. Click **Verify root cause**. Expect **Insufficient evidence** and a **Next check** for active connections, configured capacity, and database latency on `user-db` from **02:24:15 to 02:29:15 UTC**. Select **Inspect this window**. Timeline should have the service and time range prefilled.
3. Upload `demo_metrics.json` to the **same** incident. Expect 20 additional observations and **Verification out of date**. Click **Verify again**. Expect **Supported by evidence** and **Connection pool limit reduction on user-db**. The verdict should show observed impact on `auth-service` and `api-gateway`.
4. Check the Evidence tab. Expect the following five source-linked steps. Expand **Source excerpt** or choose **Open in timeline** to inspect a record. Open **Ranked hypotheses** and expand **CPU pressure** to see why the later spike does not explain the initial outage.

| UTC | Evidence |
| --- | --- |
| 02:29:00 | Pre-change baseline |
| 02:29:15 | Pool limit reduced from 100 to 20 |
| 02:30:00 | Pool at capacity, with database latency at 420 ms |
| 02:31:01 | `auth-service` reports a database connection timeout |
| 02:31:02 | `api-gateway` returns a 504 |

5. Check the telemetry overview. Expect **34 records**, **3 services**, **4 errors**, and **15 anomalies**. Source coverage should show **13 log records**, **20 metric samples**, and **1 change**. The UTC range should be **02:27:00–02:33:30** on **15 Jan 2024**. The dependency manifest appears in Sources because it supplies graph edges.
6. Hover over an **Event activity** bar or reach it with Tab. Expect the UTC window, total records, and a breakdown by event type. Click the bar or press Enter to open that window in Timeline. Click **Clear range**, then combine **Errors** with `auth-service`. Expect two rows, at **02:31:01** and **02:31:16 UTC**. Try **Anomalies** and check that the flagged metric fields are highlighted. The end of the time filter is exclusive.
7. Select **Graph**. It should fit the **Impact path** and select `user-db`. The arrows follow **user-db → auth-service → api-gateway**. Use the canvas controls to zoom, fit, and reset. Switch to **Dependency graph** and check that call arrows now point from caller to target. **All relationships** also shows source and error entities. Check the view again at 390 px and confirm that the canvas, badges, and right-side cards fit without horizontal page scrolling.
8. Select `user-db` in Graph and inspect **Caller impact**. The latest sample at **02:33:30 UTC** should show **15.2 ms** latency, **0.9%** error rate, and **12 / 20** pool connections. Hover over a latency point to see its time and value. The `auth-service` and `api-gateway` cards should show **Observed impact** and a short failure summary. Expand **Show all fields** on a metric row or **Recent signals** on a caller card. Check that each set of signals is sorted oldest first and that **Open in timeline** jumps to its exact source.
9. Open **Chat** and ask **“What caused the outage?”**. Expect **Local reply** with citations. Click one. At desktop width the source appears beside the conversation, which remains usable. At a mobile width such as **390 px**, the citation opens a bottom sheet. Try closing it with **Escape** or **Close source**, then reopen it and select **Open in timeline**. The exact row should open with its excerpt expanded. Use **Back to chat** to return to the conversation.
10. Reload the page and reopen the live incident from the sidebar. Confirm that the saved verdict and chat history are present. Re-upload one of the same files with the same name. The upload should add zero observations and the record count should stay at 34.
11. In **Chat**, ask **“What was the peak database latency for user-db?”**. Expect **Source-backed reply** with **30,045 ms** at **02:31:00 UTC** and a citation to `demo_metrics.json` record 11. **Agent trace → Grounded chat** should say it computed the peak from the incident's metric records without a provider call. The same question follows this route with Gemini enabled.

### Check the temporal evidence graph

1. Reopen an incident containing the four demo files. Opening the incident rebuilds the temporal graph from existing observations. Choose **Graph → Temporal evidence**. The graph should contain 34 observation nodes covering logs, metric samples, and the operational change, arranged in lanes by service and input type. UTC event order runs left to right. Labels become available as you zoom or select records.
2. Select a metric or change node. Its card should show the UTC timestamp and source filename with its record number. Choose **Open source in timeline** and confirm the exact row opens. Return to Graph and check that **Dependency graph** still shows the caller-to-target topology.
   In **Temporal evidence**, dragging a record should keep its position fixed in the lane. Drag the background to pan and use the zoom controls to inspect the graph.
3. Re-upload the four files. Expect zero new observations. The graph's observation nodes and relation counts should remain stable. Repeated `POST /api/sessions/{id}/finalize` requests return the same relation totals.
4. With Gemini enabled, upload a screenshot. Each extracted panel should appear as an observation node. A panel displaying a single timestamp with a date and timezone may enter point ordering; explicitly dated UTC intervals are represented as ranges. A clock range such as `02:30–02:34` should keep **Time unknown** in its card and retain its source link.
5. Choose **All relationships** and hover or select a record. Its connections should stand out while unrelated edges recede. Expand **Browse records and entities**, search `02:31:00`, and select the metric result for record 11. Expect the full source summary and a working link to Timeline. Zoom in, select another result, and drag a node to check that the zoom is retained. Repeat at 390 px and confirm the page fits horizontally.
6. Select the same record again to clear its selection, or select another node and click empty canvas. The selected-node card and persistent connection highlight should clear. Select a node, pan or zoom, and choose **Reset layout**. Expect the current graph view to return to its fitted layout with selection, hover highlight, and record search cleared. Repeat with the suspected-origin node to confirm it can also be deselected.

The graph labels shared request identifiers and conflicting readings. The root-cause verdict continues to come from the incident verifier's hypothesis checks. Screenshot extraction in the automated graph tests uses mocked provider output. Use section 3 for the live screenshot check.

### Check potential impact

Create a temporary replacement manifest with both original edges and a new caller that has no error logs.

```bash
cat > /tmp/logsys-test-dependencies.json <<'JSON'
{
  "kind": "service_dependencies",
  "dependencies": [
    { "caller": "api-gateway", "callee": "auth-service" },
    { "caller": "auth-service", "callee": "user-db" },
    { "caller": "checkout-service", "callee": "user-db" }
  ]
}
JSON
```

Upload it to the same incident. Select **Verify again** and open Graph. The original two callers should still show **Observed impact**. `checkout-service` should show **Potential impact** and no cited recent signals. The record count should stay at 34 because the manifest changes the dependency graph.

## 3. Test generated chat with Gemini

1. Open [Google AI Studio API keys](https://aistudio.google.com/app/apikey), sign in, and use **Create API key**. Select an existing project or create one in the dialog. Save the new key as `GEMINI_API_KEY` in the repo’s root `.env`. [Gemini API key documentation](https://ai.google.dev/gemini-api/docs/api-key)
2. From `LOGSYS`, run `test -f .env || cp .env.example .env` to create the file if needed. Edit it locally. Keep the keys out of Git and out of frontend environment variables. Start with these settings and replace the Gemini placeholder.

   ```dotenv
   GEMINI_API_KEY=paste_your_key_here
   LLM_MODEL=gemini-3.5-flash
   VISION_MODEL=gemini-3.5-flash
   GROQ_API_KEY=
   PINECONE_API_KEY=
   PINECONE_INDEX_NAME=logmind-index
   EMBEDDING_MODEL=gemini-embedding-001
   EMBEDDING_DIM=768
   ```

3. In Terminal 1, stop the backend with **Ctrl+C** and restart it with the command below. The frontend can stay running. This command clears inherited provider and model variables in its own process so `.env` supplies the settings.

   ```bash
   env -u GEMINI_API_KEY -u GROQ_API_KEY -u PINECONE_API_KEY \
     -u LLM_MODEL -u VISION_MODEL -u PINECONE_INDEX_NAME -u EMBEDDING_MODEL -u EMBEDDING_DIM \
     .venv/bin/python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
   ```

4. Reopen the live incident that has the four demo files. Ask **“When did auth-service report its first database timeout?”**. Expect a **Model reply** that cites `demo_app.log` line 7 and reports **02:31:01 UTC**. Open the citation and compare the reply with the original line. Existing records are available to keyword retrieval.
5. Follow up with **“Did that happen before the pool limit changed?”**. The answer should connect the first timeout to the change at **02:29:15 UTC** and say it happened **after** the change. **Agent trace** should group the run into Verification, Incident retrieval, and Grounded chat. Check **Previous sources** under retrieval and expand **Raw result** to see the original message. With a blank Pinecone key, zero semantic matches is expected.

To check screenshot upload, add [demo_dashboard.png](backend/data/demo/demo_dashboard.png) to a test incident while Gemini is enabled. Expect it to appear in **Sources** after vision processing. Timeline should show a **Screenshot** entry for each readable panel and link to its extracted record. A displayed clock range without a date or timezone should remain time uncertain. The telemetry overview counts extracted panels, while the event activity chart excludes undated records.

## 4. Test semantic retrieval with Pinecone

1. Sign in to the [Pinecone Console](https://app.pinecone.io/), select a project, open **API keys**, and create a project key. Use a key with permission to list indexes and read and write vectors in the chosen project. [Pinecone key documentation](https://docs.pinecone.io/admin/projects/manage-api-keys)
2. In `.env`, set `PINECONE_API_KEY` and keep your Gemini key. Use `PINECONE_INDEX_NAME=logmind-index` or set the name of your own test index. The backend can create a missing index on first upload if the key can create indexes. To create it in the console, choose **Bring your own vectors**, **dense**, **768 dimensions**, and **cosine**. Select a serverless configuration available to your project. Confirm that the exact index name matches `.env`. [Pinecone index documentation](https://docs.pinecone.io/guides/indexes/create-an-index)
3. Restart using the same provider-enabled command as in section 3. In your live incident, re-upload `demo_app.log` and `demo_metrics.json`. Expect **0 new observations** and **2 chunks indexed** for these two files. The upload associates the vectors with the exact source versions and the selected incident.
4. Once the index has the vectors, ask **“Which component returned a gateway timeout while waiting for authentication?”**. Expect a cited **Model reply** identifying `api-gateway` and a positive **Semantic matches** count in **Agent trace**. If it is zero immediately after upload, wait briefly and ask again. The semantic and keyword paths can both contribute to one reply.
5. Create another live incident without uploads. Its Evidence and Timeline should be empty, and live chat should prompt for telemetry. Switch back to the first incident and check that the original evidence is still there.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| **Canned replies** | Switch from the sidebar’s Sample incident to your live incident. |
| **Local reply** with Gemini configured | Confirm the backend was restarted with the provider-enabled command. Read **Agent trace → Grounded chat** and the backend terminal. Gemini 401/403 responses point to credentials or access. A 429 points to quota or rate limits. A 404 can mean the configured model is unavailable to the project. [Gemini troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting) |
| **0 chunks indexed** with keys configured | Check the upload warnings and backend logs. Confirm both provider keys, the project and index name, and dimension 768. |
| **0 semantic records** after indexing | Ask in the same incident where you uploaded the files. Check for a successful upload, allow time for vectors to become searchable, and retry. |
| **Port in use** | Stop the earlier frontend or backend process, then rerun the matching startup command. |
| **File picker does not open** | Check that the backend is connected and the incident is live. The **Sources** drop zone also accepts drag and drop. |

## Automated checks

Stop or leave the dev processes running independently. From `LOGSYS`, run the following.

```bash
.venv/bin/python -m pip install -r backend/requirements-dev.txt
.venv/bin/python -m pytest -q
node frontend/src/lib/incident-graph.test.js
npm --prefix frontend run build
git diff --check
```

The backend suite uses temporary incidents and mocked provider responses. Use sections 2–4 to verify browser interactions and the live provider calls.

For the labelled retrieval checks and the optional live Graph RAG and LLM comparisons, see [EVALUATION.md](EVALUATION.md).

### Clean Docker Compose smoke test

On a host with Docker Compose, run the following from a fresh checkout with a local `.env` file. Use the app through port 3000 for the walkthrough above so the requests exercise the frontend proxy.

```bash
docker compose up --build --wait
docker compose ps
curl -fsS http://localhost:3000/api/health
```

## 5. Phase 0–1 close-out checks (19 November 2026)

Use **CPython 3.12 on Linux**, matching `backend/Dockerfile`. Run commands from the `LOGSYS` repository root, not from `backend`. Direct dependencies are pinned in `backend/requirements.txt`; `backend/requirements.lock` records the tested transitive versions. Development installation applies that constraints file automatically.

### A. Verify a clean installation

Keep your working environment intact and install into a disposable environment.

```bash
python3.12 -m venv /tmp/logsys-clean-venv
/tmp/logsys-clean-venv/bin/python -m pip install -r backend/requirements-dev.txt
/tmp/logsys-clean-venv/bin/python -m pip check
/tmp/logsys-clean-venv/bin/python -m pytest -q
npm --prefix frontend ci
npm --prefix frontend run build
```

The existing environment passes `pip check`. The fresh installation is **not yet verified**. This session's package registry returned no matching distributions. Docker Compose is also missing and Docker daemon access is denied here. These are open verification gates, not passing results.

### B. Verify unit conversion and evidence quality

Create `/tmp/logsys-units.json` with the following contents and upload it to a **new** incident.

```json
[
  {"timestamp":"2024-02-01T12:00:00Z","service":"test-db","cpu_percent":"94%","db_latency_s":0.42,"error_rate":"18%","unsupported":"12GiB","stream_id":"lab/test-db"},
  {"timestamp":"2024-02-02T12:00:00Z","service":"test-db","cpu_percent":20,"db_latency_ms":900,"error_rate":0.01,"stream_id":"lab/test-db"}
]
```

The first row should show `cpu_percent=94`, `db_latency_ms=420`, and `error_rate=0.18`. Its original excerpt must still contain the strings and seconds value. Upload feedback and the source details should warn that `12GiB` was excluded. The Stream badge should show `lab/test-db`.

Supported conversions are explicit percent values for CPU/memory, percent-to-ratio for error rates, and seconds-to-milliseconds for `db_latency_s`, `api_latency_s`, and `latency_s`. Canonical `_ms` fields also accept explicit `ms` or `s` suffixes. Bare `error_rate` is a ratio from 0 to 1; bare CPU/memory percentages are from 0 to 100. Unknown units are not inferred. Other finite numeric fields remain readings under their existing names. Absolute anomaly thresholds and a five-distinct-instant baseline are heuristics. The deviation tolerance is the larger of 2.5 standard deviations, 10% of baseline magnitude, and 1 unit (0.01 for error ratios). Unknown timestamps cannot form a temporal baseline.

### C. Ask within a service and UTC window

1. Open Timeline in the units incident. Set Service to `test-db`, From to `2024-02-01 00:00:00`, and Until to `2024-02-02 00:00:00`. Click **Apply range**. Both inputs mean UTC; the end is exclusive.
2. Open Chat and enable **Use timeline scope**. Confirm the displayed service and range. Ask **What was the peak database latency for test-db?** Expect **420 ms**, with a citation to record 1. Disable the scope and repeat. Expect **900 ms**, record 2.
3. Scope an empty day. Expect no dated observations for that scope, never an answer borrowed from the other day. Unknown-time records are omitted when a time bound is active.
4. Ask a dependency question with a range selected. The bounded dependency hop must use the same range. A caller-to-target hop may add the explicitly declared neighboring service. Chat's scoped reply does not replace the saved full-incident verification.
5. Open Agent trace. It should show the selected scope, semantic-search status, record counts, and source groups. A group means a declared stream or a byte-identical file version. It is not a calibrated confidence score.
6. Use **Reset chat** on this test incident. It should clear the saved conversation after reload. A response that began before reset must not repopulate it. Uploads and graph records remain available.

### D. Verify screenshots with providers enabled

Restart the backend after the code changes using section 3's provider-enabled command. Use a **new** incident for the updated fixtures. Re-uploading an updated fixture into an older incident retains both source versions, as intended.

Upload the four text/JSON fixtures, then `backend/data/demo/demo_dashboard.png`. This dashboard is generated directly from `demo_metrics.json`. It prints `user-db`, the date **2024-01-15**, range **02:27:00–02:33:30 UTC**, and stream ID `demo/user-db/pool-monitor`. Expected maxima are **30,045 ms** database latency, **20 active connections**, **96.1% CPU**, and **96.1% error rate**. The limit drops from **100 to 20** at **02:29:15 UTC**.

Each readable panel should have an exact source link, typed readings with units, and an interval when the complete date/timezone is legible. Maxima and last values are stored as separate readings. A maximum over a range must not receive an invented peak timestamp. Graph should label interval evidence as a range, and overlapping ranges must not create a precedence edge between one another. Timeline filtering uses interval overlap. Clock-only screenshots remain time uncertain.

Panels and metric rows with the printed stream ID share a provenance group. Distinct source records remain citeable. The active deterministic verifier still uses point-in-time metrics, changes, and caller error logs; screenshots supply additional context without adding independent votes or replacing decisive pool samples. Unknown source lineage is not inferred merely from equal values. A declaration is required for differently encoded exports and screenshots.

The vision cache now uses extraction schema version 2. Older saved observations remain readable; a same-byte retry upgrades their extraction fields without changing the citation ID or increasing the record count. Panel matching preserves existing citations when extraction order changes. A genuinely new or ambiguous unmatched panel is retained as a separate record rather than retargeting an older citation. Re-upload the screenshot, reload, and reopen its graph and Timeline sources. Verify zero new records on an unchanged retry. The final live v2 extraction check is **pending**. The earlier successful live pilot used v1.

### E. Verify failure and isolation behavior

- With keys blank, local metrics and source-backed lookups work; screenshots show an actionable Gemini requirement. In Agent trace, semantic search says **Disabled**.
- With working keys and a legitimate empty vector query, semantic search says it succeeded with no matches. Provider failures and embedding failures are labelled separately. Generation distinguishes rate limits, timeouts, provider errors, and rejected output while retaining source-backed fallback.
- If an existing index has the wrong dimension, the upload warning reports expected and actual dimensions. The application does not delete or recreate that index.
- Partial indexing leaves local observations available. Retry uses the same vector IDs and adds no duplicate observations. The backend tests exercise partial batch failures and retry.
- Create incident A with telemetry and an empty B. Start a slow A request and switch to B, including while an upload is processing. B must not display A's files, chat, verdict, or graph. Return to A and check persistence. Use the browser's network throttling for the slow-request check. The final throttled walkthrough remains open.
- The legacy global `/api/query` route now loads synthetic fallback only with an explicit `"demo": true` request. Session queries reject that flag. No-match or failed retrieval cannot silently load demo telemetry.

### F. Run an isolated Docker check

Install Docker's official [Compose plugin](https://docs.docker.com/compose/install/linux/) if `docker compose version` is unavailable. For daemon access, follow the [Linux post-install guide](https://docs.docker.com/engine/install/linux-postinstall/). Confirm `docker info` succeeds as your normal user before proceeding. These commands use separate ports, a separate Compose project, blank provider keys, and a new data directory. They do not stop the existing dev servers or mount your saved incidents.

```bash
test -f .env || cp .env.example .env
export COMPOSE_PROJECT_NAME=logsys-smoke
export COMPOSE_FILE=docker-compose.yml:docker-compose.smoke.yml
export BACKEND_PORT=8300 FRONTEND_PORT=3300
export LOGSYS_DATA_DIR="$(mktemp -d /tmp/logsys-compose-data.XXXXXX)"
docker compose up --build --wait
docker compose ps
python3 scripts/compose_smoke.py --restart
# Stop only this isolated stack after reviewing the result.
docker compose down
```

The script checks health through the nginx proxy, creates its own disposable incident, ingests 34 records, loads the graph, asks the exact peak lookup, opens record 11 through its citation, retries the upload, restarts the backend, and checks saved telemetry, graph, and chat. It deletes only the incident it created. The Docker run is **pending**.

### G. Record final evaluation and review

```bash
.venv/bin/python -m pytest -q
node frontend/src/lib/incident-graph.test.js
npm --prefix frontend run build
.venv/bin/python backend/evaluate.py > evaluation/reports/phase01-offline.json
env -u GEMINI_API_KEY -u GROQ_API_KEY -u PINECONE_API_KEY \
  .venv/bin/python backend/evaluate.py --live --provider-gap-seconds 15 \
  > evaluation/reports/phase01-live-attempt.json
git diff --check
```

The offline report is saved with fixture hashes and the Git revision/dirty state. Its six lookup cases are development fixtures, not held-out accuracy measurements. The live attempt saved on 19 November records DNS unavailability and **no new provider results**. Re-run it on your host to replace that blocked attempt with the final raw outputs. Provider setup and key sources are in sections 3–4.

The original entity graph and retrieval sources are frozen at `f22e968` under `backend/evaluation_baseline`, with checksums. Live vector-only and Graph RAG arms share vector matches, generation settings, and the original synthesis prompt; only the frozen graph context changes. The current verifier is measured separately. In-process evaluation runs ingest steps sequentially and does not measure HTTP concurrency or Docker performance.

The stale Cypress example was removed because it had nonexistent selectors and no installed Cypress runner. The actual gates are backend regressions, Node graph tests, the production build, and the browser walkthrough. After A–G pass, review the integrated feature branch, commit, push, merge into the fork's main, and repeat the clean-checkout checks. Authentication and multi-user ownership remain outside this private synthetic-data demo; review them before public deployment.
