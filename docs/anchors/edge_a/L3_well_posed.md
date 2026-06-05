# 边 A 闸 · L3_process 良定义 `well_posed()` —— 设计

> **定位**:验证三角(§V)的 **(A) 题面↔gold 良定义边**,L3_process 产线专属。接地闸(§G / `grounding.py`,(B) 边)治"值在不在语料"已到 86%;run190554 盲审证明剩余错误 100% 落在 (A) 边,L3 是重灾区且**根上是 `gt()` 算错 gold**。本闸 = 接地闸的对偶:纯代码、确定性、零 LLM、**绝不碰 corpus**,只用 `(order 意图 + gt + 世界 ws)` 判 gold 是否为题面在世界里的**唯一、合法、可复算**解。
>
> **契约**(与 `ground()` 对称,4 线统一):
> `well_posed(self, order: dict, ws: WorldState) -> tuple[str, str]` → `("well_posed","")` 或 `("drop", reason)`。
> 跑位:orders→questions 这一支,phrase 之前;drop 的题在出题前剔除。

---

## 1. L3"良定义"一句话定义

> 一道 L3 排序题良定义 ⟺ 它呈现的【每个事件】都是该实体该字段在世界里**真实发生、且 `(field,value)` 在该字段变更序里恰出现一次(唯一可定位)**的一次变更;并且 gold 给出的先后序 = 用世界 `(date,session)` 重算出的**唯一**全序(无同周平手导致的多解),长度 ≥ `MIN_EVENTS`。

> **★措辞订正(2026-06-05 实测)**:原文写"唯一**首现**周"暗示"锚到第一次出现即可消歧"——**这是错的心智模型**。现 `intent` 题面是裸的「{field}变为{value}」(无"首次"字样),若该值复现,解题者在语料里看见 ≥2 个落点,**锚 gold 内部到首现 session 并不能消除题面歧义**。良定义的真要求是该值在世界**恰出现一次**(唯一可定位),而非"取首现"。详见 §6.1①。

形式化:设题面事件集 \(E=\{e_i\}\),每个 \(e_i=(\text{field}_i,\text{value}_i,\text{op}_i)\)。令世界真值事件链
\(\mathcal{W}=\texttt{gt\_event\_order}(ws,\text{entity})\)(已按 \((date,session)\) 升序)。良定义要求存在**唯一**单射 \(\phi: E\to\mathcal{W}\) 使得:
① 每个 \(e_i\) 匹配到 \(\mathcal{W}\) 中**恰好一个**事件(存在性 I1 + 唯一可定位 I2:`(field,value)` 基数 =1);
② gold 序 = \(\{\phi(e_i)\}\) 按 \((date,session)\) 的序(方向一致);
③ 无两个被选事件落在**同一** \((date,session)\)(否则 \(\phi\) 给不出唯一全序)。
任一条不满足 ⇒ ill-posed ⇒ drop。

---

## 2. 不变量清单(每条 = 一个可证伪检查)

约定:`E = order["gt"]`(已是有序 list);`W = gt_event_order(ws, entity)`;`real = {(field, _norm(value), op): [sessions...]}` 由 `W` 按 `(field,_norm(value),op)` 分桶得到的真值事件索引(用 `_norm` 与判分同口径)。停用事件(EXPIRE/DELETE)的 value 恒为 None,用 op 本身作身份的一部分,不靠 value。

