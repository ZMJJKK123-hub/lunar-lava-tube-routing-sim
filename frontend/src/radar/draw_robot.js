// 机器人绘制器 (动态层): SOS 脉冲 / 面包屑 / 步进平滑播放 / 覆盖圈 / 救援线
// (挂到 Radar2D.prototype; 快照 5Hz -> 帧间平滑)

export const robotDraw = {
  /* ---------- 机器人 + SOS 呼救 (动态层: 快照 5Hz -> 帧间平滑) ---------- */
  _drawRobot(ctx) {
    const snap = this.snapshot
    if (!snap) return
    const nodes = snap.nodes
    ctx.save()
    ctx.translate(this.view.x, this.view.y)
    ctx.scale(this.view.scale, this.view.scale)
    const lw = (px) => px / this.view.scale
    ctx.textAlign = 'center'

    // SOS 呼救节点: 红色脉冲扩散环 + SOS 字样 (救到自动消失)
    const tt = performance.now() / 1000
    for (const [id, n] of Object.entries(nodes)) {
      if (!n.sos) continue
      const ph = (tt * 1.6 + (parseInt(id.slice(-2), 10) || 0) * 0.13) % 1
      ctx.strokeStyle = 'rgba(255,90,60,' + (0.85 * (1 - ph)).toFixed(3) + ')'
      ctx.lineWidth = lw(1.8)
      ctx.beginPath(); ctx.arc(n.x, n.z, lw(6) + ph * lw(34), 0, Math.PI * 2); ctx.stroke()
      ctx.fillStyle = 'rgba(255,130,100,0.95)'
      ctx.font = 'bold ' + Math.max(8, lw(9)) + 'px Consolas,monospace'
      ctx.fillText('SOS', n.x, n.z - lw(14))
    }

    const rb = snap.robot
    if (rb) {
      // 面包屑轨迹: 核查/救援途中逐 tick 记录 (绿=此处可见主网, 红=无网)
      if (rb.trail) {
        for (const [tx, tz, c] of rb.trail) {
          ctx.fillStyle = c ? 'rgba(80,255,160,0.5)' : 'rgba(255,110,90,0.3)'
          ctx.beginPath(); ctx.arc(tx, tz, lw(1.7), 0, Math.PI * 2); ctx.fill()
        }
      }
      const [x, z] = this._robotPos(rb)
      this._drawRobotBody(ctx, lw, x, z, rb, nodes)
    }
    ctx.restore()
  },

  // 步进队列 + 单调 250ms 播放时钟 (jitter buffer):
  // 位置到达间隔因 tick(250ms)与快照(200ms)差拍而在 200/400ms 交替,
  // 固定时长播放会被截断(微跳)或空窗(停顿) —— 即"流畅一段卡一段"。
  // 严格每 0.25s 播一步, 队列吸收到达抖动, 速率 ±15% 微调维持 ~1.5 步水位
  _robotPos(rb) {
    const nowMs = performance.now()
    if (!this._rbQ) this._rbQ = { pts: [{ x: rb.x, z: rb.z }], lx: rb.x, lz: rb.z,
                                  clock: 0, last: nowMs }
    const Q = this._rbQ
    if (rb.x !== Q.lx || rb.z !== Q.lz) {           // 新位置入队 (去重)
      Q.lx = rb.x; Q.lz = rb.z
      Q.pts.push({ x: rb.x, z: rb.z })
      if (Q.pts.length > 6) Q.pts.shift()           // 保险丝
    }
    const dtMs = nowMs - Q.last; Q.last = nowMs
    const water = Q.pts.length - 1                  // 待播步数
    const spd = 250 * (1 + 0.15 * Math.max(-1, Math.min(1, 1.5 - water)))
    Q.clock += dtMs
    while (Q.clock >= spd && Q.pts.length > 1) { Q.pts.shift(); Q.clock -= spd }
    if (Q.pts.length < 2) Q.clock = Math.min(Q.clock, spd)   // 停驻: 时钟封顶防跳
    const f = Q.pts.length >= 2 ? Math.min(1, Q.clock / spd) : 1
    return [Q.pts[0].x + (Q.pts[1].x - Q.pts[0].x) * f,
            Q.pts[0].z + (Q.pts[1].z - Q.pts[0].z) * f]
  },

  _drawRobotBody(ctx, lw, x, z, rb, nodes) {
    // 通信覆盖圈 (300 世界米)
    ctx.setLineDash([lw(10), lw(8)])
    ctx.strokeStyle = 'rgba(232,200,110,0.32)'
    ctx.lineWidth = lw(1.2)
    ctx.beginPath(); ctx.arc(x, z, 300, 0, Math.PI * 2); ctx.stroke()
    ctx.setLineDash([])
    // 救援线: 机器人 -> 呼救目标
    if ((rb.state === 'RESCUE' || rb.state === 'INVESTIGATE' || rb.state === 'FALLBACK') && rb.target && nodes[rb.target]) {
      const t = nodes[rb.target]
      ctx.setLineDash([lw(6), lw(6)])
      ctx.strokeStyle = 'rgba(255,150,80,0.7)'
      ctx.lineWidth = lw(1.4)
      ctx.beginPath(); ctx.moveTo(x, z); ctx.lineTo(t.x, t.z); ctx.stroke()
      ctx.setLineDash([])
    }
    // 本体: 金色菱形 + 状态标签
    const r = lw(7)
    ctx.shadowColor = '#F0D080'; ctx.shadowBlur = lw(14)
    ctx.fillStyle = '#E8C860'
    ctx.beginPath()
    ctx.moveTo(x, z - r); ctx.lineTo(x + r, z)
    ctx.lineTo(x, z + r); ctx.lineTo(x - r, z); ctx.closePath()
    ctx.fill()
    ctx.shadowBlur = 0
    ctx.fillStyle = 'rgba(240,215,150,0.95)'
    ctx.font = Math.max(8, lw(9)) + 'px Consolas,monospace'
    ctx.fillText('BOT·' + (rb.state === 'RESCUE' ? '救援' : rb.state === 'INVESTIGATE' ? '核查' : rb.state === 'FALLBACK' ? '回撤' : '巡逻') + ' 钉×' + rb.stock,
                 x, z - lw(12))
  },
}
