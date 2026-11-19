import { ArrowUpRight, Database, ListChecks, MessageSquare, Route, ShieldCheck } from 'lucide-react'
import { Badge, Card } from '@/components/ui/surfaces'
import { Button } from '@/components/ui/button'

const stages = {
  Verification: { icon: ShieldCheck, color: 'text-primary' },
  'Incident retrieval': { icon: Database, color: 'text-metrics' },
  'Grounded chat': { icon: MessageSquare, color: 'text-log' },
}
const defaultStage = { icon: Route, color: 'text-muted-foreground' }
const statuses = {
  confirmed: { label: 'Confirmed', tone: 'positive' },
  missing: { label: 'Evidence missing', tone: 'warning' },
  generated: { label: 'Model reply', tone: 'positive' },
  extractive: { label: 'Source-backed reply', tone: 'neutral' },
  local: { label: 'Local reply', tone: 'warning' },
  no_evidence: { label: 'Insufficient evidence', tone: 'warning' },
}
const verificationOnly = { label: 'Verification only', tone: 'neutral' }
const numeric = new Intl.NumberFormat()

const metricLayouts = {
  'Compare candidates': {
    agent: 'Verification',
    labels: { observations: 'Dated observations', hypotheses: 'Hypotheses checked', passed: 'Passed all checks' },
    legacy: /^Loaded (\d+) dated observations, checked (\d+) hypotheses, and found (\d+) that passed every required check\.$/,
  },
  'Retrieve session observations': {
    agent: 'Incident retrieval',
    labels: { keyword: 'Keyword matches', semantic: 'Semantic matches', carried: 'Previous sources', distinct: 'Distinct records used', graph: 'Dependency records added', source_groups: 'Source groups' },
    legacy: /^Keyword search matched (\d+) records\. Semantic search resolved (\d+) records\. Retained (\d+) previous sources\. Supplied (\d+) distinct records\.$/,
  },
  'Compose cited answer': {
    agent: 'Grounded chat',
    labels: { model_calls: 'Model requests' },
  },
}

function stepMetrics(step) {
  const layout = metricLayouts[step.action]
  if (!layout || step.agent !== layout.agent) return []
  const legacy = layout.legacy && typeof step.result === 'string' ? step.result.match(layout.legacy) : null
  return Object.entries(layout.labels)
    .map(([key, label], index) => ({ key, label, value: step.metrics?.[key] ?? (legacy ? Number(legacy[index + 1]) : null) }))
    .filter(entry => Number.isFinite(entry.value))
}

function replyStatus(step, answerMode) {
  if (step.status) return statuses[step.status]
  if (typeof step.result === 'string' &&
    /^No session observations matched|^The model found insufficient evidence/.test(step.result)) return statuses.no_evidence
  return statuses[answerMode]
}

function groupStages(trace) {
  const groups = []
  for (const step of trace) {
    const agent = step.agent || 'Analysis'
    if (groups.at(-1)?.agent !== agent) groups.push({ agent, steps: [] })
    groups.at(-1).steps.push(step)
  }
  return groups
}

function StepResult({ result }) {
  if (result == null) return null
  return typeof result === 'string'
    ? <p className="mt-1 text-sm leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">{result}</p>
    : <pre className="mt-1 whitespace-pre-wrap rounded-md bg-background p-2 text-xs text-muted-foreground [overflow-wrap:anywhere]">{JSON.stringify(result, null, 2)}</pre>
}

