"""Run with PYTHONPATH=backend python -m unittest discover -s backend/tests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from main import app
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
        for key in ("GEMINI_API_KEY", "PINECONE_API_KEY"):
            patcher = patch(f"config.{key}", "")
            patcher.start()
            self.addCleanup(patcher.stop)
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
        event_id = gateway["observations"][0]["id"]
        observation = (await self.client.get(f"{self.url}/observations/{event_id}")).json()
        self.assertIn("504", observation["excerpt"])
        second = (await self.client.post("/api/sessions")).json()["id"]
        self.assertEqual((await self.client.get(f"/api/sessions/{second}/observations/{event_id}")).status_code, 404)
        isolated = (await self.client.get(f"/api/sessions/{second}/impact", params={"service": "user-db"})).json()
        self.assertEqual(isolated["dependents"], [])
        self.assertEqual(isolated["observations"], [])

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
