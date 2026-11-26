import { useMemo } from 'react'
import { Card } from '@/components/ui/surfaces'

const METRIC_SERIES = [
  { key: 'db_latency_ms', label: 'DB Latency', unit: 'ms', color: '#38bdf8' },
  { key: 'cpu_percent', label: 'CPU Utilization', unit: '%', color: '#f59e0b' },
  { key: 'memory_percent', label: 'Memory Utilization', unit: '%', color: '#c084fc' },
  { key: 'error_rate', label: 'Error Rate', unit: '%', scale: 100, color: '#fb7185' },
  { key: 'queue_depth', label: 'Queue Depth', unit: '', color: '#34d399' },
]

function SparklineCard({ label, unit, color, points }) {
  const values = points.map((p) => p.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const latest = values[values.length - 1]
  const span = max - min || 1
  const width = 320
  const height = 64
  const pad = 6

  const coords = points.map((pt, i) => {
    const x = points.length === 1 ? width / 2 : pad + (i / (points.length - 1)) * (width - pad * 2)
    const y = height - pad - ((pt.value - min) / span) * (height - pad * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const polyline = coords.join(' ')
  const area = `${pad},${height - pad} ${polyline} ${width - pad},${height - pad}`
  const fmt = (v) => (Number.isInteger(v) ? String(v) : v.toFixed(1))

  return (
    <Card className="space-y-2 p-3.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold">{label}</span>
        <div className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
          <span>Peak: <strong className="text-foreground">{fmt(max)}{unit}</strong></span>
          <span>Latest: <strong style={{ color }}>{fmt(latest)}{unit}</strong></span>
        </div>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-16 w-full overflow-visible">
        <polygon points={area} fill={color} fillOpacity="0.12" />
        <polyline fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" points={polyline} />
        {points.length > 0 && (() => {
          const last = coords[coords.length - 1].split(',')
          return <circle cx={last[0]} cy={last[1]} r="3" fill={color} />
        })()}
      </svg>
      <div className="flex justify-between font-mono text-[10px] text-muted-foreground">
        <span>{points[0]?.time || ''}</span>
        <span>{points.length} samples</span>
        <span>{points[points.length - 1]?.time || ''}</span>
      </div>
    </Card>
  )
}

export default function MetricSparklines({ observations = [] }) {
  const series = useMemo(() => {
    const timed = observations
      .filter((o) => o.modality === 'metrics' && o.metric_values && Object.keys(o.metric_values).length > 0)
      .slice()
      .sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''))

    return METRIC_SERIES.map((cfg) => {
      const pts = []
      for (const obs of timed) {
        const raw = obs.metric_values?.[cfg.key]
        if (typeof raw === 'number' && Number.isFinite(raw)) {
          pts.push({
            value: cfg.scale ? raw * cfg.scale : raw,
            time: obs.timestamp ? `${obs.timestamp.slice(11, 19)}Z` : '',
          })
        }
      }
      return { ...cfg, points: pts }
    }).filter((s) => s.points.length >= 2)
  }, [observations])

  if (!series.length) {
    return (
      <p className="panel-empty">
        Upload timestamped metrics (JSON/CSV) with at least 2 samples to view interactive metric charts.
      </p>
    )
  }

  return (
    <div className="space-y-3">
      {series.map((item) => (
        <SparklineCard key={item.key} {...item} />
      ))}
    </div>
  )
}

