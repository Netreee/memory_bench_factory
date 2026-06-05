# 边 A 闸 · L5_conflict 良定义 `well_posed()` 设计

> **定位**:验证三角的 **(A) 题面↔答案【良定义】边**(§V.3),L5_conflict 产线专属。
> **对称对象**:边 B 接地闸 `pipeline/grounding.py` + `L5_conflict.ground()`。本闸照其架构对称设计:同签名风格、纯代码确定性、stage 编排 + 线实现、fail-closed。
> **状态**:设计稿(只设计,不落地代码)。基于真实数据结构(`pipeline/lines/L5_conflict.py`、`pipeline/world_state.py`)。
> **实测背书(2026-06-05)**:I0–I7 已逐条用真实世界数据(run0605 15 单 + run190554 3 单 = **18 单** L5 矛盾)实测误杀率/真覆盖/I4 增量,与 L2 同口径对抗式 QA。结论 = **8 条全保留**:I0–I6 在真实数据上 0 误杀且各有独立必要性(逻辑非 I4 子集);**I7 是这轮唯一被实测验真的"症状嫌疑犯"——0% 误杀 / 11.1% 真覆盖(2/18)/ 与 I4 零重叠**,是真不变量(详见 §2 I7、§6 worked example)。与 L2 `roles_of` 被撤(13% 误杀 / 0 真覆盖)恰成镜像对照。
> **跑位**:`orders → questions` 这一支,在 `phrase`(调 `intent`)**之前**逐题过闸;`drop` 的题剔掉、弃因进报告。与接地闸(`questions+corpus` 之后)正交,职责不重叠。

---

## 0. 与接地闸的边界(为什么这是【另一条边】,不能被 §G 替代)

| 边 | 拿什么判 | L5 管什么 |
|---|---|---|
| **(B) 接地 §G**(`ground()`) | order + **corpus** | 权威值、小道值**两边都真渲进了语料**(渲染没漏写) |
| **(A) 良定义(本闸)** | order + **ws**(含 `ws.conflicts`),**绝不碰 corpus** | 这道矛盾题在**世界**里是否"有唯一正解 + 传闻是真干扰":真矛盾 / 唯一权威解 / 可靠度可分 / gold 锚定正确 / 传闻非等价真相 |

run0604 实证:接地把"值在不在语料"治到 86%,治不了"题本身好不好"。L5 现状(Q43/44/45 被 4 人评"优秀")恰是良定义的样板——本闸把这份良定义**显式化、可证伪化、防回归**:以后若有人改注入逻辑/规则/来源池把这些性质弄坏,闸会 `drop`,不会让坏题溜进题库。

---

## 1. L5 "良定义" 定义(一句话)

> 一道 L5 矛盾题是良定义的,当且仅当:在矛盾发生的那一时刻(session),**世界给出唯一、合法的权威值,它严格地由一个可靠度高于传闻来源的权威来源背书,且 gold 恰好锚定到这个权威值;而传闻值是一个与权威值真冲突、且在世界 canonical 里并不成立的干扰项。** ——读者只能靠"按来源可靠度甄别"得到唯一正解,传闻是真干扰而非等价真相。

把它拆成可证伪的坐标:`gold = arbitrate(rule, 矛盾@(entity,field,session))`。题面承诺的坐标 = `{rule: source_reliability, session}`(题面点明"按来源可靠度裁决")。闸只用 `(entity, field, session, rule)` 回世界 + `ws.conflicts` 重算,若得不到"唯一 = gold 的合法权威值"或"传闻不是真干扰",即 ill-posed。

---

## 2. 不变量清单(每条 = 一个可证伪检查)

记号:`c` = 从 `ws.conflicts` 按 `(entity, field, session)` 查回的本题矛盾条目;`aux` = `order["aux"]`;`auth = aux["authoritative_value"]`、`rumor = aux["rumor_value"]`、`auth_src/rumor_src` 同理;`s = aux["session"]`;`canon = ws.timeline(entity, field).value_at_session(s)`(世界在该 session 的 canonical 值)。所有值比较走 `_norm`(判分同口径)。

