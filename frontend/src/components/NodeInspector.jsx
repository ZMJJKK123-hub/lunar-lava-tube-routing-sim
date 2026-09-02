// 节点属性监视器 (Node Inspector): 全部底层物理参数暴露为双向绑定滑块
import { useEffect, useState } from 'react'

const slider = (label, key, min, max, step, unit, fmt = (v) => v) => ({
  label, key, min, max, step, unit, fmt,
})

// 参数面板定义: 与后端 Node.MUTABLE 一一对应
const PARAM_GROUPS = [
  {
    title: '① 能源参数 (Power)',
    items: [
      slider('电池剩余容量', 'battery_mah', 0, 12000, 50, 'mAh'),
      slider('发射电流', 'i_tx', 50, 900, 10, 'mA'),
      slider('超级电容', 'supercap_pct', 0, 100, 1, '%'),
    ],
  },
  {
    title: '② 射频与天线 (RF & Antenna)',
    items: [
      slider('发射功率 TX Power', 'tx_power_dbm', -10, 24, 0.5, 'dBm'),
      slider('接收灵敏度 RX Sens', 'rx_sensitivity_dbm', -120, -70, 0.5, 'dBm'),
      slider('天线增益', 'ant_gain_dbi', 0, 12, 0.5, 'dBi'),
      slider('天线倾角 (沉降)', 'tilt_deg', 0, 60, 1, '°'),
    ],
  },
  {
    title: '③ 环境与机械 (Environmental)',
    items: [
      slider('节点温度', 'temp_c', -80, 120, 1, '°C'),
      slider('累积辐射剂量', 'radiation_rad', 0, 40000, 100, 'rad'),
    ],
  },
  {
    title: '④ 网络与缓冲 (Networking)',
    items: [
      slider('队列积压率', 'queue_pct', 0, 100, 1, '%'),
    ],
  },
]

export default function NodeInspector({ node, routes, links, onSetParam, onClose, onReplayWave }) {
  const [pending, setPending] = useState({})

  // 后端每帧回传最新值, 未在拖动中的滑块跟随刷新
  useEffect(() => setPending({}), [node?.id])

  if (!node) return null
  const val = (key, fallback) =>
    key in pending ? pending[key] : (node[key] ?? fallback)

  const push = (key, v) => {
    setPending((p) => ({ ...p, [key]: v }))
    onSetParam(node.id, { [key]: v })   // 瞬间下发后端, 下一 Tick 立即重算
  }

  const route = routes?.[node.id]
  const stateColor = { ACTIVE: '#35ff9e', DEGRADED: '#ffb020', SEU_RESET: '#ff7a3c', DEAD: '#666' }[node.state] || '#fff'

  return (
    <div style={{
      position: 'absolute', right: 12, top: 60, bottom: 12, width: 330,
      background: 'rgba(6,12,24,0.92)', border: '1px solid #1d3a5f', borderRadius: 8,
      padding: 14, overflowY: 'auto', backdropFilter: 'blur(6px)', fontSize: 12,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
        <b style={{ fontSize: 14 }}>⚡ {node.id} <span style={{ color: stateColor }}>[{node.state}]</span></b>
        <span style={{ cursor: 'pointer', color: '#7aa' }} onClick={onClose}>✕</span>
      </div>
      <div style={{ color: '#7f9bb8', marginBottom: 10 }}>
        角色: {node.role} · 温度 {node.temp_c}°C · SoC {node.battery_soc}% · 温度衰减系数 {node.thermal_derating}
        <br />
        SNR {node.snr_db}dB · BER {node.ber?.toExponential(2)} · 有效灵敏度 {node.effective_rx_sens}dBm
        <br />
        跳数 {route?.hop_count ?? '-'} · 邻居 {node.neighbors} · 队列 {node.queue_pct.toFixed(0)}% · SEU {node.seu_flips}次
        <br />
        电台(PAMAS): {node.radio === 'SLEEP' ? '😴 SLEEP 休眠省电(15% 电流)' : node.radio === 'TXRX' ? '📡 TXRX 收发中' : '💤 IDLE 空闲监听'}
        {node.blocked_nbrs?.length > 0 && (
          <div style={{ marginTop: 6, padding: '5px 8px', background: 'rgba(90,20,20,0.35)',
                       border: '1px solid #7a2030', borderRadius: 5 }}>
            <div style={{ color: '#ff9a9a' }}>🚫 视线被遮挡的邻居(图中无边,必须中继):</div>
            {node.blocked_nbrs.map((b) => (
              <div key={b.id} style={{ color: '#e8b8b8' }}>
                ‣ {b.id.split('-')[1]}号 (距离 {Math.round(b.d * 10)}m, 岩壁/巨石遮挡)
                {b.via && ` → 数据经 ${b.via.split('-')[1]}号 中继绕行`}
              </div>
            ))}
          </div>
        )}
        {route?.path?.length > 0 && <div style={{ color: '#35c8ff' }}>路径: {route.path.join(' → ')}</div>}
        <div style={{ color: '#c9893a', fontSize: 11, marginTop: 4 }}>
          ⚠ 拖动滑块做压力测试:温度 ≥100°C 或电量 ≤3% 时,该节点将当场报废冒烟。
        </div>
      </div>

      {/* 频段切换 */}
      <div style={{ marginBottom: 10 }}>
        频段/调制模式:
        {['UWB', 'LoRa'].map((b) => (
          <button key={b} onClick={() => push('band', b)}
            style={{
              margin: '0 4px', padding: '3px 10px', cursor: 'pointer',
              background: (pending.band ?? node.band) === b ? '#0e5c8a' : '#12203a',
              color: '#cfe9ff', border: '1px solid #1d3a5f', borderRadius: 4,
            }}>{b}</button>
        ))}
      </div>

      {PARAM_GROUPS.map((g) => (
        <div key={g.title} style={{ borderTop: '1px solid #14263e', paddingTop: 8, marginTop: 6 }}>
          <div style={{ color: '#4d8fc4', marginBottom: 6 }}>{g.title}</div>
          {g.items.map((it) => (
            <div key={it.key} style={{ marginBottom: 7 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>{it.label}</span>
                <span style={{ color: '#35c8ff' }}>{Math.round(val(it.key) * 10) / 10} {it.unit}</span>
              </div>
              <input type="range" min={it.min} max={it.max} step={it.step}
                value={val(it.key)}
                onChange={(e) => push(it.key, parseFloat(e.target.value))}
                style={{ width: '100%', accentColor: '#35c8ff' }} />
            </div>
          ))}
        </div>
      ))}

      <div style={{ marginTop: 10 }}>
        {onReplayWave && (
          <button onClick={onReplayWave}
            style={{ width: '100%', marginBottom: 6, padding: 6, cursor: 'pointer', background: '#0e2a4a', color: '#9ad4ff', border: '1px solid #1d5a8a', borderRadius: 4 }}>
            ▶ 重放 Dijkstra 波前扩散 (算法过程)
          </button>
        )}
        <button onClick={() => push('state', 'DEAD')}
          style={{ width: '100%', padding: 6, cursor: 'pointer', background: '#5c1420', color: '#ff9a9a', border: '1px solid #7a2030', borderRadius: 4 }}>
          ☠ 模拟节点完全失效 (击杀)
        </button>
        <button onClick={() => push('state', 'ACTIVE')}
          style={{ width: '100%', marginTop: 6, padding: 6, cursor: 'pointer', background: '#0e4a2a', color: '#9affc0', border: '1px solid #1a6a40', borderRadius: 4 }}>
          ♻ 恢复节点上线
        </button>
      </div>
    </div>
  )
}
