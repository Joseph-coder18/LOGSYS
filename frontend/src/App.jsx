import { useCallback, useEffect, useRef, useState } from 'react'
import { Activity, FlaskConical, FolderOpen, Menu, Plus, X } from 'lucide-react'
import Investigation from '@/components/Investigation'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Alert, Badge } from '@/components/ui/surfaces'
import { checkHealth, createSession, deleteSession, listSessions } from '@/services/api'
import { SAMPLE_ID, sampleIncident } from './sample-incident'

export default function App() {
  const [status, setStatus] = useState('checking')
  const [sessions, setSessions] = useState([])
  const [selected, setSelected] = useState(SAMPLE_ID)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState(null)
  const [error, setError] = useState('')
  const sessionRequest = useRef(0)
  const connected = status === 'online'

  const refreshSessions = useCallback(async () => {
    const request = ++sessionRequest.current
    const response = await listSessions()
    if (request === sessionRequest.current) setSessions(response.sessions || [])
  }, [])

  useEffect(() => {
    let active = true
    async function refresh() {
      try {
        const result = await checkHealth()
        if (!active) return
        setStatus(result.status === 'ok' ? 'online' : 'offline')
        if (result.status === 'ok') await refreshSessions().catch(() => {
          if (active) setError('Could not load live incidents.')
        })
      } catch {
        if (active) setStatus('offline')
      }
    }
    refresh()
    const interval = setInterval(refresh, 30_000)
    return () => { active = false; clearInterval(interval) }
  }, [refreshSessions])

  function select(id) {
    setSelected(id)
    setSidebarOpen(false)
    setError('')
  }

  async function newIncident() {
    setCreating(true)
    setError('')
    try {
      const incident = await createSession()
      ++sessionRequest.current
      setSessions(previous => [incident, ...previous.filter(item => item.id !== incident.id)])
      select(incident.id)
    } catch {
      setError('Could not create an incident. Check that the backend is running.')
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
      setSessions(previous => previous.filter(item => item.id !== pendingDelete.id))
      setSelected(previous => previous === pendingDelete.id ? SAMPLE_ID : previous)
      setPendingDelete(null)
    } catch {
      setPendingDelete(null)
      setError('Could not delete the incident.')
    } finally {
      setDeleting(false)
    }
  }

  const incident = selected === SAMPLE_ID ? sampleIncident : sessions.find(item => item.id === selected)

  return (
    <div className="console">
      <a className="skip-link" href="#workspace">Skip to investigation</a>
      <header className="console-top">
        <div className="top-brand">
          <Button className="hidden max-[700px]:inline-flex" variant="ghost" size="icon" onClick={() => setSidebarOpen(open => !open)} aria-label={sidebarOpen ? 'Hide incidents' : 'Show incidents'} aria-expanded={sidebarOpen} aria-controls="incident-nav">{sidebarOpen ? <X /> : <Menu />}</Button>
          <span className="brand"><span className="brand-mark"><Activity size={20} /></span>LogMind</span>
        </div>
        <Badge role="status" tone={connected ? 'positive' : 'neutral'}><span className="status-dot" />{status === 'checking' ? 'Connecting' : connected ? 'Backend connected' : 'Backend offline'}</Badge>
      </header>
      <div className="console-body">
        <nav id="incident-nav" aria-label="Incidents" className={`console-nav${sidebarOpen ? ' is-open' : ''}`}>
          <div className="nav-heading"><h2>Incidents</h2></div>
          <Button className="w-full justify-start" onClick={newIncident} disabled={!connected || creating}><Plus /> {creating ? 'Creating…' : 'New incident'}</Button>
          <div className="nav-list">
            {sessions.map(item => (
              <Button key={item.id} variant="ghost" className="nav-item justify-start" onClick={() => select(item.id)} aria-current={selected === item.id ? 'true' : undefined}><FolderOpen /><span>{item.name}</span></Button>
            ))}
          </div>
          <div className="sample-nav">
            <h2>Preview</h2>
            <Button variant="ghost" className="nav-item justify-start" onClick={() => select(SAMPLE_ID)} aria-current={selected === SAMPLE_ID ? 'true' : undefined}><FlaskConical /><span>Sample incident</span></Button>
          </div>
        </nav>
        <main id="workspace" className="workspace">
          {error && <Alert className="mb-4">{error}</Alert>}
          {incident ? <Investigation key={incident.id} incident={incident} connected={connected} onSessionsChange={refreshSessions} onDelete={() => setPendingDelete(incident)} /> : (
            <div className="panel-empty">This incident is unavailable. Choose another incident or open the sample.</div>
          )}
        </main>
      </div>
      <ConfirmDialog open={!!pendingDelete} onOpenChange={open => { if (!open && !deleting) setPendingDelete(null) }} title="Delete this incident?" description={`This removes ${pendingDelete?.name || 'the incident'} and its investigation history.`} onConfirm={removeIncident} busy={deleting} />
    </div>
  )
}