| # | 名称 | 查什么 | drop 条件 | reason 文案 |
|---|---|---|---|---|
| **I0** | 矛盾可溯源 | 能否在 `ws.conflicts` 按 `(entity,field,session)` 查回 `c` | 查不回 `c`(`None`) | `矛盾不可溯源:ws.conflicts 无 (ent,fld,session) 条目(order 与世界脱钩)` |
| **I1** | order/世界一致 | `aux` 的四值(auth/rumor 值与源)是否 `_norm` 等于 `c` 对应字段 | 任一不一致 | `order/世界不一致:aux 的{字段} 与 ws.conflicts 不符(出题旁路了世界真相)` |
| **I2** | 规则可裁(已知规则) | `c["rule"]` 是否在【已实现的可裁规则集】`{source_reliability}` | `rule` 不在集合内 | `未知裁决规则 '{rule}':闸无法证伪其良定义(fail-closed)` |
| **I3** | 真矛盾 | `_norm(auth) != _norm(rumor)` | 权威值 == 传闻值 | `假矛盾:权威值 与 传闻值 相等(无冲突,题无意义)` |
| **I4** | 唯一合法权威解 | `canon` 是否为单一、合法值(非 `INVALID`/`INSUFFICIENT`/空)且 `_norm(canon)==_norm(auth)`;且该 session 该字段在世界里只解出这一个值 | `canon` 非法(已停用/从未出现)**或** `canon != auth` | `权威解不唯一/不合法:session {s} 世界 canonical={canon!r},非唯一等于权威值 {auth!r}` |
| **I5** | gold 锚定正确 | `_norm(order["gt"]) == _norm(auth)` **且** `_norm(gt_重算) == _norm(auth)`(用 `ConflictLine.gt(ws, order)` 重算)| gold ≠ 权威值,或锚到了别的 session 的值 | `gold 锚错:gt={gt!r} ≠ 该 session 权威值 {auth!r}(可能锚到传闻或别周)` |
| **I6** | 可靠度严格可分 | 权威来源可靠度是否**严格高于**传闻来源:`reliability_rank(auth_src) > reliability_rank(rumor_src)`(两源分属互斥的权威集 / 传闻集) | 两源可靠度不可比 / 相当 / 倒挂(同档、都在权威集、都在传闻集、或权威源落进传闻集) | `可靠度不可分:权威源 '{auth_src}' 未严格高于传闻源 '{rumor_src}'(读者无从甄别)` |
| **I7** | 传闻非等价真相 〔实测:0% 误杀 / 11.1% 真覆盖(2/18)/ I4 零重叠〕 | 传闻值是否在世界 canonical 里**也成立**(= 该 (entity,field) 时间线任一 session 取过 `rumor`) | `rumor` 在 canonical 时间线里出现过(它是别周的真值,不是假干扰) | `传闻非干扰:传闻值 {rumor!r} 在 canonical 时间线确出现过(它是真值不是干扰,题不公平)` |

> **全过 → `("well_posed", "")`;任一 drop → `("drop", reason)`。** 顺序按 I0→I7,先溯源再判值,reason 取首个失败项。

### 关键不变量的设计理由

