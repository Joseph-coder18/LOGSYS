import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Clock3, FileText, GitFork, Info, ListChecks, MessagesSquare, ScanLine } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Alert, Badge, Card, Skeleton } from '@/components/ui/surfaces'
import { InfoTooltip } from '@/components/ui/tooltip'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { sessionChat, sessionGraph, sessionIngest, sessionObservation, sessionObservations, sessionQuery } from '@/services/api'
import { SAMPLE_ID, sampleGraph, samplePrompts, sampleResult } from '@/sample-incident'
import InvestigationGraph from './InvestigationGraph'
import IncidentSources from './IncidentSources'
import IncidentChat from './IncidentChat'
import TelemetryTimeline from './TelemetryTimeline'
import TimelineFilters from './TimelineFilters'

const emptyGraph = { nodes: [], edges: [] }
const emptyTimeline = { observations: [], next_offset: null, counts: { all: 0, anomalies: 0, changes: 0, errors: 0 }, services: [] }
const allEvents = { category: 'all', service: '' }
const tabs = [
  { value: 'evidence', label: 'Evidence', icon: FileText },
  { value: 'timeline', label: 'Timeline', icon: Clock3 },
  { value: 'graph', label: 'Graph', icon: GitFork },
  { value: 'trace', label: 'Agent trace', icon: ListChecks },
  { value: 'chat', label: 'Chat', icon: MessagesSquare },
]
const modalities = { log: 'Log', metrics: 'Metrics', image: 'Screenshot' }

function Timeline({ events, selectedIndex, navigable = false }) {
  if (!events.length) return <p className="panel-empty">Timeline events appear after an investigation.</p>
  return (
    <ol className="timeline">
      {events.map((event, index) => {
        const match = String(event).match(/^(\d{2}:\d{2}(?::\d{2})?(?:\s*UTC)?)\s*(?:[·–—:\-]\s*)?(.+)$/)
        return (
          <li key={index} id={navigable ? `timeline-row-${index}` : undefined} tabIndex={navigable ? -1 : undefined} className={navigable && index === selectedIndex ? "reference-target" : undefined}>
            {match && <span className="timeline-time">{match[1]}</span>}
            <p>{match ? match[2] : event}</p>
          </li>
        )
      })}
    </ol>
  )
}

function Evidence({ items, sample, selectedIndex }) {
  if (!items.length) return <p className="panel-empty">No evidence was returned for this incident.</p>
  return (
    <div className="evidence-list">
      {items.map((item, index) => (
        <Card key={index} id={`evidence-row-${index}`} tabIndex={-1} className={`evidence-card${selectedIndex === index ? " evidence-selected" : ""}`}>
          <div className="evidence-head">
            <Badge tone={modalities[item.type] ? item.type : 'neutral'}>{modalities[item.type] || item.type || 'Source'}</Badge>
            <span className="source-name">{item.source}</span>
            {Number.isFinite(item.score) && (
              <InfoTooltip className="evidence-score" label={<><span className="info-label">Relevance {item.score.toFixed(2)}</span><Info size={12} aria-hidden="true" /></>}>
                {sample ? 'Example retrieval score for this preloaded excerpt.' : 'Retrieval score for how closely this excerpt matches the query.'} Causal confidence is shown separately.
              </InfoTooltip>
            )}
          </div>
          {item.snippet && <pre>{item.snippet}</pre>}
        </Card>
      ))}
    </div>
  )
}

