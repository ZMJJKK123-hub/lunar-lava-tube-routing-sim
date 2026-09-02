// Radar2D —— 2D 极简蓝图沙盘引擎 v2 (Canvas 2D, 60FPS)
//
// 设计原则:
//   1. 纯净画布: 纯 #0A0F1A 背景, 只画 溶洞边界/障碍物/节点/连线, 零装饰图案。
//   2. 全局静息: 默认所有连线 opacity 0.15 暗绿实线, 无发光无动画 —— 若隐若现的暗网。
//   3. Hover 激发: 悬停节点 A -> 仅 A 的直连边变 #00FFFF 高亮发光,
//      并从 A 沿每条边播放波浪脉冲传向邻居; 移出立即恢复静息。
//   4. 性能: 静息层缓存到离屏 canvas, 仅数据/视图变化时重绘;
//      未 Hover 时 rAF 只做一次位图拷贝, 零动画开销。
export class Radar2D {
  constructor(container, { client, onSelect }) {
    this.container = container
    this.client = client
    this.onSelect = onSelect
    this.snapshot = null
    this.geology = null
    this.time = 0
    this.selectedId = null
    this.hoverId = null
    this.hoverEdges = []           // [{na: A端, nb: 邻居端}]
    this.pulseT = 0
    this.drag = null
    this.view = { x: 0, y: 0, scale: 0.32 }
    this._seedPath = {}
    this.staticDirty = true

    this.canvas = document.createElement('canvas')
    this.canvas.style.cssText = 'display:block;width:100%;height:100%;cursor:crosshair'
    container.appendChild(this.canvas)
    this.ctx = this.canvas.getContext('2d')

    // 静息层离屏缓存
    this.off = document.createElement('canvas')
    this.offCtx = this.off.getContext('2d')

    this.infoPanel = null          // 点击节点的极客数据面板
    this.wallMode = false          // 放墙模式: 关闭时左键=平移画面, 开启时左键拖=画墙
    // 放墙模式光标: 砖墙图标; 悬停到已放置的墙上 -> 红叉(点击即删除)
    this.cursorWall = this._svgCursor(
      "<rect x='2' y='5' width='22' height='7' fill='#9fb2c8' stroke='#1a2230' stroke-width='1.4'/>" +
      "<rect x='2' y='14' width='22' height='7' fill='#9fb2c8' stroke='#1a2230' stroke-width='1.4'/>" +
      "<line x1='10' y1='5' x2='10' y2='12' stroke='#1a2230' stroke-width='1.4'/>" +
      "<line x1='18' y1='5' x2='18' y2='12' stroke='#1a2230' stroke-width='1.4'/>" +
      "<line x1='6' y1='14' x2='6' y2='21' stroke='#1a2230' stroke-width='1.4'/>" +
      "<line x1='14' y1='14' x2='14' y2='21' stroke='#1a2230' stroke-width='1.4'/>" +
      "<line x1='22' y1='14' x2='22' y2='21' stroke='#1a2230' stroke-width='1.4'/>", 'cell')
    this.cursorDelX = this._svgCursor(
      "<circle cx='13' cy='13' r='11' fill='rgba(255,64,48,0.22)' stroke='#ff5040' stroke-width='2'/>" +
      "<path d='M8 8 L18 18 M18 8 L8 18' stroke='#ff3b28' stroke-width='2.6' stroke-linecap='round'/>",
      'not-allowed')
    this.menu = null
    this._bindEvents()
    this.animate = this.animate.bind(this)
    requestAnimationFrame(this.animate)
  }

  /* 自定义光标: SVG -> data URI cursor */
  _svgCursor(body, fallback) {
    const svg = "<svg xmlns='http://www.w3.org/2000/svg' width='26' height='26'>" + body + "</svg>"
    return "url(" + JSON.stringify("data:image/svg+xml;utf8," + encodeURIComponent(svg))
      + ") 13 13, " + fallback
  }

