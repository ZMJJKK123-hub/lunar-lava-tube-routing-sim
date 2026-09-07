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
//
// 模块组织: 本文件 = 类壳 + 装配 (交互/面板/各绘制层按职责拆在同目录模块,
// 经 Object.assign(prototype) 挂载, 方法体彼此通过 this 协作)。

import { interaction } from './interaction'         // 事件与命中检测
import { panels } from './panels'                   // 信息面板/右键菜单/发送提示
import { busDraw } from './draw_bus'                // 链上泛洪报文点 (指令模型)
import { transportDraw } from './draw_transport'    // DATA 报文/边/徽章/闪烁/红叉
import { staticDraw } from './draw_static'          // 静息层 (离屏缓存)
import { hoverDraw } from './draw_hover'            // Hover 邻域情报
import { robotDraw } from './draw_robot'            // 机器人/SOS (步进平滑)

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
    // 世界重置检测: tick 骤降(上帝重置) -> 清空全部动画缓存, 杜绝跨世界残影
    if (this.snapshot && snapshot.tick < this.snapshot.tick - 100) {
      this._pkSmooth.clear?.() ?? (this._pkSmooth = new Map())
      this._busPool = []; this._busTick = -1
      this._rbQ = null; this._rbStep = null; this._rbLast = null; this._pilot = null
      this.flashes = []; this.crosses = []; this._lastEvId = -1
    }
    this.snapshot = snapshot
    this._snapPerf = performance.now()
    this.staticDirty = true          // 节点/边数据 5Hz 变化 -> 静息层重绘
    this._refreshHoverEdges()
    this._collectFlashes()           // 真实报文事件 -> 送达闪烁 / 失败红叉
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

  /* ================= 对外开关 ================= */
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

// ---- 装配: 各职责模块的方法挂到原型 (this 协作, 零复制零转发) ----
Object.assign(Radar2D.prototype,
  interaction, panels, busDraw, transportDraw, staticDraw, hoverDraw, robotDraw)
