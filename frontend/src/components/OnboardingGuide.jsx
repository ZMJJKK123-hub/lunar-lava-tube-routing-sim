// 分步聚光灯新手引导: 背景压暗 -> 高亮框指向真实 UI 元素 -> 逐步讲解
// 「跳过引导」随时退出; 「下一步」逐个介绍; 指向位置实时跟随(300ms 轮询)
import { useEffect, useRef, useState } from 'react'

// 每步: getRect() 返回屏幕矩形 {x,y,w,h}
const STEPS = [
  {
    key: 'stats',
    getRect: () => document.getElementById('guide-stats')?.getBoundingClientRect(),
    title: '📡 网络总览',
    text: '这里实时显示全网健康度:存活节点数、覆盖率(多少节点能连回基站)、平均信噪比 SNR、电量 SoC 和最大跳数。发生灾害后盯着这里——覆盖率会先跌、再被算法救回来。',
  },
  {
    key: 'canvas',
    getRect: () => {
      const r = window.__radar?.canvas?.getBoundingClientRect()
      if (!r) return null
      return { x: r.x + r.width * 0.3, y: r.y + r.height * 0.25, w: r.width * 0.4, h: r.height * 0.4 }
    },
    title: '🕸 暗网与视距规则',
    text: '画布上若隐若现的暗绿细线,是节点之间"视线可达"的可用链路。规则很简单:两个节点之间的直线如果被巨石挡住,它们之间就没有线,数据必须绕行中继。把鼠标悬停在任意节点上,能直连的邻居会亮起青色并播放信号脉冲。',
  },
  {
    key: 'rock',
    getRect: () => {
      const r = window.__radar
      if (!r?.snapshot?.obstacles?.length) return null
      const cv = r.canvas.getBoundingClientRect()
      const cx = cv.x + cv.width / 2, cy = cv.y + cv.height / 2
      let best = null, bd = 1e9
      for (const o of r.snapshot.obstacles) {
        const xy = r._w2s(o.x, o.z)
        const d = Math.hypot(xy[0] + cv.x - cx, xy[1] + cv.y - cy)
        if (d < bd) { bd = d; best = { x: xy[0], y: xy[1], r: Math.max(26, o.r * r.view.scale) } }
      }
      if (!best) return null
      return { x: best.x + cv.x - best.r, y: best.y + cv.y - best.r, w: best.r * 2, h: best.r * 2 }
    },
    title: '🪨 巨石障碍物',
    text: '灰色实心多边形是巨石——本沙盘唯一的遮挡物。用鼠标按住它拖动:把石头拖到某条线上松手,那条链路会被立刻切断,算法会在几百毫秒内重新规划绕行路径。挡了就是挡了。',
  },
  {
    key: 'wall',
    getRect: () => document.getElementById('guide-wall')?.getBoundingClientRect(),
    title: '🧱 放墙模式',
    text: '默认状态下按住左键拖动是平移画面。点这个按钮进入"放墙模式"后,左键拖拽可以在任意位置画一堵墙(自定义遮挡物),同样会实时切断穿墙的链路;再点一次退出,恢复平移。',
  },
  {
    key: 'disasters',
    getRect: () => document.getElementById('guide-disasters')?.getBoundingClientRect(),
    title: '☄ 灾害模拟',
    text: '一键给网络制造事故:摧毁主干道节点 / 塌方(巨石砸断主干信道) / 热浪(高温熔断链路) / 耀斑(辐射导致内存翻转)。注入后观察算法如何自愈。悬停按钮可看每个灾害的说明。',
  },
  {
    key: 'log',
    getRect: () => document.getElementById('guide-log')?.getBoundingClientRect(),
    title: '📜 日志与帮助',
    text: '这个按钮开关"算法过程时间线"——链路熔断、重路由、自愈收敛的每一步都记录在里面,随时开、随时关。右边的 ❓说明 是完整说明书:灾害、巨石、堵路机制的详细解释。祝演示顺利!',
  },
]

