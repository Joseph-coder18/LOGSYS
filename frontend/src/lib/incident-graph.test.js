import assert from 'node:assert/strict'
import test from 'node:test'
import { fitGraph, graphNodeSource, graphNodeTitle, layoutTemporalNodes, shouldLabelGraphEdge } from './incident-graph.js'

function record(id, modality, timestamp, service = 'user-db') {
  return { id, observation_id: id, modality, utc_timestamp: timestamp, service,
    source_file: `${modality}.json`, source_position: Number(id.replace(/\D/g, '')) || 1 }
}

test('temporal layout separates modalities, preserves ordering, and stacks equal-time records', () => {
  const nodes = [
    record('metric2', 'metrics', '2024-01-15T02:31:00Z'),
    record('log1', 'log', '2024-01-15T02:30:00Z'),
    record('metric1', 'metrics', '2024-01-15T02:30:00Z'),
    record('metric3', 'metrics', '2024-01-15T02:31:00Z'),
    record('image1', 'image', null),
  ]
  const layout = layoutTemporalNodes(nodes, 420)
  const byId = new Map(nodes.map(node => [node.id, node]))
  assert.ok(byId.get('metric1').x < byId.get('metric2').x)
  assert.equal(byId.get('metric2').x, byId.get('metric3').x)
  assert.ok(Math.abs(byId.get('metric2').y - byId.get('metric3').y) >= 56)
  assert.notEqual(byId.get('log1').y, byId.get('metric1').y)
  assert.ok(byId.get('image1').x > byId.get('metric2').x)
  assert.equal(layout.unknownStart, byId.get('image1').x)
  assert.ok(nodes.every(node => node.x === node.fx && node.y === node.fy))
})

test('dense incident lane headings retain readable spacing after fitting the incident width', () => {
  const nodes = Array.from({ length: 20 }, (_, i) => record(`metric${i}`, 'metrics', `2024-01-15T02:${String(10 + i).padStart(2, '0')}:00Z`))
  nodes.push(record('log1', 'log', nodes[0].utc_timestamp), record('change1', 'change', nodes[1].utc_timestamp))
  const layout = layoutTemporalNodes(nodes, 410)
  const scale = 410 / layout.end
  for (let i = 1; i < layout.lanes.length; i++) {
    assert.ok((layout.lanes[i].y - layout.lanes[i - 1].y) * scale >= 50)
  }
})

test('dense graph edge labels appear for focused records while small dependency graphs stay labelled', () => {
  const edge = { source: 'a', target: 'b', rel: 'observed_in' }
  assert.equal(shouldLabelGraphEdge(edge, 1, 70, null), false)
  assert.equal(shouldLabelGraphEdge(edge, 1, 70, 'a'), true)
  assert.equal(shouldLabelGraphEdge(edge, 0.3, 70, 'a'), false)
  assert.equal(shouldLabelGraphEdge({ source: { id: 'a' }, target: { id: 'b' }, rel: 'precedes' }, 1, 70, 'b'), true)
  assert.equal(shouldLabelGraphEdge({ ...edge, rel: 'depends_on' }, 1, 2, null), true)
})

test('the record browser distinguishes records from the same service by time and source', () => {
  const first = record('metric1', 'metrics', '2024-01-15T02:30:00Z')
  const second = record('metric2', 'metrics', '2024-01-15T02:31:00Z')
  assert.notEqual(graphNodeTitle(first), graphNodeTitle(second))
  assert.notEqual(graphNodeSource(first), graphNodeSource(second))
  assert.match(graphNodeTitle(record('image1', 'image', null)), /Time unknown/)
})

test('fitting reserves space for lane headings and keeps nodes within the padded viewport', () => {
  let zoom, center
  const graph = {
    getGraphBbox: () => ({ x: [0, 1520], y: [0, 800] }),
    zoom: value => { zoom = value },
    centerAt: (x, y) => { center = { x, y } },
  }
  fitGraph(graph, 620, 460, { left: 160, right: 45, top: 90, bottom: 65 })
  assert.ok(310 + (0 - center.x) * zoom >= 159)
  assert.ok(310 + (1520 - center.x) * zoom <= 576)
  assert.ok(230 + (0 - center.y) * zoom >= 89)
  assert.ok(230 + (800 - center.y) * zoom <= 396)
})

test('dated screenshot ranges use their start bound without becoming point readings', () => {
  const range = {...record('image2', 'image', null), interval_start:'2024-01-15T02:27:00Z', interval_end:'2024-01-15T02:33:30Z'}
  const point = record('metric4','metrics','2024-01-15T02:30:00Z')
  const unknown = record('image3','image',null)
  layoutTemporalNodes([point, range, unknown], 900)
  assert.ok(range.x < point.x && point.x < unknown.x)
  assert.match(graphNodeTitle(range), /02:27:00–02:33:30 UTC \(range\)/)
  assert.equal(range.utc_timestamp, null)
})
