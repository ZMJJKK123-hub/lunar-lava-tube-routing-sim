// 传输层绘制器: 真实 DATA 报文可视化 (活跃边/排队徽章/匀速方块/标记环/闪烁/红叉)
// (挂到 Radar2D.prototype; _drawTransport 为编排, 各小节独立成法)

import { DATA_HOP_S } from './styles'

// 信道配色: 与后端 rscspa 的 3 信道一一对应
const CHAN_COL = ['#00E8FF', '#FFC04D', '#B08CFF']

// 折线取点: 按路程比例 f 在路径折线上插值 (各段按欧氏长度加权)
function pointOnPath(path, f) {
  const total = path.length - 1
  const lens = []
  let L = 0
  for (let i = 0; i < total; i++) {
    const d = Math.hypot(path[i + 1].x - path[i].x, path[i + 1].z - path[i].z)
    lens.push(d); L += d
  }
  let want = f * L, x = path[0].x, z = path[0].z
  for (let i = 0; i < total; i++) {
    if (want <= lens[i] || i === total - 1) {
      const q = lens[i] > 0 ? Math.min(1, want / lens[i]) : 1
      x = path[i].x + (path[i + 1].x - path[i].x) * q
      z = path[i].z + (path[i + 1].z - path[i].z) * q
      break
    }
    want -= lens[i]
  }
  return [x, z]
}

// 折线总长 (缓存于条目._len)
function pathLen(path) {
  return path.reduce(
    (a, n, i) => i ? a + Math.hypot(n.x - path[i - 1].x, n.z - path[i - 1].z) : 0, 0)
}

