# ProductionLine 抽象设计(工程层)—— §6 命门2 落地

> **定位**:工程层设计,服务 `redesign_factory_v2.md` §6("共用基类 `ProductionLine`,强制 `gt()` 抽象方法")。
> 配 `run_system_design.md` 的 **Stage×Line 矩阵**:那篇把"横轴 Stage(工序)"做成一等公民;本篇把"**纵轴 Line(能力/产线)**"做成一等公民对象。
> **一句话**:把每条产线散落四处的 **{世界基质、代码 gt、点菜枚举、出题意图}** 收进一个车间(`lines/Lx.py`),基类强制每个车间必须有 `gt()`(护城河)。**加 L3 = 新开一个文件。**
> **本文是蓝图**;落地靠 self-test 兜底(纯搬迁/重组,零行为改变,除 §8 标注的一处正确化)。

---

## §0 现状量准:4 个关注点散在哪(为什么要收)

| 关注点 | 现在住在哪 | 到 line 了吗 |
|---|---|---|
| **世界基质准备** | `run_factory_v2._augment_relations`(L2 加人员实体);L1 隐式 | ✗ 在编排层 |
| **代码 gt** | `world_state.gt_*`(gt_multihop/gt_mr/gt_forget…,44 自测) | △ 共享引擎,**该留** |
| **点菜枚举** | `order_gen._candidates`(L1 九能力)/`enumerate_l2_orders`+`discover_fk_paths`(L2) | ✗ 在 order_gen |
| **出题意图** | `run_factory_v2._ask_intent`(按 capability 分支) | ✗ 在编排层 |
| 派发 | `constitution.LineSpec.generator` + `line_for` | ✓ 已到位 |

**"已经接近" = 派发到位**(LineSpec 已是契约);**"差的" = 基质/枚举/出题三件散在 order_gen + 编排层,没归到 line。**

**类比**:每条产线现在是**散件**——图纸(LineSpec)在宪法、核心机床(gt)在 world_state、下料工序(枚举)在 order_gen、质检话术(出题)在编排层。重构 = 给每条线建一个**车间**装齐全套家伙;车间长(`ProductionLine` 基类)规定每个车间必须有 `gt()` 这台机床。

---

## §1 ProductionLine ABC 接口

```python
# lines/base.py
class ProductionLine(ABC):
    # ── 身份(= 现 LineSpec 字段,并进类属性)──
    id: str                  # "L1_timeline";中央议会照抄,变体由 line_for 兜
    title: str               # "时间线"
    memory: str              # 考什么记忆
    gt_substrate: str        # gt 基质描述(护城河,人读)
    implemented: bool = True

    # ── 工序方法(一条 line 穿过 3 个 stage)──
    def prepare(self, ws, profile) -> None:        # 【world stage】把本线需要的基质备进【共享世界】
        ...                                        # 默认 no-op;L2 override = 加人员实体(_augment_relations)
    @abstractmethod
    def enumerate(self, ws, target, wp) -> list[Order]:   # 【orders stage】点菜:选 (entity,field,cap,aux) 并烘焙 gt
        ...
    @abstractmethod
    def gt(self, ws, order) -> Any:                # ★强制:给 order 规格,用【代码】算出答案(护城河)
        ...                                        # enumerate 内部调它烘焙;L1 按 order.capability 派发
    @abstractmethod
    def intent(self, order) -> tuple[str, list]:   # 【questions stage】(提问意图, 须隐藏词表),取代 _ask_intent
        ...
```

**谁在哪调**(对齐 Stage×Line):
- `world` stage:`build_world` 后 `for line in active: line.prepare(ws)` —— 各线把基质叠进**同一个**共享世界(命门1)。
- `orders` stage:`for line in active: orders += line.enumerate(ws, target, wp)`。
- `questions` stage:`intent, hide = line_for(o["line"]).intent(o)` → 喂 `render("phrase.user", …)`。

---

## §2 lines/ 包布局 + 谁搬到哪

```
pipeline/lines/
  base.py            # ProductionLine ABC + Order dataclass + 共享 helper(generate_orders 配额采样 / _after 日期)
  L1_timeline.py     # class TimelineLine —— enumerate(=_candidates 九能力) / gt(按 cap 派发) / intent(L1 分支) / prepare(no-op)
  L2_relational.py   # class RelationalLine —— enumerate(=discover+enumerate_l2) / gt(=gt_multihop) / intent(L2) / prepare(=_augment_relations)
  __init__.py        # CONSTITUTION=[TimelineLine(), RelationalLine(), …] 实例注册表 + line_for/taxonomy_prose/implemented_ids/INVARIANTS
```

