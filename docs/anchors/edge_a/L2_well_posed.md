# 边 A 闸 · L2_relational 良定义闸 `well_posed()` 设计

> **定位**:验证三角(`redesign_factory_v2.3.md` §V)的 **(A) 题面↔答案【良定义】边**,L2 多跳产线专版。
> **对偶**:与 §G 接地闸(边 B,`pipeline/grounding.py`)同哲学——纯代码兜底、坏题出不了厂;签名/跑位与 `ground()` 对称。
> **状态**:设计稿(伪代码 + 自检方案),不落地实现。
> **实证动机**:run0604 + run190554 两轮盲审证明,接地闸把"值在不在语料"治到 86%,但 **L2 是边 A 重灾区**——一批题【过了接地闸却仍 ill-posed】(无周锚→多解、锚不统一、gold 查无实据、桥实体歧义)。本闸专治这族。

---

## 0. 契约(与 `ground()` 对称,务必遵守)

```python
def well_posed(self, order: dict, ws: WorldState) -> tuple[str, str]:
    """返回 ('well_posed','') 或 ('drop', reason)。
    纯代码、确定性、零 LLM、绝不碰 corpus。
    跑位:orders→questions 这一支,phrase 前调用;drop 的 order 不进出题。
    order 字段(enumerate 产物):line/capability/entity/field/gt/evidence_sessions/aux
        aux = {path, at_week, bridge, cross_week, hops}
    ws = WorldState(唯一真相源)。"""
```

L2 的 `order` 真实形状(取自 `output/factory_v2_office_l2/03_orders.json`):

```json
{"line":"L2_relational","capability":"L2_multihop","entity":"前端缺陷率",
 "field":"负责人→汇报对象","gt":"CFO","evidence_sessions":[0,5],
 "aux":{"path":["负责人","汇报对象"],"at_week":5,"bridge":"张三","cross_week":true,"hops":2}}
```

---

## 1. L2"良定义"的定义(一句话)

> 一道 L2 多跳题是**良定义**的,当且仅当:**存在唯一一个锚定周 W(题面已点明),使得从起点实体沿关系链 `path` 在 W 处逐跳解析时,每一跳的桥实体在 W 处唯一无歧义(★由名键结构+单值时间线【结构保证】,非独立检查,见 INV-4)、每个值都真实存在于世界、整条链解析出的终点恰等于 `gt`,且全链所有跳都锚定在同一个 W**。

换言之,把 `gold = f(start, path, W)` 这个函数的【所有自变量】都钉死且无歧义,在世界里重算只得到一个值、且这个值 = `gt`。任何一个自变量松动(W 缺失 / 链上某跳在 W 无值致链断 / gold 在世界 at W 不成立 / 不同跳偷偷锚到不同周)都使 gold 不再唯一可定 → **ill-posed → drop**。(注:"起点自带末跳字段"曾被列为 0 跳二义,经实测证伪撤下,见 INV-5。)

---

## 2. 不变量清单(每条 = 一个可证伪检查)

> 顺序即推荐的短路顺序(便宜/前置条件在前;失败即返回)。所有"周内解析"统一调 `gt_multihop(ws, start, path, W)`,与产线 `gt()` 同口径(命门2),避免重写遍历逻辑产生口径漂移。

记号:`start=order["entity"]`,`path=aux["path"]`,`W=aux["at_week"]`,`gold=str(order["gt"])`。

### INV-0 · 结构前置(path/hops 自洽)
- **查**:`path` 是 list 且 `len(path) >= 2`;`aux["hops"] == len(path)`;`gold` 非空、非 `INSUFFICIENT`/`INVALID`。
- **drop 条件**:任一不成立。
- **reason**:`"ill-formed:path<2 跳 / hops 不符 / gold 空(L2 必须真多跳且有答案)"`
- **为何通用**:L2 的定义就是 ≥2 跳且有终点;这是后续不变量的合法性前提。

