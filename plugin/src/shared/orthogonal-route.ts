export interface RouteNode {
  id: string
  x: number
  y: number
  width: number
  height: number
}

export interface RouteEdge {
  id: string
  from: string
  to: string
}

export interface RoutedEdge {
  points: Array<{ x: number; y: number }>
  exitX: number
  exitY: number
  entryX: number
  entryY: number
}

interface Point { x: number; y: number }

const CELL = 20

class Heap {
  private items: Array<{ key: number; value: number }> = []
  push(key: number, value: number): void {
    this.items.push({ key, value })
    let index = this.items.length - 1
    while (index > 0) {
      const parent = (index - 1) >> 1
      const current = this.items[index]
      const up = this.items[parent]
      if (current === undefined || up === undefined || up.key <= current.key) break
      this.items[index] = up
      this.items[parent] = current
      index = parent
    }
  }
  pop(): number | undefined {
    const first = this.items[0]
    const last = this.items.pop()
    if (first === undefined || last === undefined) return undefined
    if (this.items.length > 0) {
      this.items[0] = last
      let index = 0
      for (;;) {
        const left = index * 2 + 1
        const right = left + 1
        let smallest = index
        const a = this.items[smallest]
        const b = this.items[left]
        const c = this.items[right]
        if (b !== undefined && a !== undefined && b.key < a.key) smallest = left
        const d = this.items[smallest]
        if (c !== undefined && d !== undefined && c.key < d.key) smallest = right
        if (smallest === index) break
        const tmp = this.items[index]
        this.items[index] = this.items[smallest] as { key: number; value: number }
        this.items[smallest] = tmp as { key: number; value: number }
        index = smallest
      }
    }
    return first.value
  }
  get size(): number {
    return this.items.length
  }
}

function port(node: RouteNode, side: 'top' | 'bottom' | 'left' | 'right'): Point {
  if (side === 'top') return { x: node.x + node.width / 2, y: node.y }
  if (side === 'bottom') return { x: node.x + node.width / 2, y: node.y + node.height }
  if (side === 'left') return { x: node.x, y: node.y + node.height / 2 }
  return { x: node.x + node.width, y: node.y + node.height / 2 }
}

function sideFor(from: RouteNode, to: RouteNode): { out: 'top' | 'bottom' | 'left' | 'right'; into: 'top' | 'bottom' | 'left' | 'right' } {
  const dx = to.x + to.width / 2 - (from.x + from.width / 2)
  const dy = to.y + to.height / 2 - (from.y + from.height / 2)
  if (Math.abs(dy) >= Math.abs(dx)) return dy >= 0 ? { out: 'bottom', into: 'top' } : { out: 'top', into: 'bottom' }
  return dx >= 0 ? { out: 'right', into: 'left' } : { out: 'left', into: 'right' }
}

