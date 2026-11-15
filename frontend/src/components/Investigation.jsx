import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Clock3, FileText, GitFork, Info, ListChecks, MessagesSquare, ScanLine } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Alert, Badge, Card, Skeleton } from '@/components/ui/surfaces'
import { InfoTooltip } from '@/components/ui/tooltip'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { sessionChat, latestVerification, sessionGraph, sessionIngest, sessionObservation, sessionObservations, sessionOverview, sessionQuery, verifyIncident } from '@/services/api'
import { SAMPLE_ID, sampleGraph, sampleOverview, samplePrompts, sampleResult } from '@/sample-incident'
import IncidentSources from './IncidentSources'
import IncidentChat from './IncidentChat'
import TelemetryTimeline from './TelemetryTimeline'
import TimelineFilters from './TimelineFilters'
import HypothesisList from './HypothesisList'
import IncidentOverview, { utcRange } from './IncidentOverview'
import ChatSourceSheet from './ChatSourceSheet'
import InvestigationTrace from './InvestigationTrace'

const InvestigationGraph = lazy(() => import('./InvestigationGraph'))
const emptyGraph = { nodes: [], edges: [] }
const emptyTimeline = { observations: [], next_offset: null, counts: { all: 0, anomalies: 0, changes: 0, errors: 0 }, services: [] }
const allEvents = { category: 'all', service: '', start: '', end: '' }
const tabs = [
  { value: 'evidence', label: 'Evidence', icon: FileText },
  { value: 'timeline', label: 'Timeline', icon: Clock3 },
  { value: 'graph', label: 'Graph', icon: GitFork },
  { value: 'trace', label: 'Agent trace', icon: ListChecks },
  { value: 'chat', label: 'Chat', icon: MessagesSquare },
]
const modalities = { change: 'Change', log: 'Log', metrics: 'Metrics', image: 'Screenshot' }

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

