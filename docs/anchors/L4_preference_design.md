# L4_preference 产线设计(剩余三线的第一条)

> **坐标**(taxonomy):`L4_preference / 偏好隐式 / 从散落行为反推稳定偏好(从不明说)`。
> **状态**:设计稿(未落地)。落地接 `ProductionLine` 六件套 + 边A/边B闸,严守 `pitfalls_4lines.md` 避坑清单。
> **一句话**:给一串**散落的选择行为**,反推那个**从未明说**的稳定偏好——考【跨文档聚合】+【抗 recency】。

---

## 1. 能力定义(它和已建线不重叠在哪)
**偏好 = 实体在多次重复选择里的【众数】(dominant choice),且语料从不写"X 偏好 Y"——只能从散落的每次选择反推。**
- vs **KU(最新值)**:★关键区分——substrate 故意让**众数 ≠ 最近一次选择**。答"最近一次"(recency 偏置)= 错。这是 L4 的签名:考"看整体倾向"而非"背最新"。
- vs **MR(数值极值)**:L4 是**类别众数**,非数值 max/min。
- vs **L3(排序)**:L4 是聚合出的**一个偏好值**,非顺序。
- vs **L1/IE(某周值)**:L4 问"一贯倾向",非"第N周值"。

## 2. 世界基质(prepare —— 像 L2 加人员、L5 注矛盾一样,L4 注【选择流】)
对若干实体注入一个**周度复选字段**(域名从白皮书 category 字段取,如「本周部署方式」「值班排法」),每个 session 都重选一次(≠ mention-on-change 的演化,是【每期都表态】):
- 选项集 `O`(白皮书 category 取值池,≥2 且**表面互不近似**,见坑#7);
- 分布:偏好项 `pref` 占 `⌈ratio·N⌉`(ratio≈0.55–0.65),其余打散;
- **★recency 陷阱:最后一个 session 的值 ≠ pref**(强制"众数≠最新",否则退化成 KU);
- 偏好**绝不另起一个"偏好"字段**——它只活在选择流的分布里(从不明说)。
> 幂等(--force 不叠加),域无关(选项/字段名只从白皮书来)。

## 3. feasible / enumerate / gt(命门2)
- **feasible**:存在实体的复选字段满足【N≥K_MIN(默认 5)+ 唯一众数 + 众数领先亚军 ≥ MARGIN(默认 2)+ 众数≠最新】。prepare 之后判;不满足 → 跳过(不产 0 单)。
- **enumerate**:对每个合格 (实体,复选字段) 产 1 单。`gt = mode(values)`(代码算,确定)。
  `evidence_sessions =` 支撑众数的那些 session(让边B验"散落证据齐"),`aux = {options, n_pref, n_total, n_runner, latest}`。
- **gt(护城河)**:从世界复算 `mode`,与烘焙逐字段相等(过校验闸)。**gold = 众数那个类别值(干净 category 值,无 session/无 0-based)**——天然规避 off-by-one(坑#2)。

## 4. intent(题面)—— 疑问词走 kind、强制聚合、压 recency
```
q = interrogative(ans_kind)               # 坑#5:类别值的疑问词,不写死
s = f"综合【{ent}】这 {n_total} 期的「{fld}」记录,它【一贯/整体】更倾向于哪一种?{q}
     ——看整体倾向,别只看最近一次(最近一次未必代表偏好);答案是那个最常出现的选项。"
hide = [str(gt)] + [每期具体值?否——每期值要留在语料(边B证据),只隐藏"偏好结论"]
```
- 必须点明"综合整体""别只看最近一次"——把题面从"问某周"钉成"问聚合"(否则可被单文档/最新值答 = 退化)。

## 5. 边B 接地(grounding `ground()`)
偏好可推 ⟺ 散落证据在语料齐:**众数值 `pref` 必须在 ≥ K_MIN 个不同 session 的文档里就近实体出现**(每期"本周选了 pref"被渲到)。不足 → 弃(读者无从聚合)。
> 与现有 `attributed`(值+实体就近)同口径;只是要求"多周多篇"而非单篇。

## 6. ★边A 良定义(well_posed)—— 唯一性 + 可聚合性,且【实测 FP/覆盖】才上(坑#3)
偏好题良定义 ⟺ 偏好**唯一且可从证据稳定推出**。候选不变量(每条都要拿真实世界量 FP/覆盖后才保留):
- **WP1 唯一众数**:`mode` 唯一(无并列冠军)→ 否则"偏好"指代不唯一,drop。
- **WP2 领先够(可聚合性,边A∩边C)**:`n_pref - n_runner ≥ MARGIN`。★这是 L4 的核心,也是最需实测的:margin 太小(7:6)读者无法稳定聚合 = ill-posed;太大则误杀真偏好。**MARGIN 必须用真实分布量"误杀率 vs 漏放率"定标,不能拍脑袋**(否则就是又一个 roles_of)。
- **WP3 非平凡(抗 recency)**:`mode ≠ latest`。否则可被 KU 直接答中,题不考聚合 → 退化,drop(或降级标 trivial)。
- **WP4 样本足**:`n_total ≥ K_MIN`。
- **WP5 gold=复算**:`gt == mode(世界)`(护城河兜底)。
> **不设**"选项数""分布熵"之类看着像、实测未验的代理 —— 等真实数据说话。

## 7. 避坑清单对照(逐条确认 L4 不重蹈)
| 坑 | L4 如何避免 |
|---|---|
| #2 gold 出厂裸内部表示 | gold = 干净 category 值(无 session/周号);不涉时间表面 |
| #3 看着像的不变量 | WP2 的 MARGIN 必须真实数据定标(FP/覆盖),否则不上 |
| #4 闸当主力 | prepare/enumerate 只在"唯一众数+够领先+≠最新"时产单 → 闸≈防回归 |
| #5 类型错配 / LLM 自由 | 疑问词走 `interrogative(ans_kind)`;题面模板锁"问偏好/最常出现",受 phrase 铁律④ 约束 |
| #7 近重名 | 选项集复用 world 的表面互不近似约束;options 互异才产单 |
| #8 域相关 | 字段名/选项池只从白皮书 category 取 |
| 边C 是 L4 的命门 | 它本质是聚合题:WP2(够领先)+ 边B(证据齐 ≥K周)共同保证"读者真能从语料推出"——L4 是第一条把边C吃重的线 |

## 8. 自检用例(well-posed vs ill-posed)
- **WP(过)**:N=8,选 A×5/B×2/C×1,latest=C → mode=A 唯一、领先 3≥MARGIN、A≠C(latest)、A 在 5 周语料 → well_posed,gold=A。
- **IP1 并列**:A×4/B×4 → WP1 drop。
- **IP2 领先不足**:A×4/B×3(MARGIN=2)→ WP2 drop(读者难稳定聚合)。
- **IP3 退化**:A×6/B×2 但 latest=A → WP3 drop/标 trivial(KU 可答,不考聚合)。
- **IP4 样本不足**:N=3 → WP4 drop。
- 离线自检:造小 WorldState(注一个复选字段)+ 断言 enumerate/gt/well_posed;**MARGIN 用真实 run 的复选分布抽验定标**(像 L2/L3 那样跑 `output/runs/*/02_world.json`)。

## 9. 落地顺序(v0)
1. `pipeline/lines/L4_preference.py`:`PreferenceLine(prepare/feasible/enumerate/gt/intent/ground/well_posed)`;
2. 注册进 `lines/__init__.LINES`(taxonomy 里 L4 从 PLANNED 升 LINES);
3. 自检挂 `__main__` + 注册表不变量;
4. **先用真实 world 抽验 MARGIN/K_MIN 定标**(坑#3),再纳入闭环配额;
5. 端到端跑一轮看 L4 题盲审(重点验:不被 recency 答中、读者能聚合)。
