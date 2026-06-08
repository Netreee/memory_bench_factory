# 闭环 TargetSpec —— 把"随缘出题"变成"按目标管够"(§S 旋钮问题的算法详设)

> **缘起**:`redesign_factory_v2.3.md §S` 的旋钮问题——**整个工厂只有一个有效旋钮 `target_tokens`,而它只灌 filler(haystack),碰不到信号(benchmark 本体)**;你真正想拧的 `#questions`/每线配比/难度分布,要么不存在、要么"议会算了执行端不读"。本文把 §S 的蓝图落成**可实现的算法**。
> **一句话**:把工厂从 **open-loop(给 tokens、题数随缘)** 升成 **closed-loop(给目标 floor、管够为止)**;旋钮从"一个错的"变成"**一组对的、能设且被执行的**"。
> **状态**:**v0 已落地**(`pipeline/targetspec.py` + `run_factory_v2.build_to_target` + CLI `--min-questions/--per-line`;离线自检 `tests/closed_loop_selftest.py` 23/23)。v1/v2 仍蓝图。**已据 3 人盲审(§12)修订**:率模型不求精度(§3)、②实测纠偏环前置 v0(§2/§10)、survival 取保守值(§2/§9)、修 §4/§6 硬伤、订正 §8 落点真实成本。
> **前置阅读**:本文是 `redesign_factory_v2.3.md` 的**算法 companion**(非独立稿);命门2/3、接地闸、议会、白皮书、L1–L7、软外键、`_PLAN`、`feasible_lines`、§V-A 良定义闸、§G 接地、§W 世界 multi-agent、§S 旋钮 等概念定义见 v2.3,本文不复述。

---

## §0 病灶的精确形式

**`#questions = f(world)`** —— 题数是世界结构的【供给受限副产物】,链路:
```
世界结构 → 各线 enumerate(L1 写死 _PLAN、L2/3/5 受世界限)→ 出题 drop 空 → 接地闸 drop 未接地
```
后果:**既不能指定、也不能预测;`+token ≠ +题`**(token 只加 filler)。同理每线配比(议会算了 `weight` 执行端不读)、难度分布(根本没旋钮)都不可控。

**"旋钮"的定义 = ① 能设 ② 被执行。** 现状这俩都不满足。本设计 = 让它俩都满足。

---

## §1 TargetSpec:旋钮面板(能设的那一半)

```jsonc
TargetSpec {
  "min_questions": 120,                       // ★一等 floor —— benchmark 的本体就是题
  "per_line_min": {"L1_timeline":40,"L2_relational":20,"L3_process":15,"L5_conflict":10},
                                              // 各线下限(平衡;缺省由 active_lines.weight × min_questions 派生)
  "per_line_max": {"L1_timeline":60},         // 可选上限(防某线爆),缺省 None
  "difficulty_dist": {"easy":0.3,"med":0.5,"hard":0.2},   // 比例,和=1(见 §4)
  "haystack_ratio": 4,                        // ★haystack 轴(独立):corpus 字符 ≈ needle × (1+ratio)
  "time_span_weeks": null                     // 可选;null = 由信号轴反推(§3)
}
```
- **来源 & 约束力**:`TargetSpec` 由 CLI/config 给(`--min-questions / --per-line / --difficulty / --haystack-ratio`),并入白皮书 §4 schema。议会可**提议**默认(weight→per_line_min 比例),但用户的 TargetSpec 是**有约束力的合同**(§S.4 白皮书=被执行的合同)。
- **`min_questions` 是 floor 不是 target**:管够即停、不刻意压;`per_line_min` 之和 ≤ `min_questions`(余量分给高产线)。

---

## §2 核心难点 & 设计决策:为什么是【双环】

朴素闭环"生成→渲染→接地→数→长世界→重渲"**会被渲染拖死**(corpus 是最贵的极)。故拆成**廉价订单环 + 贵的接地补足环**:

