import { useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { FileText, Upload, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Alert, Card } from '@/components/ui/surfaces'

const accepted = {
  'text/plain': ['.log', '.txt'],
  'text/csv': ['.csv'],
  'application/json': ['.json'],
  'image/png': ['.png'],
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/webp': ['.webp'],
}

export default function IncidentSources({ files, sample, disabled, onUpload }) {
  const [staged, setStaged] = useState([])
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState(null)
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: accepted,
    disabled: disabled || sample || busy,
    onDrop: (incoming, rejected) => {
      setStaged(previous => [...new Map([...previous, ...incoming].map(file => [file.name, file])).values()])
      setFeedback(rejected.length ? { errors: rejected.map(({ file }) => `Unsupported file: ${file.name}`) } : null)
    },
  })

  async function upload() {
    setBusy(true)
    setFeedback(null)
    try {
      const result = await onUpload(staged)
      setFeedback(result)
      if (result.status === 'success') setStaged([])
    } catch (error) {
      setFeedback({ errors: [error.response?.data?.detail || error.message] })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="side-panel">
      <div className="side-panel-title"><h2>Sources</h2><span>{files.length}</span></div>
      {files.length > 0 && <ul className="source-list">{files.map(name => <li key={name}><FileText size={15} /><span>{name}</span></li>)}</ul>}
      {!sample && (
        <>
          <div {...getRootProps({ className: `dropzone${isDragActive ? ' is-dragging' : ''}`, role: 'button', 'aria-label': 'Choose telemetry files', 'aria-disabled': disabled || busy })}>
            <input {...getInputProps()} />
            <Upload size={18} />
            <span>{isDragActive ? 'Drop files here' : 'Drop files or browse'}</span>
            <small>Logs, metrics, screenshots</small>
          </div>
          {staged.length > 0 && (
            <>
              <ul className="source-list">{staged.map(file => (
                <li key={file.name}><span>{file.name}</span><Button variant="ghost" size="icon" aria-label={`Remove ${file.name}`} disabled={busy} onClick={() => setStaged(previous => previous.filter(item => item !== file))}><X /></Button></li>
              ))}</ul>
              <Button className="mt-3 w-full" disabled={disabled || busy} onClick={upload}>{busy ? 'Uploading…' : 'Upload files'}</Button>
            </>
          )}
          {feedback?.errors?.length > 0 && <Alert className="mt-3">{feedback.errors.map((error, index) => <p key={index}>{String(error)}</p>)}</Alert>}
          {feedback?.status === 'success' && <p className="mt-3 text-xs text-primary" role="status">{feedback.files_processed} files processed · {feedback.chunks_indexed} chunks indexed</p>}
        </>
      )}
    </Card>
  )
}
