import { useEffect, useRef, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { Badge as ChakraBadge, HStack, Spinner } from '@chakra-ui/react'
import {
  IconActivity as Activity,
  IconAlertTriangle as AlertTriangle,
  IconArrowUp as ArrowUp,
  IconCheck as Check,
  IconCopy as Copy,
  IconGitFork as GitFork,
  IconPaperclip as Paperclip,
  IconRotate as RotateCcw,
  IconShieldExclamation as ShieldAlert,
  IconShieldCheck as ShieldCheck,
  IconAdjustmentsHorizontal as SlidersHorizontal,
  IconSparkles as Sparkles,
  IconUpload as Upload,
} from '@tabler/icons-react'
import { Button } from '@/components/ui/button'
import { Popover } from '@/components/ui/popover'
import { Alert } from '@/components/ui/surfaces'
import { useIncidentSession } from '@/hooks/useIncidentSession'
import { samplePrompts } from '@/sample-incident'
import InspectorDrawer from './InspectorDrawer'

const verdictStyles = {
  supported: { label: 'Supported', colorPalette: 'green' },
  ambiguous: { label: 'Ambiguous', colorPalette: 'yellow' },
  insufficient_evidence: { label: 'Insufficient evidence', colorPalette: 'orange' },
}

const modeLabels = {
  extractive: 'Source-backed',
  generated: 'Model + Verified',
  local: 'Local Synthesis',
}

function AssistantMessage({ content, onOpenCitation }) {
  const [copied, setCopied] = useState(false)
  const claims = Array.isArray(content?.claims) && content.claims.length
    ? content.claims
    : content?.answer
      ? [{ text: content.answer, citations: [], audit_status: 'verified', audit_warnings: [] }]
      : []

  const allCitations = []
  const seenIds = new Set()
  for (const claim of claims) {
    for (const cit of claim.citations || []) {
      if (!seenIds.has(cit.observation_id)) {
        seenIds.add(cit.observation_id)
        allCitations.push(cit)
      }
    }
  }
  const indexById = new Map(allCitations.map((c, i) => [c.observation_id, i + 1]))

  const confPct = Number.isFinite(content?.confidence_score) && content.confidence_score > 0
    ? Math.round(content.confidence_score * 100)
    : null
  const auditScore = content?.audit_score
  const flaggedWarnings = claims.flatMap((c) => c.audit_warnings || [])

  async function handleCopy() {
    const text = claims.map((c) => c.text).join('\n\n')
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      // ignore clipboard restrictions
    }
  }

  return (
    <div className="group flex gap-3.5 py-2">
      <div className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full border border-border bg-[var(--bg-elevated)] text-emerald-400">
        <Activity className="size-3.5" />
      </div>
      <div className="min-w-0 flex-1 space-y-3">
        <div className="space-y-2.5 text-[15px] leading-7 text-[var(--fg-primary)]">
          {claims.map((claim, idx) => (
            <p key={idx} className="whitespace-pre-wrap break-words">
              {claim.text}
              {(claim.citations || []).length > 0 && (
                <span className="ml-1.5 inline-flex flex-wrap gap-1 align-baseline">
                  {claim.citations.map((cit) => (
                    <button
                      key={cit.observation_id}
                      type="button"
                      onClick={() => onOpenCitation(cit)}
                      title={cit.label}
                      className="inline-flex h-5 items-center rounded-md border border-border bg-[var(--bg-elevated)] px-1.5 font-mono text-[11px] font-medium text-sky-400 transition-colors hover:border-sky-400/50 hover:bg-sky-500/10"
                    >
                      [{indexById.get(cit.observation_id)}]
                    </button>
                  ))}
                </span>
              )}
            </p>
          ))}
        </div>

        {flaggedWarnings.length > 0 && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
            <div className="mb-1 flex items-center gap-1.5 font-medium">
              <ShieldAlert className="size-3.5" />
              Claim Auditor flagged {flaggedWarnings.length} potential mismatch{flaggedWarnings.length > 1 ? 'es' : ''}:
            </div>
            <ul className="list-disc space-y-0.5 pl-4 text-[11px] opacity-90">
              {flaggedWarnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-0.5 text-xs text-muted-foreground">
          {confPct !== null && (
            <ChakraBadge
              size="sm"
              variant="subtle"
              colorPalette={confPct >= 80 ? 'green' : confPct >= 50 ? 'yellow' : 'red'}
            >
              {confPct}% confidence
            </ChakraBadge>
          )}
          {typeof auditScore === 'number' && (
            <ChakraBadge
              size="sm"
              variant="subtle"
              colorPalette={auditScore === 1 ? 'green' : 'yellow'}
            >
              {auditScore === 1 ? (
                <span className="inline-flex items-center gap-1"><ShieldCheck className="size-3" />Audit 100%</span>
              ) : (
                <span className="inline-flex items-center gap-1"><ShieldAlert className="size-3" />Audit {Math.round(auditScore * 100)}%</span>
              )}
            </ChakraBadge>
          )}
          {content?.answer_mode && (
            <span className="rounded-md bg-[var(--bg-elevated)] px-2 py-0.5 text-[11px]">
              {modeLabels[content.answer_mode] || content.answer_mode}
            </span>
          )}
          <button
            type="button"
            onClick={handleCopy}
            className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-muted-foreground opacity-75 transition hover:bg-muted hover:text-foreground group-hover:opacity-100"
          >
            {copied ? <Check className="size-3 text-emerald-400" /> : <Copy className="size-3" />}
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function IncidentWorkspace({
  incident,
  connected,
  sidebarCollapsed,
  onSessionsChange,
  onDelete,
}) {
  const session = useIncidentSession(incident, connected, onSessionsChange)
  const [draft, setDraft] = useState('')
  const [scopeOpen, setScopeOpen] = useState(false)
  const [scope, setScope] = useState({ service: '', start: '', end: '' })
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [inspectorTab, setInspectorTab] = useState('graph')
  const fileInputRef = useRef(null)
  const bottomRef = useRef(null)

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: (accepted) => {
      if (accepted.length) session.uploadFiles(accepted)
    },
    noClick: false,
    disabled: session.sample || session.uploading,
  })

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [session.messages.length, session.asking])

  function handleOpenCitation(cit) {
    session.openCitation(cit)
    setInspectorTab('source')
    setInspectorOpen(true)
  }

  function openInspector(tab) {
    setInspectorTab(tab)
    setInspectorOpen(true)
  }

  async function handleSubmit(event) {
    event?.preventDefault()
    if (!draft.trim() || session.asking) return
    const text = draft
    setDraft('')
    await session.sendQuestion(text, {
      service: scope.service || undefined,
      start: scope.start || undefined,
      end: scope.end || undefined,
    })
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      handleSubmit()
    }
  }

  const verdict = session.result?.decision ? verdictStyles[session.result.decision] : null
  const confPct = Number.isFinite(session.result?.confidence_score) && session.result.confidence_score > 0
    ? Math.round(session.result.confidence_score * 100)
    : null
  const hasScope = Boolean(scope.service || scope.start || scope.end)
  const services = session.timeline?.services || []

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Top Minimal Context Bar */}
      <header className="flex h-13 shrink-0 items-center justify-between gap-3 border-b border-border bg-[var(--bg-canvas)]/90 px-4 backdrop-blur">
        <div className={`flex min-w-0 items-center gap-2.5 ${sidebarCollapsed ? 'pl-9' : ''}`}>
          <h1 className="truncate text-sm font-semibold">{incident.name}</h1>
          {verdict && (
            <HStack gap={1.5}>
              <ChakraBadge size="sm" variant="subtle" colorPalette={verdict.colorPalette}>
                {verdict.label}
                {session.result?.suspected_origin ? ` · ${session.result.suspected_origin}` : ''}
              </ChakraBadge>
              {confPct !== null && (
                <ChakraBadge size="sm" variant="outline" colorPalette={confPct >= 80 ? 'green' : confPct >= 50 ? 'yellow' : 'red'}>
                  {confPct}%
                </ChakraBadge>
              )}
            </HStack>
          )}
          {session.files.length > 0 && (
            <span className="hidden font-mono text-[11px] text-muted-foreground sm:inline">
              {session.files.length} file{session.files.length > 1 ? 's' : ''}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {!session.sample && (
            <Button
              variant="ghost"
              size="sm"
              onClick={session.runVerification}
              disabled={session.verifying || session.files.length === 0}
              className="h-8 gap-1.5 text-xs"
            >
              {session.verifying ? <Spinner size="xs" /> : <ShieldCheck className="size-3.5 text-emerald-400" />}
              <span className="hidden sm:inline">{session.verifying ? 'Verifying…' : 'Verify'}</span>
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => openInspector('graph')}
            className="h-8 gap-1.5 border-border bg-[var(--bg-elevated)] text-xs"
          >
            <GitFork className="size-3.5" />
            <span>Inspect</span>
          </Button>
        </div>
      </header>

      {/* Scrollable Chat Stream */}
      <div className="flex-1 overflow-y-auto px-4">
        <div className="mx-auto flex w-full max-w-3xl flex-col py-6">
          {session.error && <Alert className="mb-4">{session.error}</Alert>}

          {session.uploadReport && (
            <div className="mb-4 rounded-xl border border-border bg-[var(--bg-surface)] px-3.5 py-2.5 text-xs text-muted-foreground">
              Uploaded <strong className="text-foreground">{session.uploadReport.files_processed}</strong> file(s) ·{' '}
              {session.uploadReport.observations_added} observations ·{' '}
              {session.uploadReport.graph_nodes} graph nodes.
              {session.uploadReport.errors?.length > 0 && (
                <span className="ml-2 text-rose-400">{session.uploadReport.errors.join(' ')}</span>
              )}
            </div>
          )}

          {/* Empty State — ChatGPT Greeting + Upload + Starter Prompts */}
          {session.messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 text-center">
              <div className="mb-4 grid size-12 place-items-center rounded-2xl border border-border bg-[var(--bg-elevated)] text-emerald-400 shadow-sm">
                <Sparkles className="size-6" />
              </div>
              <h2 className="text-2xl font-semibold tracking-tight">
                {session.sample ? 'Sample DB Pool Incident' : 'Investigate an Incident'}
              </h2>
              <p className="mt-1.5 max-w-md text-sm text-muted-foreground">
                {session.files.length === 0
                  ? 'Drop logs, JSON/CSV metrics, dependency manifests, or dashboard screenshots below to begin root cause analysis.'
                  : 'Telemetry loaded. Ask about root causes, timelines, service dependencies, or anomalies.'}
              </p>

              {/* Inline Dropzone when no files yet */}
              {!session.sample && session.files.length === 0 && (
                <div
                  {...getRootProps()}
                  className={`mt-6 w-full max-w-lg cursor-pointer rounded-2xl border border-dashed p-6 transition-colors ${
                    isDragActive
                      ? 'border-emerald-400 bg-emerald-500/10'
                      : 'border-border bg-[var(--bg-surface)] hover:border-[var(--border-strong)]'
                  }`}
                >
                  <input {...getInputProps()} />
                  <div className="flex flex-col items-center gap-2 text-xs text-muted-foreground">
                    {session.uploading ? <Spinner size="sm" /> : <Upload className="size-5 text-foreground" />}
                    <span className="font-medium text-foreground">
                      {session.uploading ? 'Ingesting telemetry…' : 'Drag & drop telemetry files, or click to browse'}
                    </span>
                    <span>Supports .log, .txt, .json, .csv, .png, .jpg</span>
                  </div>
                </div>
              )}

              {/* Starter Prompt Chips */}
              {(session.files.length > 0 || session.sample) && (
                <div className="mt-7 grid w-full max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
                  {(session.sample
                    ? samplePrompts
                    : [
                        { topic: 'Root cause', label: 'What caused the latency spike?' },
                        { topic: 'Origin', label: 'Which service is the suspected origin?' },
                        { topic: 'Timeline', label: 'Show the chronological event sequence.' },
                        { topic: 'Verification', label: 'Are there any contradicting signals?' },
                      ]
                  ).slice(0, 4).map((prompt) => (
                    <button
                      key={prompt.label}
                      type="button"
                      onClick={() => session.sendQuestion(prompt.label)}
                      className="rounded-xl border border-border bg-[var(--bg-surface)] px-3.5 py-2.5 text-left transition hover:border-[var(--border-strong)] hover:bg-[var(--bg-elevated)]"
                    >
                      <div className="text-[11px] font-medium text-muted-foreground">{prompt.topic}</div>
                      <div className="mt-0.5 text-xs text-foreground">{prompt.label}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-6">
              {session.messages.map((turn, idx) => {
                if (turn.role === 'user') {
                  return (
                    <div key={idx} className="flex justify-end">
                      <div className="max-w-[80%] rounded-2xl bg-[var(--user-bubble)] px-4 py-2.5 text-[15px] leading-relaxed text-[var(--fg-primary)]">
                        {turn.content}
                      </div>
                    </div>
                  )
                }
                return (
                  <AssistantMessage
                    key={idx}
                    content={turn.content}
                    onOpenCitation={handleOpenCitation}
                  />
                )
              })}

              {session.asking && (
                <div className="flex items-center gap-3 py-2 text-sm text-muted-foreground">
                  <div className="grid size-7 place-items-center rounded-full border border-border bg-[var(--bg-elevated)]">
                    <Spinner size="xs" />
                  </div>
                  <span>Verifying observations & synthesizing reply…</span>
                </div>
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Missing Telemetry Guidance Banner */}
      {!session.sample && session.result?.decision === 'insufficient_evidence' && session.result?.next_check && (
        <div className="mx-auto mb-2 w-full max-w-3xl px-4">
          <div className="flex items-center justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3.5 py-2 text-xs text-amber-200">
            <div className="flex min-w-0 items-center gap-2">
              <AlertTriangle className="size-4 shrink-0 text-amber-400" />
              <span className="truncate">{session.result.next_check.instruction}</span>
            </div>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => openInspector('hypotheses')}
              className="h-6 shrink-0 px-2 text-[11px] text-amber-200 hover:bg-amber-500/20"
            >
              Details
            </Button>
          </div>
        </div>
      )}

      {/* Bottom Floating Pill Composer */}
      <div className="shrink-0 px-4 pb-4 pt-1">
        <form
          onSubmit={handleSubmit}
          className="mx-auto w-full max-w-3xl rounded-2xl border border-border bg-[var(--bg-surface)] p-2 shadow-lg transition-colors focus-within:border-[var(--border-strong)]"
        >
          <textarea
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={session.sample ? 'Ask a question about the sample incident…' : 'Message LogMind-X about this incident…'}
            className="max-h-40 w-full resize-none bg-transparent px-2.5 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
          />

          <div className="flex items-center justify-between gap-2 pt-1">
            <div className="flex items-center gap-1">
              {!session.sample && (
                <>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(e) => {
                      const list = Array.from(e.target.files || [])
                      if (list.length) session.uploadFiles(list)
                      e.target.value = ''
                    }}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={session.uploading}
                    className="h-8 gap-1.5 rounded-lg px-2 text-xs text-muted-foreground hover:text-foreground"
                  >
                    {session.uploading ? <Spinner size="xs" /> : <Paperclip className="size-3.5" />}
                    <span>Attach</span>
                  </Button>
                </>
              )}

              <Popover
                open={scopeOpen}
                onOpenChange={setScopeOpen}
                trigger={
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setScopeOpen((o) => !o)}
                    className={`h-8 gap-1.5 rounded-lg px-2 text-xs ${hasScope ? 'text-sky-400' : 'text-muted-foreground hover:text-foreground'}`}
                  >
                    <SlidersHorizontal className="size-3.5" />
                    <span>{scope.service || 'Scope'}</span>
                  </Button>
                }
              >
                <div className="space-y-2.5 text-xs">
                  <div className="font-medium">Filter query scope</div>
                  <div>
                    <label className="mb-1 block text-[11px] text-muted-foreground">Service</label>
                    <select
                      value={scope.service}
                      onChange={(e) => setScope((s) => ({ ...s, service: e.target.value }))}
                      className="w-full rounded-md border border-border bg-[var(--bg-canvas)] px-2 py-1.5 text-xs"
                    >
                      <option value="">All services</option>
                      {services.map((svc) => <option key={svc} value={svc}>{svc}</option>)}
                    </select>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="mb-1 block text-[11px] text-muted-foreground">UTC Start</label>
                      <input
                        type="text"
                        placeholder="2024-01-15T02:29:00Z"
                        value={scope.start}
                        onChange={(e) => setScope((s) => ({ ...s, start: e.target.value }))}
                        className="w-full rounded-md border border-border bg-[var(--bg-canvas)] px-2 py-1 text-[11px]"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-[11px] text-muted-foreground">UTC End</label>
                      <input
                        type="text"
                        placeholder="2024-01-15T02:32:00Z"
                        value={scope.end}
                        onChange={(e) => setScope((s) => ({ ...s, end: e.target.value }))}
                        className="w-full rounded-md border border-border bg-[var(--bg-canvas)] px-2 py-1 text-[11px]"
                      />
                    </div>
                  </div>
                  {hasScope && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => setScope({ service: '', start: '', end: '' })}
                      className="h-7 w-full text-[11px]"
                    >
                      Reset scope
                    </Button>
                  )}
                </div>
              </Popover>

              {session.messages.length > 0 && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={session.resetChat}
                  disabled={session.clearingChat}
                  className="h-8 gap-1 rounded-lg px-2 text-xs text-muted-foreground hover:text-foreground"
                >
                  <RotateCcw className="size-3.5" />
                  <span className="hidden sm:inline">Clear</span>
                </Button>
              )}
            </div>

            <Button
              type="submit"
              size="icon"
              disabled={!draft.trim() || session.asking}
              aria-label="Send message"
              className="size-8 rounded-xl bg-foreground text-background hover:opacity-90 disabled:opacity-35"
            >
              <ArrowUp className="size-4" />
            </Button>
          </div>
        </form>
      </div>

      {/* Slide-over Right Inspector Drawer */}
      <InspectorDrawer
        open={inspectorOpen}
        onOpenChange={setInspectorOpen}
        activeTab={inspectorTab}
        onTabChange={setInspectorTab}
        incident={incident}
        session={session}
      />
    </div>
  )
}