| 环 | 在哪 | 代价 | 干什么 |
|---|---|---|---|
| **① 订单供给环** | world↔orders(**纯代码、无 LLM 渲染**) | 极低(只 world-gen + enumerate) | 把每线 **order 数**凑到 `target_orders_Lx`(已按存活率 over-provision);不够就长世界重 enumerate |
| **② 接地补足环** | render→ground→floor 检查 | 高(要渲染) | 渲一次→接地→数最终题;**若实测存活率低于先验导致 floor 未达**,才长世界 + **增量续渲**(有界 1–2 轮) |

**关键洞察:over-provision 用【存活率】做除数,让"渲一次就够"成为常态;② 只是安全网。** 而存活率正是接地闸(命门3)落在 `manifest.algo.grounding.survival` 的现成数 —— **闭环的除数白捡**。

---

## §3 产能率模型 + 反推(world 参数 ↔ order 数)

闭环要"反推:N 题需要多大世界"。需一个**可标定**的率模型(首轮用先验,之后用 manifest 实测刷新):

```
rate_L1(ws) ≈ min(quota_L1, supply_L1)        # L1 受配额上限;supply_L1 ≈ Σcap 各能力候选数
rate_L2(ws) ≈ k2 · n_person_entities          # 软外键 2 跳路径数 ∝ 人员实体数
rate_L3(ws) ≈ p3(change_density, n_sess) · n_entities   # 有 ≥3 跨字段跨周事件的实体数
rate_L5(ws) = min(max_n_conflicts, n_text_fields)       # = 注入上限(可配)
```
**★率模型只给【起点】,不求精度(盲审 C2:别信单点外推)**:
- 单点标定会崩——run #3 的 entity 是**部门**、人员是字段值提升来的实体(层级不同),换 medical(entity=人名)语义直接翻转;线性外推换规模必崩。
- 正确做法:对"软外键 2 跳可达对数""有 ≥3 跨周事件的实体数"等**实测 ≥3 个规模点、拟合带置信区间**;`invert_rate` 输出**夹在 `[n_min, n_max]`**(防反推出几百实体或太小)。
- 首轮可用粗先验(如 L3 每实体产 ~0.5 序列题、L5=max_n),但**真正的纠偏来自 ②实测环**(§2),不靠模型准。

**反推 `invert_rate(per_line_min, survival, slack)`**(求满足供给的**最小**世界):
```
target_orders_Lx = ceil(per_line_min_Lx / survival_Lx) · slack          # over-provision,slack≈1.25
n_person_needed  = ceil(target_orders_L2 / k2)
n_ent_needed     = max( ceil(target_orders_L3 / p3),  n_person_needed,  …)
quota_L1         = target_orders_L1                                       # L1 配额直接设
max_n_conflicts  = target_orders_L5
n_sess           = spec.time_span_weeks or default_for(change_density)
```
> 模型不必精确——**它只给起点,②环用实测纠偏**。★survival 先验取**保守值**(历史 P25 或实测×0.8;盲审 C1:两个近同 run 的 survival **0.865 vs 0.633、近 2× 方差**,且 §G 自承偏乐观上界 → 必须保守,否则反推世界系统性偏小、floor 系统性不达)。
>
> **协调规则(盲审 A4)**:over-provision 作用于 **orders**(中间品),不是终题;`per_line_min` = 每线 floor、`min_questions` = 总 floor(`Σ per_line_min ≤ min_questions`,余量给高产线);**超过 floor 的好题【保留不砍】**(上限 `per_line_max` 防爆)——"管够" = 多供好题,不是卡死在 floor。

---

## §4 难度分布旋钮(§S.3 的算法化)

