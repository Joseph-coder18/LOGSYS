import { useCallback, useEffect, useRef, useState } from 'react'
import {
  IconActivity,
  IconFlask,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconMessage,
  IconMoon,
  IconPlus,
  IconSun,
  IconTrash,
} from '@tabler/icons-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Alert } from '@/components/ui/surfaces'
import { checkHealth, createSession, deleteSession, listSessions } from '@/services/api'
import { SAMPLE_ID, sampleIncident } from './sample-incident'
import IncidentWorkspace from '@/components/IncidentWorkspace'

function readInitialSession() {
  const params = new URLSearchParams(window.location.search)
  return params.get('session') || SAMPLE_ID
}

export default function App() {
  const [status, setStatus] = useState('checking')
  const [sessions, setSessions] = useState([])
  const [selected, setSelected] = useState(readInitialSession)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [theme, setTheme] = useState(() => localStorage.getItem('logmind-theme') || 'dark')
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState(null)
  const [error, setError] = useState('')
  const sessionRequest = useRef(0)
  const connected = status === 'online'

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'light') {
      root.classList.add('light')
    } else {
      root.classList.remove('light')
    }
    localStorage.setItem('logmind-theme', theme)
  }, [theme])

  useEffect(() => {
    const url = new URL(window.location.href)
    if (selected === SAMPLE_ID) {
      url.searchParams.delete('session')
    } else {
      url.searchParams.set('session', selected)
    }
    window.history.replaceState({}, '', url)
  }, [selected])

  const refreshSessions = useCallback(async () => {
    const request = ++sessionRequest.current
    const response = await listSessions()
    if (request === sessionRequest.current) {
      setSessions(response.sessions || [])
    }
  }, [])

  useEffect(() => {
    let active = true
    async function refresh() {
      try {
        const result = await checkHealth()
        if (!active) return
        setStatus(result.status === 'ok' ? 'online' : 'offline')
        if (result.status === 'ok') {
          await refreshSessions().catch(() => {
            if (active) setError('Could not load incidents.')
          })
        }
      } catch {
        if (active) setStatus('offline')
      }
    }
    refresh()
    const interval = setInterval(refresh, 30_000)
    return () => {
      active = false
      clearInterval(interval)
    }
  }, [refreshSessions])

  function select(id) {
    setSelected(id)
    setError('')
    if (window.innerWidth < 768) setSidebarOpen(false)
  }

  async function newIncident() {
    setCreating(true)
    setError('')
    try {
      const incident = await createSession()
      ++sessionRequest.current
      setSessions((prev) => [incident, ...prev.filter((item) => item.id !== incident.id)])
      select(incident.id)
    } catch {
      setError('Could not create an incident. Ensure the backend is running.')
    } finally {
      setCreating(false)
    }
  }

  async function removeIncident() {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await deleteSession(pendingDelete.id)
      ++sessionRequest.current
      setSessions((prev) => prev.filter((item) => item.id !== pendingDelete.id))
      setSelected((prev) => (prev === pendingDelete.id ? SAMPLE_ID : prev))
      setPendingDelete(null)
    } catch {
      setPendingDelete(null)
      setError('Could not delete the incident.')
    } finally {
      setDeleting(false)
    }
  }

  const incident =
    selected === SAMPLE_ID
      ? sampleIncident
      : sessions.find((item) => item.id === selected) || (sessions.length === 0 ? sampleIncident : null)

  return (
    <div className="flex h-dvh w-full overflow-hidden bg-[var(--bg-canvas)] text-[var(--fg-primary)]">
      {/* Collapsible ChatGPT-style Left Sidebar */}
      {sidebarOpen && (
        <aside
          aria-label="Incidents"
          className="flex h-full w-[260px] shrink-0 flex-col border-r border-border bg-[var(--bg-sidebar)]"
        >
          <div className="flex items-center justify-between px-3 py-3">
            <div className="flex items-center gap-2 font-semibold tracking-tight">
              <span className="grid size-7 place-items-center rounded-lg bg-emerald-500/15 text-emerald-400">
                <IconActivity className="size-4" stroke={1.75} />
              </span>
              <span className="text-sm">LogMind-X</span>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setSidebarOpen(false)}
              aria-label="Collapse sidebar"
              className="size-8 text-muted-foreground hover:text-foreground"
            >
              <IconLayoutSidebarLeftCollapse className="size-4" stroke={1.75} />
            </Button>
          </div>

          <div className="px-3 pb-2">
            <Button
              onClick={newIncident}
              disabled={!connected || creating}
              className="w-full justify-start gap-2 rounded-lg border border-border bg-[var(--bg-elevated)] text-foreground hover:bg-muted"
            >
              <IconPlus className="size-4" stroke={1.75} />
              <span className="text-xs font-medium">{creating ? 'Creating…' : 'New incident'}</span>
            </Button>
          </div>

          <div className="flex-1 overflow-y-auto px-2 py-2">
            <div className="mb-1.5 px-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Incidents
            </div>
            <div className="space-y-0.5">
              {sessions.length === 0 && (
                <p className="px-2 py-2 text-xs text-muted-foreground">No live incidents yet.</p>
              )}
              {sessions.map((item) => {
                const active = selected === item.id
                return (
                  <div
                    key={item.id}
                    className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm transition-colors ${
                      active ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => select(item.id)}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    >
                      <IconMessage className="size-3.5 shrink-0 opacity-70" stroke={1.75} />
                      <span className="truncate text-xs">{item.name}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setPendingDelete(item)}
                      aria-label={`Delete ${item.name}`}
                      className="hidden rounded p-1 text-muted-foreground hover:text-destructive group-hover:inline-flex"
                    >
                      <IconTrash className="size-3.5" stroke={1.75} />
                    </button>
                  </div>
                )
              })}
            </div>

            <div className="mt-5 mb-1.5 px-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Demo Preview
            </div>
            <button
              type="button"
              onClick={() => select(SAMPLE_ID)}
              className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${
                selected === SAMPLE_ID ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'
              }`}
            >
              <IconFlask className="size-3.5 shrink-0 text-emerald-400" stroke={1.75} />
              <span className="truncate">Sample DB Incident</span>
            </button>
          </div>

          {/* Sidebar Footer: Status + Theme */}
          <div className="flex items-center justify-between border-t border-border px-3 py-2.5 text-xs text-muted-foreground">
            <div className="flex items-center gap-2">
              <span
                className={`size-2 rounded-full ${
                  connected ? 'bg-emerald-500' : status === 'checking' ? 'bg-amber-400' : 'bg-rose-500'
                }`}
              />
              <span>{connected ? 'API Online' : status === 'checking' ? 'Connecting…' : 'Offline'}</span>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
              aria-label="Toggle theme"
              className="size-7"
            >
              {theme === 'dark' ? <IconSun className="size-3.5" stroke={1.75} /> : <IconMoon className="size-3.5" stroke={1.75} />}
            </Button>
          </div>
        </aside>
      )}

      {/* Main Conversational Canvas */}
      <main className="relative flex min-w-0 flex-1 flex-col">
        {!sidebarOpen && (
          <div className="absolute top-2.5 left-3 z-20">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open sidebar"
              className="size-8 border border-border bg-[var(--bg-sidebar)]"
            >
              <IconLayoutSidebarLeftExpand className="size-4" stroke={1.75} />
            </Button>
          </div>
        )}

        {error && (
          <div className="mx-auto mt-3 w-full max-w-3xl px-4">
            <Alert>{error}</Alert>
          </div>
        )}

        {incident ? (
          <IncidentWorkspace
            key={incident.id}
            incident={incident}
            connected={connected}
            sidebarCollapsed={!sidebarOpen}
            onSessionsChange={refreshSessions}
            onDelete={() => setPendingDelete(incident)}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
            This incident is unavailable. Select another incident or open the Sample Preview.
          </div>
        )}
      </main>

      <ConfirmDialog
        open={!!pendingDelete}
        onOpenChange={(open) => {
          if (!open && !deleting) setPendingDelete(null)
        }}
        title="Delete this incident?"
        description={`This removes ${pendingDelete?.name || 'the incident'} and its investigation history.`}
        onConfirm={removeIncident}
        busy={deleting}
      />
    </div>
  )
}