function Evidence({ items, sample, selectedIndex, onObservation }) {
  if (!items.length) return <p className="panel-empty">No evidence was returned for this incident.</p>
  return (
    <ol className={sample ? 'evidence-list' : 'evidence-list evidence-sequence'}>
      {items.map((item, index) => (
        <li className="min-w-0" key={item.observation_id || index}>
          <Card id={`evidence-row-${index}`} tabIndex={-1} className={`evidence-card${selectedIndex === index ? " evidence-selected" : ""}`}>
            <div className="evidence-head">
              <Badge tone={modalities[item.type] ? item.type : 'neutral'}>{modalities[item.type] || item.type || 'Source'}</Badge>
              {item.timestamp && <time dateTime={item.timestamp} className="timeline-time">{item.timestamp.slice(11, 19)} UTC</time>}
              <span className="source-name">{item.source}{item.source_position && ` · ${item.type === 'log' ? 'line' : 'record'} ${item.source_position}`}</span>
              {!item.observation_id && Number.isFinite(item.score) && (
                <InfoTooltip className="evidence-score" label={<><span className="info-label">Relevance {item.score.toFixed(2)}</span><Info size={12} aria-hidden="true" /></>}>
                  {sample ? 'Example retrieval score for this preloaded excerpt.' : 'Retrieval score for how closely this excerpt matches the query.'} Causal confidence is shown separately.
                </InfoTooltip>
              )}
            </div>
            {item.why && <p className="mt-2 text-sm">{item.why}</p>}
            {item.snippet && (item.observation_id
              ? <details className="mt-2"><summary className="text-xs text-muted-foreground">Source excerpt</summary><pre>{item.snippet}</pre></details>
              : <pre>{item.snippet}</pre>)}
            {item.observation_id && <Button className="mt-2" size="sm" variant="outline" onClick={() => onObservation(item.observation_id, index)}><ArrowUpRight /> Open in timeline</Button>}
          </Card>
        </li>
      ))}
    </ol>
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
  const [overview, setOverview] = useState(sample ? sampleOverview : null)
  const [chatSource, setChatSource] = useState(null)
  const chatTrigger = useRef(null)
  const chatSourceRequest = useRef(0)
  const [verification, setVerification] = useState(null)
  const [verifying, setVerifying] = useState(false)
  const [result, setResult] = useState(sample ? sampleResult : null)
  const [graph, setGraph] = useState(sample ? sampleGraph : emptyGraph)
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(!sample)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const id = incident.id
  const previewTimer = useRef(null)
  const verificationRequest = useRef(0)
  const referenceRequest = useRef(0)

  useEffect(() => () => clearTimeout(previewTimer.current), [])

  useEffect(() => {
    if (sample) return
    let current = true
    const requests = [sessionChat(id), sessionGraph(id), sessionObservations(id), sessionOverview(id), latestVerification(id)]
    Promise.allSettled(requests).then(([chat, graphResponse, telemetry, coverage, saved]) => {
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
      if (coverage.status === 'fulfilled') setOverview(coverage.value)
      if (saved.status === 'fulfilled') setVerification(saved.value)
      if ([chat, graphResponse, telemetry, coverage, saved].some(response => response.status === 'rejected')) {
        setError('Some incident data could not be loaded.')
      }
      setLoading(false)
    })
    return () => { current = false }
  }, [id, sample])

  async function upload(files) {
    const response = await sessionIngest(id, files)
    if (response.files_processed) {
      ++verificationRequest.current
      setVerifying(false)
      const updates = await Promise.allSettled([
        sessionGraph(id).then(setGraph),
        sessionOverview(id).then(setOverview),
        refreshTimeline(allEvents).then(page => {
          if (page?.observations.length && !diagnosis) changeTab('evidence')
        }),
        onSessionsChange(),
      ])
      if (updates.some(update => update.status === 'rejected')) {
        setError('Upload saved. Some incident data could not be refreshed.')
      }
    }
    return response
  }

  async function runVerification() {
    const request = ++verificationRequest.current
    setVerifying(true)
    setError('')
    try {
      const diagnosis = await verifyIncident(id)
      if (request === verificationRequest.current) {
        setVerification(diagnosis)
        changeTab('evidence')
      }
    } catch {
      if (request === verificationRequest.current) setError('Could not verify the incident.')
    } finally {
      if (request === verificationRequest.current) setVerifying(false)
    }
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

  function openTimeRange(start, end, service = '') {
    changeTab('timeline')
    changeTimelineFilters({ ...allEvents, start: start || '', end: end || '', service })
  }

  function showSources() {
    const picker = document.getElementById('telemetry-picker')
    if (!picker || picker.getAttribute('aria-disabled') === 'true') return
    picker.focus({ preventScroll: true })
    picker.scrollIntoView({ block: 'nearest' })
    picker.click()
  }

  async function openChatSource(observationId, trigger) {
    const request = ++chatSourceRequest.current
    chatTrigger.current = trigger
    setChatSource({})
    try {
      const observation = await sessionObservation(id, observationId)
      if (request === chatSourceRequest.current) setChatSource({ observation })
    } catch {
      if (request === chatSourceRequest.current) setChatSource({ error: true })
    }
  }

  function closeChatSource() {
    ++chatSourceRequest.current
    setChatSource(null)
  }

  useEffect(() => {
    if (!reference || tab !== reference.view || reference.view === 'graph') return
    const targetId = reference.observation
      ? `observation-${reference.observation.id}`
      : `${reference.view}-row-${reference.index}`
    const target = document.getElementById(targetId)
    target?.focus({ preventScroll: true })
    target?.scrollIntoView({ block: 'nearest' })
  }, [tab, reference])

  function openReference(target) {
    changeTab(target.view)
    setReference(target)
  }

  async function openObservation(observationId, node, evidenceIndex, fromChat = false) {
    const request = ++referenceRequest.current
    setError('')
    try {
      const observation = await sessionObservation(id, observationId)
      if (request === referenceRequest.current) openReference({ view: 'timeline', observation, node, evidenceIndex, fromChat })
    } catch {
      if (request === referenceRequest.current) setError('Could not open this observation.')
    }
  }

  function changeTab(view) {
    ++referenceRequest.current
    if (view !== 'chat') closeChatSource()
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
    if (!query || running || verifying || loading || (!sample && !connected)) return
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
  const diagnosis = result?.evidence_revision && result.evidence_revision === overview?.revision &&
    verification?.evidence_revision !== overview?.revision ? result : verification || result
  const snapshotChanged = !sample && !!diagnosis?.decision && !!overview &&
    diagnosis.evidence_revision !== overview.revision
  const canVerify = !loading && connected && (overview?.counts.all ?? counts.all) > 0
  const verifiedDiagnosis = diagnosis?.decision ? diagnosis : null
  const evidence = diagnosis?.evidence || []
  const timeline = diagnosis?.timeline || []
  const services = diagnosis?.affected_services || []
  const impactedCallers = services.filter(service => service !== diagnosis?.suspected_origin)
  const traceResult = result?.evidence_revision && result.evidence_revision === diagnosis?.evidence_revision ? result : diagnosis
  const nextCheck = diagnosis?.next_check
  const graphFocus = reference?.view === 'graph' ? reference.node : verifiedDiagnosis?.suspected_origin || services[0]
  const decisions = { supported: 'Supported by evidence', ambiguous: 'Ambiguous', insufficient_evidence: 'Insufficient evidence' }

  function openGraph(node) {
    openReference({ view: 'graph', node, fromDiagnosis: true })
  }

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
            {canVerify || diagnosis ? <Button disabled={!canVerify || verifying || running} onClick={runVerification}><ScanLine /> {verifying ? 'Verifying…' : snapshotChanged ? 'Verify again' : 'Verify root cause'}</Button>
              : <Button disabled={!connected || loading} onClick={showSources}><FileText /> Add telemetry</Button>}
            <Button variant="outline" onClick={() => changeTab('chat')}><MessagesSquare /> Ask a question</Button>
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
              {snapshotChanged && <Badge tone="warning">Verification out of date</Badge>}
              {verifiedDiagnosis && !snapshotChanged && <Badge tone={verifiedDiagnosis.decision === 'supported' ? 'positive' : 'warning'}>{decisions[verifiedDiagnosis.decision]}</Badge>}
              {!verifiedDiagnosis && result?.root_cause && result?.confidence && (
                <InfoTooltip label={<Badge tone={result.confidence === 'high' ? 'positive' : 'neutral'}><span className="info-label">{result.confidence} confidence</span><Info size={12} aria-hidden="true" /></Badge>}>
                  {sample ? 'Example confidence label for this sample diagnosis.' : 'Qualitative confidence reported by the analysis. Treat it as an estimate when reviewing the evidence.'}
                </InfoTooltip>
              )}
            </div>
            {loading ? <div role="status" aria-label="Loading incident"><Skeleton className="mb-3 h-6 w-2/3" /><Skeleton className="w-3/4" /></div> : (
              <>
                <h3>{diagnosis?.root_cause || 'No diagnosis yet'}</h3>
                <p>{diagnosis?.summary || diagnosis?.answer || 'Upload logs and metrics to start. Add configuration changes and service dependencies when available.'}</p>
                {snapshotChanged && <p className="mt-2">The uploaded evidence has changed. Verify again to update this diagnosis.</p>}
                {!snapshotChanged && impactedCallers.length > 0 && (
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <h4 className="text-xs font-semibold text-muted-foreground">Observed impact</h4>
                    <ul className="service-list">
                      {impactedCallers.map(service => <li key={service}><button type="button" className="service-chip" onClick={() => openGraph(service)} aria-label={`Inspect ${service} in graph`}>{service}</button></li>)}
                    </ul>
                  </div>
                )}
                {!snapshotChanged && nextCheck && (
                  <div className="mt-4 rounded-md border border-border bg-background/55 p-3">
                    <h4 className="mb-1 text-xs font-semibold text-primary">Next check</h4>
                    <p>{nextCheck.instruction}</p>
                    {(nextCheck.service || nextCheck.start) && <p className="mt-1 font-mono !text-xs">{[nextCheck.service, utcRange(nextCheck.start, nextCheck.end)].filter(Boolean).join(' · ')}</p>}
                    {nextCheck.start && <Button className="mt-2" variant="outline" size="sm" onClick={() => openTimeRange(nextCheck.start, nextCheck.end, nextCheck.service)}><Clock3 /> Inspect this window</Button>}
                  </div>
                )}
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
            {reference && !reference.fromTimeline && !reference.fromDiagnosis && tab === reference.view && (
              <Button className="mt-3" variant="outline" size="sm" onClick={() => {
                if (reference.fromChat || !referencedObservation) changeTab('chat')
                else if (reference.evidenceIndex === null) changeTab('evidence')
                else if (reference.evidenceIndex !== undefined) openReference({ view: 'evidence', index: reference.evidenceIndex, fromDiagnosis: true })
                else openReference({ view: 'graph', node: reference.node, fromTimeline: true })
              }}>
                {reference.evidenceIndex !== undefined ? <><FileText /> Back to evidence</> : referencedObservation && !reference.fromChat ? <><GitFork /> Back to graph</> : <><MessagesSquare /> Back to chat</>}
              </Button>
            )}
            <TabsContent value="evidence">
              {(evidence.length > 0 || verifiedDiagnosis) && (
                <div className="mb-4">
                  <h2 className="mb-2 text-sm font-semibold">Evidence sequence</h2>
                  <Evidence items={evidence} sample={sample} selectedIndex={reference?.view === 'evidence' ? reference.index : null} onObservation={(observationId, index) => openObservation(observationId, undefined, index)} />
                </div>
              )}
              {verifiedDiagnosis?.hypotheses.length > 0 && (
                <HypothesisList hypotheses={verifiedDiagnosis?.hypotheses} onObservation={observationId => openObservation(observationId, undefined, null)} />
              )}
              <IncidentOverview overview={overview} sample={sample} onRange={openTimeRange} />
            </TabsContent>
            <TabsContent value="timeline">
              {outsidePage && (
                <Card className="side-panel mb-3">
                  <div className="side-panel-title"><h2>Referenced observation</h2></div>
                  <TelemetryTimeline observations={[referencedObservation]} selectedId={referencedObservation.id} />
                </Card>
              )}
              {(overview?.counts.all > 0 || counts.all > 0 || timelineServices.length > 0 || filters.start || filters.end) && (
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
              {!sample && !overview?.counts.all && !counts.all && !timelineServices.length && !timeline.length && !filters.start && !filters.end && <p className="panel-empty">Upload logs or metrics to see their events here.</p>}
            </TabsContent>
            <TabsContent value="graph">
              <Suspense fallback={<Skeleton className="h-72" />}>
                <InvestigationGraph
                  key={graphFocus} graph={graph} referenceNode={graphFocus} focusSelection={reference?.view === 'graph'}
                  sessionId={sample ? null : id} evidenceRevision={overview?.revision} onObservation={openObservation}
                  suspectedOrigin={snapshotChanged ? null : diagnosis?.suspected_origin}
                  affectedServices={snapshotChanged ? [] : services}
                />
              </Suspense>
            </TabsContent>
            <TabsContent value="trace">
              <InvestigationTrace
                trace={traceResult?.agent_trace || []} answerMode={traceResult?.answer_mode} sample={sample}
                stale={snapshotChanged} hasEvidence={evidence.length > 0 || !!verifiedDiagnosis?.hypotheses.length}
                onNavigate={changeTab}
              />
            </TabsContent>
            <TabsContent value="chat">
              <IncidentChat
                sample={sample}
                hasTelemetry={canVerify}
                messages={messages}
                running={running}
                disabled={loading || verifying || (!sample && !canVerify)}
                question={question}
                onQuestionChange={setQuestion}
                onSend={ask}
                onReference={openReference}
                onObservation={openChatSource}
                onReset={resetSampleChat}
              />
            </TabsContent>
          </Tabs>
        </div>
        <aside className="investigation-aside" aria-label="Incident context" data-source-open={tab === 'chat' && !!chatSource}>
          {!sample && tab === 'chat' && <ChatSourceSheet source={chatSource} onClose={closeChatSource} trigger={chatTrigger.current} onTimeline={observation => {
            openReference({ view: 'timeline', observation, fromChat: true })
          }} />}
          <IncidentSources files={incident.files || []} sample={sample} disabled={!connected || loading || running || verifying} onUpload={upload} />
          {!sample && overview?.services.length > 0 && (
            <Card className="side-panel">
              <div className="side-panel-title"><h2>Services in telemetry</h2><span>{overview.services.length}</span></div>
              <ul className="service-list">
                {overview.services.map(service => <li key={service}><button type="button" className="service-chip" onClick={() => openTimeRange('', '', service)}>{service}</button></li>)}
              </ul>
            </Card>
          )}
          {!snapshotChanged && !!diagnosis?.recommendations?.length && (
            <Card className="side-panel"><div className="side-panel-title"><h2>Recommended actions</h2></div><ul className="actions-list">{diagnosis.recommendations.map((item, index) => <li key={index}>{item}</li>)}</ul></Card>
          )}
        </aside>
      </div>
    </>
  )
}
