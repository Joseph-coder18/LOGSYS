import { useEffect, useState } from 'react'
import { ArrowUpRight } from 'lucide-react'
import { Alert, Card } from '@/components/ui/surfaces'
import { Button } from '@/components/ui/button'
import { sessionImpact } from '@/services/api'

function SignalLinks({ observations, onObservation }) {
  if (!observations.length) return <p className="text-xs text-muted-foreground">No changes, warnings, or anomalies attributed to this service.</p>
  return (
    <ul className="grid gap-1">
      {observations.map(event => (
        <li key={event.id}>
          <Button variant="ghost" className="h-auto w-full justify-start whitespace-normal px-1 py-1 text-left" size="sm" onClick={() => onObservation(event.id)}>
            <ArrowUpRight aria-hidden="true" />
            <span className="min-w-0 break-words">{event.summary}<small className="block text-muted-foreground">{event.source_file} · {event.timestamp ? event.timestamp.slice(11, 19) + ' UTC' : 'Time uncertain'}</small></span>
          </Button>
        </li>
      ))}
    </ul>
  )
}

export default function IncidentImpact({ sessionId, service, onObservation }) {
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
    <Card className="side-panel mt-3">
      <div className="side-panel-title"><h2>Potential impact</h2></div>
      <p className="mb-3 text-xs text-muted-foreground">Declared dependency paths and up to three recent observations per service.</p>
      <h3 className="mb-1 text-sm font-semibold">{impact.service}</h3>
      <SignalLinks observations={impact.observations} onObservation={onObservation} />
      {impact.dependents.length ? (
        <ul className="mt-3 grid gap-3">
          {impact.dependents.map(item => (
            <li key={item.service} className="border-t border-border pt-3">
              <h3 className="text-sm font-semibold">{item.service}</h3>
              <p className="mb-1 break-words font-mono text-xs text-muted-foreground">{item.path.join(' → ')}</p>
              <SignalLinks observations={item.observations} onObservation={onObservation} />
            </li>
          ))}
        </ul>
      ) : <p className="mt-3 text-xs text-muted-foreground">No dependent callers in the active manifest.</p>}
    </Card>
  )
}