| # | 不变量(查什么) | drop 条件 | reason 文案 |
|---|---|---|---|
| **I0** | 序列长度足够 | `len(E) < MIN_EVENTS` | `"序列过短(n={n}<{MIN_EVENTS}),无序可排"` |
| **I1 · 存在性** | E 每个事件 \(e\),其 `(field,_norm(value),op)` 必须在 `real` 中出现(即世界里真有这次变更) | 任一 `e` 的键不在 `real` | `"事件「{field}={value}」(op={op})世界无此变更(悬空值/物理不存在)"` |
| **I2 · 唯一可定位值** ✅实测背书 | E 每个**非停用**事件 \(e\),其 `(field,_norm(value))` 在该字段的**变更序**里只对应**一个** change-session(题面「{field}变为{value}」在语料里唯一可定位) | `len(real[(field,_norm(value),UPDATE)]) > 1`(同字段同值变了多次,题面无法消歧指代哪一次) | `"事件「{field}={value}」无唯一可定位周(出现于周{weeks},指代歧义)"` |
| **I3 · 方向/顺序一致** | 把 E 按各事件**真实** `(date,session)` 重排,得 `E_true`;比对 gold 顺序 | `[id(e)…顺序] != E_true 顺序`(gold 序 ≠ 世界真序) | `"gold 顺序与世界真序不一致(时序倒置)"` |
| **I4 · 无平手(唯一性)** | E 中被选事件的 `(date,session)` 两两不同 | 存在两事件 `(date,session)` 相等 | `"事件「{a}」与「{b}」同周({date}),全序不唯一 → 非良定义"` |
| **I5 · 停用位置自洽** | 对每个停用事件(EXPIRE/DELETE),其在 gold 中的位次 = 它真实 session 在 E 中的位次(I3 的特例显式化) | 停用事件的 gold 位次 ≠ 其真实时序位次 | `"停用事件「{field}」错位(gold 第{i}位,真实时序第{j}位)"` |
| **I6 · 可复算护城河(可选,与既有校验闸合流)** | `line.gt(ws, order) == order["gt"]` | 不相等 | `"gt() 重算 ≠ 烘焙序(护城河漏:选择集编码不稳)"` |

> **★I2 实测背书(2026-06-05,真实世界对抗 QA;与 L2 对 `roles_of` 同口径量化)**:
> I2 是本闸**唯一在现线真实可达、且非症状代理**的核心收紧——与 L2 撤下的 `roles_of` 形成正反对照。两个真实 run 实测:
> - **真覆盖(高,远超 I3)**:run133356(L3 38 单)**21/38=55%**、run190554(L3 14 单)**5/14=36%** 因含复现值事件被 I2 drop;**同批 I3(gold序==世界真序)drop = 0/38 与 0/14**——I3 看不到这族病(烘焙序与所选 session 自洽,歧义在「题面文本→世界」的映射,不在 gold 内部序),故 **I2 对 I3 的增量 = 全部命中数,非零增量**。
> - **误杀率 = 0%**:逐题复核(`probe_l3_wp.py`),**全部 17/9 个存活者其每个 `(field,value)` 在世界确唯一可定位**(无逻辑矛盾);**全部 21/5 个被砍者确含 ≥1 个 `(field,value)` 复现事件**(如 P0缺陷率=8 出现于 s1 与 s10)。现 L3 题面是裸的「「{field}」变为 {value}」(`L3_process.intent`,实测 0/38 含"首次/第一次"字样),复现值在语料里有 ≥2 个落点 → 解题者**无从判定指代哪次** → 两种读法给出不同全序 → 真 ill-posed。
> - **"排首现 vs 排复现"子型实测不存在**:现 `intent` 从不问"首次达到 X 的先后",只呈现裸事件卡片,故无"复现不影响首现序"的合法子型可被误杀;I2 **一刀切是对的**(若未来 `intent` 引入"首次"措辞,需对该子型放宽——见 §4)。
> - **判据结论**:I2 过了 L2 逼出的硬关——【查世界+不含字面量】(必要)+【真覆盖 55%/36% 且 I3 零覆盖】+【误杀 0%】(充分)。**保留,强背书。** 同时强烈建议 `enumerate` 源头修复(见 §5 ★),把 I2 从"出题前 drop 55%"降为"几乎不触发"——实测源头改后 L3 良定义产量 ≈ 41 单(≥现 38,因改选唯一值事件后这些实体重新可产),**净增正向**。

> **I5 的关键澄清(踩坑点,见 §4)**:盲审把 Q38 概括成"停用必须排末位"。**这是错的归纳**——实测 EXPIRE 完全可以合法发生在中游(某字段 s3 停统计,而另一字段 s4 才变)。正确的通用不变量是 **I3(gold 序 = 世界真序)**;I5 只是把"停用事件的位置也必须等于其真实时序位置"显式拎出来当一条可读检查(因为停用题最容易被 `gt()` 错排)。**不写死"末位"**——写死末位本身就是特判,会误杀合法的中游停用题。Q38 真正的病是"停用排在第 1 位但真实是最末",由 I3/I5 一并 drop;而"该部根本无 SLA 停用事件"由 I1 drop。

---

