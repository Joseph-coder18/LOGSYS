import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Maximize, Minus, Plus, RotateCcw } from 'lucide-react'
import ForceGraph from 'react-force-graph-2d'
import IncidentImpact from './IncidentImpact'
import { Badge, Card } from '@/components/ui/surfaces'
import { Button } from '@/components/ui/button'
import {
  drawGraphEdgeLabel,
  drawGraphNode,
  fitGraph,
  graphInset,
  impactDistances,
  layoutImpactNodes,
  maxGraphZoom,
  nodeRadius,
  paintNodeTarget,
  relationshipLabel,
} from '@/lib/incident-graph'

const minZoom = 0.003
const legend = {
  origin: { label: 'Suspected origin', tone: 'error' },
  affected: { label: 'Observed impact', tone: 'warning' },
  potential: { label: 'Potential impact', tone: 'change' },
}

function serviceNode(graph, service) {
  return graph.nodes.find(node => ['service', 'database'].includes(node.type) && (node.service || node.id) === service)
}

function SelectedNode({ node, role, edges, labels, impact }) {
  return (
    <Card className="side-panel graph-selection" id="graph-reference" tabIndex={-1}>
      <div className="side-panel-title flex-wrap">
        <h2>Selected node · <span className="font-mono">{node.service || node.id}</span></h2>
        <div className="flex flex-wrap gap-1.5">
          <Badge>{node.type || 'Entity'}</Badge>
          {role && <Badge tone={legend[role].tone}>{legend[role].label}</Badge>}
        </div>
      </div>
      {edges.length ? (
        <ul className="grid gap-2 text-xs">
          {edges.map((edge, index) => (
            <li key={index} className="rounded-md border border-border bg-background/50 p-2">
              <p className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono">{labels.get(edge.source)}</span>
                <span aria-hidden="true">→</span>
                <span className="font-mono">{labels.get(edge.target)}</span>
                <Badge>{relationshipLabel(edge, impact)}</Badge>
              </p>
              {edge.source_file && <small className="mt-1 block text-muted-foreground">{edge.source_file} · record {edge.source_position}</small>}
            </li>
          ))}
        </ul>
      ) : <p className="text-xs text-muted-foreground">No relationships in this view.</p>}
    </Card>
  )
}