  /* ================= 坐标变换 ================= */
  _resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight
    const dpr = Math.min(devicePixelRatio, 2)
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(h * dpr)) {
      this.canvas.width = Math.round(w * dpr)
      this.canvas.height = Math.round(h * dpr)
      this.off.width = this.canvas.width
      this.off.height = this.canvas.height
      this.staticDirty = true
    }
    this.dpr = dpr
  }
  _fitView() {
    if (!this.geology) return
    let minX = 1e9, maxX = -1e9, minZ = 1e9, maxZ = -1e9
    for (const c of this.geology.chambers) {
      minX = Math.min(minX, c.x - c.r); maxX = Math.max(maxX, c.x + c.r)
      minZ = Math.min(minZ, c.z - c.r); maxZ = Math.max(maxZ, c.z + c.r)
    }
    const w = this.container.clientWidth, h = this.container.clientHeight
    const s = Math.min(w / (maxX - minX), h / (maxZ - minZ)) * 0.94
    this.view = { scale: s, x: w / 2 - (minX + maxX) / 2 * s, y: h / 2 - (minZ + maxZ) / 2 * s }
    this.staticDirty = true
  }
  _w2s(wx, wz) { return [wx * this.view.scale + this.view.x, wz * this.view.scale + this.view.y] }
  _s2w(sx, sy) { return [(sx - this.view.x) / this.view.scale, (sy - this.view.y) / this.view.scale] }

  /* ================= 数据入口 ================= */
  setGeology(geo) {
    this.geology = geo
    this._fitView()
    this.chamberPaths = geo.chambers.map((c, ci) => {
      const p = new Path2D()
      const N = 40
      for (let k = 0; k <= N; k++) {
        const a = (k / N) * Math.PI * 2
        const wob = 1 + (this._noise(ci * 13 + k) - 0.5) * 0.14
        const r = c.r * wob
        const x = c.x + Math.cos(a) * r, z = c.z + Math.sin(a) * r
        k === 0 ? p.moveTo(x, z) : p.lineTo(x, z)
      }
      p.closePath()
      return p
    })
    this.staticDirty = true
  }
  update(snapshot) {
    this.snapshot = snapshot
    this.staticDirty = true          // 节点/边数据 5Hz 变化 -> 静息层重绘
    this._refreshHoverEdges()
    if (this.infoPanel) this._fillInfoPanel()
  }
  _noise(i) { const s = Math.sin(i * 127.1 + 311.7) * 43758.5453; return s - Math.floor(s) }

  /* ================= Hover 边集合 ================= */
  _refreshHoverEdges() {
    this.hoverEdges = []
    if (!this.hoverId || !this.snapshot) return
    const snap = this.snapshot
    for (const lk of snap.links ?? []) {
      if (!lk.up) continue
      let a = null, b = null
      if (lk.a === this.hoverId) { a = snap.nodes[lk.a]; b = snap.nodes[lk.b] }
      else if (lk.b === this.hoverId) { a = snap.nodes[lk.b]; b = snap.nodes[lk.a] }
      if (!a || !b) continue
      this.hoverEdges.push({ na: a, nb: b })       // na 始终是悬停节点端
    }
  }

  /* ================= 交互 ================= */
  _bindEvents() {
    const cv = this.canvas
    cv.addEventListener('contextmenu', (e) => e.preventDefault())
    cv.addEventListener('mousedown', (e) => this._down(e))
    window.addEventListener('mousemove', (e) => this._move(e))
    window.addEventListener('mouseup', (e) => this._up(e))
    cv.addEventListener('mouseleave', () => { this.hoverId = null; this._refreshHoverEdges() })
    window.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z'
          && this.snapshot?.walls?.length) {
        e.preventDefault()
        this.client?.send({ cmd: 'remove_wall', index: this.snapshot.walls.length - 1 })
      }
    })
    cv.addEventListener('wheel', (e) => {
      e.preventDefault()
      const f = e.deltaY > 0 ? 0.9 : 1.11
      const rect = cv.getBoundingClientRect()
      const mx = e.clientX - rect.left, my = e.clientY - rect.top
      const [wx, wz] = this._s2w(mx, my)
      this.view.scale *= f
      this.view.x = mx - wx * this.view.scale
      this.view.y = my - wz * this.view.scale
      this.staticDirty = true
      this._placeInfoPanel()
    }, { passive: false })
  }
  _hitWall(sx, sy) {
    if (!this.snapshot) return -1
    for (let i = 0; i < (this.snapshot.walls ?? []).length; i++) {
      const w = this.snapshot.walls[i]
      const [x1, y1] = this._w2s(w.x1, w.z1)
      const [x2, y2] = this._w2s(w.x2, w.z2)
      // 点到线段距离 < 10px
      const dx = x2 - x1, dy = y2 - y1
      const len2 = dx * dx + dy * dy || 1
      let t = ((sx - x1) * dx + (sy - y1) * dy) / len2
      t = Math.max(0, Math.min(1, t))
      const px = x1 + t * dx, py = y1 + t * dy
      if (Math.hypot(sx - px, sy - py) < 10) return i
    }
    return -1
  }

  _hitNode(sx, sy) {
    if (!this.snapshot) return null
    let best = null, bd = 14
    for (const [id, n] of Object.entries(this.snapshot.nodes)) {
      const [x, y] = this._w2s(n.x, n.z)
      const d = Math.hypot(x - sx, y - sy)
      if (d < bd) { bd = d; best = id }
    }
    return best
  }
  _hitObstacle(sx, sy) {
    if (!this.snapshot) return -1
    let best = -1, bd = 1e9
    this.snapshot.obstacles.forEach((o, i) => {
      const [x, y] = this._w2s(o.x, o.z)
      const d = Math.hypot(x - sx, y - sy)
      if (d < Math.max(8, o.r * this.view.scale) && d < bd) { bd = d; best = i }
    })
    return best
  }
  _down(e) {
    if (!this.snapshot) return
    const rect = this.canvas.getBoundingClientRect()
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top
    if (e.button === 2) {
      const nid = this._hitNode(sx, sy)
      if (nid) this._showMenu(e.clientX, e.clientY, nid)
      return
    }
    if (this.wallMode) {                          // 放墙模式
      // 先判定是否点在已有墙上 -> 撤销该堵
      const wi = this._hitWall(sx, sy)
      if (wi >= 0) {
        this.client?.send({ cmd: 'remove_wall', index: wi })
        return
      }
      const [wx, wz] = this._s2w(sx, sy)
      this.drag = { type: 'wall', x1: wx, z1: wz, x2: wx, z2: wz, sx, sy, moved: false }
      return
    }
    const ob = this._hitObstacle(sx, sy)
    if (ob >= 0 && e.button === 0) {             // 拖巨石 (未点中墙时)
      this.drag = { type: 'obstacle', idx: ob }
      this.canvas.style.cursor = 'grabbing'
      return
    }
    // 默认模式: 左键拖 = 平移画面; 左键点(位移<6px) = 选中节点
    this.drag = { type: 'pan', lx: e.clientX, ly: e.clientY, sx, sy, moved: false }
  }
  _move(e) {
    const rect = this.canvas.getBoundingClientRect()
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top
    if (this.drag?.type === 'pan') {
      if (Math.hypot(sx - this.drag.sx, sy - this.drag.sy) > 6) this.drag.moved = true
      this.view.x += e.clientX - this.drag.lx
      this.view.y += e.clientY - this.drag.ly
      this.drag.lx = e.clientX; this.drag.ly = e.clientY
      this.staticDirty = true
      this._placeInfoPanel()
      return
    }
    if (this.drag?.type === 'wall') {
      if (Math.hypot(sx - this.drag.sx, sy - this.drag.sy) > 6) this.drag.moved = true
      const [wx, wz] = this._s2w(sx, sy)
      this.drag.x2 = wx; this.drag.z2 = wz
      return
    }
    if (this.drag?.type === 'obstacle') {
      const [wx, wz] = this._s2w(sx, sy)
      const o = this.snapshot.obstacles[this.drag.idx]
      if (o) { o.x = wx; o.z = wz; this.staticDirty = true }
      return
    }
    // 放墙模式: 悬停到已放置的墙上 -> 光标变叉叉(点击即删除), 否则墙图标
    if (this.wallMode) {
      this.canvas.style.cursor = this._hitWall(sx, sy) >= 0 ? this.cursorDelX : this.cursorWall
    }
    // 悬停: 只在命中变化时刷新边集 (避免每帧重建)
    const nid = this._hitNode(sx, sy)
    if (nid !== this.hoverId) {
      this.hoverId = nid
      this.pulseT = 0
      this._edgePh = []
      this.hoverRipples = []
      this._refreshHoverEdges()
      if (!this.wallMode) this.canvas.style.cursor = nid ? 'pointer' : 'crosshair'
    }
  }
  _up() {
    if (!this.drag) return
    const d = this.drag
    this.drag = null
    this.canvas.style.cursor = this.wallMode ? 'cell' : 'crosshair'
    if (d.type === 'pan' && !d.moved) {           // 平移未发生位移 = 单击
      const nid = this._hitNode(d.sx, d.sy)
      if (nid) {
        this.onSelect?.(nid)
        this._showInfoPanel(nid)
      } else {
        this._hideInfoPanel()
      }
      return
    }
    if (d.type === 'obstacle') {
      const o = this.snapshot?.obstacles?.[d.idx]
      if (o) this.client?.send({ cmd: 'move_obstacle', index: d.idx, x: o.x, z: o.z })
    } else if (d.type === 'wall' && d.moved) {
      this.client?.send({ cmd: 'add_wall', x1: d.x1, z1: d.z1, x2: d.x2, z2: d.z2 })
    }
  }

  /* ================= Click 数据面板 (极客风 Overlay) ================= */
  _showInfoPanel(nid) {
    this._hideInfoPanel()
    this.selectedId = nid
    this.staticDirty = true
    const div = document.createElement('div')
    div.style.cssText = [
      'position:absolute', 'z-index:35', 'pointer-events:none',
      'background:linear-gradient(160deg, rgba(8,16,30,0.97), rgba(5,10,20,0.97))',
      'border:1px solid #1f4a6f', 'border-left:3px solid #00FFFF', 'border-radius:4px',
      "padding:10px 14px", "font:11px/1.75 Consolas,'Courier New',monospace",
      'color:#9fc4e0', 'min-width:215px', 'white-space:pre',
      'box-shadow:0 4px 24px rgba(0,60,90,0.45)',
    ].join(';')
    this.infoPanel = { div, nid }
    this.container.appendChild(div)
    this._fillInfoPanel()
    this._placeInfoPanel()
  }
  _fillInfoPanel() {
    if (!this.infoPanel) return
    const n = this.snapshot?.nodes?.[this.infoPanel.nid]
    if (!n) return
    const c = (v, col = '#e8f6ff') => '<span style="color:' + col + '">' + v + '</span>'
    const hop = this.snapshot.routes?.[n.id]?.hop_count ?? '-'
    this.infoPanel.div.innerHTML =
      '<span style="color:#00FFFF">&#9656; ' + n.id + '</span>  <span style="color:#5d7ea3">[' + n.role + ']</span>\n' +
      'POS      ( ' + c(n.x.toFixed(1)) + ' , ' + c(n.z.toFixed(1)) + ' )\n' +
      'STATE    ' + c(n.state, n.state === 'ACTIVE' ? '#35ff9e' : '#ffb020') + '   RADIO ' + c(n.radio) + '\n' +
      'SoC      ' + c(n.battery_soc + '%', n.battery_soc < 25 ? '#ffb020' : '#35ff9e') + '\n' +
      'TEMP     ' + c(n.temp_c.toFixed(1) + ' °C', n.temp_c > 60 ? '#ff6050' : '#e8f6ff') + '\n' +
      'SNR      ' + c(n.snr_db + ' dB') + '   BER ' + c(n.ber.toExponential(1)) + '\n' +
      'MODE     ' + c(n.band) + '   QUEUE ' + c(Math.round(n.queue_pct) + '%') + '\n' +
      'HOP      ' + c(hop) + '   NBR ' + c(n.neighbors) +
      ((n.blocked_nbrs ?? []).length
        ? '\n<span style="color:#ff8a8a">-- 视线被挡 (图中无边, 需中继) --</span>\n' +
          (n.blocked_nbrs).map((x) =>
            '  ' + c(x.id.replace('NODE-', 'N-'), '#ffb8b8') + '  d=' + Math.round(x.d * 10) + 'm  ' +
            c(x.cause ?? '遮挡', '#ff8a8a')).join('\n')
        : '')
  }
  _placeInfoPanel() {
    if (!this.infoPanel) return
    const n = this.snapshot?.nodes?.[this.infoPanel.nid]
    if (!n) return
    const [x, y] = this._w2s(n.x, n.z)
    const d = this.infoPanel.div
    const W = this.container.clientWidth, H = this.container.clientHeight
    d.style.left = Math.min(x + 22, W - 250) + 'px'
    d.style.top = Math.min(Math.max(y - 30, 8), H - 210) + 'px'
  }
  _hideInfoPanel() {
    if (this.infoPanel) { this.infoPanel.div.remove(); this.infoPanel = null }
    if (this.selectedId) { this.selectedId = null; this.staticDirty = true }
  }

  _showMenu(cx, cy, nid) {
    this._hideMenu()
    const n = this.snapshot?.nodes?.[nid]
    this.menu = document.createElement('div')
    this.menu.style.cssText = 'position:absolute; z-index:40; left:' + cx + 'px; top:' + cy + 'px;' +
      'background:rgba(8,16,30,0.97); border:1px solid #1f4a6f; border-radius:4px;' +
      'font:12px Consolas,monospace; color:#cfe9ff; padding:4px; min-width:150px;' +
      'box-shadow:0 0 16px rgba(0,80,120,0.4)'
    const mk = (label, color, fn) => {
      const b = document.createElement('div')
      b.textContent = label
      b.style.cssText = 'padding:6px 12px; cursor:pointer; border-radius:3px; color:' + color
      b.onmouseenter = () => (b.style.background = 'rgba(30,90,140,0.3)')
      b.onmouseleave = () => (b.style.background = 'none')
      b.onclick = () => { fn(); this._hideMenu() }
      this.menu.appendChild(b)
    }
    if (n?.state === 'DEAD') {
      mk('♻ 恢复此节点', '#9affc0', () => this.client?.send({ cmd: 'set_param', node: nid, params: { state: 'ACTIVE' } }))
    } else {
      mk('☠ 手动破坏此节点', '#ff8a8a', () => this.client?.send({ cmd: 'set_param', node: nid, params: { state: 'DEAD' } }))
      mk('🔥 过热测试 (+80°C)', '#ffb060', () => this.client?.send({ cmd: 'set_param', node: nid, params: { temp_c: Math.min(120, n.temp_c + 80) } }))
    }
    this.container.appendChild(this.menu)
    // 点击菜单外任意位置收起菜单。延迟一帧注册: 弹出菜单的那次右键事件
    // 仍在冒泡途中, 立即注册会被同一次点击误触发 (菜单刚弹就被自己关掉)
    this._menuAway = (e) => {
      if (this.menu && !this.menu.contains(e.target)) this._hideMenu()
    }
    this._menuAwayTimer = setTimeout(
      () => document.addEventListener('mousedown', this._menuAway), 0)
  }
  _hideMenu() {
    this.menu?.remove(); this.menu = null
    if (this._menuAway) {
      clearTimeout(this._menuAwayTimer)
      document.removeEventListener('mousedown', this._menuAway)
      this._menuAway = null
    }
  }

  /* ================= 渲染主循环 ================= */
  animate() {
    requestAnimationFrame(this.animate)
    try {
      this._frame()
    } catch (err) {
      if (!this._err) { this._err = String(err?.stack || err); console.error(err) }
    }
  }
  _frame() {
    this._resize()
    const W = this.canvas.width / this.dpr, H = this.canvas.height / this.dpr
    const ctx = this.ctx

    // 静息层: 仅在数据/视图/交互变化时重绘 (未 Hover 时几乎零开销)
    if (this.staticDirty) {
      this._renderStatic(W, H)
      this.staticDirty = false
    }
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)
    ctx.clearRect(0, 0, W, H)
    ctx.drawImage(this.off, 0, 0, W, H)

    // Hover 层: 高亮邻边 + 波浪脉冲 (只在悬停时逐帧绘制)
    if (this.hoverId && this.hoverEdges.length) {
      this.time += 0.016
      this._drawHoverGlow(ctx)
    }
  }

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

    // (纯 2D 沙盘: 溶洞边界与管道带不再渲染 —— 只留石头/节点/连线)

    // 巨石 / 巨柱 (实心岩石: 高不透明填充 + 亮描边 + 裂纹, 一眼可辨)
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
    ;(geo.pillars ?? []).forEach((p) => {
      o.beginPath(); o.arc(p.x, p.z, p.r, 0, Math.PI * 2)
      o.fillStyle = 'rgba(105,95,135,0.26)'; o.fill()
      o.strokeStyle = 'rgba(160,145,205,0.35)'; o.lineWidth = lw(1); o.stroke()
    })

    // 用户墙体
    o.strokeStyle = '#b8503a'; o.lineWidth = lw(2.6)
    ;(snap.walls ?? []).forEach((w) => {
      o.beginPath(); o.moveTo(w.x1, w.z1); o.lineTo(w.x2, w.z2); o.stroke()
    })
    if (this.drag?.type === 'wall' && this.drag.moved) {
      o.strokeStyle = 'rgba(220,110,90,0.55)'
      o.setLineDash([12, 9]); o.lineWidth = lw(1.8)
      o.beginPath()
      o.moveTo(this.drag.x1, this.drag.z1); o.lineTo(this.drag.x2, this.drag.z2)
      o.stroke(); o.setLineDash([])
    }

    // ---- 全局静息连线: 全部暗绿 0.15, 无发光无动画 ----
    o.strokeStyle = 'rgba(70,150,100,0.15)'
    o.lineWidth = lw(1)
    o.beginPath()
    for (const lk of snap.links ?? []) {
      if (!lk.up) continue
      const na = snap.nodes[lk.a], nb = snap.nodes[lk.b]
      if (!na || !nb) continue
      o.moveTo(na.x, na.z); o.lineTo(nb.x, nb.z)
    }
    o.stroke()
    // 机器人 (暗金点, 路径静息)
    const rb = snap.robot
    if (rb?.route?.path?.length) {
      o.strokeStyle = 'rgba(200,160,90,0.15)'; o.lineWidth = lw(1)
      o.beginPath(); o.moveTo(rb.x, rb.z)
      for (const nid of rb.route.path) {
        const n = snap.nodes[nid]
        if (n) o.lineTo(n.x, n.z)
      }
      o.stroke()
    }

    // ---- 节点 (静态图标, 无呼吸动画) ----
    this._drawNodes(o, snap, lw, rb)
    o.restore()
  }

  /* ---------- Hover 层: 局部高亮 + 波浪脉冲 ---------- */
  _drawHoverGlow(ctx) {
    const A = this.snapshot.nodes[this.hoverId]
    if (!A) return
    ctx.save()
    ctx.translate(this.view.x, this.view.y)
    ctx.scale(this.view.scale, this.view.scale)
    const lw = (px) => px / this.view.scale

    // ---- 通信范围圈: 以悬停节点为圆心的 UWB 半径(300m) 虚线大圆 ----
    // 圈内 = 距离上可达(是否直连还看视线遮挡); 圈外 = 超距
    const R_COMM = 30 * 10
    const fillG = ctx.createRadialGradient(A.x, A.z, 0, A.x, A.z, R_COMM)
    fillG.addColorStop(0, 'rgba(0,206,201,0.06)')
    fillG.addColorStop(1, 'rgba(0,206,201,0)')
    ctx.fillStyle = fillG
    ctx.beginPath(); ctx.arc(A.x, A.z, R_COMM, 0, Math.PI * 2); ctx.fill()
    ctx.setLineDash([lw(14), lw(10)])
    ctx.strokeStyle = 'rgba(0,206,201,0.4)'
    ctx.lineWidth = lw(1.4)
    ctx.beginPath(); ctx.arc(A.x, A.z, R_COMM, 0, Math.PI * 2); ctx.stroke()
    ctx.setLineDash([])

    // 高亮邻边: 半透明底线 + 霓虹光晕 (光纤通道质感, 高光留给流光)
    ctx.strokeStyle = 'rgba(0, 206, 201, 0.4)'
    ctx.lineWidth = lw(2)
    ctx.shadowColor = '#00CEC9'
    ctx.shadowBlur = 18
    ctx.beginPath()
    for (const e of this.hoverEdges) {
      ctx.moveTo(e.na.x, e.na.z); ctx.lineTo(e.nb.x, e.nb.z)
    }
    ctx.stroke()
    ctx.shadowBlur = 0

    // ---- 彗星流光 (Data Stream): 渐变流星尾迹替代圆点 ----
    // 速度: 约 2.6 秒走完一条边 (原 1.35/s 放慢 3.5 倍, 用户可看清)
    this.pulseT += 0.016 * 0.38
    const t = this.pulseT % 1
    if (!this._edgePh) this._edgePh = []
    ctx.shadowColor = '#00FFFF'
    ctx.shadowBlur = 12
    ctx.lineCap = 'round'
    this.hoverEdges.forEach((e, i) => {
      const ph = (t + i * 0.09) % 1
      const ease = ph * ph * (3 - 2 * ph)          // smoothstep 起步快末端缓
      // 当前头部坐标 (na=悬停节点端 -> nb=邻居端)
      const hx = e.na.x + (e.nb.x - e.na.x) * ease
      const hz = e.na.z + (e.nb.z - e.na.z) * ease
      // 运动方向角: atan2(dz, dx), 拖尾沿反方向拉出
      const ang = Math.atan2(e.nb.z - e.na.z, e.nb.x - e.na.x)
      const TAIL = lw(42)                          // 42px 流星尾迹长
      const tx = hx - Math.cos(ang) * TAIL
      const tz = hz - Math.sin(ang) * TAIL
      // 宽度阶梯衰减: 从发射点出发最粗, 每走 1/4 路程降一档 (能量分段耗散),
      // 视觉上呈现"信号发出时很强, 越传越弱"
      const step = Math.min(3, Math.floor(ease * 4))          // 0..3 四档
      const wNow = lw(3.8 * (1 - step * 0.24) + 0.55)         // 3.8 -> 2.9 -> 2.0 -> 1.1px
      // 渐变: 尾部完全透明 -> 青色 -> 头部高亮 (头部亮度随档位微降)
      const headA = (1 - step * 0.18).toFixed(3)
      const grad = ctx.createLinearGradient(tx, tz, hx, hz)
      grad.addColorStop(0, 'rgba(0,255,255,0)')
      grad.addColorStop(0.7, 'rgba(0,255,255,' + (0.85 - step * 0.12).toFixed(3) + ')')
      grad.addColorStop(1, 'rgba(255,255,255,' + headA + ')')
      ctx.strokeStyle = grad
      ctx.lineWidth = wNow
      ctx.beginPath(); ctx.moveTo(tx, tz); ctx.lineTo(hx, hz); ctx.stroke()
      // 到达检测: 进度回绕 = 流光抵达邻居 -> 触发涟漪
      const prev = this._edgePh[i]
      if (prev !== undefined && ph < prev) {
        if (!this.hoverRipples) this.hoverRipples = []
        this.hoverRipples.push({ x: e.nb.x, z: e.nb.z, age: 0 })
      }
      this._edgePh[i] = ph
    })
    ctx.shadowBlur = 0

    // ---- 到达涟漪: 空心圆快速扩散 + 透明度衰减 (数据送达打击感) ----
    if (this.hoverRipples?.length) {
      const dt = 0.016
      for (let k = this.hoverRipples.length - 1; k >= 0; k--) {
        const rp = this.hoverRipples[k]
        rp.age += dt
        if (rp.age > 0.45) { this.hoverRipples.splice(k, 1); continue }
        const p = rp.age / 0.45
        ctx.strokeStyle = 'rgba(0,255,255,' + ((1 - p) * 0.85).toFixed(3) + ')'
        ctx.lineWidth = lw(2 * (1 - p) + 0.4)
        ctx.beginPath(); ctx.arc(rp.x, rp.z, lw(5) + p * lw(34), 0, Math.PI * 2); ctx.stroke()
      }
    }

    // 超距邻居: 锥形衰减线 —— 模拟"信号从悬停节点出发, 传到一半因距离过远而衰竭"
    // 画法: 分 8 段, 宽度与透明度同步递减 (起点粗亮 -> 终点细到消失)
    const RANGE = 30 * 10            // UWB 仿真半径 30 x WORLD_SCALE 10 = 300 世界米
    const far = []
    for (const n2 of Object.values(this.snapshot.nodes)) {
      if (n2 === A || n2.state === 'DEAD') continue
      const d = Math.hypot(n2.x - A.x, n2.z - A.z)
      if (d > RANGE && d < RANGE * 1.6) far.push([d, n2])
    }
    far.sort((p, q) => p[0] - q[0])
    ctx.lineCap = 'butt'
    ctx.fillStyle = 'rgba(180,180,200,0.75)'
    ctx.font = Math.max(9, lw(10)) + 'px Consolas,monospace'
    for (const [d, n2] of far.slice(0, 3)) {
      const SEG = 8
      for (let k = 0; k < SEG; k++) {
        const t0 = k / SEG, t1 = (k + 1) / SEG
        ctx.strokeStyle = 'rgba(150,162,188,' + (0.5 * (1 - k / SEG) + 0.04).toFixed(3) + ')'
        ctx.lineWidth = lw(2.8 * (1 - k / SEG) + 0.22)
        ctx.beginPath()
        ctx.moveTo(A.x + (n2.x - A.x) * t0, A.z + (n2.z - A.z) * t0)
        ctx.lineTo(A.x + (n2.x - A.x) * t1, A.z + (n2.z - A.z) * t1)
        ctx.stroke()
      }
      const mx = A.x + (n2.x - A.x) * 0.62, mz = A.z + (n2.z - A.z) * 0.62
      ctx.fillText('超出通信范围 ' + Math.round(d) + 'm', mx + lw(8), mz - lw(8))
    }

    // 被挡视线: 暗红虚线 + 中点 X —— 解释"这个邻居明明很近为什么连不上"
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

    // 悬停节点本体高亮圈
    ctx.strokeStyle = '#FFFFFF'
    ctx.lineWidth = lw(1.6)
    ctx.beginPath(); ctx.arc(A.x, A.z, lw(13), 0, Math.PI * 2); ctx.stroke()
    ctx.restore()
  }

  _rockPath(o, idx) {
    const key = idx + ':' + o.r
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
  }

  _drawNodes(o, snap, lw, rb) {
    for (const [id, n] of Object.entries(snap.nodes)) {
      const r = lw(id === 'NODE-00' ? 10 : 6.5)
      const hot = n.temp_c > 60
      const lowbat = n.battery_soc < 25
      let color = '#39d7c4'
      if (n.state === 'DEAD') color = '#4a5260'
      else if (hot) color = '#FF6050'
      else if (lowbat || n.state === 'DEGRADED') color = '#FFC04D'

      o.strokeStyle = color
      o.fillStyle = '#0A0F1A'
      o.lineWidth = lw(id === this.selectedId ? 2.2 : 1.5)
      o.beginPath()
      if (id === 'NODE-00') {
        for (let k = 0; k < 6; k++) {
          const a = Math.PI / 6 + (k / 6) * Math.PI * 2
          const x = n.x + Math.cos(a) * r, z = n.z + Math.sin(a) * r
          k === 0 ? o.moveTo(x, z) : o.lineTo(x, z)
        }
        o.closePath()
      } else if (n.role === 'sensor') {
        o.moveTo(n.x, n.z - r); o.lineTo(n.x + r, n.z)
        o.lineTo(n.x, n.z + r); o.lineTo(n.x - r, n.z); o.closePath()
      } else {
        o.arc(n.x, n.z, r, 0, Math.PI * 2)
      }
      o.fill(); o.stroke()
      if (id === this.selectedId) {
        o.strokeStyle = 'rgba(255,255,255,0.75)'; o.lineWidth = lw(1)
        o.beginPath(); o.arc(n.x, n.z, r + lw(6), 0, Math.PI * 2); o.stroke()
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
        o.fillText(id.replace('NODE-', 'N-'), n.x + r + lw(3), n.z - r - lw(2))
      }
    }
    if (rb) {
      o.fillStyle = '#E8D9A8'
      o.beginPath(); o.arc(rb.x, rb.z, lw(4.5), 0, Math.PI * 2); o.fill()
      o.font = Math.max(8, lw(9)) + 'px Consolas,monospace'
      o.fillText('BOT', rb.x + lw(6), rb.z + lw(3))
    }
  }

  setWallMode(on) {
    this.wallMode = !!on
    this.canvas.style.cursor = this.wallMode ? this.cursorWall : 'crosshair'
  }

  select(id) {
    this.selectedId = id
    this.staticDirty = true
    if (id) this._showInfoPanel(id)
  }
  dispose() {
    this.canvas.remove(); this._hideInfoPanel(); this._hideMenu()
  }
}
