// 全局 HUD: 网络统计 + 网络模式 + 灾害注入 + 放墙模式 + 帮助
export default function GlobalHUD({ stats, mode, connected, onDisaster, wallMode, onToggleWall, onHelp, logOpen, onToggleLog }) {
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
        <button id="guide-log" onClick={onToggleLog}
          title="算法过程时间线: 链路熔断/重路由/自愈收敛 等事件的实时日志 (可随时开关)"
          style={{
            padding: '5px 10px', cursor: 'pointer', fontSize: 12,
            background: logOpen ? '#0e4a2a' : '#12203a',
            color: logOpen ? '#9affc0' : '#9fb8d0',
            border: logOpen ? '1px solid #1a6a40' : '1px solid #1d3a5f', borderRadius: 4,
          }}>📜 日志{logOpen ? ' ●' : ''}</button>
        <button id="guide-wall" onClick={onToggleWall}
          title="开启后: 左键拖拽画墙(切断视线→链路消失); 二次点击已放置的墙可删除它(悬停变红叉); Ctrl+Z=撤销最后一堵; 再点按钮退出"
          style={{
            padding: '5px 10px', cursor: 'pointer', fontSize: 12,
            background: wallMode ? '#5c4a14' : '#12203a',
            color: wallMode ? '#ffd76e' : '#9fb8d0',
            border: wallMode ? '1px solid #8a6a1e' : '1px solid #1d3a5f', borderRadius: 4,
          }}>🧱 放墙模式{wallMode ? ' ●' : ''}</button>
        {disasters.map(([k, label, tip]) => (
          <button key={k} onClick={() => onDisaster(k)} title={tip}
            style={{
              padding: '5px 10px', cursor: 'pointer', fontSize: 12,
              background: '#20101a', color: '#ffb8c8', border: '1px solid #5c2030', borderRadius: 4,
            }}>{label}</button>
        ))}
        <button id="guide-help" onClick={onHelp} title="灾害按钮/巨石/堵路机制 说明"
          style={{
            padding: '5px 10px', cursor: 'pointer', fontSize: 12,
            background: '#0e2a4a', color: '#9ad4ff', border: '1px solid #1d5a8a', borderRadius: 4,
          }}>❓ 说明</button>
      </div>
    </div>
  )
}