把"难度"做成**可设 + 可证伪 + 被执行**的旋钮:
1. **度量(纯 gt 代码,超越"祈祷变难")**:每线加 **`difficulty_score(order) -> float`**(第 6 件套,和 `gt`/`intent`/`ground` 并列)。轴 = 结构量:
   - L1:`at_week` 距最近变更周(中段更难)、聚合跨度;L2:hop 数 + 是否跨周拼接 + 桥实体重名度;L3:事件数 + 时间紧密度;L5:小道值与权威值的相似度。
   - 切档点 = 全线 score 的**等分位**(定义谁算 easy/med/hard)**叠一个绝对 score 阈值**(盲审 C3:纯相对分位永远切 1/3、保证不了"hard 档绝对更难";绝对阈值才锁死"hard 是真难")。
2. **控制(切档 ≠ 配比,盲审 A6 厘清两者是两回事)**:**切档点**(上条)决定每题落哪档=分类;**`difficulty_dist`**(用户给的 0.3/0.5/0.2)是**采样配额** —— 在 `每线 × 每档` 格子里按配额取。**缺档**用 §9.3 五算子代码版合成(`+hop/+distractor…`,gt 代码重算;**合成题必重过 §G 接地 + 可答性闸**,防"gt 成立却可被规则识破")或长世界。
3. **验证(可证伪)**:三系统(`eval/multi_system.py`)算 `Difficulty=1−max_acc`,验"hard 档 acc 真的更低";落 `manifest.algo.difficulty`(像 §9.6 多样性)。
> **难度在【接地存活后】统计**(难题更易掉接地,§G)——分层供给目标也要按存活后计数,故难度并进 ② 环。

---

## §5 闭环主算法(伪代码,具体到可实现)

```python
def build_to_target(spec: TargetSpec, scenario, max_rounds=3):
    survival = load_survival_prior()                       # 历史 manifest 或默认 {L1:.92,L2:1,L3:.57,L5:1}
    params   = invert_rate(spec.per_line_min, survival, slack=1.25)   # → n_ent,n_sess,quota_L1,max_n,n_person…
    feasible = None
    for rnd in range(max_rounds):
        ws = build_world(params)                           # §W.3 修复轮 + 消费白皮书(已落地)
        feasible = feasible_lines(ws, profile)             # 依赖图:基质喂不饱的线 → 退出其 floor(不死循环)
        # ── ① 订单供给环(纯代码,无渲染)──
        for _ in range(3):
            orders = run_lines(ws, quotas=params.target_orders)        # ★配额驱动,取代 L1 写死 _PLAN
            short  = {Lx: params.target_orders[Lx]-count(orders,Lx)
                      for Lx in active if feasible[Lx] and count(orders,Lx) < params.target_orders[Lx]}
            if not short: break
            grow_world(ws, params, short)                  # 长世界补缺线(见 §3 反推:加实体/周/text字段/max_n)
        # ── 出题 + 渲染(贵)+ 接地 ──
        questions = phrase_questions(orders)               # + §V-A 良定义闸(题面坐标齐才出厂)
        corpus    = render_corpus(ws)                      # 增量续渲(resume:只渲新增实体/周)
        grounded  = grounding_gate(questions, corpus)      # 命门3
        # ── ② 楼层检查 ──
        survival  = measure_survival(grounded, questions)  # 刷新【实测】存活率(喂回下一轮 + 历史)
        ok_floor  = total(grounded) >= spec.min_questions
        ok_line   = all(grounded_count(grounded,Lx) >= spec.per_line_min[Lx]
                        for Lx in active if feasible[Lx])
        ok_diff   = difficulty_satisfied(grounded, spec.difficulty_dist)
        if ok_floor and ok_line and ok_diff:
            return grounded, "MET"                         # ✅ 出厂可信题库 ≥ floor
        # 不够:按【实测】存活率重算 target_orders、长世界、重来(② 环,有界)
        params = invert_rate(deficit_targets(spec, grounded), survival, slack=1.4)  # slack↑
    # ── 兜底:到顶仍不够 → fail-open(出现有 + 标缺,绝不假装满足)──
    return grounded, f"UNMET: 缺 {unmet_breakdown(spec, grounded)}"
```

