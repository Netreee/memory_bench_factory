export const DEMO_META = {
  runId: 'game_showcase__20260906-053636',
  sourceRun: 'game_showcase__20260906-053636',
  review: 'PASS · SEALED',
  mode: 'RECORDED RUN',
  questions: 18,
} as const;

export const SCENES = [
  {
    nav: '世界白皮书',
    eyebrow: 'WORLD SPECIFICATION · STORY-FIRST CONSTRUCTION',
    title: '一个悖论，先被锻造成一份可执行的世界白皮书',
    status: '七步规格正在锁定主角、因果链与证据规则',
    metrics: [
      ['WHITEPAPER STEPS', '07', 'SEALED'],
      ['WORLD NODES', '31', 'DEFINED'],
      ['KEY EVENTS', '07', 'CAUSAL'],
    ],
  },
  {
    nav: '世界构建',
    eyebrow: 'WORLD GRAPH · SIX-CHAPTER CAUSAL ARC',
    title: '人物、物件与制度，在六章剧情里彼此推动',
    status: '世界状态正沿六章因果链演化',
    metrics: [
      ['WORLD NODES', '31', 'TOTAL'],
      ['STORY CHAPTERS', '06', 'COMPLETE'],
      ['KEY EVENTS', '07', 'CONNECTED'],
    ],
  },
  {
    nav: '问题铸造',
    eyebrow: 'CAPABILITY DISPATCH · QUESTION FORGE',
    title: '六条能力通道，让问题一题一题从这个世界里蹦出来',
    status: '六条有效能力线路正在生成可诊断问题',
    metrics: [
      ['ACTIVE LINES', '06', 'MAPPED'],
      ['BENCH QUESTIONS', '18', 'FORGED'],
      ['STAR QUESTIONS', '06', 'SPOTLIGHT'],
    ],
  },
  {
    nav: '证据校验',
    eyebrow: 'GROUNDING GATE V0 · EVIDENCE AUDIT',
    title: '每道题都必须穿过问题、真值与证据的闭环闸门',
    status: '18 道题已完成良定义与证据接地双重验收',
    metrics: [
      ['CANDIDATES', '18', 'INPUT'],
      ['GROUNDED', '18', 'SURVIVED'],
      ['REJECTED', '00', 'FINAL'],
    ],
  },
  {
    nav: '成果封存',
    eyebrow: 'BENCHMARK SEALED · BUILD THE WORLD',
    title: '一个完整世界，最终凝结成一套可追问、可验证的 Benchmark',
    status: '游戏世界 Benchmark 已通过全部出厂闸门',
    metrics: [
      ['EVIDENCE DOCS', '30', 'SEALED'],
      ['WELL-POSED', '18/18', 'PASS'],
      ['GROUNDED', '100', 'PERCENT'],
    ],
  },
] as const;

export const COUNCIL_AGENTS = [
  { id: 'SCENE', label: '场景契约', summary: '锁定“任务尚未签发，死亡却已登记”的核心悖论', angle: -90 },
  { id: 'STORY', label: '故事圣经', summary: '冻结艾尔文的唯一主角身份与六章人物弧光', angle: -30 },
  { id: 'GRAPH', label: '世界图谱', summary: '编织 31 个世界节点与 7 个关键事件', angle: 30 },
  { id: 'EVIDENCE', label: '证据系统', summary: '建立原始记录、具名证词、污染公报与传闻层级', angle: 90 },
  { id: 'MAPPER', label: '能力映射', summary: '映射时间、多跳、顺序、冲突、拒答与整合', angle: 150 },
  { id: 'REDTEAM', label: '红队验收', summary: '封存知识边界、物件链与不可逆终局', angle: 210 },
] as const;

export const WORLD_SERIES = [
  {
    name: '艾尔文·霜脊',
    short: '艾尔文',
    color: '#ff8a4c',
    p0: [35, 48, 62, 78, 94, 70],
    oncall: [2, 4, 8, 12, 18, 21],
    sla: [92, 88, 76, 61, 45, 72],
  },
  {
    name: '伊瑟拉·霜爪',
    short: '伊瑟拉',
    color: '#e8f2f5',
    p0: [15, 44, 56, 100, 100, 100],
    oncall: [1, 5, 9, 14, 16, 18],
    sla: [40, 82, 93, 100, 100, 100],
  },
  {
    name: '莉安娜·逐影',
    short: '莉安娜',
    color: '#b8c7ff',
    p0: [22, 38, 76, 70, 58, 42],
    oncall: [1, 3, 8, 11, 15, 17],
    sla: [35, 52, 86, 91, 94, 96],
  },
  {
    name: '冬眠钟',
    short: '冬眠钟',
    color: '#86e5cf',
    p0: [8, 18, 35, 68, 100, 0],
    oncall: [2, 4, 7, 12, 22, 26],
    sla: [28, 43, 59, 81, 100, 100],
  },
] as const;

