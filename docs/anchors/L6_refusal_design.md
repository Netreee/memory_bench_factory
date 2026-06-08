# L6_refusal 设计(剩余三线第二条)—— 抗虚构拒答,按"被坑 5 波后"的纪律生

> **坐标**:`L6_refusal / 拒答边界 / 知道记忆边界,不存在则拒答`。**状态**:设计稿(主理人拍板"全谱系含诱饵")。
> **一句话**:gold = 拒答,但语料里**故意摆着一个像样的诱饵**(兄弟实体的值 / 旧周的值 / 假前提)——考记忆系统最致命的失败:**宁可说"不知道",也别瞎编**。
> ★把 `pitfalls_4lines.md` 的账**前置成出生约束**(§0)。L6 有两处别的线没有的新机理,见 §6(★接地是反的)。

---

## 0. 出生即带的纪律(抄 5 波血泪,逐条说清 L6 会在哪复发)
1. **gold 出厂即唯一人类口径**:L6 答案是【拒答】,必须走 `ANSWER_PROTOCOL` 的拒答口径(`无此项/查无此记录`、`已停止统计`),**不准 ship 内部 `refusal_type` 当答案**。窗外题的 `at_week` 进题面**必须 1-based(`week_label`)**——★off-by-one 最可能在这里复发(L6 也用周号)。
2. **完成判据 = 下一波独立盲审,不是自检绿**。L6 盲审的命门问法见 §7:**reader 是否同意"确实答不出"**(而非"审稿人替系统找到了那个本该被拒的答案")。
3. **闸条上线前必过真实数据 FP/覆盖**:L6 的头号毒 = **假拒答**(题面其实有合法接地答案,gold 却标拒答)。MARGIN 不是拍的——见 §6 必须实测"假拒答率"。
4. **无补丁**:拒答条件**必须结构性**(字段在实体上全程无值 / `at_week>n_sessions` / EXPIRE 后无 resume op),**绝不准**"题面含'预算'且场景无预算→拒答"这种关键词/子串猜(那正是 audit★1 同款补丁)。
5. **type-critical 表面不交给 LLM**:题面**长得跟普通题一模一样**(这就是诱饵),疑问词仍走 `ans_kind`;拒答这件事**不准在题面泄漏**(不准"如适用则…")。
6. **源头做对、闸只兜底**:enumerate 只在"确认无合法答案"时产拒答单,well_posed≈防回归。

---

## 1. 能力定义(和 L1.ABS/FORGET 不重叠在哪——否则就是换皮)
**L6 = 抗诱答拒答。签名 = 语料里有一个【真实存在的诱饵】把系统往错答上拽。**
- vs **ABS**(从未涉及→无此项):ABS 是"干净的没有"(冰箱里真没牛奶);**L6 是冰箱里摆着一瓶豆奶**,问"牛奶过期没"——忍住别把豆奶当牛奶。
- vs **FORGET**(已停统):FORGET 是干净的停;L6 的假前提型("恢复后的值")在 FORGET 之上**再加一个假设**,考"不接受虚假前提"。
- **本线唯一价值在"诱饵"**:没有诱饵的拒答 = ABS,不做。

**拒答三型(全部 code-falsifiable):**
| 型 | 题面 | 诱饵(语料里真实存在) | 结构性命门(gt 可复算) |
|---|---|---|---|
| **T1 张冠李戴** | "A部门的负责人是谁" | A **无**该字段,但兄弟部门 B **有且显眼**(李四) | A 全程无该字段值 ∧ ∃ 异实体 B 有该字段 |
| **T2 时间窗外** | "第 {n+2} 周 A 的 P0 是多少"(语料只到第 n 周) | A 在窗内**真有** P0(更诱人去外推) | `at_week > n_sessions`(1-based) ∧ 字段窗内存在 |
| **T3 假前提** | "X 恢复统计后的首个值" | X 停统前的值在语料里(诱去答停前值) | ∃ EXPIRE/DELETE op ∧ 其后**无** SET/UPDATE |

