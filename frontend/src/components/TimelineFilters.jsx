import { useState } from "react"
import { Button } from '@/components/ui/button'

const categories = [
  ['all', 'All'],
  ['anomalies', 'Anomalies'],
  ['changes', 'Changes'],
  ['errors', 'Errors'],
]

function TimeRange({ start, end, disabled, onChange }) {
  const [from, setFrom] = useState(start ? new Date(start).toISOString().slice(0, 19) : '')
  const [until, setUntil] = useState(end ? new Date(end).toISOString().slice(0, 19) : '')
  const invalid = !!(from && until && new Date(from + 'Z') >= new Date(until + 'Z'))
  const inputClass = 'min-w-0 rounded-md border border-border bg-background px-2 py-1.5 text-xs text-foreground'

  return (
    <form className="mb-4 flex flex-wrap items-end gap-2 border-b border-border pb-3" onSubmit={event => {
      event.preventDefault()
      if (!invalid) onChange(from ? new Date(from + 'Z').toISOString() : '', until ? new Date(until + 'Z').toISOString() : '')
    }}>
      <label className="grid gap-1 text-xs text-muted-foreground">From (UTC)
        <input className={inputClass} type="datetime-local" step="1" value={from} onChange={event => setFrom(event.target.value)} disabled={disabled} />
      </label>
      <label className="grid gap-1 text-xs text-muted-foreground">Until (UTC, exclusive)
        <input className={inputClass} type="datetime-local" step="1" value={until} onChange={event => setUntil(event.target.value)} disabled={disabled} />
      </label>
      <Button size="sm" variant="outline" type="submit" disabled={disabled || invalid}>Apply range</Button>
      {(start || end || from || until) && <Button size="sm" variant="ghost" type="button" disabled={disabled} onClick={() => {
        setFrom('')
        setUntil('')
        if (start || end) onChange('', '')
      }}>Clear range</Button>}
      {invalid && <span className="w-full text-xs text-error" role="status">Choose an end time after the start.</span>}
    </form>
  )
}

export default function TimelineFilters({ counts, services, filters, disabled, onChange }) {
  return (
    <>
    <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Event type">
        {categories.map(([category, label]) => (
          <Button
            key={category}
            size="sm"
            variant={filters.category === category ? 'default' : 'outline'}
            className="rounded-full"
            aria-pressed={filters.category === category}
            disabled={disabled}
            onClick={() => onChange({ ...filters, category })}
          >
            {label} <span className="text-xs">{counts[category]}</span>
          </Button>
        ))}
      </div>
      {services.length > 0 && (
        <label className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          Service
          <select
            className="min-w-0 max-w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground"
            value={filters.service}
            disabled={disabled}
            onChange={event => onChange({ ...filters, service: event.target.value })}
          >
            <option value="">All services</option>
            {services.map(service => <option key={service} value={service}>{service}</option>)}
          </select>
        </label>
      )}
    </div>
    <TimeRange key={`${filters.start}/${filters.end}`} start={filters.start} end={filters.end} disabled={disabled} onChange={(start, end) => onChange({ ...filters, start, end })} />
    </>
  )
}
