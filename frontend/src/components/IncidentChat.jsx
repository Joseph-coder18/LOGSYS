import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, ArrowUpRight, Activity, Check, ClipboardList, Clock3, Copy, FileText, GitFork, ListChecks, MessagesSquare, RotateCcw, ScanLine } from 'lucide-react'
import { Loader } from '@/components/prompt-kit/loader'
import { Button } from '@/components/ui/button'
import { Badge, Textarea } from '@/components/ui/surfaces'
import { samplePrompts } from '@/sample-incident'

const referenceIcons = { evidence: FileText, timeline: Clock3, graph: GitFork }
const topicIcons = [ScanLine, Activity, ListChecks, ClipboardList]
const replyLabels = { generated: 'Model reply', extractive: 'Source-backed reply', local: 'Local reply' }
const livePrompts = [
  { topic: 'What changed?', label: 'What changed before the errors started?' },
  { topic: 'Which services saw errors?', label: 'Which services saw errors, and when?' },
  { topic: 'What should I check next?', label: 'What evidence should I check next?' },
]

function Reply({ content, onReference, onObservation }) {
  const [copyStatus, setCopyStatus] = useState('')
  const reply = typeof content === 'string' ? content : content?.answer || content?.summary || ''
  const references = content?.references || []
  const claims = content?.claims || []

  async function copy() {
    try {
      await navigator.clipboard.writeText(reply)
      setCopyStatus('Copied')
    } catch {
      setCopyStatus('Copy failed. Select the reply to copy it.')
    }
  }

  return (
    <>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        {content?.answer_mode && (
          <Badge tone={content.answer_mode === 'generated' ? 'positive' : 'neutral'}>
            {replyLabels[content.answer_mode] || replyLabels.local}
          </Badge>
        )}
        {typeof content?.confidence_score === 'number' && content.confidence_score > 0 && (
          <Badge tone={content.confidence_score >= 0.8 ? 'positive' : content.confidence_score >= 0.5 ? 'neutral' : 'warning'}>
            {Math.round(content.confidence_score * 100)}% confidence
          </Badge>
        )}
      </div>
      {claims.length ? (
        <div className="grid gap-3">
          {claims.map((claim, index) => (
            <div key={index}>
              <p className="chat-text">{claim.text}</p>
              {claim.citations.length > 0 && <div className="mt-2 flex flex-wrap gap-2" aria-label="Claim sources">
                {claim.citations.map(citation => (
                  <Button key={citation.observation_id} size="sm" variant="outline" className="chat-reference" onClick={event => onObservation(citation.observation_id, event.currentTarget)}>
                    <FileText aria-hidden="true" /><span>{citation.label}</span><ArrowUpRight aria-hidden="true" />
                  </Button>
                ))}
              </div>}
            </div>
          ))}
        </div>
      ) : <p className="chat-text">{reply}</p>}
      {references.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2" aria-label="Investigation references">
          {references.map(reference => {
            const Icon = referenceIcons[reference.view]
            return (
              <Button key={reference.label} className="chat-reference" size="sm" variant="outline" onClick={() => onReference(reference)}>
                <Icon aria-hidden="true" /><span>{reference.label}</span><ArrowUpRight aria-hidden="true" />
              </Button>
            )
          })}
        </div>
      )}
      {reply && (
        <div className="mt-1 flex items-center gap-2">
          <Button size="icon" variant="ghost" aria-label="Copy reply" onClick={copy}>
            {copyStatus === 'Copied' ? <Check /> : <Copy />}
          </Button>
          <span role="status" className="text-xs text-muted-foreground">{copyStatus}</span>
        </div>
      )}
    </>
  )
}

export default function IncidentChat({ sample, hasTelemetry, messages, running, disabled, question, onQuestionChange, onSend, onReference, onObservation, onReset }) {
  const history = useRef(null)
  const follow = useRef(true)
  const [atBottom, setAtBottom] = useState(true)

  function scrollToLatest() {
    if (history.current) history.current.scrollTop = history.current.scrollHeight
    follow.current = true
    setAtBottom(true)
  }

  useEffect(() => {
    if (follow.current) scrollToLatest()
  }, [messages, running])

  function resetChat() {
    scrollToLatest()
    onReset()
  }

  return (
    <section className="incident-chat" aria-labelledby="incident-chat-heading">
      <header className="chat-panel-heading">
        <h2 id="incident-chat-heading"><MessagesSquare size={16} /> Incident chat</h2>
        {(sample || hasTelemetry) && (
          <div className="flex flex-wrap items-center gap-2">
            {sample && <Badge tone="warning">Canned replies</Badge>}
            <Button variant="ghost" size="sm" onClick={resetChat} disabled={!messages.length && !question.trim() && !running}><RotateCcw /> Reset chat</Button>
          </div>
        )}
      </header>
      <div
        className="chat-history"
        ref={history}
        role="log"
        aria-label="Conversation"
        tabIndex={0}
        onScroll={({ currentTarget }) => {
          follow.current = currentTarget.scrollHeight - currentTarget.scrollTop - currentTarget.clientHeight < 24
          setAtBottom(follow.current)
        }}
      >
        {!messages.length && <p className="chat-intro">{sample || hasTelemetry ? 'What do you need to investigate?' : 'Upload telemetry to ask questions about this incident.'}</p>}
        <ol className="chat-messages">
          {messages.map((message, index) => (
            <li key={index} className={message.role === 'user' ? 'chat-user' : 'chat-assistant'}>
              {message.role === 'user'
                ? <p className="chat-text"><span className="sr-only">You: </span>{message.content}</p>
                : <><h3>LogMind</h3><Reply content={message.content} onReference={onReference} onObservation={onObservation} /></>}
            </li>
          ))}
          {running && (
            <li className="chat-assistant chat-loading">
              <h3>LogMind</h3>
              <div className="typing-indicator" role="status">
                <Loader size="sm" />
                <span className="sr-only">{sample ? 'Loading sample reply' : 'Analyzing incident evidence'}</span>
              </div>
            </li>
          )}
        </ol>
      </div>
      {!atBottom && <Button className="chat-latest" size="sm" variant="outline" onClick={scrollToLatest}><ArrowDown /> Jump to latest</Button>}
      {(sample || (!messages.length && hasTelemetry)) && (
        <div className="chat-starters chat-followups" aria-label="Suggested investigations">
          {(sample ? samplePrompts : livePrompts).map(({ label, topic }, index) => {
            const Icon = topicIcons[index]
            return (
              <Button key={label} className="chat-starter" variant="outline" size="sm" disabled={running || disabled} onClick={() => { scrollToLatest(); onSend(label) }}>
                <Icon aria-hidden="true" /><span>{topic}</span><ArrowUpRight aria-hidden="true" />
              </Button>
            )
          })}
        </div>
      )}
      <form className="chat-composer" onSubmit={event => { event.preventDefault(); onSend(question) }}>
        <label htmlFor="incident-question" className="sr-only">Ask about this incident</label>
        <Textarea
          id="incident-question"
          value={question}
          onChange={event => onQuestionChange(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              onSend(question)
            }
          }}
          placeholder="Ask about this incident…"
          disabled={disabled}
          rows={2}
        />
        <div className="chat-composer-footer">
          <span>Shift + Enter for a new line</span>
          <Button type="submit" size="icon" aria-label="Send question" disabled={disabled || running || !question.trim()}><ArrowUp /></Button>
        </div>
      </form>
    </section>
  )
}
