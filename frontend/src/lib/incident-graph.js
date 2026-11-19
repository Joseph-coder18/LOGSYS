export const nodeRadius = 17
export const graphInset = 4
export const maxGraphZoom = 3
const nodeColors = {
  origin: '#ff9ca7',
  affected: '#f8ca81',
  potential: '#d7b8ff',
  service: '#98b9ff',
  database: '#8dd9ec',
  file: '#bfc0ff',
  error: '#ff9ca7',
  ObservationLog: '#a78bfa',
  Deployment: '#f43f5e',
  ConfigChange: '#fb923c',
  Metric: '#38bdf8',
  Screenshot: '#d7b8ff',
}

export const edgeColors = {
  depends_on: '#98b9ff',
  propagates_to: '#818cf8',
  precedes: '#f59e0b',
  supports: '#10b981',
  contradicts: '#ef4444',
  has_observation: '#64748b',
}

export const relationshipLabel = (edge, impact = false) => {
  if (edge.rel === 'depends_on') return impact ? 'caller' : 'depends on'
  if (edge.rel === 'supports') return 'shared request'
  if (edge.rel === 'contradicts') return 'conflicting readings'
  if (edge.rel === 'propagates_to') return 'request observed at target'
  return edge.rel?.replaceAll('_', ' ') || 'related to'
}

const graphTime = node => node.utc_timestamp || node.interval_start

const modalityNames = { log: 'Log', metrics: 'Metrics', change: 'Change', image: 'Screenshot' }

export function graphNodeTitle(node) {
  if (!node.observation_id) return node.service || node.id
  const kind = modalityNames[node.modality] || node.type || 'Record'
  const time = node.interval_start && node.interval_end ? `${node.interval_start.slice(11, 19)}–${node.interval_end.slice(11, 19)} UTC (range)` : node.utc_timestamp ? `${node.utc_timestamp.slice(11, 19)} UTC` : 'Time unknown'
  return `${node.service || 'Unattributed'} · ${kind} · ${time}`
}

export function graphNodeSource(node) {
  if (!node.observation_id) return node.type || 'Entity'
  return `${node.source_file} · ${node.modality === 'log' ? 'line' : 'record'} ${node.source_position}${node.source_label ? ` · ${node.source_label}` : ''}`
}

export function layoutTemporalNodes(nodes, availableWidth = 900) {
  const instants = [...new Set(nodes.map(graphTime).filter(Boolean))].sort()
  const columns = new Map(instants.map((timestamp, index) => [timestamp, index * 80]))
  const services = new Map()
  const groups = new Map()
  for (const node of nodes) {
    const service = node.service || 'Unattributed'
    const earliest = graphTime(node) || '\uffff'
    if (!services.has(service) || earliest < services.get(service)) services.set(service, earliest)
    const key = JSON.stringify([service, node.modality])
    if (!groups.has(key)) groups.set(key, { service, modality: node.modality, nodes: [] })
    groups.get(key).nodes.push(node)
  }
  const kinds = ['change', 'metrics', 'log', 'image']
  const lanes = [...groups.values()].sort((a, b) =>
    services.get(a.service).localeCompare(services.get(b.service)) || a.service.localeCompare(b.service) ||
    kinds.indexOf(a.modality) - kinds.indexOf(b.modality))
  let top = 0
  let end = Math.max(0, instants.length - 1) * 80
  const unknownCount = Math.max(0, ...lanes.map(lane => lane.nodes.filter(node => !graphTime(node)).length))
  const span = Math.max(80, end + (unknownCount ? (unknownCount + 1) * 80 : 0))
  const scaleHint = Math.min(1.3, Math.max(80, availableWidth) / span)
  for (const lane of lanes) {
    const stacks = new Map()
    let unknown = 0
    lane.nodes.sort((a, b) => graphNodeSource(a).localeCompare(graphNodeSource(b)) || a.id.localeCompare(b.id))
    for (const node of lane.nodes) {
      const x = columns.get(graphTime(node)) ?? (instants.length + 1 + unknown++) * 80
      if (!stacks.has(x)) stacks.set(x, [])
      stacks.get(x).push(node)
      end = Math.max(end, x)
    }
    const gap = Math.max(56, 28 / scaleHint)
    const height = Math.max(96, 52 / scaleHint, Math.max(...[...stacks.values()].map(stack => stack.length)) * gap)
    lane.y = top + height / 2
    lane.height = height
    for (const [x, stack] of stacks) {
      stack.forEach((node, index) => {
        const y = lane.y + (index - (stack.length - 1) / 2) * gap
        Object.assign(node, { x, y, fx: x, fy: y })
      })
    }
    top += height
  }
  return { lanes, end, instants, columns, unknownStart: unknownCount ? (instants.length + 1) * 80 : null }
}

