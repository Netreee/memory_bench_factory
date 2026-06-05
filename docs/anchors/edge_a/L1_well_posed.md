# 边 A 闸(良定义 well_posed)· L1_timeline 详设

> **定位**:验证三角(`redesign_factory_v2.3.md` §V)的 **(A) 题面↔gold** 边。run0604 实证:接地闸(边 B,§G)把"值在不在语料"治到 86%,但剩余错误 100% 落在边 A——题面没把 gold 赖以确定的坐标点全、或 gt 本身与世界不符、或答案类型与能力不符。本闸 = §G 的对偶:**纯代码、确定性、零 LLM、绝不碰 corpus**,只用 `(order 意图 + gt + 世界 ws)` 判。
>
> **跑位**:`stage_orders`(03_orders)产出后、`stage_questions`(phrase)前。与 `ground()` 对称的 per-line `well_posed()`,drop 的 order 在 phrase 前剔掉(坏题不进出题、不浪费 LLM)。
>
> **签名(统一契约,4 条线一致)**:`well_posed(self, order: dict, ws: WorldState) -> tuple[str, str]`,返回 `("well_posed", "")` 或 `("drop", reason)`。

---

## 1. L1"良定义"的一句话定义

> **一道 L1 题良定义,当且仅当:把 order 的意图坐标(能力语义 + aux 里承诺的时点/聚合)代回【世界 ws】机械重算,得到的答案【存在、唯一、合法、类型与能力相符】,且与 `order.gt` 逐字段相等。**

换言之:gold 必须是"该题面在世界里的【唯一正确解】"。重算值 ≠ 烘焙 gt(Q19)、或答案类型与提问类型错配(Q07 计数答人名)、或停用类时效不成立(FORGET 停后复活 / ABS 字段其实存在)——皆 ill-posed,drop。
> **★实测厘清两个"看着像歧义、其实不是"的坐标**(详 §6 worked):(a) **MR 极值平手**——题面问"值"非"哪周",平手时值仍唯一,**非多解**(原 W5 撤下);(b) **IE「第N周」off-by-one**——是【题面文字↔世界坐标】错位,**本闸看不到题面文字、治不了**,治本在 `intent()` 全局周编号口径(原 W4 降为结构兜底)。

**核心不变量(总纲)**:`recompute(order.intent_coords, ws) == order.gt`,且该 recompute 在世界里**唯一**。这是 `gt()` 护城河(命门2:gt 对世界成立)的**反向复核 + 唯一性补强**:命门2 让 gt 诞生正确,本闸验它"作为一道题"仍成立。

---

## 2. 不变量清单(每条 = 一个可证伪检查)

所有不变量都用 `ws` 重算、与 `order` 比对;不读 corpus。reason 文案统一前缀 `well_posed:` 便于报告聚合。