- **I4(唯一合法权威解)是良定义的核心**。它用世界 canonical(`value_at_session`)作裁判:权威值必须是世界在该时刻**唯一、有效**的真值。这同时挡掉三种坏题:① 权威值其实指向一个已 EXPIRE/DELETE 的字段(`INVALID`,世界已"忘了它")→ 无合法权威解;② 字段在该 session 从未出现(`INSUFFICIENT`)→ 无解;③ 权威值与 canonical 对不上(注入器选错了 session 的值)。这是"唯一正确解"的代码化。**实测**:18 真实矛盾的 canonical@矛盾周 == auth **全部成立**(I4 0 触发)——因 `inject_conflicts` 的权威值正是取自该 session 的 `set_values()`,I4 与注入器同源天然对齐;它的价值是**防回归**(若日后注入器选错 session、或矛盾挂到 EXPIRE 后的字段,I4 立刻翻红)。**逻辑独立性**:I3(auth==rumor)、I5(gt≠auth)都能在 I4 放行时单独触发 → I0/I1/I3/I5 **非 I4 子集**,无可并入项。
- **I6(可靠度可分)是把"优秀"性质显式化的那条**(见 §5 命中映射)。L5 的解法靠"官方 > 小道",所以两来源必须分属**严格可比的两档**。当前数据里可靠度无数值 tier,故用**集合归属 + 严格序**实现:`AUTHORITATIVE_SRC` 单独成"权威档",`RUMOR_SRCS` 同属"传闻档",权威档 > 传闻档。`reliability_rank()` 是唯一的可靠度真相源(见 §3),将来若引入多档来源,只改这张表,闸自动生效。**实测**:18 真实矛盾的来源池 = `{官方通报}`(全 auth)∪ `{内部群聊转述, 未经核实的外部传闻, 走廊里的口耳相传}`(全 rumor),**全部登记在 `_RELIABILITY_TIERS` 且严格可分**(I6 0 误杀)。"未登记来源 fail-closed drop" 在真实数据上**未误伤任何注入来源**(注入器只用这 4 个源,与 §3 表完全对齐);该分支是给"日后新增来源忘登档位"留的保险,非当下负担。
- **I7(传闻非等价真相)挡"假干扰"**。L5 的小道值取自"同字段全局值池里 ≠ 权威值的另一真实取值"(张冠李戴)。这在**跨实体**取值时是好干扰(别的部门的负责人,本部门 canonical 从没出现过);但若注入器恰好选了**本 (entity,field) 自己别周**的真值当传闻(如负责人 s0=李娜、s3=王强,s3 矛盾却把"李娜"当传闻),那"传闻"其实是这个实体的合法历史值——读者按时效也能争辩它对,矛盾退化成 L1.CONFLICT(合法演化),不再是"官方 vs 谣言"。I7 把这种退化挡在门外。
  - **★实测验真(0% 误杀 / 11.1% 真覆盖 / 与 I4 零重叠)**:18 真实矛盾里 I7 命中 **2**(`基础安全组.安全评级`@s4:auth=A、rumor=B,而 B 是本组 s0–3 的**过去**真值;`支付清算部.负责人`@s3:auth=钱七、rumor=孙八,而孙八是本部 s6–8 的**未来**真值)。两条传闻值都**只出现在本实体本字段的时间线上**(全世界别处没有),坐实"本实体别周真值"而非跨实体张冠李戴。这正是退化的真发生:传闻值是 corpus 里**真存在、真属于本实体**的事实,会"检索两边"的好系统会读成合法时效演化(L1.CONFLICT/KU)并按 recency 给出一个**与世界相符**的竞争答案,却被按 source_reliability 判错 → gold 不再是唯一可辩护解。I7 的 drop 是**真覆盖**,且 I4(只验 canonical@矛盾周==auth)对这两条**全放行**(canon 恰好==auth),零重叠。
  - **为何不是误杀(关键)**:I7 仅在传闻值**逐字落在本实体本字段 canonical 时间线**时触发(`_appears_in_canonical`)——这正好是退化的充要现象,不是连接度/频次一类的相关性代理。低基数 category 字段(如 `安全评级∈{B,A,S}`)尤其危险:即便注入器**本意**取的是"别组的评级 B"当跨实体干扰,其字面与"本组上周的 B"**不可区分**,退化照样发生;I7 按"值是否在本时间线成立"判,把这种内容性退化也一并接住,故对低基数字段是**必要**而非过严。剩余 16 条传闻均为纯跨实体取值(本时间线从未出现)→ I7 一律放行,**无一误杀**。

