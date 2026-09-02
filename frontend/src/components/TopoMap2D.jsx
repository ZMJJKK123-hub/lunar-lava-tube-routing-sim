// 2D 算法俯视图: 经典图论示意风格的拓扑沙盘
// - 节点圆点 + 真实存在的边(暗灰细线) + 激活主干(青绿) + 被遮挡近邻(红虚线)
// - 用户按住拖拽即可"画墙", 墙落地的瞬间后端重算视距拓扑, 被切断的边实时消失
import { useEffect, useRef, useState } from 'react'

const W = 385, H = 300
const NODE_COLOR = { ACTIVE: '#35ff9e', DEGRADED: '#ffb020', SEU_RESET: '#ff7a3c', DEAD: '#4a4f5a' }

export default function TopoMap2D({ snapshot, geology, selected, onSelect, onAddWall, onClearWalls }) {
  const canvasRef = useRef(null)
  const dragRef = useRef(null)
  const [draft, setDraft] = useState(null)

  // 世界坐标 -> 面板坐标 (x-z 俯视投影, 自动 fit)
  const project = (pt) => {
    const nodes = Object.values(snapshot?.nodes ?? {})
    if (!nodes.length) return [0, 0]
    let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity
    for (const n of nodes) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x)
      minZ = Math.min(minZ, n.z); maxZ = Math.max(maxZ, n.z)
    }
    const pad = 34
    const sx = (W - pad * 2) / (maxX - minX || 1)
    const sz = (H - pad * 2) / (maxZ - minZ || 1)
    const s = Math.min(sx, sz)
    const ox = pad + ((W - pad * 2) - (maxX - minX) * s) / 2
    const oz = pad + ((H - pad * 2) - (maxZ - minZ) * s) / 2
    return { pt: [(pt[0] - minX) * s + ox, (pt[1] - minZ) * s + oz], s, minX, minZ, ox, oz }
  }

  useEffect(() => {
    const cv = canvasRef.current
    if (!cv || !snapshot) return
    const ctx = cv.getContext('2d')
    ctx.clearRect(0, 0, W, H)
    const { pt, s, minX, minZ, ox, oz } = project([0, 0])
    const P = (x, z) => [(x - minX) * s + ox, (z - minZ) * s + oz]

    // 背景
    ctx.fillStyle = '#060c16'
    ctx.fillRect(0, 0, W, H)

    // 底图: 腔室圆 + 隧道走向 (浅描)
    ctx.strokeStyle = 'rgba(70,110,160,0.22)'
    ctx.lineWidth = 1
    const geology0 = geology
    if (geology0) {
      for (const c of geology0.chambers) {
        ctx.beginPath()
        ctx.arc(...P(c.x, c.z), c.r * s, 0, Math.PI * 2)
        ctx.stroke()
      }
      ctx.lineWidth = Math.max(2, 6 * s)
      ctx.strokeStyle = 'rgba(60,100,150,0.13)'
      for (const t of geology0.tunnels) {
        ctx.beginPath()
        ctx.moveTo(...P(t.a[0], t.a[2]))
        ctx.quadraticCurveTo(...P(t.mid[0], t.mid[2]), ...P(t.b[0], t.b[2]))
        ctx.stroke()
      }
    }

    // 巨石投影
    ctx.fillStyle = 'rgba(120,120,140,0.25)'
    for (const o of snapshot.obstacles ?? []) {
      const [x, y] = P(o.x, o.z)
      ctx.beginPath(); ctx.arc(x, y, Math.max(1, o.r * s), 0, Math.PI * 2); ctx.fill()
    }

    // 用户墙体 (深红粗线)
    ctx.strokeStyle = '#c8503a'
    ctx.lineWidth = 3.5
    for (const w of snapshot.walls ?? []) {
      ctx.beginPath()
      ctx.moveTo(...P(w.x1, w.z1)); ctx.lineTo(...P(w.x2, w.z2))
      ctx.stroke()
    }
    if (draft) {
      ctx.strokeStyle = 'rgba(230,110,90,0.6)'
      ctx.setLineDash([5, 4])
      ctx.beginPath()
      ctx.moveTo(draft.x1, draft.y1); ctx.lineTo(draft.x2, draft.y2)
      ctx.stroke()
      ctx.setLineDash([])
    }

    // 真实存在的边: 暗灰细线 (未被路由选中)
    const active = new Set()
    for (const tr of snapshot.traffic ?? []) {
      const p = tr.path ?? []
      for (let i = 0; i < p.length - 1; i++) active.add([p[i], p[i + 1]].sort().join('|'))
    }
    for (const lk of snapshot.links ?? []) {
      const na = snapshot.nodes[lk.a], nb = snapshot.nodes[lk.b]
      if (!na || !nb) continue
      const on = active.has([lk.a, lk.b].sort().join('|'))
      ctx.strokeStyle = on ? '#00ffcc' : (lk.up ? 'rgba(70,130,90,0.5)' : 'rgba(150,60,60,0.4)')
      ctx.lineWidth = on ? 2.2 : 1
      ctx.beginPath()
      ctx.moveTo(...P(na.x, na.z)); ctx.lineTo(...P(nb.x, nb.z))
      ctx.stroke()
    }

    // 机器人 RCSPA 路径 (金色)
    const rb = snapshot.robot
    if (rb?.route?.path?.length) {
      ctx.strokeStyle = '#ffcf6e'
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.moveTo(...P(rb.x, rb.z))
      for (const nid of rb.route.path) {
        const n = snapshot.nodes[nid]
        if (n) ctx.lineTo(...P(n.x, n.z))
      }
      ctx.stroke()
      const [rx, ry] = P(rb.x, rb.z)
      ctx.fillStyle = '#fff0c0'
      ctx.beginPath(); ctx.arc(rx, ry, 4.5, 0, Math.PI * 2); ctx.fill()
    }

    // 被遮挡近邻 (选中节点): 红虚线 + X
    if (selected && snapshot.nodes[selected]?.blocked_nbrs) {
      const n0 = snapshot.nodes[selected]
      for (const b of snapshot.nodes[selected].blocked_nbrs) {
        const n1 = snapshot.nodes[b.id]
        if (!n1) continue
        const [x0, y0] = P(n0.x, n0.z), [x1, y1] = P(n1.x, n1.z)
        ctx.strokeStyle = 'rgba(255,90,90,0.75)'
        ctx.lineWidth = 1.4
        ctx.setLineDash([4, 4])
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke()
        ctx.setLineDash([])
        const mx = (x0 + x1) / 2, my = (y0 + y1) / 2
        ctx.strokeStyle = '#ff6050'
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.moveTo(mx - 5, my - 5); ctx.lineTo(mx + 5, my + 5)
        ctx.moveTo(mx + 5, my - 5); ctx.lineTo(mx - 5, my + 5)
        ctx.stroke()
      }
    }

    // 节点
    for (const [id, n] of Object.entries(snapshot.nodes ?? {})) {
      const [x, y] = P(n.x, n.z)
      ctx.fillStyle = NODE_COLOR[n.state] ?? '#35ff9e'
      ctx.beginPath(); ctx.arc(x, y, id === 'NODE-00' ? 5.5 : 3.5, 0, Math.PI * 2); ctx.fill()
      if (id === selected) {
        ctx.strokeStyle = '#ffffff'
        ctx.lineWidth = 1.6
        ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2); ctx.stroke()
      }
    }
  }, [snapshot, selected, draft])

  // ---- 交互: 拖拽画墙 / 点击选节点 ----
  const toWorld = (e) => {
    const rect = canvasRef.current.getBoundingClientRect()
    const px = ((e.clientX - rect.left) / rect.width) * W
    const py = ((e.clientY - rect.top) / rect.height) * H
    const nodes = Object.values(snapshot?.nodes ?? {})
    let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity
    for (const n of nodes) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x)
      minZ = Math.min(minZ, n.z); maxZ = Math.max(maxZ, n.z)
    }
    const pad = 34
    const s = Math.min((W - pad * 2) / (maxX - minX || 1), (H - pad * 2) / (maxZ - minZ || 1))
    const ox = pad + ((W - pad * 2) - (maxX - minX) * s) / 2
    const oz = pad + ((H - pad * 2) - (maxZ - minZ) * s) / 2
    return [(px - ox) / s + minX, (py - oz) / s + minZ]
  }

  const onDown = (e) => {
    if (e.shiftKey || e.button === 2) return
    const [x, z] = toWorld(e)
    dragRef.current = { x, z, px: e.clientX, py: e.clientY, moved: false }
  }
  const onMove = (e) => {
    const d = dragRef.current
    if (!d) return
    if (Math.hypot(e.clientX - d.px, e.clientY - d.py) > 6) d.moved = true
    if (d.moved) {
      const rect = canvasRef.current.getBoundingClientRect()
      setDraft({
        x1: ((d.px - rect.left) / rect.width) * W,
        y1: ((d.py - rect.top) / rect.height) * H,
        x2: ((e.clientX - rect.left) / rect.width) * W,
        y2: ((e.clientY - rect.top) / rect.height) * H,
      })
    }
  }
  const onUp = (e) => {
    const d = dragRef.current
    dragRef.current = null
    setDraft(null)
    if (!d) return
    if (d.moved) {
      const [x2, z2] = toWorld(e)
      if (Math.hypot(x2 - d.x, z2 - d.z) > 20) onAddWall?.(d.x, d.z, x2, z2)
    } else {
      // 单击: 选中最近节点
      let best = null, bd = Infinity
      for (const [id, n] of Object.entries(snapshot?.nodes ?? {})) {
        const dist = Math.hypot(n.x - d.x, n.z - d.z)
        if (dist < bd) { bd = dist; best = id }
      }
      if (best && bd < 60) onSelect?.(best)
    }
  }

  if (!snapshot) return null
  return (
    <div style={{
      position: 'absolute', left: 12, top: 58, zIndex: 7,
      background: 'rgba(5,10,20,0.92)', border: '1px solid #1d3a5f', borderRadius: 8,
      fontSize: 11, backdropFilter: 'blur(6px)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', padding: '6px 10px', borderBottom: '1px solid #14263e' }}>
        <b style={{ color: '#9fd4ff', letterSpacing: 1 }}>🗺 算法俯视图 (2D)</b>
        <span style={{ color: '#5d7ea3', marginLeft: 8 }}>拖拽画墙 → 实时重算</span>
        <button onClick={onClearWalls} style={{
          marginLeft: 'auto', padding: '2px 8px', cursor: 'pointer', fontSize: 10,
          background: '#3a1414', color: '#ff9a9a', border: '1px solid #7a2030', borderRadius: 4,
        }}>清墙</button>
      </div>
      <canvas
        ref={canvasRef} width={W} height={H}
        style={{ display: 'block', cursor: 'crosshair', borderRadius: '0 0 8px 8px' }}
        onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={() => { dragRef.current = null; setDraft(null) }}
      />
    </div>
  )
}
