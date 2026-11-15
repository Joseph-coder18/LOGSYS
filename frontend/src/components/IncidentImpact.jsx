import { useEffect, useState } from 'react'
import { Activity, ArrowUpRight, ChevronDown, Database, Network, TriangleAlert } from 'lucide-react'
import { Alert, Badge, Card } from '@/components/ui/surfaces'
import { Button } from '@/components/ui/button'
import { sessionImpact } from '@/services/api'
import { MetricValues } from './TelemetryTimeline'

const keyMetrics = new Set(['db_latency_ms', 'error_rate', 'db_connections_limit', 'db_connections_active'])
const errorLevels = new Set(['ERROR', 'CRITICAL', 'FATAL'])
const errorTokens = /\b([A-Z][\w]*(?:Error|Timeout|Exception)|5\d{2})\b/
const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })
const percent = new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 })

function timeOf(timestamp) {
  return timestamp.slice(11, 19)
}

function SignalTime({ timestamp }) {
  return timestamp
    ? <time dateTime={timestamp} title={`${timestamp.slice(0, 10)} ${timeOf(timestamp)} UTC`}>{timeOf(timestamp)} UTC</time>
    : 'Time uncertain'
}

function metricReadings(event) {
  const { db_latency_ms: latency, error_rate: errorRate, db_connections_active: active, db_connections_limit: limit } = event?.metric_values || {}
  return [
    { id: 'latency', label: 'DB latency', value: Number.isFinite(latency) ? `${number.format(latency)} ms` : null, icon: Activity, fields: ['db_latency_ms'] },
    { id: 'errors', label: 'Error rate', value: Number.isFinite(errorRate) ? percent.format(errorRate) : null, icon: TriangleAlert, fields: ['error_rate'] },
    {
      id: 'pool', label: 'Pool usage', value: Number.isFinite(active) && Number.isFinite(limit) && limit > 0 ? `${number.format(active)} / ${number.format(limit)}` : null,
      icon: Database, fields: ['db_connections_active', 'db_connections_limit'], utilization: active / limit,
    },
  ].filter(reading => reading.value !== null)
}

function LatencyTrend({ samples }) {
  const readings = samples.filter(sample => Number.isFinite(sample.metric_values?.db_latency_ms) && sample.metric_values.db_latency_ms >= 0)
  if (readings.length < 2) return null
  const first = Date.parse(readings[0].timestamp)
  const span = Date.parse(readings.at(-1).timestamp) - first
  const ceiling = Math.max(1, ...readings.map(sample => sample.metric_values.db_latency_ms))
  const points = readings.map((sample, index) => {
    const value = sample.metric_values.db_latency_ms
    const position = span ? (Date.parse(sample.timestamp) - first) / span : index / (readings.length - 1)
    return { id: sample.id, time: timeOf(sample.timestamp), value, x: 4 + position * 200, y: 38 - value / ceiling * 32 }
  })
  const midpoint = points[Math.floor(points.length / 2)]
  return (
    <figure className="mt-2 min-w-0">
      <svg className="block h-10 w-full overflow-visible text-metrics" viewBox="0 0 208 42" preserveAspectRatio="none" role="img"
        aria-label={`Recent DB latency samples, UTC. ${points.map(point => `${point.time}, ${number.format(point.value)} milliseconds`).join('. ')}`}>
        <path d="M4 38H204" className="stroke-border" fill="none" vectorEffect="non-scaling-stroke" />
        <polyline points={points.map(point => `${point.x},${point.y}`).join(' ')} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        {points.map(point => (
          <circle key={point.id} cx={point.x} cy={point.y} r="2.5" fill="currentColor">
            <title>{point.time} UTC · {number.format(point.value)} ms</title>
          </circle>
        ))}
      </svg>
      <figcaption className="impact-trend-times font-mono text-muted-foreground">
        <span>{points[0].time}</span>
        {midpoint.x >= 68 && midpoint.x <= 140 && <span className="impact-trend-middle" style={{ left: `${midpoint.x / 208 * 100}%` }}>{midpoint.time}</span>}
        <span>{points.at(-1).time}</span>
      </figcaption>
    </figure>
  )
}

