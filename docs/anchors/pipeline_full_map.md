# Pipeline 完整全貌(严格照代码,含每层重试/每个补丁/每句专设 prompt)

> 本文按【代码实际行为】写,不是设计意图。读完应对系统有完整心智模型。
> 文件落点:`pipeline/{factory,central_office,world_gen,world_state,render,closed_loop,grounding,well_posed}.py` + `pipeline/lines/{base,L1,L2,L3,L5}.py` + `pipeline/prompts.py` + `config.py`。

---

## 0. 数据流(8 stage)+ 两条执行路径
```
input → whitepaper(议会) → world → orders → well_posed(边A闸) → questions → corpus → grounding(边B闸)
        [LLM]              [LLM]   [代码]   [代码]              [LLM]      [LLM大头] [代码]
```
产物:`00_input … 06_grounded_questions.json` 逐 stage 落盘(`factory.ART`)。

**两条路径(factory.main)**:
- **普通 drive**(无 `--min-questions`):`drive(STAGES, from, to, only, force)` 顺序跑,幂等跳过已完成(manifest 为准)。
- **闭环旋钮**(给了 `--min-questions`):先 `drive(…, to="whitepaper")`,再 `build_to_target` 自管 world→grounding,带 ①供给环 + ②纠偏环(见 §5)。

---

## 1. 真值机器 world_state.py(命门2 的地基)
**状态机**:每个 `(实体, 字段)` = 一条 `Timeline`(`Op` 列表)。`Op = {session, date, op, value, prev}`,`op ∈ {SET, UPDATE, EXPIRE, DELETE}`。
**两个哨兵**:`INVALID="__INVALIDATED__"`(字段已 EXPIRE/DELETE,该忘)、`INSUFFICIENT="INSUFFICIENT_EVIDENCE"`(从未出现,ABS)。
**Timeline 核心方法**:
- `_fold(stop, key_of)`:从头放电影到 stop:SET/UPDATE→cur=value,EXPIRE/DELETE→cur=INVALID。→ `value_at_session(s)` / `latest_valid()` / `value_at(date)`。
- `set_values()`=所有 SET/UPDATE 取值时间序(MR 用);`change_ops()`=真正值变化的 op(TR 用:删/过期 或 新值≠prev)。

**各 gt(代码机械算,argmax/argmin 都对 = "结构裁判")**:
| gt 函数 | 算什么 | 关键 |
|---|---|---|
| `gt_ie(s)` | 第 s 周当时值 | `value_at_session` |
| `gt_ku` | 最新有效值 | `latest_valid`(被 EXPIRE→INVALID) |
| `gt_mr(agg)` | 跨周极值 | `set_values` 里 argmax/argmin,**返回 {value,session,date}** |
| `gt_tr(to?)` | 首次变化/变成某值的时刻 | `change_ops`,**返回 {session,date,from,to}**(★FixA 后 L1 用 week 不用 session) |
| `gt_forget(qd)` | qd 切片是否已停用 | `value_at(qd)==INVALID` → `{forgotten,value}` |
| `gt_pre_expire` | 停用前最后值 | EXPIRE op 的 `prev` |
| `gt_event_order` | 所有 UPDATE/EXPIRE/DELETE 按 (date,session) 排序 | **初始 SET 不算事件**(L3 用) |
| `gt_multihop(start,path,W)` | 沿软外键逐跳 `value_at_session(W)`,非末跳值=下一跳实体名 | 断链→INSUFFICIENT;返回 {answer,path_evidence,broke_at} |
| `stable_query_weeks` | 本周无 op 但有效值 + 之前变过的【可问周】 | IE/KU 出题靠它(逼跨周回忆,单看本周答不出) |

---

## 2. 八个 stage 逐个详解

### ① input(`stage_input`,0 LLM)
写 `SCENARIOS[scenario]`(office/medical 的 description+few_shot,**无 QA**)。

### ② whitepaper(`stage_whitepaper` → `central_office`,~8-12 LLM)
**议会**:6 视角并行(`council.{observe,skeptic,medium,style,map,traps}`)→ 代码装配草案 → `council.critic` 批判润色 → 白皮书。产物 = `{active_lines[{line,weight}], domain_profile{entity_noun,field_schema[{name,kind}],doc_genres,stopped_phrase}, shared_world_spec{entities.count,timeline.n_sessions,change_density}, traps}`。
- **议会某视角失败可跳过**(如 run020444 medium ⚠失败,综合/批判仍成功 → 白皮书正常)。

