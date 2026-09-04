// ⛓ 账本侧边栏 (上帝模式): 直观查看全网区块链同步过程
// 顶部横幅: 全网同步状态; 列表: 每节点链高+状态点(默认收起), 点击展开
// 可看到该节点存储的全网世界状态 (所有节点的参数与信息)
import { useState } from 'react'

const F = (v, d = 0) => (typeof v === 'number' ? v.toFixed(d) : (v ?? '-'))
const nid = (s) => (s ? s.replace('NODE-', 'N-') : '-')

function StateDot({ r, hMax, baseHash }) {
  let col
  if (r.h === hMax && r.sh !== baseHash) col = '#ff6050'    // 同高度但状态分叉 (真异常)
  else if (r.h >= hMax - 1 && r.sh === baseHash) col = '#35ff9e' // 已同步 (含容差1块)
  else col = '#ffd76e'                                      // 传播中 / 落后追赶
  return <span style={{ color: col, fontSize: 10 }}>●</span>
}

export default function ChainPanel({ chain, onClose }) {
  const [open, setOpen] = useState(null)
  if (!chain || !chain.per) return null
  const { h_max: hMax, n, na, aligned, lag1, agree, base_hash: baseHash,
          tip, per, world, diffs, stats } = chain
  // 链前缀一致 (后端判定): 全部活跃节点的链尾都在基准链最近几块内。
  // 正常传播波不闪; 真分叉/掉队 -> agree < na -> 显示"同步中"。
  const synced = agree >= na

  const expand = (r) => {
    const mine = r.sh === baseHash ? world : (diffs[r.id] ?? null)
    const rows = mine ? Object.entries(mine).sort((a, b) => a[0].localeCompare(b[0])) : []
    return (
      <div style={{ borderTop: '1px solid #16324f', margin: '4px 0 2px', padding: '5px 0 3px' }}>
        <div style={{ color: '#7fd8ff', fontSize: 11, marginBottom: 3 }}>
          链高 {r.h} · mempool {r.mp} 笔 · 状态哈希 {r.sh}
          {r.sh !== baseHash && (r.h >= hMax - 1
            ? <span style={{ color: '#7fd8ff' }}> (最新块传播中)</span>
            : <span style={{ color: '#ff8a8a' }}> (与基准分叉)</span>)}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1px 6px' }}>
          {rows.map(([rid, st]) => (
            <div key={rid} style={{ color: '#9fc4e0', fontSize: 10, lineHeight: '14px',
                                    whiteSpace: 'nowrap', overflow: 'hidden' }}>
              <b style={{ color: '#5d7ea3' }}>{nid(rid)}</b>{' '}
              #{st.seq} {F(st.soc)}% {F(st.temp)}°{st.state === 'ACTIVE' ? '' : ' ⚠'}
            </div>
          ))}
        </div>
        {!mine && (
          <div style={{ color: '#ffb060', fontSize: 11, padding: '4px 0' }}>
            追赶中: 落后最高链 {hMax - r.h} 块, 整链对齐后可查看
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{
      position: 'absolute', top: 56, right: 10, bottom: 10, width: 336, zIndex: 30,
      background: 'linear-gradient(170deg, rgba(6,14,26,0.96), rgba(4,10,20,0.97))',
      border: '1px solid #1d3a5f', borderRadius: 6,
      display: 'flex', flexDirection: 'column',
      fontFamily: "Consolas,'Courier New',monospace",
      boxShadow: '0 6px 28px rgba(0,20,40,0.5)',
    }}>
      {/* ---- 顶部: 同步状态横幅 ---- */}
      <div style={{ padding: '8px 10px', borderBottom: '1px solid #1d3a5f' }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <b style={{ color: synced ? '#35ff9e' : '#ffd76e', fontSize: 13 }}>
            {synced ? '✓ 信息已经同步' : '⟳ 同步中…'}
          </b>
          <span title="关闭账本侧边栏 (可从顶部 ⛓ 账本 按钮重新打开)"
                onClick={onClose}
                style={{ marginLeft: 'auto', cursor: 'pointer', color: '#5d7ea3' }}>✕</span>
        </div>
        <div style={{ color: '#7fa8c8', fontSize: 11, marginTop: 2 }}>
          高度 {hMax} · 活跃 {na}/{n} · 已对齐 {lag1}/{na} · 前缀一致 {agree}/{na}
        </div>
        {tip && (
          <div style={{ color: '#4d7298', fontSize: 10, lineHeight: '15px' }}>
            最新块 #{tip.index} · 出块 {nid(tip.creator)} · {tip.txs} 笔 · tick {tip.tick}
          </div>
        )}
        <div style={{ color: '#4d7298', fontSize: 10 }}>
          累计 {stats.blocks} 块 · 追块 {stats.catchups} 次 · 分叉愈合 {stats.fork_heals} 次
        </div>
      </div>
      {/* ---- 节点列表 (默认收起, 点击展开) ---- */}
      <div id="guide-chain" style={{ overflowY: 'auto', flex: 1, padding: '4px 10px' }}>
        {per.map((r) => (
          <div key={r.id}
               onClick={() => setOpen(open === r.id ? null : r.id)}
               style={{ cursor: 'pointer', padding: '3px 2px', fontSize: 12,
                        borderBottom: '1px solid rgba(20,40,64,0.5)',
                        color: '#cfe9ff' }}>
            <StateDot r={r} hMax={hMax} baseHash={baseHash} />{' '}
            <b>{nid(r.id)}</b>
            <span style={{ color: '#5d7ea3', float: 'right' }}>
              {hMax - r.h === 0 ? '已对齐' : `落后 ${hMax - r.h} 块`}{r.mp ? ` · 池${r.mp}` : ''}
            </span>
            {open === r.id && expand(r)}
          </div>
        ))}
      </div>
    </div>
  )
}