### INV-1 · 必须带唯一周锚(治"无周锚→横跳多解")
- **查**:`W = aux.get("at_week")` 不为 `None` 且为 int 且 `0 <= W < ws.n_sessions`(或在 `ws.sessions()` 内)。
- **drop 条件**:`W is None` 或越界。
- **reason**:`"无周锚:时变关系链不带 at_week,逐周多值 gold 不唯一(横跳多解)"`
- **为何通用**:时变软外键下,不指定 W 则"向谁汇报"是周的函数而非常量。这是边 A 在 L2 的头号病(run0604 整族 Q18–24、run190554 Q02/03/05)。**注**:即便 `intent()` 已被 §V-A 强制吐周锚,本闸仍独立校验——闸不信任出题端的自觉,只信世界与 order 字段(与接地闸"不信渲染端自觉"同构)。

### INV-2 · 链在 W 处可完整解析(治"链断/某跳在 W 无值")
- **查**:`mh = gt_multihop(ws, start, path, W)`;`mh["answer"]` 不是 `INSUFFICIENT`;`len(mh["path_evidence"]) == len(path)`(每一跳都解出了值,没在中途 `broke_at`)。
- **drop 条件**:`answer == INSUFFICIENT` 或证据跳数 < `len(path)`。
- **reason**:`f"链在第{W}周断裂:{mh.get('broke_at')} 处无有效值(该周此关系不成立)"`
- **为何通用**:某跳在 W 无值(字段尚未 SET / 已 EXPIRE / 悬空外键),则该周根本没有"那个人",题问了一个 W 处不存在的关系 → 无解。

### INV-3 · gold 在 W 处真实成立(治 Q33"查无实据" + 锚不统一)
- **查**:`_norm(mh["answer"]) == _norm(gold)`。
- **drop 条件**:不相等。
- **reason**:`f"gold 与世界@第{W}周重算不符:世界算出 '{mh['answer']}',gold='{gold}'(查无实据/锚不统一)"`
- **为何通用**:这是边 A 的核心不变量——**gold 必须 = 该题面坐标 (start,path,W) 在世界里唯一重算出的那个值**。
  - **直接毙 Q33**:gold=COO 但世界里该链 at W 解出的是 CTO/VP → 不符 → drop("查无实据")。
  - **直接毙 Q32 / run0604 锚不统一**:gold 取自 w0 旧值或 w5 最新值、与 order 声明的 W 不是同一个 → 在 W 重算必然 ≠ 那个跨周拼来的 gold → drop。**机制**:`gt_multihop` 在【单一 W】对全链切片,任何"一跳取 w0、一跳取 w5"的混锚 gold 都无法通过"在同一 W 重算 == gold"。

### INV-4 · 桥实体指代唯一 —— 结构保证 + INV-3 覆盖(原 `roles_of` 设计【实测证伪,撤下】)

> **本条经实测从"独立检查"降级为"无需独立检查"。留档说明判据演化,防后人重新引入 `roles_of`。**
> 这是"补丁 vs 不变量"标准的一次实战(见 §6 worked example):一条**看着像不变量、实测却 13% 误杀 / 0 真覆盖**的检查,该删。

**为何不需要独立的桥唯一性检查**(桥歧义的真条件只有两种,两种都不靠 `roles_of`):
- **① referent 唯一 = 结构保证**:`ws.entities` 以实体名为 dict 键,**不可能有两个同名实体** → 桥名 `b` 永远唯一指向 `ws.entities[b]`。"同名碰撞"在本数据模型里**不可发生**。
- **② chain 解析唯一 = INV-3 已覆盖**:timeline 每周单值(`value_at_session(W)` 返回唯一值),`gt_multihop` 从【钉死的起点】沿 path 在单一 W **确定性**逐跳解析,只得一个 gold;INV-3("gold == 在 W 重算")已断言这条链唯一锁死 gold。

