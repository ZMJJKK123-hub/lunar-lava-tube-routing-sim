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

// 明暗两套画布调色板: 「信息 → 亮色背景」开关消费 (dark 为默认)。
// 只收录在浅底上会失效/伤可读性的颜色; 饱和警示色(红叉/SOS红脉/橙救援线)两态通用;
// outline 非空时给发光点补一圈深色描边 (浅底上纯发光点会发虚)。
export const THEME = {
  dark: {
    bg: '#0A0F1A',
    chamberFill: 'rgba(28,46,74,0.5)',
    chamberStroke: 'rgba(105,145,196,0.3)',
    rockStroke: 'rgba(165,175,195,0.95)',
    boulderStroke: 'rgba(205,140,105,0.95)',
    linkOk: 'rgba(70,150,100,0.15)',
    linkWarm: 'rgba(255,170,70,0.32)',
    linkHot: 'rgba(255,80,70,0.5)',
    nodeFill: '#0A0F1A',
    beaconFill: 'rgba(120,95,40,0.55)',
    nodeStroke: '#39d7c4',
    label: 'rgba(150,190,220,0.66)',
    selRing: 'rgba(255,255,255,0.75)',
    socHi: 'rgba(90,230,140,0.85)',
    queueArc: 'rgba(0,232,255,0.95)',
    pboostRing: 'rgba(255,200,90,0.9)',
    text: 'rgba(220,245,255,0.9)',
    badgeText: '#EAFDFF',
    activeEdge: 'rgba(0,220,215,0.55)',
    markerSrc: 'rgba(0,232,255,0.8)',
    markerDst: 'rgba(255,255,255,0.7)',
    failText: 'rgba(255,150,140,0.9)',
    outline: null,
    robotLabel: 'rgba(240,215,150,0.95)',
    robotFill: '#E8C860',
    robotRange: 'rgba(232,200,110,0.32)',
    sosLabel: 'rgba(255,130,100,0.95)',
    trailOn: 'rgba(80,255,160,0.5)',
    trailOff: 'rgba(255,110,90,0.3)',
    jamRing: 'rgba(255,80,160,0.45)',
    jamLabel: 'rgba(255,160,200,0.95)',
    hoverEdge: 'rgba(0,206,201,0.4)',
    hoverFill: 'rgba(0,206,201,0.06)',
    hoverRing: '#FFFFFF',
    farRGB: '150,162,188',
    farLabel: 'rgba(180,180,200,0.75)',
  },
  light: {
    bg: '#E7ECF3',
    chamberFill: 'rgba(188,203,224,0.55)',
    chamberStroke: 'rgba(70,100,145,0.4)',
    rockStroke: 'rgba(62,72,92,0.95)',
    boulderStroke: 'rgba(150,84,50,0.95)',
    linkOk: 'rgba(52,124,84,0.46)',
    linkWarm: 'rgba(214,132,32,0.6)',
    linkHot: 'rgba(216,58,44,0.68)',
    nodeFill: '#F7FAFD',
    beaconFill: 'rgba(196,164,60,0.5)',
    nodeStroke: '#0d9488',
    label: 'rgba(38,58,88,0.85)',
    selRing: 'rgba(28,38,58,0.8)',
    socHi: 'rgba(26,158,88,0.9)',
    queueArc: 'rgba(0,140,175,0.95)',
    pboostRing: 'rgba(196,138,20,0.95)',
    text: 'rgba(22,42,72,0.92)',
    badgeText: '#08303f',
    activeEdge: 'rgba(0,145,158,0.6)',
    markerSrc: 'rgba(0,140,170,0.85)',
    markerDst: 'rgba(38,52,78,0.8)',
    failText: 'rgba(198,52,40,0.95)',
    outline: 'rgba(28,38,58,0.55)',
    robotLabel: 'rgba(122,86,14,0.95)',
    robotFill: '#C9A22E',
    robotRange: 'rgba(158,128,36,0.45)',
    sosLabel: 'rgba(198,56,26,0.95)',
    trailOn: 'rgba(26,148,84,0.55)',
    trailOff: 'rgba(212,74,52,0.4)',
    jamRing: 'rgba(214,36,124,0.5)',
    jamLabel: 'rgba(188,36,116,0.95)',
    hoverEdge: 'rgba(0,124,136,0.5)',
    hoverFill: 'rgba(0,124,136,0.07)',
    hoverRing: '#1E263A',
    farRGB: '82,98,128',
    farLabel: 'rgba(52,72,102,0.9)',
  },
}