export default function Investigation({ incident, connected, onSessionsChange, onDelete }) {
  const sample = incident.id === SAMPLE_ID
  const [tab, setTab] = useState('evidence')
  const [reference, setReference] = useState(null)
  const [timelinePage, setTimelinePage] = useState(emptyTimeline)
  const [filters, setFilters] = useState(allEvents)
  const [timelineBusy, setTimelineBusy] = useState(false)
  const { observations, next_offset: nextOffset, counts, services: timelineServices } = timelinePage
  const timelineRequest = useRef(0)
  const [messages, setMessages] = useState([])
  const [result, setResult] = useState(sample ? sampleResult : null)
  const [graph, setGraph] = useState(sample ? sampleGraph : emptyGraph)
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(!sample)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const id = incident.id
  const previewTimer = useRef(null)
  const referenceRequest = useRef(0)

  useEffect(() => () => clearTimeout(previewTimer.current), [])

  useEffect(() => {
    if (sample) return
    let current = true
    Promise.allSettled([sessionChat(id), sessionGraph(id), sessionObservations(id)]).then(([chat, graphResponse, telemetry]) => {
      if (!current) return
      if (chat.status === 'fulfilled') {
        const history = chat.value.history || []
        setMessages(history)
        setResult([...history].reverse().find(message => message.role === 'assistant')?.content || null)
      }
      if (graphResponse.status === 'fulfilled') setGraph(graphResponse.value)
      if (telemetry.status === 'fulfilled') {
        setTimelinePage(telemetry.value)
      }
      if ([chat, graphResponse, telemetry].some(response => response.status === 'rejected')) {
        setError('Some incident data could not be loaded.')
      }
      setLoading(false)
    })
    return () => { current = false }
  }, [id, sample])

  async function upload(files) {
    const response = await sessionIngest(id, files)
    if (response.files_processed) {
      const updates = await Promise.allSettled([
        sessionGraph(id).then(setGraph),
        refreshTimeline(allEvents).then(page => {
          if (page?.observations.length) setTab('timeline')
        }),
        onSessionsChange(),
      ])
      if (updates.some(update => update.status === 'rejected')) {
        setError('Upload saved. Some incident data could not be refreshed.')
      }
    }
    return response
  }

  async function refreshTimeline(nextFilters, offset = 0) {
    const request = ++timelineRequest.current
    setTimelineBusy(true)
    try {
      const page = await sessionObservations(id, { ...nextFilters, offset })
      if (request !== timelineRequest.current) return
      setFilters(nextFilters)
      setTimelinePage(previous => ({
        ...page,
        observations: offset
          ? [...new Map([...previous.observations, ...page.observations].map(event => [event.id, event])).values()]
          : page.observations,
      }))
      return page
    } finally {
      if (request === timelineRequest.current) setTimelineBusy(false)
    }
  }

  function changeTimelineFilters(nextFilters, offset) {
    setError('')
    refreshTimeline(nextFilters, offset).catch(() => setError('Could not refresh the telemetry timeline.'))
  }

  useEffect(() => {
    if (!reference || tab !== reference.view) return
    const targetId = reference.observation
      ? `observation-${reference.observation.id}`
      : reference.view === 'graph' ? 'graph-reference' : `${reference.view}-row-${reference.index}`
    const target = document.getElementById(targetId)
    target?.focus({ preventScroll: true })
    target?.scrollIntoView({ block: 'nearest' })
  }, [tab, reference])

  function openReference(target) {
    setReference(target)
    setTab(target.view)
  }

  async function openObservation(observationId, node) {
    const request = ++referenceRequest.current
    setError('')
    try {
      const observation = await sessionObservation(id, observationId)
      if (request === referenceRequest.current) openReference({ view: 'timeline', observation, node })
    } catch {
      if (request === referenceRequest.current) setError('Could not open this observation.')
    }
  }

  function changeTab(view) {
    ++referenceRequest.current
    setReference(null)
    setTab(view)
  }

  function resetSampleChat() {
    clearTimeout(previewTimer.current)
    setMessages([])
    setQuestion('')
    setRunning(false)
  }

  async function ask(input) {
    const query = input.trim()
    if (!query || running || loading || (!sample && !connected)) return
    setRunning(true)
    setError('')
    setMessages(history => [...history, { role: 'user', content: query }])
    setQuestion('')
    if (sample) {
      const prompt = samplePrompts.find(item => item.label.toLowerCase() === query.toLowerCase())
      previewTimer.current = setTimeout(() => {
        setMessages(history => [...history, { role: 'assistant', content: prompt ? { answer: prompt.reply, references: prompt.references } : { answer: 'This preview has prepared replies for the cause, impact, next checks, and handoff. Try one of those questions.' } }])
        setRunning(false)
      }, 450)
      return
    }
    try {
      const response = await sessionQuery(id, query)
      setResult(response)
      setMessages(history => [...history, { role: 'assistant', content: response }])
    } catch {
      setError('The query failed. You can retry when the backend is available.')
      setQuestion(value => value || query)
      setMessages(history => history.slice(0, -1))
    } finally {
      setRunning(false)
    }
  }

  const referencedObservation = reference?.observation
  const outsidePage = referencedObservation && !observations.some(event => event.id === referencedObservation.id)
  const evidence = result?.evidence || []
  const timeline = result?.timeline || []
  const services = result?.affected_services || []
  const trace = result?.agent_trace || []

  return (
    <>
      <header className="incident-heading">
        <div>
          <div className="incident-context"><span>Incidents</span><span aria-hidden="true">/</span><span>{sample ? 'Sample incident' : 'Investigation'}</span></div>
          <h1>{incident.name}</h1>
          {sample && <p className="incident-date">15 Jan 2024 <span>·</span> 02:27–02:33 UTC</p>}
        </div>
        {sample ? <Badge tone="warning"><ScanLine size={13} /> Sample data</Badge> : (
          <div className="heading-actions">
            <Button variant="outline" onClick={() => setTab('chat')}><MessagesSquare /> Ask a question</Button>
            <Button variant="ghost" disabled={!connected} onClick={onDelete}>Delete</Button>
          </div>
        )}
      </header>
      {error && <Alert className="mb-4">{error}</Alert>}
      <div className="investigation-grid">
        <div className="investigation-main">
          <Card className="diagnosis">
            <div className="diagnosis-top">
              <h2><ScanLine size={17} /> Root cause analysis</h2>
              {result?.root_cause && result?.confidence && (
                <InfoTooltip label={<Badge tone={result.confidence === 'high' ? 'positive' : 'neutral'}><span className="info-label">{result.confidence} confidence</span><Info size={12} aria-hidden="true" /></Badge>}>
                  {sample ? 'Example confidence label for this sample diagnosis.' : 'Qualitative confidence reported by the analysis. Treat it as an estimate when reviewing the evidence.'}
                </InfoTooltip>
              )}
            </div>
            {loading ? <div role="status" aria-label="Loading incident"><Skeleton className="mb-3 h-6 w-2/3" /><Skeleton className="w-3/4" /></div> : (
              <>
                <h3>{result?.root_cause || 'No diagnosis yet'}</h3>
                <p>{result?.summary || result?.answer || 'Upload incident telemetry, then ask a question to start the analysis.'}</p>
              </>
            )}
            {running && !sample && <div className="mt-3 text-xs text-primary" role="status">Analyzing incident evidence…</div>}
          </Card>
          <Tabs value={tab} onValueChange={changeTab}>
            <TabsList aria-label="Investigation views">
              {tabs.map(({ value, label, icon: Icon }) => (
                <TabsTrigger key={value} value={value}>
                  <Icon aria-hidden="true" /> {label}
                  {value === 'evidence' && evidence.length > 0 && <span className="tab-count">{evidence.length}</span>}
                </TabsTrigger>
              ))}
            </TabsList>
            {reference && !reference.fromTimeline && tab === reference.view && (
              <Button className="mt-3" variant="outline" size="sm" onClick={() => referencedObservation ? openReference({ view: 'graph', node: reference.node, fromTimeline: true }) : changeTab('chat')}>
                {referencedObservation ? <><GitFork /> Back to graph</> : <><MessagesSquare /> Back to chat</>}
              </Button>
            )}
            <TabsContent value="evidence"><Evidence items={evidence} sample={sample} selectedIndex={reference?.view === 'evidence' ? reference.index : null} /></TabsContent>
            <TabsContent value="timeline">
              {outsidePage && (
                <Card className="side-panel mb-3">
                  <div className="side-panel-title"><h2>Referenced observation</h2></div>
                  <TelemetryTimeline observations={[referencedObservation]} selectedId={referencedObservation.id} />
                </Card>
              )}
              {(counts.all > 0 || timelineServices.length > 0) && (
                <Card className="side-panel" aria-busy={timelineBusy}>
                  <div className="side-panel-title">
                    <h2>Uploaded telemetry</h2><span>{observations.length} of {counts[filters.category]} shown</span>
                  </div>
                  <TimelineFilters counts={counts} services={timelineServices} filters={filters} disabled={timelineBusy} onChange={changeTimelineFilters} />
                  {observations.length ? <TelemetryTimeline observations={observations} selectedId={referencedObservation?.id} /> : (
                    <p className="panel-empty" role="status">No events match these filters.</p>
                  )}
                  {nextOffset !== null && <Button className="mt-3" variant="outline" disabled={timelineBusy} onClick={() => changeTimelineFilters(filters, nextOffset)}>Load more</Button>}
                </Card>
              )}
              {(sample || timeline.length > 0) && (
                <Card className={`side-panel${counts.all > 0 ? ' mt-3' : ''}`}>
                  {!sample && <div className="side-panel-title"><h2>Analysis timeline</h2></div>}
                  <Timeline events={timeline} navigable selectedIndex={reference?.view === 'timeline' ? reference.index : null} />
                </Card>
              )}
              {!sample && !counts.all && !timelineServices.length && !timeline.length && <p className="panel-empty">Upload logs or metrics to see their events here.</p>}
            </TabsContent>
            <TabsContent value="graph"><InvestigationGraph graph={graph} referenceNode={reference?.view === 'graph' ? reference.node : null} sessionId={sample ? null : id} onObservation={openObservation} /></TabsContent>
            <TabsContent value="trace">
              {trace.length ? <ol className="trace-list">{trace.map((step, index) => (
                <li key={index}><Card className="side-panel"><Badge>{step.agent}</Badge><h3>{step.action}</h3><p>{typeof step.result === 'string' ? step.result : JSON.stringify(step.result)}</p></Card></li>
              ))}</ol> : <p className="panel-empty">Run an analysis to view its agent trace.</p>}
            </TabsContent>
            <TabsContent value="chat">
              <IncidentChat
                sample={sample}
                messages={messages}
                running={running}
                disabled={loading || (!sample && !connected)}
                question={question}
                onQuestionChange={setQuestion}
                onSend={ask}
                onReference={openReference}
                onReset={resetSampleChat}
              />
            </TabsContent>
          </Tabs>
        </div>
        <aside className="investigation-aside" aria-label="Incident context">
          <Card className="side-panel timeline-preview">
            <div className="side-panel-title">
              <h2>{observations.length ? "Uploaded telemetry" : "Event timeline"}</h2>
              {(timeline.length > 0 || observations.length > 0) && <Button className="size-6" variant="ghost" size="icon" onClick={() => setTab('timeline')} aria-label="Open full timeline"><ArrowUpRight /></Button>}
            </div>
            {observations.length ? <TelemetryTimeline observations={observations.slice(0, 4)} preview /> : <Timeline events={timeline.slice(0, 4)} />}
          </Card>
          {services.length > 0 && (
            <Card className="side-panel">
              <div className="side-panel-title"><h2>Affected services</h2><span>{services.length}</span></div>
              <ul className="service-list">{services.map(service => <li key={service} className="service-chip">{service}</li>)}</ul>
            </Card>
          )}
          <IncidentSources files={incident.files || []} sample={sample} disabled={!connected || loading || running} onUpload={upload} />
          {!!result?.recommendations?.length && (
            <Card className="side-panel"><div className="side-panel-title"><h2>Recommended actions</h2></div><ul className="actions-list">{result.recommendations.map((item, index) => <li key={index}>{item}</li>)}</ul></Card>
          )}
        </aside>
      </div>
    </>
  )
}