| # | 不变量(名) | 适用能力 | 查什么 | drop 条件 | reason 文案 |
|---|---|---|---|---|---|
| **W0** | **结构完整** | 全部 | order 有 capability/entity/field(ABS 除外:entity 可空);非 ABS 字段在 `ws.timeline(ent,fld)` 存在 | 字段在世界缺 timeline(且非 ABS) | `结构缺失:{ent}.{fld} 世界无此时间线` |
| **W1** | **gt 与世界一致(可复算闸)** | IE/KU/MR/TR/PREEXPIRE | 用 ws 按能力重算 gold,与 `order.gt` 逐字段 `_norm` 比 | 重算 ≠ 烘焙 | `gt 与世界不符:重算={r} 烘焙={g}` |
| **W2** | **gold 合法(非哨兵)** | IE/KU/MR/TR/PREEXPIRE | 重算值 ∉ {INVALID, INSUFFICIENT, None, ""} | 重算得哨兵/空 | `gold 悬空:重算为 {INVALID/INSUFFICIENT}` |
| **W3** | **答案类型与能力相符** | 全部 | 按能力断言 gt 的形状/语义类型(计数能力→数值;时点能力→单值;变更能力→含 session/to) | 类型错配 | `类型错配:{cap} 应为{期望型},gt={g}` |
| **W4** | **IE 周锚结构合法(fail-closed)** | **IE only**(★实测下调,见 §2 注 + §6 worked #2) | IE 的 `aux.at_week` 是 int 且 ∈ `[0, n_sessions)`,且 `gt.at_week == aux.at_week`(同源) | at_week 缺失/非整数/越界(KU 不查:其 at_week 不上题面) | `IE 周锚非法:at_week={N} 越界/缺失/与 gt 不一致` |
| **W5** | ~~极值唯一(无平手)~~ **【实测证伪,撤下】** | ~~MR~~ | ~~极值数值多 session 并列~~ | ~~平手~~ —— **删除** | —— **见 §2 注 + §6 worked #1** |
| **W6** | **首次变更唯一** | TR | `change_ops()` 过滤 SET/UPDATE&prev≠None,取 `[0]`;必须存在且其前有 ≥1 prior session(否则"首次变化"无参照) | 无变更 / 无 prior | `首次变更不成立:{ent}.{fld} 无变更或无变更前周` |
| **W7** | **停用事件确有其事** | FORGET/PREEXPIRE | 世界里该字段确有 DELETE/EXPIRE op;FORGET 的 question_date 切片确为 INVALID(已停);PREEXPIRE 的 prev(停前值)非空 | 无停用 op / question_date 处未停 / 停前值空 | `停用不成立:{ent}.{fld} 无 EXPIRE/DELETE 或切片未停` |
| **W8** | **缺失为真(ABS)** | ABS | `ws.has_field(fld)` 必须为 False(世界里任何 timeline 都没这个字段) | 世界里该字段存在 | `ABS 不成立:字段「{fld}」在世界存在` |
| **W9** | **证据可读齐** | 全部(非 ABS) | `evidence_sessions` 非空且全 < `ws.n_sessions`;能力相关锚 session(MR.session/TR.session/IE.at_week)∈ evidence_sessions | 锚 session 不在证据里 | `证据缺锚:{cap} 锚 session={s} 不在 evidence_sessions` |

**W1 是主力闸**(覆盖 Q19,实测真覆盖核心 + 唯一可证伪),W3 治 Q07,W6/W7 治时序/停用时效(实测 W7 抓到 run190554 一道真坏 FORGET)。W0/W2/W4/W9 是 fail-closed 兜底(实测 0 误杀)。

> **★实测撤改两条(2026-06-05,对 office 当前 28 单 + 备用 26 单 + 全候选池 194/72 MR 探针)**:
> - **W5(极值平手)撤下**:实测 **误杀 10.3%(20/194)、真覆盖 0** —— L1 的 MR 题面只问"最高/最低**是多少**"(问值,非问周),平手时值仍唯一,平手 ⊥ gold 歧义。与 L2 `roles_of≥2`(连接度⊥歧义)同型的【症状相关性代理】。详见 §6 worked #1。
> - **W4 下调到"IE-only 结构 fail-closed"**:实测 0 drop;其"治 Q01 off-by-one"的原始承诺**证伪**——off-by-one 在 `intent()` 渲染端(`第N周` 应为 `第N+1周`),**order dict 里看不到题面文字,W4/W1 都无法看见它**。真正的治本是命门1 全局单一周编号真源 `week_label(s)=s+1`;W4 只保留"at_week 是合法 session 索引 + 与 gt 同源"这一结构兜底,且**只对 IE**(KU 的 at_week 是内部记账、不上题面,卡它是 over-reach)。详见 §6 worked #2。

---

## 3. 逐能力算法(伪代码)

> 通用 helper(放 grounding.py 或新 `pipeline/well_posed.py`,与 grounding 对称):
> - `_eq(a, b)`:`_norm(a) == _norm(b)`(与判分同口径)。
> - `_eq_dict(r, g, keys)`:对 keys 逐个 `_eq(r[k], g[k])`(忽略只在一边的辅助键如 date)。
> - `_is_sentinel(v)`:`v in (INVALID, INSUFFICIENT, None, "") or _norm(v)==""`。
> - `_to_num` / `_date_of` / 各 `gt_*` 直接复用 `world_state`。

派发骨架(与 `ground()` 对称):

```python
def well_posed(self, order, ws):
    cap = order.get("capability")
    ent, fld = order.get("entity",""), order.get("field","")
    gt, aux = order.get("gt"), (order.get("aux") or {})
    ev = order.get("evidence_sessions") or []

    # ── W0 结构完整(ABS 不需 entity/timeline)──
    if cap != "ABS":
        tl = ws.timeline(ent, fld)
        if tl is None:
            return ("drop", f"well_posed:结构缺失:{ent}.{fld} 世界无此时间线")

    dispatch = {"IE": _wp_ie, "KU": _wp_ku, "MR": _wp_mr, "TR": _wp_tr,
                "FORGET": _wp_forget, "PREEXPIRE": _wp_preexpire, "ABS": _wp_abs}
    fn = dispatch.get(cap)
    if fn is None:
        return ("drop", f"well_posed:未知能力 {cap}(fail-closed)")
    return fn(ws, ent, fld, gt, aux, ev)
```

### IE —— 某周生效值

意图坐标 = `at_week`(0-based session 索引)。良定义要求:① at_week 是合法 session 索引(W4 结构兜底);② 在 at_week 重算的值 = gt.value 且非哨兵(W1+W2,**主力**);③ at_week ∈ evidence_sessions(W9);④ value_at_session 是 fold,本就唯一,只需拒 INVALID/INSUFFICIENT。
> **★不再声称"治 Q01 off-by-one"**(实测证伪,见 §6 worked #2):闸只看 order dict、看不到题面渲染的"第N周"文字,故无法判 off-by-one。本闸 W4 的角色降为"at_week 结构合法 + 与 gt 同源"的 fail-closed,off-by-one 治本在 `intent()` 全局口径(`week_label(s)=s+1`)。

```python
def _wp_ie(ws, ent, fld, gt, aux, ev):
    N = aux.get("at_week")
    if N is None or not isinstance(N, int):                       # W4: 结构——周锚缺失/非整数(fail-closed)
        return ("drop", "well_posed:IE 题面无周锚(at_week 缺失/非整数)")
    if not (0 <= N < (ws.n_sessions or 10**9)):                   # W4: 结构——越界(实测 0 触发,纯兜底)
        return ("drop", f"well_posed:IE 周锚越界:at_week={N} 不在 [0,{ws.n_sessions})")
    g = (gt or {}).get("value") if isinstance(gt, dict) else None # W3: 类型——IE gt 必须 {value,at_week}
    if g is None:
        return ("drop", f"well_posed:IE 类型错配:gt 非 {{value,at_week}},gt={gt}")
    r = gt_ie(ws, ent, fld, N)                                    # = tl.value_at_session(N)
    if _is_sentinel(r):                                           # W2: 该周无合法值(fail-closed,W1 子集除外)
        return ("drop", f"well_posed:IE gold 悬空:第{N}周(session)处值={r}")
    if not _eq(r, g):                                             # W1 ★主力:重算==烘焙
        return ("drop", f"well_posed:IE gt 与世界不符:重算={r} 烘焙={g} @session{N}")
    if (gt or {}).get("at_week") != N:                           # W4: gt 内周锚与 aux 同源(单一真源残留校验)
        return ("drop", f"well_posed:IE 周锚不一致:gt.at_week={gt.get('at_week')} aux.at_week={N}")
    if N not in ev:                                              # W9: 锚周可读(fail-closed)
        return ("drop", f"well_posed:IE 证据缺锚:session{N} 不在 evidence_sessions")
    return ("well_posed", "")
```

> **★Q01 off-by-one 治本在全局,不在本闸(实测厘清,见 §6 worked #2)**:实测确认当前 `intent()` 直接渲染 `复盘第 {at_week} 周`(at_week=2 → "第2周",而 session 2 的日期 01-20 被人读成"第3周")是 off-by-one 源头。**但这条 off-by-one 是【题面文字 ↔ 世界坐标】的错位,order dict 里不含题面文字,本闸(及 W1)根本看不见它**——闸只拿到 `at_week=2 + gt.value`,而 `gt_ie(ws,…,2)` 与烘焙 `gt.value` 同源(都把 at_week 当 session 索引),W1 必然通过。⟹ **治本只能在命门1:全局单一周编号真源**,建议 `intent()` 把 `第 {at_week} 周` 改成 `第 {at_week+1} 周`(即 `week_label(s)=s+1`,人读 1-based = session+1),题面与语料同函数。本闸 W4 不再宣称"治 off-by-one",只保留"at_week 是合法 session 索引 + 与 gt.at_week 同源"的结构 fail-closed(实测 0 触发,纯兜底未来 enumerate 退化)。

### KU —— 最新有效值

意图坐标 = "截至最新"(temporal:latest,**无显式周锚**)。良定义:① 最新值 = gt 且非哨兵(被 EXPIRE/DELETE 则应走 FORGET,KU 给悬空 → drop);② 确实"更新过"(distinct ≥ 2,否则是 trivial stable,enumerate 已限,这里复核)。
> **★KU 不查 at_week**(实测):KU 的 `gold = latest_valid`,与 `aux.at_week` **无关**(`gt_ku` 不接收 N);且 KU intent 明确"不要暗示是第几周",at_week 是 enumerate 的内部记账(最后稳定参照周),**不上题面**。故不把 at_week ∈ evidence 当良定义条件(那是卡一个不承载题面语义的内部字段 = over-reach;原 W4/W9 对 KU 卡 at_week 的分支删除)。KU 的证据齐由 W9 通用"证据非空 + 不越界"兜底即可。

```python
def _wp_ku(ws, ent, fld, gt, aux, ev):
    r = gt_ku(ws, ent, fld)                                       # = tl.latest_valid()
    if _is_sentinel(r):                                           # W2: 最新已失效/无 → 不是 KU
        return ("drop", f"well_posed:KU gold 悬空:最新值={r}(应走 FORGET 或字段无效)")
    if isinstance(gt, dict):                                      # W3: KU gt 必须是纯标量
        return ("drop", f"well_posed:KU 类型错配:gt 应为单值标量,得 dict={gt}")
    if not _eq(r, gt):                                            # W1
        return ("drop", f"well_posed:KU gt 与世界不符:重算={r} 烘焙={gt}")
    distinct = {_norm(v) for (_,_,v) in (ws.timeline(ent,fld).set_values())}
    if len(distinct) < 2:                                         # 复核"真更新过"(否则 trivial,非记忆题)
        return ("drop", f"well_posed:KU 退化:{ent}.{fld} 全程单值,无'更新'语义")
    return ("well_posed", "")
```

### MR —— 极值(治 Q19;★不再查平手 W5,实测证伪撤下)

意图坐标 = `agg ∈ {max,min}`。**L1 的 MR 题面只问"最高/最低【是多少】"(问值,非问周)**——见 `intent()`:`问…最高/最大是多少`。良定义:① 重算 argmax/argmin 值 = gt.value 且 session/agg 一致(W1,**主力,治 Q19**);② 值非哨兵(W2);③ gt 形如 `{value,session,agg}`(W3)。**极值数值可在多周并列(平手),但值仍唯一 → 不影响良定义,不查**(见下注 + §6 worked #1)。

```python
def _wp_mr(ws, ent, fld, gt, aux, ev):
    agg = aux.get("agg", "max")
    if agg not in ("max", "min"):
        return ("drop", f"well_posed:MR agg 非法:{agg}")
    if not isinstance(gt, dict) or "value" not in gt or "session" not in gt:  # W3
        return ("drop", f"well_posed:MR 类型错配:gt 应为 {{value,session,...}},得 {gt}")
    r = gt_mr(ws, ent, fld, agg)                                  # {value,session,date,agg}
    if _is_sentinel(r.get("value")):                             # W2
        return ("drop", f"well_posed:MR gold 悬空:无数值候选")
    if not (_eq(r["value"], gt["value"]) and r["session"] == gt["session"]
            and r.get("agg") == gt.get("agg")):                  # W1 ★主力:治 Q19(重算 max=14 ≠ 烘焙 15)
        return ("drop", f"well_posed:MR gt 与世界不符:重算={r} 烘焙={gt}")
    # ── W5(极值平手)撤下:实测 10.3% 误杀 / 0 真覆盖 —— 题面问"值"非"哪周",平手时值仍唯一。
    #    平手 ⊥ gold 歧义(与 L2 roles_of≥2 同型的症状相关性代理)。见 §2 注 + §6 worked #1。
    if gt["session"] not in ev:                                  # W9: 证据可读(fail-closed)
        return ("drop", f"well_posed:MR 证据缺锚:极值 session{gt['session']} 不在 evidence")
    return ("well_posed", "")
```

> **★MR gt 仍带 session,但平手不破坏良定义**:gt 锚的 session 由 `gt_mr` 的 `max/min(key=…)` 确定性取首个(实测重算可复现);题面问"值"故读者只需答 `r.value`(唯一),`session` 仅供接地(边 B)锚证据用——而平手时该值在每个并列周都被陈述,锚任一周接地都成立。**平手 ⊥ 可接地性,也 ⊥ 答案唯一性**。若未来加"问哪周最高"的 MR-which 子型(gt 答案=周),平手才构成歧义,届时应**按子型分流**重新引入平手检查(`if aux.get("ask")=="which"`),而非对当前"问值"子型一刀切。

> **★Q19 根因 + gt 改对**:`gt_mr` 用 `_to_num` 抽数比大小、argmax/argmin 都给,**算法本身正确**。Q19(gold=15 但 max 实为 14)只可能来自:(a) order.gt 被旧世界烘焙、世界后续被 CRITIC 改了字段而 order 没重烘焙;或 (b) `_to_num` 抽错(如 "14次/15人" 混读)。W1 用**当前 ws** 重算并逐字段比,**任何**这类"烘焙 gt 与现世界不符"的题都会被 drop——不是特判 15。若高频触发,治本在 stage_orders 后强制 `gt(ws,o)==o['gt']` 自检(L1 自检已有 RECOMP 闸,但只在单测跑;建议提到产线 stage 末做)。

### TR —— 首次变更(哪一周)

意图坐标 = "首次变化的周"。良定义:① 存在 ≥1 真变更(`change_ops` 过滤 SET/UPDATE&prev≠None);② 取首个变更 op,与 gt.session/from/to 一致(W1);③ 该变更前有 prior session(否则"首次变化"无"变化前"参照,不成立);④ to 值非哨兵;⑤ 类型:gt 必须含 session+to(问"哪周",答案是周/含变更,非人名计数)。

```python
def _wp_tr(ws, ent, fld, gt, aux, ev):
    tl = ws.timeline(ent, fld)
    chs = [o for o in tl.change_ops() if o.op in (SET,UPDATE) and o.prev is not None]
    if not chs:                                                  # W6: 无变更
        return ("drop", f"well_posed:TR 首次变更不成立:{ent}.{fld} 无 SET/UPDATE 变更")
    op = chs[0]
    if not isinstance(gt, dict) or "session" not in gt or "to" not in gt:  # W3
        return ("drop", f"well_posed:TR 类型错配:gt 应为 {{session,to,...}},得 {gt}")
    if not (op.session == gt["session"] and _eq(op.value, gt["to"]) and _eq(op.prev, gt.get("from"))):  # W1
        return ("drop", f"well_posed:TR gt 与世界不符:首变={op} 烘焙={gt}")
    prior = [s for (s,_,_) in tl.set_values() if s < op.session]
    if not prior:                                               # W6: 无"变化前"周 → "首次变化"无参照
        return ("drop", f"well_posed:TR 首变前无周:session{op.session} 之前无该字段值")
    if op.session not in ev:                                    # W9
        return ("drop", f"well_posed:TR 证据缺锚:首变 session{op.session} 不在 evidence")
    return ("well_posed", "")
```

### FORGET —— 遗忘/停用(时效良定义是关键)

意图坐标 = `question_date`(停用后某时点)。良定义:① 世界确有 DELETE/EXPIRE op;② question_date 切片确为 INVALID(即问的时点确已停用);③ gt 形如 `{forgotten:True, value:None}`(类型:答"已停/查不到",不是给个旧值)。注意:order dict 不保留 question_date(`gt()` 注释),但 enumerate 阶段 Order 有;**well_posed 在 orders→questions 支跑,order 是 enumerate 直出的 dict**——需确认 question_date 是否落进 dict。若未落进(当前 enumerate 的 dict 不含 question_date),则用 `gt_forget` 无法重算 → **退化为:验世界有停用 op + gt.forgotten 与"最新是否失效"一致**。

```python
def _wp_forget(ws, ent, fld, gt, aux, ev):
    tl = ws.timeline(ent, fld)
    has_stop = any(o.op in (DELETE, EXPIRE) for o in tl.ops)
    if not has_stop:                                            # W7: 世界根本没停用 → FORGET 不成立
        return ("drop", f"well_posed:FORGET 停用不成立:{ent}.{fld} 无 EXPIRE/DELETE op")
    if not (isinstance(gt, dict) and "forgotten" in gt):       # W3
        return ("drop", f"well_posed:FORGET 类型错配:gt 应含 forgotten,得 {gt}")
    qd = order_question_date(aux, ws)        # 若 dict 带 question_date 用之,否则取 EXPIRE 后日期
    if qd:                                                     # W7: 切片时点确已停
        v = tl.value_at(qd)
        if (v == INVALID) != bool(gt.get("forgotten")):
            return ("drop", f"well_posed:FORGET 时效不符:@{qd} 切片={'停' if v==INVALID else '在'} gt.forgotten={gt.get('forgotten')}")
    else:
        # 无 question_date:至少要求 latest_valid 已失效(否则"现在还查得到吗"答 forgotten 矛盾)
        if (tl.latest_valid() == INVALID) != bool(gt.get("forgotten")):
            return ("drop", f"well_posed:FORGET 时效不符:最新={'失效' if tl.latest_valid()==INVALID else '有效'} gt.forgotten={gt.get('forgotten')}")
    return ("well_posed", "")
```

### PREEXPIRE —— 过期前值

意图坐标 = "停掉【前】最后值"。良定义:① 世界有 EXPIRE/DELETE op;② 停前值(= EXPIRE op 的 prev)= gt 且非空;③ 类型:gt 是纯标量(那个旧值),非 dict;④ aux.expire_session ∈ evidence。

```python
def _wp_preexpire(ws, ent, fld, gt, aux, ev):
    pe = gt_pre_expire(ws, ent, fld)                           # {value, expire_session} 或 INSUFFICIENT
    if not isinstance(pe, dict) or _is_sentinel(pe.get("value")):  # W7 + W2
        return ("drop", f"well_posed:PREEXPIRE 停用前无值:{ent}.{fld} 无 EXPIRE 或停前值空")
    if isinstance(gt, dict):                                   # W3: 停前值是单标量
        return ("drop", f"well_posed:PREEXPIRE 类型错配:gt 应为单值,得 dict={gt}")
    if not _eq(pe["value"], gt):                               # W1
        return ("drop", f"well_posed:PREEXPIRE gt 与世界不符:停前={pe['value']} 烘焙={gt}")
    es = aux.get("expire_session", pe["expire_session"])
    if es not in ev:                                           # W9
        return ("drop", f"well_posed:PREEXPIRE 证据缺锚:expire session{es} 不在 evidence")
    return ("well_posed", "")
```

### ABS —— 缺失(拒答)

意图坐标 = "该字段本场景不存在"。良定义:**世界里该字段确实不存在**(`has_field` False),且 gt 是拒答哨兵。注意:这是边 A(对世界)判;边 B 的 ABS 是对 corpus 判(字段全语料不出现)。两者正交——A 验"世界真没有",B 验"语料真没渲"。

```python
def _wp_abs(ws, ent, fld, gt, aux, ev):
    if ws.has_field(fld):                                      # W8: 世界里其实有这个字段 → 拒答不成立
        return ("drop", f"well_posed:ABS 不成立:字段「{fld}」在世界存在(非缺失)")
    if _norm(gt) != _norm(INSUFFICIENT):                       # W3: ABS gold 必须是拒答哨兵
        return ("drop", f"well_posed:ABS 类型错配:gt 应为 {INSUFFICIENT},得 {gt}")
    return ("well_posed", "")
```

---

## 4. 边界处理

- **平手(极值多周并列)——不 drop(实测撤下 W5)**:L1 的 MR 题面问"最高/最低【是多少】"(值),平手时**值仍唯一**(实测全部 20 个平手候选 distinct 归一值 = 1),答案良定义。`gt.session` 仅供接地锚证据,且并列周都陈述该值,接地不受影响。平手 ⊥ 答案唯一性 ⊥ 可接地性 → 不查(详 §6 worked #1)。仅当未来出"问哪周最高"的 MR-which 子型(答案=周)才**按子型**重引入平手检查。
- **多周同值(IE/KU)**:value_at_session 是前向 fold,任一 session 的值唯一,不会多解。IE 的"题面周锚指代是否清"由 `intent()` 全局 `week_label` 口径决定(off-by-one 治本在那),W4 只兜底 at_week 结构合法。
- **数据不足**:W0(无 timeline)、W2(重算哨兵)、W6(TR 无变更/无 prior)、W9(证据空或锚不在证据)——全部 fail-closed drop,reason 指明缺什么。实测均 0 误杀。
- **停用事件(FORGET/PREEXPIRE/ABS)**:时效良定义是关键(任务点名)。
  - FORGET:必须世界真有 EXPIRE/DELETE(W7),**且口径锚到"最新"**(因 intent 问"截至最新…可能已停止")——`latest_valid()==INVALID` 须与 `gt.forgotten` 一致。**实测**:run190554 的 `数据平台部.平台SLA` 在 s0 EXPIRE 后又复活(latest_valid=99.5 有效),gt 却写 forgotten=True ⟹ 与"截至最新"框架矛盾 = 真坏题,**W7 退化口径正确 drop 它**(W1 抓不到,真增量)。注:enumerate 用 `_after(exp[0])` 烘焙 question_date,对"停-复活"字段会与"截至最新"框架错位;W7 锚 latest 与 intent 对齐,是正确判据。
  - PREEXPIRE:停前值 = EXPIRE op 的 prev,必须非空(刚 SET 就 EXPIRE 没有"前值"→ drop)。
  - ABS:**世界里真的没有该字段**(W8)——这是与"有但停用"(FORGET)的本质区别;混了就是把"曾有后停"误当"从未有"。
- **「第N周」周编号口径(★系统性,off-by-one 治本)**:全管线**单一真源 = session 索引**;`_date_of(session)` 是该 session 的唯一日期(base 01-06 + 7×session)。**实测确认** `intent()` 渲染 `复盘第 {at_week} 周` 是 off-by-one 源头(session 2 渲染成"第2周",人读应为"第3周")。**治本**:`intent()` 统一 `week_label(s)=s+1`(题面与语料同函数)。**本闸看不到题面文字,不治此症**;W4 仅强制 at_week 为合法 session 索引并与 gt.at_week 同源(结构 fail-closed,实测 0 触发)。

---

## 5. 命中映射(盲审 L1 症状 ← 哪条不变量 drop)

| 盲审症状 | 病因 | 被 drop 的不变量 | 怎么 drop |
|---|---|---|---|
| **Q01** 「第N周」off-by-one(题面"第N周" ↔ 世界 session 坐标错位 → 解析错周) | 周编号双口径 | **不由本闸治**(治本在 `intent()` 全局口径) | ★实测厘清:off-by-one 是【题面文字↔世界坐标】错位,order dict 不含题面文字,W4/W1 都**看不见**它(`gt_ie(…,at_week)` 与烘焙 gt 同源,W1 必过)。治本 = 命门1 全局 `week_label(s)=s+1`(`intent()` 把 `第{N}周`→`第{N+1}周`)。W4 只兜底 at_week 结构合法 + 与 gt.at_week 同源(实测 0 触发) |
| **Q19** MR 极值 gold=15 但世界 max=14 | 烘焙 gt 与现世界不符 | **W1**(MR 重算==烘焙) | `gt_mr(ws,...)` 重算 value/session,与 order.gt 逐字段比,不等即 drop(治**任何**陈旧/错算极值,非特判 15) |
| **Q06** 累计 vs 快照(问"总共几次"却给最新周快照) | 答案语义 ≠ 提问语义 | **W3**(类型/语义错配) | "总共/累计"是计数语义,L1 无"累计计数"能力;若 order 是 KU 而题面问累计 → gt 是快照标量、与"几次"计数语义不符 → drop。**注**:更根上,L1 不该 enumerate"累计"题(L1 无该能力);W3 在闸侧兜底语义错配 |
| **Q07** 计数题答人名(答案类型与问题类型不符) | 类型错配 | **W3**(类型/能力相符) | 若提问坐标是计数(期望数值),gt 却是人名(`_to_num` 抽不出且非数值型)→ drop。逐能力的 W3 断言每个能力 gt 的合法形状(IE/KU=单值、MR/TR=带 session 的 dict、FORGET=带 forgotten),把"答非所问"挡在 phrase 前 |

> Q06/Q07 的更彻底治理在 enumerate(L1 根本不该出"累计计数"题、出题意图不该把计数问法配人名 gold);W3 是**闸侧通用兜底**:任何"gt 形状/数值性与能力承诺的答案类型不符"的 order 都 drop,不依赖具体题号。

---

## 6. 反补丁自审(逐条论证为通用不变量、非特判)

- **W1(重算==烘焙)**:对**任意** IE/KU/MR/TR/PREEXPIRE order 成立——用当前世界按能力 gt 函数重算,与烘焙逐字段比。drop 的是"gt 与世界不一致"这一**类**(陈旧烘焙、错算、CRITIC 改世界后未重烘焙),Q19 只是其一实例。可证伪:造一个 gt.value 故意改错的 order,必被 drop;造一个正确的,必通过。
- **W3(类型/能力相符)**:对每个能力断言 gt 的**结构契约**(不是"if gold==某人名")。drop 的是"答案类型与提问类型错配"这一类(Q06 累计配快照、Q07 计数配人名)。可证伪:把任一能力的 gt 换成错误形状(KU 给 dict、MR 给裸标量),必被 drop。
- **W4(IE 周锚结构 fail-closed,实测下调)**:对**任意** IE order——at_week 必须是合法 session 索引、与 gt.at_week 同源。**不再宣称"治 Q01 off-by-one"**(实测证伪:闸看不到题面文字,见 §6 worked #2);它现在只是"at_week 结构没坏"的兜底(实测 0 触发)。**不再对 KU 查 at_week**(KU 的 at_week 不上题面,非良定义条件)。可证伪:把 IE at_week 设成越界/非整数/与 gt.at_week 不符,必被 drop。
- ~~**W5(极值唯一)**~~ **【实测证伪撤下,见 worked #1】**:量的是"极值数值是否多 session 并列(连接度)",而 L1 MR 题面问"**值**"(唯一),**平手 ⊥ gold 歧义** → 它 drop 的全是好题。实测 **误杀 10.3%(20/194)、真覆盖 0**。与 L2 `roles_of≥2` 同型的症状相关性代理,删除(留着才是补丁)。
- **W6/W7/W8(变更/停用/缺失成立)**:分别对 TR/FORGET·PREEXPIRE/ABS——查"变更/停用/缺失"这件事在世界里是否**物理成立**。drop 的是"问了个世界里没发生的事"这一类(无变更却问首变、无停用/停后复活却问遗忘、有字段却问缺失)。**实测 W7 在 run190554 抓到 1 道真坏 FORGET(停-复活)**,W1 抓不到 → 真增量。全部可证伪:造对应的反例世界即触发。
- **共性**:每条留下的检查都是"`f(order意图, ws)` 的一个布尔谓词",drop **所有**违背者,不含任何题号/具体值的硬编码。与接地闸同构(接地闸弃**任何**不接地的 gold,本闸弃**任何**不良定义的 gold)。

### §6.1 worked example #1 —— W5(极值平手)实测证伪撤下(L1 版 `roles_of`)

> **看着像不变量、实测却 0 真覆盖 / 高误杀的检查,该删**。判据【查世界 + 不含字面量】是**必要非充分**;还得过"它度量的量真的 ≡ 不良定义吗 + 实测误杀/覆盖"这关。

- **它度量的量**:取得极值的数值在几个 session 并列(session 多重性)。
- **题面真实需求**:`intent()` MR = "问…最高/最大**是多少**" → 答案是**值**,不是周。
- **错位(核心论证)**:平手只让 `gt.session` 不唯一,但**值唯一**;读者答 `r.value` 即对,与有几周并列**正交**。`gt_mr` 的 `max/min(key=…)` 在平手时确定性取首个 session(实测可复现),`gt.session` 仅供边 B 接地锚证据——而并列周都陈述该值,锚任一周接地都成立。⟹ **"W5 触发" ⊥ "gold 错"、⊥ "不可接地"**。
- **实测(office 当前世界,194 个 MR 候选 / 备用 72 个)**:`W5` drop 掉 **10.3%(20/194,备用 5.6%=4/72)**——例:`基础架构组.SLA` 序列 `99.9/99.8/99.7/99.9/…99.9`,max=99.9 在 s0/s3/s7/s10 四周并列,但"SLA 最高是 **99.9**"答案唯一、完全良定义,W5 却砍掉它。20 个平手候选**逐一核**:distinct 归一值恒 = 1(值唯一)、`gt_mr` 重算确定、接地不受影响 → **真歧义抓到 0**。
- → **W5 是症状相关性代理(伪不变量),撤下**。**保留扩展位**:未来若出"问哪周最高"MR-which 子型(答案=周),平手才构成歧义,届时**按 `aux["ask"]=="which"` 子型**重引入,而非对"问值"子型一刀切。

### §6.2 worked example #2 —— W4"治 off-by-one"承诺实测证伪(降为结构兜底)

- **原承诺**:W4"治 Q01 off-by-one(题面'第N周' ↔ 世界坐标错位)"。
- **实测厘清**:off-by-one 的病灶在 `intent()` 渲染——`复盘第 {at_week} 周` 把 session 2 渲成"第2周",而人读 session 2(日期 01-20)应是"第3周"。**但这是【题面文字 ↔ 世界坐标】错位,题面文字不进 order dict**;闸只拿到 `at_week=2 + gt.value`,而 `gt_ie(ws,…,2)` 与烘焙 `gt.value` **同源**(都把 at_week 当 session 索引),W1 必过、W4 的合法性检查也必过。**实测**:当前 7 道 IE 的 at_week ∈ [0,13)、gt.at_week 全一致 → W4 **0 drop**;它对 off-by-one **完全不可见**。
- **结论**:off-by-one 治本只能在**命门1 全局单一周编号真源**(`intent()` 统一 `week_label(s)=s+1`),不在边 A 闸。W4 **降级**为"at_week 结构合法(int+在界)+ 与 gt.at_week 同源"的 fail-closed(实测纯兜底,0 触发),并**收窄到 IE-only**(KU 的 at_week 不上题面,卡它是 over-reach)。**留此档防后人把"off-by-one 治理"重新塞进边 A 闸**——闸看不到渲染,接不了这个症状(与 L2 把"读者绕晕"误塞 INV-4 是同类 category error)。

---

## 7. 自检用例(造小 WorldState + 断言,离线可跑)

复用 `build_demo_world()`(AI工程部:P0缺陷率 2.5→1.8→2.8→EXPIRE;Oncall 12→15→9;负责人 张三→李四;部门代号 ENG stable;absent=季度营收/团队规模)。建议在 `pipeline/well_posed.py` 末尾仿 grounding 的 `_self_test()` 写:

**well-posed(应 PASS,返回 `("well_posed","")`)**
1. **IE 负责人@s0=张三**:`order={cap:IE, ent:AI工程部, fld:负责人, gt:{value:"张三",at_week:0}, aux:{at_week:0}, evidence_sessions:[0,1]}` → well_posed(重算 value_at_session(0)=张三,at_week 合法且在证据)。
2. **MR Oncall max=15@s2**:`gt={value:"15次",session:2,agg:"max"}, aux:{agg:max}, ev:[0,2,4]` → well_posed(重算一致)。
3. **PREEXPIRE P0 停前=2.8%**:`gt:"2.8%", aux:{expire_session:5}, ev:[3,5]` → well_posed。
4. **★MR 平手不再误杀(撤下 W5 的反例)**:小世界 `Oncall 10→15→15`(s0/s1/s2,s1=s2=15),`MR max gt={value:"15次",session:1,agg:max}` → **well_posed**(平手但值唯一;证明撤下 W5 不再误杀)。**对照**:gt.value 改成 "16次" → drop(W1)。

**ill-posed(应 drop,断言 reason 含对应关键词)**
5. **Q19 复刻——MR gt 错算**:同 #2 但 `gt.value="16次"` → drop,reason 含"gt 与世界不符"(W1)。
6. **IE 周锚结构坏——越界/不一致**:同 #1 但 `aux.at_week=9`(越界,n_sessions=6)→ drop,reason 含"周锚越界"(W4);或 `gt.at_week=1` 而 `aux.at_week=0` → drop,reason 含"周锚不一致"(W4)。**注**:这查的是 at_week 结构,**不是** off-by-one(后者闸不可见,见 §6.2)。
7. **ABS 不成立——字段其实存在**:`order={cap:ABS, fld:"负责人", gt:INSUFFICIENT}` → drop,reason 含"ABS 不成立...在世界存在"(W8);对照 `fld:"季度营收"` → well_posed。
8. **FORGET 停-复活(实测真坏题型)**:字段 `s0 EXPIRE 后 s1 复活到有效值`,gt 写 `forgotten=True` → drop,reason 含"FORGET 时效不符"(W7,latest_valid 有效 ≠ forgotten)。
9. **TR 无变更**:稳定字段 `部门代号` 走 TR → drop,reason 含"首次变更不成立"(W6)。
10. **FORGET 无停用**:对未 EXPIRE 的"负责人"造 FORGET order → drop,reason 含"停用不成立"(W7)。

**离线自检方式**:`./venv/bin/python -m pipeline.well_posed`,断言 `npass==total`;并加一条"全 enumerate 穿闸不崩 + 全 well_posed"的烟测:`all(line.well_posed(o, ws)[0]=="well_posed" for o in line.enumerate(build_demo_world()))`(实测当前 office 28 单 100% well_posed、备用 26 单仅 1 道被 W7 drop)。与 grounding 自检同形,CI 可挂。

---

## 8. 与 ground() 的对称性 + 落地接线(供实现参考,本设计不落码)

- **对称点**:都是 per-line 方法、`(status, reason)` 返回、纯代码零 LLM、fail-closed(无法判定 = drop)。差异:`ground` 读 corpus、`well_posed` 读 ws。
- **stage 编排建议**:仿 `run_grounding`,新增 `run_well_posed(orders, ws) -> (kept_orders, report)`,在 `stage_orders` 末或新 `stage_well_posed`(依赖 [orders, world])跑,输出 `03b_well_posed_orders.json` + 报告(按 line/cap 的良定义存活率 + 逐条 drop 因),`stage_questions` 读 kept。报告字段对齐 grounding(overall/by_line/by_capability/n_dropped/drops)。
- **顺序**:well_posed(边 A,orders 后)→ phrase → 渲染 → grounding(边 B,corpus 后)。两闸正交、串联 = (A)+(B) 双边护城河。