## 3. 算法(伪代码)

```python
from pipeline.world_state import gt_event_order, _norm
from pipeline.world_state import EXPIRE, DELETE  # 停用标记

MIN_EVENTS = 3  # 与 L3_process.MIN_EVENTS 单一真源,import 之,勿复制字面量

def well_posed(self, order: dict, ws: WorldState) -> tuple[str, str]:
    ent = order.get("entity", "")
    E   = order.get("gt") or []                       # 已是“有序事件 list”

    # ── I0 长度 ───────────────────────────────────────────────
    if len(E) < MIN_EVENTS:
        return ("drop", f"序列过短(n={len(E)}<{MIN_EVENTS}),无序可排")

    # ── 从世界重建真值事件链(唯一真相源)────────────────────
    W = gt_event_order(ws, ent)                        # 已按 (date,session) 升序
    if not W:
        return ("drop", f"实体「{ent}」世界里无任何变更事件")

    # real:按 (field, _norm(value), op) 索引到【真实 change-session 列表】
    #   - 变更事件 value=具体值;停用事件 value=None → 身份 = (field, "", op)
    #   - 一个键对应多 session ⇒ 该 (字段,值,op) 在世界里发生过多次(I2 用)
    real = {}
    pos_in_W = {}                                      # (field,_norm(value),op,session) -> W 中的真序下标
    for idx, w in enumerate(W):
        k = (w["field"], _norm(w["value"]), w["op"])
        real.setdefault(k, []).append(w["session"])
        pos_in_W[(*k, w["session"])] = idx

    # ── 为 E 每个事件解析它对应的【唯一】真实 session ──────────
    resolved = []                                      # [(e, true_session, true_idx)]
    for e in E:
        is_stop = e.get("op") in (EXPIRE, DELETE) or e.get("value") in (None, "")
        op = e.get("op") or (EXPIRE if is_stop else "UPDATE")
        k  = (e.get("field"), "" if is_stop else _norm(e.get("value")), op)

        sessions = real.get(k)
        # I1 存在性:世界里压根没这次变更
        if not sessions:
            return ("drop", f"事件「{e.get('field')}={e.get('value')}」(op={op})"
                            f"世界无此变更(悬空值/物理不存在)")
        # I2 唯一可定位值:同 (字段,值) 变了多次 → 题面「变为该值」无唯一指代(停用天然唯一,仍统一判)
        #   ✅实测核心收紧:真覆盖 55%/36%、误杀 0%、I3 零覆盖(详见 §2 实测背书、§6 worked example)
        if len(set(sessions)) > 1:
            return ("drop", f"事件「{e.get('field')}={e.get('value')}」"
                            f"无唯一可定位周(出现于周 {sorted(set(sessions))},指代歧义)")
        s = sessions[0]
        resolved.append((e, s, pos_in_W[(*k, s)]))

    # ── I4 无平手:被选事件真实 (date,session) 两两不同 ─────────
    #   用 (date,session) 而非仅 session(防跨字段同周不同日的极端;date 取自 W)
    seen = {}
    for (e, s, idx) in resolved:
        w = W[idx]; key = (w["date"], w["session"])
        if key in seen:
            return ("drop", f"事件「{e.get('field')}」与「{seen[key]}」同周({w['date']}),"
                            f"全序不唯一 → 非良定义")
        seen[key] = e.get("field")

    # ── I3 方向/顺序一致:gold 序 == 按真实 idx 升序 ───────────
    true_order = [e for (e, s, idx) in sorted(resolved, key=lambda r: r[2])]
    if [id(x) for x in true_order] != [id(e) for e in E]:
        return ("drop", "gold 顺序与世界真序不一致(时序倒置)")

    # ── I5 停用位置自洽(I3 的显式特例,便于诊断;不写死“末位”)──
    for gold_i, (e, s, idx) in enumerate(zip_pos(E, resolved)):
        if e.get("op") in (EXPIRE, DELETE) or e.get("value") in (None, ""):
            true_i = sorted(resolved, key=lambda r: r[2]).index_of(e)
            if gold_i != true_i:
                return ("drop", f"停用事件「{e.get('field')}」错位"
                                f"(gold 第{gold_i}位,真实时序第{true_i}位)")
    # (实现上 I5 在 I3 通过后恒成立;保留为独立断言以产出可读 reason)

    # ── I6 护城河(可选;与既有“gt()重算==烘焙”校验闸合流)─────
    if self.gt(ws, order) != E:
        return ("drop", "gt() 重算 ≠ 烘焙序(护城河漏:选择集编码不稳)")

    return ("well_posed", "")
```