---

## 3. 可靠度真相源(单一来源,audit 纲领)

闸不私藏域知识:可靠度档位从产线常量派生(`AUTHORITATIVE_SRC` / `RUMOR_SRCS`),与注入器同源。

```python
from pipeline.lines.L5_conflict import AUTHORITATIVE_SRC, RUMOR_SRCS

# 可靠度档:数值越大越可信。当前两档;将来加"准官方/已核实"等档只改此表。
_RELIABILITY_TIERS: dict[str, int] = {
    AUTHORITATIVE_SRC: 2,          # 官方通报
    **{src: 0 for src in RUMOR_SRCS},  # 各类传闻/小道,同档
}
_KNOWN_RULES = {"source_reliability"}   # 闸能证伪良定义的裁决规则(fail-closed:未知规则一律 drop)

def reliability_rank(src: str) -> int | None:
    """来源 → 可靠度档(整数)。未登记来源返回 None(= 不可比 → I6 判 drop)。"""
    return _RELIABILITY_TIERS.get(src)
```

> 用 `None`(未登记)而非默认 0,确保**新增来源若忘了登记档位,闸 fail-closed**(I6 判不可比),而不是默默当成传闻放过。

---

## 4. 算法(伪代码)

```python
def well_posed(self, order: dict, ws: WorldState) -> tuple[str, str]:
    """边 A 良定义闸:order 的 gold(权威值)是否为该矛盾题在世界里【唯一、合法】的答案,
    且传闻是真干扰。纯代码、确定性、零 LLM、绝不碰 corpus。与 ground() 对称。
    返回 ("well_posed","") 或 ("drop", reason)。"""
    aux  = order.get("aux") or {}
    ent  = order.get("entity"); fld = order.get("field"); s = aux.get("session")
    auth = aux.get("authoritative_value"); rumor   = aux.get("rumor_value")
    asrc = aux.get("authoritative_source"); rsrc   = aux.get("rumor_source")

    # I0 矛盾可溯源:回 ws.conflicts 查回本题矛盾(与 gt() 同口径)
    c = next((c for c in (getattr(ws, "conflicts", None) or [])
              if c["entity"] == ent and c["field"] == fld and c.get("session") == s), None)
    if c is None:
        return ("drop", "矛盾不可溯源:ws.conflicts 无 (ent,fld,session) 条目(order 与世界脱钩)")

    # I1 order/世界一致:aux 四值须与 ws.conflicts 一致(防出题旁路世界)
    for k in ("authoritative_value", "rumor_value", "authoritative_source", "rumor_source"):
        if _norm(aux.get(k)) != _norm(c.get(k)):
            return ("drop", f"order/世界不一致:aux.{k} 与 ws.conflicts 不符(出题旁路了世界真相)")

    # I2 规则可裁:未知裁决规则 fail-closed(闸无法证伪其良定义)
    rule = c.get("rule")
    if rule not in _KNOWN_RULES:
        return ("drop", f"未知裁决规则 '{rule}':闸无法证伪其良定义(fail-closed)")

    # ── 以下按 rule 分派各自的良定义不变量(当前仅 source_reliability)──
    if rule == "source_reliability":
        # I3 真矛盾
        if _norm(auth) == _norm(rumor):
            return ("drop", f"假矛盾:权威值与传闻值相等({auth!r}),无冲突")

        # I4 唯一合法权威解:世界在该 session 的 canonical = 唯一、合法、== 权威值
        canon = _canonical_at(ws, ent, fld, s)          # value_at_session,见下
        if canon in (INVALID, INSUFFICIENT, None, "") :
            return ("drop", f"权威解不合法:session {s} 世界 canonical={canon!r}(已停用/从未出现)")
        if _norm(canon) != _norm(auth):
            return ("drop", f"权威解不唯一:session {s} canonical={canon!r} ≠ 权威值 {auth!r}")

        # I5 gold 锚定正确:order.gt 与 gt() 重算都 == 权威值
        gt_recomputed = self.gt(ws, order)              # 复用护城河,确定性
        if _norm(order.get("gt")) != _norm(auth) or _norm(gt_recomputed) != _norm(auth):
            return ("drop", f"gold 锚错:gt={order.get('gt')!r} ≠ 该 session 权威值 {auth!r}")

        # I6 可靠度严格可分:权威源档位严格 > 传闻源档位(两源都须登记)
        ra, rr = reliability_rank(asrc), reliability_rank(rsrc)
        if ra is None or rr is None or not (ra > rr):
            return ("drop", f"可靠度不可分:权威源 '{asrc}' 未严格高于传闻源 '{rsrc}'(读者无从甄别)")

        # I7 传闻非等价真相:传闻值不得在该 (ent,fld) canonical 时间线任一 session 成立
        if _appears_in_canonical(ws, ent, fld, rumor):
            return ("drop", f"传闻非干扰:传闻值 {rumor!r} 在 canonical 时间线确出现过(它是真值不是干扰)")

        return ("well_posed", "")

    return ("drop", f"裁决规则 '{rule}' 无对应良定义检查(fail-closed)")   # 兜底


# ── 辅助(纯查世界,不碰 corpus)──
def _canonical_at(ws, ent, fld, s) -> str:
    tl = ws.timeline(ent, fld)
    return tl.value_at_session(s) if tl else INSUFFICIENT

def _appears_in_canonical(ws, ent, fld, value) -> bool:
    """value 是否在该 (ent,fld) canonical 时间线的【任一】有效取值里出现过(_norm 比较)。"""
    tl = ws.timeline(ent, fld)
    if not tl:
        return False
    return any(_norm(v) == _norm(value) for (_s, _d, v) in tl.set_values())
```

