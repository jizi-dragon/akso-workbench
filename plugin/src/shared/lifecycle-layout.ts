import * as dagre from '@dagrejs/dagre'

export interface LayoutNode {
  id: string
  width: number
  height: number
}

export interface LayoutEdge {
  id: string
  from: string
  to: string
}

export interface LayoutPoint {
  x: number
  y: number
}

export interface LaidOutNode extends LayoutNode {
  x: number
  y: number
}

export interface LaidOutEdge {
  id: string
  from: string
  to: string
  points: LayoutPoint[]
}

export interface LayoutResult {
  nodes: Map<string, LaidOutNode>
  edges: Map<string, LaidOutEdge>
  width: number
  height: number
}

/**
 * 用 dagre 做分层布局（TB）。只算坐标，不碰 drawio 样式；
 * 自环不参与排序（dagre 不处理自环），由调用方单独画。
 */
export function layoutLayered(nodes: LayoutNode[], edges: LayoutEdge[]): LayoutResult {
  const graph = new dagre.graphlib.Graph({ multigraph: true, directed: true })
  graph.setGraph({
    rankdir: 'TB',
    nodesep: 64,
    ranksep: 92,
    edgesep: 28,
    marginx: 40,
    marginy: 40,
  })
  graph.setDefaultEdgeLabel(() => ({}))

  for (const node of nodes) {
    graph.setNode(node.id, { width: node.width, height: node.height })
  }
  const acyclic = new Set<string>()
  for (const edge of edges) {
    if (edge.from === edge.to) continue
    graph.setEdge(edge.from, edge.to, {}, edge.id)
    acyclic.add(edge.id)
  }
  dagre.layout(graph)

  const laidOut = new Map<string, LaidOutNode>()
  for (const node of nodes) {
    const placed = graph.node(node.id)
    laidOut.set(node.id, {
      ...node,
      x: placed.x - node.width / 2,
      y: placed.y - node.height / 2,
    })
  }

  const laidOutEdges = new Map<string, LaidOutEdge>()
  for (const edge of edges) {
    if (!acyclic.has(edge.id)) {
      laidOutEdges.set(edge.id, { ...edge, points: [] })
      continue
    }
    const placed = graph.edge({ v: edge.from, w: edge.to, name: edge.id })
    laidOutEdges.set(edge.id, {
      ...edge,
      points: (placed.points ?? []).map((point: LayoutPoint) => ({ x: point.x, y: point.y })),
    })
  }

  let maxX = 0
  let maxY = 0
  for (const node of laidOut.values()) {
    maxX = Math.max(maxX, node.x + node.width)
    maxY = Math.max(maxY, node.y + node.height)
  }
  return { nodes: laidOut, edges: laidOutEdges, width: maxX + 80, height: maxY + 120 }
}
