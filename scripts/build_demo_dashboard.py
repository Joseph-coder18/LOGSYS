"""Render the synthetic dashboard from the exact exported metric samples.

Run with the backend environment. Pillow is already a runtime dependency.
No model, network access, or manually entered chart values are involved.
"""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'backend/data/demo'
METRICS = DEMO / 'demo_metrics.json'
STREAM = 'demo/user-db/pool-monitor'


def build():
    document = json.loads(METRICS.read_text())
    rows = document['metrics']
    for row in rows:
        row['stream_id'] = STREAM
    METRICS.write_text(json.dumps(document, indent=2) + '\n')
    image = Image.new('RGB', (1600, 1040), '#0d1422')
    draw = ImageDraw.Draw(image)
    font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    def font(size):
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            return ImageFont.load_default(size=size)
    draw.text((45, 24), 'user-db / telemetry dashboard', font=font(30), fill='#edf3ff')
    draw.text((45, 70), '2024-01-15 02:27:00–02:33:30 UTC   ·   synthetic lab data', font=font(21), fill='#b0c3df')
    draw.text((45, 105), f'Stream ID: {STREAM}', font=font(18), fill='#9eb3d4')
    changes = json.loads((DEMO / 'context/demo_changes.json').read_text())['events']
    trigger = datetime.fromisoformat(changes[0]['timestamp']).timestamp()
    times = [datetime.fromisoformat(row['timestamp']).timestamp() for row in rows]
    panels = [('Database latency', 'db_latency_ms', 'ms', '#82b2ff'),
              ('Connection pool', 'db_connections_active', 'connections', '#b6a0ff'),
              ('Error rate', 'error_rate', '%', '#ffa269'),
              ('CPU usage', 'cpu_percent', '%', '#68d8bf')]
    for index, (title, key, unit, color) in enumerate(panels):
        left, top = 40 + (index % 2) * 785, 155 + (index // 2) * 430
        right, bottom = left + 745, top + 400
        draw.rounded_rectangle((left, top, right, bottom), radius=14, fill='#172133', outline='#35445c', width=2)
        values = [row[key] * (100 if key == 'error_rate' else 1) for row in rows]
        maximum = max(values)
        draw.text((left + 22, top + 15), title, font=font(24), fill='#edf3ff')
        draw.text((left + 22, top + 51), f'Max {maximum:,.1f} {unit}   ·   Last {values[-1]:,.1f} {unit}', font=font(19), fill=color)
        x0, x1, y0, y1 = left + 90, right - 25, top + 110, bottom - 55
        ymax = (100 if key in {'error_rate', 'cpu_percent'} else 110 if key == 'db_connections_active' else maximum * 1.1)
        def x(t): return x0 + (t - times[0]) / (times[-1] - times[0]) * (x1 - x0)
        def y(v): return y1 - v / ymax * (y1 - y0)
        for step in range(5):
            value = ymax * step / 4
            yy = y(value)
            draw.line((x0, yy, x1, yy), fill='#30415b')
            draw.text((left + 10, yy - 10), f'{value:,.0f}', font=font(15), fill='#9eb3d4')
        draw.line((x(trigger), y0, x(trigger), y1), fill='#f5c278', width=2)
        if key == 'db_connections_active':
            draw.line([(x(times[0]), y(changes[0]['pool_limit_change']['before'])), (x(trigger), y(changes[0]['pool_limit_change']['before'])), (x(trigger), y(changes[0]['pool_limit_change']['after'])), (x(times[-1]), y(changes[0]['pool_limit_change']['after']))], fill='#f5c278', width=3)
            draw.text((left + 22, top + 78), 'Active (purple) / configured limit (amber)', font=font(16), fill='#b0c3df')
        draw.line([(x(t), y(v)) for t, v in zip(times, values)], fill=color, width=4)
        for t, value in zip(times, values):
            xx, yy = x(t), y(value)
            draw.ellipse((xx-3, yy-3, xx+3, yy+3), fill=color)
        for pos in (0, 6, 10, 19):
            label = rows[pos]['timestamp'][11:19]
            draw.text((x(times[pos])-32, y1+16), label, font=font(14), fill='#b0c3df')
    draw.text((45, 1020), 'Source: demo_metrics.json · vertical marker: 02:29:15 UTC pool configuration change', font=font(15), fill='#9eb3d4')
    output = DEMO / 'demo_dashboard.png'
    image.save(output)
    manifest = {
        'case_id': 'db-pool-reduction-v2', 'synthetic': True,
        'stream_id': STREAM, 'service': 'user-db',
        'start': rows[0]['timestamp'], 'end': rows[-1]['timestamp'],
        'expected': {'root_service': 'user-db', 'change_at': changes[0]['timestamp'],
                     'first_saturation_at': '2024-01-15T02:30:00Z',
                     'latency_peak_ms': max(row['db_latency_ms'] for row in rows),
                     'latency_peak_record': 11, 'cpu_peak_percent': max(row['cpu_percent'] for row in rows),
                     'error_peak_ratio': max(row['error_rate'] for row in rows)},
        'inputs': {str(path.relative_to(DEMO)): sha256(path.read_bytes()).hexdigest() for path in [METRICS, output, DEMO/'demo_app.log', DEMO/'context/demo_changes.json', DEMO/'context/demo_dependencies.json']},
        'note': 'Evaluation labels only. Never ingest this manifest as incident telemetry.',
    }
    (ROOT/'evaluation/cases/db-pool-reduction.json').write_text(json.dumps(manifest, indent=2)+'\n')


if __name__ == '__main__':
    build()