### 覆盖各 rule 类型
- **`source_reliability`(已实现)**:走 I3–I7 全套(上面)。
- **`recency`/"新近裁决"(v1,未落地)**:产线 §L5.7 明确**不在本线造**(≈ L1.KU)。若将来落地,在 `_KNOWN_RULES` 加 `recency`,并新增分支:I4 改判"**最新**权威值唯一",可靠度检查(I6)替换为**时效可分**(`date(权威) > date(传闻)` 严格成立);I3/I5/I7 不变。**架构已留好分派位**——加规则 = 加一个 `if rule == ...` 块,不改既有不变量。在那之前,`recency` 落进 I2 fail-closed,绝不放行未验证的规则。

---

## 5. 边界用例(逐一对应不变量)

| 边界 | 触发哪条 | 行为 |
|---|---|---|
| 权威值 == 传闻值(假矛盾) | I3 | drop |
| 可靠度不可比 / 相当(如把官方源也填成"内部群聊",或新增来源忘登记档位) | I6 | drop |
| 权威值在该 session 不唯一 / 不合法(字段已 EXPIRE、或该周 canonical 与权威值不符) | I4 | drop |
| gold 锚错周(gt 取了别周的值或取了传闻值) | I5 | drop |
| 传闻 = 本实体别周真值(矛盾退化为合法演化) | I7 | drop |
| order 的 aux 与 ws.conflicts 对不上(出题环节篡改/旁路) | I1 | drop |
| 出现 `recency` 等未实现规则 | I2 | drop(fail-closed) |

---

## 6. 命中映射:把 L5 "优秀" 特性钉成不变量(防回归)

run0604 盲审 4 人一致评 Q43/44/45 "优秀",原因 = **语料埋了"查无此人 / 无红头文件"的可靠度线索,逼来源甄别**。把这份"优秀"反推成本闸的不变量,使其**可回归测试、改坏即 drop**:

| "优秀" 性质(盲审原话) | 对应不变量 | 改动若破坏它 → 闸的反应 |
|---|---|---|
| "官方 vs 传闻"两方真冲突、不是同一个值 | **I3 真矛盾** | 若注入器退化成 auth==rumor → I3 drop |
| "按来源可靠度裁决"成立(官方严格 > 小道) | **I6 可靠度严格可分** | 若有人把 `AUTHORITATIVE_SRC` 改进 `RUMOR_SRCS`、或新增同档来源 → 档位不再严格可分 → I6 drop |
| 权威方是**唯一**可认定的真相 | **I4 唯一合法权威解** | 若注入器选到已停用/歧义 session 的权威值 → I4 drop |
| 传闻是**干扰**(读者甄别后该排除),不是等价真相 | **I7 传闻非等价真相** | 当前注入器**已在真实数据上偶发退化**(从字段【全局值池】抽小道值时,会抽中本实体别周真值)→ I7 实测命中 **2/18(11.1%)** drop 之(详见 §7.1)。这不是假想回归,是当下就在拦的真坏题。 |
| 答案就是被甄别出的那个权威值 | **I5 gold 锚定正确** | 若烘焙/重算口径漂移,gold ≠ 权威值 → I5 drop |

> **防回归机制 = 这些不变量进 `well_posed` 的离线自检(§8)**。任何让 L5 不再"优秀"的代码改动(注入逻辑、来源池、规则、gt 口径),都会被对应不变量在 CI 自检里 drop 掉对应用例 → 红灯。**"优秀"不再靠出题人记性,而是被代码守住。**

---

## 7. 反补丁自审(逐条论证为何是通用不变量、非特判)

| 不变量 | 为何是通用不变量(能 drop 任何违背它的矛盾题),不是特判某题 |
|---|---|
| I0 溯源 | 对**任何** order 都要求"能在世界里找到对应矛盾"——是 order↔世界的结构契约,不针对任何具体实体/字段/值。 |
| I1 一致 | 对**任意**矛盾题断言"出题不得旁路世界真相";检查的是 aux 与 `ws.conflicts` 的恒等关系,与具体内容无关。 |
| I2 规则闭集 | fail-closed 是**对全体未知规则**的统一态度(白名单),不是为某条规则开后门。 |
| I3 真矛盾 | `auth ≠ rumor` 是"矛盾"概念的定义本身,对所有 (ent,fld,session) 成立。 |
| I4 唯一合法权威解 | 用世界 canonical 当裁判,对**任何**字段一视同仁(数值/人名/状态都走 `value_at_session`);它检验的是"唯一正解"这一抽象性质,不枚举症状。 |
| I5 gold 锚定 | "gold == 该 session 权威值"是 gold 语义的恒等式;且用 `gt()` 重算交叉验,与接地闸"gt 重算 == 烘焙"同构。 |
| I6 可靠度可分 | "权威源严格 > 传闻源"是 source_reliability 规则**可裁**的充要前提;通过单一 `_RELIABILITY_TIERS` 表实现,泛化到任意来源组合。 |
| I7 传闻非干扰 | "传闻不在 canonical 成立"是"干扰项"概念的定义;对任意 (ent,fld) 时间线通用,不针对具体传闻值。 |

**自检结论**:全部 8 条都是"对任意 L5 矛盾题成立的命题",形式为"查某个世界/order 不变量 → 违背即 drop",没有任何"if 实体名 == X / if 值 == Y"式症状特判。新规则只在 §4 的分派处加分支、复用同一组抽象不变量(真矛盾/唯一解/可分/锚定/非干扰),架构而非补丁。

### 7.1 · worked example:I7 是真不变量(实测验真,与 L2 `roles_of` 镜像对照)

> **铁律(L2 逼出来):一条"查世界 + 不含字面量"的检查只是【必要非充分】。还必须过实测关——它度量的量真等价于"不良定义"吗?拿真实数据量误杀/真覆盖。** L2 的 `roles_of` 没过这关被撤;L5 的 I7 过了,留作正例。

