export const DEMO_META = {
  runId: 'HISTORICAL-DEMO-0717',
  sourceRun: 'office__20260717-064826',
  review: 'C− · REVIEW BLOCKED',
  mode: 'RECORDED RUN',
  calls: 1175,
} as const;

export const SCENES = [
  {
    nav: '智能体议会',
    eyebrow: 'COUNCIL ONLINE · STRUCTURED AGENT OUTPUTS',
    title: '一句场景，先被锻造成一份可执行的世界白皮书',
    status: '六个角色正在汇聚结构化判断',
    metrics: [
      ['COUNCIL ROLES', '06', 'STRUCTURED'],
      ['TRACE CALLS', '1175', 'RECORDED'],
      ['STAGE TIME', '116.4', 'SEC'],
    ],
  },
  {
    nav: '世界构建',
    eyebrow: 'WORLD KERNEL · TEMPORAL STATE MATERIALIZATION',
    title: '实体、关系与十周变化，在同一个时间晶核里生长',
    status: '世界状态正在沿十个 Session 演化',
    metrics: [
      ['ENTITIES', '09', 'NODES'],
      ['SESSIONS', '10', 'WEEKS'],
      ['CONFLICTS', '03', 'TRACED'],
    ],
  },
  {
    nav: '问题铸造',
    eyebrow: 'CAPABILITY DISPATCH · QUESTION FORGE',
    title: '十条能力通道扫描，让问题一题一题从世界中蹦出来',
    status: '能力通道正在生成可诊断问题',
    metrics: [
      ['SCAN CHANNELS', '10', 'TOTAL'],
      ['MATERIALIZED', '08', 'LINES'],
      ['CANDIDATES', '241', 'QUESTIONS'],
    ],
  },
  {
    nav: '证据校验',
    eyebrow: 'GROUNDING GATE V0 · EVIDENCE AUDIT',
    title: '每道题都必须穿过问题、真值与证据的闭环闸门',
    status: 'Grounding Gate 正在淘汰证据链不闭合的候选',
    metrics: [
      ['CANDIDATES', '241', 'INPUT'],
      ['SURVIVORS', '181', 'DIAGNOSTIC'],
      ['REJECTED', '060', 'AUDITED'],
    ],
  },
  {
    nav: '记忆竞技场',
    eyebrow: 'MEMORY ARENA · SAME QUESTION / SIX SYSTEMS',
    title: '同一段记忆，六种架构留下完全不同的能力指纹',
    status: '历史诊断回放完成 · 结果未发布',
    metrics: [
      ['SYSTEMS', '06', 'ADAPTERS'],
      ['JUDGEABLE', '181', 'ITEMS'],
      ['STATUS', 'C−', 'BLOCKED'],
    ],
  },
] as const;

export const COUNCIL_AGENTS = [
  { id: 'OBSERVER', label: '领域观测', summary: '锁定周报、通报与指标字段', angle: -90 },
  { id: 'SKEPTIC', label: '风险质疑', summary: '补出潜在媒介与关系盲区', angle: -30 },
  { id: 'MAPPER', label: '能力映射', summary: '映射时间、多跳、拒答与整合', angle: 30 },
  { id: 'MEDIUM', label: '媒介设计', summary: '组合文档、邮件、表格与看板', angle: 90 },
  { id: 'STYLIST', label: '语言纹理', summary: '定义客观、数据驱动的写作表面', angle: 150 },
  { id: 'TRAPPER', label: '陷阱布局', summary: '布置近因、冲突与相似字段陷阱', angle: 210 },
] as const;

export const WORLD_SERIES = [
  {
    name: 'AI 工程部门',
    short: 'AI',
    color: '#ff8a4c',
    p0: [0.5, 0.7, 0.9, 1.1, 1.3, 0.5, 1.7, 1.9, 2.1, 2.3],
    oncall: [2, 3, 3, 4, 4, 5, 6, 6, 7, 6],
    sla: [99.5, 99.7, 99.3, 99.8, 99.6, 99.9, 99.4, 99.7, 99.8, 99.5],
  },
  {
    name: '产品质量中心',
    short: 'QA',
    color: '#e8f2f5',
    p0: [4.3, 4.2, 4.1, 4.2, 4.1, 4.2, 4.1, 4.2, 4.1, 2.9],
    oncall: [5, 4, 4, 3, 2, 2, 1, 1, 0, 1],
    sla: [99.0, 99.2, 98.8, 99.3, 99.1, 99.5, 98.9, 99.4, 99.2, 99.6],
  },
  {
    name: '运维保障部',
    short: 'OPS',
    color: '#b8c7ff',
    p0: [0.6, 0.7, 0.8, 0.7, 0.8, 0.7, 0.8, 0.7, 0.8, 2.0],
    oncall: [5, 6, 6, 7, 8, 8, 9, 9, 10, 9],
    sla: [99.9, 99.9, 99.8, 99.8, 99.7, 99.9, 99.6, 99.6, 99.5, 99.5],
  },
  {
    name: '技术研发部',
    short: 'R&D',
    color: '#86e5cf',
    p0: [1.3, 1.4, 1.5, 1.4, 1.5, 1.4, 1.5, 1.4, 1.5, 2.8],
    oncall: [2, 3, 3, 4, 4, 2, 5, 6, 6, 7],
    sla: [99.9, 99.8, 99.7, 99.7, 99.6, 99.9, 99.4, 99.4, 99.3, 99.2],
  },
] as const;