**几个要点**:
- **① 环纯代码**(world-gen + enumerate,无渲染)→ 跑得起多轮,把订单供给凑足后**才渲一次**。
- **存活率双用**:over-provision 的除数(先验)+ ② 环的纠偏(实测)。
- **`feasible_lines`(依赖图,已落地)兜底死循环**:场景没 person 字段 → L2 永远供不上 → 退出 L2 的 floor + 日志,不无限长世界。
- **fail-open**:到 max_rounds 仍不达 → **出厂现有题 + manifest 标 `UNMET` 及缺口**,绝不静默假装满足(诚实)。

---

## §6 两轴耦合(signal × haystack)

```
needle_chars  = Σ 信号文档字符                # 信号轴产物(由 ①环 + 渲染定;中文按 1 字≈1 token 计)
corpus_chars  = needle_chars · (1 + haystack_ratio)        # ratio=4 → 信号 1 份 + filler 4 份
filler_per_week ≈ (corpus_chars - needle_chars) / n_sess / 800   # 每篇 filler ~800 字
```
> **修正(盲审 A5)**:去掉原 `max(...)` 退化(ratio≥0 恒取后者 = 死代码);单位统一用**字符**(中文 char≈token,落 config 再换算)。`haystack_ratio` 只调 filler 倍数、**floor 在 needle**:先由信号轴定世界+needle,再按 ratio 灌 filler —— 取代现状"给 tokens、needle 随缘"。

---

## §7 白皮书 = 被执行的合同(§S.4 落点:谁消费哪个旋钮)

| 旋钮 | 现状 | 改后(谁执行) |
|---|---|---|
| `min_questions` | 写死 200、不绑定 | **闭环主算法**(§5)反推 + 兜底 |
| `per_line_min` | 不存在(L1 _PLAN 写死) | `run_lines` 按配额产;L1 `_PLAN` **退役 → 配额参数** |
| `active_lines.weight` | 议会算了**一行没读** | 派生 `per_line_min` 默认值 |
| 实体数 / 周数 | 公式 `2×rel+6` / 写死 `10` | `invert_rate` 反推(§3) |
| `difficulty_dist` | 不存在 | `order_gen` 分层采样(§4) |
| `traps/change_density` | W.3 刚起步读 | build_world 继续消费(已落地) |

→ 白皮书从"愿望清单"变"**被执行的合同**"。

---

## §8 Stage 集成(不破坏现有 DAG)

- **新增 `TargetSpec` 入口**:CLI/config → 白皮书 §4 schema 加 `min_questions/per_line_min/difficulty_dist/haystack_ratio`。
- **新增 benchmark 级 driver `build_to_target`**(§5):它**包在 `drive(STAGES)` 之上**,把 world/orders/render/ground 当子步,加闭环。单 run 内多轮 = 复用 world 批次 top-up 的循环模式,抬到 benchmark 层。
- **改动落点 + 真实成本(盲审 B 订正:不是"小而集中",有两处大重写)**:
  - **★`run_lines`(大重写,v0 真地基之一)**:现状只取**一个全局 `target`、各线 `[:target]` 截断、无下限**,L1 `_PLAN` **写死且无视 target**。改配额驱动 + `_PLAN` 退役 = 动 base 契约 + 4 条线 + `run_lines` 多轮 top-up。
  - **★`build_world(params)`(中-大,v0 真地基之二)**:现状 `n_ent=2×rel+6`、`n_sess` **议会硬编码 10**、`max_n_conflicts` 在 L5 写死 3;要接显式参 + 新写 `invert_rate`。
  - **`build_to_target` driver(中)**:`drive(STAGES)` 是**一次性幂等**(`is_done` 会跳过单 run 内多轮重跑)→ 多轮闭环要新写 + 让 stage 可强制重跑。
  - **难度第 6 件套(中-大,v1)**:`ProductionLine` 现只 5 件套、无 `difficulty_score`;**`order_gen` 模块其实不存在**(已并进 `L1_timeline`,只按 cap 取前 n)→ 分层采样 + §9.3 算子全新写。
  - **`render_corpus`(小-中)**:现只认 `target_tokens`(filler-only,正是 §S 病灶),要加 needle 反算。**★resume 是【整周】跳过**——长世界后新增实体落旧周会**被整周跳过、漏渲**;entity 级增量续渲**当前缺失**(归 v2;v0 ②环用整轮重渲)。
  - **★`feasible_lines`:在库但编排从不调用(零件没装上)**——§5 靠它兜死循环,v0 需先接线。
  - grounding:不动,产 survival 喂闭环(✓ 已落地);`manifest.algo` 加 `targetspec / met_status / per_line_final`(留痕 + 论文证据)。