**形式上 I7 和 `roles_of` 长得一样**:都是"对世界时间线的结构查询、不含具体名字"。差别在**它度量的量是否=不良定义**:
- `roles_of` 量的是桥的**连接度**(戴几顶帽子)——而 gold 由 `gt_multihop` 从钉死起点解析,**与连接度正交**,故"`roles_of` 触发"⊥"gold 不唯一"。**13% 误杀 / 0 真覆盖** → 症状相关性代理,撤。
- I7 量的是**传闻值是否逐字落在本实体本字段 canonical 时间线**——这**直接**等价于"矛盾退化成时效演化、读者有第二个可辩护正解"。它度量的就是不良定义本身,不是它的相关量。

**实测(run0605 15 单 + run190554 3 单 = 18 真实矛盾,`./venv/bin/python` 载 `02_world.json`/`03_orders.json`):**

| 不变量 | 误杀率(砍合法好题) | 真覆盖(确为坏题) | 与 I4 增量 | 判据 |
|---|---|---|---|---|
| `roles_of`(L2) | **13%(2/15)** | **0** | — | 连接度 ⊥ 歧义 → **撤** |
| **I7(L5)** | **0%(0/18)** | **11.1%(2/18)** | **2 条全不被 I4 覆盖** | 值落本时间线 ⟺ 退化 → **留** |

- **真覆盖的 2 条**:① `基础安全组.安全评级`@s4——auth=A,rumor=B,而 B 是本组 s0–3 的过去真值;② `支付清算部.负责人`@s3——auth=钱七,rumor=孙八,而孙八是本部 s6–8 的未来真值。两传闻值经探针确认**只出现在本实体本字段时间线**(全世界别处无此值),坐实"本实体别周真值",非跨实体干扰。这两题里"小道消息说 B/孙八"对应的是 corpus 里**真属于本实体**的事实,会检索两边的好系统读成合法 L1.CONFLICT(时效演化)、按 recency 给出与世界相符的竞争答案,却被判错 → ill-posed。
- **0 误杀**:其余 16 条传闻均为纯跨实体取值(本时间线从未出现),I7 一律放行;无一合法好题被砍。
- **I4 零重叠**:这 2 条的 canonical@矛盾周恰好 == auth(I4 全放行);I7 是 I4 够不到的独立防线,**非冗余**。
- **低基数 category 字段的额外必要性**:`安全评级∈{B,A,S}` 这类小词表字段,"别组的 B"与"本组上周的 B"字面不可区分——即便注入器本意取跨实体干扰,退化照样发生;I7 按"值是否在本时间线成立"判,把这种内容性退化也接住。→ I7 不是过严,是**这族字段的刚需**。

**结论**:I7 = **被实测验真的真不变量**(0 误杀 / 真覆盖且 I4 够不到),**保留**。它与 `roles_of` 同形而异质,正是"形式像不变量 ≠ 是不变量,须实测"这条铁律的**正反两面教材**。

#### 7.1.1 · 根因 & 上游建议(inject_conflicts 可顺手改对,但非闸的前置)

实测命中的 2 条退化**源于注入器的取值口径**:`inject_conflicts`(`pipeline/lines/L5_conflict.py:68`)选小道值时,`wrong = [v for v in pool[fname] if _norm(v)!=_norm(auth)]`——`pool[fname]` 是该字段名的**全局值池(跨全部实体聚合)**,过滤只排掉了 `auth`,**没排掉本实体自己别周的真值**。于是当某实体该字段历史上有 ≥3 个不同值时,非 auth 的本实体旧值/未来值会落进 `wrong`,被 `rng.choice` 抽中当"小道",制造出退化矛盾。

