"""Run with PYTHONPATH=backend python -m unittest discover -s backend/tests."""
import json
from pathlib import Path
from hashlib import sha256
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from main import app
from models import RCAOutput
from services import session_store
from services.metrics_parser import parse_metrics


class LiveTimelineTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for key, value in {
            "_SESSIONS_DIR": self.root,
            "_INDEX_PATH": self.root / "index.json",
        }.items():
            patcher = patch.object(session_store, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "PINECONE_API_KEY"):
            patcher = patch(f"config.{key}", "")
            patcher.start()
            self.addCleanup(patcher.stop)
        async def run_inline(function, *args):
            return function(*args)

        worker = patch("api.routes.run_in_threadpool", run_inline)
        worker.start()
        self.addCleanup(worker.stop)
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.session = (await self.client.post("/api/sessions", json={"name": "Timeline test"})).json()["id"]
        self.url = f"/api/sessions/{self.session}"

    async def upload(self, *files):
        return await self.client.post(f"{self.url}/ingest", files=[("files", file) for file in files])

    async def test_upload_is_source_linked_and_idempotent_without_providers(self):
        logs = ("app.log", b"unknown time\n2024-01-15T08:01:00+05:30 | auth-service | ERROR | database timeout")
        metrics = ("metrics.csv", b"timestamp,cpu_percent\n2024-01-15T02:30:00Z,94\n")
        bad = ("invalid.json", b"[1]")
        outside = ("../escape.log", b"ignore")
        response = await self.upload(logs, metrics, bad, outside)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed_files"], ["app.log", "metrics.csv"])
        self.assertEqual(result["observations_added"], 3)
        self.assertEqual(result["indexing_status"], "skipped")
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual((await self.client.get(self.url)).json()["files"], ["app.log", "metrics.csv"])

        first = (await self.client.get(f"{self.url}/observations", params={"limit": 2})).json()
        self.assertEqual(first["next_offset"], 2)
        cpu, timeout = first["observations"]
        self.assertEqual(cpu["source_file"], "metrics.csv")
        self.assertEqual(cpu["source_position"], 1)
        self.assertIn("threshold breach", cpu["anomalies"][0])
        self.assertEqual(timeout["timestamp"], "2024-01-15T02:31:00.000000+00:00")
        self.assertEqual(timeout["service"], "auth-service")
        self.assertEqual(timeout["source_position"], 2)
        self.assertIn("database timeout", timeout["excerpt"])

        page = (await self.client.get(f"{self.url}/observations", params={"offset": 2})).json()
        self.assertIsNone(page["next_offset"])
        self.assertIsNone(page["observations"][0]["timestamp"])

        self.assertEqual((await self.upload(logs, metrics)).json()["observations_added"], 0)
        self.assertEqual((await self.client.get(f"{self.url}/observations")).json()["observations"], [cpu, timeout, *page["observations"]])
        second = (await self.client.post("/api/sessions")).json()["id"]
        self.assertEqual((await self.client.get(f"/api/sessions/{second}/observations")).json()["observations"], [])
        self.assertEqual((await self.client.delete(self.url)).status_code, 200)
        self.assertFalse((self.root / self.session / "observations.sqlite3").exists())
        self.assertEqual((await self.client.get(f"{self.url}/observations")).status_code, 404)

    async def test_indexing_failure_leaves_locally_saved_observations(self):
        async def run_inline(function, *args):
            return function(*args)

        with (
            patch("api.routes.run_in_threadpool", run_inline),
            patch("config.GEMINI_API_KEY", "test-key"),
            patch("config.PINECONE_API_KEY", "test-key"),
            patch("services.embedder.embed_batch", return_value=[[0.0] * 768]),
            patch("services.pinecone_store.upsert_chunks") as upsert,
        ):
            result = (await self.upload(("app.log", b"2024-01-15T02:31:00Z | auth-service | ERROR | database timeout"))).json()
        upsert.assert_not_called()
        self.assertEqual(result["indexing_status"], "failed")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len((await self.client.get(f"{self.url}/observations")).json()["observations"]), 1)

    async def test_oversized_upload_is_not_registered(self):
        with patch("api.routes._MAX_FILE_BYTES", 8):
            result = (await self.upload(("app.log", b"this is too long"))).json()
        self.assertEqual(result["status"], "failed")
        self.assertIn("upload limit", result["errors"][0])
        self.assertEqual((await self.client.get(self.url)).json()["files"], [])

    async def test_screenshot_retry_persists_valid_panels_without_inventing_timestamps(self):
        async def run_inline(function, *args):
            return function(*args)

        image = ("dashboard.png", b"test image bytes")
        reading = json.dumps({"panels": [{
            "panel": "API latency", "observation": "Peak of 400 ms",
            "visible_time_range": "02:30–02:34", "service": None,
        }]})
        with (
            patch("config.GEMINI_API_KEY", "fake"),
            patch("api.routes.run_in_threadpool", run_inline),
            patch("services.image_analyzer._read_screenshot", side_effect=["invalid", reading]) as vision,
        ):
            failed = (await self.upload(image)).json()
            self.assertEqual(failed["status"], "failed")
            self.assertEqual((await self.upload(image)).json()["observations_added"], 1)
            self.assertEqual((await self.upload(image)).json()["observations_added"], 0)
            self.assertEqual(vision.call_count, 2)
        observation, = (await self.client.get(f"{self.url}/observations")).json()["observations"]
        self.assertEqual(observation["source_label"], "API latency")
        self.assertEqual(observation["modality"], "image")
        self.assertEqual(observation["original_timestamp"], "02:30–02:34")
        self.assertIsNone(observation["timestamp"])
        self.assertIsNone(observation["service"])

    async def test_temporal_graph_reuses_source_ids_across_all_modalities_and_reuploads(self):
        demo = Path(__file__).resolve().parents[1] / "data" / "demo"
        names = ("demo_app.log", "demo_metrics.json", "context/demo_changes.json", "context/demo_dependencies.json")
        files = [(Path(name).name, (demo / name).read_bytes()) for name in names]
        panels = {"panels": [
            {"panel": "Known sample", "observation": "Latency 420 ms", "service": "user-db",
             "visible_time_range": "2024-01-15T02:30:00Z"},
            {"panel": "Time range", "observation": "Latency rises", "service": "user-db",
             "visible_time_range": "02:30–02:34"},
        ]}
        with (
            patch("config.GEMINI_API_KEY", "test"),
            patch("services.image_analyzer._read_screenshot", return_value=json.dumps(panels)) as vision,
        ):
            uploaded = (await self.upload(*files, ("dashboard.png", b"fixture image"))).json()
            self.assertEqual(uploaded["observations_added"], 36)
            graph = (await self.client.get(f"{self.url}/graph")).json()
            nodes = {node["id"]: node for node in graph["nodes"] if node.get("observation_id")}
            self.assertEqual(len(nodes), 36)
            self.assertEqual({node["modality"] for node in nodes.values()}, {"log", "metrics", "change", "image"})
            self.assertEqual(sum(node["timestamp"] is not None for node in nodes.values()), 35)
            for observation_id, node in nodes.items():
                record = (await self.client.get(f"{self.url}/observations/{observation_id}")).json()
                self.assertEqual(record["source_position"], node["source_position"])
                self.assertEqual(record["source_file"], node["source_file"])
            unknown = next(node["id"] for node in nodes.values() if node["source_label"] == "Time range")
            self.assertFalse(any(unknown in (edge["source"], edge["target"]) for edge in graph["edges"]
                                 if edge["rel"] in {"precedes", "propagates_to", "supports", "contradicts"}))
            self.assertEqual((await self.upload(*files, ("dashboard.png", b"fixture image"))).json()["observations_added"], 0)
            self.assertEqual((await self.client.get(f"{self.url}/graph")).json(), graph)
            self.assertEqual(vision.call_count, 1)

    async def test_finalize_repairs_legacy_nodes_without_reuploading(self):
        await self.upload(("old.log", b'2024-01-15T08:01:00+05:30 | auth-service | ERROR | timeout\nservice=auth-service ERROR undated'))
        events = (await self.client.get(f"{self.url}/observations")).json()["observations"]
        graph = session_store.get_session_graph(self.session)
        graph.remove_nodes_from([node for node, attrs in graph.nodes(data=True) if attrs.get("observation_id")])
        for node in ("log-auth-service-a1234567", "log-auth-service-b1234567"):
            graph.add_node(node, type="ObservationLog", modality="log", service="auth-service", timestamp=9999999999)
        graph.add_edge("auth-service", "invented-db", rel="propagates_to", timestamp=9999999999)
        session_store.save_session_graph(self.session, graph)
        first = await self.client.post(f"{self.url}/finalize")
        self.assertEqual(first.status_code, 200)
        self.assertEqual((await self.client.post(f"{self.url}/finalize")).json(), first.json())
        repaired = (await self.client.get(f"{self.url}/graph")).json()
        observations = {node["id"]: node for node in repaired["nodes"] if node.get("observation_id")}
        self.assertEqual(set(observations), {event["id"] for event in events})
        dated = next(node for node in observations.values() if node["timestamp"] is not None)
        self.assertTrue(dated["utc_timestamp"].startswith("2024-01-15T02:31:00"))
        self.assertTrue(any(node["timestamp"] is None for node in observations.values()))
        self.assertFalse(any(node["id"].startswith("log-auth-service-") for node in repaired["nodes"]))

    async def test_correlation_keeps_multiple_relations_and_numeric_ids_out_of_metrics(self):
        log = {"timestamp": "2024-01-15T02:30:00Z", "service": "auth-service", "level": "ERROR",
               "message": "Queue unavailable", "request_id": 1234}
        metric = {"timestamp": "2024-01-15T02:30:05Z", "service": "auth-service", "cpu_percent": 20, "request_id": 1234}
        await self.upload(("app.log", json.dumps(log).encode()), ("metrics.json", json.dumps([metric]).encode()))
        graph = (await self.client.get(f"{self.url}/graph")).json()
        self.assertEqual({edge["rel"] for edge in graph["edges"] if edge.get("derived_by") == "temporal_edges"},
                         {"precedes", "supports"})
        source = next(node for node in graph["nodes"] if node.get("modality") == "metrics" and node.get("observation_id"))
        self.assertEqual(source["metric_values"], {"cpu_percent": 20})
        first = (await self.client.post(f"{self.url}/finalize")).json()
        self.assertEqual(first["precedes"], 1)
        self.assertEqual(first["supports"], 1)
        self.assertEqual((await self.client.post(f"{self.url}/finalize")).json(), first)

    async def test_teammate_log_fixture_retains_explicit_calls_and_compute_mentions(self):
        fixture = Path(__file__).resolve().parents[2] / "demo_logs.txt"
        await self.upload((fixture.name, fixture.read_bytes()))
        graph = (await self.client.get(f"{self.url}/graph")).json()
        calls = {(edge["source"], edge["target"]) for edge in graph["edges"] if edge["rel"] == "depends_on"}
        self.assertIn(("frontend", "auth"), calls)
        self.assertIn(("frontend", "orders"), calls)
        self.assertTrue(any(edge["target"] == "node-089" and edge["rel"] == "mentions" for edge in graph["edges"]))
        self.assertTrue(any(edge["rel"] == "propagates_to" for edge in graph["edges"]))

    async def test_dependency_question_retrieves_exact_record_past_keyword_budget(self):
        from services.incident_retrieval import query_terms, retrieve_question_records

        lines = [
            {"timestamp": "2024-01-15T02:30:00Z", "service": "orders-db", "level": "INFO",
             "message": "Connection pool limit: 24 connections"},
            *[{"timestamp": f"2024-01-15T02:30:0{i}Z", "service": "checkout-api", "level": "INFO",
               "message": "Connection limit check completed"} for i in range(1, 7)],
        ]
        manifest = {"kind": "service_dependencies", "dependencies": [{"caller": "checkout-api", "callee": "orders-db"}]}
        await self.upload(
            ("app.log", "\n".join(json.dumps(line) for line in lines).encode()),
            ("dependencies.json", json.dumps(manifest).encode()),
        )
        question = "What is the connection limit for the dependency of checkout-api?"
        directory = self.root / self.session
        keywords, *_ = retrieve_question_records(directory, query_terms(question), use_graph=False)
        graph, _, added, links = retrieve_question_records(directory, query_terms(question))
        self.assertEqual(len(keywords), len(graph))
        self.assertTrue(all(event.service == "checkout-api" for event in keywords))
        self.assertEqual(graph[0].source_position, 1)
        self.assertEqual(added, 1)
        self.assertEqual(links[0]["source_file"], "dependencies.json")
        with (
            patch("services.grounded_chat.configured_provider", return_value="test"),
            patch("services.grounded_chat.semantic_retrieval_enabled", return_value=True),
            patch("services.grounded_chat.retrieve_semantic") as semantic,
            patch("services.grounded_chat.generate_text") as generate,
        ):
            reply = (await self.client.post(f"{self.url}/query", json={"query": question})).json()
            semantic.assert_not_called()
            generate.assert_not_called()
        self.assertEqual(reply["answer_mode"], "extractive")
        self.assertEqual(reply["agent_trace"][-1]["metrics"]["model_calls"], 0)
        self.assertTrue(any(
            "24 connections" in claim["text"] and claim["citations"][0]["observation_id"] == graph[0].id
            for claim in reply["claims"] if claim["citations"]
        ))
        self.assertEqual(reply["decision"], "insufficient_evidence")

    async def test_peak_lookup_uses_full_session_metric_series(self):
        from config import DEMO_DIR
        from services.incident_retrieval import query_terms, retrieve_question_records

        metrics = DEMO_DIR / "demo_metrics.json"
        await self.upload((metrics.name, metrics.read_bytes()))
        question = "What was the peak database latency for user-db?"
        directory = self.root / self.session
        shortlist, *_ = retrieve_question_records(directory, query_terms(question))
        self.assertTrue(all(event.metric_values.get("db_latency_ms") != 30045 for event in shortlist))

        reply = (await self.client.post(f"{self.url}/query", json={"query": question})).json()
        self.assertEqual(reply["answer_mode"], "extractive")
        self.assertEqual(reply["agent_trace"][-1]["metrics"]["model_calls"], 0)
        self.assertIn("30,045 ms", reply["answer"])
        citation, = reply["claims"][0]["citations"]
        row = (await self.client.get(f'{self.url}/observations/{citation["observation_id"]}')).json()
        self.assertEqual(row["metric_values"]["db_latency_ms"], 30045)
        self.assertEqual(row["source_position"], 11)

        scoped_question = {"query": "What was the peak database latency for user-db in January?"}
        scoped_reply = (await self.client.post(f"{self.url}/query", json=scoped_question)).json()
        self.assertEqual(scoped_reply["answer_mode"], "local")

    async def test_lookup_defers_when_an_older_record_has_a_different_value(self):
        lines = [
            {"timestamp": f"2024-01-15T02:30:0{i}Z", "service": "export-worker", "level": "INFO",
             "message": f"CPU reached {75 if i == 0 else 91} percent"} for i in range(7)
        ]
        await self.upload(("app.log", "\n".join(json.dumps(line) for line in lines).encode()))
        reply = (await self.client.post(f"{self.url}/query", json={
            "query": "What CPU percentage did export-worker reach?",
        })).json()
        self.assertEqual(reply["answer_mode"], "local")

    def test_legacy_provider_failure_never_calls_an_error_a_cause(self):
        from agents.rca_agent import RCAAgent

        evidence = [{"metadata": {"modality": "log"}, "snippet": "ERROR connection timed out", "score": 0.6}]
        with (
            patch("agents.rca_agent.configured_provider", return_value="Gemini"),
            patch("agents.rca_agent.generate_text", return_value='{"summary":"incomplete JSON schema"}'),
        ):
            reply = RCAAgent().run({"query": "What caused this?", "evidence": evidence})["rca"]
        self.assertEqual(reply.root_cause, "")
        self.assertEqual(reply.recommendations, [])
        self.assertEqual(len(reply.evidence), 1)

    async def test_context_upload_links_dependencies_to_session_observations(self):
        demo = Path(__file__).resolve().parents[1] / "data" / "demo"
        names = ("demo_app.log", "demo_metrics.json", "context/demo_changes.json", "context/demo_dependencies.json")
        files = [(Path(name).name, (demo / name).read_bytes()) for name in names]
        result = (await self.upload(*files)).json()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["observations_added"], 34)
        self.assertEqual((await self.upload(files[2], files[3])).json()["observations_added"], 0)

        events = (await self.client.get(f"{self.url}/observations")).json()["observations"]
        change = next(event for event in events if event["modality"] == "change")
        self.assertEqual(change["source_position"], 1)
        self.assertEqual(change["change_type"], "config_change")
        self.assertLess(change["timestamp"], next(event["timestamp"] for event in events if event["severity"] == "ERROR"))

        graph = (await self.client.get(f"{self.url}/graph")).json()
        declared = [edge for edge in graph["edges"] if edge["rel"] == "depends_on"]
        self.assertEqual(len(declared), 2)
        self.assertEqual(declared[0]["source_file"], "demo_dependencies.json")
        self.assertEqual((declared[0]["source"], declared[0]["target"]), ("api-gateway", "auth-service"))
        self.assertTrue(any(edge["rel"] == "observed_in" for edge in graph["edges"]))

        impact = (await self.client.get(f"{self.url}/impact", params={"service": "user-db"})).json()
        auth, gateway = impact["dependents"]
        self.assertEqual(auth["path"], ["user-db", "auth-service"])
        self.assertEqual(gateway["path"], ["user-db", "auth-service", "api-gateway"])
        db_signals = impact["observations"]
        notable = [
            event for event in events if event["service"] == "user-db" and (
                event["modality"] == "change" or event["anomalies"]
                or event["severity"] in ("ERROR", "CRITICAL", "FATAL", "WARN", "WARNING")
            )
        ]
        self.assertEqual([event["id"] for event in db_signals], [event["id"] for event in notable[-3:]])
        for event in db_signals:
            self.assertEqual(event["modality"], "metrics")
            self.assertIn("db_latency_ms", event["metric_values"])
        self.assertTrue(any(event["anomaly_fields"] for event in db_signals))
        db_samples = impact["metric_samples"]
        recent_metrics = [event for event in events if event["service"] == "user-db" and event["modality"] == "metrics"][-5:]
        self.assertEqual([event["id"] for event in db_samples], [event["id"] for event in recent_metrics])
        self.assertEqual(db_samples[-1]["metric_values"]["db_latency_ms"], 15.2)
        self.assertEqual(db_samples[-1]["anomaly_fields"], [])
        self.assertEqual(auth["observations"][0]["severity"], "ERROR")
        event_id = gateway["observations"][0]["id"]
        observation = (await self.client.get(f"{self.url}/observations/{event_id}")).json()
        self.assertIn("504", observation["excerpt"])
        second = (await self.client.post("/api/sessions")).json()["id"]
        self.assertEqual((await self.client.get(f"/api/sessions/{second}/observations/{event_id}")).status_code, 404)
        isolated = (await self.client.get(f"/api/sessions/{second}/impact", params={"service": "user-db"})).json()
        self.assertEqual(isolated["dependents"], [])
        self.assertEqual(isolated["observations"], [])
        self.assertEqual(isolated["metric_samples"], [])

        # A new manifest replaces the active topology and can be uploaded before any observations.
        replacement = {
            "kind": "service_dependencies",
            "dependencies": [{"caller": "api-gateway", "callee": "user-db"}],
        }
        payload = json.dumps(replacement).encode()
        for url in (self.url, f"/api/sessions/{second}"):
            result = (await self.client.post(f"{url}/ingest", files={"files": ("replacement.json", payload)})).json()
            self.assertEqual(result["status"], "success")
            impact = (await self.client.get(f"{url}/impact", params={"service": "user-db"})).json()
            self.assertEqual([item["service"] for item in impact["dependents"]], ["api-gateway"])
        page = (await self.client.get(f"/api/sessions/{second}/observations")).json()
        self.assertEqual(page["observations"], [])

    async def test_context_validation_rejects_an_undated_change_and_self_dependency(self):
        change = {"kind": "change_events", "events": [{
            "timestamp": "2024-01-15T02:29:00", "service": "user-db", "type": "restart", "summary": "Restarted",
        }]}
        topology = {"kind": "service_dependencies", "dependencies": [{"caller": "user-db", "callee": "user-db"}]}
        result = (await self.upload(
            ("changes.json", json.dumps(change).encode()),
            ("topology.json", json.dumps(topology).encode()),
        )).json()
        self.assertEqual(result["status"], "failed")
        self.assertIn("timezone", result["errors"][0])
        self.assertIn("two different services", result["errors"][1])
        self.assertEqual((await self.client.get(self.url)).json()["files"], [])

    async def test_timeline_filters_count_and_page_all_matching_events(self):
        logs = (
            '{"timestamp":"2024-01-15T02:30:00Z","service":"user-db","level":"INFO","message":"Pool ready"}\n'
            '{"timestamp":"2024-01-15T02:31:00Z","service":"user-db","level":"ERROR","message":"Pool exhausted"}\n'
            '{"timestamp":"2024-01-15T02:32:00Z","service":"auth-service","level":"ERROR","message":"Request timed out"}'
        ).encode()
        metrics = b"timestamp,service,cpu_percent,memory_percent\n2024-01-15T02:33:00Z,user-db,94,61\n"
        changes = Path(__file__).resolve().parents[1] / "data/demo/context/demo_changes.json"
        result = await self.upload(("app.log", logs), ("metrics.csv", metrics), (changes.name, changes.read_bytes()))
        self.assertEqual(result.json()["observations_added"], 5)

        endpoint = f"{self.url}/observations"
        first = (await self.client.get(endpoint, params={"category": "errors", "limit": 1})).json()
        self.assertEqual(first["counts"], {"all": 5, "anomalies": 1, "changes": 1, "errors": 2})
        self.assertEqual(first["next_offset"], 1)
        self.assertEqual(first["observations"][0]["service"], "user-db")

        second = (await self.client.get(endpoint, params={"category": "errors", "limit": 1, "offset": 1})).json()
        self.assertIsNone(second["next_offset"])
        self.assertEqual(second["observations"][0]["service"], "auth-service")

        scoped = (await self.client.get(endpoint, params={"category": "anomalies", "service": "user-db"})).json()
        self.assertEqual(scoped["services"], ["auth-service", "user-db"])
        self.assertEqual(scoped["counts"], {"all": 4, "anomalies": 1, "changes": 1, "errors": 1})
        metric, = scoped["observations"]
        self.assertEqual(metric["metric_values"], {"cpu_percent": 94.0, "memory_percent": 61.0})
        self.assertEqual(metric["anomaly_fields"], ["cpu_percent"])

        empty = (await self.client.get(endpoint, params={"category": "changes", "service": "auth-service"})).json()
        self.assertEqual(empty["observations"], [])
        self.assertEqual(empty["counts"]["changes"], 0)
        self.assertEqual((await self.client.get(endpoint, params={"category": "invalid"})).status_code, 422)

    async def test_rule_based_verification_uses_exact_uploaded_records(self):
        demo = Path(__file__).resolve().parents[1] / "data" / "demo"
        change = demo / "context/demo_changes.json"
        topology = demo / "context/demo_dependencies.json"
        logs = demo / "demo_app.log"
        metrics = demo / "demo_metrics.json"
        files = [(path.name, path.read_bytes()) for path in (logs, metrics, topology)]

        await self.upload(*files)
        endpoint = f"{self.url}/verification"
        missing = (await self.client.get(endpoint)).json()
        self.assertEqual(missing["decision"], "insufficient_evidence")
        self.assertIsNone(missing["suspected_origin"])

        await self.upload((change.name, change.read_bytes()))
        result = (await self.client.get(endpoint)).json()
        self.assertEqual(result["decision"], "supported")
        self.assertEqual(result["suspected_origin"], "user-db")
        self.assertEqual(result["affected_services"], ["user-db", "auth-service", "api-gateway"])
        self.assertEqual(len(result["evidence"]), 5)
        pool, cpu = result["hypotheses"]
        self.assertEqual((pool["label"], pool["score"]), ("Connection pool limit reduction", 4))
        self.assertEqual(cpu["label"], "CPU pressure")
        self.assertLess(cpu["score"], pool["score"])
        timing = next(check for check in cpu["checks"] if check["outcome"] == "contradicts")
        reference = (await self.client.get(f'{self.url}/observations/{timing["observation_id"]}')).json()
        self.assertEqual(reference["metric_values"]["db_connections_active"], 20)
        for card in result["evidence"]:
            observation = await self.client.get(f'{self.url}/observations/{card["observation_id"]}')
            self.assertEqual(observation.status_code, 200)
            self.assertEqual(observation.json()["source_position"], card["source_position"])
            self.assertEqual(observation.json()["source_file"], card["source"])

        saved = (await self.client.post(endpoint)).json()
        self.assertEqual((await self.client.get(f"{endpoint}/latest")).json(), saved)
        self.assertEqual((await self.client.get(f"{self.url}/overview")).json()["revision"], saved["evidence_revision"])

        # An earlier CPU spike leaves a competing hypothesis open.
        alternative = json.loads(metrics.read_text())
        alternative["metrics"][4]["cpu_percent"] = 95
        await self.upload(("early_cpu.json", json.dumps(alternative).encode()))
        self.assertNotEqual((await self.client.get(f"{self.url}/overview")).json()["revision"], saved["evidence_revision"])
        self.assertEqual((await self.client.get(f"{endpoint}/latest")).json(), saved)
        competing = (await self.client.get(endpoint)).json()
        self.assertEqual(competing["decision"], "ambiguous")
        self.assertIsNone(competing["suspected_origin"])
        self.assertEqual([item["score"] for item in competing["hypotheses"][:2]], [4, 4])

    async def test_overview_buckets_match_timeline_and_cover_all_pages(self):
        logs = [
            f"2024-01-15T02:{minute:02}:00.999999Z | auth-service | ERROR | database timeout"
            for minute in range(12)
        ]
        file = ("activity.log", "\n".join([*logs, "undated record"]).encode())
        await self.upload(file)
        overview_url = f"{self.url}/overview"
        overview = (await self.client.get(overview_url)).json()
        self.assertEqual(overview["modalities"], {"log": 13})
        self.assertEqual(overview["undated"], 1)
        self.assertEqual(overview["counts"], {"all": 13, "errors": 12, "anomalies": 0, "changes": 0})
        self.assertLessEqual(len(overview["activity"]), 16)
        self.assertEqual(sum(bucket["counts"]["all"] for bucket in overview["activity"]), 12)
        for bucket in overview["activity"]:
            params = {"start": bucket["start"], "end": bucket["end"], "limit": 1}
            page = (await self.client.get(f"{self.url}/observations", params=params)).json()
            self.assertEqual(page["counts"], bucket["counts"])
        bad = await self.client.get(f"{self.url}/observations", params={"start": "2024-01-15T02:00:00"})
        self.assertEqual(bad.status_code, 422)
        await self.upload(file)
        self.assertEqual((await self.client.get(overview_url)).json()["revision"], overview["revision"])

    async def test_missing_pool_samples_produce_a_service_and_utc_window(self):
        demo = Path(__file__).resolve().parents[1] / "data" / "demo"
        names = ("demo_app.log", "context/demo_changes.json", "context/demo_dependencies.json")
        await self.upload(*[(Path(name).name, (demo / name).read_bytes()) for name in names])
        result = (await self.client.post(f"{self.url}/verification")).json()
        self.assertEqual(result["decision"], "insufficient_evidence")
        check = result["next_check"]
        self.assertEqual(check["service"], "user-db")
        self.assertIn("active connections", check["instruction"])
        page = await self.client.get(f"{self.url}/observations", params={
            key: check[key] for key in ("service", "start", "end")
        })
        self.assertEqual(page.status_code, 200)

    async def test_verification_requires_declared_and_temporally_ordered_impact(self):
        demo = Path(__file__).resolve().parents[1] / "data" / "demo"
        log = demo / "demo_app.log"
        metrics = demo / "demo_metrics.json"
        change = demo / "context/demo_changes.json"
        topology = demo / "context/demo_dependencies.json"

        early_logs = log.read_bytes().replace(b"02:31:01Z", b"02:29:45Z").replace(b"02:31:16Z", b"02:29:46Z")
        await self.upload((log.name, early_logs), (metrics.name, metrics.read_bytes()), (change.name, change.read_bytes()))
        endpoint = f"{self.url}/verification"
        self.assertEqual((await self.client.get(endpoint)).json()["decision"], "insufficient_evidence")
        await self.upload((topology.name, topology.read_bytes()))
        result = (await self.client.get(endpoint)).json()
        self.assertEqual(result["decision"], "insufficient_evidence")
        self.assertFalse(any(card["type"] == "log" for card in result["evidence"]))
        pool = next(candidate for candidate in result["hypotheses"] if candidate["label"] == "Connection pool limit reduction")
        self.assertTrue(any(check["outcome"] == "missing" and check["observation_id"] is None for check in pool["checks"]))

    async def test_chat_uses_verifier_and_preserves_citations_in_history(self):
        demo = Path(__file__).resolve().parents[1] / "data" / "demo"
        files = [demo / name for name in (
            "demo_app.log", "demo_metrics.json", "context/demo_dependencies.json",
        )]
        await self.upload(*[(path.name, path.read_bytes()) for path in files])
        endpoint = f"{self.url}/query"
        unresolved = (await self.client.post(endpoint, json={"query": "What caused this?"})).json()
        self.assertEqual(unresolved["decision"], "insufficient_evidence")
        self.assertIsNone(unresolved["suspected_origin"])

        change = demo / "context/demo_changes.json"
        await self.upload((change.name, change.read_bytes()))
        response = await self.client.post(endpoint, json={"query": "What caused this?"})
        self.assertEqual(response.status_code, 200, response.text)
        reply = response.json()
        self.assertEqual(reply["decision"], "supported")
        self.assertEqual(reply["suspected_origin"], "user-db")
        claim, = reply["claims"]
        self.assertEqual(reply["answer_mode"], "local")
        self.assertIn("100 to 20", claim["text"])
        compare, telemetry, callers, retrieval, generation = reply["agent_trace"]
        self.assertEqual(compare["metrics"]["hypotheses"], 2)
        self.assertEqual(compare["metrics"]["passed"], 1)
        self.assertGreater(compare["metrics"]["observations"], 0)
        self.assertEqual(telemetry["status"], "confirmed")
        self.assertEqual(callers["status"], "confirmed")
        self.assertEqual(retrieval["metrics"]["semantic"], 0)
        self.assertEqual(generation["status"], "local")
        known_ids = {item["observation_id"] for item in reply["evidence"]}
        self.assertEqual({citation["observation_id"] for citation in claim["citations"]}, known_ids)

        history = (await self.client.get(f"{self.url}/chat")).json()["history"]
        self.assertEqual(history[-2]["content"], "What caused this?")
        self.assertEqual(history[-1]["content"]["claims"], reply["claims"])

        other = (await self.client.post("/api/sessions")).json()["id"]
        isolated = (await self.client.post(f"/api/sessions/{other}/query", json={"query": "What caused this?"})).json()
        self.assertEqual(isolated["decision"], "insufficient_evidence")
        self.assertEqual(isolated["evidence"], [])
        self.assertEqual(isolated["agent_trace"][-1]["status"], "local")
        self.assertTrue(all(not claim["citations"] for claim in isolated["claims"]))

    async def test_model_generates_from_retrieved_records_and_cannot_invent_citation_ids(self):
        await self.upload(
            ("app.log", b'{"timestamp":"2024-01-15T03:00:00Z","service":"billing-api","level":"ERROR","message":"Invoice worker failed with queue timeout"}'),
            ("other.log", b'{"timestamp":"2024-01-15T03:01:00Z","service":"cache","level":"INFO","message":"Cache warmup completed"}'),
        )
        question = "What error did billing-api report?"
        endpoint = f"{self.url}/query"

        async def run_inline(function, *args):
            return function(*args)

        with (
            patch("services.grounded_chat.configured_provider", return_value="test"),
            patch("services.grounded_chat.run_in_threadpool", run_inline),
            patch("services.grounded_chat.generate_text") as generate,
        ):
            generate.return_value = json.dumps({
                "decision": "insufficient_evidence",
                "claims": [{"text": "The invoice worker reported a queue timeout.", "sources": ["E1"]}],
            })
            result = (await self.client.post(endpoint, json={"query": question})).json()
            self.assertEqual(result["answer_mode"], "generated")
            self.assertEqual(result["agent_trace"][-1]["status"], "generated")
            self.assertEqual(result["decision"], "insufficient_evidence")
            self.assertIsNone(result["suspected_origin"])
            self.assertIn("The invoice worker reported a queue timeout.", result["answer"])
            source, = result["claims"][-1]["citations"]
            row = (await self.client.get(f'{self.url}/observations/{source["observation_id"]}')).json()
            self.assertEqual(row["source_file"], "app.log")
            self.assertIn("queue timeout", generate.call_args.args[0])
            self.assertNotIn("Cache warmup completed", generate.call_args.args[0])

            followup = (await self.client.post(endpoint, json={"query": "When did that happen?"})).json()
            context = json.loads(generate.call_args.args[0].split("Input: ", 1)[1])
            self.assertEqual(context["previous_question"], question)
            self.assertTrue(context["observations"][0]["cited_in_previous_reply"])
            self.assertEqual(followup["claims"][-1]["citations"], [source])
            self.assertEqual(followup["agent_trace"][-2]["metrics"]["carried"], 1)

            comparison = await self.client.post(endpoint, json={
                "query": "What was the peak database latency for billing-api before the queue timeout?",
            })
            self.assertEqual(comparison.json()["answer_mode"], "generated")

            for invalid in (
                {"decision": "supported", "claims": [{"text": "Fabricated cause", "sources": ["E1"]}]},
                {"decision": "insufficient_evidence", "claims": [{"text": "Fabricated source", "sources": ["E999"]}]},
                {"decision": "insufficient_evidence", "claims": [{"text": "Uncited", "sources": []}]},
            ):
                generate.return_value = json.dumps(invalid)
                result = (await self.client.post(endpoint, json={"query": question})).json()
                self.assertEqual(result["answer_mode"], "local")
                self.assertNotIn("Fabricated", result["answer"])
                self.assertEqual(result["decision"], "insufficient_evidence")

            generate.side_effect = TimeoutError
            result = (await self.client.post(endpoint, json={"query": question})).json()
            self.assertEqual(result["answer_mode"], "local")

    async def test_semantic_hit_resolves_only_to_uploaded_source_version_and_span(self):
        from services.incident_retrieval import retrieve_semantic

        original = b"First log line\nSecond log line"
        changed = b"Changed first line\nChanged second line"
        await self.upload(("app.log", original), ("app.log", changed))
        metadata = {
            "source_file": "app.log",
            "source_digest": sha256(original).hexdigest(),
            "start_line": 2,
            "end_line": 2,
        }
        with (
            patch("services.embedder.embed_query", return_value=[1.0] * 768),
            patch("services.pinecone_store.query", return_value=[{"metadata": metadata}]) as query,
        ):
            records = retrieve_semantic(self.root / self.session, "What was the second line?", ["second"])
        query.assert_called_once_with([1.0] * 768, top_k=5, namespace=self.session)
        self.assertEqual([record.excerpt for record in records], ["Second log line"])

    async def test_graph_rag_baseline_is_explicitly_selectable(self):
        async def run_inline(function, *args):
            return function(*args)

        with (
            patch("api.routes.run_in_threadpool", run_inline),
            patch("agents.orchestrator.Orchestrator.run", return_value=RCAOutput(answer="Baseline reply")) as baseline,
        ):
            response = await self.client.post(
                f"{self.url}/query", json={"query": "What happened?", "mode": "graph_rag"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Baseline reply")
        baseline.assert_called_once_with("What happened?", self.session)

    async def test_metric_spike_uses_prior_rows_and_retains_source_record(self):
        path = self.root / "metrics.csv"
        path.write_text(
            "timestamp,latency_custom\n"
            "2024-01-15T02:31:30Z,120\n"
            "2024-01-15T02:30:00Z,10\n"
            "2024-01-15T02:30:30Z,12\n"
            "2024-01-15T02:31:00Z,11\n"
        )
        _, observations = parse_metrics(path)
        self.assertEqual(observations[0].source_position, 1)
        self.assertIn("deviation from preceding samples", observations[0].anomalies[0])
        self.assertTrue(all(not observation.anomalies for observation in observations[1:]))
