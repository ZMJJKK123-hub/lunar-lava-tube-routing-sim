// Radar2D —— 2D 极简蓝图沙盘引擎 v2 (Canvas 2D, 60FPS)
//
// 设计原则:
//   1. 纯净画布: 纯 #0A0F1A 背景, 只画 溶洞边界/障碍物/节点/连线, 零装饰图案。
//   2. 全局静息: 默认所有连线 opacity 0.15 暗绿实线, 无发光无动画 —— 若隐若现的暗网。
//   3. Hover 激发: 悬停节点 A -> 仅 A 的直连边高亮发光 + 邻域信息
//      (通信范围圈/超距衰减线/被挡视线)。
//   4. 真实数据流: 数据包以匀速发光方块沿路径滑动 (本地时钟, 每跳 0.25s),
//      有真实流量的边自动亮起; 连接接纳在发送瞬间完成;
//      报文失败(超时/无路/重传耗尽)在出事位置显示红叉。
//   5. 渲染总线: 后端任意层上报的报文跳 (kind 区分) 由通用绘制器自动上屏;
//      样式表只是美化覆盖, 未知 kind 按名称哈希自动配色 —— 新类型零注册。
//   6. 性能: 静息层缓存到离屏 canvas, 仅数据/视图变化时重绘;
//      未 Hover 时 rAF 只做一次位图拷贝, 零动画开销。

