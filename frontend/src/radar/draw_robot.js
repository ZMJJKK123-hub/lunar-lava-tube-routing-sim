// 机器人绘制器 (动态层): SOS 脉冲 / 面包屑 / 步进平滑播放 / 覆盖圈 / 救援线
// (挂到 Radar2D.prototype; 快照 5Hz -> 帧间平滑)

import { THEME } from './styles'

export const robotDraw = {
  /* ---------- 机器人 + SOS 呼救 (动态层: 快照 5Hz -> 帧间平滑) ---------- */
  _drawRobot(ctx) {
    const snap = this.snapshot
    if (!snap) return
    const T = THEME[this.theme] ?? THEME.dark
    const nodes = snap.nodes
    ctx.save()
    ctx.translate(this.view.x, this.view.y)
    ctx.scale(this.view.scale, this.view.scale)
    const lw = (px) => px / this.view.scale
    ctx.textAlign = 'center'

    // SOS 呼救节点: 红色脉冲扩散环 + SOS 字样 (救到自动消失)
    const tt = this._pnow() / 1000
    for (const [id, n] of Object.entries(nodes)) {
      if (!n.sos) continue
      const ph = (tt * 1.6 + (parseInt(id.slice(-2), 10) || 0) * 0.13) % 1
      ctx.strokeStyle = 'rgba(255,90,60,' + (0.85 * (1 - ph)).toFixed(3) + ')'
      ctx.lineWidth = lw(1.8)
      ctx.beginPath(); ctx.arc(n.x, n.z, lw(6) + ph * lw(34), 0, Math.PI * 2); ctx.stroke()
      ctx.fillStyle = T.sosLabel
      ctx.font = 'bold ' + Math.max(8, lw(9)) + 'px Consolas,monospace'
      ctx.fillText('SOS', n.x, n.z - lw(14))
    }

    const rb = snap.robot
    if (rb) {
      // 面包屑轨迹: 核查/救援途中逐 tick 记录 (绿=此处可见主网, 红=无网)
      if (rb.trail) {
        for (const [tx, tz, c] of rb.trail) {
          ctx.fillStyle = c ? T.trailOn : T.trailOff
          ctx.beginPath(); ctx.arc(tx, tz, lw(1.7), 0, Math.PI * 2); ctx.fill()
        }
      }
      const [x, z] = this._robotPos(rb)
      this._drawRobotBody(ctx, lw, x, z, rb, nodes)
    }

    // 移动干扰源 (开关灾害): 紫红脉冲圈 + 边界虚线圈 + 核心
    // (脉冲相位走 _pnow —— 暂停时干扰圈定格, 便于讲解)
    const jm = snap.jammer
    if (jm) {
      const ph = (this._pnow() / 1000 * 1.1) % 1
      ctx.strokeStyle = 'rgba(255,80,160,' + (0.5 * (1 - ph)).toFixed(3) + ')'
      ctx.lineWidth = lw(2)
      ctx.beginPath(); ctx.arc(jm.x, jm.z, jm.r * (0.25 + 0.75 * ph), 0, Math.PI * 2); ctx.stroke()
      ctx.strokeStyle = T.jamRing
      ctx.lineWidth = lw(1.2)
      ctx.setLineDash([lw(10), lw(8)])
      ctx.beginPath(); ctx.arc(jm.x, jm.z, jm.r, 0, Math.PI * 2); ctx.stroke()
      ctx.setLineDash([])
      ctx.shadowColor = '#FF50A0'; ctx.shadowBlur = lw(16)
      ctx.fillStyle = '#FF50A0'
      ctx.beginPath(); ctx.arc(jm.x, jm.z, lw(6), 0, Math.PI * 2); ctx.fill()
      ctx.shadowBlur = 0
      ctx.fillStyle = T.jamLabel
      ctx.font = 'bold ' + Math.max(8, lw(9)) + 'px Consolas,monospace'
      ctx.fillText('📵 JAM', jm.x, jm.z - lw(12))
    }
    ctx.restore()
  },

  // 步进队列 + 单调 250ms 播放时钟 (jitter buffer):
  // 位置到达间隔因 tick(250ms)与快照(200ms)差拍而在 200/400ms 交替,
  // 固定时长播放会被截断(微跳)或空窗(停顿) —— 即"流畅一段卡一段"。
  // 严格每 0.25s 播一步, 队列吸收到达抖动, 速率 ±15% 微调维持 ~1.5 步水位
  _robotPos(rb) {
    const nowMs = this._pnow()
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
    // 停驻 (仅剩当前点): 时钟清零并原地返回, 严禁触碰不存在的 pts[1];
    // 清零 (而非封顶 spd) 保证队列空窗后的下一步从 0 起满速播放, 不瞬跳
    if (Q.pts.length < 2) { Q.clock = 0; return [Q.pts[0].x, Q.pts[0].z] }
    const f = Math.min(1, Q.clock / spd)
    return [Q.pts[0].x + (Q.pts[1].x - Q.pts[0].x) * f,
            Q.pts[0].z + (Q.pts[1].z - Q.pts[0].z) * f]
  },

  _drawRobotBody(ctx, lw, x, z, rb, nodes) {
    const T = THEME[this.theme] ?? THEME.dark
    // 通信覆盖圈 (300 世界米)
    ctx.setLineDash([lw(10), lw(8)])
    ctx.strokeStyle = T.robotRange
    ctx.lineWidth = lw(1.2)
    ctx.beginPath(); ctx.arc(x, z, 300, 0, Math.PI * 2); ctx.stroke()
    ctx.setLineDash([])
    // 加固/救援线: 机器人 -> 目标
    if ((rb.state === 'RESCUE' || rb.state === 'INVESTIGATE' || rb.state === 'FALLBACK'
         || rb.state === 'ASSIST') && rb.target && nodes[rb.target]) {
      const t = nodes[rb.target]
      ctx.setLineDash([lw(6), lw(6)])
      ctx.strokeStyle = 'rgba(255,150,80,0.7)'
      ctx.lineWidth = lw(1.4)
      ctx.beginPath(); ctx.moveTo(x, z); ctx.lineTo(t.x, t.z); ctx.stroke()
      ctx.setLineDash([])
    }
    // 加固择点: 机器人依历史观测选定的落钉位 (琥珀虚线小环 + 连接线)
    if (rb.state === 'ASSIST' && rb.spot) {
      ctx.setLineDash([lw(3), lw(3)])
      ctx.strokeStyle = T.pboostRing
      ctx.lineWidth = lw(1.2)
      ctx.beginPath(); ctx.arc(rb.spot[0], rb.spot[1], lw(10), 0, Math.PI * 2); ctx.stroke()
      ctx.beginPath(); ctx.moveTo(x, z); ctx.lineTo(rb.spot[0], rb.spot[1]); ctx.stroke()
      ctx.setLineDash([])
    }
    // 本体: 金色菱形 + 状态标签 (浅色主题压暗金色并补描边)
    const r = lw(7)
    ctx.shadowColor = '#F0D080'; ctx.shadowBlur = lw(14)
    ctx.fillStyle = T.robotFill
    ctx.beginPath()
    ctx.moveTo(x, z - r); ctx.lineTo(x + r, z)
    ctx.lineTo(x, z + r); ctx.lineTo(x - r, z); ctx.closePath()
    ctx.fill()
    ctx.shadowBlur = 0
    if (T.outline) {
      ctx.strokeStyle = T.outline
      ctx.lineWidth = lw(0.8)
      ctx.stroke()
    }
    ctx.fillStyle = T.robotLabel
    ctx.font = Math.max(8, lw(9)) + 'px Consolas,monospace'
    ctx.fillText('BOT·' + (rb.state === 'RESCUE' ? '救援' : rb.state === 'INVESTIGATE' ? '核查'
                 : rb.state === 'FALLBACK' ? '回撤' : rb.state === 'ASSIST' ? '加固' : '巡逻')
                 + ' 钉×' + rb.stock, x, z - lw(12))
  },
}
