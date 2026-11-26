import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { IconMaximize as Maximize, IconMinus as Minus, IconPlus as Plus, IconRotate as RotateCcw } from '@tabler/icons-react'
import ForceGraph from 'react-force-graph-2d'
import IncidentImpact from './IncidentImpact'
import { Badge, Card } from '@/components/ui/surfaces'
import { Button } from '@/components/ui/button'
import {
  drawGraphEdgeLabel,
  drawGraphNode,
  drawTemporalLanes,
  edgeColors,
  fitGraph,
  graphInset,
  graphNodeSource,
  graphNodeTitle,
  impactDistances,
  layoutImpactNodes,
  layoutTemporalNodes,
  maxGraphZoom,
  nodeRadius,
  paintNodeTarget,
  relationshipLabel,
  shouldLabelGraphEdge,
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

function SelectedNode({ node, role, edges, labels, impact, onObservation }) {
  return (
    <Card className="side-panel graph-selection" id="graph-reference" tabIndex={-1}>
      <div className="side-panel-title flex-wrap">
        <h2>{node.observation_id ? 'Selected record' : 'Selected node'} · <span className="font-mono">{graphNodeTitle(node)}</span></h2>
        <div className="flex flex-wrap gap-1.5">
          <Badge>{node.type || 'Entity'}</Badge>
          {role && <Badge tone={legend[role].tone}>{legend[role].label}</Badge>}
        </div>
      </div>
      {node.observation_id && <div className="mb-3 grid gap-2 text-sm">
        <p>{node.summary}</p>
        <p className="text-xs text-muted-foreground">
          {node.source_file} · {node.source_label || `${node.modality === 'log' ? 'line' : 'record'} ${node.source_position}`}
          {' · '}{node.interval_start && node.interval_end ? `${node.interval_start.slice(0, 19).replace('T', ' ')} → ${node.interval_end.slice(0, 19).replace('T', ' ')} UTC (range)` : node.utc_timestamp ? `${node.utc_timestamp.slice(0, 19).replace('T', ' ')} UTC` : 'Time unknown'}
        </p>
        <Button size="sm" variant="outline" className="w-fit" onClick={() => onObservation(node.observation_id)}>
          Open source in timeline
        </Button>
      </div>}
      {edges.length ? (
        <ul className="graph-relationships grid gap-2 text-xs">
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
  const [nodeSearch, setNodeSearch] = useState('')
  const [motion, setMotion] = useState(false)
  const dependencies = useMemo(() => graph.edges.filter(edge => edge.rel === 'depends_on'), [graph])
  const temporalEdges = useMemo(() => graph.edges.filter(edge => ['precedes', 'supports', 'contradicts', 'propagates_to'].includes(edge.rel)), [graph])
  const origin = serviceNode(graph, suspectedOrigin)?.id
  const distances = useMemo(() => impactDistances(dependencies, origin), [dependencies, origin])
  const hasImpact = distances.size > 1
  const activeScope = scope === 'all' || scope === 'temporal' || (scope === 'impact' && hasImpact) || (scope === 'dependencies' && dependencies.length)
    ? scope : hasImpact ? 'impact' : temporalEdges.length ? 'temporal' : dependencies.length ? 'dependencies' : 'all'
  const isImpact = activeScope === 'impact'
  const isTemporal = activeScope === 'temporal'
  const vertical = width < 510
  const height = isTemporal || activeScope === 'all' ? 460 : vertical ? 418 : 328
  const { nodes, edges } = useMemo(() => {
    if (activeScope === 'all') return { nodes: graph.nodes, edges: graph.edges }
    if (activeScope === 'temporal') {
      const endpoints = new Set(temporalEdges.flatMap(edge => [edge.source, edge.target]))
      return { nodes: graph.nodes.filter(node => node.observation_id || endpoints.has(node.id)), edges: temporalEdges }
    }
    if (isImpact) {
      // Use caller propagation direction in the impact view and keep source provenance.
      const edges = dependencies
        .filter(edge => distances.get(edge.source) === distances.get(edge.target) + 1)
        .map(edge => ({ ...edge, source: edge.target, target: edge.source }))
      return { nodes: graph.nodes.filter(node => distances.has(node.id)), edges }
    }
    const endpoints = new Set(dependencies.flatMap(edge => [edge.source, edge.target]))
    return { nodes: graph.nodes.filter(node => endpoints.has(node.id)), edges: dependencies }
  }, [graph, activeScope, isImpact, dependencies, distances, temporalEdges])

  // The renderer mutates nodes and links while simulating.
  const data = useMemo(() => {
    const graphNodes = nodes.map(node => ({ ...node }))
    if (isImpact) layoutImpactNodes(graphNodes, distances, vertical)
    const timeline = isTemporal ? layoutTemporalNodes(graphNodes, width - Math.min(160, width * 0.35) - 45) : null
    return { nodes: graphNodes, links: edges.map(edge => ({ ...edge })), timeline }
  }, [nodes, edges, isImpact, isTemporal, distances, vertical, layoutVersion, width])
  const selected = selectedId === null ? undefined : nodes.find(node => node.id === selectedId) || nodes.find(node => node.id === origin)
  const hovered = nodes.find(node => node.id === hoveredId)
  const labels = new Map(graph.nodes.map(node => [node.id, `${graphNodeTitle(node)}${node.observation_id ? ` · ${graphNodeSource(node)}` : ''}`]))
  const connectionCount = hovered ? edges.filter(edge => edge.source === hovered.id || edge.target === hovered.id).length : 0
  const pulse = motion && isImpact && nodes.length <= 20
  const focusId = hovered?.id || selected?.id
  const neighbors = new Set([focusId])
  for (const edge of edges) {
    if (edge.source === focusId) neighbors.add(edge.target)
    if (edge.target === focusId) neighbors.add(edge.source)
  }
  const filteredNodes = nodes.filter(node => `${graphNodeTitle(node)} ${graphNodeSource(node)} ${node.summary || ''}`
    .toLowerCase().includes(nodeSearch.trim().toLowerCase()))
    .sort((a, b) => (a.service || '').localeCompare(b.service || '') ||
      (a.utc_timestamp || '\uffff').localeCompare(b.utc_timestamp || '\uffff') || graphNodeSource(a).localeCompare(graphNodeSource(b)))
  const dense = nodes.length > 12

  function edgeIsFocused(edge) {
    return (edge.source.id || edge.source) === focusId || (edge.target.id || edge.target) === focusId
  }

  function clearSelection() {
    setSelectedId(null)
    setHoveredId(null)
  }

  function selectNode(node, center = false) {
    if (selected?.id === node.id) {
      clearSelection()
      return
    }
    setSelectedId(node.id)
    setHoveredId(null)
    const rendered = data.nodes.find(item => item.id === node.id)
    if (center && Number.isFinite(rendered?.x) && Number.isFinite(rendered?.y)) {
      force.current?.centerAt(rendered.x, rendered.y, 250)
    }
  }

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
    if (width > 0 && force.current) fitGraph(force.current, width, height,
      isTemporal ? { left: Math.min(160, width * 0.35), right: 45, top: 90, bottom: 65 } : {})
  }, [width, height, isTemporal])

  const attachGraph = useCallback(instance => {
    force.current = instance
    if (!instance) return
    instance.d3Force('link')?.distance(170)
    instance.d3Force('charge')?.strength(-650)
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
    clearSelection()
    setNodeSearch('')
    setLayoutVersion(version => version + 1)
  }

  if (!graph.nodes.length) return <p className="panel-empty">Upload incident data to populate the graph.</p>

  return (
    <>
      {(dependencies.length > 0 || temporalEdges.length > 0 || graph.nodes.some(node => node.observation_id)) && (
        <div className="mb-2 flex flex-wrap gap-2" role="group" aria-label="Graph view">
          {hasImpact && <Button size="sm" variant={isImpact ? 'default' : 'outline'} aria-pressed={isImpact} onClick={() => setScope('impact')}>Impact path</Button>}
          {graph.nodes.some(node => node.observation_id) && <Button size="sm" variant={activeScope === 'temporal' ? 'default' : 'outline'} aria-pressed={activeScope === 'temporal'} onClick={() => setScope('temporal')}>Temporal evidence ({temporalEdges.length})</Button>}
          {dependencies.length > 0 && <Button size="sm" variant={activeScope === 'dependencies' ? 'default' : 'outline'} aria-pressed={activeScope === 'dependencies'} onClick={() => setScope('dependencies')}>Dependency graph</Button>}
          <Button size="sm" variant={activeScope === 'all' ? 'default' : 'outline'} aria-pressed={activeScope === 'all'} onClick={() => setScope('all')}>All relationships</Button>
        </div>
      )}
      <p id="graph-direction" className="mb-2 text-xs text-muted-foreground">
        {isTemporal ? 'Records run left to right by UTC start, grouped by service and input type. Overlapping ranges do not establish event order.' : isImpact ? 'Arrows follow impact from the origin to its callers.' : activeScope === 'all' ? 'Explore source records and service relationships. Hover or select to inspect a connection.' : 'Dependency arrows point from caller to target.'}
      </p>
      <div className="graph-frame" ref={frame} role="region" aria-label="Incident relationship graph" aria-describedby="graph-direction" style={{ height: height + 2 }}>
        {activeScope === 'temporal' ? (
          <ul className="graph-legend" aria-label="Temporal relationship legend">
            {['precedes', 'supports', 'contradicts', 'propagates_to'].map(relation => (
              <li key={relation}><Badge><span className="status-dot" style={{ backgroundColor: edgeColors[relation] }} />{relationshipLabel({ rel: relation })}</Badge></li>
            ))}
          </ul>
        ) : origin && (
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
            enableNodeDrag={!isImpact && !isTemporal}
            linkColor={edge => `${edgeColors[edge.rel] || '#7185a6'}${focusId && dense && !edgeIsFocused(edge) ? '30' : 'c0'}`}
            linkWidth={edge => edgeIsFocused(edge) ? 2 : edge.rel === 'depends_on' ? 1.8 : 0.8}
            linkDirectionalArrowLength={edge => focusId && dense && !edgeIsFocused(edge) ? 0 : 7}
            linkDirectionalArrowRelPos={0.94}
            linkCanvasObjectMode={() => 'after'}
            linkCanvasObject={(edge, context, scale) => {
              const labelFocus = hoveredId || (selected?.observation_id ? selected.id : null)
              if (shouldLabelGraphEdge(edge, scale, edges.length, labelFocus)) {
                drawGraphEdgeLabel(edge, context, scale, relationshipLabel(edge, isImpact))
              }
            }}
            onRenderFramePre={(context, scale) => { if (data.timeline) drawTemporalLanes(context, scale, data.timeline) }}
            warmupTicks={isImpact || isTemporal ? 0 : 36}
            cooldownTicks={isImpact || isTemporal ? 1 : 75}
            autoPauseRedraw={!pulse}
            onEngineStop={fitAfterInitialLayout}
            onNodeDrag={() => { fittedLayout.current = data }}
            onNodeClick={node => selectNode(node)}
            onBackgroundClick={clearSelection}
            onNodeHover={node => setHoveredId(node?.id ?? null)}
            nodeCanvasObject={(node, context, scale) => drawGraphNode(node, context, scale, {
              role: incidentRole(node),
              selected: selected?.id === node.id,
              hovered: hovered?.id === node.id,
              pulse,
              showLabel: selected?.id === node.id || hoveredId === node.id ||
                (node.observation_id ? scale >= (dense ? 1.15 : 0.7) : ['service', 'database'].includes(node.type) || scale >= 0.65),
              dimmed: Boolean(focusId && dense && !neighbors.has(node.id)),
              minRadius: isTemporal && vertical ? 3 : 7,
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
        {hovered ? <><strong>{graphNodeTitle(hovered)}</strong> · {graphNodeSource(hovered)} · {connectionCount} {connectionCount === 1 ? 'connection' : 'connections'}{hovered.summary && <span className="graph-hover-summary">{hovered.summary}</span>}</> : 'Select a node for details. Select it again or click empty canvas to clear. Drag to pan; zoom in for labels.'}
      </p>
      <details className="graph-list">
        <summary>Browse records and entities ({nodes.length})</summary>
        <input className="graph-node-search" type="search" aria-label="Find graph records or entities" placeholder="Find a service, time, or source" value={nodeSearch} onChange={event => setNodeSearch(event.target.value)} />
        <div className="graph-node-browser" role="group" aria-label="Graph nodes">
          {filteredNodes.map(node => (
            <button type="button" className="graph-node-entry" key={node.id} onClick={() => selectNode(node, true)} aria-pressed={selected?.id === node.id}>
              <strong>{graphNodeTitle(node)}</strong>
              <small>{graphNodeSource(node)}</small>
            </button>
          ))}
          {!filteredNodes.length && <p className="p-3">No matching records or entities.</p>}
        </div>
      </details>
      {selected && (
        <SelectedNode node={selected} role={incidentRole(selected)} labels={labels} impact={isImpact}
          onObservation={observationId => onObservation(observationId, selected.id)}
          edges={edges.filter(edge => edge.source === selected.id || edge.target === selected.id)} />
      )}
      {sessionId && selected && ['service', 'database'].includes(selected.type) && (
        <IncidentImpact key={`${selected.id}/${evidenceRevision || ''}`} sessionId={sessionId} service={selected.service || selected.id} affectedServices={affectedServices} onObservation={observationId => onObservation(observationId, selected.id)} />
      )}
    </>
  )
}