export default function OnboardingGuide() {
  const [step, setStep] = useState(0)
  const [rect, setRect] = useState(null)
  const [done, setDone] = useState(false)
  const timer = useRef(null)

  useEffect(() => {
    const measure = () => {
      const s = STEPS[step]
      if (!s) return
      const r = s.getRect()
      if (r) setRect({ x: r.x, y: r.y, w: r.width ?? r.w ?? 0, h: r.height ?? r.h ?? 0 })
    }
    measure()
    timer.current = setInterval(measure, 300)
    return () => clearInterval(timer.current)
  }, [step])

  if (done || step >= STEPS.length) return null
  const s = STEPS[step]
  const PAD = 8
  const box = rect
    ? { left: rect.x - PAD, top: rect.y - PAD, width: rect.w + PAD * 2, height: rect.h + PAD * 2 }
    : { left: -100, top: -100, width: 0, height: 0 }

  // 提示卡贴着目标元素: 下方优先 (16px 间距), 放不下则放上方; 水平对准目标并夹在屏内
  const tipW = 400
  const tipH = 190
  let tipStyle = { position: 'absolute', width: tipW, zIndex: 60, left: -999, top: -999 }
  if (rect && rect.w > 0) {
    const below = rect.y + rect.h + 16
    const putBelow = below + tipH < innerHeight
    tipStyle = {
      ...tipStyle,
      top: putBelow ? below : Math.max(12, rect.y - tipH - 16),
      left: Math.min(Math.max(12, rect.x + rect.w / 2 - tipW / 2), innerWidth - tipW - 12),
    }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 55 }}>
      {/* 聚光灯遮罩: 高亮框外全部压暗 (box-shadow 大扩散), 框随目标移动 */}
      <div style={{
        position: 'absolute',
        left: box.left, top: box.top, width: box.width, height: box.height,
        borderRadius: 8,
        boxShadow: '0 0 0 9999px rgba(2,4,10,0.82)',
        border: '2px solid #00CEC9',
        transition: 'all 0.35s cubic-bezier(.4,0,.2,1)',
        pointerEvents: 'none',
      }} />
      {/* 步骤提示卡 */}
      <div style={{
        ...tipStyle,
        background: 'linear-gradient(160deg, rgba(8,18,34,0.98), rgba(5,10,20,0.98))',
        border: '1px solid #1f6a8f', borderLeft: '3px solid #00CEC9', borderRadius: 8,
        padding: '12px 16px', transition: 'all 0.35s cubic-bezier(.4,0,.2,1)',
        boxShadow: '0 6px 30px rgba(0,80,140,0.5)',
        fontSize: 12.5, color: '#c8dcf0', lineHeight: 1.75,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <b style={{ color: '#00CEC9', fontSize: 14 }}>{s.title}</b>
          <span style={{ color: '#5d7ea3', fontSize: 11 }}>{step + 1} / {STEPS.length}</span>
        </div>
        <div>{s.text}</div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12 }}>
          <button onClick={() => setDone(true)}
            style={{
              padding: '6px 14px', cursor: 'pointer', fontSize: 12,
              background: 'transparent', color: '#7a95b5',
              border: '1px solid #2a4364', borderRadius: 6,
            }}>跳过引导</button>
          <button onClick={() => setStep(step + 1)}
            style={{
              padding: '6px 18px', cursor: 'pointer', fontSize: 12.5, fontWeight: 'bold',
              background: 'linear-gradient(90deg, #0e4a7a, #1470a8)', color: '#eaf6ff',
              border: '1px solid #4aa0e0', borderRadius: 6,
              boxShadow: '0 0 14px rgba(50,140,220,0.5)',
            }}>{step === STEPS.length - 1 ? '完成 ✓' : '下一步 ▸'}</button>
        </div>
      </div>
    </div>
  )
}
