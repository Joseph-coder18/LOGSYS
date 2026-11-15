import { useEffect, useRef, useSyncExternalStore } from 'react'
import { Dialog } from '@base-ui/react/dialog'
import { ArrowUpRight, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Alert, Card, Skeleton } from '@/components/ui/surfaces'
import TelemetryTimeline from './TelemetryTimeline'

const desktopQuery = '(min-width: 951px)'

function subscribeToDesktop(onChange) {
  const query = window.matchMedia(desktopQuery)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

function isDesktop() {
  return window.matchMedia(desktopQuery).matches
}

function SourceContent({ source, onTimeline }) {
  if (source.error) return <Alert>Could not load this observation. Close the preview and try the citation again.</Alert>
  if (!source.observation) return <div role="status" aria-label="Loading observation"><Skeleton /></div>
  return (
    <>
      <TelemetryTimeline observations={[source.observation]} anchored={false} />
      <Button className="mt-3" size="sm" variant="outline" onClick={() => onTimeline(source.observation)}><ArrowUpRight /> Open in timeline</Button>
    </>
  )
}

export default function ChatSourceSheet({ source, onClose, onTimeline, trigger }) {
  const desktop = useSyncExternalStore(subscribeToDesktop, isDesktop, () => false)
  const heading = useRef(null)
  const open = !!source

  useEffect(() => {
    if (desktop && open) heading.current?.focus({ preventScroll: true })
  }, [desktop, open])

  if (desktop) {
    if (!source) return null
    function dismiss() {
      onClose()
      if (trigger?.isConnected) trigger.focus({ preventScroll: true })
    }
    return (
      <Card aria-labelledby="cited-observation-title" className="side-panel chat-source-panel" onKeyDown={event => {
        if (event.key === 'Escape') dismiss()
      }}>
        <div className="side-panel-title">
          <h2 id="cited-observation-title" ref={heading} tabIndex={-1}>Cited observation</h2>
          <Button variant="ghost" size="icon" aria-label="Close source" onClick={dismiss}><X /></Button>
        </div>
        <SourceContent source={source} onTimeline={onTimeline} />
      </Card>
    )
  }

  return (
    <Dialog.Root open={open} onOpenChange={nextOpen => { if (!nextOpen) onClose() }}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/55" />
        <Dialog.Popup className="chat-source-sheet" finalFocus={() => trigger?.isConnected ? trigger : false}>
          <div className="flex items-center justify-between gap-3">
            <Dialog.Title className="text-base font-semibold">Cited observation</Dialog.Title>
            <Dialog.Close render={<Button variant="ghost" size="icon" aria-label="Close source"><X /></Button>} />
          </div>
          {source && <SourceContent source={source} onTimeline={onTimeline} />}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