export const CAPABILITY_CHANNELS = [
  { id: 'L1', name: '时间线', candidates: 3, grounded: 3, status: 'materialized' },
  { id: 'L2', name: '关系多跳', candidates: 5, grounded: 5, status: 'materialized' },
  { id: 'L3', name: '过程排序', candidates: 3, grounded: 3, status: 'materialized' },
  { id: 'L5', name: '冲突裁决', candidates: 3, grounded: 3, status: 'materialized' },
  { id: 'L6', name: '边界拒答', candidates: 2, grounded: 2, status: 'materialized' },
  { id: 'L7', name: '长期整合', candidates: 2, grounded: 2, status: 'materialized' },
] as const;

export const SPOTLIGHT_QUESTIONS = [
  {
    id: 'Q13',
    line: 'L2 · 关系多跳',
    capability: 'RELATIONAL REASONING',
    text: '“提前的死亡”在法律上把守钟权交给了哪个机构？',
    trace: ['死亡登记', '守钟权转交', '银鹿议会'],
  },
  {
    id: 'Q14',
    line: 'L5 · 冲突裁决',
    capability: 'SOURCE CONFLICT',
    text: '一个三天前就被宣布死亡的人，究竟何时、在哪里才真正死去？',
    trace: ['污染死亡簿', '白钟桥现场报告', '2025-02-17'],
  },
  {
    id: 'Q16',
    line: 'L5 · 物证裁决',
    capability: 'EVIDENCE RESOLUTION',
    text: '证物库第 47 号宣称是“原铸”，它实际上是什么？',
    trace: ['议会认定', '黑银纹缺失', '霜狼之牙·赝品'],
  },
  {
    id: 'Q18',
    line: 'L2 · 因果多跳',
    capability: 'CAUSAL MULTI-HOP',
    text: '艾尔文把冬律原册烧进核心后，冬眠钟与外城区迎来了什么结果？',
    trace: ['焚毁原册', '终止冬眠钟', '外城区免于冰封'],
  },
] as const;

export const GATE_CASES = [
  {
    id: 'Q14',
    label: 'GROUNDED',
    state: 'pass',
    question: '伊瑟拉真正死亡的时间与地点是什么？',
    verdict: '污染记录与现场证据完成来源裁决',
    evidence: ['c4_sig_bridge_field_report · 白钟桥现场报告', 'c4_sig_scene_bridge · 剧情实录'],
    checks: [true, true, true],
  },
  {
    id: 'Q16',
    label: 'GROUNDED',
    state: 'pass',
    question: '证物库第 47 号狼牙实际是什么？',
    verdict: '物证特征与核心响应共同指向赝品',
    evidence: ['c2_sig_auction_catalog · 议会证物目录', 'c2_sig_guardian_testimony · 守钟人证词'],
    checks: [true, true, true],
  },
  {
    id: 'Q11',
    label: 'REFUSAL PASS',
    state: 'redacted',
    question: '假面猎手的银纹面具后究竟是什么姓名？',
    verdict: '语料没有提供姓名 · 正确答案必须拒绝猜测',
    evidence: ['KNOWLEDGE BOUNDARY · UNKNOWN', 'SUPPORTED OUTPUT · 无法确定'],
    checks: [true, true, true],
  },
] as const;

export const ARENA_SYSTEMS = [
  { id: 'L1', name: '时间线', questions: 3, stars: 0, score: 100, role: 'TEMPORAL', color: '#8190a0' },
  { id: 'L2', name: '关系多跳', questions: 5, stars: 4, score: 100, role: 'RELATIONAL', color: '#f2f5f7' },
  { id: 'L3', name: '过程排序', questions: 3, stars: 0, score: 100, role: 'ORDER', color: '#9eabc2' },
  { id: 'L5', name: '冲突裁决', questions: 3, stars: 2, score: 100, role: 'CONFLICT', color: '#ff8a4c' },
  { id: 'L6', name: '边界拒答', questions: 2, stars: 0, score: 100, role: 'REFUSAL', color: '#b8c7ff' },
  { id: 'L7', name: '长期整合', questions: 2, stars: 0, score: 100, role: 'CONSOLIDATE', color: '#86e5cf' },
] as const;

export const ARENA_QUESTION = {
  id: 'Q13',
  text: '“提前的死亡”在法律上把守钟权交给了哪个机构？',
  results: { L1: false, L2: true, L3: false, L5: false, L6: false, L7: false },
} as const;

export const CAPABILITY_FINGERPRINT = [
  { key: 'QUESTIONS', values: [3, 5, 3, 3, 2, 2] },
  { key: 'STAR', values: [0, 4, 0, 2, 0, 0] },
  { key: 'GROUNDED', values: [3, 5, 3, 3, 2, 2] },
] as const;
