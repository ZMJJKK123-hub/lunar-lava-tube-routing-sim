// 算法过程时间线: 实时滚动显示引擎下发的算法事件 (断链/重路由/自愈/收敛)
import { useEffect, useRef } from 'react'

const SEV_STYLE = {
  error: { color: '#ff6a6a', icon: '✖' },
  warn: { color: '#ffc14d', icon: '⟳' },
  ok: { color: '#4dffa0', icon: '✔' },
  info: { color: '#6ec1ff', icon: '›' },
}

const TYPE_LABEL = {
  link_down: '链路熔断', link_up: '链路恢复', reroute: '重路由',
  isolated: '节点失联', rejoin: '节点入网', node_dead: '节点损毁',
  congestion: '拥塞告警', healing_start: '自愈启动', converged: '全网收敛',
  disaster: '灾害注入', override: '上帝模式',
}

export default function EventLog({ events, mode, onClose }) {
  const ref = useRef(null)
  useEffect(() => { ref.current?.scrollTo({ top: 1e6 }) }, [events])

  const modeStyle = {
    STABLE: { color: '#4dffa0', text: '● 网络稳定' },
    HEALING: { color: '#ffc14d', text: '◐ 自愈中 · Dijkstra 波前扩散' },
    CONVERGED: { color: '#6ec1ff', text: '✦ 路由收敛' },
  }[mode] ?? { color: '#888', text: mode }

  return (
    <div id="guide-timeline" style={{
      position: 'absolute', left: 12, bottom: 12, width: 460, maxHeight: 260,
      background: 'rgba(5,10,20,0.88)', border: '1px solid #1d3a5f', borderRadius: 8,
      fontSize: 11.5, backdropFilter: 'blur(6px)', zIndex: 5,
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{
        padding: '7px 12px', borderBottom: '1px solid #14263e',
        display: 'flex', justifyContent: 'space-between', fontWeight: 'bold',
        alignItems: 'center',
      }}>
        <span>⟡ 算法过程时间线 (Multi-Agent Routing)</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ color: modeStyle.color }}>{modeStyle.text}</span>
          <span style={{ cursor: 'pointer', color: '#7aa', padding: '0 4px' }}
                onClick={onClose} title="关闭时间线 (可从顶部 📜 日志 按钮重新打开)">✕</span>
        </span>
      </div>
      <div ref={ref} style={{ overflowY: 'auto', padding: '6px 12px' }}>
        {(events ?? []).slice(-14).reverse().map((ev) => {
          const s = SEV_STYLE[ev.severity] ?? SEV_STYLE.info
          return (
            <div key={ev.id} style={{
              display: 'flex', gap: 8, padding: '2.5px 0',
              opacity: 0.95, borderBottom: '1px dashed rgba(30,60,95,0.35)',
            }}>
              <span style={{ color: '#4a6a8f', minWidth: 46 }}>t{ev.tick}</span>
              <span style={{ color: s.color, minWidth: 66 }}>
                [{TYPE_LABEL[ev.type] ?? ev.type}]
              </span>
              <span style={{ color: '#c8dcf0', flex: 1 }}>{ev.msg}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