export default function InvestigationGraph({ graph, referenceNode, focusSelection, sessionId, evidenceRevision, onObservation, suspectedOrigin, affectedServices }) {
  const frame = useRef(null)
  const force = useRef(null)
  const fittedLayout = useRef(null)
  const [width, setWidth] = useState(0)
  const [scope, setScope] = useState(null)
  const [layoutVersion, setLayoutVersion] = useState(0)
  const [selectedId, setSelectedId] = useState(() => serviceNode(graph, referenceNode)?.id || referenceNode)
  const [hoveredId, setHoveredId] = useState(null)
  const [motion, setMotion] = useState(false)
  const dependencies = useMemo(() => graph.edges.filter(edge => edge.rel === 'depends_on'), [graph])
  const origin = serviceNode(graph, suspectedOrigin)?.id
  const distances = useMemo(() => impactDistances(dependencies, origin), [dependencies, origin])
  const hasImpact = distances.size > 1
  const activeScope = scope === 'all' || (scope === 'impact' && hasImpact) || (scope === 'dependencies' && dependencies.length)
    ? scope : hasImpact ? 'impact' : dependencies.length ? 'dependencies' : 'all'
  const isImpact = activeScope === 'impact'
  const vertical = width < 510
  const height = vertical ? 418 : 328
  const { nodes, edges } = useMemo(() => {
    if (activeScope === 'all') return { nodes: graph.nodes, edges: graph.edges }
    if (isImpact) {
      // Use caller propagation direction in the impact view and keep source provenance.
      const edges = dependencies
        .filter(edge => distances.get(edge.source) === distances.get(edge.target) + 1)
        .map(edge => ({ ...edge, source: edge.target, target: edge.source }))
      return { nodes: graph.nodes.filter(node => distances.has(node.id)), edges }
    }
    const endpoints = new Set(dependencies.flatMap(edge => [edge.source, edge.target]))
    return { nodes: graph.nodes.filter(node => endpoints.has(node.id)), edges: dependencies }
  }, [graph, activeScope, isImpact, dependencies, distances])

  // The renderer mutates nodes and links while simulating.
  const data = useMemo(() => {
    const graphNodes = nodes.map(node => ({ ...node }))
    if (isImpact) layoutImpactNodes(graphNodes, distances, vertical)
    return { nodes: graphNodes, links: edges.map(edge => ({ ...edge })) }
  }, [nodes, edges, isImpact, distances, vertical, layoutVersion])
  const selected = nodes.find(node => node.id === selectedId) || nodes.find(node => node.id === origin)
  const hovered = nodes.find(node => node.id === hoveredId)
  const labels = new Map(graph.nodes.map(node => [node.id, node.service || node.id]))
  const connectionCount = hovered ? edges.filter(edge => edge.source === hovered.id || edge.target === hovered.id).length : 0
  const pulse = motion && isImpact && nodes.length <= 20

  function incidentRole(node) {
    if (!['service', 'database'].includes(node.type)) return null
    if (node.id === origin) return 'origin'
    if (affectedServices?.includes(node.service || node.id)) return 'affected'
    return distances.has(node.id) ? 'potential' : null
  }

  useEffect(() => {
    if (!frame.current) return
    const observer = new ResizeObserver(([entry]) => {
      setWidth(Math.floor(entry.contentRect.width))
    })
    observer.observe(frame.current)
    return () => observer.disconnect()
  }, [graph.nodes.length])

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setMotion(!query.matches)
    update()
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])

  const fit = useCallback(() => {
    if (width > 0 && force.current) fitGraph(force.current, width, height)
  }, [width, height])

  const attachGraph = useCallback(instance => {
    force.current = instance
    if (!instance) return
    instance.d3Force('link')?.distance(116)
    instance.d3Force('charge')?.strength(-250)
  }, [])

  useEffect(() => {
    const frameId = requestAnimationFrame(fit)
    return () => cancelAnimationFrame(frameId)
  }, [fit, data])

  function fitAfterInitialLayout() {
    if (fittedLayout.current === data) return
    fittedLayout.current = data
    fit()
  }

  useEffect(() => {
    if (!focusSelection) return
    const target = document.getElementById('graph-reference')
    target?.focus({ preventScroll: true })
    target?.scrollIntoView({ block: 'nearest' })
  }, [focusSelection, referenceNode])

  function zoomBy(factor) {
    const zoom = force.current?.zoom()
    if (Number.isFinite(zoom)) force.current.zoom(Math.max(minZoom, Math.min(maxGraphZoom, zoom * factor)))
  }

  function resetLayout() {
    setHoveredId(null)
    setLayoutVersion(version => version + 1)
  }

  if (!graph.nodes.length) return <p className="panel-empty">Upload incident data to populate the graph.</p>

  return (
    <>
      {dependencies.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2" role="group" aria-label="Graph view">
          {hasImpact && <Button size="sm" variant={isImpact ? 'default' : 'outline'} aria-pressed={isImpact} onClick={() => setScope('impact')}>Impact path</Button>}
          <Button size="sm" variant={activeScope === 'dependencies' ? 'default' : 'outline'} aria-pressed={activeScope === 'dependencies'} onClick={() => setScope('dependencies')}>Dependency graph</Button>
          <Button size="sm" variant={activeScope === 'all' ? 'default' : 'outline'} aria-pressed={activeScope === 'all'} onClick={() => setScope('all')}>All relationships</Button>
        </div>
      )}
      {dependencies.length > 0 && (
        <p id="graph-direction" className="mb-2 text-xs text-muted-foreground">
          {isImpact ? 'Arrows follow impact from the origin to its callers.' : 'Dependency arrows point from caller to target.'}
        </p>
      )}
      <div className="graph-frame" ref={frame} role="region" aria-label="Incident relationship graph" aria-describedby={dependencies.length ? 'graph-direction' : undefined} style={{ height: height + 2 }}>
        {origin && (
          <ul className="graph-legend" aria-label="Node status legend">
            {Object.entries(legend).map(([role, item]) => (
              <li key={role}><Badge tone={item.tone}><span className="status-dot" />{item.label}</Badge></li>
            ))}
          </ul>
        )}
        {width > 0 && (
          <ForceGraph
            key={layoutVersion}
            ref={attachGraph}
            width={width}
            height={height}
            graphData={data}
            minZoom={minZoom}
            maxZoom={maxGraphZoom}
            nodeRelSize={nodeRadius + graphInset}
            nodeLabel={() => ''}
            linkLabel={() => ''}
            nodePointerAreaPaint={paintNodeTarget}
            enableNodeDrag={!isImpact}
            linkColor={edge => edge.rel === 'depends_on' ? '#98b9ff' : '#7185a6'}
            linkWidth={edge => edge.rel === 'depends_on' ? 1.8 : 1}
            linkDirectionalArrowLength={9}
            linkDirectionalArrowRelPos={0.94}
            linkCanvasObjectMode={() => 'after'}
            linkCanvasObject={(edge, context, scale) => drawGraphEdgeLabel(edge, context, scale, relationshipLabel(edge, isImpact))}
            warmupTicks={isImpact ? 0 : 36}
            cooldownTicks={isImpact ? 1 : 75}
            autoPauseRedraw={!pulse}
            onEngineStop={fitAfterInitialLayout}
            onNodeDrag={() => { fittedLayout.current = data }}
            onNodeClick={node => setSelectedId(node.id)}
            onNodeHover={node => setHoveredId(node?.id ?? null)}
            nodeCanvasObject={(node, context, scale) => drawGraphNode(node, context, scale, {
              role: incidentRole(node),
              selected: selected?.id === node.id,
              hovered: hovered?.id === node.id,
              pulse,
            })}
          />
        )}
        <div className="graph-controls" role="group" aria-label="Graph camera">
          <Button size="icon" variant="ghost" title="Zoom in" aria-label="Zoom in" onClick={() => zoomBy(1.25)}><Plus /></Button>
          <Button size="icon" variant="ghost" title="Zoom out" aria-label="Zoom out" onClick={() => zoomBy(0.8)}><Minus /></Button>
          <Button size="icon" variant="ghost" title="Fit to screen" aria-label="Fit to screen" onClick={fit}><Maximize /></Button>
          <Button size="icon" variant="ghost" title="Reset layout" aria-label="Reset layout" onClick={resetLayout}><RotateCcw /></Button>
        </div>
      </div>
      <p className="graph-inspection">
        {hovered ? <><strong>{hovered.service || hovered.id}</strong> · {hovered.type || 'entity'} · {connectionCount} {connectionCount === 1 ? 'connection' : 'connections'}</> : 'Drag to pan. Select a node to see its relationships and source records.'}
      </p>
      <details className="graph-list">
        <summary>Inspect graph nodes</summary>
        <div className="service-list pt-3" role="group" aria-label="Graph nodes">
          {nodes.map(node => (
            <button type="button" className="service-chip" key={node.id} onClick={() => setSelectedId(node.id)} aria-pressed={selected?.id === node.id}>
              {node.service || node.id}
            </button>
          ))}
        </div>
      </details>
      {selected && (
        <SelectedNode node={selected} role={incidentRole(selected)} labels={labels} impact={isImpact}
          edges={edges.filter(edge => edge.source === selected.id || edge.target === selected.id)} />
      )}
      {sessionId && selected && ['service', 'database'].includes(selected.type) && (
        <IncidentImpact key={`${selected.id}/${evidenceRevision || ''}`} sessionId={sessionId} service={selected.service || selected.id} affectedServices={affectedServices} onObservation={observationId => onObservation(observationId, selected.id)} />
      )}
    </>
  )
}
