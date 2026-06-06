# L4_preference 设计(剩余三线第一条)—— 按"被坑 5 波后"的纪律重做

> **坐标**:`L4_preference / 偏好隐式 / 从散落行为反推稳定偏好(从不明说)`。**状态**:设计稿。
> **一句话**:给一串**散落的选择行为**,反推那个**从未明说**的稳定偏好——考【跨文档聚合】+【抗 recency】。
> ★本设计把 `pitfalls_4lines.md` 的账**前置成出生约束**(§0),不等盲审来教第 6 次。

---

## 0. 出生即带的纪律(直接抄 5 波血泪,违反就别建)
1. **gold 出厂即唯一人类口径**:shipped gold/aux 里**不准有任何会被消费者误读的内部表示**(0-based session、内部词表)。off-by-one 5 连发的根就是 TR gold 并存了裸 session,导出挑了它。→ 见 §5【消费者路径审查】,**建之前先做,不是建完才 grep**。
2. **完成判据 = 下一波独立盲审确认,不是自检绿**:每次自检都绿、下一波都复发。L4 的 self-test 是必要非充分。→ 见 §7【完成判据】。
3. **闸条上线前必过【真实数据 FP/覆盖】实测**:MARGIN/K_MIN 是**定标出来的、不是拍的**(否则又一个 roles_of/W5/INV-5)。
4. **无补丁**:不准关键词黑名单 / 中文子串猜 / 症状相关代理。结构性或原则性判定,且**反补丁不自评**(留给独立审)。
5. **type-critical 表面不交给 LLM**:疑问词/答案类型走 `field_kind`,phrase 受铁律④约束。
6. **源头做对、闸只兜底**:prepare/enumerate 只在良定义时产单,well_posed≈防回归。

---

## 1. 能力定义(和已建线不重叠在哪)
**偏好 = 实体多次重复选择里的【众数】,语料从不写"X 偏好 Y"——只能从散落的每次选择反推。**
- vs **KU(最新值)**:★签名——substrate 故意让 **众数 ≠ 最近一次**。答"最近一次"(recency)= 错。考"看整体"非"背最新"。
- vs **MR**(数值极值)/ **L3**(排序)/ **IE**(某周值):L4 是【类别众数 = 一贯倾向】,正交。

## 2. 世界基质(prepare —— 像 L2 加人员、L5 注矛盾,L4 注【选择流】)
对若干实体注入一个**周度复选字段**(字段名/选项池只从白皮书 category 取,域无关):每期都重选一次(非 mention-on-change 的演化,是【每期都表态】)。
- 选项集 `O`(≥2,**表面互不近似**,复用 world 的 `_base_name` 约束);
- 分布:偏好项 `pref` 占 `⌈ratio·N⌉`(ratio≈0.55–0.65),余打散;
- **★recency 陷阱**:`values[-1] ≠ pref`(强制众数≠最新);
- **偏好绝不另起"偏好"字段**——它只活在选择流分布里(从不明说);幂等。

## 3. feasible / enumerate / gt(命门2)
- **feasible**:存在实体复选字段满足 `N≥K_MIN ∧ 唯一众数 ∧ (n_pref−n_runner)≥MARGIN ∧ mode≠latest`。不满足→跳(不产 0 单)。
- **enumerate**:每个合格 (实体,字段) 产 1 单。`gt = mode(values)`(代码算,确定)。
- **gt(护城河)**:从世界复算 mode==烘焙(过校验闸)。

## 4. intent(题面)—— 疑问词走 kind、强制聚合、压 recency
```
q = interrogative(ans_kind)        # 类别值疑问词,不写死(坑#5)
s = f"综合【{ent}】这 {n_total} 期「{fld}」记录,它【一贯/整体】更倾向于哪一种?{q}
     ——看整体倾向、别只看最近一次;答案是最常出现的那一种。"
hide = [str(gt)]                    # 只藏"偏好结论",每期具体值【留在语料】(边B证据)
```