### ③ world(`stage_world` → `world_gen.build_world` + `lines.prepare_lines`,~20-50 LLM)
`build_world`:
- 读 `shared_world_spec.entities.count`(=n_entities)、`timeline.n_sessions`。
- **★4 轮实体循环**:每轮把"还差几个"凑批(batch=8)并发发 `world.batch`;累计够 n_entities 即停。
- **★收集期去重**:`seen`(精确名)+ `seen_base`(主干,`_strip_disambig`)——**近重名(张三/张三(数据))整条丢弃**(Fix3)。
- **★W.3 CRITIC 修复轮(最多 3 轮)**:`validate(ws)` 算出 `monotonic`(数值轨迹单调,MR 退化)/`fake_evolving`(声明 evolving 却 1 个值)缺陷 → `world.repair` 定向重生成坏字段(只覆盖被点名字段,不动其它/不改名)。
- **★traps 过滤**:`近重名/重名/同名/近似名` 关键词的 trap 不注入 prompt(Fix3;★审计点名这是补丁,该删信 prompt 层)。
- 末了 `name_collisions(ws)` 兜底检测,有塌缩则 log ⚠。

`prepare_lines`(各线把基质叠进共享世界,命门1):
- **L2.prepare**:把【人名类字段值】提升为人员实体,给软外键链「负责人→人员→汇报对象」;下一跳值=另一真人名(★2 根因修,不再写死 CTO/CEO);人名也按主干去重(Fix3)。
- **L5.prepare**:`inject_conflicts` 注入跨来源矛盾到 `ws.conflicts`(canonical 不动);小道值排除本实体别周真值(源头修);max_n 由 `profile.l5_max_conflicts`(闭环配额)定。
- L1/L3 prepare = no-op(基质免费)。

### ④ orders(`stage_orders` → `lines.run_lines`,0 LLM)
各激活线 `feasible()` 过关 → `enumerate(ws, quota, wp)` 点菜烘焙 gt。quota 来自 `manifest.config.quotas`(闭环喂)或回退 `total_q`。

### ⑤ well_posed(`stage_well_posed` → `well_posed.run_well_posed`,0 LLM)= 边A闸(详 §4)
逐 order 派各线 `well_posed(order, ws)` → 过闸子集**覆写 03_orders**,弃因留 `03_well_posed_report.json`。

### ⑥ questions(`stage_questions` → `render.phrase_questions`,1 LLM/题)
每 order 取 `line.intent(order)=(意图, 须隐藏词)` → `phrase` LLM 润色成自然题面(max_tokens=2048,temp 0.5)。丢并发下偶发空题面。

### ⑦ corpus(`stage_corpus` → `render.render_corpus`,**LLM 大头**)= 整个 pipeline 最慢
- `filler_per_week = max(8, target_tokens/n_sessions/800)`(每篇≈800字)。
- **周并行**(`pmap(_render_week, weeks, workers=n_weeks)`),周内信号组/草堆批再并发(workers=8)——但**真上限是全局 `LLM_CONCURRENCY` 信号量**。
- 逐周 checkpoint 落 `05_corpus.json`(断点续渲)。
- **★多样性诊断**(NDG/IDO/CR)附加测,失败不拖垮 run。
- **★_render_sig 的"4-attempt 重渲循环"**(就是你问的"渲不对重渲4次"):见 §6。

### ⑧ grounding(`stage_grounding` → `grounding.run_grounding`,0 LLM)= 边B闸(详 §4)
逐题派各线 `ground()`,gold 须在证据文档【逐字+就近(±90字)】可验,否则弃。出厂 = `06_grounded_questions.json`,存活率 + 弃因留 `06_grounding_report.json`。

---

## 3. 七条产线(六件套:prepare/feasible/enumerate/gt/intent/ground + 第7件 well_posed)
| 线 | 状态 | 能力 | gt 基质 |
|---|---|---|---|
| **L1_timeline** | 已建 | IE/KU/TR/MR/FORGET/PREEXPIRE/ABS(7能力捆绑) | 状态机切片;`_CAP_WEIGHT` 配比分配 quota,按权重降序截断 |
| **L2_relational** | 已建 | 多跳(负责人→汇报对象) | `gt_multihop`;prepare 注人员软外键 |
| **L3_process** | 已建 | 事件排序 | `gt_event_order`;`_locatable_events` 跳复现值(源头修) |
| **L5_conflict** | 已建 | 官方vs传闻可靠度裁决 | `ws.conflicts`;gold=权威值 |
| L4_preference / L6_refusal / L7_consolidation | **规划未建** | — | 见 `L4_preference_design.md` |

---

## 4. 两道闸(验证三角的 A、B 边)+ 全部不变量