- **与 §W 协同**:闭环反推世界规模 = architect 按 TargetSpec 搭骨架(L2 大世界);难度结构轴正是 architect 要布的料。

---

## §9 边界 & 风险(诚实)

1. **不收敛**:`max_rounds` 有界 + **fail-open**(出厂现有 + 标 `UNMET`)。绝不为凑数塞坏题(坏题被 §V/§G 闸挡在外)。
2. **某线天生供不上**(场景无 person 字段→L2):`feasible_lines` 提前退出其 floor + 日志,不死循环。★**盲审 C6**:它现在是静态二值谓词、会误杀"稀疏但能产"的世界 → 改成**返回估计供给量**、低于阈值**降配额而非退线**(且需先接线进编排)。
3. **★survival 不稳 + 偏乐观(盲审 C1,核心风险)**:近同 run survival **近 2× 方差**、§G 自承上界 → 反推世界系统性偏小、floor 系统性不达。**对策**:保守 survival(P25/×0.8)+ slack 跟方差 + **②实测纠偏环前置 v0**(见 §10),不靠先验一锤。
4. **★floor↔质量【负相关】(盲审 C4,改原"两者正交"的错判)**:floor 未达→①环唯一动作"长世界",而**数据显示世界越长 survival 越易掉**(0.865→0.633)→ 追 floor 客观上压低质量。**对策**:留痕"长世界→survival 趋势"、下降即告警 + 限制世界增长上限;质量闸(§V/§G)始终不松。
5. **★冷启动(盲审 C5)**:新域无 survival 历史(默认值是 office 的、新域可能只产部分线)→ 首轮**强制大 slack(≥2)或先跑一轮纯探测**再据实测反推。
6. **再渲成本**:保守 survival + slack 让"渲一次够"成常态;**v0 的 ②环用整轮重渲**(entity 级增量续渲缺失、归 v2)。
7. **难度↔接地耦合**:难题更易掉接地 → 难度分布在**接地存活后**统计(并进 ②环)。

---

## §10 落地路线(v0 → v2)

