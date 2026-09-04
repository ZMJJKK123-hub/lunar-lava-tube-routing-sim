import { useEffect, useRef, useState } from 'react'
import { SimClient } from './ws'
import { Radar2D } from './radar/Radar2D'
// [3D 方案已废弃] 旧 Three.js 场景保留于 src/scene/LavaTubeScene.js, 不再挂载
// import { LavaTubeScene } from './scene/LavaTubeScene'
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
  const [chainOpen, setChainOpen] = useState(true)
  const [chainFlow, setChainFlow] = useState(true)

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
      <div ref={mountRef} style={{ position: 'absolute', inset: 0, background: '#0A0F1A' }} />
      <GlobalHUD
        stats={snapshot?.stats}
        mode={snapshot?.mode}
        connected={connected}
        onDisaster={disaster}
        wallMode={wallMode}
        onToggleWall={() => { const v = !wallMode; setWallMode(v); radarRef.current?.setWallMode(v) }}
        onHelp={() => setHelpOpen(true)}
        logOpen={logOpen}
        onToggleLog={() => setLogOpen(!logOpen)}
        chainOpen={chainOpen}
        onToggleChain={() => setChainOpen(!chainOpen)}
        chainFlow={chainFlow}
        onToggleChainFlow={() => { const v = !chainFlow; setChainFlow(v); radarRef.current?.setLayer('chain', v) }}
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
