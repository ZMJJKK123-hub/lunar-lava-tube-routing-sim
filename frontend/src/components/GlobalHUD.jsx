// 全局 HUD: 网络统计 + 网络模式 + 暂停控制 + 灾害注入 + 放墙模式 + 帮助
// (顶栏收纳: 四灾害收进下拉菜单, 面板开关图标化 —— 高频操作保留文字)
import { useEffect, useRef, useState } from 'react'   // React 钩子: 灾害下拉开关态/容器引用/外点关闭
export default function GlobalHUD({ stats, mode, connected, paused, onTogglePause, onDisaster, wallMode, onToggleWall, onHelp, logOpen, onToggleLog, chainOpen, onToggleChain, chainFlow, onToggleChainFlow, resetArmed, onArmReset }) {
  const [disasterOpen, setDisasterOpen] = useState(false)   // 灾害下拉展开态
  const disasterRef = useRef(null)                          // 按钮+菜单容器 (外点判定范围)
  // 外点关闭: mousedown 落在容器之外即收起 (与画布右键菜单同惯例)
  useEffect(() => {
    if (!disasterOpen) return
    const away = (e) => { if (!disasterRef.current?.contains(e.target)) setDisasterOpen(false) }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [disasterOpen])
  const box = (label, v, color = '#cfe9ff') => (
    <div style={{ marginRight: 16 }}>
      <span style={{ color: '#5d7ea3' }}>{label} </span>
      <b style={{ color }}>{v}</b>
    </div>
  )
  const disasters = [
    ['kill_backbone', '摧毁主干道节点', '炸毁当前承载流量最大的中继节点, 观察数据流绕行自愈'],
    ['collapse', '塌方', '一块巨石砸落, 切断最繁忙的主干信道(视线遮挡+LOS重算)'],
    ['thermal_surge', '热浪', '全网温度飙升→热噪声增大→SNR跌破门限→链路熔断'],
    ['solar_flare', '耀斑', '宇宙射线暴增→节点内存单粒子翻转(SEU)→短暂失联'],
  ]
  const modeColor = { STABLE: '#4dffa0', HEALING: '#ffc14d', CONVERGED: '#6ec1ff' }[mode] ?? '#888'
  return (
    <div style={{
      position: 'absolute', top: 0, left: 0, right: 0, height: 48,
      background: 'linear-gradient(180deg, rgba(6,12,24,0.95), rgba(6,12,24,0.55))',
      borderBottom: '1px solid #1d3a5f', display: 'flex', alignItems: 'center',
      padding: '0 16px', fontSize: 13, zIndex: 10,
    }}>
      <b style={{ marginRight: 18, letterSpacing: 1 }}>🌍 月球熔岩管 · 多智能体网络沙盘</b>
      <span style={{ color: connected ? '#35ff9e' : '#ff5050', marginRight: 14, fontSize: 11 }}>
        ● {connected ? '引擎已连接' : '连接断开'}
      </span>
      <div id="guide-stats" style={{ display: 'flex', alignItems: 'center' }}>
      {stats && <>
        {box('存活', `${stats.alive}/${stats.total}`)}
        {box('覆盖率', stats.coverage_pct + '%', stats.coverage_pct > 90 ? '#35ff9e' : '#ffb020')}
        {box('平均SNR', stats.avg_snr_db + ' dB', stats.avg_snr_db > 10 ? '#35ff9e' : '#ffb020')}
        {box('平均SoC', stats.avg_soc_pct + '%')}
        {box('最大跳数', stats.max_hop)}
      </>}
      </div>
      <span style={{ marginLeft: 8, color: modeColor, fontWeight: 'bold', fontSize: 12 }}>
        {mode === 'HEALING' ? '◐ 自愈重构中…' : mode === 'CONVERGED' ? '✦ 已收敛' : '● 稳定运行'}
      </span>
      <div id="guide-disasters" style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
        <button id="guide-pause" onClick={onTogglePause}
          title="暂停/恢复仿真: 所有计算与报文飞行冻结在当前帧, 便于观察与讲解; 悬停/缩放/上帝操作不受影响, 再点一次原速继续"
          style={{
            padding: '5px 10px', cursor: 'pointer', fontSize: 12,
            background: paused ? '#5c4214' : '#12203a',
            color: paused ? '#ffd76e' : '#9fb8d0',
            border: paused ? '1px solid #8a6a1e' : '1px solid #1d3a5f', borderRadius: 4,
            fontWeight: paused ? 'bold' : 'normal',
          }}>{paused ? '▶ 继续' : '⏸ 暂停'}</button>
        <button id="guide-wall" onClick={onToggleWall}
          title="开启后: 左键拖拽画墙(切断视线→链路消失); 二次点击已放置的墙可删除它(悬停变红叉); Ctrl+Z=撤销最后一堵; 再点按钮退出"
          style={{
            padding: '5px 10px', cursor: 'pointer', fontSize: 12,
            background: wallMode ? '#5c4a14' : '#12203a',
            color: wallMode ? '#ffd76e' : '#9fb8d0',
            border: wallMode ? '1px solid #8a6a1e' : '1px solid #1d3a5f', borderRadius: 4,
          }}>🧱 放墙{wallMode ? ' ●' : ''}</button>
        {/* 灾害下拉: 四类灾害收进菜单 (长文字不再占顶栏, 也防演示误触) */}
        <div ref={disasterRef} style={{ position: 'relative' }}>
          <button onClick={() => setDisasterOpen(v => !v)}
            title="灾害注入实验: 摧毁主干道节点 / 塌方 / 热浪 / 耀斑 (点开选择)"
            style={{
              padding: '5px 10px', cursor: 'pointer', fontSize: 12,
              background: '#20101a', color: '#ffb8c8',
              border: '1px solid #5c2030', borderRadius: 4,
            }}>💥 灾害 {disasterOpen ? '▴' : '▾'}</button>
          {disasterOpen && (
            <div style={{
              position: 'absolute', top: '110%', right: 0, zIndex: 40,
              background: 'rgba(8,16,30,0.97)', border: '1px solid #5c2030',
              borderRadius: 4, padding: 4, minWidth: 150,
              boxShadow: '0 0 16px rgba(120,20,40,0.4)',
            }}>
              {disasters.map(([k, label, tip]) => (
                <div key={k} title={tip}
                  onClick={() => { setDisasterOpen(false); onDisaster(k) }}
                  onMouseEnter={(e) => (e.target.style.background = 'rgba(90,20,40,0.35)')}
                  onMouseLeave={(e) => (e.target.style.background = 'none')}
                  style={{
                    padding: '7px 12px', cursor: 'pointer', fontSize: 12,
                    color: '#ffb8c8', whiteSpace: 'nowrap', borderRadius: 3,
                  }}>{label}</div>
              ))}
            </div>
          )}
        </div>
        <button id="guide-log" onClick={onToggleLog}
          title="📜 日志 — 算法过程时间线: 链路熔断/重路由/自愈收敛 等事件的实时日志 (可随时开关)"
          style={{
            padding: '5px 9px', cursor: 'pointer', fontSize: 12,
            background: logOpen ? '#0e4a2a' : '#12203a',
            color: logOpen ? '#9affc0' : '#9fb8d0',
            border: logOpen ? '1px solid #1a6a40' : '1px solid #1d3a5f', borderRadius: 4,
          }}>📜{logOpen ? ' ●' : ''}</button>
        <button id="guide-chain-flow" onClick={onToggleChainFlow}
          title="⛓ 链流量 — 链上报文流量可视化: 金点=遥测交易, 青白大点=新块广播, 紫点=追块请求/响应 (画墙拆墙时可见同步风暴)"
          style={{
            padding: '5px 9px', cursor: 'pointer', fontSize: 12,
            background: chainFlow ? '#2a1a4a' : '#12203a',
            color: chainFlow ? '#c9b0ff' : '#9fb8d0',
            border: chainFlow ? '1px solid #5a3a9a' : '1px solid #1d3a5f', borderRadius: 4,
          }}>⛓{chainFlow ? ' ●' : ''}</button>
        <button id="guide-chain-btn" onClick={onToggleChain}
          title="📒 账本 — 区块链账本侧边栏: 每个节点存储的全网状态、链高度与同步进度 (画墙分区可见分叉, 拆墙后自动愈合)"
          style={{
            padding: '5px 9px', cursor: 'pointer', fontSize: 12,
            background: chainOpen ? '#0e2a4a' : '#12203a',
            color: chainOpen ? '#7fd8ff' : '#9fb8d0',
            border: chainOpen ? '1px solid #1d5a8a' : '1px solid #1d3a5f', borderRadius: 4,
          }}>📒{chainOpen ? ' ●' : ''}</button>
        <button onClick={onArmReset}
          title="以初始种子重建整个世界: 节点电量/位置/区块链/机器人/巨石/墙体全部复原 (点两次确认)"
          style={{
            padding: '5px 10px', cursor: 'pointer', fontSize: 12,
            background: resetArmed ? '#5c1420' : '#20101a',
            color: '#ffb8c8', border: '1px solid #7a2030', borderRadius: 4,
          }}>{resetArmed ? '!! 再点确认' : '↺ 重置'}</button>
        <button id="guide-help" onClick={onHelp} title="❓ 说明 — 灾害按钮/巨石/堵路机制 说明"
          style={{
            padding: '5px 9px', cursor: 'pointer', fontSize: 12,
            background: '#0e2a4a', color: '#9ad4ff', border: '1px solid #1d5a8a', borderRadius: 4,
          }}>❓</button>
      </div>
    </div>
  )
}
