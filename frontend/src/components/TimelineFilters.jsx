import { Button } from '@/components/ui/button'

const categories = [
  ['all', 'All'],
  ['anomalies', 'Anomalies'],
  ['changes', 'Changes'],
  ['errors', 'Errors'],
]

export default function TimelineFilters({ counts, services, filters, disabled, onChange }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
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
  )
}