function TraceStep({ step }) {
  const metrics = stepMetrics(step)
  const status = ['confirmed', 'missing'].includes(step.status) ? statuses[step.status] : null
  return (
    <li className="min-w-0">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h4 className="text-sm font-medium [overflow-wrap:anywhere]">{step.action || 'Result'}</h4>
        {status && <Badge tone={status.tone}>{status.label}</Badge>}
      </div>
      {step.provider_status && step.provider_status.status !== 'not_requested' && <p className="mt-2 text-xs text-muted-foreground">Generation · {({validated: 'Validated reply', rate_limited: 'Provider rate limit', timeout: 'Provider timeout', provider_error: 'Provider error', invalid_output: 'Output failed validation', no_answer: 'No answer in the supplied evidence'})[step.provider_status.status] || step.provider_status.status}{step.provider_status.http_status && ` (HTTP ${step.provider_status.http_status})`}</p>}
      {step.semantic_status && <p className="mt-2 text-xs text-muted-foreground">Semantic search · {({disabled: 'Disabled', not_requested: 'Not needed for this answer', matched: 'Matched indexed sources', no_matches: 'Search succeeded with no matches', error: 'Provider unavailable — local evidence retained', dimension_mismatch: 'Index dimension mismatch', embedding_error: 'Embedding unavailable', unresolved_sources: 'No matching source records in this scope'})[step.semantic_status.status] || step.semantic_status.status}{step.semantic_status.message && ` · ${step.semantic_status.message}`}</p>}
      {(step.scope?.service || step.scope?.start || step.scope?.end) && <p className="mt-1 break-words text-xs text-muted-foreground">Scope · {step.scope.service || 'All services'} · {step.scope.start || 'Unbounded'} → {step.scope.end || 'Unbounded'} UTC</p>}
      {metrics.length ? (
        <>
          <dl className="trace-metrics mt-2">
            {metrics.map(({ key, label, value }) => (
              <div key={key} className="min-w-0 rounded-md border border-border bg-background/55 px-2.5 py-2">
                <dt className="text-xs leading-snug text-muted-foreground">{label}</dt>
                <dd className="mt-1 font-mono text-lg font-semibold [overflow-wrap:anywhere]">{numeric.format(value)}</dd>
              </div>
            ))}
          </dl>
          <details className="mt-2">
            <summary className="w-fit text-xs text-muted-foreground hover:text-primary">Raw result</summary>
            <StepResult result={step.result} />
          </details>
        </>
      ) : <StepResult result={step.result} />}
    </li>
  )
}

export default function InvestigationTrace({ trace, answerMode, sample, stale, hasEvidence, onNavigate }) {
  if (!trace.length) return <p className="panel-empty">Verify a root cause or ask a question to see the investigation steps.</p>

  const grouped = groupStages(trace)
  const reply = trace.find(step => step.agent === 'Grounded chat')
  const status = reply ? replyStatus(reply, answerMode) : !sample && grouped.every(group => group.agent === 'Verification') ? verificationOnly : null
  return (
    <section aria-label="Investigation trace">
      <Card className="side-panel mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold"><ListChecks size={16} className="text-primary" aria-hidden="true" /> Investigation trace</h2>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {status && <Badge tone={status.tone}>{status.label}</Badge>}
            {sample && <Badge tone="warning">Sample trace</Badge>}
            {stale && <Badge tone="warning">Previous evidence</Badge>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {hasEvidence && <Button size="sm" variant="outline" onClick={() => onNavigate('evidence')}>View evidence <ArrowUpRight aria-hidden="true" /></Button>}
          {reply && <Button size="sm" variant="outline" onClick={() => onNavigate('chat')}>Open chat <ArrowUpRight aria-hidden="true" /></Button>}
        </div>
      </Card>

      <ol className="trace-stages">
        {grouped.map(({ agent, steps }, index) => {
          const { icon: Icon, color } = stages[agent] || defaultStage
          return (
            <li className="trace-stage" key={`${agent}/${index}`}>
              <div className={`trace-stage-icon ${color}`}><Icon size={15} aria-hidden="true" /></div>
              <Card className={`side-panel ${agent === 'Grounded chat' && status?.tone === 'warning' ? 'border-warning/40' : ''}`}>
                <h3 className={`mb-2 text-xs font-semibold [overflow-wrap:anywhere] ${color}`}>{agent}</h3>
                <ol className="trace-steps">
                  {steps.map((step, stepIndex) => <TraceStep step={step} key={stepIndex} />)}
                </ol>
              </Card>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
