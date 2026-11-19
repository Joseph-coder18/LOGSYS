# LogMind-X evaluation

This pilot separates finding the right records, determining the incident cause, and writing an answer. The repository includes six synthetic lookup cases and three variants of the bundled database incident.

## Local results

| Check | Control | With the component |
| --- | --- | --- |
| Labelled observation at rank 1 | Keyword retrieval · 2/6 | Keyword plus one dependency hop · 6/6 |
| Labelled observation in the first five | Keyword retrieval · 2/6 | Keyword plus one dependency hop · 6/6 |
| Median per-case retrieval time in one local run | About 0.17 ms | About 0.35 ms |
| Exact fact and source on lookup questions | Previous local chat · 4/6 | Automatic routing · 6/6, with zero model calls |
| Exact times and source, full database chronology question | Local chat · 0/2 | Extractive control over verifier records · 2/2 |

The retrieval gain came from four questions that named a caller or callee and buried the matching record beneath plausible keyword distractors. The two directly named service questions stayed at rank 1. Each arm returned at most five question records. These intentionally diagnostic cases establish the effect of using declared topology on this workload. They are development fixtures, so they do not estimate general recall or root-cause accuracy.

The model-free verifier supported `user-db` on the complete incident and returned insufficient evidence when either metrics or topology was removed. The three runs share the same underlying incident. The previous local chat answered four of the six lookup questions by citing records found through dependency expansion. The extractive control matched 6/6. Automatic routing now matches the same six facts and exact sources in the production chat path, bypassing generation and semantic retrieval for those questions. When an older observation contains a different value or the question specifies a time window, chat defers to the usual generation and fallback path.

For explicitly scoped peak questions, the automatic path calculates the maximum across the service's uploaded metric rows and cites the winning row. The regression test finds **30,045 ms** for `user-db` at **02:31:00 UTC** even though that record falls outside the five highest-ranked question records. Questions about event order still use the synthesis path. The extractive chronology control shows that the timestamps can also be recovered locally.

### Live-provider results

The live run from the project owner's machine produced the following results. The saved replies were rescored after fixing a matcher that rejected a version number at the end of a sentence.

| Answer arm | Fact and source matched | Generated replies | Median reply latency |
| --- | --- | --- | --- |
| Direct excerpts over graph-backed records | 6/6 | 0 | Unmeasured |
| Local chat with graph | 4/6 | 0 | 1.1 ms |
| Gemini with graph disabled | 1/6 | 5/6 | 1,155 ms |
| Gemini with graph enabled | 4/6 | 3/6 | 648 ms |

In the graph-enabled Gemini arm, three correct replies were generated and one came from local fallback. The terminal log reveals HTTP 429 on three of six graph-enabled lookups and one of six graph-disabled lookups. Gemini also returned HTTP 429 on the chronology question. Both chronology chat arms used the same local reply, which omitted the exact timestamps. The extractive control included both with citations. Median latency includes failed calls and fallback replies.

Pinecone returned two shared vector matches for the legacy comparison. Gemini returned HTTP 429 for both legacy synthesis arms, LogMind-X synthesis, and screenshot extraction. LogMind-X still retrieved four semantic records and identified `user-db` using the verifier and local reply. The provider limits leave the model-quality comparison incomplete. Docker Compose and interactive browser checks remain open.

### Paced live run

The second live run used a different Gemini project key and a 15-second minimum gap between text and vision calls. Fifteen of 17 calls returned successfully. The two errors were HTTP 503 and 504. There were no HTTP 429 responses. Gemini extracted four screenshot panels.

| Answer arm | Fact and source matched | Generated replies | Median provider call time |
| --- | --- | --- | --- |
| Direct excerpts over graph-backed records | 6/6 | 0 | No provider call |
| Local chat with graph | 4/6 | 0 | No provider call |
| Gemini with graph disabled | 1/6 | 4/6 | 10.5 s |
| Gemini with graph enabled | 6/6 | 5/6 | 12.9 s |

The graph-enabled arm had five correct generated replies and one correct local fallback. Times are medians across attempted calls and exclude pacing waits. The run reproduced retrieval recall@5 of 6/6 with the graph and 2/6 with keyword search.

Gemini answered the chronology question with the correct order and both exact cited timestamps. The saved report originally scored this 0/2 because the matcher rejected ISO timestamps containing `T`. Rescoring the same reply gives 2/2, matching the extractive control. The default local chat gave the correct order and omitted the exact times.

All three RCA arms identified `user-db` for the complete incident. Both legacy replies put the change around 02:29:30 UTC, while the change record places it at 02:29:15 UTC. LogMind-X cited the exact change and caller records, but its generated summary quoted 30,002 ms as the peak. The complete metrics contain a peak of 30,045 ms. This one incident illustrates the value of provenance and the need to check synthesized claims against the full records.

## Reproduce

From the repository root:

```bash
.venv/bin/python backend/evaluate.py > /tmp/logsys-evaluation.json
.venv/bin/python backend/evaluate.py --live --provider-gap-seconds 15 > /tmp/logsys-evaluation-live.json
```

The first run uses temporary sessions and makes zero provider calls. The second opts into configured providers and uses synthetic inputs only. Set the gap based on the project's active limits in Google AI Studio. The example spaces text and vision request starts by 15 seconds. Pacing can reduce request bursts. Daily and token quotas still apply. The benchmark alternates which graph arm runs first and records rate-limited calls, provider call time, and pacing wait separately from end-to-end latency. The legacy comparison uses an isolated Pinecone namespace and attempts to remove it after the run. Check stderr for its name if cleanup fails.

The live run includes two controlled comparisons:

1. **What does generation add?** Compare `model_off` with `model_on_graph_on` on the same question records and verifier decision. Semantic retrieval is held off in this pair. Compare both to `extractive`. Use `synthesis` to check whether the reply correctly compares the two events and cites each exact UTC timestamp. Count failed calls and fallback outputs against the model arm when reporting coverage.
2. **What does Graph RAG add?** Compare `model_on_graph_off` with `model_on_graph_on` on the labelled lookup questions. The legacy arms separately reuse the same vector matches and synthesis settings, with only the graph context toggled. The LogMind-X arm uses the same uploaded files and separately reports its end-to-end time.

The `automatic` arm measures production lookup routing. The forced `model_on_*` arms bypass that routing to preserve the generation comparison. The paced live results predate the routing change.

## Review the live outputs

- Record whether the output states the correct origin or appropriately abstains, whether each factual and causal claim is supported by the cited record, and whether it answers the actual question. A citation can resolve to a real record and still fail to support the claim.
- For the chronology question, confirm that saturation at **02:30:00 UTC** precedes the caller timeout at **02:31:01 UTC**. The automated score checks each time and its source. Review the stated ordering and any additional claims by hand.
- Review the legacy outputs manually because they lack claim-level citations and structured origin fields. Treat model/provider failure as an unavailable result.
- Expand to held-out incidents and unanswerable questions before reporting overall accuracy. Keep all variants of one incident together when splitting cases. Retain generation for question classes where reviewed outputs improve usefulness or correctness enough to justify additional calls and latency.

## Phase 0–1 close-out snapshot · 19 November 2026

The current offline report is saved as a review artifact at `evaluation/reports/phase01-offline.json`. It retains raw claims/citations, fixture and runtime source hashes, Python version, Git revision and dirty-state flag, provider status, and matched-budget scores. Current development results remain **2/6** keyword recall@5, **6/6** with dependency expansion, and **6/6** automatic fact/source matches with zero generation requests. The complete incident is supported; removing metrics or topology produces insufficient evidence. These are synthetic development checks, not a held-out accuracy estimate.

Original sources from `f22e968` are frozen with checksums in `backend/evaluation_baseline`. The benchmark reconstructs the original file/entity graph using the original log/metrics parsers and replays the original two-hop retrieval algorithm. It does not inject modern observation nodes or temporal edges into that arm. Vector matches, the original synthesis prompt, configured model, timeout and output budget are shared with the vector-only control. The current safe generation wrapper reports failures rather than recreating the original cause-generating fallback. Node order is canonicalized for reproducibility without adding graph content. The offline preflight records graph construction; live generation still requires reachable providers. In-process ASGI ingest is sequential, so it does not measure HTTP worker scheduling, concurrent throughput or Docker performance.

The new live attempt at `evaluation/reports/phase01-live-attempt.json` reports **DNS unavailable** for text, vision and vectors. No new provider-quality result was obtained. The successful paced pilot above predates this final fixture, extraction-v2 and scope work; keep that distinction when presenting results. Replace the blocked attempt by re-running the documented paced command on the project owner's host.

The updated screenshot fixture is generated from the same exported metrics by `scripts/build_demo_dashboard.py`. Evaluation labels and input hashes live in `evaluation/cases/db-pool-reduction.json`, outside the uploaded telemetry. The manifest is never supplied to retrieval or generation.

Time-scoped retrieval now applies UTC overlap bounds to keyword candidates, exact semantic source resolution and declared dependency neighbors. Chat bounds the final packet to 20 distinct records, retains uncertainty and typed aggregation, and identifies shared telemetry groups. The active deterministic verifier continues to require point samples for its causal checks. Screenshots neither supply invented peak times nor add independent votes for a stream already exported as metrics. A broader held-out incident suite and claim-by-claim human review remain Phase 4 work.

On the aligned fixture, the frozen preflight returns three seed service/database identities with singleton paths. It adds no dated observation or caller path to the model packet. That limits what the original graph context can substantiate; a successful API call alone is not evidence of a Graph RAG accuracy gain. The declared-dependency lookup comparison above measures a separate, observable retrieval gain.
