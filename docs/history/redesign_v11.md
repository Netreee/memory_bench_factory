# Redesign V11 — 对标主流后的 Tier 0 升级（治"单调病" + 补题型 + 提保真）

> **来源**：`benchmark_landscape.md`(对标 17 个主流 memory benchmark)+ 4 路 subAgent 头脑风暴 + 主理人评审分级。
> **V11 = V10 as-built + Tier 0 五件**(便宜、低风险、gt 机制基本不动)。Tier 1/2 见 §9 路线图。
> **基线**：Stage 0/A/B 与 v10 完全一致;本文档只详写 V11 改/新的步骤。每步写清 输入/谁/逻辑/输出/示例。

## V10 → V11 改动总览

| # | 步骤 | V10 | V11 | 治什么 |
|---|---|---|---|---|
| 1 | Stage C 世界 | 数值轨迹常单调 | **★非单调形状(ShapeSpec)** + assemble 校验内部极值 | 单调→MR极值永端点/TR首变=第二次出现/CONFLICT误判 |
| 2 | Stage D-0 草堆 | filler 用通用模板 | **★场景化 filler palette + 实体名黑名单** | legal填科技味filler、人名串扰 |
| 3 | 点菜 能力 | IE/MR/TR/KU/CONFLICT/FORGET/ABS | **+ ORDER(事件排序) + 复合题(compose) + DURATION** | 补 BEAM event-ordering;造引擎独有难题 |
| 4 | 点菜 IE | 时点切片 | **+ 决策时点 framing**(反 recency) | 提区分度(recency baseline 必挂) |
| 5 | Stage F/G 判分 | 二值 LLM-judge | **+ ORDER 的 Kendall-τ 判分** | 排序题部分给分 |

> **MR 留空待定**:office/legal 真系统评测出来后,按"做实(Memora事件流) or 降级(DURATION/interval)"二选一,届时补进本文档 §8。

---

## Stage C V11 — 多实体世界（+ ★非单调形状 ShapeSpec）

- **输入**:spec + dims + plan + n_entities + n_sessions（同 v10）
- **谁**:LLM 填世界表 + 代码 assemble（同 v10）+ **新增形状校验**
- **逻辑（v10 基础上加）**:
  1. `WORLD_SYSTEM` prompt 增约束:**数值 evolving 字段的 trajectory 必须【非单调】**——制造峰/谷/平台/反弹(如 `2.5%→1.8%→2.8%→2.1%`),严禁一路单调升/降;**每个数值演化字段至少有一个【内部极值】**(max 或 min 落在非首非尾的某周)。
  2. `assemble_world` 新增 `_shape_issues(ws)`:对每个数值 evolving 字段,算 argmax/argmin 的 session;若**两者都在端点**(={首,尾}) ⇒ 单调,记 issue `"X 轨迹单调,MR 退化"`(非致命,供回炉/标弱题)。
- **输出**:`(WorldState, table, issues)`,issues 含单调告警
- **示例**:`AI工程部.P0缺陷率: 2.5%@s0 → 1.8%@s2 → 2.8%@s4 → 2.1%@s6`（峰 2.8%@s4 = 内部极值 → MR-max 不在端点,必须扫全程）
- **出处**:`stages_v10.py:WORLD_SYSTEM` + `world_state.py:assemble_world / _shape_issues`

---

## Stage D-0 V11 — Filler Palette（★新子阶段，场景化草堆）

- **输入**:spec（场景描述）+ WorldState（取被追踪实体值做黑名单）
- **谁**:LLM(每场景一次)
- **逻辑**:
  1. LLM 按 `spec.description` 产出**领域匹配**的 filler 配置:`{filler_topics:[本领域杂事], filler_person_names:[虚构人名], filler_org_names:[虚构机构]}`(律所→开庭排期/案卷归档/律协培训;科技→团建/扩容/培训)。
  2. **★实体名黑名单**:`_tracked_proper_nouns(ws)` 收集世界里所有人名/机构名型字段值;palette 的人名/机构名与之取交集为空,渲染后再扫一遍命中则丢。