export const transportDraw = {
  /* ---------- 动态层: 真实数据包可视化 (握手在底层, 画面只演数据) ---------- */
  _drawTransport(ctx) {
    const snap = this.snapshot
    if (!snap) return
    const now = performance.now()
    const dt = Math.min(0.05, (now - (this._tprev ?? now)) / 1000)
    this._tprev = now
    ctx.save()
    ctx.translate(this.view.x, this.view.y)
    ctx.scale(this.view.scale, this.view.scale)
    const lw = (px) => px / this.view.scale
    const pk = snap.packets ?? []
    const nodes = Object.assign(Object.create(null), snap.nodes)
    if (snap.robot) nodes.ROBOT = { x: snap.robot.x, z: snap.robot.z }

    this._drawActiveEdges(ctx, lw, nodes, pk)     // 1) 有真实流量的边亮起
    this._drawQueueBadges(ctx, lw, nodes, pk)     // 2) 排队徽章
    const alive = this._drawDataBlocks(ctx, lw, nodes, pk, dt)   // 3) 在途方块
    this._drawOrphanBlocks(ctx, lw, dt, alive)    //    送达/作废后飞完再消失
    this._drawMarkers(ctx, lw, nodes)             // 4/5) 源/目的环 + 发送模式环
    this._drawFlashes(ctx, lw, dt)                // 6) 送达/失败闪烁圈
    this._drawCrosses(ctx, lw, dt)                // 7) 失败红叉
    ctx.restore()
  },

  // 1) 有真实流量的边自动亮起 (青色霓虹, 盖过静息暗绿; 仅 DATA, 链上点不染边)
  _drawActiveEdges(ctx, lw, nodes, pk) {
    const seen = new Set()
    for (const p of pk) {
      if (p.t < 0 || p.kind !== 'DATA') continue
      seen.add(p.a < p.b ? p.a + '|' + p.b : p.b + '|' + p.a)
    }
    if (!seen.size) return
    ctx.strokeStyle = 'rgba(0, 220, 215, 0.55)'
    ctx.lineWidth = lw(2.2)
    ctx.shadowColor = '#00CEC9'
    ctx.shadowBlur = 14
    ctx.beginPath()
    for (const k of seen) {
      const [a, b] = k.split('|')
      const na = nodes[a], nb = nodes[b]
      if (!na || !nb) continue
      ctx.moveTo(na.x, na.z); ctx.lineTo(nb.x, nb.z)
    }
    ctx.stroke()
    ctx.shadowBlur = 0
  },

  // 2) 排队徽章: 节点缓冲中等待发送的报文数 (半双工: 每 tick 每节点仅一个
  //    发送名额)。数字 = 排队中的报文 —— 不再在路上冻结/节点旁堆小方块
  _drawQueueBadges(ctx, lw, nodes, pk) {
    const queued = {}
    for (const p of pk) {
      if (p.t >= 0 || p.kind !== 'DATA') continue
      queued[p.a] = (queued[p.a] ?? 0) + 1
    }
    ctx.font = 'bold ' + Math.max(8, lw(9)) + 'px Consolas,monospace'
    ctx.textAlign = 'center'
    for (const [nid, cnt] of Object.entries(queued)) {
      const n = nodes[nid]
      if (!n) continue
      const x = n.x + lw(16), z = n.z - lw(13)
      const w = lw(cnt >= 10 ? 17 : 12), h = lw(11)
      ctx.shadowColor = '#00E8FF'
      ctx.shadowBlur = lw(6)
      ctx.fillStyle = 'rgba(0,130,155,0.9)'
      ctx.strokeStyle = 'rgba(130,240,255,0.95)'
      ctx.lineWidth = lw(0.8)
      ctx.beginPath()
      if (ctx.roundRect) ctx.roundRect(x - w / 2, z - h / 2, w, h, lw(3))
      else ctx.rect(x - w / 2, z - h / 2, w, h)
      ctx.fill(); ctx.stroke()
      ctx.shadowBlur = 0
      ctx.fillStyle = '#EAFDFF'
      ctx.fillText(String(cnt), x, z + lw(3))
    }
  },

  // 3) DATA 分段: 匀速直发动画 —— 纯本地时钟推进(每跳 0.25s), 零回拉零纠偏。
  //    快照只负责: 路径形状 / 停驻等待(真实拥塞) / 生命周期 / 严重超前校正。
  //    返回本帧存活的旅程键集合 (孤儿判定用)。
  _drawDataBlocks(ctx, lw, nodes, pk, dt) {
    ctx.font = 'bold ' + Math.max(8, lw(9)) + 'px Consolas,monospace'
    ctx.textAlign = 'center'
    const alive = new Set()
    for (const p of pk) {
      if (p.kind !== 'DATA') continue
      const key = p.msg + ':' + p.seg          // 跨跳稳定的旅程键
      alive.add(key)
      const path = (p.path ?? []).map(id => nodes[id]).filter(Boolean)
      if (path.length < 2) continue
      const total = path.length - 1
      let s = this._pkSmooth.get(key)
      if (!s) { s = { h: p.ph, path, total, chan: p.chan }; this._pkSmooth.set(key, s) }
      s.path = path; s.total = total; s.chan = p.chan
      if (p.t < 0) {
        // 排队中: 不上路绘制 (节点徽章已示意), 静默把进度对齐到节点,
        // 恢复飞行时从节点起飞 —— 消灭"冻在半路"的观感
        s.h = Math.max(s.h, p.ph)
        continue
      }
      // 纯匀速模型: 进度 = 已飞跳数 s.h, 飞行时每 0.25s 匀速前进一跳。
      s.h = Math.min(total, s.h + dt / DATA_HOP_S)
      const f = total > 0 ? Math.min(1, Math.max(0, s.h / total)) : 1
      const [x, z] = pointOnPath(path, f)
      this._drawPacketBlock(ctx, lw, x, z, CHAN_COL[p.chan ?? 0] ?? '#00E8FF')
      ctx.fillStyle = 'rgba(220,245,255,0.9)'
      const fmtB = (b) => (b >= 1024 ? (b / 1024).toFixed(b % 1024 ? 1 : 0) + 'KB' : b + 'B')
      ctx.fillText('DATA ' + fmtB(p.bytes), x, z - lw(10))
    }
    return alive
  },

  // 孤儿方块: 报文已从快照消失 (送达/作废) 但视觉未到终点 -> 飞完再消失
  _drawOrphanBlocks(ctx, lw, dt, alive) {
    for (const [k, s] of this._pkSmooth) {
      if (alive.has(k) || !s.path || s.h >= s.total) continue
      s.h = Math.min(s.total, s.h + dt / DATA_HOP_S)
      const f = Math.min(1, Math.max(0, s.h / s.total))
      const path = s.path
      s._len ?? (s._len = pathLen(path))
      const total = s.total
      let want = f * s._len, px = path[0].x, pz = path[0].z
      for (let i = 0; i < total; i++) {
        const seg = Math.hypot(path[i + 1].x - path[i].x, path[i + 1].z - path[i].z)
        if (want <= seg || i === total - 1) {
          const q = seg > 0 ? Math.min(1, want / seg) : 1
          px = path[i].x + (path[i + 1].x - path[i].x) * q
          pz = path[i].z + (path[i + 1].z - path[i].z) * q
          break
        }
        want -= seg
      }
      this._drawPacketBlock(ctx, lw, px, pz, CHAN_COL[s.chan ?? 0] ?? '#00E8FF')
    }
    for (const [k, s] of this._pkSmooth)
      if (!alive.has(k) && (!s.path || s.h >= s.total)) this._pkSmooth.delete(k)
  },

  // 单个 DATA 方块 (发光圆角方块本体)
  _drawPacketBlock(ctx, lw, x, z, col) {
    ctx.shadowColor = col
    ctx.shadowBlur = 10
    ctx.fillStyle = col
    const w = lw(6.5)
    ctx.beginPath()
    if (ctx.roundRect) ctx.roundRect(x - w / 2, z - w / 2, w, w, lw(1.5))
    else ctx.rect(x - w / 2, z - w / 2, w, w)
    ctx.fill()
    ctx.shadowBlur = 0
  },

  // 4) 在途报文的源/目的节点标记环  5) 发消息模式: 源节点常亮大环
  _drawMarkers(ctx, lw, nodes) {
    for (const tr of this.snapshot.traffic ?? []) {
      const dst = tr.path?.[tr.path.length - 1]
      const s1 = nodes[tr.src], s2 = nodes[dst]
      if (s1) this._ring(ctx, s1.x, s1.z, lw(12), 'rgba(0,232,255,0.8)', lw(1.4))
      if (s2 && dst !== tr.src) this._ring(ctx, s2.x, s2.z, lw(14), 'rgba(255,255,255,0.7)', lw(1.4))
    }
    if (this.sendFrom) {
      const s1 = nodes[this.sendFrom]
      if (s1) this._ring(ctx, s1.x, s1.z, lw(16), '#00E8FF', lw(2))
    }
  },

  // 6) 事件闪烁圈: 送达=青绿扩散 / 失败=红
  _drawFlashes(ctx, lw, dt) {
    for (let i = this.flashes.length - 1; i >= 0; i--) {
      const f = this.flashes[i]
      f.age += dt
      if (f.age > 0.7) { this.flashes.splice(i, 1); continue }
      const t = f.age / 0.7
      ctx.strokeStyle = (f.ok ? 'rgba(53,255,158,' : 'rgba(255,90,80,')
        + ((1 - t) * 0.9).toFixed(3) + ')'
      ctx.lineWidth = lw(2.4 * (1 - t) + 0.4)
      ctx.beginPath(); ctx.arc(f.x, f.z, lw(6) + t * lw(40), 0, Math.PI * 2); ctx.stroke()
    }
  },

  // 7) 失败红叉: 报文死在哪 (超时/无路/重传耗尽/握手失败), 红叉停 2s 淡出
  _drawCrosses(ctx, lw, dt) {
    for (let i = this.crosses.length - 1; i >= 0; i--) {
      const c = this.crosses[i]
      c.age += dt
      if (c.age > 2) { this.crosses.splice(i, 1); continue }
      const a = c.age < 1.6 ? 1 : (2 - c.age) / 0.4
      const s = lw(9)
      ctx.strokeStyle = 'rgba(255, 80, 64, ' + (a * 0.95).toFixed(3) + ')'
      ctx.lineWidth = lw(2.8)
      ctx.shadowColor = '#FF4030'
      ctx.shadowBlur = 12
      ctx.beginPath()
      ctx.moveTo(c.x - s, c.z - s); ctx.lineTo(c.x + s, c.z + s)
      ctx.moveTo(c.x + s, c.z - s); ctx.lineTo(c.x - s, c.z + s)
      ctx.stroke()
      ctx.shadowBlur = 0
      ctx.fillStyle = 'rgba(255, 150, 140, ' + (a * 0.9).toFixed(3) + ')'
      ctx.fillText('✗ 报文失败', c.x, c.z + lw(20))
    }
  },

  _ring(ctx, x, z, r, col, w) {
    ctx.strokeStyle = col; ctx.lineWidth = w
    ctx.beginPath(); ctx.arc(x, z, r, 0, Math.PI * 2); ctx.stroke()
  },
}
