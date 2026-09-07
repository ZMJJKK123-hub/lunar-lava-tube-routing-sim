// 面板与浮层模块: 信息面板 / 右键菜单 / 发消息提示 / 事件闪烁收集
// (挂到 Radar2D.prototype; DOM 操作全部集中在此, 绘制模块零 DOM)

export const panels = {
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
  },

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
  },

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
  },

  _placeInfoPanel() {
    if (!this.infoPanel) return
    const n = this.snapshot?.nodes?.[this.infoPanel.nid]
    if (!n) return
    const [x, y] = this._w2s(n.x, n.z)
    const d = this.infoPanel.div
    const W = this.container.clientWidth, H = this.container.clientHeight
    d.style.left = Math.min(x + 22, W - 250) + 'px'
    d.style.top = Math.min(Math.max(y - 30, 8), H - 210) + 'px'
  },

  _hideInfoPanel() {
    if (this.infoPanel) { this.infoPanel.div.remove(); this.infoPanel = null }
    if (this.selectedId) { this.selectedId = null; this.staticDirty = true }
  },

  /* ================= 右键菜单 ================= */
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
  },

  _hideMenu() {
    this.menu?.remove(); this.menu = null
    if (this._menuAway) {
      clearTimeout(this._menuAwayTimer)
      document.removeEventListener('mousedown', this._menuAway)
      this._menuAway = null
    }
  },

  /* ================= 发消息模式 (右键菜单发起, 两步点击) ================= */
  _startSendTo(nid) {
    this.sendFrom = nid
    this.canvas.style.cursor = 'crosshair'
    this._sendHint('📡 源 ' + nid.replace('NODE-', 'N-')
      + ' — 点击目标节点发送 2KB 报文 (Esc 取消)', 0)
  },

  _cancelSend() {
    this.sendFrom = null
    this.canvas.style.cursor = this.wallMode ? this.cursorWall : 'crosshair'
    this._hideSendHint()
  },

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
  },

  _hideSendHint() {
    clearTimeout(this._sendHintTimer)
    this._sendHintEl?.remove()
    this._sendHintEl = null
  },
}
