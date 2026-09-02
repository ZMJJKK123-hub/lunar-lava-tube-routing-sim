// 算法动态解说面板: 把引擎事件翻译成通俗中文, 打字机效果播出
import { useEffect, useRef, useState } from 'react'

export default function Narration({ narration }) {
  // narration: { id, text } | null —— id 变化时重播
  const [typed, setTyped] = useState('')
  const timer = useRef(null)
  const curId = useRef(null)

  useEffect(() => {
    if (!narration || narration.id === curId.current) return
    curId.current = narration.id
    clearInterval(timer.current)
    let i = 0
    setTyped('')
    timer.current = setInterval(() => {
      i += 1
      setTyped(narration.text.slice(0, i))
      if (i >= narration.text.length) clearInterval(timer.current)
    }, 26)
    return () => clearInterval(timer.current)
  }, [narration])

  if (!narration) return null
  const text = narration.text || ''
  const tone = text.startsWith('✅')
    ? { icon: '✅', color: '#4dffa0', border: '#1a6a40' }
    : text.startsWith('⚠') || text.startsWith('☠') || text.startsWith('☄')
    ? { icon: '🚨', color: '#ff7a7a', border: '#7a2030' }
    : { icon: '🛰️', color: '#6ec1ff', border: '#1d5a8a' }
  const done = typed.length >= text.length

  return (
    <div style={{
      position: 'absolute', left: 12, bottom: 292, width: 460, zIndex: 6,
      background: 'rgba(5,10,20,0.92)', border: `1px solid ${tone.border}`, borderRadius: 8,
      padding: '10px 14px', backdropFilter: 'blur(6px)', fontSize: 13,
      boxShadow: `0 0 16px rgba(0,0,0,0.5)`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
        <span style={{ fontSize: 15 }}>{tone.icon}</span>
        <b style={{ color: tone.color, letterSpacing: 2, fontSize: 12 }}>算法解说员 · LIVE</b>
        {!done && <span style={{ color: '#4d8fc4', fontSize: 10, marginLeft: 'auto' }}>▍正在解说</span>}
      </div>
      <div style={{ color: '#d5e8f8', lineHeight: 1.7, minHeight: 44 }}>
        {typed}
        {!done && <span style={{ color: tone.color }}>▍</span>}
      </div>
    </div>
  )
}
