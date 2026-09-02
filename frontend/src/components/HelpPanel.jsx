// 帮助面板: 解释灾害按钮 / 巨石 / 堵路机制 / BOT / 操作
export default function HelpPanel({ onClose }) {
  const S = { color: '#c8dcf0', lineHeight: 1.8 }
  const H = { color: '#00CEC9', fontWeight: 'bold', marginTop: 10 }
  const K = { color: '#ffd76e' }
  return (
    <div style={{
      position: 'absolute', right: 12, top: 60, width: 400, maxHeight: 'calc(100% - 90px)',
      overflowY: 'auto', zIndex: 45, fontSize: 12.5,
      background: 'rgba(5,11,22,0.96)', border: '1px solid #1f4a6f', borderRadius: 8,
      padding: '14px 18px', backdropFilter: 'blur(6px)',
      boxShadow: '0 0 30px rgba(0,80,140,0.35)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <b style={{ color: '#9fd4ff', letterSpacing: 2 }}>❓ 沙盘说明书</b>
        <span style={{ cursor: 'pointer', color: '#7aa' }} onClick={onClose}>✕</span>
      </div>

      <div style={H}>☄ 灾害模拟按钮(右上角)是干什么的?</div>
      <div style={S}>
        它们用来给网络<b>制造事故</b>,展示算法的自愈能力:
        <br />• <span style={K}>摧毁主干道节点</span>:炸毁当前承载流量最大的中继节点,观察数据流绕路;
        <br />• <span style={K}>塌方</span>:一块巨石砸下来,切断最繁忙的主干信道;
        <br />• <span style={K}>热浪</span>:全网温度飙升 → 热噪声增大 → 信噪比跌破门限 → 链路熔断;
        <br />• <span style={K}>耀斑</span>:宇宙射线暴增 → 节点内存单粒子翻转(SEU),短暂失联。
      </div>

      <div style={H}>🪨 巨石是干什么的?为什么有的线连不上?</div>
      <div style={S}>
        灰色实心多边形就是<b>巨石障碍物</b>(棕色的是塌方落石)。本沙盘的核心规则是
        <b>视距通信(LOS)</b>:两个节点之间如果视线被巨石或岩壁挡住,它们之间就<b>没有边、无法直连</b>,
        数据必须经其他节点中继多跳绕行。
        <br />判断方法:把鼠标<b>悬停</b>在一个节点上——亮青色的线是它能直连的邻居;
        <span style={{ color: '#ff8a8a' }}>红色虚线+X</span>表示"这个邻居距离很近,但视线被挡住了,连不上"。
        <br />你还可以<b>直接拖动巨石</b>:把巨石拖到某条线上,松手瞬间该链路就会被切断,算法立刻重新绕路。
      </div>

      <div style={H}>🤖 BOT 是什么?</div>
      <div style={S}>
        BOT 是<b>巡检机器人</b>(动态移动信源):它在管道网络中游走,每到一处就实时向洞口基站回传数据。
        暗金色细线是它当前的最优回传路径,由 RCSPA 资源约束最短路径算法每跳重算(会避开繁忙信道、选择总发射功率最低的绕行路线)。
      </div>

      <div style={H}>🖱 操作</div>
      <div style={S}>
        • <span style={K}>左键拖动</span> = 平移画面;<span style={K}>滚轮</span> = 缩放
        <br />• <span style={K}>左键点节点</span> = 弹出数据面板;<span style={K}>悬停</span> = 高亮直连邻居+被挡视线
        <br />• <span style={K}>拖巨石</span> = 移动障碍物切断链路
        <br />• <span style={K}>🧱 放墙模式</span>按钮 = 开启后左键拖拽画墙(自定义遮挡),再点一次退出
        <br />• <span style={K}>右键节点</span> = 手动破坏 / 过热测试 / 恢复
      </div>
    </div>
  )
}
