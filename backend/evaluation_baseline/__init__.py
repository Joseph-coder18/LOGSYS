"""Read-only baseline sources from f22e968; imported only by the benchmark."""
from hashlib import sha256
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import networkx as nx

ROOT = Path(__file__).parent


def _load(name):
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    path = ROOT / name
    data = path.read_bytes()
    if sha256(data).hexdigest() != manifest['files'][name]:
        raise ValueError(f'Frozen baseline changed: {name}')
    module = ModuleType('frozen_' + name.replace('.', '_'))
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


def frozen_graph_context(demo_dir: Path, question: str, matches: list[dict]) -> dict:
    """Replay original entity extraction and traversal on aligned fixtures.

    Retrieval matches and model settings are shared with the vector-only arm.
    New observation nodes and temporal edges cannot leak into this baseline.
    """
    graph = nx.DiGraph()
    chunks = []
    for parser_name, filename, method in [('services_log_parser.py.txt', 'demo_app.log', 'parse_log_file'),
                                           ('services_metrics_parser.py.txt', 'demo_metrics.json', 'parse_metrics_file')]:
        chunks.extend(getattr(_load(parser_name), method)(demo_dir / filename))
    for chunk in chunks:
        filename, meta = chunk.source_file, chunk.metadata
        graph.add_node(filename, type='file', modality=chunk.modality)
        for key, kind in [('services', 'service'), ('exceptions', 'error'), ('databases', 'database')]:
            names = meta.get(key, [])
            names = names.split(',') if isinstance(names, str) else names
            for name in sorted(set(names)):
                graph.add_node(name, type=kind)
                source, target = (name, filename) if kind == 'error' else (filename, name)
                graph.add_edge(source, target, rel='observed_in')
    store = _load('services_graph_store.py.txt')
    store._resolve_graph = lambda session_id: (graph, False)
    retrieval = _load('agents_retrieval_agent.py.txt')
    with patch('services.embedder.embed_query', return_value=[1.] * 768), patch('services.pinecone_store.query', return_value=matches), patch('services.graph_store.get_neighbors', store.get_neighbors), patch('services.graph_store.get_graph_json', lambda **kwargs: {'nodes': [{'id': node, **attrs} for node, attrs in graph.nodes(data=True)]}):
        result = retrieval.RetrievalAgent().run({'query': question, 'session_id': 'frozen-baseline'})
    result['graph_nodes'].sort(key=lambda node: node['node'])
    return {'normalization': 'Canonical node order; original two-hop content and budgets retained', 'manifest': json.loads((ROOT/'manifest.json').read_text()),
            'graph_nodes': result['graph_nodes'], 'trace': result['trace'],
            'projection_nodes': graph.number_of_nodes(), 'projection_edges': graph.number_of_edges()}