- **v0(真地基)— ✅ 已落地**:① `run_lines` 配额驱动 + L1 `_PLAN→_CAP_WEIGHT` 退役(配比非写死);② `build_world` 经 driver patch 白皮书 `entities.count/n_sessions` 消费显式规模 + `invert_rate`(夹 clamp);③ `TargetSpec(min_questions+per_line_min)` 入口 + `build_to_target` driver(**直接调底层 stage 逻辑函数管自己循环,不套 `drive`**,盲审 B3);④ **①订单供给环**(`build_world+run_lines` 多子轮,赤字→`_grow_for_supply` 长世界,绝不渲)**+ ②实测纠偏环**(渲一次→接地→`_floor_status`→不达就按**实测 survival** 重 `invert_rate`+整轮重渲,有界 `max_rounds`)都在 v0;⑤ `line.feasible` 接线兜死循环 + 不可行线 floor → 永久 `UNMET` 不空转;⑥ fail-open 标 `met_status` 留痕 `manifest.algo`。**总下限盈余按比例摊进各线 floor**(消"总数单独差"歧义)。决策逻辑抽 4 个纯函数离线自检。→ "按目标管够题数 + 平衡配比",survival 纠偏内建。
- **v1(暂缓但★重要,主理人已确认记着)**:难度旋钮(`difficulty_score()` 各线 + 切档绝对阈值 + 分层采样 + `manifest.algo.difficulty` 验证,详 §4)。诚实边界:`difficulty_score` 只是【生成侧代理】,"对模型真难不难"须靠多系统评测的正确率分布校准 → v1 落地要与 `eval/multi_system.py` 联动。
- **v2**:**entity 级增量续渲**(②环不再整轮重渲、大幅降成本,详 §10.1)+ 两轴 needle:haystack 精配 + 率模型多点拟合带 CI。

## §10.1 增量续渲(v2.1)具体方案 —— 已批准、待实现
**病**:②实测纠偏环现在【整轮重渲】(floor 不达→`unlink` corpus ckpt→`stage_corpus` 从头),corpus 是最贵步,成本爆炸(`closed_loop.py:127`)。
**根**:②环把世界【重建】(`stage_world` 重跑 `build_world` 出全新更大世界)→ 旧 corpus 失效→只能整轮重渲。
**正解(加性长世界 + 只渲增量,★非 ckpt 特判补丁)**:
1. **`build_world` 加 augment 模式**:`build_world(wp,…,existing=ws)` —— 只生成 `target - len(existing)` 个【新实体】(名避开 existing + `seen_base` 主干),merge 进 existing;`existing=None` 即现行全量。
2. **`render_corpus` 加 delta**:`render_corpus(…, only_entities=set)` —— 每周【只渲这些新实体】的 signal(filler 已在、不重生),`by_id[s]["docs"].extend(新docs)` 追加;不 `unlink`、不重渲旧实体。
3. **stage 经 config 信号驱动(不复制 stage 体,守 B3)**:`stage_world` 见 `config["augment"]` + 存在 `02_world.json` → augment;`stage_corpus` 见 `config["render_only"]`(新实体名集)→ delta 追加。
4. **`closed_loop` ②环改加性**:不再 grow→重建→整轮重渲;改为 augment(ΔN 实体)→ 算 `新实体 = set(新世界)-set(旧世界)` → 置 `config["render_only"]` → delta 渲 → 重 enumerate(世界更大→更多 order)→ 重接地。旧实体 docs 原样保留。
**自检**:augment 后 `name_collisions=空`(新实体不撞旧)、delta 渲后旧周 docs 数只增不减且旧 doc 不变、`render_only` 子集渲出的 signal 就近新实体可接地。
**成本**:O(整 corpus) → O(ΔN 实体)。**风险红线**:绝不用"哪周渲过了"的散装 if 兜;只走 augment(加实体)+ delta(渲新实体)两个干净原语 + config 信号,无 ckpt 特判。

---