## 5. ★消费者路径审查(off-by-one 教训前置:建之前就逐字段问"会被读错吗")
| shipped 字段 | 人类消费者(评审/评分)读它会怎样 | 判 |
|---|---|---|
| `gt` = mode 的**类别值**(如「灰度发布」) | 直接当答案,干净 | ✓ 无歧义 |
| `aux.options` = 类别值列表 | 选项,类别 | ✓ |
| `aux.n_pref/n_total/n_runner` = **计数** | 计数,**非周号**(不会被格式化成"第N周") | ✓ |
| `aux.latest` = 最近一次的**类别值** | 类别,非周 | ✓ |
| `evidence_sessions` = 0-based session | ★唯一的裸索引——但它**不进 gold_answer、且语义是"证据周集合"非"答案周"**;脱敏会剥 aux/evidence | ✓ 但**强制约定**:L4 答案侧绝不出现 session/周号(L4 答的是类别,本就无"周答案",天然规避 TR 那个坑) |
> 结论:L4 的人类口径答案 = **一个类别值**,从设计上就没有"周/数值"可被误读。这正是吸取 TR-session 的教训——**L4 出生就不给消费者挑错的机会**。

## 6. 边A 良定义(well_posed)+ 边B 接地
- **边B**:众数值 `pref` 须在 **≥K_MIN 个不同 session** 的文档就近实体出现(散落证据齐,读者才可聚合);不足→弃。
- **边A**(每条都要真实数据定标后才保留,坑#3):
  - WP1 唯一众数(无并列冠军);
  - **WP2 领先够**(`n_pref−n_runner≥MARGIN`)= L4 的**边A∩边C 核心**:margin 太小读者无法稳定聚合=ill-posed、太大误杀真偏好。**MARGIN 必须拿真实选择流分布量"误杀率 vs 漏放率"定标**;
  - WP3 抗 recency(`mode≠latest`,否则可被 KU 答中→退化,drop/标 trivial);
  - WP4 样本足(`N≥K_MIN`);WP5 gold==复算(护城河兜底)。
  - **不设**"选项数/分布熵"等看着像、未实测的代理。

## 7. ★完成判据(这是这次重做的核心:L4 不靠自检"宣布完成")
L4 的 DoD 分三关,**缺一不算完**:
1. **自检绿**(必要非充分):离线 well-posed/ill-posed 用例 + 校验闸 + 真实 world 抽验 MARGIN/K_MIN 定标(像 L2/L3 那样跑 `output/runs/*/02_world.json`)。
2. **真实产物抽验**(防"自检覆盖不到真实消费者路径"):一个真实 run 出 L4 题后,**手动 grep `06_grounded_questions.json` 和评审脱敏后的 `reviewed_qa.json`**,确认:gold_answer 是干净类别值、题面强制聚合、无 recency 可答。(off-by-one 就是直到 020444 才去 grep reviewed_qa 才看见——L4 这步**前置到第一轮**。)
3. **★独立盲审确认**(真正的完成线):一波盲审里 L4 题被验:① 不被 recency 答中(背最新=错);② reader 真能从散落证据聚合出偏好;③ 类型/疑问词对。**没过这关 = 没建完**,哪怕自检全绿。

## 8. 自检用例(necessary-not-sufficient)
- WP 过:N=8,A×5/B×2/C×1,latest=C → mode=A 唯一、领先 3、A≠C、A 在 5 周语料 → well_posed,gold=A。
- IP:并列(A4/B4)→WP1;领先不足(A4/B3,MARGIN=2)→WP2;退化(A6/B2 但 latest=A)→WP3;样本不足(N=3)→WP4。
- ★fixture 用**真实 schema 词表**(category/numeric…),别手搓错词(坑#6 假绿)。

## 9. 落地顺序(v0)
1. `pipeline/lines/L4_preference.py`(六件套 + well_posed),注册进 `LINES`(taxonomy L4 从 PLANNED 升 LINES);
2. 自检 + 注册表不变量;
3. **真实 world 抽验定标 MARGIN/K_MIN**(过实测才算闸条);
4. 端到端跑一轮 → **执行 §7 三关**,过独立盲审才宣布 L4 v0 完成。
