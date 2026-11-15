import { Accordion } from '@base-ui/react/accordion'
import { ArrowUpRight, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge, Card } from '@/components/ui/surfaces'

const outcomes = {
  supports: { label: 'Supports', tone: 'positive', points: '+1' },
  contradicts: { label: 'Contradicts', tone: 'error', points: '−1' },
  missing: { label: 'Missing', tone: 'warning', points: '0' },
}

export default function HypothesisList({ hypotheses, onObservation }) {
  if (!hypotheses?.length) return null
  return (
    <Card className="side-panel mb-3">
      <div className="side-panel-title"><h2>Ranked hypotheses</h2></div>
      <Accordion.Root className="grid gap-2">
        {hypotheses.map((hypothesis, index) => (
          <Accordion.Item key={index} className="rounded-md border border-border">
            <Accordion.Header>
              <Accordion.Trigger className="group flex w-full flex-wrap items-center gap-x-2 gap-y-1 rounded-md px-3 py-2 text-left text-sm font-semibold hover:bg-muted focus-visible:outline-offset-[-2px]">
                <span className="min-w-0 flex-1 break-words">{hypothesis.label} <small className="block text-xs font-normal text-muted-foreground">{hypothesis.service}</small></span>
                <Badge>{hypothesis.score} {hypothesis.score === 1 ? 'point' : 'points'}</Badge>
                <ChevronDown size={15} className="group-aria-expanded:rotate-180" aria-hidden="true" />
              </Accordion.Trigger>
            </Accordion.Header>
            <Accordion.Panel className="px-3 pb-3">
              <p className="mb-2 text-xs text-muted-foreground">Each supporting check adds one point and each contradiction subtracts one. A supported diagnosis must pass every required check.</p>
              <ul className="grid gap-2">
                {hypothesis.checks.map(check => {
                  const { label, tone, points } = outcomes[check.outcome]
                  return (
                    <li key={check.criterion} className="border-t border-border pt-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={tone}>{label} · {points}</Badge>
                        <span className="text-xs font-medium">{check.criterion}</span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">{check.detail}</p>
                      {check.observation_id && (
                        <Button size="sm" variant="ghost" className="mt-1" onClick={() => onObservation(check.observation_id)}>
                          <ArrowUpRight /> Open source
                        </Button>
                      )}
                    </li>
                  )
                })}
              </ul>
            </Accordion.Panel>
          </Accordion.Item>
        ))}
      </Accordion.Root>
    </Card>
  )
}
