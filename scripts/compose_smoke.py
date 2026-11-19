"""Verify the Compose frontend proxy and restart persistence using a disposable incident.

Docker stack must already be healthy. Commands are printed in TESTING.md.
Python standard library only. Never touches an existing incident.
"""
import argparse
import json
from pathlib import Path
import subprocess
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def request(base, path, method='GET', payload=None, multipart=None):
    headers = {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    if multipart is not None:
        boundary = uuid4().hex
        data = b''.join(b'--' + boundary.encode() + b'\r\nContent-Disposition: form-data; name="files"; filename="' + file.name.encode() + b'"\r\nContent-Type: application/octet-stream\r\n\r\n' + file.read_bytes() + b'\r\n' for file in multipart)
        data += b'--' + boundary.encode() + b'--\r\n'
        headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
    with urlopen(Request(base + '/api' + path, data=data, headers=headers, method=method), timeout=90) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:3300')
    parser.add_argument('--restart', action='store_true', help='Restart only backend and verify persisted records and chat.')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    assert request(base, '/health')['status'] == 'ok'
    session = request(base, '/sessions', 'POST', {'name': 'Disposable Compose smoke test'})['id']
    route = '/sessions/' + session
    try:
        demo = ROOT / 'backend/data/demo'
        files = [demo / name for name in ['demo_app.log', 'demo_metrics.json', 'context/demo_changes.json', 'context/demo_dependencies.json']]
        ingest = request(base, route + '/ingest', 'POST', multipart=files)
        assert ingest['observations_added'] == 34, ingest
        assert request(base, route + '/observations')['counts']['all'] == 34
        graph = request(base, route + '/graph')
        assert len([node for node in graph['nodes'] if node.get('observation_id')]) == 34
        reply = request(base, route + '/query', 'POST', {'query': 'What was the peak database latency for user-db?'})
        assert reply['answer_mode'] == 'extractive' and '30,045' in reply['answer']
        citation = reply['claims'][0]['citations'][0]['observation_id']
        assert request(base, route + '/observations/' + citation)['source_position'] == 11
        assert request(base, route + '/ingest', 'POST', multipart=files)['observations_added'] == 0
        if args.restart:
            subprocess.run(['docker', 'compose', 'restart', 'backend'], cwd=ROOT, check=True)
            subprocess.run(['docker', 'compose', 'up', '-d', '--wait', '--no-recreate', 'backend'], cwd=ROOT, check=True)
            assert request(base, route + '/observations')['counts']['all'] == 34
            assert len(request(base, route + '/chat')['history']) == 2
            assert request(base, route + '/graph') == graph
        print(json.dumps({'status': 'passed', 'proxy': base, 'records': 34, 'citations': 'resolved', 'restart_checked': args.restart}))
    finally:
        request(base, route, 'DELETE')


if __name__ == '__main__':
    main()