> 注:伪代码里 `zip_pos`/`index_of` 仅示意"按 gold 下标与真序下标对齐";落地时 I3 通过即可直接由 `enumerate(E)` 与 `true_order` 同序保证 I5,无需重复扫。保留 I5 是为了在停用题被 drop 时给出**指向性**的 reason(诊断价值)。

**为什么靠 `gt_event_order` 重建而非信 `aux`**:与 `gt()`(护城河)同源——`aux.events` 里的 date/session 是 enumerate 烘焙的、可能与世界漂移;`gt_event_order(ws,ent)` 是从状态机 ops 现算的唯一真相。本闸**只信世界**,与 §G 的 `attributed` 只信语料、本闸只信 gt+世界,三边各守一源。

---

## 4. 边界处理

| 边界 | 处置 | 由谁 |
|---|---|---|
| **平手(同周多事件)** | drop(I4)。同 `(date,session)` 的两事件无客观先后,任何 gold 序都是"多个合法序之一" → 非唯一 → 非良定义。**现 `enumerate` 实测不产**:pass1(字段+周唯一)与 pass2(去重 session)**都按 session 去重**,故最终选中集天然无同周对(已实测确认)。所以 I4 = **纯防御/防回归**(未来若 enumerate 放宽去重、或手构 order、或复用旧 ORDER 分支才可达)。 | enumerate 天然不产 + I4 防回归 |
| **值不存在(Q42 类)** | drop(I1)。世界 `gt_event_order` 里无 `(field,值,op)` 键。**新 L3 线本不该产**——**实测确认**:fresh `enumerate` 在两个真实世界上产 0 个悬空值事件(`probe_l3_wp.py`)。故 I1 = 纯兜底"未来 enumerate 改动 / 上游污染 / 复用旧 ORDER"。 | enumerate 实测不产 + I1 兜底 |
| **停用错位(Q38 类)** | drop(I3,I5 给诊断 reason)。**不写死末位**;停用合法可在中游。错位 = gold 序 ≠ 真序。**实测确认**:fresh `enumerate` 产 0 个倒序事件;含停用的 L3 题(133356 仅 1 单、190554 仅 2 单)其停用位 = 真实时序位,**0 错位**(`probe_l3_wp.py`)→ I3/I5 在现线 0 触发,纯兜底。 | I3/I5(实测 0 触发,兜底) |
| **该实体无该停用事件(Q38 后半)** | drop(I1)。"SLA 停统计"但该字段无 EXPIRE/DELETE op → 键 `(SLA,"",EXPIRE)` 不在 real。 | I1 |
| **序列过短** | drop(I0)。`MIN_EVENTS`(=3)从 L3_process import,单一真源。 | I0 |
| **同值多次出现(复现 → 指代不唯一)** | drop(I2)。如值班数 8→9→7→**9**:值 9 有两个 change-week(s1,s3),题面"值班变为 9"无唯一指代 → ill-posed。**这是 Q42 同根的隐性变体**(Q42 是"值压根不存在",I2 治"值存在但指代不唯一")。停用事件天然单次,I2 对其恒过。**实测:这是现线唯一真高发病(55%/36%),误杀 0%**(§2 背书)。 | I2 |
| **同字段重复呈现,但各值唯一(实测新增,well-posed)** | **不 drop**。如 `报表产出数=25@s2、报表产出数=15@s4`(实测 商业分析部):同字段出现 2 次,但 25 只在 s2、15 只在 s4 → 每个 `(field,value)` 仍唯一可定位 → 解题者能排序 → **良定义**。I2 按 `(field,value)` 判、不按 field 判,**正确放行**;无需新检查。**只有当某个值还复现时**(如 商业智能部 SLA 出现 3 次且其中一值复现)才 drop,由 I2 一并接住。**结论:同字段重复 ≠ ill-posed,勿误加"字段去重"检查(那会是误杀好题的特判)。** | I2(按值判,自然放行) |
| **"首次达到 X"措辞(假想子型)** | 现 `intent` **不产**(实测 0/38 含"首次/第一次")。**若未来引入**"按首次达到各值的先后排序"措辞,则复现不再致歧义(问的是首达周,唯一),此时 I2 应对该子型**放宽**(锚到首个 change-week 即可)。**当前裸"变为 X"措辞下 I2 一刀切是对的。** | (前瞻;现不适用) |
| **EXPIRE value=None 的 key 匹配** | 身份 = `(field, "", op)`,**不**用 `_norm(None)` 与具体值混淆。实测 `gt()` 现用 `str(value)` 会把 None 变 `"None"`——本闸用 `op∈{EXPIRE,DELETE}` 显式判停用,比字符串 `"None"` 更稳(见 §gt 需先改对)。 | I1/I5 实现细节 |

