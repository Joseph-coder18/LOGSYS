import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Badge, Skeleton } from '@/components/ui/surfaces'

const ForceGraph = lazy(() => import('react-force-graph-2d'))
const colors = { service: '#98b9ff', database: '#f8ca81', file: '#bfc0ff', error: '#ffa5ac' }

export default function InvestigationGraph({ graph, referenceNode }) {
  const frame = useRef(null)
  const [width, setWidth] = useState(0)
  const [selectedId, setSelectedId] = useState(referenceNode)
  const [hoveredId, setHoveredId] = useState(null)
  const data = useMemo(() => {
    const nodes = graph.nodes.map(node => ({ ...node }))
    const ids = new Set(nodes.map(node => node.id))
    const links = graph.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)).map(edge => ({ ...edge }))
    return { nodes, links }
  }, [graph])
  const selected = graph.nodes.find(node => node.id === selectedId)
  const hovered = graph.nodes.find(node => node.id === hoveredId)
  const connectionCount = hovered
    ? graph.edges.filter(edge => edge.source === hovered.id || edge.target === hovered.id).length
    : 0

  useEffect(() => {
    if (!frame.current) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)))
    observer.observe(frame.current)
    return () => observer.disconnect()
  }, [graph.nodes.length])

  if (!graph.nodes.length) return <p className="panel-empty">Upload incident data to populate the graph.</p>

  return (
    <>
      <div className="graph-frame" ref={frame} aria-label="Incident relationship graph">
        <Suspense fallback={<Skeleton className="h-full" />}>
          {width > 0 && (
            <ForceGraph
              width={width}
              height={300}
              graphData={data}
              nodeLabel={() => ''}
              linkColor={() => '#667e9e'}
              linkDirectionalArrowLength={4}
              linkDirectionalArrowRelPos={0.9}
              cooldownTicks={65}
              onNodeClick={node => setSelectedId(node.id)}
              onNodeHover={node => setHoveredId(node?.id ?? null)}
              nodeCanvasObject={(node, context, scale) => {
                const radius = hoveredId === node.id ? 7 : 5
                context.beginPath()
                context.arc(node.x, node.y, radius, 0, 2 * Math.PI)
                context.fillStyle = colors[node.type] || '#a3b5b1'
                context.fill()
                if (selectedId === node.id) {
                  context.strokeStyle = '#fff'
                  context.lineWidth = 1.5 / scale
                  context.stroke()
                }
                context.font = `${12 / scale}px Inter, sans-serif`
                context.textAlign = 'center'
                context.fillStyle = '#edf2fc'
                context.fillText(node.id, node.x, node.y + radius + 13 / scale)
              }}
            />
          )}
        </Suspense>
      </div>
      <p className="graph-inspection">
        {hovered ? <><strong>{hovered.id}</strong> · {hovered.type || 'entity'} · {connectionCount} {connectionCount === 1 ? 'connection' : 'connections'}</> : 'Hover over a node to see its type and connections. Select it for details.'}
      </p>
      <details className="graph-list">
        <summary>Inspect graph nodes</summary>
        <div className="flex flex-wrap gap-2 pt-3">
          {graph.nodes.map(node => (
            <button type="button" className="service-chip" key={node.id} onClick={() => setSelectedId(node.id)} aria-pressed={selectedId === node.id}>
              {node.id}
            </button>
          ))}
        </div>
      </details>
      {selected && (
        <div className="graph-selection reference-target" id="graph-reference" tabIndex={-1}>
          <strong>{selected.id}</strong> <Badge>{selected.type || 'entity'}</Badge>
          <ul>
            {graph.edges.filter(edge => edge.source === selected.id || edge.target === selected.id).map((edge, index) => (
              <li key={index}>{edge.source} → {edge.target}{edge.rel && ` · ${edge.rel}`}</li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}
