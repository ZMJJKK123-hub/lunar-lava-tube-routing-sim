// 全局 HUD: 网络统计 + 网络模式 + 暂停控制 + 灾害注入 + 放墙模式 + 信息面板
// (顶栏收纳 v2: 右上仅 暂停/放墙/灾害▾/信息▾ 四键 —— 观察面板与说明书收进信息下拉,
//  重置收进灾害下拉; 全部功能保留, 仅收纳层级变化)
import { useEffect, useRef, useState } from 'react'   // React 钩子: 下拉开关态/容器引用/外点关闭

// 顶栏下拉菜单: 触发钮 + 绝对定位菜单 + 外点自动收起
// (id 透传到触发钮 —— 新手引导的聚光锚点; tone: danger=灾害红系 / info=情报蓝系;
//  badge: 触发钮右侧指示灯 (如干扰源运行中的红点), 关菜单也可见)
function TopDropdown({ id, label, title, tone, open, setOpen, badge, children }) {
  const ref = useRef(null)   // 按钮+菜单容器 (外点判定范围)
  useEffect(() => {
    if (!open) return
    const away = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [open, setOpen])
  const active = tone === 'danger'
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button id={id} onClick={() => setOpen(!open)} title={title}
        style={{
          padding: '5px 10px', cursor: 'pointer', fontSize: 12,
          background: active ? '#20101a' : (open ? '#0e2a4a' : '#12203a'),
          color: active ? '#ffb8c8' : '#9ad4ff',
          border: active ? '1px solid #5c2030' : '1px solid #1d5a8a', borderRadius: 4,
        }}>{label} {open ? '▴' : '▾'}{badge ? <span style={{ color: '#ff4090' }}>{badge}</span> : null}</button>
      {open && (
        <div style={{
          position: 'absolute', top: '110%', right: 0, zIndex: 40,
          background: 'rgba(8,16,30,0.97)', border: '1px solid #1f4a6f',
          borderRadius: 4, padding: 4, minWidth: 150,
          boxShadow: '0 0 16px rgba(0,80,120,0.4)',
        }}>{children}</div>
      )}
    </div>
  )
}

// 下拉菜单行: 悬停高亮 (hoverBg 区分红/蓝系); active 视图开关亮色 + ● 前缀
function MenuRow({ active, tip, hoverBg, onClick, children }) {
  return (
    <div title={tip} onClick={onClick}
      onMouseEnter={(e) => (e.target.style.background = hoverBg)}
      onMouseLeave={(e) => (e.target.style.background = 'none')}
      style={{
        padding: '7px 12px', cursor: 'pointer', fontSize: 12,
        color: active ? '#9ad4ff' : '#9fb8d0', whiteSpace: 'nowrap', borderRadius: 3,
      }}>{children}</div>
  )
}

export default function GlobalHUD({ stats, mode, connected, paused, onTogglePause, onDisaster, jammerOn, wallMode, onToggleWall, onHelp, logOpen, onToggleLog, chainOpen, onToggleChain, chainFlow, onToggleChainFlow, resetArmed, onArmReset }) {
  const [disasterOpen, setDisasterOpen] = useState(false)   // 灾害下拉展开态
  const [infoOpen, setInfoOpen] = useState(false)           // 信息下拉展开态
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
  // 信息下拉的三个观察面板开关 (点击保持菜单敞开, 便于连开多个)
  const panels = [
    ['📜 日志时间线', logOpen, onToggleLog, '算法过程时间线: 链路熔断/重路由/自愈收敛 等事件的实时日志 (可随时开关)'],
    ['⛓ 链流量可视化', chainFlow, onToggleChainFlow, '链上报文流量: 金点=遥测交易, 青白大点=新块广播, 紫点=追块请求/响应 (画墙拆墙时可见同步风暴)'],
    ['📒 区块链账本', chainOpen, onToggleChain, '账本侧边栏: 每个节点存储的全网状态、链高度与同步进度 (画墙分区可见分叉, 拆墙后自动愈合)'],
  ]
  const modeColor = { STABLE: '#4dffa0', HEALING: '#ffc14d', CONVERGED: '#6ec1ff' }[mode] ?? '#888'
  const R = 'rgba(90,20,40,0.35)'    // 红系悬停底色 (灾害/重置)
  const B = 'rgba(30,70,120,0.35)'   // 蓝系悬停底色 (信息面板)
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
        <TopDropdown label="💥 灾害" tone="danger" open={disasterOpen} setOpen={setDisasterOpen}
          badge={jammerOn ? ' ●' : null}
          title="灾害注入实验: 摧毁主干道节点 / 塌方 / 热浪 / 耀斑; 干扰源为开关式 (再点召回); 底部可重置世界 (点开选择)">
          <MenuRow tip="开关式灾害: 强干扰源全管游走, 靠近区域噪声飙升、链路成片熔断, 走远自动恢复; 再点一次召回"
            hoverBg={R} active={jammerOn}
            onClick={() => onDisaster('jammer')}>   {/* 开关行: 不收菜单, 状态可见 */}
            <span style={{ color: jammerOn ? '#ff80c0' : undefined, fontWeight: jammerOn ? 'bold' : 'normal' }}>
              {jammerOn ? '● 📵 干扰源运行中 (点击召回)' : '📵 移动干扰源 (点击开机)'}</span>
          </MenuRow>
          {disasters.map(([k, label, tip]) => (
            <MenuRow key={k} tip={tip} hoverBg={R}
              onClick={() => { setDisasterOpen(false); onDisaster(k) }}>{label}</MenuRow>
          ))}
          <div style={{ borderTop: '1px solid #1f3a5f', margin: '4px 2px' }} />
          {/* 重置两段式确认: 首点布防(菜单保持敞开显示确认态), 3s 内再点执行 */}
          <MenuRow tip="以初始种子重建整个世界: 节点电量/位置/区块链/机器人/巨石/墙体全部复原 (点两次确认)"
            hoverBg={R} active={resetArmed}
            onClick={() => onArmReset()}>
            <span style={{ color: resetArmed ? '#ff8080' : '#ffb8c8', fontWeight: resetArmed ? 'bold' : 'normal' }}>
              {resetArmed ? '!! 再点确认重置' : '↺ 重置世界'}</span>
          </MenuRow>
        </TopDropdown>
        {/* id=guide-log: 新手引导第 6 步的聚光锚点 (日志开关已收进此菜单) */}
        <TopDropdown id="guide-log" label="ℹ 信息" tone="info" open={infoOpen} setOpen={setInfoOpen}
          title="观察面板开关: 日志时间线 / 链流量 / 区块链账本; 底部是完整说明书">
          {panels.map(([label, on, fn, tip]) => (
            <MenuRow key={label} tip={tip} hoverBg={B} active={on} onClick={() => fn()}>
              {on ? '● ' : ''}{label}
            </MenuRow>
          ))}
          <div style={{ borderTop: '1px solid #1f3a5f', margin: '4px 2px' }} />
          <MenuRow tip="灾害按钮/巨石/堵路机制 的完整说明" hoverBg={B}
            onClick={() => { setInfoOpen(false); onHelp() }}>❓ 说明文档</MenuRow>
        </TopDropdown>
      </div>
    </div>
  )
}
