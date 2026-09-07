// WebSocket 客户端: 与后端仿真引擎双向实时通信
// 地址按当前页面来源自动推导: 本机跑 vite -> ws://127.0.0.1:5000 (直连后端);
// 经公网域名 (cloudflare 隧道等) -> wss://同域名/ws (隧道自动转发)
const WS_URL = location.hostname === 'localhost' || location.hostname === '127.0.0.1'
  ? 'ws://127.0.0.1:5000/ws'
  : (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws'

export class SimClient {
  constructor({ onSnapshot, onGeology }) {
    this.onSnapshot = onSnapshot
    this.onGeology = onGeology
    this.connect()
  }

  connect() {
    this.ws = new WebSocket(WS_URL)
    this.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.cmd === 'geology') {
        this.onGeology?.(msg.geology)
      } else if (msg.tick !== undefined) {
        this.onSnapshot?.(msg)
      }
    }
    this.ws.onclose = () => setTimeout(() => this.connect(), 1500)
  }

  setParam(nodeId, params) {
    this.send({ cmd: 'set_param', node: nodeId, params })
  }

  disaster(kind) {
    this.send({ cmd: 'disaster', kind })
  }

  send(obj) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj))
  }
}