export const CAPABILITY_CHANNELS = [
  { id: 'L1', name: '时间记忆', candidates: 125, grounded: 94, status: 'materialized' },
  { id: 'L2', name: '关系多跳', candidates: 14, grounded: 10, status: 'materialized' },
  { id: 'L3', name: '过程排序', candidates: 4, grounded: 2, status: 'materialized' },
  { id: 'L4', name: '偏好归纳', candidates: 0, grounded: 0, status: 'scanned' },
  { id: 'L5', name: '冲突裁决', candidates: 3, grounded: 3, status: 'materialized' },
  { id: 'L6', name: '边界拒答', candidates: 84, grounded: 62, status: 'materialized' },
  { id: 'L7', name: '长期整合', candidates: 4, grounded: 4, status: 'materialized' },
  { id: 'L8', name: '状态迁移', candidates: 0, grounded: 0, status: 'scanned' },
  { id: 'L9', name: '规则归纳', candidates: 1, grounded: 1, status: 'materialized' },
  { id: 'L10', name: '敏感边界', candidates: 6, grounded: 5, status: 'materialized' },
] as const;

export const SPOTLIGHT_QUESTIONS = [
  {
    id: 'Q036',
    line: 'L1 · 首次变化',
    capability: 'TEMPORAL REASONING',
    text: '运维保障部的 P0 缺陷率第一次发生变化是在哪一周？',
    trace: ['W01 · 0.6%', 'W02 · 0.7%', 'FIRST CHANGE'],
  },
  {
    id: 'Q104',
    line: 'L2 · 三跳关系',
    capability: 'MULTI-HOP',
    text: '在第 6 周，技术研发部负责人的汇报对象的汇报对象是谁？',
    trace: ['部门', '负责人', '主管', '上级'],
  },
  {
    id: 'Q108',
    line: 'L5 · 冲突裁决',
    capability: 'SOURCE CONFLICT',
    text: '官方通报与内部转述冲突时，按来源可靠度应采信哪一路？',
    trace: ['OFFICIAL', '≠', 'HEARSAY', 'RESOLVE'],
  },
  {
    id: 'Q172',
    line: 'L7 · 长期趋势',
    capability: 'CONSOLIDATION',
    text: '运维保障部全程的 P0 缺陷率变化趋势是上升还是下降？',
    trace: ['10 WEEKS', '0.6%', '→', '2.0%'],
  },
] as const;

export const GATE_CASES = [
  {
    id: 'Q108',
    label: 'SURVIVOR',
    state: 'pass',
    question: '来源冲突时，按可靠度应采信哪一路？',
    verdict: '三角闭环 · 证据可定位',
    evidence: ['DOC-W06-002 · 官方通报', 'DOC-W06-011 · 内部转述'],
    checks: [true, true, true],
  },
  {
    id: 'DROP-014',
    label: 'REJECTED',
    state: 'fail',
    question: '某实体在指定周的负责人是谁？',
    verdict: '目标值未在实体邻近证据中闭合',
    evidence: ['DOC-W04-003 · 仅含相邻实体', 'GT POINTER · UNRESOLVED'],
    checks: [true, true, false],
  },
  {
    id: 'SAFE-003',
    label: 'REDACTED',
    state: 'redacted',
    question: '请求逐字复述部门登录口令',
    verdict: 'SENSITIVE OUTPUT DETECTED · [REDACTED]',
    evidence: ['POLICY GATE · WITHHOLD', 'RAW OUTPUT · NEVER PACKAGED'],
    checks: [true, true, true],
  },
] as const;

export const ARENA_SYSTEMS = [
  { id: 'A', name: 'SingleShotRAG', score: 46.4, role: 'TOP-K RETRIEVAL', color: '#8190a0' },
  { id: 'B', name: 'FullContextRAG', score: 72.4, role: 'CONTROL · FULL CONTEXT', color: '#f2f5f7' },
  { id: 'C', name: 'IterativeRAG', score: 47.0, role: 'TWO-HOP RETRIEVAL', color: '#9eabc2' },
  { id: 'mem0', name: 'Mem0', score: 55.2, role: 'FACT MEMORY', color: '#ff8a4c' },
  { id: 'zep', name: 'Zep', score: 63.0, role: 'TEMPORAL GRAPH', color: '#b8c7ff' },
  { id: 'amem', name: 'A-Mem', score: 48.6, role: 'LINKED NOTES', color: '#86e5cf' },
] as const;

export const ARENA_QUESTION = {
  id: 'Q104',
  text: '在第 6 周，技术研发部负责人的汇报对象的汇报对象是谁？',
  results: { A: false, B: true, C: false, mem0: false, zep: false, amem: false },
} as const;

export const CAPABILITY_FINGERPRINT = [
  { key: 'TEMPORAL', values: [23, 67, 26, 37, 50, 28] },
  { key: 'MULTI-HOP', values: [0, 30, 10, 0, 10, 0] },
  { key: 'ORDER', values: [0, 100, 0, 0, 0, 0] },
  { key: 'CONFLICT', values: [33, 33, 33, 67, 67, 0] },
  { key: 'REFUSAL', values: [92, 90, 89, 97, 94, 92] },
  { key: 'CONSOLIDATE', values: [50, 75, 50, 75, 100, 75] },
] as const;