**搬迁映射表**(源 → 目标):

| 现在 | → 目标 | 形态 |
|---|---|---|
| `world_state.gt_*` | **不动** | 共享基质引擎,line 调它 |
| `order_gen.Order` | `lines/base.py` | 共享 dataclass |
| `order_gen._candidates`(L1 九能力) | `lines/L1_timeline.py` | → `enumerate()` 体 |
| `order_gen.enumerate_l2_orders`+`discover_fk_paths` | `lines/L2_relational.py` | → `enumerate()` 体 |
| `order_gen.generate_orders`(配额采样)/`_after` | `lines/base.py` | 共享 helper |
| `order_gen._self_test`(52 检查) | 拆到 L1/L2 各自 self-test | **测试随实现搬家** |
| `constitution.LineSpec` | 并进 `ProductionLine` 类属性 | **消失** |
| `constitution._gen_L1/_gen_L2` | → 各 line `.enumerate()` | **消失(变方法)** |
| `constitution.CONSTITUTION` | `lines/__init__.py` | line 实例表 |
| `constitution.taxonomy_prose/implemented_ids/line_for/INVARIANTS` | `lines/__init__.py` | 注册表函数 |
| `run_factory._augment_relations` | `lines/L2_relational.py` `.prepare()` | 方法 |
| `run_factory._ask_intent`(分支) | 拆到各 line `.intent()` | 方法 |
| `run_factory.run_lines` | `stage_orders`:`for line: enumerate` | 编排遍历 |
| `run_factory.build_world` 里 `_augment_relations` 调用 | `stage_world`:`for line: line.prepare(ws)` | 编排遍历 |
| `run_factory.phrase_questions` 里 `_ask_intent(o)` | `line_for(o["line"]).intent(o)` | 编排派发 |

→ **`order_gen.py` 与 `constitution.py` 双双溶解进 `lines/`,删除。**

**import 重指**(消费方就这俩):
- `central_office`:`from pipeline.constitution import taxonomy_prose` → `from pipeline.lines import taxonomy_prose`。
- `run_factory_v2`:`from pipeline.constitution import line_for` → `from pipeline.lines import line_for, CONSTITUTION`。
- 迁移时 grep `legacy/`、`tools/` 是否有 order_gen/constitution 的 importer(有则一并处理,legacy 归档代码可只标注)。

---

## §3 L1 多能力线 —— 唯一的皱褶,讲清楚

L1 是**多能力捆绑线**(IE/KU/TR/MR/FORGET/ORDER/PREEXPIRE/CONFLICT/ABS 九个),宪法 note 已写 ORDER→L3、CONFLICT→L5、ABS→L6 以后拆走。所以 L1 的三个方法**内部按 `order.capability` 派发**:

```python
class TimelineLine(ProductionLine):
    def enumerate(self, ws, target, wp):
        plan = {"IE":6,"KU":6,"TR":5,"MR":4,"FORGET":3,"ORDER":3,"PREEXPIRE":2,"CONFLICT":2,"ABS":3}
        return generate_orders(ws, plan)          # 内部 _candidates 按 cap 选 + 调 self.gt 烘焙
    def gt(self, ws, order):
        cap = order.capability
        if cap == "MR":   return gt_mr(ws, order.entity, order.field, order.aux["agg"])
        if cap == "FORGET": return gt_forget(...)
        ...                                        # 九能力 → 对应 world_state.gt_*
    def intent(self, order):                       # = _ask_intent 的 L1 那几支
        ...
```
L2 这种**单能力线**就是一根筋:`gt = gt_multihop`、`intent` 一个分支。**基类不禁止多能力线**——这是 as-built 的合理形态。

---

## §4 gt() 的形态 = 护城河(设计决策)

**决策**:`gt(ws, order)` 是**强制抽象方法**,语义 = "给定 order 的【问题规格】(entity/field/capability/aux,不含答案),用纯代码算出答案"。

