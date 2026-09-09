// Hover 层绘制器: 悬停节点的邻域情报 (通信圈/高亮边/超距衰减线/被挡视线)
// (挂到 Radar2D.prototype; 仅 hoverId 存在时由 _frame 调用)

import { THEME } from './styles'

export const hoverDraw = {
  /* ---------- Hover 层: 局部高亮 + 波浪脉冲 ---------- */
  _drawHoverGlow(ctx) {
    const A = this.snapshot.nodes[this.hoverId]
    if (!A) return
    const T = THEME[this.theme] ?? THEME.dark
    ctx.save()
    ctx.translate(this.view.x, this.view.y)
    ctx.scale(this.view.scale, this.view.scale)
    const lw = (px) => px / this.view.scale

    // ---- 通信范围圈: 以悬停节点为圆心的 UWB 半径(300m) 虚线大圆 ----
    // 圈内 = 距离上可达(是否直连还看视线遮挡); 圈外 = 超距
    const R_COMM = 30 * 10
    const fillG = ctx.createRadialGradient(A.x, A.z, 0, A.x, A.z, R_COMM)
    fillG.addColorStop(0, T.hoverFill)
    fillG.addColorStop(1, 'rgba(0,206,201,0)')
    ctx.fillStyle = fillG
    ctx.beginPath(); ctx.arc(A.x, A.z, R_COMM, 0, Math.PI * 2); ctx.fill()
    ctx.setLineDash([lw(14), lw(10)])
    ctx.strokeStyle = T.hoverEdge
    ctx.lineWidth = lw(1.4)
    ctx.beginPath(); ctx.arc(A.x, A.z, R_COMM, 0, Math.PI * 2); ctx.stroke()
    ctx.setLineDash([])

    // 高亮邻边: 半透明底线 + 霓虹光晕 (真实报文方块由动态层负责)
    ctx.strokeStyle = T.hoverEdge
    ctx.lineWidth = lw(2)
    ctx.shadowColor = '#00CEC9'
    ctx.shadowBlur = 18
    ctx.beginPath()
    for (const e of this.hoverEdges) {
      ctx.moveTo(e.na.x, e.na.z); ctx.lineTo(e.nb.x, e.nb.z)
    }
    ctx.stroke()
    ctx.shadowBlur = 0

    // 超距邻居: 锥形衰减线 —— 模拟"信号从悬停节点出发, 传到一半因距离过远而衰竭"
    // 画法: 分 8 段, 宽度与透明度同步递减 (起点粗亮 -> 终点细到消失)
    this._drawOutOfRange(ctx, lw, A)
    // 被挡视线: 暗红虚线 + 中点 X —— 解释"这个邻居明明很近为什么连不上"
    this._drawBlockedSight(ctx, lw, A)

    // 悬停节点本体高亮圈
    ctx.strokeStyle = T.hoverRing
    ctx.lineWidth = lw(1.6)
    ctx.beginPath(); ctx.arc(A.x, A.z, lw(13), 0, Math.PI * 2); ctx.stroke()
    ctx.restore()
  },

  _drawOutOfRange(ctx, lw, A) {
    const T = THEME[this.theme] ?? THEME.dark
    const RANGE = 30 * 10            // UWB 仿真半径 30 x WORLD_SCALE 10 = 300 世界米
    const far = []
    for (const n2 of Object.values(this.snapshot.nodes)) {
      if (n2 === A || n2.state === 'DEAD') continue
      const d = Math.hypot(n2.x - A.x, n2.z - A.z)
      if (d > RANGE && d < RANGE * 1.6) far.push([d, n2])
    }
    far.sort((p, q) => p[0] - q[0])
    ctx.lineCap = 'butt'
    ctx.fillStyle = T.farLabel
    ctx.font = Math.max(9, lw(10)) + 'px Consolas,monospace'
    for (const [d, n2] of far.slice(0, 3)) {
      const SEG = 8
      for (let k = 0; k < SEG; k++) {
        const t0 = k / SEG, t1 = (k + 1) / SEG
        ctx.strokeStyle = 'rgba(' + T.farRGB + ',' + (0.5 * (1 - k / SEG) + 0.04).toFixed(3) + ')'
        ctx.lineWidth = lw(2.8 * (1 - k / SEG) + 0.22)
        ctx.beginPath()
        ctx.moveTo(A.x + (n2.x - A.x) * t0, A.z + (n2.z - A.z) * t0)
        ctx.lineTo(A.x + (n2.x - A.x) * t1, A.z + (n2.z - A.z) * t1)
        ctx.stroke()
      }
      const mx = A.x + (n2.x - A.x) * 0.62, mz = A.z + (n2.z - A.z) * 0.62
      ctx.fillText('超出通信范围 ' + Math.round(d) + 'm', mx + lw(8), mz - lw(8))
    }
  },

  _drawBlockedSight(ctx, lw, A) {
    ctx.strokeStyle = 'rgba(255,90,90,0.55)'
    ctx.lineWidth = lw(1.5)
    ctx.setLineDash([lw(9), lw(7)])
    const blocked = A.blocked_nbrs ?? []
    for (const b of blocked) {
      const n1 = this.snapshot.nodes[b.id]
      if (!n1) continue
      ctx.beginPath(); ctx.moveTo(A.x, A.z); ctx.lineTo(n1.x, n1.z); ctx.stroke()
      const mx = (A.x + n1.x) / 2, mz = (A.z + n1.z) / 2
      const s = lw(7)
      ctx.setLineDash([])
      ctx.strokeStyle = '#FF6050'
      ctx.lineWidth = lw(2.2)
      ctx.beginPath()
      ctx.moveTo(mx - s, mz - s); ctx.lineTo(mx + s, mz + s)
      ctx.moveTo(mx + s, mz - s); ctx.lineTo(mx - s, mz + s)
      ctx.stroke()
      // 遮挡原因标注 (岩壁/巨石/巨柱)
      ctx.fillStyle = 'rgba(255,150,150,0.9)'
      ctx.font = Math.max(9, lw(10)) + 'px Consolas,monospace'
      ctx.fillText(b.cause ?? '遮挡', mx + lw(9), mz - lw(9))
      ctx.setLineDash([lw(9), lw(7)])
      ctx.strokeStyle = 'rgba(255,90,90,0.55)'
    }
    ctx.setLineDash([])
  },
}
