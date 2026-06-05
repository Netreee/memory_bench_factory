# Redesign V7 — 把 structure-first 贯彻到出题端

> 依据:对 V6 产物的两轮独立多 agent 审查(能力有效性 / 场景化 / 设计合理性 / 战略定位
> + 代码 / 数据质量)。四份宏观审查高度收敛:**方法 novel 是真的、structure-first corpus 扎实、
> 场景化真有差异,但生成端有几处让 benchmark 失真的硬伤。** V7 在扎实骨架上增强,不推倒。

---

## §1 病根:structure-first 只做了一半

V6 的 Stage C 写了一本**可靠的设定集**(skeleton:每 period 每字段的值 = ground truth,
审查 C 实测逐字进文档、无 drift)。但 Stage D 出题时:

- **M3 题**照设定集确定性派生 → gt 可靠 ✅
- **其它题(尤其 MR 聚合)**让 LLM 看着"成片"现场出题+**自己算答案** → 算错(4-5 道 MR 真值错)、
  把"周期0:Oncall=10,最高"这种**草稿过程**塞进答案(元信息泄漏)❌

**明明手里有标准答案的设定集,出一半题却让 LLM 凭印象答。这是 V7 要修的头号病根。**

## §2 核心转变:出题也 structure-first(答案代码派生)

非 M3 题也**从 skeleton 的 state 程序化派生题+答案**,LLM 只把题面润色成自然问句:

| 能力 | 程序化派生规则(答案由代码算,gt 保证正确) |
|---|---|
| IE | 取某 period 某字段值:`answer = state[p][f]` |
| KU | 取最新值:`answer = state[last][f]`(evolving 字段) |
| TR | 从 state diff / events 找字段变化的 period:`answer = 变点 period/date` |
| MR | 跨 period 聚合用**代码**算 max/min/argmax/sum/趋势 ← 直接修掉"LLM 算错聚合" |
| ABS | 取不在 fields 里的字段 → 拒答 sentinel |
| M3 | 保留 V6 信号竞争确定性派生(已可靠) |

失败模式(M1/M2/M4/M5)= **预测标签**,作为程序化题的"陷阱变体"叠加(M4 加同义改写答案集、
M5 加格式变体、M1 包装伪多跳题面、M2 配合多文档密度),但**答案始终由代码定**。

收益:gt 100% 正确 ✅ 无元信息泄漏 ✅ per-period schema 受控 ✅ "哪些题真考记忆"可控 ✅

## §3 改动清单(按性价比)

1. **★ Stage D V7 程序化出题(核心)**:`stage_d_v7_question_synthesis.py`。各能力从 state 代码派生
   题+答案;LLM 仅润色题面(可关)。一招修:MR gt 算错 / 元信息泄漏 / per-period 误配。
2. **Stage C V7 多文档密度**:每 period 生成多篇异质文档(周报+邮件+纪要),信息**拆散**到多文档;
   配合出题让答案必须跨文档/跨 period 聚合 → 消"~40% 伪记忆题"(单 doc 可答)。
3. **Stage C V7 种子扰动**:skeleton 生成不照搬 few-shot 具体值(人名/数值轨迹加扰动)→ 破"种子黏滞",
   让多 chain 真独立(不再是同一条 张三→李四 换措辞)。
4. **次要**:Stage B 配额语义(cell_quota = benchmark 总预算,在 n_chains 间**分配**而非复制;
   修 `quota_vs_actual` 的假 ×n delta);算子轴(F/E/Q)从"事后改写凑数"降级为 **observed 统计**,
   不再冒充配额轴。

## §4 保留(审查认证的真资产,不动)

- structure-first corpus(skeleton → 文档,无 drift)— 最硬工程资产
- cell_quota 反向配额驱动(失败模式分类学当配额轴 × 多场景)— 真 novel 的方法内核
- 场景化(schema 推断 + profile 化配额让不同场景考不同 memory 行为)— 第二支柱
- M3 信号竞争确定性派生 + failmode_evidence 审计痕迹 — 可控性硬资产

## §5 不在 V7 范围(留给后续)

- **W2 validity 三连**(cell 区分度 / 多 seed ranking 稳定 / 与真实 bench Spearman 相关)
  — 战略上是"决定生死"的下一大步,但用户定:V7 先把生成端做干净,validity 不急。
- 长程(LRU)/ 冲突消歧 / TTL 等高级 memory 能力扩展。
- profile 从 5 个硬编码模板 → 可泛化(野生领域 fallback 验证)。

## §6 任务分解

- T14 Stage D V7 程序化出题(核心)
- T15 Stage C V7 多文档密度 + 种子扰动
- T16 Stage B 配额语义 + 算子轴诚实化
- T17 run_pipeline_v7 端到端 + 全貌验证(gt 正确 / 无伪记忆 / 无元信息泄漏)