function TimelineLink({ event, onObservation }) {
  return (
    <Button variant="ghost" size="sm" className="shrink-0 self-start text-xs text-primary" onClick={() => onObservation(event.id)}>
      Open in timeline <ArrowUpRight aria-hidden="true" />
    </Button>
  )
}

function LatestMetrics({ samples, onObservation }) {
  const latest = samples.at(-1)
  const readings = metricReadings(latest)
  if (!readings.length) return null
  const anomalies = new Set(latest.anomaly_fields)
  return (
    <div className="mt-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">Latest sample · <SignalTime timestamp={latest.timestamp} /></p>
        <TimelineLink event={latest} onObservation={onObservation} />
      </div>
      <dl className="impact-metric-grid">
        {readings.map(({ id, label, value, icon: Icon, fields, utilization }) => {
          const flagged = fields.some(field => anomalies.has(field))
          return (
            <div key={id} className="min-w-0 rounded-md border border-border bg-background/65 p-2.5">
              <dt className="flex items-center gap-1.5 text-xs text-muted-foreground"><Icon size={13} aria-hidden="true" />{label}</dt>
              <dd className={`mt-1 font-mono text-lg font-semibold [overflow-wrap:anywhere] ${flagged ? 'text-warning' : 'text-foreground'}`}>
                {value}{flagged && <span className="sr-only"> (anomalous)</span>}
              </dd>
              {id === 'latency' && <dd><LatencyTrend samples={samples} /></dd>}
              {id === 'pool' && (
                <dd className="mt-2">
                  <div className="h-1 overflow-hidden rounded-full bg-muted" aria-hidden="true">
                    <div className={`h-full rounded-full ${flagged ? 'bg-warning' : 'bg-primary'}`} style={{ width: `${Math.max(0, Math.min(1, utilization)) * 100}%` }} />
                  </div>
                  <span className="mt-1 block text-xs text-muted-foreground">{percent.format(utilization)} of capacity</span>
                </dd>
              )}
            </div>
          )
        })}
      </dl>
    </div>
  )
}

function LogSummary({ event }) {
  if (!errorLevels.has(event.severity)) return event.summary
  return event.summary.split(errorTokens).map((part, index) => index % 2
    ? <strong key={index} className="font-mono text-error">{part}</strong>
    : part)
}

function SignalRows({ observations, onObservation }) {
  if (!observations.length) return <p className="text-xs text-muted-foreground">No recent signals for this service.</p>
  return (
    <ol className="divide-y divide-border">
      {observations.map(event => {
        const readings = metricReadings(event)
        const flagged = new Set(event.anomaly_fields)
        return (
          <li key={event.id} className="impact-signal-row">
            <div className="impact-signal-source min-w-0 text-xs text-muted-foreground">
              <span className="font-mono text-primary"><SignalTime timestamp={event.timestamp} /></span>
              <span className="block [overflow-wrap:anywhere]">{event.source_file}</span>
            </div>
            <div className="impact-signal-detail min-w-0 text-sm [overflow-wrap:anywhere]">
              {event.modality === 'metrics' ? (
                <>
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
                    {readings.length ? readings.map(reading => (
                      <span key={reading.id} className="text-muted-foreground">
                        {reading.label} <strong className={`font-mono ${reading.fields.some(field => flagged.has(field)) ? 'text-warning' : 'text-foreground'}`}>{reading.value}</strong>
                      </span>
                    )) : <span>Metric sample</span>}
                  </div>
                  <details className="mt-1">
                    <summary className="w-fit text-xs text-muted-foreground hover:text-primary">Show all fields</summary>
                    <p className="mt-1 rounded bg-muted/55 p-2 font-mono text-xs"><MetricValues event={event} emphasize={keyMetrics} /></p>
                  </details>
                </>
              ) : <LogSummary event={event} />}
            </div>
            <TimelineLink event={event} onObservation={onObservation} />
          </li>
        )
      })}
    </ol>
  )
}

