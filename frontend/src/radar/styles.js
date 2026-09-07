// 渲染样式表与节拍常量 (跨绘制模块共享的视觉语言)
//
// - DATA_HOP_S / BUS_HOP_S: 报文每跳视觉耗时 (后端真实 0.25s,
//   放慢 2.5 倍便于观看);
// - KIND_STYLE: 链上泛洪报文的视觉语言 (可选覆盖, 与 DATA 方块严格区分,
//   稳态 SYNC 流量每 tick 上百跳, 紫系一律小而暗 —— BLOCK 才是主角);
// - autoKindStyle: 零注册兜底, 未知 kind 按名称哈希取色。

export const DATA_HOP_S = 0.625    // DATA 方块每跳视觉耗时 (秒)
export const BUS_HOP_S = 0.625     // 链上泛洪点每跳视觉耗时 (秒)
export const BUS_HOP_MS = BUS_HOP_S * 1000   // 上式对应毫秒数 (总线点计时全部用毫秒!)

export const KIND_STYLE = {
  BLOCK:     { color: '#A5F4FF', size: 5.5, glow: 18 },              // 出块波: 亮青白大光点
  SYNC_RESP: { color: '#B08CFF', size: 2.6, glow: 7, stream: 3 },    // 批量追块: 暗紫串点
  SYNC_REQ:  { color: '#8E7CFF', size: 2.0, glow: 5 },               // 追块请求: 暗紫微点
  TX:        { color: '#E8C06E', size: 2.0, glow: 5 },               // 遥测交易: 暗金微点
  SOS:       { color: '#FF8A5C', size: 3.2, glow: 12 },             // 呼救信标: 橙红点
}

// 零注册兜底: 未知 kind 按名称哈希取色 —— 后端新报文类型自动上屏
export function autoKindStyle(kind) {
  let h = 0
  for (let i = 0; i < kind.length; i++) h = (h * 31 + kind.charCodeAt(i)) >>> 0
  return { color: `hsl(${h % 360} 85% 72%)`, size: 3.2, glow: 9 }
}