/** 简易栅格正交路由：节点外扩 6px 作为障碍，A* 带转弯代价，保证不穿节点。 */
export function routeEdges(nodes: RouteNode[], edges: RouteEdge[]): Map<string, RoutedEdge> {
  const result = new Map<string, RoutedEdge>()
  if (nodes.length === 0) return result
  const byId = new Map(nodes.map((node) => [node.id, node]))
  let maxX = 0
  let maxY = 0
  for (const node of nodes) {
    maxX = Math.max(maxX, node.x + node.width)
    maxY = Math.max(maxY, node.y + node.height)
  }
  const cols = Math.ceil((maxX + 120) / CELL) + 2
  const rows = Math.ceil((maxY + 120) / CELL) + 2
  const blocked = new Uint8Array(cols * rows)
  for (const node of nodes) {
    const x1 = Math.floor((node.x - 6) / CELL)
    const y1 = Math.floor((node.y - 6) / CELL)
    const x2 = Math.ceil((node.x + node.width + 6) / CELL)
    const y2 = Math.ceil((node.y + node.height + 6) / CELL)
    for (let y = Math.max(0, y1); y <= Math.min(rows - 1, y2); y += 1) {
      for (let x = Math.max(0, x1); x <= Math.min(cols - 1, x2); x += 1) blocked[y * cols + x] = 1
    }
  }
  const indexOf = (x: number, y: number): number => y * cols + x
  const toCell = (point: Point): { x: number; y: number } => ({
    x: Math.max(0, Math.min(cols - 1, Math.round(point.x / CELL))),
    y: Math.max(0, Math.min(rows - 1, Math.round(point.y / CELL))),
  })
  const dirs = [
    { dx: 0, dy: 1 },
    { dx: 0, dy: -1 },
    { dx: 1, dy: 0 },
    { dx: -1, dy: 0 },
  ]

  for (const edge of edges) {
    const from = byId.get(edge.from)
    const to = byId.get(edge.to)
    if (from === undefined || to === undefined) continue
    const sides = sideFor(from, to)
    const startPort = port(from, sides.out)
    const endPort = port(to, sides.into)
    const start = toCell({ x: startPort.x + (sides.out === 'right' ? CELL : sides.out === 'left' ? -CELL : 0), y: startPort.y + (sides.out === 'bottom' ? CELL : sides.out === 'top' ? -CELL : 0) })
    const end = toCell({ x: endPort.x + (sides.into === 'right' ? CELL : sides.into === 'left' ? -CELL : 0), y: endPort.y + (sides.into === 'bottom' ? CELL : sides.into === 'top' ? -CELL : 0) })
    // 端口附近放行，避免起终点本身落在障碍格
    for (let dy = -1; dy <= 1; dy += 1) for (let dx = -1; dx <= 1; dx += 1) {
      const sx = start.x + dx; const sy = start.y + dy
      const ex = end.x + dx; const ey = end.y + dy
      if (sx >= 0 && sx < cols && sy >= 0 && sy < rows) blocked[indexOf(sx, sy)] = 0
      if (ex >= 0 && ex < cols && ey >= 0 && ey < rows) blocked[indexOf(ex, ey)] = 0
    }
    const startKey = indexOf(start.x, start.y)
    const endKey = indexOf(end.x, end.y)
    const INF = Number.POSITIVE_INFINITY
    const dist = new Float64Array(cols * rows * 5).fill(INF)
    const prev = new Int32Array(cols * rows * 5).fill(-1)
    const heap = new Heap()
    dist[startKey * 5 + 4] = 0
    heap.push(0, startKey * 5 + 4)
    let found = -1
    while (heap.size > 0) {
      const state = heap.pop()
      if (state === undefined) break
      const dir = state % 5
      const cellIndex = (state - dir) / 5
      if (cellIndex === endKey) { found = state; break }
      const cx = cellIndex % cols
      const cy = Math.floor(cellIndex / cols)
      for (let nextDir = 0; nextDir < 4; nextDir += 1) {
        const step = dirs[nextDir]
        if (step === undefined) continue
        const nx = cx + step.dx
        const ny = cy + step.dy
        if (nx < 0 || nx >= cols || ny < 0 || ny >= rows) continue
        const nIndex = indexOf(nx, ny)
        if (blocked[nIndex] === 1) continue
        const nextState = nIndex * 5 + nextDir
        const turn = dir !== 4 && dir !== nextDir ? 1.8 : 0
        const nextDist = (dist[state] ?? INF) + 1 + turn
        if (nextDist >= (dist[nextState] ?? INF)) continue
        dist[nextState] = nextDist
        prev[nextState] = state
        const heuristic = Math.abs(nx - end.x) + Math.abs(ny - end.y)
        heap.push(nextDist + heuristic * 0.9, nextState)
      }
    }
    const path: Point[] = []
    if (found >= 0) {
      let cursor = found
      while (cursor >= 0) {
        const dir = cursor % 5
        const cellIndex = (cursor - dir) / 5
        path.push({ x: (cellIndex % cols) * CELL, y: Math.floor(cellIndex / cols) * CELL })
        cursor = prev[cursor] ?? -1
      }
      path.reverse()
    }
    const cleaned: Point[] = []
    for (const point of path) {
      const last = cleaned[cleaned.length - 1]
      const before = cleaned[cleaned.length - 2]
      if (last !== undefined && before !== undefined && last.x === before.x && point.x === last.x) {
        cleaned[cleaned.length - 1] = point
      } else if (last !== undefined && before !== undefined && last.y === before.y && point.y === last.y) {
        cleaned[cleaned.length - 1] = point
      } else {
        cleaned.push(point)
      }
    }
    if (cleaned.length === 0) {
      // A* 偶尔在起终点仅隔一条通道时找不到路：退化成一条带外绕的正交折线。
      const horizontal = Math.abs(endPort.x - startPort.x) >= Math.abs(endPort.y - startPort.y)
      if (horizontal) {
        const detourY = Math.min(startPort.y, endPort.y) - CELL * 2
        cleaned.push(
          { x: startPort.x + Math.sign(endPort.x - startPort.x) * CELL, y: startPort.y },
          { x: startPort.x + Math.sign(endPort.x - startPort.x) * CELL, y: detourY },
          { x: endPort.x - Math.sign(endPort.x - startPort.x) * CELL, y: detourY },
          { x: endPort.x - Math.sign(endPort.x - startPort.x) * CELL, y: endPort.y },
        )
      } else {
        const detourX = Math.min(startPort.x, endPort.x) - CELL * 2
        cleaned.push(
          { x: startPort.x, y: startPort.y + Math.sign(endPort.y - startPort.y) * CELL },
          { x: detourX, y: startPort.y + Math.sign(endPort.y - startPort.y) * CELL },
          { x: detourX, y: endPort.y - Math.sign(endPort.y - startPort.y) * CELL },
          { x: endPort.x, y: endPort.y - Math.sign(endPort.y - startPort.y) * CELL },
        )
      }
    }
    const points = [startPort, ...cleaned, endPort]
    const exit =
      sides.out === 'bottom'
        ? { x: 0.5, y: 1 }
        : sides.out === 'top'
          ? { x: 0.5, y: 0 }
          : sides.out === 'right'
            ? { x: 1, y: 0.5 }
            : { x: 0, y: 0.5 }
    const entry =
      sides.into === 'top'
        ? { x: 0.5, y: 0 }
        : sides.into === 'bottom'
          ? { x: 0.5, y: 1 }
          : sides.into === 'left'
            ? { x: 0, y: 0.5 }
            : { x: 1, y: 0.5 }
    result.set(edge.id, { points, exitX: exit.x, exitY: exit.y, entryX: entry.x, entryY: entry.y })
  }
  return result
}
