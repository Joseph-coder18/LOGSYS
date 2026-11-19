"""Regression cases for evidence quality, isolation, persistence and provider failures."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import networkx as nx
import pytest

import config
from models import BaseChunk, Observation
from services import pinecone_store, session_store
from services.grounded_chat import answer_incident
from services.image_analyzer import VisiblePanel, _panel_observation
from services.incident_retrieval import query_terms, retrieve_question_records, retrieve_semantic
from services.metric_units import normalize_metrics
from services.metrics_parser import _detect_anomalies, parse_metrics
from services.observations import load_observations, save_observations, search_observations
from services.temporal_edges import _build


@pytest.fixture
def incident(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, '_SESSIONS_DIR', tmp_path)
    monkeypatch.setattr(session_store, '_INDEX_PATH', tmp_path / 'index.json')
    for key in ('GEMINI_API_KEY', 'GROQ_API_KEY', 'PINECONE_API_KEY'):
        monkeypatch.setattr(config, key, '')
    meta = session_store.create_session('Close-out test')
    return meta['id'], tmp_path / meta['id']


def event(value, when, *, filename='metrics.json', stream=None, position=1):
    return Observation(modality='metrics', source_file=filename, source_position=position,
                       service='user-db', timestamp=when, summary=f'db_latency_ms={value}',
                       excerpt=str(value), metric_values={'db_latency_ms': value}, stream_id=stream)


def test_units_preserve_meaning_and_reject_ambiguous_inputs(tmp_path):
    values, units, warnings = normalize_metrics({'cpu_percent': '94%', 'db_latency_s': '0.42',
        'error_rate': '18%', 'memory_percent': '62.4%', 'db_connections_active': 20,
        'mystery': '12GiB', 'request_id': 1234, 'latency': '0.42s'})
    assert values == {'cpu_percent': 94, 'db_latency_ms': 420, 'error_rate': .18,
                      'memory_percent': 62.4, 'db_connections_active': 20}
    assert units['db_latency_ms'] == 'ms'
    assert len(warnings) == 2
    values, _, warnings = normalize_metrics({'cpu': 94, 'cpu_percent': 12, 'error_rate': 18})
    assert values == {} and len(warnings) == 2
    path = tmp_path / 'units.json'
    raw = {'timestamp': '2024-01-15T02:30:00Z', 'service': 'user-db', 'cpu_percent': '94%', 'odd': '12GiB'}
    path.write_text(json.dumps([raw]))
    _, observations = parse_metrics(path)
    assert json.loads(observations[0].excerpt) == raw
    assert observations[0].anomaly_fields == ['cpu_percent']
    assert observations[0].quality_warnings


def test_constant_baseline_spike_drift_and_out_of_order():
    def records(values):
        return [{'timestamp': f'2024-01-15T02:30:{index:02}Z', 'service': 'user-db', 'db_latency_ms': value}
                for index, value in enumerate(values)]
    assert [item['row'] for item in _detect_anomalies(records([10, 10, 10, 400]))] == [3]
    assert _detect_anomalies(records([10, 10, 10, 10.2])) == []
    assert _detect_anomalies(records([10, 11, 12, 13, 14])) == []
    assert _detect_anomalies(records([10, 400])) == []  # sparse, below absolute threshold
    tied = records([10, 10, 10, 400])
    for row in tied[:3]: row['timestamp'] = '2024-01-15T02:30:00Z'
    assert _detect_anomalies(tied) == []
    assert len(_detect_anomalies(records([600, 600, 600]))) == 3  # sustained absolute breach
    rows = records([10, 10, 10, 400])
    shuffled = [rows[3], rows[0], rows[2], rows[1]]
    assert _detect_anomalies(shuffled)[0]['row'] == 0


def test_typed_screenshot_ranges_never_invent_peak_time():
    panel = VisiblePanel.model_validate({'panel': 'Latency', 'observation': 'Max 420 ms', 'service': 'user-db',
        'visible_time_range': '2024-01-15 02:27–02:33 UTC',
        'interval_start': '2024-01-15T02:27:00Z', 'interval_end': '2024-01-15T02:33:00Z',
        'stream_id': 'monitor-1', 'signals': [{'field': 'db_latency_ms', 'value': 420, 'unit': 'ms', 'statistic': 'maximum'}]})
    reading = _panel_observation(panel, 'dashboard.png', 1)
    assert reading.timestamp is None
    assert reading.interval_start.startswith('2024-01-15T02:27')
    assert reading.metric_values == {'db_latency_ms': 420}
    assert reading.metric_aggregations == {'db_latency_ms': 'maximum'}
    unknown = _panel_observation(VisiblePanel(panel='Clock only', observation='Rises', visible_time_range='02:27–02:33',
        interval_start='2024-01-15T02:27:00', interval_end='2024-01-15T02:33:00'), 'clock.png', 1)
    assert unknown.timestamp is None and unknown.interval_start is None and unknown.quality_warnings
    assert Observation.model_validate({'modality': 'image', 'source_file': 'old.png', 'source_position': 1,
        'summary': 'Legacy reading', 'excerpt': '{}'}).metric_units == {}


def test_interval_overlap_queries_and_temporal_order(incident):
    _, directory = incident
    reading = _panel_observation(VisiblePanel(panel='Latency', observation='Max 420 ms', service='user-db',
        interval_start='2024-01-15T02:27:00Z', interval_end='2024-01-15T02:33:00Z'), 'dashboard.png', 1)
    save_observations(directory, 'image-hash', [reading])
    start, end = '2024-01-15T02:30:00.000000+00:00', '2024-01-15T02:31:00.000000+00:00'
    assert load_observations(directory, 0, 10, start=start, end=end).observations[0].id == reading.id
    from services.observations import incident_overview
    coverage = incident_overview(directory)
    assert coverage.intervals == 1 and coverage.undated == 0
    assert coverage.first_at == reading.interval_start and coverage.last_at == reading.interval_end
    assert coverage.activity == []
    assert search_observations(directory, ['latency'], 5, start=start, end=end)[0].id == reading.id
    graph = nx.DiGraph()
    graph.add_node('range', service='user-db', modality='image', timestamp=100,
                   interval_end='1970-01-01T00:05:00+00:00')
    graph.add_node('overlap', service='user-db', modality='log', timestamp=200)
    graph.add_node('later', service='user-db', modality='log', timestamp=400)
    _build(graph)
    assert not graph.has_edge('range', 'overlap')
    assert graph.has_edge('range', 'later')


def test_duplicate_exports_share_provenance_without_merging_citations(incident):
    _, directory = incident
    rows = [event(10, '2024-01-15T02:30:00Z', filename='a.json'),
            event(10, '2024-01-15T02:30:00Z', filename='renamed.json'),
            event(10, '2024-01-15T02:30:00Z', filename='independent.json')]
    save_observations(directory, 'same-content', rows[:2])
    save_observations(directory, 'independent-content', rows[2:])
    assert rows[0].id != rows[1].id
    assert rows[0].provenance_group == rows[1].provenance_group != rows[2].provenance_group
    explicit = [event(10, '2024-01-15T02:30:00Z', filename='export.csv', stream='same-monitor'),
                event(10, '2024-01-15T02:30:00Z', filename='shot.png', stream='same-monitor')]
    explicit[1].modality = 'image'
    for index, item in enumerate(explicit):
        save_observations(directory, f'different-{index}', [item])
    graph = nx.DiGraph()
    for index, item in enumerate(explicit):
        graph.add_node(str(index), **item.model_dump(), correlation_id='req-1')
        graph.nodes[str(index)]['timestamp'] = 100 + index
    assert _build(graph)['supports'] == 0


def test_concurrent_metadata_chat_and_restart_recovery(incident):
    sid, directory = incident
    def write(index):
        session_store.register_session_files(sid, [f'source-{index}.log'])
        session_store.append_session_chat(sid, {'role': 'user', 'content': str(index)},
                                         {'role': 'assistant', 'content': str(index)})
        other = session_store.create_session(f'Parallel {index}')
        return other['id']
    with ThreadPoolExecutor(max_workers=8) as pool:
        others = list(pool.map(write, range(16)))
    assert len(session_store.get_session(sid)['files']) == 16
    history = session_store.get_session_chat(sid)
    assert len(history) == 32
    assert all(history[index]['content'] == history[index+1]['content'] for index in range(0, 32, 2))
    # The index is a rebuildable cache; a crash between metadata and index publication loses no incidents.
    session_store._INDEX_PATH.write_text('broken partial index')
    assert {item['id'] for item in session_store.list_sessions()} == {sid, *others}
    session_store.clear_session_chat(sid)
    assert session_store.append_session_chat(sid, {'role': 'assistant', 'content': 'stale'}, expected_generation=0) is False
    assert session_store.get_session_chat(sid) == []
    assert session_store.append_session_chat(sid, {'role': 'user', 'content': 'new'}, expected_generation=1)


def test_two_incidents_same_service_obey_scope_in_keyword_semantic_and_peak(incident):
    _, directory = incident
    rows = [event(20, '2024-01-15T02:30:00.000000+00:00', position=1),
            event(900, '2024-01-16T02:30:00.000000+00:00', position=2)]
    save_observations(directory, 'versions', rows)
    scope = {'start': '2024-01-15T00:00:00.000000+00:00', 'end': '2024-01-16T00:00:00.000000+00:00'}
    records, *_ = retrieve_question_records(directory, query_terms('database latency user-db'), **scope)
    assert [item.id for item in records] == [rows[0].id]
    with patch('services.embedder.embed_query', return_value=[1.] * config.EMBEDDING_DIM), patch('services.pinecone_store.query', return_value=[{
        'metadata': {'source_digest': 'versions', 'source_file': 'metrics.json', 'start_line': 1, 'end_line': 2}}]):
        assert [item.id for item in retrieve_semantic(directory, 'latency', ['latency'], **scope)] == [rows[0].id]
    reply = asyncio.run(answer_incident(directory, 'What was the peak database latency for user-db?', use_model=False, **scope))
    assert reply.answer_mode == 'extractive' and '20 ms' in reply.answer and '900' not in reply.answer
    assert reply.claims[0].citations[0].observation_id == rows[0].id
    assert reply.evidence_revision is None
    assert reply.query_scope == {'service': None, **scope}
    from services.incident_context import save_dependencies
    save_dependencies(directory, {'kind':'service_dependencies','dependencies':[{'caller':'checkout-api','callee':'user-db'}]}, 'dependencies.json')
    records, _, added, _ = retrieve_question_records(directory, query_terms('What latency did the dependency of checkout-api report?'), services=['checkout-api'], **scope)
    assert [item.id for item in records] == [rows[0].id] and added == 1


def test_vector_status_distinguishes_disabled_empty_failure_and_dimension(monkeypatch):
    monkeypatch.setattr(pinecone_store, '_get_index', lambda: None)
    monkeypatch.setattr(pinecone_store, 'PINECONE_API_KEY', '')
    assert pinecone_store.query_with_status([1.] * 768)['status'] == 'disabled'
    class Index:
        def query(self, **kwargs): return SimpleNamespace(matches=[])
    monkeypatch.setattr(pinecone_store, '_get_index', lambda: Index())
    assert pinecone_store.query_with_status([1.] * 768)['status'] == 'no_matches'
    assert pinecone_store.query_with_status([1.])['status'] == 'dimension_mismatch'
    class Failed:
        def query(self, **kwargs): raise TimeoutError()
    monkeypatch.setattr(pinecone_store, '_get_index', lambda: Failed())
    assert pinecone_store.query_with_status([1.] * 768)['status'] == 'error'


def test_partial_index_retry_is_idempotent(monkeypatch):
    chunks = [BaseChunk(chunk_id=str(index), source_file='app.log', modality='log', text='line') for index in range(101)]
    stored = {}
    class Index:
        calls = 0
        def upsert(self, vectors, **kwargs):
            self.calls += 1
            if self.calls == 2: raise TimeoutError()
            stored.update({item['id']: item for item in vectors})
            return SimpleNamespace(upserted_count=len(vectors))
    index = Index()
    monkeypatch.setattr(pinecone_store, '_get_index', lambda: index)
    first = pinecone_store.upsert_chunks(chunks, [[1.] * 768] * 101, namespace='incident-a')
    assert first['upserted'] == 100 and first['status'] == 'partial'
    second = pinecone_store.upsert_chunks(chunks, [[1.] * 768] * 101, namespace='incident-a')
    assert second['upserted'] == 101 and len(stored) == 101


def test_legacy_zero_matches_never_silently_load_demo(incident):
    from agents.retrieval_agent import RetrievalAgent
    sid, _ = incident
    with patch('services.embedder.embed_query', return_value=[0.] * 768), patch('agents.retrieval_agent._load_demo_fallback') as demo:
        assert RetrievalAgent().run({'query': 'outage', 'session_id': sid})['evidence'] == []
        assert RetrievalAgent().run({'query': 'outage'})['evidence'] == []
        demo.assert_not_called()
        RetrievalAgent().run({'query': 'outage', 'demo': True})
        demo.assert_called_once()


def test_metadata_lock_preserves_writes_across_server_processes(incident):
    import subprocess
    import sys
    sid, directory = incident
    source = '''from pathlib import Path
import sys
from services import session_store as store
store._SESSIONS_DIR = Path(sys.argv[1])
store._INDEX_PATH = store._SESSIONS_DIR / 'index.json'
for index in range(8):
    store.register_session_files(sys.argv[2], [sys.argv[3] + '-' + str(index)])
    store.append_session_chat(sys.argv[2], {'role':'user','content':sys.argv[3] + str(index)}, {'role':'assistant','content':sys.argv[3] + str(index)})
'''
    def worker(index):
        return subprocess.run([sys.executable, '-c', source, str(directory.parent), sid, str(index)],
                              env={**__import__('os').environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
                              check=True, capture_output=True, timeout=30)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(worker, range(4)))
    assert len(session_store.get_session(sid)['files']) == 32
    assert len(session_store.get_session_chat(sid)) == 64


def test_reextraction_updates_quality_without_changing_citation_identity(incident):
    from services.observations import evidence_revision, observation_by_id
    _, directory = incident
    original = event(420, '2024-01-15T02:30:00Z', filename='shot.png')
    original.modality = 'image'
    assert save_observations(directory, 'image-bytes', [original]) == 1
    revision = evidence_revision(directory)
    upgraded = original.model_copy(update={'metric_units': {'db_latency_ms': 'ms'}, 'extraction_version': 2})
    assert save_observations(directory, 'image-bytes', [upgraded]) == 0
    assert original.id == upgraded.id
    assert observation_by_id(directory, original.id).extraction_version == 2
    assert evidence_revision(directory) != revision


def test_provider_failure_and_malformed_reply_keep_resolvable_citations(incident):
    import services.grounded_chat as chat
    _, directory = incident
    source = event(420, '2024-01-15T02:30:00Z')
    save_observations(directory, 'provider-fixture', [source])
    async def inline(function, *args): return function(*args)
    class RateLimit(Exception): code = 429
    for error, expected in [(RateLimit(), 'rate_limited'), (TimeoutError(), 'timeout'), (ValueError(), 'invalid_output')]:
        with patch.object(chat, 'configured_provider', return_value='test'), patch.object(chat, 'run_in_threadpool', inline), patch.object(chat, 'generate_text', side_effect=error):
            reply = asyncio.run(answer_incident(directory, 'Explain user-db latency', service='user-db'))
        assert reply.answer_mode == 'local'
        assert reply.agent_trace[-1]['provider_status']['status'] == expected
        assert reply.claims[0].citations[0].observation_id == source.id
    payload = json.dumps({'decision': 'insufficient_evidence', 'claims': [{'text':'Invented', 'sources':['unknown-source']}]})
    with patch.object(chat, 'configured_provider', return_value='test'), patch.object(chat, 'run_in_threadpool', inline), patch.object(chat, 'generate_text', return_value=payload):
        reply = asyncio.run(answer_incident(directory, 'Explain user-db latency', service='user-db'))
    assert 'Invented' not in reply.answer
    assert reply.agent_trace[-1]['provider_status']['status'] == 'invalid_output'


def test_scope_api_rejects_timezone_free_bounds_and_scopes_peak(incident):
    from httpx import ASGITransport, AsyncClient
    from main import app
    sid, directory = incident
    save_observations(directory, 'scope-api', [event(20, '2024-01-15T02:30:00Z', position=1), event(900, '2024-01-16T02:30:00Z', position=2)])
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            endpoint = f'/api/sessions/{sid}/query'
            invalid = await client.post(endpoint, json={'query':'latency', 'start':'2024-01-15T00:00:00'})
            assert invalid.status_code == 422
            result = await client.post(endpoint, json={'query':'What was the peak database latency for user-db?',
                'service':'user-db', 'start':'2024-01-15T00:00:00Z', 'end':'2024-01-16T00:00:00Z'})
            assert result.status_code == 200
            assert '20 ms' in result.json()['answer'] and '900' not in result.json()['answer']
    asyncio.run(run())


def test_existing_index_dimension_is_checked_without_modifying_index(monkeypatch):
    import pinecone
    class Client:
        def __init__(self, **kwargs): pass
        def list_indexes(self): return [SimpleNamespace(name='test-index')]
        def describe_index(self, name): return SimpleNamespace(dimension=1536)
        def Index(self, *args, **kwargs): raise AssertionError('Must not open incompatible index')
        def create_index(self, **kwargs): raise AssertionError('Must not recreate an existing index')
    monkeypatch.setattr(pinecone, 'Pinecone', Client)
    monkeypatch.setattr(pinecone_store, 'PINECONE_API_KEY', 'fake')
    monkeypatch.setattr(pinecone_store, 'PINECONE_INDEX_NAME', 'test-index')
    monkeypatch.setattr(pinecone_store, '_initialized', False)
    monkeypatch.setattr(pinecone_store, '_index', None)
    monkeypatch.setattr(pinecone_store, '_init_failure', {})
    result = pinecone_store.query_with_status([1.] * 768)
    assert result['status'] == 'dimension_mismatch'
    assert result['expected_dimension'] == 768 and result['actual_dimension'] == 1536


def test_vision_upgrade_reordering_does_not_retarget_existing_panel_citations(incident):
    from services.observations import observation_by_id
    _, directory = incident
    cpu = _panel_observation(VisiblePanel(panel='CPU usage', observation='Maximum 94%', service=None), 'shot.png', 1)
    latency = _panel_observation(VisiblePanel(panel='Database latency', observation='Maximum 420 ms', service=None), 'shot.png', 2)
    assert save_observations(directory, 'same-screenshot', [cpu, latency]) == 2
    upgraded_latency = _panel_observation(VisiblePanel(panel='Database latency', observation='Maximum 420 ms', service='user-db',
        signals=[{'field':'db_latency_ms','value':420,'unit':'ms','statistic':'maximum'}]), 'shot.png', 1)
    upgraded_cpu = _panel_observation(VisiblePanel(panel='CPU usage', observation='Maximum 94%', service='user-db'), 'shot.png', 2)
    assert save_observations(directory, 'same-screenshot', [upgraded_latency, upgraded_cpu]) == 0
    assert upgraded_latency.id == latency.id and upgraded_cpu.id == cpu.id
    assert observation_by_id(directory, cpu.id).source_label == 'CPU usage'
    assert observation_by_id(directory, latency.id).metric_values['db_latency_ms'] == 420