- **上游一行修复(推荐)**:把 `wrong` 的过滤从"≠ auth"收紧到"**不在本实体本字段时间线**"——即 `own = {_norm(v) for (_,_,v) in sv}; wrong = [v for v in pool[fname] if _norm(v) not in own]`。这样小道值恒为**跨实体**真值(纯干扰),从源头消除退化,I7 的真覆盖会归零(但 I7 **仍保留**作防回归:防止日后有人改回宽松池)。
- **为何不把修复当作闸的前提**:边 A 闸的职责是"坏题出不了厂",**不依赖上游是否先修好**——即便注入器永不改,I7 也独立把这 11% 退化挡在题库外(fail-closed 哲学,与接地闸"不信渲染端自觉"同构)。故**两件事解耦**:闸照设计保留 I7;注入器修复列为**独立的产线改进项**(改了让 L5 出题良率更高,但不改 I7 也安全)。

---

## 8. 自检用例 + 离线自检法

**离线自检法**:造小 `WorldState` + 手写 `ws.conflicts` + 构 order(模拟 `enumerate` 输出),断言 `well_posed` 返回。零 corpus、零 LLM、确定性,挂进 `python -m pipeline.lines.L5_conflict` 自检尾部(与现有 20/20 自检并列)。骨架:

```python
def _mk(ws_table, conflicts, order):
    ws, _ = assemble_world(ws_table); ws.conflicts = conflicts
    return ConflictLine().well_posed(order, ws)

def _order(ent, fld, s, auth, rumor, asrc=AUTHORITATIVE_SRC, rsrc=RUMOR_SRCS[0], gt=None):
    return {"line":"L5_conflict","capability":"L5_conflict","entity":ent,"field":fld,
            "gt": auth if gt is None else gt,
            "aux":{"session":s,"rule":"source_reliability",
                   "authoritative_value":auth,"authoritative_source":asrc,
                   "rumor_value":rumor,"rumor_source":rsrc}}
```

| # | 用例 | 期望 | 验的不变量 |
|---|---|---|---|
| 1 | **well-posed**:搜索部.负责人 s3 canonical=王强(权威),传闻=周明(他部门人,本部门从没出现),官方源 vs 群聊源 | `("well_posed","")` | 全过 |
| 2 | **假矛盾**:auth=王强、rumor=王强 | drop / `假矛盾` | I3 |
| 3 | **可靠度不可分**:asrc 与 rsrc 都填 `RUMOR_SRCS[0]`(同档) | drop / `可靠度不可分` | I6 |
| 4 | **权威解不合法**:矛盾挂在字段已 EXPIRE 的 session(`value_at_session=INVALID`) | drop / `权威解不合法` | I4 |
| 5 | **gold 锚错**:order.gt 填成 rumor 值 | drop / `gold 锚错` | I5 |
| 6 | **传闻非干扰**:rumor 填"李娜"(搜索部.负责人 s0 的真值,本实体别周值) | drop / `传闻非干扰` | I7 |
| 7 | **未知规则**:c["rule"]="recency" | drop / `未知裁决规则` | I2 |

> 断言写法:`status, reason = _mk(...); assert status=="drop" and "可靠度不可分" in reason`。用例 1 必须 `well_posed`,2–7 必须 `drop` 且 reason 命中对应关键词 → 形成回归网:任何破坏 L5 "优秀"性质的改动会翻红。

---

## 9. 复用 vs 新建

- **复用**:`_norm`(判分同口径)、`ConflictLine.gt()`(I5 重算,护城河复用)、`ws.timeline().value_at_session()/set_values()`(I4/I7)、`INVALID/INSUFFICIENT` 哨兵、`AUTHORITATIVE_SRC/RUMOR_SRCS`(I6 真相源)、与 `ground()` 对称的 `(status, reason)` 契约与 stage 编排思路。
- **新建**:① `ProductionLine.well_posed()`(基类可给 fail-closed 默认:无对应良定义判定 → drop,逼各线覆写,同 `ground()` 的 fail-closed 哲学);② L5 覆写 = 本文 §4;③ `_RELIABILITY_TIERS` + `reliability_rank()` + `_KNOWN_RULES`(可靠度/规则真相源);④ orders→questions 支的 stage 编排(逐题过闸 + 存活率报告 + 弃因),与 `run_grounding` 对称;⑤ 自检用例(§8)。