### 边A · well_posed(题面↔答案【良定义】,出题前,0 LLM)
基类默认 pass-through(opt-in 收紧)。各线覆写:
- **L1**:W0 结构 / W1 gt==世界重算(逐能力,主力,治错算 gold)/ W2 gold 非哨兵 / W3 答案类型与能力相符(治问谁答数)/ W4 IE 周锚结构(降级版,IE-only)/ W6 TR首变有参照 / W7 停用确有其事 / W8 ABS真缺失 / W9 证据齐。**W5(MR极值平手)实测10.3%误杀已撤**。
- **L2**:INV-0 结构 / INV-1 必须带周锚 / INV-2 链在W可解 / INV-3 gold==gt_multihop@W 重算(核心,治查无实据/混锚)。**INV-4(桥roles_of)13%误杀、INV-5(起点自带末跳)16/16误杀,均已撤**。
- **L3**:I0 长度 / I1 值存在 / I2 唯一可定位值(核心,源头已修故近0触发)/ I3 序==世界真序 / I4 无平手 / I5 停用位自洽 / I6 护城河重算。
- **L5**:I0 可溯源 / I1 order==ws.conflicts / I2 rule白名单 / I3 真矛盾 / I4 唯一权威解(核心,value_at_session)/ I5 gold锚定 / I6 可靠度可分 / I7 传闻非等价真相。

### 边B · grounding(答案↔语料【接地】,渲染后,0 LLM)
- `attributed(value, anchor, docs, window=90)`:value 与 anchor 在某篇 doc 的 **±90 字**内共现(均 `_norm` 后子串),any 语义。(★WINDOW 从 50→90:±50 把相距 68 的真归属误杀。)
- `gold_scalar`:IE/MR→gt.value、TR→gt.to、KU/PREEXPIRE/L2→str(gt)、L3/ABS/FORGET→None(线自处理)。
- 各线 `ground()`:L1 多能力(MR/TR 锚 gt 标注周;FORGET 找停用标记;ABS 反向判字段全语料不现);L3 每事件就近;L5 权威值就近。**fail-closed**:无 ground() / 抛异常 → 弃。

---

## 5. 闭环驱动器 closed_loop.build_to_target(给了 --min-questions 才走)
**反推**:`invert_rate(spec)` 用保守 survival 先验 + slack=1.4 → `{n_entities, n_sessions, target_orders(过度供给), max_n_conflicts}`,夹 clamp(实体 8-80、周 6-26)。
**总下限盈余摊派**:Σper_line_min < min_questions 时按比例摊进各线 floor。
**一轮**(`max_rounds=2`):
1. **①供给环**(`order_subrounds=2`,便宜不渲):world→orders→well_posed,算 `_order_deficit`(★带容差 max(1,round(配额×0.15)),噪声不算)→ 有实质赤字则 `_grow_for_supply`(★按缺口比例温和补,不再×1.4)重跑;否则跳出。
2. **出题 → 整轮重渲**(清 corpus ckpt 从头)→ **接地**。
3. **②floor校验**(`_floor_status`):met→MET返回;floor落不可行线→fail-open不空转;否则用**实测 survival** 重 invert_rate(slack×1.3,只增不减)进下一轮。
**fail-open**:耗尽 max_rounds 仍不达 → 标 `met_status=UNMET`,题库照出、留痕 manifest。

---

## 6. ★所有重试/循环层级(5 层嵌套 —— 这是耗时/调用数爆炸的根)
| 层 | 在哪 | 触发 | 次数 |
|---|---|---|---|
| **A. SDK 超时** | config.client | 死 socket | `max_retries=0`(SDK 不重试);timeout=read300/connect15/write30/pool15 |
| **B. chat_json 重试** | config.chat_json | API超时/网络/JSON解析失败 | **3 次**,指数退避 3/6/12s;失败再走 json_repair 兜底;3 次后抛 |
| **C. 信号重渲** | render._render_sig | 某事实没"实体+值就近(±90字)" | **最多 4 次**,每次带 hint 重写(★你问的"渲不对重渲4次")——deepseek 老对不准就烧满 4 |
| **D1. 世界实体轮** | world_gen.build_world | 实体没凑够 n_entities | **最多 4 轮** |
| **D2. 世界修复轮** | world_gen | validate 有 monotonic/fake_evolving 缺陷 | **最多 3 轮** |
| **E1. 供给子轮** | closed_loop ①环 | 实质赤字 | **最多 2 次**(长世界重跑) |
| **E2. 纠偏轮** | closed_loop ②环 | floor 未达 | **最多 2 轮**(整轮重渲) |
> **乘法效应**:一个渲不准的信号组 = 4 次(C)× 每次最多 3 次(B)= 最多 **12 次** chat_json,× ②纠偏重渲(E2 ×2)= **最多 24 次**。一个 0.06M 小 run 实测 258 次调用、其中 corpus ~200(4-attempt 把 ~80 篇该有的调用撑到 200)。**慢的地板是后端 per-call ~20-40s(deepseek 推理),× 几百次 ÷ 并发。**

