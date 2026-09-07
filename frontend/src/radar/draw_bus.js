// 渲染总线绘制器: 链上泛洪报文点 (TX/BLOCK/SYNC_*/SOS, 指令模型)
// (挂到 Radar2D.prototype)
//
// 指令模型 (前端完全自治): 后端每 tick 只下发"这一跳从 a 飞往 b"的指令
// (p.t=快照构建时已飞进度); 前端反推起飞时刻后, 位置完全由本地时钟推进
// —— 快照早到/迟到/丢帧都不影响运动。以 150ms 渲染延迟播放"过去的世界",
// 换取每跳从节点完整出发 -> 到站淡出消失的全程动画。
// 样式表可选覆盖 (styles.KIND_STYLE), 未知 kind 按名称哈希自动配色 (零注册);
// r=false 的跳半透明 (接收方已去重吸收, 波前止步)。

import { BUS_HOP_MS, KIND_STYLE, autoKindStyle } from './styles'

export const busDraw = {
  _drawBusDots(ctx) {
    const snap = this.snapshot
    if (!snap || !this.showChain) return
    const nodes = Object.assign(Object.create(null), snap.nodes)
    if (snap.robot) nodes.ROBOT = { x: snap.robot.x, z: snap.robot.z }
    const pk = (snap.packets ?? []).filter((p) => p.kind && p.kind !== 'DATA')
    // 指令池: 每 tick 新生一批跳入池 (视觉寿命 BUS_HOP_S > tick 周期 ->
    // 多代共存, 上一代飞完自然过期), 慢速化后不会中途消失
    const pool = this._busPool ?? (this._busPool = [])
    if (pk.length && this._busTick !== snap.tick) {
      this._busTick = snap.tick
      const now = performance.now()
      for (const p of pk) {
        pool.push({ a: p.a, b: p.b, kind: p.kind, r: p.r,
                    born: now - p.t * BUS_HOP_MS })
      }
    }
    const nowCut = performance.now() - 150 - BUS_HOP_MS * 1.3
    for (let i = pool.length - 1; i >= 0; i--) if (pool[i].born < nowCut) pool.splice(i, 1)
    let hops = pool
    if (!hops.length) return
    // 显示采样: BLOCK 全保留, 其余超 ~160 时等距抽样 (保风暴氛围)
    if (hops.length > 160) {
      const blocks = hops.filter((h) => h.kind === 'BLOCK')
      const rest = hops.filter((h) => h.kind !== 'BLOCK')
      const step = rest.length / 140, sampled = []
      for (let i = 0; i < rest.length; i += step) sampled.push(rest[Math.floor(i)])
      hops = blocks.concat(sampled)
    }
    ctx.save()
    ctx.translate(this.view.x, this.view.y)
    ctx.scale(this.view.scale, this.view.scale)
    const lw = (px) => px / this.view.scale
    const now = performance.now() - 150        // 渲染延迟: 播放 150ms 前的世界
    for (const p of hops) {
      const na = nodes[p.a], nb = nodes[p.b]
      if (!na || !nb) continue
      const st = KIND_STYLE[p.kind] ?? autoKindStyle(p.kind)
      const dim = p.kind === 'BLOCK' ? 0.95 : (p.r === false ? 0.3 : 0.6)
      const trail = st.stream || 1              // 串点: 批量报文 (如 SYNC_RESP)
      for (let k = 0; k < trail; k++) {
        const f = (now - p.born) / BUS_HOP_MS - k * 0.09
        if (f <= 0 || f >= 1) continue
        const fade = Math.min(1, f / 0.12, (1 - f) / 0.15)   // 两端淡入淡出
        if (fade <= 0) continue
        const x = na.x + (nb.x - na.x) * f
        const z = na.z + (nb.z - na.z) * f
        ctx.globalAlpha = dim * (1 - k * 0.25) * fade
        ctx.shadowColor = st.color
        ctx.shadowBlur = lw(st.glow)
        ctx.fillStyle = st.color
        ctx.beginPath()
        ctx.arc(x, z, lw(st.size), 0, Math.PI * 2)
        ctx.fill()
      }
    }
    ctx.globalAlpha = 1
    ctx.shadowBlur = 0
    ctx.restore()
  },
}