---

## 5. 命中映射(盲审 L3 症状 ← 哪条不变量;哪些该在 gt() 修对)

| 盲审题 | 症状 | drop 它的不变量 | 根因层 / 该怎么修对 |
|---|---|---|---|
| **Q42** | 排"值班变为 5",但该部全程 8→9→7→10→9→7,**世界从无 5** | **I1(存在性)** | **gt/enumerate 层**:旧 L1 ORDER 分支无校验闸 → 产出悬空值。新 `L3_process` 已天然不产(`enumerate` 只从 `gt_event_order` 取真实事件)。**I1 = 兜底**,防旧分支/上游污染复发。 |
| **Q42 隐性变体(★现线唯一真高发病)** | 同字段同值变多次(8→9→7→**9**),"变为 9"指代不唯一 | **I2(唯一可定位值)** | **实测 55%/36% 命中、误杀 0%、I3 零覆盖**(§2 背书)。**enumerate 应源头改对**:点菜时**跳过"该字段该值有 >1 个 change-week"的事件**(只选唯一可定位值)。⚠️注意"在 gt 里锚定到首现 session"**不能**消歧(解题者看语料仍见两个落点),**唯一有效的源头修复是 enumerate 不选复现值**——实测改后产量 ≈41 单(净增,见 §5 ★修正)。 |
| **Q38(前半)** | "SLA 停统计"排**第 1 位**,真实停用在**第 9 周(最末)** | **I3(方向)+ I5(诊断)** | **gt 层应改对**:`gt()` 用 `gt_event_order` 重排本应得到正确序——说明产 Q38 的是**旧 L1 ORDER 分支**(L1 自己注明"ORDER 选择编码进答案 → 不入校验闸",见 `L1_timeline.py:279`)。迁到 L3 后 `gt()` 重排 + I3 双保险。 |
| **Q38(后半)** | 该部**根本无 SLA 停用事件** | **I1(存在性)** | 同上:旧分支凭空造停用事件;新线 `gt_event_order` 不会产、I1 兜底。 |

> **★头号发现(给你的"gt 需先改对"清单;2026-06-05 实测全部复核)**:
> 1. **Q42/Q38 实测确认源自已退役的 L1 ORDER 分支,现 `L3_process` 结构上不产**。证据:`L1_timeline.py:5` 注"ORDER 已拆给 L3_process";`:279` 注"ORDER 选择编码进答案 → 不入闸"——旧 ORDER **绕过了** `gt()重算==烘焙` 校验,才产悬空值/倒序。**实测背书**:对两个真实世界 fresh `enumerate`(`probe_l3_wp.py`),**悬空值 0、倒序 0、平手 0**;含停用的 L3 题停用位 0 错位。→ **I1/I3/I4/I5 在现线实测 0 触发,纯兜底/防回归**(防旧分支复用 / 上游污染 / 手构 order)。保留(廉价,且守住已知 bug 来源,与 L2 对结构不可达 case 同处置)。
> 2. **现线真实可达的 ill-posed 只有一族:同值复现 → 指代不唯一(I2)**。`enumerate`/`gt` **不区分唯一值 vs 复现值**,照样选取并烘焙 → 实测 **55%/36% 的 L3 order 含复现值事件**,well_posed/I2 是**唯一防线**。次要隐患:`gt()` 的 key 用 `(field,str(value),session)`——对**停用(value=None→字符串 "None")**会让不同字段的停用 key 碰撞(实测含停用题极少,暂未触发,但是真隐患)。
> 3. **`enumerate` 该源头改对的一点(根治 I2,实测净增产量)**:
>    - **唯一可定位值**:`enumerate_l3_orders` 点菜时,对"该字段该值有 >1 个 change-week"的事件**不予选取**(改选该实体其它唯一可定位的变更)。⚠️**关键修正**:原设计写的"或在 gt 里锚定到首现 session"**无效**——解题者看语料仍见复现值的两个落点,锚 gt 内部 session 不消除题面歧义。**唯一有效修复 = enumerate 不选复现值**。**实测**(`probe_l3_wp.py` 末段):源头改后仍有 **41/81 实体**能凑够 ≥3 个唯一可定位的跨字段事件 → L3 良定义产量 ≈41(≥现 baked 38,**净增**:因这些实体改选唯一值事件后重新可产);少数数值剧烈震荡的实体(如支付平台部,P0 在 13 周内每值都复现)凑不够 3 个唯一值 → 本就该 drop(确 ill-posed)。
>    - **停用身份**(前瞻,现未触发):`gt()` 的 key 宜把停用并入 `op` 维度(`(field, value_or_None, op)`),而非把 None 字符串化成 `"None"` 混入 value——否则两个不同字段的停用会 key 碰撞。

