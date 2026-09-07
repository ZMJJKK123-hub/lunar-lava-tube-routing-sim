// 交互模块: 鼠标/滚轮/键盘事件 + 命中检测 (挂到 Radar2D.prototype)
//
// 职责: 平移/缩放/悬停/选中/拖巨石/画墙/右键菜单入口/发消息两步点击。
// 只改 this 上的交互状态 (drag/hoverId/view), 不直接绘制。

export const interaction = {
  /* ================= 事件绑定 ================= */
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
  },

  /* ================= 命中检测 (屏幕坐标 -> 世界对象) ================= */
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
  },

  _hitNode(sx, sy) {
    if (!this.snapshot) return null
    let best = null, bd = 14
    for (const [id, n] of Object.entries(this.snapshot.nodes)) {
      const [x, y] = this._w2s(n.x, n.z)
      const d = Math.hypot(x - sx, y - sy)
      if (d < bd) { bd = d; best = id }
    }
    return best
  },

  _hitObstacle(sx, sy) {
    if (!this.snapshot) return -1
    let best = -1, bd = 1e9
    this.snapshot.obstacles.forEach((o, i) => {
      const [x, y] = this._w2s(o.x, o.z)
      const d = Math.hypot(x - sx, y - sy)
      if (d < Math.max(8, o.r * this.view.scale * 1.05) && d < bd) { bd = d; best = i }
    })
    return best
  },

  /* ================= 按下 / 移动 / 抬起 ================= */
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
  },

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
  },

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
  },
}
