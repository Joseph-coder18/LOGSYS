# Test LogMind locally

Run the local workflow first, then test Gemini chat and Pinecone retrieval. Use the bundled synthetic files for these checks. With providers enabled, selected telemetry is sent to Gemini, and indexable chunks are embedded and stored with excerpts in Pinecone.

## 1. Start the app on Pop!_OS or Linux

Open two terminals in the `LOGSYS` checkout. Stop any older backend or frontend for this project if it already occupies the same port.

**Terminal 1 · Backend with providers disabled**

```bash
test -x .venv/bin/python || python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
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
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
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