export function drawTemporalLanes(ctx, scale, layout) {
  ctx.save()
  ctx.font = `${11 / scale}px Inter, sans-serif`
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  for (const lane of layout.lanes) {
    ctx.strokeStyle = '#35435977'
    ctx.lineWidth = 1 / scale
    ctx.beginPath()
    ctx.moveTo(-12 / scale, lane.y)
    ctx.lineTo(layout.end + 24 / scale, lane.y)
    ctx.stroke()
    if (lane.height * scale >= 30) {
      ctx.fillStyle = '#d9e3f6'
      ctx.fillText(lane.service.length > 18 ? `${lane.service.slice(0, 17)}…` : lane.service, -22 / scale, lane.y - 7 / scale)
      ctx.fillStyle = '#91a1ba'
      ctx.fillText(`${modalityNames[lane.modality] || 'Records'} · ${lane.nodes.length}`, -22 / scale, lane.y + 9 / scale)
    }
  }
  ctx.textAlign = 'center'
  ctx.fillStyle = '#91a1ba'
  const stride = Math.max(1, Math.ceil(100 / (80 * scale)))
  const top = (layout.lanes[0]?.y || 0) - 45 / scale
  layout.instants.forEach((timestamp, index) => {
    if (index % stride === 0) ctx.fillText(`${timestamp.slice(11, 19)} UTC`, layout.columns.get(timestamp), top)
  })
  if (layout.unknownStart !== null) ctx.fillText('Time unknown', layout.unknownStart, top)
  ctx.restore()
}

export function shouldLabelGraphEdge(edge, scale, edgeCount, focusId) {
  const source = typeof edge.source === 'object' ? edge.source.id : edge.source
  const target = typeof edge.target === 'object' ? edge.target.id : edge.target
  const focused = focusId && (source === focusId || target === focusId)
  return scale >= 0.55 && Boolean(focused || (edgeCount <= 8 && edge.rel === 'depends_on'))
}

export function impactDistances(dependencies, origin) {
  const distances = new Map()
  if (!origin) return distances
  const callers = new Map()
  for (const { source, target } of dependencies) {
    if (!callers.has(target)) callers.set(target, [])
    callers.get(target).push(source)
  }
  distances.set(origin, 0)
  for (const [id, distance] of distances) {
    for (const caller of callers.get(id) || []) {
      if (!distances.has(caller)) distances.set(caller, distance + 1)
    }
  }
  return distances
}

export function layoutImpactNodes(nodes, distances, vertical) {
  const layers = new Map()
  for (const node of nodes) {
    const distance = distances.get(node.id)
    if (!layers.has(distance)) layers.set(distance, [])
    layers.get(distance).push(node)
  }
  for (const [distance, layer] of layers) {
    layer.sort((a, b) => (a.service || a.id).localeCompare(b.service || b.id))
    layer.forEach((node, index) => {
      const along = distance * (vertical ? 95 : 160)
      const across = (index - (layer.length - 1) / 2) * 116
      const x = vertical ? across : along
      const y = vertical ? along : across
      Object.assign(node, { x, y, fx: x, fy: y })
    })
  }
}

export function fitGraph(graph, width, height, insets = {}) {
  const bounds = graph.getGraphBbox()
  if (!bounds) return
  const [left, right] = bounds.x
  const [top, bottom] = bounds.y
  if (![left, right, top, bottom].every(Number.isFinite)) return
  // Keep room for the labels, legend and controls around the node bounds.
  const side = Math.min(90, width * 0.26)
  const vertical = Math.min(80, height * 0.24)
  const padding = { left: side, right: side, top: vertical, bottom: vertical, ...insets }
  const zoom = Math.min(1.3, Math.max(1, width - padding.left - padding.right) / Math.max(1, right - left),
    Math.max(1, height - padding.top - padding.bottom) / Math.max(1, bottom - top))
  graph.zoom(zoom)
  graph.centerAt((left + right) / 2 + (padding.right - padding.left) / (2 * zoom),
    (top + bottom) / 2 + (padding.bottom - padding.top) / (2 * zoom))
}

const iconPaths = {
  database: 'M3 5a9 3 0 1 0 18 0a9 3 0 1 0-18 0 M3 5v14a9 3 0 0 0 18 0V5 M3 12a9 3 0 0 0 18 0',
  shield: 'M12 22s7-3 7-9V5l-7-3-7 3v8c0 6 7 9 7 9Z M9 12l2 2 4-4',
  globe: 'M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0 M2 12h20 M12 2a15 15 0 0 1 0 20a15 15 0 0 1 0-20',
  service: 'M3 4h18v6H3z M3 14h18v6H3z M7 7h.01 M7 17h.01',
  file: 'M6 2h8l4 4v16H6z M14 2v5h4 M9 12h6 M9 16h6',
  error: 'M12 3 2 21h20L12 3Z M12 9v4 M12 17h.01',
}
const icons = new Map()

