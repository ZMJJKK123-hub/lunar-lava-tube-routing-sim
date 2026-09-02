// WebSocket 客户端: 与后端仿真引擎双向实时通信
const WS_URL = 'ws://127.0.0.1:5000/ws'

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