- **输出**:`palette = {topics, person_names, org_names}`
- **示例(legal)**:`{topics:["庭审排期","案卷归档","律协CLE培训","所务会"], person_names:["柳明哲","简若彤"(均不与被考承办律师撞)]}`
- **出处**:`stages_v10.py:stage_d0_filler_palette`（新）

## Stage D-filler V11 — 加草堆（用 palette + 双黑名单）

- **输入**:WorldState + corpus + palette
- **谁**:LLM(`FILLER_SYSTEM` 注入 palette) + 代码(双黑名单扫描)
- **逻辑（v10 基础上）**:filler 只许用 palette 的 topics/names;扫描黑名单 = **字段名(v10) ∪ 被追踪实体值(新)**,命中任一 → 丢该 filler
- **输出**:corpus（filler 领域匹配、零串扰）
- **出处**:`stages_v10.py:FILLER_SYSTEM / stage_d_add_filler_v10`

---

## 点菜 V11 — 新增能力 + 复合题（`order_gen.py`）

### 新 gt 函数（`world_state.py`，纯代码、可单测）

| 能力 | 函数 | 算法 | gt 示例 |
|---|---|---|---|
| **ORDER** 事件排序 | `gt_event_order(ws,entity,k)` | 取该实体跨字段 ≥3 个变更 op,按 (date,session) 排序 = gt 序;打乱呈现 | `["负责人换李娜","P0达峰","Oncall归零"]`(真序) |
| **DURATION** 持续时长 | `gt_duration(ws,entity,field,value)` | 某 value 从被 SET 到被下一个 op 取代,跨几个 session | `张三任负责人 3 周(s0-s2)` |
| **复合 FORGET→IE** | `gt_pre_expire(ws,entity,field)` | EXPIRE op 的 `prev` = 停统计前最后值 | `P0 暂停前最后是 2.8%` |

### `_candidates` 新分支
- **ORDER**:对每个实体,收集所有 UPDATE/EXPIRE(变更)op,若 ≥3 → 采样 k=3 个(尽量跨字段、跨周),订单 `{capability:ORDER, gt:[ordered events], scorer:"kendall_tau", events:[...]}`。evidence = 这些事件所在周(天然跨文档)。
- **DURATION**:对 evolving 字段,选一个有明确"开始-结束"的值(被后续 op 取代的),gt=持续周数。evidence=[设值周, 取代周]。
- **复合 FORGET→IE**:对 EXPIRE 字段,订单 `{capability:IE, subtype:"pre_expire", gt:prev值}`,问"停统计前最后是多少"。evidence=[值周, EXPIRE周]。
- **IE 决策时点 framing**:现有 IE 时点切片订单加 `aux["framing"]="decision_time"`(只对"该周值≠最新值"的,保证反 recency)。

- **输出**:orders（含 ORDER/DURATION/复合/decision-time 标记）
- **出处**:`order_gen.py:_candidates` + `world_state.py` 新 gt 函数

---

## Stage E V11 — 出题句式（新能力）

- **输入**:orders + corpus（同 v10）
- **谁**:LLM(`QUESTION_SYSTEM_V10` 增能力句式)
- **逻辑（新增句式）**:
  - **ORDER**:"把以下几件事按【发生先后】排序:①… ②… ③…"(给事件描述、不给时间;答案是有序列表)
  - **DURATION**:"X 一共持续/担任了几周?"(★只问跨度,不泄漏起止周)
  - **复合 FORGET→IE**:"那个【已暂停统计】的指标,在停掉前最后一次是多少?"(★题面不写该值)
  - **IE 决策时点**:"复盘第 N 周那次决策——【当时】团队掌握的 X 是多少?"(制造"当时 vs 现在"对抗,反 recency)
- **输出**:raw questions（ORDER 题 answer 为有序列表 + `scorer` 标记）
- **示例**:`{"question":"把这三件事按先后排序:负责人换人、P0达峰、Oncall归零","answer":["P0达峰(s2)","负责人换人(s3)","Oncall归零(s5)"],"capability":"ORDER","scorer":"kendall_tau"}`
- **出处**:`stages_v10.py:QUESTION_SYSTEM_V10 / stage_e_synthesize_v10`

---

## Stage F/G V11 — 判分扩展（★ORDER 的 Kendall-τ）

