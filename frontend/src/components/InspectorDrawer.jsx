import { lazy, Suspense, useState } from 'react'
import { Badge as ChakraBadge, Progress } from '@chakra-ui/react'
import {
  IconArrowLeft,
  IconClock,
  IconFileText,
  IconGitFork,
  IconListCheck,
  IconNetwork,
  IconShieldCheck,
  IconTool,
} from '@tabler/icons-react'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent } from '@/components/ui/sheet'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Card, Skeleton } from '@/components/ui/surfaces'
import { suggestRemediation } from '@/services/api'
import HypothesisList from './HypothesisList'
import InvestigationTrace from './InvestigationTrace'
import TelemetryTimeline from './TelemetryTimeline'
import TimelineFilters from './TimelineFilters'

const InvestigationGraph = lazy(() => import('./InvestigationGraph'))

export default function InspectorDrawer({
  open,
  onOpenChange,
  activeTab,
  onTabChange,
  incident,
  session,
}) {
  const [remediation, setRemediation] = useState(null)
  const [remediating, setRemediating] = useState(false)
  const {
    sourceSheet,
    setSourceSheet,
    openCitation,
    graph,
    timeline,
    timelineFilter,
    setTimelineFilter,
    loadTimeline,
    result,
    causalCandidates,
    files,
    sample,
  } = session

  async function runRemediation(service) {
    if (sample || !service) return
    setRemediating(true)
    try {
      const data = await suggestRemediation(incident.id, service)
      setRemediation(data)
    } catch {
      setRemediation(null)
    } finally {
      setRemediating(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        title="Investigation Inspector"
        subtitle={`${incident.name} · ${files.length} files`}
      >
        <Tabs value={activeTab} onValueChange={onTabChange}>
          <TabsList className="mb-4 flex w-full flex-wrap gap-1 bg-[var(--bg-elevated)] p-1">
            <TabsTrigger value="source" className="gap-1 text-xs"><IconFileText className="size-3.5" stroke={1.75} />Sources</TabsTrigger>
            <TabsTrigger value="graph" className="gap-1 text-xs"><IconGitFork className="size-3.5" stroke={1.75} />Graph</TabsTrigger>
            <TabsTrigger value="timeline" className="gap-1 text-xs"><IconClock className="size-3.5" stroke={1.75} />Timeline</TabsTrigger>
            <TabsTrigger value="hypotheses" className="gap-1 text-xs"><IconShieldCheck className="size-3.5" stroke={1.75} />Checks</TabsTrigger>
            <TabsTrigger value="causal" className="gap-1 text-xs"><IconNetwork className="size-3.5" stroke={1.75} />Ranking</TabsTrigger>
            <TabsTrigger value="trace" className="gap-1 text-xs"><IconListCheck className="size-3.5" stroke={1.75} />Trace</TabsTrigger>
          </TabsList>

          {/* Sources & Evidence Tab */}
          <TabsContent value="source" className="space-y-4">
            {sourceSheet.loading && <Skeleton className="h-32 w-full" />}
            {sourceSheet.error && <p className="text-xs text-rose-400">{sourceSheet.error}</p>}
            {sourceSheet.observation && (
              <Card className="space-y-3 border-sky-500/30 p-3.5">
                <div className="flex items-center justify-between gap-2">
                  <ChakraBadge size="sm" colorPalette="blue">{sourceSheet.observation.modality}</ChakraBadge>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setSourceSheet((prev) => ({ ...prev, observation: null, citation: null }))}
                    className="h-6 gap-1 px-2 text-[11px]"
                  >
                    <IconArrowLeft className="size-3" /> All sources
                  </Button>
                </div>
                <div className="font-mono text-[11px] text-muted-foreground">
                  {sourceSheet.observation.service || 'unknown'} · {sourceSheet.observation.source_file}
                </div>
                {sourceSheet.observation.timestamp && (
                  <div className="font-mono text-[11px] text-muted-foreground">
                    {sourceSheet.observation.timestamp}
                  </div>
                )}
                <p className="text-sm font-medium">{sourceSheet.observation.summary}</p>
                {sourceSheet.observation.excerpt && (
                  <pre className="max-h-64 overflow-auto rounded-lg bg-[var(--bg-canvas)] p-2.5 font-mono text-xs leading-relaxed">
                    {sourceSheet.observation.excerpt}
                  </pre>
                )}
              </Card>
            )}

            {/* Uploaded Files */}
            <div className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Uploaded Files ({files.length})
              </h3>
              {files.length === 0 ? (
                <p className="panel-empty">No telemetry files uploaded yet.</p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {files.map((file) => (
                    <span
                      key={file}
                      className="inline-flex items-center gap-1.5 rounded-md border border-border bg-[var(--bg-surface)] px-2.5 py-1 font-mono text-[11px]"
                    >
                      <IconFileText className="size-3 text-sky-400" />
                      {file}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Verified Evidence Records */}
            <div className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Verified Evidence ({result?.evidence?.length || 0})
              </h3>
              {!result?.evidence?.length ? (
                <p className="panel-empty">Run Verify to extract linked evidence records.</p>
              ) : (
                <div className="space-y-2">
                  {result.evidence.map((ev, idx) => (
                    <Card key={ev.observation_id || idx} className="space-y-1.5 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <ChakraBadge size="sm" colorPalette="purple">{ev.type}</ChakraBadge>
                        <span className="font-mono text-[11px] text-muted-foreground">
                          {ev.source}{ev.source_position ? ` · #${ev.source_position}` : ''}
                        </span>
                      </div>
                      {ev.why && <p className="text-xs text-muted-foreground">{ev.why}</p>}
                      <pre className="whitespace-pre-wrap rounded bg-[var(--bg-canvas)] p-2 font-mono text-[11px]">
                        {ev.snippet}
                      </pre>
                      {ev.observation_id && (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-6 px-2 text-[11px] text-sky-400"
                          onClick={() => openCitation({ observation_id: ev.observation_id, label: ev.source })}
                        >
                          Inspect full record
                        </Button>
                      )}
                    </Card>
                  ))}
                </div>
              )}
            </div>
          </TabsContent>

          {/* Interactive Knowledge Graph Tab */}
          <TabsContent value="graph">
            <Suspense fallback={<Skeleton className="h-64 w-full" />}>
              <InvestigationGraph
                graph={graph}
                incidentId={incident.id}
                sample={sample}
                onFilterService={(svc) => {
                  const next = { ...timelineFilter, service: svc }
                  setTimelineFilter(next)
                  loadTimeline(next, 0, false)
                  onTabChange('timeline')
                }}
              />
            </Suspense>
          </TabsContent>

          {/* Timeline Tab */}
          <TabsContent value="timeline" className="space-y-3">
            <TimelineFilters
              filters={timelineFilter}
              services={timeline.services || []}
              counts={timeline.counts || { all: 0, anomalies: 0, changes: 0, errors: 0 }}
              disabled={session.timelineLoading || sample}
              onChange={(next) => {
                setTimelineFilter(next)
                loadTimeline(next, 0, false)
              }}
            />
            {session.timelineError && (
              <p className="text-xs text-rose-400">{session.timelineError}</p>
            )}
            {timeline.observations?.length ? (
              <>
                <TelemetryTimeline observations={timeline.observations} />
                {timeline.next_offset !== null && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={session.timelineLoading}
                    onClick={() => loadTimeline(timelineFilter, timeline.next_offset, true)}
                    className="w-full text-xs"
                  >
                    {session.timelineLoading ? 'Loading…' : 'Load more'}
                  </Button>
                )}
              </>
            ) : (
              <p className="panel-empty">
                {session.timelineLoading ? 'Loading timeline…' : 'No timeline observations match this filter.'}
              </p>
            )}
          </TabsContent>

          {/* Hypotheses & 4-Point Verification Checks */}
          <TabsContent value="hypotheses" className="space-y-3">
            {result?.hypotheses?.length ? (
              <HypothesisList
                hypotheses={result.hypotheses}
                onObservation={(obsId) => {
                  openCitation({ observation_id: obsId, label: 'Check source' })
                  onTabChange('source')
                }}
              />
            ) : (
              <div className="space-y-3 text-center">
                <p className="panel-empty">No verification checks run yet for this incident.</p>
                {!sample && files.length > 0 && (
                  <Button size="sm" onClick={session.runVerification} disabled={session.verifying}>
                    {session.verifying ? 'Verifying…' : 'Run verification now'}
                  </Button>
                )}
              </div>
            )}
          </TabsContent>

          {/* Unsupervised Causal Ranking + Counterfactual Remediation */}
          <TabsContent value="causal" className="space-y-3">
            {causalCandidates.length === 0 ? (
              <p className="panel-empty">Upload telemetry to rank candidate root-cause services automatically.</p>
            ) : (
              causalCandidates.map((cand, index) => (
                <Card key={cand.service} className="space-y-2.5 p-3.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-muted-foreground">#{index + 1}</span>
                      <span className="text-sm font-semibold">{cand.service}</span>
                    </div>
                    <ChakraBadge size="sm" colorPalette={index === 0 ? 'green' : 'gray'}>
                      Score {Math.round(cand.score * 100)}%
                    </ChakraBadge>
                  </div>
                  <Progress.Root value={Math.round(cand.score * 100)} size="xs" colorPalette={index === 0 ? 'green' : 'blue'}>
                    <Progress.Track>
                      <Progress.Range />
                    </Progress.Track>
                  </Progress.Root>
                  <div className="grid grid-cols-3 gap-2 font-mono text-[11px] text-muted-foreground">
                    <span>Temporal: {cand.temporal_score.toFixed(2)}</span>
                    <span>Upstream: {cand.upstream_score.toFixed(2)}</span>
                    <span>Modal: {cand.modal_score.toFixed(2)}</span>
                  </div>
                  {cand.downstream_affected?.length > 0 && (
                    <div className="text-xs text-muted-foreground">
                      Blast radius: <span className="text-foreground">{cand.downstream_affected.join(', ')}</span>
                    </div>
                  )}
                  {!sample && index === 0 && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => runRemediation(cand.service)}
                      disabled={remediating}
                      className="h-7 gap-1.5 text-xs"
                    >
                      <IconTool className="size-3" />
                      {remediating ? 'Simulating…' : 'Simulate counterfactual fix'}
                    </Button>
                  )}
                </Card>
              ))
            )}

            {remediation && (
              <Card className="space-y-2.5 border-emerald-500/30 bg-emerald-500/5 p-3.5 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold text-emerald-400">
                    Simulated Remediation ({remediation.causal_node_id})
                  </span>
                  <ChakraBadge size="sm" colorPalette="green">
                    Symptoms: {remediation.symptom_before ?? 0} → {remediation.symptom_after ?? 0}
                  </ChakraBadge>
                </div>
                {!remediation.actions?.length ? (
                  <p className="text-muted-foreground">No simulated actions returned for this node.</p>
                ) : (
                  <div className="space-y-2">
                    {remediation.actions.map((act, i) => (
                      <div key={act.id || i} className="space-y-1 rounded-md border border-border bg-[var(--bg-surface)] p-2.5">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <ChakraBadge size="sm" colorPalette="blue">{act.action_type}</ChakraBadge>
                          {act.reversible && <ChakraBadge size="sm" colorPalette="green">Reversible</ChakraBadge>}
                          <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                            {Math.round((act.confidence || 0) * 100)}% impact
                          </span>
                        </div>
                        <p className="font-medium text-foreground">{act.description}</p>
                        {act.expected_signal_change && (
                          <p className="text-[11px] text-emerald-400">Expected: {act.expected_signal_change}</p>
                        )}
                        {act.rollback_plan && (
                          <p className="text-[11px] text-muted-foreground">Rollback: {act.rollback_plan}</p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            )}
          </TabsContent>

          {/* Agent Trace */}
          <TabsContent value="trace">
            <InvestigationTrace
              trace={result?.agent_trace || []}
              answerMode={result?.answer_mode}
              sample={sample}
              hasEvidence={Boolean(result?.evidence?.length)}
              onNavigate={(tab) => {
                if (tab === 'chat') onOpenChange(false)
                else onTabChange(tab === 'evidence' ? 'source' : tab)
              }}
            />
          </TabsContent>
        </Tabs>
      </SheetContent>
    </Sheet>
  )
}