function nodeIcon(node) {
  const service = node.service || node.id
  if (node.type === 'database' || (node.type === 'service' && /(?:^|-)db(?:-|$)/i.test(service))) return 'database'
  if (node.type === 'service' && /auth/i.test(service)) return 'shield'
  if (node.type === 'service' && /gateway|router/i.test(service)) return 'globe'
  return node.type in iconPaths ? node.type : 'service'
}

function circle(ctx, x, y, radius) {
  ctx.beginPath()
  ctx.arc(x, y, radius, 0, Math.PI * 2)
}

export function paintNodeTarget(node, color, ctx, scale = 1) {
  circle(ctx, node.x, node.y, Math.max(nodeRadius + graphInset, 10 / scale))
  ctx.fillStyle = color
  ctx.fill()
}

export function drawGraphNode(node, ctx, scale, { role, selected, hovered, pulse, showLabel = true, dimmed = false, minRadius = 7 }) {
  const color = nodeColors[role] || nodeColors[node.type] || nodeColors.service
  const radius = Math.max(nodeRadius, minRadius / scale)
  ctx.save()
  if (dimmed) ctx.globalAlpha = 0.35
  if (role === 'origin') {
    const progress = pulse ? (1 + Math.sin(performance.now() / 520)) / 2 : 0
    circle(ctx, node.x, node.y, nodeRadius + graphInset + progress * 4)
    ctx.globalAlpha = 0.55 - progress * 0.3
    ctx.strokeStyle = color
    ctx.lineWidth = 1.5 / scale
    ctx.stroke()
    ctx.globalAlpha = 1
  }
  circle(ctx, node.x, node.y, radius)
  ctx.fillStyle = '#171e2b'
  ctx.fill()
  ctx.lineWidth = (selected || hovered ? 2.5 : 1.5) / scale
  ctx.strokeStyle = selected ? '#edf2fc' : color
  ctx.stroke()

  const icon = nodeIcon(node)
  if (!icons.has(icon)) icons.set(icon, new Path2D(iconPaths[icon]))
  ctx.save()
  ctx.translate(node.x - 9, node.y - 9)
  ctx.scale(0.75, 0.75)
  ctx.strokeStyle = color
  ctx.lineWidth = 1.75
  ctx.lineJoin = 'round'
  ctx.lineCap = 'round'
  ctx.stroke(icons.get(icon))
  ctx.restore()

  if (!showLabel) {
    ctx.restore()
    return
  }
  const fullLabel = node.observation_id
    ? `${node.modality} · ${node.utc_timestamp?.slice(11, 19) || `#${node.source_position}`}`
    : node.service || node.id
  const label = fullLabel.length > 22 ? fullLabel.slice(0, 21) + '…' : fullLabel
  ctx.font = `600 ${12 / scale}px Inter, sans-serif`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  ctx.strokeStyle = '#131925'
  ctx.lineWidth = 3 / scale
  ctx.strokeText(label, node.x, node.y + radius + 7 / scale, 146 / scale)
  ctx.fillStyle = '#edf2fc'
  ctx.fillText(label, node.x, node.y + radius + 7 / scale, 146 / scale)
  ctx.restore()
}

export function drawGraphEdgeLabel(edge, ctx, scale, label) {
  const { source, target } = edge
  if (!Number.isFinite(source.x) || !Number.isFinite(target.x)) return
  const dx = target.x - source.x
  const dy = target.y - source.y
  ctx.save()
  ctx.font = `500 ${10 / scale}px Inter, sans-serif`
  const width = ctx.measureText(label).width + 10 / scale
  if (Math.hypot(dx, dy) < nodeRadius * 2 + width + 10 / scale) {
    ctx.restore()
    return
  }
  const vertical = Math.abs(dy) > Math.abs(dx)
  const x = (source.x + target.x) / 2 + (vertical ? width / 2 + 7 / scale : 0)
  const y = (source.y + target.y) / 2 - (vertical ? 0 : 11 / scale)
  ctx.fillStyle = '#171e2b'
  ctx.strokeStyle = '#354359'
  ctx.lineWidth = 1 / scale
  ctx.beginPath()
  ctx.roundRect(x - width / 2, y - 8 / scale, width, 16 / scale, 4 / scale)
  ctx.fill()
  ctx.stroke()
  ctx.fillStyle = '#d9e3f6'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(label, x, y)
  ctx.restore()
}
