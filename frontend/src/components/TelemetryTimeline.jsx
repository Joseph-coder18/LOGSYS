import { Badge } from '@/components/ui/surfaces'

const errorLevels = new Set(['ERROR', 'CRITICAL', 'FATAL'])
const changeLabels = { deployment: 'Deployment', restart: 'Restart', config_change: 'Configuration change' }

export function MetricValues({ event, emphasize }) {
  // Existing observations have only a display summary and anomaly reasons.
  const structured = Object.keys(event.metric_values || {}).length > 0
  const pairs = structured
    ? Object.entries(event.metric_values).map(([field, value]) => [field, `${field}=${value}`])
    : event.summary.split(', ').map(pair => [pair.slice(0, pair.indexOf('=')), pair])
  const flagged = new Set(event.anomaly_fields)
  return (
    <>
      {pairs.map(([field, pair], index) => {
        const isAnomaly = structured
          ? flagged.has(field)
          : event.anomalies?.some(reason => reason.startsWith(`${field}: `))
        return (
          <span key={index}>
            {index > 0 && ', '}
            {isAnomaly ? <strong className="font-semibold text-warning">{pair}<span className="sr-only"> (anomalous)</span></strong>
              : emphasize?.has(field) ? <strong className="font-semibold text-primary">{pair}</strong> : pair}
          </span>
        )
      })}
    </>
  )
}

export default function TelemetryTimeline({ observations, anchored = true, selectedId }) {
  return (
    <ol className="timeline">
      {observations.map(event => (
        <li key={event.id} id={anchored ? `observation-${event.id}` : undefined} tabIndex={anchored ? -1 : undefined} className={event.id === selectedId ? "reference-target" : undefined}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="timeline-time">
              {event.timestamp
                ? `${event.timestamp.slice(0, 10)} ${event.timestamp.slice(11, 19)} UTC`
                : 'Time uncertain'}
            </span>
            <Badge tone={errorLevels.has(event.severity) ? 'error' : event.modality}>
              {changeLabels[event.change_type] || (event.modality === 'log' ? event.severity || 'Log' : event.modality === 'image' ? 'Screenshot' : 'Metrics')}
            </Badge>
            {event.service && <span className="source-name">{event.service}</span>}
            {event.anomalies.length > 0 && <Badge tone="warning">Anomaly</Badge>}
          </div>
          <p>{event.modality === 'metrics' ? <MetricValues event={event} /> : event.summary}</p>
          <details className="mt-1.5 min-w-0" open={!anchored || event.id === selectedId ? true : undefined}>
            <summary className="text-xs text-primary break-all">
              {event.source_file} · {event.source_label || `${event.modality === 'log' ? 'line' : 'record'} ${event.source_position}`}
            </summary>
            {!event.timestamp && event.original_timestamp && (
              <p>{event.modality === 'image' ? 'Displayed time range' : 'Source timestamp'} · {event.original_timestamp}</p>
            )}
            <pre className="mt-2 whitespace-pre-wrap break-all rounded-md bg-background p-2 text-xs">{event.excerpt}</pre>
            {event.anomalies.length > 0 && (
              <ul className="mt-1.5 text-xs text-warning">{event.anomalies.map(anomaly => <li key={anomaly}>{anomaly}</li>)}</ul>
            )}
          </details>
        </li>
      ))}
    </ol>
  )
}