## §11 出处 / 依据
- `redesign_factory_v2.3.md §S`(规模与难度目标,蓝图)+ §V(良定义/接地不松)+ §W(世界 multi-agent / 反推骨架)+ §G(接地存活率=闭环除数)。
- `research_difficulty_distribution.md`(难度三件套 + IRT 极化谱风险)。
- 标定数据:`output/runs/office__20260604-190554`(run #3:orders/survival 实测)。
- **落点**:`build_world / run_lines / order_gen / 产线 difficulty_score() / 白皮书 §4 schema / 新 build_to_target driver`。

---

## §12 盲审结论(3 个独立冷读 subAgent,2026-06-04)

> 方法:① 纯新人冷读(只给本页,测自包含性);② 实现者(对照现有代码,测"照着写卡在哪");③ 算法怀疑者(用 `output/runs/` 三个 run 实测数据测逻辑站不站得住)。三人独立、互不通气。**总评:动机/骨架("open→closed loop、双环、survival 当除数")讲得清且有说服力;但一落到"怎么做"就靠外部文档 + 黑话 + 单点标定撑着——当前据此无法实现,且两条核心假设被实测数据动摇。**

### A. 第一次读冒出的问号(自包含性 / 清晰度)
1. **★全文不自包含**:`§S/§W/§W.3/§V/§V-A/§G/§9.3 五算子/§9.6` 全在 `redesign_factory_v2.3.md`,本页只给名字没给定义;且本文自己也有 §9(内容是"风险"),"§9.3 五算子"到底指本文还是父稿,新人分不清。→ **开头需注明"前置阅读 v2.3 全文",或给被依赖概念各一句话内联定义**。
2. **黑话零解释**:命门3、接地闸、议会、`_PLAN`、白皮书、L1–L7、软外键、needle、survival、feasible_lines —— 新人接不上。
3. **★§3 率模型自相矛盾**:`k2≈10/15_persons` 里的 **"15 persons" 在 run#3 数据里不存在**(上文只给 n_ent=29);标定只展示结果数字、不展示拟合方法。**这是本页就能发现的硬伤,不靠外部文档**。
4. **§1↔§3 张力**:`per_line_min` 之和(85)、`min_questions`(120)、over-provision ×1.25~1.4 三者如何协调?"管够即停"和"放大供给"谁砍超量、按什么规则?没讲。
5. **§6 公式**:`max(needle, needle·(1+ratio))` 当 ratio≥0 **恒取后者**(死代码/疑似写错);`needle_chars`(字符)直接进 `corpus_tokens`(token)、`/800` 魔数、`g(orders,n_sess)` 全黑箱——单位 char/token 反复横跳。
6. **§4 自相矛盾**:§4.1"全线 score 等分位切(=1/3 each)" vs §4.2"按用户 `difficulty_dist` 0.3/0.5/0.2 采样"——切分点到底按哪个?
7. **"已落地" vs 状态栏"未落地"** 矛盾:哪些现成可复用、哪些要新写,新人无法区分。

### B. 照着实现会卡在哪(对照现有代码,按"最挡路"排)
1. **★卡点1(大重写)`run_lines` 根本不是配额驱动**:现状 `run_factory_v2.py:run_lines` 只取**一个全局 `target`(默认200)**,各线 `enumerate` 用 **`[:target]` 截断**(无"产够 N"下限);**L1 `_PLAN` 写死且无视 `target`**(`L1_timeline.py`)。"配额驱动 + L1 `_PLAN` 退役"要改 base 契约 + 4 条线 + `run_lines` 多轮 top-up,全新写。
2. **★卡点2(中-大)`invert_rate` 零踪迹 + `build_world` 不接显式 `n_ent/n_sess`**:现状 `n_ent/n_sess` 从白皮书 dict 抠,且 `central_office` 把 `n_sess` **硬编码 10**、`n_ent=2×rel+6`。`max_n_conflicts` 也要打通到 `L5`(现写死 3)。
3. **卡点3(中)TargetSpec / `build_to_target` driver 全不存在**:`drive(STAGES)` 是**一次性幂等扫 stage**,`is_done` 会把"单 run 内多轮重跑 world/orders"**跳过**——多轮循环要新写,且要让 stage 可被强制重跑。
4. **卡点4(中-大)难度第6件套全缺**:`ProductionLine` 只有5件套、无 `difficulty_score`;`order_gen` 模块其实**不存在**(已并进 `L1_timeline`,只按 cap 取前 n、无分档);分层采样 + §9.3 五算子 = 零实现。
5. **卡点5(小-中)`render_corpus` 只认 `target_tokens`**(filler-only,正是 §S 病灶),无 `needle_chars` 反算、无 floor 保护。
6. **★"已落地"声明真伪核对**(实现者实查):§W.3 修复轮 **✓真**;接地闸 **✓真**;`config.pmap` **✓真**;survival 落在 `manifest.algo.grounding.survival` **✓真**(闭环"除数白捡"成立);`active_lines.weight` "写了没读" **✓属实**。**但**:`feasible_lines` **在库、编排从不调用(零件在没装上)**——§5 靠它兜死循环,需接线;`difficulty_score` **✗没有**;**resume 增量续渲半真**——现为**按【整周】跳过已完成周**,长世界后新增实体落在旧周会被**整周跳过、漏渲**,②环要的"entity 级增量续渲"**实为缺失**(大改)。

### C. 算法逻辑的硬风险(数据实证,按致命度排)
1. **★[高] survival 除数在真实数据里不稳**:两个近同配置 office run 的 overall survival **0.865 vs 0.633**(L1 .96 vs .65、L3 .571 vs .333),近 2× 方差;`slack 1.25` 吃不掉 → 稳定触发"长世界→survival 又漂→再长"②环震荡。且父稿 §G 自承 survival 是**偏乐观上界** → 反推世界系统性偏小 → **floor 系统性不达**。**补**:survival 取区间(历史 P25 或 ×0.8),slack 跟方差走;先把 §G 收紧再用它做除数。
2. **★[高] §3 k2 标定既错又单点**:`n_person=15` 不在 manifest(office 的 entity 是**部门**,人员是字段值,层级不对);换 medical(entity 是人名)同一公式语义翻转;单点 + 线性外推换规模必崩。**补**:对"软外键 2 跳可达对数"实测、≥3 点拟合带置信区间;`invert_rate` 输出**夹在 [n_min,n_max]** 防反推出几百实体。
3. **[中] §4 分位切档 vs 可证伪难度自相矛盾**:等分位永远切 30/50/20(相对),无法保证"hard 档绝对更难";难度在接地后统计 × 各档存活率不同会**放大第1条的方差**;五算子合成易造"gt 成立但可被规则识破"的题。**补**:分位之上叠**绝对 score 阈值**,合成题必重过 §G + 可答性闸。
4. **[中] floor 与质量非正交(§9.6 存疑)**:floor 未达时①环唯一动作是"长世界",而数据显示**世界越长 survival 越易掉**——追 floor 客观上**压低**质量,二者负相关。**补**:留痕"长世界→survival 趋势",出现下降即告警。
5. **[中] 冷启动无解**:medical run 无任何 survival 历史(且 6 线只产出 L1/L2),`load_survival_prior` 对新域**无历史可加载**,默认值是 office 的。**补**:新域首轮强制大 slack(≥2)或先跑一轮纯探测。
6. **[低] `feasible_lines` 偏保守**:静态二值谓词(L3 需 ≥3 跨周事件)会把"少量能产"的稀疏世界误判不可行而退 floor。**补**:返回**估计供给量**而非 bool,低于阈值降配额而非退线。

### 一句话给作者
**v0 的真正地基是 B-卡点1(配额驱动 + L1 `_PLAN` 退役)和 B-卡点2(`build_world` 接显式参数 + `invert_rate`)——这俩不动,后面全写不下去**;而 C-1/C-2(survival 方差 + k2 单点标定)说明**反推模型先别信精度、只当起点,②环的实测纠偏必须前置到 v0**(否则 v0 的 over-provision 会系统性不达 floor)。另外:本页务必先补"前置阅读 + 关键概念内联定义",并修掉 §3 person=15、§4 切档矛盾、§6 max() 退化这三处**本页自身就能改的硬伤**。
> 证据:`docs/anchors/blind_review_*`(同协议盲审)、`output/runs/office__20260604-{161117,190554}/manifest.json`(survival 方差实测)、`pipeline/{run_factory_v2,lines/*,run}.py`(卡点代码依据)。