- `enumerate()` 的职责 = **选哪些问题**(selection);`gt()` 的职责 = **算答案**(answer)。enumerate 选好规格后 `order.gt = self.gt(ws, order)` 烘焙。→ **gt 逻辑在 line 内单一来源,不重复。**
- **白送一个校验闸**:`assert line.gt(ws, o) == o.gt`(重算 == 烘焙)证明 gt 是代码可复现的、非 LLM 判分 —— 这正是护城河。L2 与 L1 取值类能力都成立。
- **诚实交底**:把 L1 的 `_candidates`(现在 selection 与 gt 计算交织)**拆成 selection(enumerate)+ answer(gt)**,是**重组而非纯搬迁**。安全网 = `order_gen` 现有 **52 个 gt 值自测**(逐能力断言 KU=李四、MR max@s2、L2 桥≠答案…);拆完这 52 条仍全绿,即证明每条 order 的 gt 逐字段没变。

---

## §5 迁移步骤(每步 self-test 绿才进下一步)

1. **`lines/base.py`**:`ProductionLine` ABC + `Order` + 共享 helper(generate_orders/_after)。自检:import OK。
2. **`lines/L1_timeline.py`**:搬 `_candidates`→`enumerate`,析出 `gt()`(按 cap),`intent()`(=_ask_intent 的 L1 支),`prepare`=no-op。**搬 L1 相关自测**。绿。
3. **`lines/L2_relational.py`**:搬 `enumerate_l2_orders`+`discover_fk_paths`→`enumerate`,`gt()`=gt_multihop,`intent()`(L2 支),`prepare()`=_augment_relations。**搬 L2 自测**。绿。
4. **`lines/__init__.py`**:`CONSTITUTION=[实例]` + `line_for/taxonomy_prose/implemented_ids/INVARIANTS`。自检:注册表不变量(implemented 必有 enumerate+gt)+ 鲁棒匹配 + L2 端到端(从 constitution 搬来)。绿。
5. **重指 import + 改编排**:central_office/run_factory 重指 `pipeline.lines`;`stage_world` 加 `for line: line.prepare(ws)`;`stage_orders` 改遍历 `line.enumerate`;`phrase_questions` 改 `line.intent`。
6. **删 `order_gen.py` + `constitution.py`**(grep 确认无其它 importer)。
7. **全链路验证**:三套 self-test 全绿(world_state 44/44、新 L1/L2/registry 自测、其它)+ 离线冒烟(`--only input`/`--to world`)+ **新旧 orders 对比**(可选:旧 `_gen_Lx` 产物存档 vs 新 `line.enumerate`,逐字段一致)+ 真车小跑可选。

---

## §6 不变 / 风险

- **不动**:`world_state.py`(世界模型 + gt_* 纯函数,共享基质引擎)、`prompts.py`、`run.py`、Stage 序列。
- **★一处正确化(非纯搬迁)**:`_augment_relations` 现在 `build_world` 内**无条件**按"≥2 person 字段"增强;搬进 `L2.prepare()` 后**仅当 L2 激活**才增强。这是**把基质绑定到产线激活**(本重构的本意),更正确,但对"有 2 person 字段却没激活 L2"的场景是个**小行为变化** —— 明确标注,不当纯搬迁宣称。(office/medical 都激活 L2,实测无差。)
- **风险**:L1 `enumerate↔gt` 拆分(§4)——52 个 gt 值自测兜底。
- **收益**:加 **L3 = 一个新文件**(见 §7),编排层/world_state/prompts 全不动。

---

## §7 收益验证:加 L3 = 一个新文件

```python
# pipeline/lines/L3_process.py —— 加一条线,就这一个文件
from pipeline.lines.base import ProductionLine, Order
from pipeline.world_state import gt_event_order      # 复用共享 gt 引擎

class ProcessLine(ProductionLine):
    id, title, memory = "L3_process", "过程序列", "事件序列/因果先后"
    gt_substrate = "事件日志 + 偏序(Kendall-τ)"
    def enumerate(self, ws, target, wp): ...          # 点菜:跨字段事件序列
    def gt(self, ws, order): return gt_event_order(...)# 护城河:代码偏序
    def intent(self, order): return ("把这些事按发生先后排序…", [..])
    # prepare 用默认 no-op(L3 不需额外基质)
```
然后 `lines/__init__.py` 的 `CONSTITUTION` 加一行 `ProcessLine()`,`implemented=True`。**编排、world_state、prompts 一行不改。** —— 这就是 §6 命门2 想要的"产线即插件"。