---

## 7. ★补丁/修复编年史(性质标注:根因修 / 补丁 / 撤下代理)
| 改动 | 性质 | 治什么 |
|---|---|---|
| ★1 删"负责/汇报"中文子串猜人名字段 | 根因(改用 field_schema.kind=person) | 域相关、medical 失效 |
| ★2 L2 下一跳=真人名(非写死 CTO/CEO) | 根因 | 别名漂移 + 域相关 |
| bug#1 L5 矛盾文档不套 LEAK_BANNED | 根因 | 矛盾文档天然说"现在"被全滤 → L5 0/3 |
| bug#2 _render_sig 就近归属 + 2实体/组 + 4-attempt | 根因 | 渲染欠覆盖 L3 0/12 |
| 源头修 week_label / L3跳复现 / L5排己 | 根因 | off-by-one / 复现值 / 矛盾退化 |
| 4 道 well_posed 闸 + stage | 根因(边A) | 题面↔答案良定义 |
| 删 roles_of / W5 / INV-5 | **撤下代理** | 实测 13%/10%/16/16 误杀、0 真覆盖 |
| **FixA** TR gold 去裸 session(只留 1-based week) | 根因 | off-by-one 第5次复发(导出读 session 不读 week) |
| **FixB** interrogative 词表对齐 `numeric`(原写 number 假绿) | 根因 + 修自检假绿 | 问谁答数 |
| **FixC** phrase 铁律④ 保留疑问类型 | 约束 LLM | LLM 把"是多少"改回"是谁" |
| **FixD** L5 题面锁周 | 根因 | 未锁周+官方值漂移 |
| Fix② 赤字容差 + 比例长世界 | 根因 | 差1单触发14→49重渲 |
| **(债)** world_gen traps 中文关键词黑名单 | **补丁(审计点名,该删)** | 近重名 trap(与★1同款) |

---

## 8. ★每句"专门添加"的 prompt 及其用意(prompts.py)
| prompt.句子 | 治什么情况 |
|---|---|
| world.system「name 与类别相称、不跨类、不拿字段当名」 | 防"XX缺陷率"当实体名(run#3 幽灵实体) |
| world.system「numeric 轨迹非单调、极值落非首尾」 | MR 良定义(极值落端点=退化) |
| world.system「≥1 字段 null 结尾」 | 造 FORGET/停用基质 |
| world.system 硬约束④「专名表面互不近似、禁近重名」 | Fix3 近重名塌缩 |
| corpus.system「★就近归属第一硬约束:实体名与值同句/紧邻、下游±90字校验」 | bug#2 渲染欠覆盖(值散落后文接不上地) |
| corpus.system「防剧透:严禁当前/现在/最新/累计/现任…、严禁回顾'由X变Y'、stopped 写'自本期起停止'」 | IE/KU/FORGET 时效题不被剧透(LEAK_BANNED 18词) |
| filler.system「绝不碰任何被追踪的名/人名/字段」 | 草堆不污染信号(防 grounding 误判) |
| conflict.system「必须一眼看出未经证实/小道、含日期、传闻可说'现在'」 | L5 官方vs传闻可靠度(盲审唯一亮点);且不套 LEAK_BANNED |
| phrase.system 铁律④「保留疑问类型,是多少↔是谁↔哪一周不可改」 | FixC LLM 渲染器覆盖类型 |
| council.observe「main_entity=被逐周跟踪的主体,person字段是关系对象不是主体」 | ★1 域无关 + L2 桥实体定位 |
| council.skeptic「缰绳:领域专家点头、给confidence、宁缺毋滥」 | 怀疑视角不编造猎奇 |
| council.traps「★不要用近重名实体,已禁用」 | Fix3 源头禁 trap |

---

## 附:产物清单(output/runs/<id>/)
`manifest.json`(单一真相:status/stages/algo{entities,sessions,orders_by_line,well_posed,grounding,met_status,per_line_final,diversity})/`run.log`/`prompts.jsonl`(每次LLM调用留痕,含 latency_ms)/`00_input…06_grounded_questions.json`/`03_well_posed_report.json`/`06_grounding_report.json`。