**分工总结(实测后)**:I1/I3/I4/I5/I6 在现线**实测 0 触发**(fresh enumerate 不产悬空值/倒序/平手;`gt()重算==烘焙` 恒成立),= **纯兜底 + 防回归**(旧 ORDER 分支复用 / 上游改动才复发)。**I2 是现线唯一真实可达的 ill-posed(同值复现,实测 55%/36% 命中、误杀 0%、对 I3 零覆盖增量),well_posed/I2 是唯一防线**——**强烈建议 `enumerate` 源头改对**(点菜跳过复现值,实测产量净增),把 I2 从"出题前 drop 一半"降为"几乎不触发"。

---

## 6. 反补丁自审(逐条论证"通用不变量,非特判")

> 铁律:每条检查必须能 drop **任何**违背它的题,而非只堵 Q42/Q38。

- **I0(长度)**:对任意实体任意事件集成立;阈值 = `MIN_EVENTS`(从产线 import,非魔数)。可证伪:任给一道 n<3 的排序题必被 drop。✔ 通用。
- **I1(存在性)**:量词是"∀ E 中事件 ∃ 世界对应变更",对任意值、任意实体、任意字段成立——不提 5、不提 SLA、不提任何具体值。Q42 只是"∃ 一个事件无世界对应"的一个实例。✔ 通用,非特判。
- **I2(唯一可定位值)**:对任意 `(field,value)` 检查其 change-week 是否唯一,与具体值无关。治的是"题面裸事件在语料里指代不唯一"这一**结构**,值班数 9、P0 2.5% 复现都一并 drop。✔ 通用,**且实测过了 L2 逼出的硬关**(见 §6.1 worked example:与 `roles_of` 正反对照)。
- **I3(方向)**:gold 序与世界重算序逐位比对,对任意排列成立。Q38 倒序只是"gold≠真序"的一个实例。**关键:没有写死任何位置规则**,纯比对。✔ 通用。
- **I4(无平手)**:对任意两被选事件检查 `(date,session)` 是否相等,与字段/值无关。治"多合法序"这一**唯一性结构**。✔ 通用。
- **I5(停用位置)**:是 I3 在停用事件上的**显式投影**,断言"停用的 gold 位 = 真实位",**不**断言"停用必在末位"(那才是特判,会误杀合法中游停用——§4 实测过)。✔ 通用,且刻意避开了盲审给的错误归纳。
- **I6(护城河)**:复用既有"gt()重算==烘焙"哲学,对任意 order 成立。✔ 通用。

**反例自检(证明无特判残留)**:把 Q42 的"5"换成任意世界没有的值、把 Q38 的"SLA"换成任意无停用的字段、把停用换到任意中游位置——I1/I2/I3/I4 仍按同一逻辑 drop 或放行,不依赖任何题号/字段名/字面值。

### 6.1 worked example —— L2 逼出的"硬关"在 L3 的两个落点(2026-06-05 实测)

