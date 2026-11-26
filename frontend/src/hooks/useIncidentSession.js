import { useCallback, useEffect, useRef, useState } from 'react'
import {
  causalRanking as fetchCausalRanking,
  clearChat,
  latestVerification,
  sessionChat,
  sessionGraph,
  sessionIngest,
  sessionObservation,
  sessionObservations,
  sessionOverview,
  sessionQuery,
  verifyIncident,
} from '@/services/api'
import { SAMPLE_ID, sampleGraph, sampleOverview, samplePrompts, sampleResult } from '@/sample-incident'

const emptyGraph = { nodes: [], edges: [] }
const emptyTimeline = {
  observations: [],
  next_offset: null,
  counts: { all: 0, anomalies: 0, changes: 0, errors: 0 },
  services: [],
}
const allEvents = { category: 'all', service: '', start: '', end: '' }

export function useIncidentSession(incident, connected, onSessionsChange) {
  const sample = incident.id === SAMPLE_ID
  const [files, setFiles] = useState(incident.files || [])
  const [graph, setGraph] = useState(sample ? sampleGraph : emptyGraph)
  const [overview, setOverview] = useState(sample ? sampleOverview : null)
  const [timeline, setTimeline] = useState(sample ? { ...emptyTimeline, services: ['user-db', 'auth-service', 'api-gateway'] } : emptyTimeline)
  const [timelineFilter, setTimelineFilter] = useState(allEvents)
  const [timelineLoading, setTimelineLoading] = useState(false)
  const [timelineError, setTimelineError] = useState('')
  const [result, setResult] = useState(sample ? sampleResult : null)
  const [causalCandidates, setCausalCandidates] = useState([])
  const [messages, setMessages] = useState([])
  const [uploading, setUploading] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [asking, setAsking] = useState(false)
  const [error, setError] = useState('')
  const [uploadReport, setUploadReport] = useState(null)
  const [clearingChat, setClearingChat] = useState(false)
  const [sourceSheet, setSourceSheet] = useState({ open: false, loading: false, citation: null, observation: null, error: '' })
  const requestIds = useRef({ timeline: 0, source: 0 })

  const loadTimeline = useCallback(async (filter = allEvents, offset = 0, append = false) => {
    if (sample) return
    const req = ++requestIds.current.timeline
    setTimelineLoading(true)
    setTimelineError('')
    try {
      const page = await sessionObservations(incident.id, { ...filter, offset })
      if (req !== requestIds.current.timeline) return
      setTimeline((prev) => ({
        ...page,
        observations: append ? [...prev.observations, ...page.observations] : page.observations,
      }))
    } catch {
      if (req === requestIds.current.timeline) setTimelineError('Could not load timeline events.')
    } finally {
      if (req === requestIds.current.timeline) setTimelineLoading(false)
    }
  }, [incident.id, sample])

  useEffect(() => {
    setFiles(incident.files || [])
    setGraph(sample ? sampleGraph : emptyGraph)
    setOverview(sample ? sampleOverview : null)
    setTimeline(sample ? { ...emptyTimeline, services: ['user-db', 'auth-service', 'api-gateway'] } : emptyTimeline)
    setTimelineFilter(allEvents)
    setResult(sample ? sampleResult : null)
    setCausalCandidates([])
    setMessages([])
    setError('')
    setUploadReport(null)
    if (sample || !connected) return

    let active = true
    Promise.allSettled([
      sessionOverview(incident.id),
      sessionGraph(incident.id),
      sessionChat(incident.id),
      latestVerification(incident.id),
      fetchCausalRanking(incident.id),
    ]).then(([ov, gr, ch, ver, rank]) => {
      if (!active) return
      if (ov.status === 'fulfilled') setOverview(ov.value)
      if (gr.status === 'fulfilled') setGraph(gr.value)
      if (ch.status === 'fulfilled') setMessages(ch.value.history || [])
      if (ver.status === 'fulfilled' && ver.value) setResult(ver.value)
      if (rank.status === 'fulfilled') setCausalCandidates(rank.value.candidates || [])
    })
    loadTimeline(allEvents, 0, false)
    return () => { active = false }
  }, [incident.id, connected, sample, loadTimeline])

  async function uploadFiles(selectedFiles) {
    if (sample || !selectedFiles.length) return
    setUploading(true)
    setError('')
    try {
      const report = await sessionIngest(incident.id, selectedFiles)
      setUploadReport(report)
      setFiles((prev) => [...new Set([...prev, ...(report.processed_files || [])])])
      const [ov, gr, rank] = await Promise.allSettled([
        sessionOverview(incident.id),
        sessionGraph(incident.id),
        fetchCausalRanking(incident.id),
      ])
      if (ov.status === 'fulfilled') setOverview(ov.value)
      if (gr.status === 'fulfilled') setGraph(gr.value)
      if (rank.status === 'fulfilled') setCausalCandidates(rank.value.candidates || [])
      await loadTimeline(timelineFilter, 0, false)
      await onSessionsChange?.()
      if (report.files_processed > 0) {
        await runVerification()
      }
    } catch (err) {
      setError(err?.response?.data?.detail || 'Upload failed. Check file formats and size limits.')
    } finally {
      setUploading(false)
    }
  }

  async function runVerification() {
    if (sample) return
    setVerifying(true)
    setError('')
    try {
      const [ver, rank] = await Promise.allSettled([
        verifyIncident(incident.id),
        fetchCausalRanking(incident.id),
      ])
      if (ver.status === 'fulfilled') setResult(ver.value)
      if (rank.status === 'fulfilled') setCausalCandidates(rank.value.candidates || [])
    } catch (err) {
      setError(err?.response?.data?.detail || 'Verification failed.')
    } finally {
      setVerifying(false)
    }
  }

  async function sendQuestion(queryText, scope = {}) {
    if (!queryText.trim()) return
    const trimmed = queryText.trim()
    setAsking(true)
    setError('')
    const optimisticUser = { role: 'user', content: trimmed }
    setMessages((prev) => [...prev, optimisticUser])

    if (sample) {
      const matched = samplePrompts.find((p) => p.label.toLowerCase() === trimmed.toLowerCase())
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: {
            ...sampleResult,
            answer_mode: 'local',
            audit_score: 1.0,
            claims: [
              {
                text: matched ? matched.reply : sampleResult.summary,
                audit_status: 'verified',
                audit_warnings: [],
                citations: sampleResult.evidence.slice(0, 3).map((ev, i) => ({
                  observation_id: `sample-${i}`,
                  label: `${ev.source} · ${ev.type === 'log' ? 'line' : 'record'} ${ev.source_position}`,
                })),
              },
            ],
          },
        },
      ])
      setAsking(false)
      return
    }

    try {
      const reply = await sessionQuery(incident.id, trimmed, scope)
      setMessages((prev) => [...prev, { role: 'assistant', content: reply }])
      if (reply.decision) {
        setResult((prev) => (prev ? { ...prev, ...reply } : reply))
      }
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to get an answer.')
    } finally {
      setAsking(false)
    }
  }

  async function resetChat() {
    if (sample) {
      setMessages([])
      return
    }
    setClearingChat(true)
    try {
      await clearChat(incident.id)
      setMessages([])
    } catch {
      setError('Could not clear chat history.')
    } finally {
      setClearingChat(false)
    }
  }

  async function openCitation(citation) {
    if (sample || !citation?.observation_id || citation.observation_id.startsWith('sample-')) {
      setSourceSheet({
        open: true,
        loading: false,
        citation,
        observation: {
          id: citation?.observation_id || 'sample',
          modality: 'metrics',
          service: 'user-db',
          source_file: citation?.label || 'demo_metrics.json',
          summary: sampleResult.summary,
          excerpt: sampleResult.evidence.map((e) => `${e.source}: ${e.snippet}`).join('\n'),
        },
        error: '',
      })
      return
    }
    const req = ++requestIds.current.source
    setSourceSheet({ open: true, loading: true, citation, observation: null, error: '' })
    try {
      const obs = await sessionObservation(incident.id, citation.observation_id)
      if (req === requestIds.current.source) {
        setSourceSheet({ open: true, loading: false, citation, observation: obs, error: '' })
      }
    } catch {
      if (req === requestIds.current.source) {
        setSourceSheet({ open: true, loading: false, citation, observation: null, error: 'Could not load source record.' })
      }
    }
  }

  return {
    sample,
    files,
    graph,
    overview,
    timeline,
    timelineFilter,
    setTimelineFilter,
    timelineLoading,
    timelineError,
    loadTimeline,
    result,
    causalCandidates,
    messages,
    uploading,
    verifying,
    asking,
    clearingChat,
    error,
    setError,
    uploadReport,
    uploadFiles,
    runVerification,
    sendQuestion,
    resetChat,
    sourceSheet,
    setSourceSheet,
    openCitation,
  }
}