// 样式表 (可选覆盖): 链上四种泛洪报文的视觉语言, 与 DATA 方块严格区分
// (稳态 SYNC 流量每 tick 上百跳, 紫系一律小而暗 —— BLOCK 才是主角)
const KIND_STYLE = {
  BLOCK:     { color: '#A5F4FF', size: 5.5, glow: 18 },              // 出块波: 亮青白大光点
  SYNC_RESP: { color: '#B08CFF', size: 2.6, glow: 7, stream: 3 },    // 批量追块: 暗紫串点
  SYNC_REQ:  { color: '#8E7CFF', size: 2.0, glow: 5 },               // 追块请求: 暗紫微点
  TX:        { color: '#E8C06E', size: 2.0, glow: 5 },               // 遥测交易: 暗金微点
  SOS:       { color: '#FF8A5C', size: 3.2, glow: 12 },             // 呼救信标: 橙红点
}
// 零注册兜底: 未知 kind 按名称哈希取色 —— 后端新报文类型自动上屏
function autoKindStyle(kind) {
  let h = 0
  for (let i = 0; i < kind.length; i++) h = (h * 31 + kind.charCodeAt(i)) >>> 0
  return { color: `hsl(${h % 360} 85% 72%)`, size: 3.2, glow: 9 }
}
export class Radar2D {
  constructor(container, { client, onSelect }) {
    this.container = container
    this.client = client
    this.onSelect = onSelect
    this.snapshot = null
    this.geology = null
    this.selectedId = null
    this.hoverId = null
    this.hoverEdges = []           // [{na: A端, nb: 邻居端}]
    this.drag = null
    this.sendFrom = null           // 发消息模式: 已选源节点, 等待点目标
    this._pkSmooth = new Map()     // 报文方块平滑进度 key -> {t}
    this.flashes = []              // 真实报文事件触发的节点闪烁圈
    this.crosses = []              // 报文失败位置的红叉 (发不出去一眼可见)
    this._lastEvId = -1
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
    this.showChain = true          // 渲染总线: 链上报文跳 (TX/BLOCK/SYNC_*) 显示开关
    this.showData = true           // 传输层 DATA 方块显示开关
    this._snapPerf = 0             // 最近一次快照到达的本地时刻 (总线点本地续走用)
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
      const rz = c.rz ?? c.r
      minX = Math.min(minX, c.x - c.r); maxX = Math.max(maxX, c.x + c.r)
      minZ = Math.min(minZ, c.z - rz); maxZ = Math.max(maxZ, c.z + rz)
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
      const rx = c.r, rz = c.rz ?? c.r      // 扁椭圆腔室: 长轴 x / 短轴 z
      for (let k = 0; k <= N; k++) {
        const a = (k / N) * Math.PI * 2
        const wob = 1 + (this._noise(ci * 13 + k) - 0.5) * 0.14
        const x = c.x + Math.cos(a) * rx * wob, z = c.z + Math.sin(a) * rz * wob
        k === 0 ? p.moveTo(x, z) : p.lineTo(x, z)
      }
      p.closePath()
      return p
    })
    this.staticDirty = true
  }
  update(snapshot) {
    this.snapshot = snapshot
    this._snapPerf = performance.now()
    this.staticDirty = true          // 节点/边数据 5Hz 变化 -> 静息层重绘
    this._refreshHoverEdges()
    this._collectFlashes()           // 真实报文事件 -> 送达闪烁 / 失败红叉
    if (this.infoPanel) this._fillInfoPanel()
  }

  /* 真实报文事件: 送达 -> 青绿闪烁圈; 失败/超时 -> 红圈 + 红叉停在出事节点 */
  _collectFlashes() {
    const evs = this.snapshot?.events ?? []
    if (this._lastEvId < 0 && evs.length) this._lastEvId = evs[evs.length - 1].id
    for (const e of evs) {
      if (e.id <= this._lastEvId) continue
      this._lastEvId = e.id
      if (e.type === 'msg_delivered' || e.type === 'msg_timeout'
          || e.type === 'msg_fail' || e.type === 'msg_no_path') {
        const n = this.snapshot?.nodes?.[e.node]
        if (!n) continue
        const ok = e.type === 'msg_delivered'
        this.flashes.push({ x: n.x, z: n.z, age: 0, ok })
        if (!ok) this.crosses.push({ x: n.x, z: n.z, age: 0 })
      }
    }
  }

  /* 真实报文事件 -> 节点闪烁圈 (送达=青绿, 超时/失败=红) */
  _collectFlashes() {
    const evs = this.snapshot?.events ?? []
    if (this._lastEvId < 0 && evs.length) this._lastEvId = evs[evs.length - 1].id
    for (const e of evs) {
      if (e.id <= this._lastEvId) continue
      this._lastEvId = e.id
      if (e.type === 'msg_delivered' || e.type === 'msg_timeout' || e.type === 'msg_fail') {
        const n = this.snapshot?.nodes?.[e.node]
        if (n) this.flashes.push({ x: n.x, z: n.z, age: 0, ok: e.type === 'msg_delivered' })
      }
    }
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
      if (e.key === 'Escape' && this.sendFrom) this._cancelSend()
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
      if (d < Math.max(8, o.r * this.view.scale * 1.05) && d < bd) { bd = d; best = i }
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
    if (this.sendFrom) {                          // 发消息模式: 点击选择目标节点
      const nid = this._hitNode(sx, sy)
      if (nid && nid !== this.sendFrom
          && this.snapshot.nodes[nid].state !== 'DEAD') {
        this.client?.send({ cmd: 'send_msg', src: this.sendFrom, dst: nid, bytes: 2048 })
        this._sendHint('📤 已发送 ' + this.sendFrom.replace('NODE-', 'N-')
          + ' → ' + nid.replace('NODE-', 'N-') + ' (2KB, 观察方块沿线传输)', 2600)
      } else {
        this._sendHint('已取消发送', 900)
      }
      this._cancelSend()
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
      mk('📤 发送消息到…', '#7fd8ff', () => this._startSendTo(nid))
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

  /* ================= 发消息模式 (右键菜单发起, 两步点击) ================= */
  _startSendTo(nid) {
    this.sendFrom = nid
    this.canvas.style.cursor = 'crosshair'
    this._sendHint('📡 源 ' + nid.replace('NODE-', 'N-')
      + ' — 点击目标节点发送 2KB 报文 (Esc 取消)', 0)
  }
  _cancelSend() {
    this.sendFrom = null
    this.canvas.style.cursor = this.wallMode ? this.cursorWall : 'crosshair'
    this._hideSendHint()
  }
  _sendHint(text, ms) {
    this._hideSendHint()
    const d = document.createElement('div')
    d.textContent = text
    d.style.cssText = 'position:absolute; z-index:38; left:50%; top:14px; transform:translateX(-50%);' +
      'background:rgba(8,16,30,0.92); border:1px solid #1f4a6f; border-radius:4px;' +
      'font:12px Consolas,monospace; color:#9fe8ff; padding:7px 16px; pointer-events:none;' +
      'box-shadow:0 0 14px rgba(0,80,120,0.4); white-space:nowrap'
    this.container.appendChild(d)
    this._sendHintEl = d
    if (ms > 0) this._sendHintTimer = setTimeout(() => this._hideSendHint(), ms)
  }
  _hideSendHint() {
    clearTimeout(this._sendHintTimer)
    this._sendHintEl?.remove()
    this._sendHintEl = null
  }

  /* ================= 渲染主循环 ================= */
  animate(ts) {
    requestAnimationFrame(this.animate)
    // 帧率封顶 ~70fps: 高刷屏下 rAF 可达 240Hz, 本画面 70fps 足够,
    // 省 3 倍绘制/GPU 开销 (发光点是 shadowBlur 大户)
    if (this._lastTs && ts - this._lastTs < 14) return
    this._lastTs = ts
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

    // 动态层: 渲染总线报文点 (链上泛洪等) + DATA 方块/活跃边/事件闪烁
    this._drawBusDots(ctx)
    if (this.showData) this._drawTransport(ctx)
    this._drawRobot(ctx)
    // Hover 层: 高亮邻边 + 邻域信息 (悬停时)
    if (this.hoverId && this.hoverEdges.length) this._drawHoverGlow(ctx)
  }

  /* ---------- 通用渲染总线绘制器: 非 DATA 报文跳一律自动上屏 ----------
     样式表可选覆盖, 未知 kind 按名称哈希自动配色 (零注册);
     r=false 的跳半透明 (接收方已去重吸收, 波前止步);
     t 为后端快照时刻的进度, 此后用本地时钟续走, 消除 0.2s 快照间隔的顿挫 */
  /* ---------- 通用渲染总线绘制器 (指令模型, 前端完全自治) ----------
     后端每 tick 只下发"这一跳从 a 飞往 b"的指令 (p.t=快照构建时已飞进度);
     前端反推起飞时刻后, 位置完全由本地时钟推进 —— 快照早到/迟到/丢帧都
     不影响运动。以 150ms 渲染延迟播放"过去的世界", 换取每跳从节点完整
     出发 -> 到站淡出消失的全程动画 (无中途生成/钳制冻结)。 */
  _drawBusDots(ctx) {
    const snap = this.snapshot
    if (!snap || !this.showChain) return
    const nodes = Object.assign(Object.create(null), snap.nodes)
    if (snap.robot) nodes.ROBOT = { x: snap.robot.x, z: snap.robot.z }
    const pk = (snap.packets ?? []).filter((p) => p.kind && p.kind !== 'DATA')
    const bus = this._busHops ?? (this._busHops = { tick: -1, hops: [] })
    if (bus.tick !== snap.tick) {              // 新 tick: 换装下一批指令
      const now = performance.now()
      bus.tick = snap.tick
      bus.hops = pk.map((p) => ({ ...p, startAt: now - p.t * 250 }))
    }
    let hops = bus.hops
    if (!hops.length) return
    // 显示采样: BLOCK 全保留, 其余超 ~140 跳时等距抽样 (保风暴氛围)
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
        const f = (now - p.startAt) / 250 - k * 0.09
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
  }

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
    const CHAN_COL = ['#00E8FF', '#FFC04D', '#B08CFF']

    // 1) 有真实流量的边自动亮起 (青色霓虹, 盖过静息暗绿; 仅 DATA, 链上点不染边)
    const seen = new Set()
    for (const p of pk) {
      if (p.t < 0 || p.kind !== 'DATA') continue
      seen.add(p.a < p.b ? p.a + '|' + p.b : p.b + '|' + p.a)
    }
    if (seen.size) {
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
    }

    ctx.font = 'bold ' + Math.max(8, lw(9)) + 'px Consolas,monospace'
    ctx.textAlign = 'center'

    // 2) 排队徽章: 节点缓冲中等待发送的报文数 (半双工: 每 tick 每节点仅一个
    //    发送名额)。数字 = 排队中的报文 —— 不再在路上冻结/节点旁堆小方块
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

    // 3) DATA 分段: 匀速直发动画 —— 纯本地时钟推进(每跳 0.25s), 零回拉零纠偏。
    //    快照只负责: 路径形状 / 停驻等待(真实拥塞) / 生命周期 / 严重超前校正。
    const alive = new Set()
    for (const p of pk) {
      if (p.kind !== 'DATA') continue
      const key = p.msg + ':' + p.seg          // 跨跳稳定的旅程键
      alive.add(key)
      const path = (p.path ?? []).map(id => nodes[id]).filter(Boolean)
      if (path.length < 2) continue
      const total = path.length - 1
      let s = this._pkSmooth.get(key)
      if (!s) { s = { h: p.ph }; this._pkSmooth.set(key, s) }
      if (p.t < 0) {
        // 排队中: 不上路绘制 (节点徽章已示意), 静默把进度对齐到节点,
        // 恢复飞行时从节点起飞 —— 消灭"冻在半路"的观感
        s.h = Math.max(s.h, p.ph)
        continue
      }
      // 纯匀速模型: 进度 = 已飞跳数 s.h, 飞行时每 0.25s 匀速前进一跳。
      s.h = Math.min(total, s.h + dt / 0.25)
      const f = total > 0 ? Math.min(1, Math.max(0, s.h / total)) : 1
      // 按路程比例在折线上取点 (各段按欧氏长度加权)
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
      const col = CHAN_COL[p.chan ?? 0] ?? '#00E8FF'
      ctx.shadowColor = col
      ctx.shadowBlur = 10
      ctx.fillStyle = col
      const w = lw(6.5)
      ctx.beginPath()
      if (ctx.roundRect) ctx.roundRect(x - w / 2, z - w / 2, w, w, lw(1.5))
      else ctx.rect(x - w / 2, z - w / 2, w, w)
      ctx.fill()
      ctx.shadowBlur = 0
      ctx.fillStyle = 'rgba(220,245,255,0.9)'
      const fmtB = (b) => (b >= 1024 ? (b / 1024).toFixed(b % 1024 ? 1 : 0) + 'KB' : b + 'B')
      ctx.fillText('DATA ' + fmtB(p.bytes), x, z - lw(10))
    }
    for (const k of this._pkSmooth.keys()) if (!alive.has(k)) this._pkSmooth.delete(k)

    // 4) 在途报文的源/目的节点标记环
    for (const tr of snap.traffic ?? []) {
      const dst = tr.path?.[tr.path.length - 1]
      const s1 = nodes[tr.src], s2 = nodes[dst]
      if (s1) this._ring(ctx, s1.x, s1.z, lw(12), 'rgba(0,232,255,0.8)', lw(1.4))
      if (s2 && dst !== tr.src) this._ring(ctx, s2.x, s2.z, lw(14), 'rgba(255,255,255,0.7)', lw(1.4))
    }

    // 5) 发消息模式: 源节点常亮大环
    if (this.sendFrom) {
      const s1 = nodes[this.sendFrom]
      if (s1) this._ring(ctx, s1.x, s1.z, lw(16), '#00E8FF', lw(2))
    }

    // 6) 事件闪烁圈: 送达=青绿扩散 / 失败=红
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

    // 7) 失败红叉: 报文死在哪 (超时/无路/重传耗尽/握手失败), 红叉停 2s 淡出
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
    ctx.restore()
  }
  _ring(ctx, x, z, r, col, w) {
    ctx.strokeStyle = col; ctx.lineWidth = w
    ctx.beginPath(); ctx.arc(x, z, r, 0, Math.PI * 2); ctx.stroke()
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

    // 溶洞腔体: 暗色填充 + 极淡描边 —— 熔岩管平面示意轮廓 (扁椭圆, 腔外=岩壁)
    if (this.chamberPaths?.length) {
      o.fillStyle = 'rgba(28,46,74,0.5)'
      o.strokeStyle = 'rgba(105,145,196,0.3)'
      o.lineWidth = lw(1.6)
      for (const p of this.chamberPaths) { o.fill(p); o.stroke(p) }
    }

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
    // (机器人已移至动态层: 移动平滑 + 覆盖圈 + SOS 脉冲 —— 见 _drawRobot)

    // ---- 节点 (静态图标, 无呼吸动画) ----
    this._drawNodes(o, snap, lw)
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

    // 高亮邻边: 半透明底线 + 霓虹光晕 (真实报文方块由动态层负责)
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
  }

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
  }

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
      // 航位推算: 后端每 tick(0.25s)离散步进, 前端由最近两采样求速度,
      // 渲染"此刻应在哪" (匀速外推, 上限 1.5 周期, 10% 阻尼) —— 消除 5Hz 阶梯
      const nowRb = performance.now()
      if (!this._rb || this._rb.tick !== snap.tick) {
        const jump = this._rb ? Math.hypot(rb.x - this._rb.cx, rb.z - this._rb.cz)
                              : Infinity
        this._rb = {
          tick: snap.tick,
          px: this._rb ? this._rb.cx : rb.x, pz: this._rb ? this._rb.cz : rb.z,
          cx: rb.x, cz: rb.z,
          tPrev: this._rb ? this._rb.tCurr : nowRb - 200,
          tCurr: nowRb,
          teleport: jump > 400,            // 瞬移(测试传送/重生): 不插值
        }
      }
      const S = this._rb
      let x, z
      if (S.teleport || S.tCurr - S.tPrev < 30) {
        x = S.cx; z = S.cz                 // 瞬移/采样异常: 直接吸附
      } else {
        // 实体插值: 沿上一段采样 (px,pz)->(cx,cz) 行进, 相位 f 随时间 0->1。
        // 滞后一个周期但永不过冲 —— 转向/急停不再"冲出去又弹回" (抽搐根除)
        const f = Math.max(0, Math.min(1,
                  (nowRb - S.tCurr) / (S.tCurr - S.tPrev)))
        x = S.px + (S.cx - S.px) * f
        z = S.pz + (S.cz - S.pz) * f
      }
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
    }
    ctx.restore()
  }

  setWallMode(on) {
    this.wallMode = !!on
    this.canvas.style.cursor = this.wallMode ? this.cursorWall : 'crosshair'
  }

  /* 渲染总线分层开关: 'chain' = 链上报文点, 'data' = 传输层 DATA 方块 */
  setLayer(layer, on) {
    if (layer === 'chain') this.showChain = !!on
    if (layer === 'data') this.showData = !!on
  }

  select(id) {
    this.selectedId = id
    this.staticDirty = true
    if (id) this._showInfoPanel(id)
  }
  dispose() {
    this.canvas.remove(); this._hideInfoPanel(); this._hideMenu(); this._hideSendHint()
  }
}