> L2 §6 的教训:**【查世界 + 不含字面量】只是必要非充分**;还须实测【真覆盖(且非别的不变量已覆盖)+ 误杀率】。`roles_of` 形式像不变量,实测 **13% 误杀 / 0 真覆盖** → 撤下。对 L3 逐条做同样揪查,得两个对照落点:

**① I2 = 通过硬关的检查(与 `roles_of` 正反对照,**保留 + 强背书**)。**
- 形式:`(field,value)` 在世界 change 序里是否唯一——查世界、不含字面量。✔ 必要条件过。
- **真覆盖**:run133356 **21/38=55%**、run190554 **5/14=36%**;且**核心邻条 I3(gold序==世界真序)对这族零覆盖**(I3 drop=0)——I2 抓的是「题面文本→世界」映射的歧义,I3 只看 gold 内部序,看不到 → **I2 增量 = 全部命中,绝非"I3 已覆盖的零增量"**。
- **误杀率 = 0%**:逐题复核全部存活者其 `(field,value)` 确唯一、全部被砍者确含复现值;现 `intent` 裸"变为 X"措辞下复现值**确无法消歧**(语料有 ≥2 落点)→ 被砍的确是坏题。
- 与 `roles_of` 的关键差异:`roles_of` 量的是**连接度**(⊥ 歧义);I2 量的是**题面 token 在世界的指代基数**(=歧义本身)。前者是症状代理,后者是歧义的**定义**。→ **I2 是真不变量。**

**② "同字段去重"= 一个**看着像不变量、实测会误杀的诱惑检查**(**刻意不加**)。**
- 诱惑:盲审若看到 `报表产出数=25、报表产出数=15`(同字段出现 2 次)可能概括成"同一字段不该在一道排序题里出现多次"。
- **实测证伪**(商业分析部,run133356):`报表产出数=25@s2、=15@s4;数据准确率=92@s3、=88@s5`——同字段各出现 2 次,但 **25 只在 s2、15 只在 s4(各值唯一可定位)** → 解题者能无歧义排序 → **良定义好题**。"字段去重"检查会**误杀**它。
- 正解:歧义的真条件是**值复现**(`(field,value)` 基数 >1),**不是字段复现**。I2 按 `(field,value)` 判、天然放行"同字段+异唯一值",**无需也不应**加"字段去重"。这正是 L2 教训的同形复现——**把症状(字段重复)误当不变量,会砍好题;只有度量歧义本身(值指代基数)的检查才是不变量。**

---

## 7. 自检用例(well-posed vs ill-posed)+ 离线自检法

离线自检 = 造小 `WorldState`(同 `world_state.py` 的 `Timeline/Op` 直构),手算期望,断言 `well_posed` 返回。**零 LLM、零 corpus、可复跑**(模仿 `grounding._self_test`)。

| # | 世界(实体 E 的字段轨迹) | order.gt(呈现序) | 期望 | 命中 |
|---|---|---|---|---|
| **WP1 ✔well** | P0:2.5→1.8(s1);SLA:99→98(s3);Onc:8→9(s4) | [P0@s1, SLA@s3, Onc@s4] | `("well_posed","")` | 全过 |
| **WP2 ✔well(中游停用)** | P0:2.5→1.8(s1);SLA:99→停(EXPIRE s3);Onc:8→9(s4) | [P0@s1, SLA停@s3, Onc@s4] | `("well_posed","")` | I5 放行中游停用(**反"末位特判"**) |
| **WP3 ✔well(同字段重复+异唯一值,反"字段去重"误杀)** | 报表:20→25(s2)→15(s4);准确率:90→92(s3)→88(s5)(每值仅一次) | [报表=25@s2, 准确率=92@s3, 报表=15@s4, 准确率=88@s5] | `("well_posed","")` | 同字段出现 2 次但各值唯一可定位 → **必须放行**(实测 商业分析部;守住"勿加字段去重特判",见 §6.1②) |
| **IP1 ✗悬空值(Q42)** | Duty:8→9→7(无 5) | [含"Duty 变为 5"] | `drop / 世界无此变更` | I1 |
| **IP2 ✗倒序(Q38 前)** | P0:→2(s1);SLA停(s9);宕机:→40(s2) | [SLA停@s9 排首, P0@s1, 宕机@s2] | `drop / 时序倒置` | I3(+I5 诊断) |
| **IP3 ✗无该停用(Q38 后)** | SLA 全程只 SET/UPDATE,无 EXPIRE | [含"SLA 停统计"] | `drop / 世界无此变更` | I1 |
| **IP4 ✗平手** | A:→a1(s2);B:→b1(s2);C:→c1(s4) | [A@s2, B@s2, C@s4] | `drop / 同周…全序不唯一` | I4 |
| **IP5 ✗值复现指代不唯一(★现线唯一真高发)** | Duty:8→9(s1)→7(s2)→9(s3) | [含"Duty 变为 9"] | `drop / 无唯一可定位周` | I2 |

