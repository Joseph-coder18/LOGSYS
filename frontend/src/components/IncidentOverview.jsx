import { Activity, Clock3, Database, Layers } from 'lucide-react'
import { Tooltip } from '@base-ui/react/tooltip'
import { Card } from '@/components/ui/surfaces'

const sources = [
  { key: 'log', label: 'Logs', color: 'var(--color-log)' },
  { key: 'metrics', label: 'Metric samples', color: 'var(--color-metrics)' },
  { key: 'change', label: 'Changes', color: 'var(--color-change)' },
  { key: 'image', label: 'Screenshot panels', color: 'var(--color-image)' },
]
const signals = [
  { key: 'errors', label: 'Errors', color: 'var(--color-error)' },
  { key: 'anomalies', label: 'Anomalies', color: 'var(--color-warning)' },
  { key: 'changes', label: 'Changes', color: 'var(--color-change)' },
  { key: 'other', label: 'Other', color: 'var(--color-primary)' },
]
const integer = new Intl.NumberFormat()
const shortTime = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', second: '2-digit' })
const day = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', day: 'numeric', month: 'short', year: 'numeric' })

export function utcRange(start, end) {
  if (!start || !end) return ''
  const startDate = new Date(start)
  const endDate = new Date(end)
  if (start === end) return `${day.format(startDate)} · ${shortTime.format(startDate)} UTC`
  if (day.format(startDate) === day.format(endDate)) {
    return `${day.format(startDate)} · ${shortTime.format(startDate)}–${shortTime.format(endDate)} UTC`
  }
  return `${day.format(startDate)} ${shortTime.format(startDate)}–${day.format(endDate)} ${shortTime.format(endDate)} UTC`
}

function SourceMix({ counts, total }) {
  let offset = 0
  const used = sources.filter(item => counts[item.key])
  const gradient = used.map(({ key, color }) => {
    const start = offset
    offset += counts[key] / total * 100
    return `${color} ${start}% ${offset}%`
  }).join(', ')
  return (
    <Card className="side-panel">
      <div className="side-panel-title"><h2 className="flex items-center gap-2"><Layers size={15} /> Source coverage</h2></div>
      <div className="source-coverage">
        <div className="coverage-ring" style={{ background: `conic-gradient(${gradient})` }} aria-hidden="true">
          <span>{integer.format(total)}<small>records</small></span>
        </div>
        <dl className="min-w-0 flex-1 space-y-2">
          {sources.filter(item => item.key !== 'image' || counts.image).map(({ key, label, color }) => (
            <div className="flex items-center justify-between gap-2 text-xs" key={key}>
              <dt className="flex items-center gap-2 text-muted-foreground"><i className="chart-key" style={{ backgroundColor: color }} />{label}</dt>
              <dd className="font-mono">{integer.format(counts[key] || 0)}</dd>
            </div>
          ))}
        </dl>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">Dependency manifests are listed under Sources.</p>
    </Card>
  )
}

function ActivityBar({ bucket, peak, onRange, sample }) {
  const { start, end, counts } = bucket
  const values = { ...counts, other: counts.all - counts.errors - counts.anomalies - counts.changes }
  const range = utcRange(start, end)
  return (
    <Tooltip.Root>
      <Tooltip.Trigger
        type="button"
        className="activity-column"
        delay={120}
        aria-label={`${sample ? 'Inspect sample window' : 'Filter timeline'} · ${range} · ${counts.all} records`}
        onClick={sample ? undefined : () => onRange(start, end)}
      >
        {signals.map(({ key, color }) => <span key={key} style={{ height: `${values[key] / peak * 100}%`, backgroundColor: color }} />)}
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Positioner sideOffset={8} collisionPadding={10} className="z-50">
          <Tooltip.Popup className="activity-tooltip">
            <p className="font-medium">{range}</p>
            <p className="mb-2 text-muted-foreground">{integer.format(counts.all)} {counts.all === 1 ? 'record' : 'records'} in this window</p>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
              {signals.map(({ key, label, color }) => (
                <div key={key} className="flex items-center justify-between gap-2">
                  <dt className="flex items-center gap-1.5"><i className="chart-key" style={{ backgroundColor: color }} />{label}</dt>
                  <dd className="font-mono">{integer.format(values[key])}</dd>
                </div>
              ))}
            </dl>
            {!sample && <p className="mt-2 border-t border-border pt-2 text-primary">Open this window in Timeline</p>}
          </Tooltip.Popup>
        </Tooltip.Positioner>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
}

function ActivityChart({ activity, undated, onRange, sample }) {
  const peak = Math.max(1, ...activity.map(bucket => bucket.counts.all))
  return (
    <Card className="side-panel">
      <div className="side-panel-title"><h2 className="flex items-center gap-2"><Activity size={15} /> Event activity</h2><span>UTC</span></div>
      {activity.length ? (
        <>
          <div className="activity-axis">
            <div className="activity-scale" aria-hidden="true"><span>{peak}</span><span>0</span></div>
            <div className="activity-plot" style={{ gridTemplateColumns: `repeat(${activity.length}, minmax(0, 1fr))` }}>
              {activity.map(bucket => <ActivityBar key={bucket.start} bucket={bucket} peak={peak} onRange={onRange} sample={sample} />)}
            </div>
          </div>
          <div className="activity-labels" aria-hidden="true"><span>{shortTime.format(new Date(activity[0].start))}</span><span>{shortTime.format(new Date(activity.at(-1).end))}</span></div>
          <ul className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {signals.map(({ key, label, color }) => <li key={key} className="flex items-center gap-1.5"><i className="chart-key" style={{ backgroundColor: color }} />{label}</li>)}
          </ul>
        </>
      ) : <p className="panel-empty">Upload records with timestamps and timezones to see activity over time.</p>}
      {undated > 0 && <p className="mt-2 text-xs text-muted-foreground">{integer.format(undated)} records without a reliable timestamp are excluded from the chart.</p>}
    </Card>
  )
}

export default function IncidentOverview({ overview, sample, onRange }) {
  if (!overview?.counts.all) return null
  const { counts, modalities, activity, undated, first_at, last_at, services } = overview
  return (
    <section className="mb-4" aria-label="Telemetry overview">
      <div className="telemetry-heading">
        <h2><Database size={16} /> {sample ? 'Sample telemetry' : 'Telemetry overview'}</h2>
        <p><Clock3 size={13} /> {utcRange(first_at, last_at) || 'Time range unknown'}</p>
      </div>
      <dl className="telemetry-stats">
        {[
          ['Records', counts.all],
          ['Services', services.length],
          ['Errors', counts.errors],
          ['Anomalies', counts.anomalies],
        ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{integer.format(value)}</dd></div>)}
      </dl>
      <div className="telemetry-charts">
        <ActivityChart activity={activity} undated={undated} onRange={onRange} sample={sample} />
        <SourceMix counts={modalities} total={counts.all} />
      </div>
    </section>
  )
}