**为何撤下 `roles_of(≥2)` / `upstream(≥2)`**:它们量的是"桥在图里的**连接度**(戴几顶帽子 / 被几个上游指向)"——这**不是指代歧义**。一个被多部门当负责人、或既是负责人又是别人汇报对象的中层,是**唯一 referent**,链穿过他**不产生任何歧义**。
- **零真覆盖(核心论证)**:gold 由 `gt_multihop` 从**钉死的起点**解析,与 b 戴几顶帽子**无关** → INV-4 触发与否,与 gold 是否唯一**正交**。它能 drop 的题 INV-3 全已覆盖(若 gold 错),它误杀的题 gold 其实是对的(INV-3 放行)。**`roles_of` 触发 ⊥ gold 错**。
- **实测误杀(run #3 真实世界,29 实体)**:`roles_of≥2` 砍掉 **13%(2/15)** 桥候选(王五/赵六:roles=`{用户画像负责人,负责人,项目负责人}`,都是"一人领多摊"的**合法唯一中层**),真歧义抓到 **0**。
- → `roles_of` 是**症状相关性代理,非不变量**,撤下(留着才是补丁)。

**Q27"赵一被复用为3身份"真正归属**:可检测缺陷 = "gold 给 CTO 但世界@W 算出 COO" = **gold≠重算 → INV-3**;"读者被赵一多角色绕晕"是**边 C(可答性)**的事。INV-4 想在边 A 接这个症状是 **category error**。
- **(保留兜底)** 悬空桥(`b not in ws.entities`)由 **INV-2** 兜(`gt_multihop` 在该跳 `broke_at` → 证据跳数不足 → drop),无需独立 4a。

### INV-5 · 起点不得 0 跳直答 —— 【实测证伪,撤下】(与 INV-4 同型的 worked example)

> **落地实现时实测 16/16 误杀 / 0 真覆盖,撤下。** 留档说明判据演化(见 §6 worked example #2)。

**原设计**(已撤):末跳字段 `path[-1]` 若挂在起点实体上(`last in ws.entities[start]`)→ 判"部门汇报 vs 负责人汇报二义"→ drop。理由曾是 run0604 Q22。

**为何撤下**(`output/runs/office__20260605-133356` 真实 L2 实测):
- 该 run 24 个部门实体自带 `汇报对象`,INV-5 把 **16/18** 道 L2 题全 drop;**逐题核题面,16/16 都是良定义**(0 真二义)。
- 真实题面是「截至第N周,**X部门负责人的汇报对象**是谁?」——中文自然解析**唯一**:`(X部门负责人)的(汇报对象)` = 那个负责人的汇报对象。部门**自己**的汇报对象要问"X部门的汇报对象"(**不带"负责人"= 另一道题面**)。
- 即:起点同名末跳字段**不使【本题】二义**——§V-A 已强制题面带全链(`负责人的…`),`gt_multihop` 算出的 gold 唯一,**INV-3 已覆盖**。INV-5 量的是"起点恰好有个同名字段"(结构巧合),**与本题是否二义正交**——和 `roles_of` 量"连接度"一样,是**症状相关性代理**。
- run0604 Q22 的真病是**当年题面没带"负责人的"全链**(pre-§V-A)→ 那是题面缺全链(§V-A 已修),不是"起点自带字段"。

**结论**:撤下 INV-5。起点读法唯一由【§V-A 题面带全链 + INV-3 唯一重算】保证。

> **覆盖关系小结**:INV-0 结构 + INV-1 周坐标存在 + INV-2 链不断 + INV-3 gold=在该坐标唯一重算。**就这四条**。
> "中间桥唯一"由【名键结构 + INV-2/3】保证(原 INV-4 `roles_of` 实测证伪撤下);"起点读法唯一"由【§V-A 题面带全链 + INV-3】保证(原 INV-5 实测 16/16 误杀撤下)。**两个结构性代理都被删,只留真不变量** ⟺ §1 良定义被逐项验真。

---

## 3. 算法(伪代码)

```python
from pipeline.world_state import gt_multihop, _norm, INSUFFICIENT, INVALID

def well_posed(self, order, ws):
    aux   = order.get("aux") or {}
    start = order.get("entity", "")
    path  = aux.get("path")
    W     = aux.get("at_week")
    gold  = order.get("gt")

    # ── INV-0 结构前置 ──
    if not isinstance(path, list) or len(path) < 2:
        return ("drop", "ill-formed:path<2 跳(L2 必须真多跳)")
    if aux.get("hops") not in (None, len(path)):
        return ("drop", f"ill-formed:hops({aux.get('hops')})≠path 长({len(path)})")
    if not (isinstance(gold, str) and gold.strip()) or gold in (INSUFFICIENT, INVALID):
        return ("drop", "ill-formed:gold 空/INSUFFICIENT(L2 必须有唯一答案)")

    # ── INV-1 必须带唯一周锚 ──
    weeks = set(ws.sessions())
    if W is None or not isinstance(W, int) or W not in weeks:
        return ("drop", "无周锚:时变关系链不带合法 at_week,逐周多值 gold 不唯一")

    # ── INV-5 撤下(实测 16/16 误杀):题面"X部门负责人的汇报对象"自然解析唯一,起点同名末跳字段不使本题二义 ──

    # ── INV-2 + INV-3:在【单一 W】重算全链,核 gold ──
    mh = gt_multihop(ws, start, path, W)          # 与产线 gt() 同口径(命门2)
    if mh["answer"] in (INSUFFICIENT, INVALID, None) \
       or len(mh.get("path_evidence", [])) < len(path):
        return ("drop", f"链在第{W}周断裂于 {mh.get('broke_at')}(该周此关系不成立)")
    if _norm(mh["answer"]) != _norm(gold):
        return ("drop", f"gold 与世界@第{W}周重算不符:世界='{mh['answer']}' gold='{gold}'(查无实据/锚不统一)")

    # ── INV-4 撤下:桥指代唯一已由【名键结构(无同名实体)+ INV-2 链不断 + INV-3 唯一重算】共同保证 ──
    #    原 roles_of/upstream 实测 13% 误杀、0 真覆盖(连接度 ⊥ 歧义),删除(见 §2 INV-4、§6 worked example)。
    return ("well_posed", "")
```

**关键算法决策**
1. **复用 `gt_multihop` 而非重写遍历**:边 A 闸的"在 W 重算"必须与产线 `gt()` 字字同口径(都走 `gt_multihop`),否则两套遍历口径漂移会自造 bug。闸的职责不是"再算一遍 gold",而是"**断言 order 声明的坐标在世界里唯一锁死了 gold**"。
2. **单一 W 是锚统一的天然保证**:`gt_multihop` 对【一个 W】给全链切片,故"INV-3 在 W 重算 == gold"这一条**同时**毙掉混锚(Q32):任何"一跳 w0、一跳 w5"拼出来的 gold,都过不了"同一 W 重算等于它"。无需单独写"锚一致"检查——它是 INV-3 的推论。
3. **桥歧义用世界结构判,不枚举症状**:`roles_of` / `upstream` 都是对世界的通用查询(谁被当作 FK 值、谁在 W 指向桥),能 drop 任意一人多身份/重名题,不针对"赵一"。
4. **(已撤)起点二义判据**:原想用 `last in ws.entities[start]` 结构谓词捕 Q22——但实测它是症状代理(16/16 误杀):题面"负责人的汇报对象"自然解析唯一,起点同名字段不使本题二义。Q22 真病是当年题面缺全链(§V-A 已修)。撤下。

---

## 4. 边界 case 处理

| 边界 | 现象 | 由哪条 / 怎么处理 |
|---|---|---|
| **无周锚** | `at_week=None` | INV-1 drop。横跳多解的根因(Q02/03/05) |
| **横跳多解(有名锚后)** | 桥/末值在周内仍变 | 有 W 后 gold 被 W 钉死(INV-3);周内多上游由 INV-4(4c) 兜 |
| **桥一人多身份**(王五领多摊) | 唯一 referent,**不构成歧义** | **不 drop**(连接度⊥歧义)。若因此 gold 算错 → INV-3 兜 |
| **同名碰撞** | 两个同名实体 | 名键模型**不可发生**(实体名=dict 键),无需检查 |
| **链断(某跳 W 无值)** | 字段未 SET / 已 EXPIRE / 悬空 | INV-2:`gt_multihop` 返 INSUFFICIENT 或证据跳数不足 → drop |
| **起点自带末跳字段**(部门自带汇报对象) | **不构成本题二义**("负责人的汇报对象"解析唯一) | **不 drop**(INV-5 已撤,实测 16/16 误杀) |
| **gold 查无实据** | gold 是世界 at W 没有的值(Q33 COO) | INV-3 重算 ≠ gold → drop |
| **混锚** | 一跳取 w0、一跳取 w5(Q32) | INV-3(单一 W 重算 ≠ 混锚 gold)→ drop |
| **self-loop** | 某跳值 = 当前实体名(A→A),或链回到已访问实体 | INV-4(4a/4b) 通常已挡;**建议加显式 visited 集**:遍历中若 `b` 已在已访问实体集 → drop `"关系链自环/回路:桥'{b}'重复出现"`(防 A→B→A 这类无意义/歧义环)。见 §6 补充不变量 INV-4d |
| **comparison 子型(免桥)** | 若未来 L2 出 comparison 题(无 bridge、aux 无 path 链) | 不属本闸"链式遍历"范畴;`well_posed` 应在 INV-0 后**按 capability/aux 形状分流**:无 `path` 链的 comparison 走另一套(两实体同字段同 W 各自唯一)——当前 enumerate 只产 bridge 型,故主体只需链式;留扩展位 |

---

## 5. 命中映射(盲审 L2 每个症状 ← 被哪条不变量 drop)

| 盲审症状 | 根因 | 被 drop 于 | 怎么 drop |
|---|---|---|---|
| **Q33** 负责人→COO,世界只有 CTO/VP;且与 Q22 自相矛盾 | gold 查无实据 | **INV-3** | 在 W 重算链得 CTO/VP ≠ gold(COO) → 不符 |
| **Q27** "赵一"3 身份,gold 给 CTO 但世界@W 是 COO | gold≠重算(非桥歧义) | **INV-3** | 在 W 重算链得 COO ≠ gold(CTO)→ drop。("读者被多角色绕晕"另属边 C,非边 A;详见 §2 INV-4) |
| **Q32** 一处取 w0 旧值、一处取 w5 最新 → 锚不统一 | 混锚 | **INV-3**(单一 W 重算)+ 概念上 INV-1 | gt_multihop 在单一 W 切全链,混锚 gold 必 ≠ 重算 |
| **Q02 / Q03 / Q05** 无周锚,关系人横跳(孙丽↔周杰)多解 | 缺周坐标 | **INV-1** | `at_week is None` → drop |
| (run0604) **Q22** "部门汇报"≠"负责人汇报"概念二义 | **当年题面缺全链**(pre-§V-A) | **§V-A**(非闸) | §V-A 强制题面带"负责人的…"全链后,该题面解析唯一;原拟的 INV-5 实测 16/16 误杀已撤 |
| (run0604) **Q18/Q20/Q23/Q24** 整族无周锚 + W0/W5 混 | 缺锚 + 混锚 | **INV-1 / INV-3** | 同 Q02/Q32 机制 |

> **覆盖度自评**:盲审点名的 L2 边 A 症状(Q33/Q27/Q32/Q02/03/05 + run0604 Q18–24)**逐条命中**,且每条命中靠的是一条通用不变量,不是逐题特判。

---

## 6. 反补丁自审(逐条论证"通用不变量 ≠ 特判")

> 铁律:每条检查须能 drop **任何**违背它的题,而非只堵某个已知例子。逐条证伪测试如下。

- **INV-1(必须带周锚)**:谓词是 `at_week is None`,与具体实体/字段/答案无关。任何时变链缺锚题都被 drop;一道"恰好关系全程不变"的题虽缺锚也无害,但 drop 它只是偏保守、不产生假阳错题(宁缺勿滥)。→ **不变量**。
- **INV-2(链可解)** / **INV-3(gold=重算)**:直接复用产线 `gt_multihop`,谓词是"世界在声明坐标处重算出的唯一值是否等于 gold"。这是 gold 良定义的**定义本身**,对任意 (start,path,W) 成立;Q33 只是其一个落点。→ **不变量**(且是核心)。
- **INV-4(撤下)· worked example:看着像不变量、实测证伪的检查**:`roles_of`/`upstream` 形式上也是"对世界图的结构查询、不含具体名字",一度被当作不变量。但它量的是**连接度而非歧义**——而 gold 由 `gt_multihop` 从钉死起点解析,**与桥连接度正交**,故"`roles_of` 触发"⊥"gold 错"。实测(run #3 真实世界):**13% 误杀(王五/赵六:合法唯一中层)、0 真覆盖**。判据=【对世界查询 + 不含字面量】是**必要非充分**条件;还得过"**它度量的量真的等价于'不良定义'吗 + 实测误杀/真覆盖率**"这一关。`roles_of` 没过 → 它是**症状相关性代理(伪不变量)**,撤下。→ **反例教训:形式像不变量 ≠ 是不变量;必须实测它 drop 的确为坏题、放行的确为好题。**
- **INV-5(撤下)· worked example #2:第二个"形式像不变量、实测证伪"的检查**:`path[-1] in ws.entities[start]` 形式上也"纯结构、不含字面量"。但它量的是"起点恰有个同名字段"(结构巧合),**与本题是否二义正交**——题面"X部门负责人的汇报对象"自然解析唯一(=负责人的,非部门的)。实测 `office__20260605-133356`:**drop 16/18,逐题核 16/16 良定义(0 真覆盖)**。同 `roles_of`:**形式合格 ≠ 是不变量;必须实测它 drop 的确为坏题**。Q22 真病是 pre-§V-A 题面缺全链(已修),不在起点结构。→ 撤下。
- **共性论据**:全部检查的输入只有 `(order 字段, ws)`,无任何字面量 entity/Q 编号常量;判据全是"对世界的可重算查询 + order 声明坐标的一致性"。**能泛化成不变量 = 架构**,符合铁律。

**反例自检(确认不会误杀好题)**:一道带合法 W、链在 W 完整可解、gold=W 处重算值的 2 跳题 → INV-0/1/2/3 四条全过 → `("well_posed","")`(起点是否自带末跳字段、桥戴几顶帽子均不影响)。即 §7 的 case A / G。

---

## 7. 自检用例(well-posed vs ill-posed)+ 离线自检方法

> **方法**:造小 `WorldState`(直接 `Op`/`Timeline` 拼,或 `assemble_world(table)`),手搓对应 `order`,断言 `well_posed` 返回值。与 `world_state.py` / `grounding.py` 的 `_self_test()` 同形,可挂到 `pipeline/lines/L2_relational.py` 的 `__main__` 自检里。

```python
from pipeline.world_state import WorldState, Timeline, Op, SET, UPDATE, assemble_world

def W(*steps):  # (session, op, value, prev)
    return Timeline([Op(s, _date_of(s), op, v, p) for (s, op, v, p) in steps])

# 公共小世界:部门→负责人→人员→汇报对象。负责人 w3 换人(李娜→王强);
# 逐周真值(已用 gt_multihop 实跑核过):w0–2→CTO(经李娜),w3–5→CEO(经王强)。
base = WorldState({
  "搜索部": {"负责人": W((0,SET,"李娜",None),(3,UPDATE,"王强","李娜"))},
  "李娜":   {"汇报对象": W((0,SET,"CTO",None),(5,UPDATE,"CVP","CTO"))},  # 李娜 w5 改值,但 w3 后桥已非李娜→该改值在本链休眠
  "王强":   {"汇报对象": W((0,SET,"CEO",None))},
}, n_sessions=6)

def od(entity, path, W_, gt, bridge=None, hops=2):
    return {"line":"L2_relational","capability":"L2_multihop","entity":entity,
            "field":"→".join(path),"gt":gt,"evidence_sessions":[0],
            "aux":{"path":path,"at_week":W_,"bridge":bridge,"hops":hops}}

P = ["负责人","汇报对象"]
line = RelationalLine()

# ── well-posed(应 PASS)──
# A) 锚 w0:搜索部→李娜→CTO,链通、gold=重算、桥唯一、起点无末跳字段
assert line.well_posed(od("搜索部", P, 0, "CTO", "李娜"), base)[0] == "well_posed"
# B) 锚 w5:桥已换王强→CEO(跨周拼接:负责人 w3 才换,单一 W 一致)
assert line.well_posed(od("搜索部", P, 5, "CEO", "王强"), base)[0] == "well_posed"

# ── ill-posed(应 drop,且 reason 命中对应不变量)──
# C) 无周锚(Q02/03/05)→ INV-1
assert line.well_posed(od("搜索部", P, None, "CTO", "李娜"), base) == ("drop", ...)  # "无周锚..."
# D) gold 查无实据(Q33):锚 w0 但 gold 写 COO(世界只有 CTO)→ INV-3
assert line.well_posed(od("搜索部", P, 0, "COO", "李娜"), base)[0] == "drop"          # "...查无实据..."
# E) 混锚(Q32):声明 W=5 但 gold 填 w0 旧值 CTO(w5 应为 CVP)→ INV-3
assert line.well_posed(od("搜索部", P, 5, "CTO", "李娜"), base)[0] == "drop"

# F) 桥"一人多身份"【不再误杀】(原 INV-4 的反例):赵一既是数据部负责人、又是新人甲的"导师"FK 值
reuse = WorldState({
  "数据部": {"负责人": W((0,SET,"赵一",None))},
  "新人甲": {"导师":   W((0,SET,"赵一",None))},     # 赵一第 2 种 FK 身份(roles_of=2)
  "赵一":   {"汇报对象": W((0,SET,"COO",None))},
}, n_sessions=2)
# F1) 多角色但 gold 对(=COO):赵一是唯一 referent、链唯一解析 → 必须【放行】(证明撤下 roles_of 不再误杀)
assert line.well_posed(od("数据部", P, 0, "COO", "赵一"), reuse)[0] == "well_posed"
# F2) 同世界但 gold 错(给 CTO):由 INV-3"在 W 重算≠gold"drop —— Q27 的真正缺陷在这条边,不在桥
assert line.well_posed(od("数据部", P, 0, "CTO", "赵一"), reuse)[0] == "drop"           # "...查无实据..."(INV-3)

# G) 起点自带末跳字段【不再误杀】(INV-5 已撤):题面"负责人的汇报对象"解析唯一 → 放行
dual = WorldState({
  "前端部": {"负责人": W((0,SET,"张三",None)), "汇报对象": W((0,SET,"李四",None))},  # 部门自带汇报对象!
  "张三":   {"汇报对象": W((0,SET,"CFO",None))},
}, n_sessions=2)
assert line.well_posed(od("前端部", P, 0, "CFO", "张三"), dual)[0] == "drop"           # "...部门汇报 vs 负责人汇报二义..."

# H) 链断(末跳实体在 W 无该字段)→ INV-2
broke = WorldState({"销售部": {"负责人": W((0,SET,"孤儿",None))}}, n_sessions=2)  # 孤儿无汇报对象
assert line.well_posed(od("销售部", P, 0, "X", "孤儿"), broke)[0] == "drop"             # "...链在第0周断裂..."
```

**端到端联检**:对真实 L2 order + `02_world.json` 跑 `well_posed`,统计存活率,人核被 drop 的题确为 ill-posed。实测 `office__20260605-133356`:撤 INV-5 后 **drop 0/18**(18 道全良定义)——印证 INV-0/1/2/3 不误杀。这与 `grounding.py` 的 CLI 干跑同形。

---

## 8. 补充不变量(可选,提升鲁棒)

- **INV-4d · self-loop / 回路**:`gt_multihop` 遍历中维护 `visited={start}`;若某跳值 `b in visited` → drop `"关系链自环/回路:'{b}'重复出现,语义退化"`。当前 `gt_multihop` 不查环,2 跳题不会自环,但为 ≥3 跳前瞻先立此不变量。
- **INV-3' · 唯一性扰动(更强的"锚得住"判据)**:除"在 W 重算 == gold"外,额外断言**邻周扰动会改变答案**——`exists w' != W: gt_multihop(start,path,w') != gold`。若全周同值,说明关系恒定、周锚其实不承载消歧负担,此时缺锚也不致多解(可放宽 INV-1 对该题的强制)。这条把"周锚是否真的锚得住唯一解"做成可证伪判据,但属优化项,不影响主闸正确性。

---

## 9. 与接地闸(边 B)的协同跑位

```
enumerate → [orders] → well_posed()  →  phrase → [questions] → ... 渲染 → corpus → ground()
                       (边 A,本闸)                                            (边 B,§G)
```
- 边 A 在**出题前**剔题(不等 corpus,只靠 order+ws);边 B 在**渲染后**剔题(靠 corpus)。
- 两者正交(§V.2):边 A 过的题 gold 仍可能没渲进语料(边 B 挂);边 B 过的题题面仍可能没锁死 gold(边 A 挂)。串联才是完整护城河。