- **输入**:raw + corpus（同 v10 四闸）
- **谁**:代码 + LLM
- **逻辑（v10 基础上）**:
  - 四闸不变(grounding/题答自洽/硬判别器/judge_wf);ORDER 题的"题答自洽/判分"走专用 `_grade_ordering`:
    1. LLM-judge 从模型回答里**抽取它给的事件顺序**(返回事件的排列);
    2. 代码算 `kendall_tau(预测序, gt序)` ∈ [-1,1];归一化 `(τ+1)/2 ∈ [0,1]`;
    3. 二值矩阵下 `τ ≥ 0.5 → 1`(允许个别相邻交换),连续分存 `aux["tau"]` 供 IRT/区分度用。
  - Stage G `_judge_grade` 对 `capability=="ORDER"` 派发到 `_grade_ordering`;其余能力同 v10。
- **输出**:passed + reject_log;ORDER 题带 `tau`
- **出处**:`stages_v10.py:_grade_ordering / _judge_grade / stage_f_validate_v10`

---

## §保留（V10 已对的，不动）
状态机+state-diff 机械 gt、mention-on-change 防剧透、四闸(grounding/题答自洽/硬判别器/judge_wf)、Stage G full_oracle 开关 + 空答案鲁棒、真系统 EmbedMemory 评测、json_repair 兜底、长文档+草堆(v11 doc-length 2000字)。

## §8 MR 已定：保留现状（真系统数据已出，2026-06-02）
**决策:保留现状 = (A)/(B) 都不做,MR 仍按 gt_mr max/min over trajectory,靠 V11 非单调撑区分度。**
依据(EmbedMemory 真系统 by_capability,跑在 V10 单调 benchmark 上):

| 场景 | MR 真系统命中 | 解读 |
|---|---|---|
| office | **4/9 (44%)** | 数值多且分散 → 检索漏召回 → 有区分度 |
| itops | **1/2 (50%)** | 同上 |
| legal | 6/6 (100%) | "诉请金额"近单值 → 退化(数值稀疏场景的固有现象,非 MR 本身的病) |

- MR 在**数值丰富场景**对真系统就是有难度(44–50%),不该砍;legal 退化是"场景数值稀疏"导致,与 ShapeSpec 无关。
- 且上表是**单调老世界**测的;V11 非单调把极值顶进中段,检索更易漏中段那周 → MR 只会**更难、区分度更高**。
- (A) EventStream 做实留作 Tier 1 可选增强(见 §9),不进 Tier 0;(B) 降级否决。

## §9 路线图（Tier 1/2，后续）
- **Tier 1**:① 关系图骨架(C0/C1 拆分)→ multi-hop 跨实体题 + 顺带解锁逐实体生成上规模;② 难度旋钮面板(实体/周/草堆/形状/证据跨度/跳数 → easy/med/hard 预设 + 难度-准确率曲线);③ citation-span grounding(θ=0.85)+ DBSCAN 去重 + 新鲜虚构实体防污染(YourBench)。
- **Tier 2(论文护城河,北极星)**:把毕设 𝓕𝓔𝓠 算子 × M1-M6 失败模式诊断层接进生成器(每题打 target_failmode + 应触发系统类)→ 多真系统交叉验证预测命中率 → "架构诊断卡"。先做最小验证(接 M3 + 跑命中率),predicate 成立再放大。adaptive-search 反馈环(AutoBencher)在此之上。

## §10 任务分解（Tier 0 实现顺序）
- **T1**(纯代码,单测):`world_state.py` 加 `gt_event_order / gt_duration / gt_pre_expire / _shape_issues`;扩 world_state 自检。
- **T2**(纯代码,单测):`order_gen.py` 加 ORDER/DURATION/复合/decision-time 分支;扩 order_gen 自检。
- **T3**(LLM):Stage C `WORLD_SYSTEM` 非单调约束;`stage_d0_filler_palette` + filler 双黑名单。
- **T4**(LLM):Stage E 新能力句式 + 决策时点 framing。
- **T5**(代码+LLM):`_grade_ordering`(Kendall-τ)+ 接进 `_judge_grade`;Stage B 给新能力开配额槽。
- 验证:小场景跑一遍,看新能力题样例 + 非单调是否让 MR 极值入中段 + filler 是否领域匹配。
