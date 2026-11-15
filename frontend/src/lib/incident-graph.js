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
}

export const relationshipLabel = (edge, impact = false) => {
  if (edge.rel === 'depends_on') return impact ? 'caller' : 'depends on'
  return edge.rel?.replaceAll('_', ' ') || 'related to'
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

export function fitGraph(graph, width, height) {
  const bounds = graph.getGraphBbox()
  if (!bounds) return
  const [left, right] = bounds.x
  const [top, bottom] = bounds.y
  if (![left, right, top, bottom].every(Number.isFinite)) return
  // Keep room for the labels, legend and controls around the node bounds.
  const side = Math.min(90, width * 0.26)
  const vertical = Math.min(80, height * 0.24)
  graph.zoom(Math.min(1.3, (width - side * 2) / Math.max(1, right - left), (height - vertical * 2) / Math.max(1, bottom - top)))
  graph.centerAt((left + right) / 2, (top + bottom) / 2)
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

export function paintNodeTarget(node, color, ctx) {
  circle(ctx, node.x, node.y, nodeRadius + graphInset)
  ctx.fillStyle = color
  ctx.fill()
}

export function drawGraphNode(node, ctx, scale, { role, selected, hovered, pulse }) {
  const color = nodeColors[role] || nodeColors[node.type] || nodeColors.service
  ctx.save()
  if (role === 'origin') {
    const progress = pulse ? (1 + Math.sin(performance.now() / 520)) / 2 : 0
    circle(ctx, node.x, node.y, nodeRadius + graphInset + progress * 4)
    ctx.globalAlpha = 0.55 - progress * 0.3
    ctx.strokeStyle = color
    ctx.lineWidth = 1.5 / scale
    ctx.stroke()
    ctx.globalAlpha = 1
  }
  circle(ctx, node.x, node.y, nodeRadius)
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

  const fullLabel = node.service || node.id
  const label = fullLabel.length > 22 ? fullLabel.slice(0, 21) + '…' : fullLabel
  ctx.font = `600 ${12 / scale}px Inter, sans-serif`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  ctx.strokeStyle = '#131925'
  ctx.lineWidth = 3 / scale
  ctx.strokeText(label, node.x, node.y + nodeRadius + 7 / scale, 146 / scale)
  ctx.fillStyle = '#edf2fc'
  ctx.fillText(label, node.x, node.y + nodeRadius + 7 / scale, 146 / scale)
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
