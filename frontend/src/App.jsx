import { useEffect, useRef, useState } from 'react'
import { SimClient } from './ws'
import { Radar2D } from './radar/Radar2D'
import GlobalHUD from './components/GlobalHUD'
import NodeInspector from './components/NodeInspector'
import EventLog from './components/EventLog'
import ChainPanel from './components/ChainPanel'
import OnboardingGuide from './components/OnboardingGuide'
import HelpPanel from './components/HelpPanel'

export default function App() {
  const mountRef = useRef(null)
  const radarRef = useRef(null)
  const clientRef = useRef(null)
  const [snapshot, setSnapshot] = useState(null)
  const [selected, setSelected] = useState(null)
  const [connected, setConnected] = useState(false)
  const [wallMode, setWallMode] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [logOpen, setLogOpen] = useState(true)
  const [chainOpen, setChainOpen] = useState(false)   // 账本面板默认收起 (常驻会遮挡画布; 从信息菜单开)
  const [chainFlow, setChainFlow] = useState(true)
  const [theme, setTheme] = useState('dark')   // 画布主题: dark 默认 / light 亮色背景 (信息菜单设置)
  const [resetArmed, setResetArmed] = useState(false)
  const resetTimer = useRef(null)

  useEffect(() => {
    const radar = new Radar2D(mountRef.current, {
      client: null,
      onSelect: (id) => { setSelected(id); radar.select(id) },
    })
    radarRef.current = radar
    const client = new SimClient({
      onGeology: (geo) => radar.setGeology(geo),
      onSnapshot: (snap) => {
        setConnected(true)
        setSnapshot(snap)
        radar.update(snap)
      },
    })
    radar.client = client
    window.__radar = radar   // 调试钩子
    clientRef.current = client
    const t = setInterval(() =>
      setConnected(client.ws?.readyState === WebSocket.OPEN), 2000)
    return () => { clearInterval(t); radar.dispose() }
  }, [])

  const setParam = (nodeId, params) => clientRef.current?.setParam(nodeId, params)
  const disaster = (kind) => clientRef.current?.disaster(kind)
  const selectNode = (id) => { setSelected(id); radarRef.current?.select(id) }

  // 调试/演示钩子
  useEffect(() => { window.__simSelect = selectNode })

  return (
    <div style={{ position: 'fixed', inset: 0 }}>
      <div ref={mountRef} style={{ position: 'absolute', inset: 0, background: theme === 'light' ? '#E7ECF3' : '#0A0F1A' }} />
      <GlobalHUD
        stats={snapshot?.stats}
        mode={snapshot?.mode}
        connected={connected}
        paused={!!snapshot?.paused}
        onTogglePause={() => clientRef.current?.send({ cmd: 'toggle_pause' })}
        jammerOn={!!snapshot?.jammer}
        rlOn={!!snapshot?.rl?.enabled}
        onToggleRl={() => clientRef.current?.send({ cmd: 'toggle_rl' })}
        deployRlOn={!!snapshot?.rl_deploy?.enabled}
        onToggleDeployRl={() => clientRef.current?.send({ cmd: 'toggle_rl_deploy' })}
        onDisaster={disaster}
        wallMode={wallMode}
        onToggleWall={() => { const v = !wallMode; setWallMode(v); radarRef.current?.setWallMode(v) }}
        onHelp={() => setHelpOpen(true)}
        logOpen={logOpen}
        onToggleLog={() => setLogOpen(!logOpen)}
        chainOpen={chainOpen}
        onToggleChain={() => setChainOpen(!chainOpen)}
        chainFlow={chainFlow}
        resetArmed={resetArmed}
        onArmReset={() => {
          if (resetArmed) {
            setResetArmed(false); clearTimeout(resetTimer.current)
            clientRef.current?.send({ cmd: 'reset' })
          } else {
            setResetArmed(true)
            clearTimeout(resetTimer.current)
            resetTimer.current = setTimeout(() => setResetArmed(false), 3000)
          }
        }}
        onToggleChainFlow={() => { const v = !chainFlow; setChainFlow(v); radarRef.current?.setLayer('chain', v) }}
        lightBg={theme === 'light'}
        onToggleTheme={() => { const v = theme === 'light' ? 'dark' : 'light'; setTheme(v); radarRef.current?.setTheme(v) }}
      />
      {helpOpen && <HelpPanel onClose={() => setHelpOpen(false)} />}
      {chainOpen && (
        <ChainPanel chain={snapshot?.chain} onClose={() => setChainOpen(false)} />
      )}
      {logOpen && (
        <EventLog events={snapshot?.events} mode={snapshot?.mode} onClose={() => setLogOpen(false)} />
      )}
      {selected && snapshot && (
        <NodeInspector
          node={snapshot.nodes[selected]}
          routes={snapshot.routes}
          links={snapshot.links}
          onSetParam={setParam}
          onClose={() => { setSelected(null); radarRef.current?.select(null) }}
        />
      )}
      {!snapshot && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex',
          alignItems: 'center', justifyContent: 'center', color: '#5d7ea3',
          pointerEvents: 'none',
        }}>正在连接仿真引擎 ws://127.0.0.1:5000 ...</div>
      )}
      <OnboardingGuide />
    </div>
  )
}