function CallerCard({ caller, observed, onObservation }) {
  const signal = caller.observations.find(event => errorLevels.has(event.severity)) || caller.observations.at(-1)
  const route = signal?.summary.match(/\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \/\S*(?=\s+504\b)/)?.[0]
  const headline = signal && (route ? `${route} returned 504` : /(?:^|\s)504\b/.test(signal.summary) ? 'Gateway timeout (504)'
    : /\bDatabaseTimeout\b/.test(signal.summary) ? 'Database connection timed out' : signal.summary)
  return (
    <Card className={`min-w-0 border-l-2 bg-background/55 p-3 ${observed ? 'border-l-warning/70' : 'border-l-change/55'}`}>
      <div className="flex flex-wrap items-center justify-between gap-1.5">
        <h4 className="min-w-0 text-sm font-semibold [overflow-wrap:anywhere]">{caller.service}</h4>
        <Badge tone={observed ? 'warning' : 'change'}>{observed ? 'Observed impact' : 'Potential impact'}</Badge>
      </div>
      <p className="mt-1 text-xs text-muted-foreground [overflow-wrap:anywhere]" aria-label={`Caller path ${caller.path.join(' to ')}`}>{caller.path.join(' → ')}</p>
      {signal ? (
        <>
          <p className="mt-2 text-sm font-medium [overflow-wrap:anywhere]">{headline}</p>
          <div className="mt-1 flex flex-wrap items-center justify-between gap-x-1.5 text-xs text-muted-foreground">
            <span className="font-mono"><SignalTime timestamp={signal.timestamp} /></span>
            <TimelineLink event={signal} onObservation={onObservation} />
          </div>
          <details className="group mt-1 border-t border-border pt-2">
            <summary className="flex w-fit list-none items-center gap-1.5 text-xs text-muted-foreground hover:text-primary">
              <ChevronDown size={13} className="group-open:rotate-180" aria-hidden="true" /> Recent signals ({caller.observations.length})
            </summary>
            <SignalRows observations={caller.observations} onObservation={onObservation} />
          </details>
        </>
      ) : <p className="mt-2 text-xs text-muted-foreground">No recent signals for this caller.</p>}
    </Card>
  )
}

export default function IncidentImpact({ sessionId, service, affectedServices = [], onObservation }) {
  const [impact, setImpact] = useState(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let active = true
    sessionImpact(sessionId, service)
      .then(result => { if (active) setImpact(result) })
      .catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [sessionId, service])

  if (error) return <Alert>Could not load the impact path.</Alert>
  if (!impact) return <p role="status" className="panel-empty">Loading impact path…</p>
  return (
    <Card className="side-panel impact-panel mt-3">
      <div className="side-panel-title">
        <h2 className="flex items-center gap-2"><Network size={16} className="text-primary" aria-hidden="true" />Caller impact</h2>
      </div>
      <div className="rounded-md border border-border p-3">
        <h3 className="text-sm font-semibold [overflow-wrap:anywhere]">{impact.service} <Badge className="ml-1">Selected service</Badge></h3>
        <LatestMetrics samples={impact.metric_samples || []} onObservation={onObservation} />
        <div className="mt-3">
          <h4 className="mb-1 text-xs font-medium text-muted-foreground">Recent signals</h4>
          <SignalRows observations={impact.observations} onObservation={onObservation} />
        </div>
      </div>
      <div className="mt-4">
        <h3 className="mb-2 text-sm font-semibold">Downstream callers</h3>
        {impact.dependents.length ? (
          <ul className="impact-caller-grid">
            {impact.dependents.map(caller => (
              <li className="min-w-0" key={caller.service}>
                <CallerCard caller={caller} observed={affectedServices.includes(caller.service)} onObservation={onObservation} />
              </li>
            ))}
          </ul>
        ) : <p className="text-xs text-muted-foreground">No dependent callers in the active manifest.</p>}
      </div>
    </Card>
  )
}