自检骨架(伪):
```python
def _self_test():
    from pipeline.world_state import WorldState, Timeline, Op, SET, UPDATE, EXPIRE
    line = ProcessLine()
    def W(field_ops):  # {field: [(s,op,val,prev),...]}
        return WorldState({"E": {f: Timeline([Op(s, _date(s), op, v, p)
                                              for (s,op,v,p) in steps])
                                 for f, steps in field_ops.items()}}, n_sessions=10)
    # WP2:中游停用必须放行(守住“不写死末位”)
    ws = W({"P0":[(0,SET,"2.5",None),(1,UPDATE,"1.8","2.5")],
            "SLA":[(0,SET,"99",None),(3,EXPIRE,None,"99")],
            "Onc":[(0,SET,"8",None),(4,UPDATE,"9","8")]})
    o  = line.enumerate(ws)[0]
    assert line.well_posed(o, ws)[0] == "well_posed"
    # IP4:人造平手,断言 drop
    ws2 = W({"A":[(0,SET,"a0",None),(2,UPDATE,"a1","a0")],
             "B":[(0,SET,"b0",None),(2,UPDATE,"b1","b0")],
             "C":[(0,SET,"c0",None),(4,UPDATE,"c1","c0")]})
    o2  = line.enumerate(ws2)[0]
    assert line.well_posed(o2, ws2)[0] == "drop"
    # WP3:同字段重复但各值唯一 → 必须【放行】(反"字段去重"误杀,守 §6.1②)
    ws3 = W({"报表":[(0,SET,"20",None),(2,UPDATE,"25","20"),(4,UPDATE,"15","25")],
             "准确率":[(0,SET,"90",None),(3,UPDATE,"92","90"),(5,UPDATE,"88","92")]})
    o3 = {"line":"L3_process","capability":"L3_order","entity":"E","field":"",
          "gt":[{"field":"报表","value":"25","session":2,"date":_date(2),"op":UPDATE},
                {"field":"准确率","value":"92","session":3,"date":_date(3),"op":UPDATE},
                {"field":"报表","value":"15","session":4,"date":_date(4),"op":UPDATE},
                {"field":"准确率","value":"88","session":5,"date":_date(5),"op":UPDATE}],
          "aux":{"events":[]}}  # events 留空让 gt() 走 o["gt"] 回退
    assert line.well_posed(o3, ws3)[0] == "well_posed"
    # IP1/IP5:手造病态 order(绕过 enumerate,直接塞悬空/复现值)再断言 drop
```
> 关键自检点:**WP2 必须放行中游停用**(防有人把 I5 退化成"停用必末位"的特判);**WP3 必须放行"同字段+异唯一值"**(防有人加"字段去重"特判误杀好题——实测真实存在,§6.1②);**IP5 复现值必须 drop**(证明 I2 真在工作,而非只堵 Q42 的字面"5")。

---

## 附:与既有架构的对称性(便于拼装)

- 签名 `well_posed(self, order, ws) -> (status, reason)` ↔ `ground(self, order, evidence_docs, all_sig) -> (status, reason)`,完全对偶。
- 编排落点:在 `orders → questions` 之间加一个 `run_well_posed(orders, ws)`,结构同 `run_grounding`(遍历 → 各线 `well_posed()` → 存活率报告 by_line/by_cap + drops 逐条 reason)。fail-closed:未实现 `well_posed` 的线默认 `("well_posed","")`(良定义闸是 opt-in 收紧,不像接地闸 fail-closed 弃——因为有些线 gt 天然唯一)。
- 真相源纪律:`ground` 只信 corpus、`well_posed` 只信 `gt+ws`、`gt` 只信 ws——三者互不串源,正交可证伪。
