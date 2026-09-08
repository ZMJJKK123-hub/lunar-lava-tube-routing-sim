// 静息层绘制器 (离屏缓存): 深空底色/腔体/巨石/墙体/暗绿连线/节点图标
// (挂到 Radar2D.prototype; 仅在 staticDirty 时整层重绘, 未 Hover 时零开销)

export const staticDraw = {
  /* ---------- 静息层 (离屏) ---------- */
  _renderStatic(W, H) {
    const o = this.offCtx
    const dpr = this.dpr
    o.setTransform(dpr, 0, 0, dpr, 0, 0)
    // 纯净深空底色, 无任何网格/图案
    o.fillStyle = '#0A0F1A'
    o.fillRect(0, 0, W, H)
    const snap = this.snapshot, geo = this.geology
    if (!snap || !geo) return

    o.save()
    o.translate(this.view.x, this.view.y)
    o.scale(this.view.scale, this.view.scale)
    const lw = (px) => px / this.view.scale

    // 溶洞腔体: 暗色填充 + 极淡描边 —— 熔岩管平面示意轮廓 (扁椭圆, 腔外=岩壁)
    if (this.chamberPaths?.length) {
      o.fillStyle = 'rgba(28,46,74,0.5)'
      o.strokeStyle = 'rgba(105,145,196,0.3)'
      o.lineWidth = lw(1.6)
      for (const p of this.chamberPaths) { o.fill(p); o.stroke(p) }
    }

    // 巨石 / 巨柱 (实心岩石: 高不透明填充 + 亮描边 + 裂纹, 一眼可辨)
    this._drawRocks(o, lw)
    ;(geo.pillars ?? []).forEach((p) => {
      o.beginPath(); o.arc(p.x, p.z, p.r, 0, Math.PI * 2)
      o.fillStyle = 'rgba(105,95,135,0.26)'; o.fill()
      o.strokeStyle = 'rgba(160,145,205,0.35)'; o.lineWidth = lw(1); o.stroke()
    })

    // 用户墙体
    this._drawWalls(o, lw)
    // (机器人已移至动态层: 移动平滑 + 覆盖圈 + SOS 脉冲 —— 见 _drawRobot)

    // ---- 全局静息连线: 按链路健康度分桶着色 ----
    // 健康=暗绿(静息); SNR 余量被压低(干扰逼近/弱链)=橙; 临界=红
    // (干扰源走到哪, 哪片先"变红"再断线 —— 退化过程肉眼可见)
    const REQ_DB = { UWB: 8, LoRa: -15 }
    const edges = { ok: [], warm: [], hot: [] }
    for (const lk of snap.links ?? []) {
      if (!lk.up) continue
      const na = snap.nodes[lk.a], nb = snap.nodes[lk.b]
      if (!na || !nb) continue
      const m = lk.snr_db - (REQ_DB[lk.band] ?? 8)   // 距解调门限的余量
      edges[m < 6 ? 'hot' : m < 14 ? 'warm' : 'ok'].push([na, nb])
    }
    const drawEdges = (pairs, style, width) => {
      if (!pairs.length) return
      o.strokeStyle = style; o.lineWidth = width
      o.beginPath()
      for (const [na, nb] of pairs) { o.moveTo(na.x, na.z); o.lineTo(nb.x, nb.z) }
      o.stroke()
    }
    drawEdges(edges.ok, 'rgba(70,150,100,0.15)', lw(1))
    drawEdges(edges.warm, 'rgba(255,170,70,0.32)', lw(1.3))
    drawEdges(edges.hot, 'rgba(255,80,70,0.5)', lw(1.6))

    // ---- 节点 (静态图标, 无呼吸动画) ----
    this._drawNodes(o, snap, lw)
    o.restore()
  },

  _drawRocks(o, lw) {
    const snap = this.snapshot
    snap.obstacles.forEach((ob, i) => {
      const p = this._rockPath(ob, i)
      const boulder = ob.shape === 'boulder'
      const held = this.drag?.type === 'obstacle' && this.drag.idx === i
      o.fillStyle = boulder ? 'rgba(126,86,64,0.92)' : 'rgba(98,104,120,0.92)'
      o.fill(p)
      o.strokeStyle = held ? '#00FFFF' : (boulder ? 'rgba(205,140,105,0.95)' : 'rgba(165,175,195,0.95)')
      o.lineWidth = held ? lw(2.4) : lw(1.3)
      o.stroke(p)
      // 岩石裂纹 (种子化 2 条短折线)
      o.strokeStyle = boulder ? 'rgba(70,44,30,0.85)' : 'rgba(52,56,68,0.85)'
      o.lineWidth = lw(0.9)
      for (let c = 0; c < 2; c++) {
        const a1 = this._noise(i * 97 + c * 31) * Math.PI * 2
        const x1 = ob.x + Math.cos(a1) * ob.r * 0.7
        const z1 = ob.z + Math.sin(a1) * ob.r * 0.7
        const x2 = ob.x + Math.cos(a1 + 2.2) * ob.r * 0.55
        const z2 = ob.z + Math.sin(a1 + 2.2) * ob.r * 0.55
        o.beginPath(); o.moveTo(x1, z1); o.lineTo(x2, z2); o.stroke()
      }
    })
  },

  _drawWalls(o, lw) {
    o.strokeStyle = '#b8503a'; o.lineWidth = lw(2.6)
    ;(this.snapshot.walls ?? []).forEach((w) => {
      o.beginPath(); o.moveTo(w.x1, w.z1); o.lineTo(w.x2, w.z2); o.stroke()
    })
    if (this.drag?.type === 'wall' && this.drag.moved) {
      o.strokeStyle = 'rgba(220,110,90,0.55)'
      o.setLineDash([12, 9]); o.lineWidth = lw(1.8)
      o.beginPath()
      o.moveTo(this.drag.x1, this.drag.z1); o.lineTo(this.drag.x2, this.drag.z2)
      o.stroke(); o.setLineDash([])
    }
  },

  _rockPath(o, idx) {
    const key = idx + ':' + o.x + ':' + o.z + ':' + o.r   // 含坐标: 拖动后轮廓必须重建
    if (this._seedPath[key]) return this._seedPath[key]
    const p = new Path2D()
    const N = 9
    for (let k = 0; k <= N; k++) {
      const a = (k / N) * Math.PI * 2
      const wob = 0.72 + this._noise(idx * 31 + k * 7) * 0.6
      const r = o.r * wob
      const x = o.x + Math.cos(a) * r, z = o.z + Math.sin(a) * r
      k === 0 ? p.moveTo(x, z) : p.lineTo(x, z)
    }
    p.closePath()
    this._seedPath[key] = p
    return p
  },

  _drawNodes(o, snap, lw) {
    for (const [id, n] of Object.entries(snap.nodes)) {
      const r = lw(id === 'NODE-00' ? 10 : (n.role === 'beacon' ? 5 : 6.5))
      const hot = n.temp_c > 60
      const lowbat = n.battery_soc < 25
      let color = '#39d7c4'
      if (n.state === 'DEAD') color = '#4a5260'
      else if (hot) color = '#FF6050'
      else if (lowbat || n.state === 'DEGRADED') color = '#FFC04D'
      else if (n.role === 'beacon') color = '#D8B860'   // 道钉: 金色系

      o.strokeStyle = color
      o.fillStyle = n.role === 'beacon' ? 'rgba(120,95,40,0.55)' : '#0A0F1A'
      o.lineWidth = lw(id === this.selectedId ? 2.2 : 1.5)
      o.beginPath()
      if (id === 'NODE-00') {
        for (let k = 0; k < 6; k++) {
          const a = Math.PI / 6 + (k / 6) * Math.PI * 2
          const x = n.x + Math.cos(a) * r, z = n.z + Math.sin(a) * r
          k === 0 ? o.moveTo(x, z) : o.lineTo(x, z)
        }
        o.closePath()
      } else if (n.role === 'beacon') {
        // 道钉: 实心小方块 (机器人投放的永久中继)
        const s = r * 0.8
        o.rect(n.x - s, n.z - s, s * 2, s * 2)
      } else if (n.role === 'sensor') {
        o.moveTo(n.x, n.z - r); o.lineTo(n.x + r, n.z)
        o.lineTo(n.x, n.z + r); o.lineTo(n.x - r, n.z); o.closePath()
      } else {
        o.arc(n.x, n.z, r, 0, Math.PI * 2)
      }
      o.fill(); o.stroke()
      // 电量环: 节点外圈按 SoC 比例填充 (绿>50% / 黄25~50% / 红<25%)
      if (n.state !== 'DEAD') {
        const soc = Math.min(1, Math.max(0, (n.battery_soc ?? 100) / 100))
        o.strokeStyle = soc > 0.5 ? 'rgba(90,230,140,0.85)'
                       : soc > 0.25 ? 'rgba(255,200,80,0.9)'
                       : 'rgba(255,90,70,0.95)'
        o.lineWidth = lw(1.5)
        o.beginPath()
        o.arc(n.x, n.z, r + lw(3.5), -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * soc)
        o.stroke()
      }
      // 度数自保: 功率自举中的节点画琥珀虚线环 (链路不足, 正在调大功率自救)
      if (n.pboost && n.state !== 'DEAD') {
        o.strokeStyle = 'rgba(255,200,90,0.9)'
        o.lineWidth = lw(1.4)
        o.setLineDash([lw(4), lw(3)])
        o.beginPath(); o.arc(n.x, n.z, r + lw(12), 0, Math.PI * 2); o.stroke()
        o.setLineDash([])
      }
      // 积压弧: 仅显示超出链流量配额(50%)的真实数据拥塞 (青色, 更外圈)
      if (n.queue_pct > 50.5 && n.state !== 'DEAD') {
        o.strokeStyle = 'rgba(0,232,255,0.95)'
        o.lineWidth = lw(1.8)
        o.beginPath()
        o.arc(n.x, n.z, r + lw(6.5), -Math.PI / 2,
              -Math.PI / 2 + Math.PI * 2 * Math.min(1, (n.queue_pct - 50) / 50))
        o.stroke()
      }
      if (id === this.selectedId) {
        o.strokeStyle = 'rgba(255,255,255,0.75)'; o.lineWidth = lw(1)
        o.beginPath(); o.arc(n.x, n.z, r + lw(9.5), 0, Math.PI * 2); o.stroke()
      }
      if (n.state === 'DEAD') {
        o.strokeStyle = '#FF5050'; o.lineWidth = lw(1.8)
        const s = r * 0.7
        o.beginPath()
        o.moveTo(n.x - s, n.z - s); o.lineTo(n.x + s, n.z + s)
        o.moveTo(n.x + s, n.z - s); o.lineTo(n.x - s, n.z + s)
        o.stroke()
      }
      // 像素风标签 (仅缩放足够时绘制, 保持画面干净)
      if (this.view.scale > 0.22) {
        o.fillStyle = 'rgba(150,190,220,0.66)'
        o.font = Math.max(8, lw(9)) + 'px Consolas,monospace'
        o.fillText(id.startsWith('BEACON') ? '📍' + id.slice(-2) : id.replace('NODE-', 'N-'),
                   n.x + r + lw(3), n.z - r - lw(2))
      }
    }
  },
}