## 2. 世界基质(prepare —— L6 几乎不造新世界,只"挑机会")
L6 **不引入新世界字段**(这是优点:不碰 world-gen = 不增多样性/塌缩风险)。prepare 极轻甚至为空:
- T1:扫现有世界,找"实体 E 缺字段 F、但兄弟 S 有 F"的对(F、S 都现成);
- T2:用现成 `n_sessions`;T3:用现成 EXPIRE 字段。
- **唯一硬约束(吸取坑#7"近重名陷阱反噬良定义")**:T1 的诱饵实体 S **必须与 E 表面可区分**(复用 world 的 surface-distinct + `_strip_disambig`)。若 S 与 E 近重名 → reader 也分不清 → 这题**真的歧义**(坏题),well_posed 必须 drop(见 §6 WP1)。

## 3. feasible / enumerate / gt(命门2)
- **feasible**:世界里 ∃ T1 对 ∨ ∃ T3 字段(T2 恒可行)。不满足→跳,不产 0 单。
- **enumerate**:每个机会产 1 拒答单。`gt = {"refusal": True, "type": "T1|T2|T3", "probe": {entity, field, at_week?}, "distractor": {entity, field, value}}`。
- **gt(护城河,代码复算)**:
  - T1:断言 `probe.entity` 全程 `value_at_session` 皆 INVALID/缺,且 `distractor.entity` 该字段有值;
  - T2:断言 `at_week-1 ≥ n_sessions`(裸 session 越界);
  - T3:断言 EXPIRE 后无 SET/UPDATE。
  - `gt()` 重算 == 烘焙 → 过校验闸。

## 4. intent(题面)—— 长得像普通题,这就是诱饵
```
# 疑问词照常走 ans_kind(person→是谁 / numeric→是多少),让题面与普通题无法区分
T1: f"{probe.entity} 的「{field}」{interrogative(ans_kind)}"
T2: f"第 {week_label(at_week_session)} 周,{probe.entity} 的「{field}」{interrogative(ans_kind)}"  # ★1-based!
T3: f"{probe.entity} 的「{field}」恢复统计后的首个值{interrogative(ans_kind)}"
hide = []   # 拒答题不藏证据:诱饵【要】在语料里;藏的是"它不可答"这个事实(本就不写题面)
```
gold_answer = 拒答口径(`ANSWER_PROTOCOL.gold_sentinel_map`),**eval 侧按"拒答类"匹配,不要求精确串**(见 §5)。

## 5. ★消费者路径审查(off-by-one 教训前置:逐字段问"会被读错吗")
| shipped 字段 | 人类消费者(评审/评分)读它会怎样 | 判 |
|---|---|---|
| `gt.refusal=True` → 映射 `无此项/查无此记录` | 拒答类答案,人类口径 | ✓ |
| `gt.type`(T1/T2/T3) | ★**内部标签,绝不当答案 ship**;只供诊断/分桶 | ✓ 强制不进 gold_answer |
| `gt.probe.at_week`(T2) | ★**进题面前过 `week_label` → 1-based**;否则 off-by-one 第 6 次复发 | ⚠ 必须 1-based,§8 有专测 |
| `gt.distractor.{entity,value}` | 渲染诱饵用,**非答案**;grader 若误读=把诱饵当 gold | ⚠ eval 契约:L6 题 gold 是"拒答类",不是 distractor 值 |
| `evidence_sessions` | 0-based 内部索引,脱敏剥离;L6 答案侧无周号(拒答无"周答案") | ✓ |
> 结论:L6 人类口径答案 = **一个拒答短语**。最大消费者风险**不在 gold 自身歧义,而在 eval 是否会把"拒答"判错**——所以 §7 第 2 关专验 grader 接受拒答类。

## 6. 边A 良定义 + ★边B 接地(L6 这两处和别的线都不同)
**★边B 接地是【反的】**(本线最特殊处):普通线要"gold 值在语料";L6 要——
- **诱饵 must 在**:`distractor.value` 须就近 `distractor.entity` 出现(诱饵真实,否则诱不动、退化成 ABS);
- **真答案 must 不在**:`probe.entity` 的 `probe.field` 在语料里**全程缺席**(否则就有合法答案 = 假拒答 = 毒)。
- → grounding 加一个 **absence-grounding 模式**:既验在场(诱饵)、又验缺席(答案)。

**边A 良定义(well_posed,每条实测定标后才留——坑#3):**
- **WP1(T1 不可混淆)**:`probe.entity` 全程确无该字段值 ∧ 诱饵实体 **表面可区分**(非近重名)。★这是 L6 边A∩边C 核心:诱饵太像 E = 真歧义坏题(坑#7),必 drop。
- **WP2(T2 真窗外)**:`at_week > n_sessions`(1-based 一致)∧ 该字段窗内确实存在(否则退化成 ABS,不算"窗外")。
- **WP3(T3 前提假)**:EXPIRE 后确无 resume(若真恢复过 → 前提成立 → 有答案 → drop)。
- **★头号毒:假拒答**。上线前必在真实 world 上实测:enumerate 出的拒答单里,**有多少其实存在合法接地答案**(FP)。FP 不达标 → 这型不上线(像 W5/roles_of 一样毙掉),不靠"看着对"。
- **不设**"题面长度/诱饵数"等看着像、未实测的代理。

## 7. ★完成判据(三关,缺一不算完)
1. **自检绿**(必要非充分):WP/IP 离线用例 + 校验闸 + 真实 world 抽验"假拒答率"定标。
2. **真实产物抽验**(防自检覆盖不到真实消费者路径):真实 run 出 L6 题后,手 grep `06_grounded_questions.json` + `reviewed_qa.json`,确认:① gold 是拒答类、② 诱饵真渲进语料且表面可区分、③ **无一题其实有合法答案**、④ T2 周号 1-based、⑤ **grader 把"不知道/无此项"判为对**(不要求精确串)。
3. **★独立盲审确认**(真正完成线):L6 题被验 ① grounded reader **同意"确实答不出"**(不是审稿人替它找到了答案);② 诱饵够诱人但**可区分**(不是真歧义);③ 拒答类型对;④ eval 没把拒答误判。**没过这关 = 没建完**。

## 8. 自检用例(necessary-not-sufficient;fixture 用真实 schema 词表,防假绿坑#6)
- **WP-T1**:E1 有缺陷率、无负责人;E2 负责人=李四 → "E1负责人是谁" gold=拒答、distractor=E2.李四 → well_posed。
- **WP-T2**:n=7,问第 9 周 → 拒答(且该字段第1–7周真有值)。**专测:题面出现"第9周"而非"第8周"(1-based)**。
- **WP-T3**:X 在 s5 EXPIRE、其后无 op → "X恢复后首值" gold=拒答。
- **IP**(必须被 drop):① E1 其实**有**负责人 → WP1(非拒答);② 诱饵实体名 ≈ E1(近重名)→ WP1(真歧义);③ 问第 5 周(≤7)→ WP2(非窗外);④ X 在 s5 停、s6 又 SET → WP3(前提成立、有答案)。
- ★fixture 的字段/值用真实 schema 词表,别手搓——`ans_kind` 假绿的教训(`number` vs `numeric`)记着。

## 9. 落地顺序(v0)
1. `pipeline/lines/L6_refusal.py`(六件套 + well_posed + **absence-grounding**),注册进 `LINES`(taxonomy L6 从 PLANNED 升);
2. grounding 支持"验缺席"模式(§6 边B);`ANSWER_PROTOCOL` 拒答口径复用(已 v2,无需改);
3. 自检 + 注册表不变量;
4. **真实 world 抽验"假拒答率"定标**(过实测才算闸条);
5. 端到端跑 → 执行 §7 三关,过独立盲审才宣布 L6 v0 完成。
