# run_factory_v2.py 拆分重构 · 设计方案

> **定位**:把 740 行、塞了 6 个子系统的 `run_factory_v2.py` 拆成单一关注点的模块,让它回归"薄装配点"。
> **手法**:和当初 `order_gen/constitution → lines/`、基建 `→ run.py` 同一套——**纯搬迁、函数自包含、self-test 兜底、零行为改变**。
> **本文是蓝图**;执行按 §5 步骤。

> **✅ 已落地(2026-06-05)**:`run_factory_v2.py`(740 行)已拆 → `world_gen.py`(85)+`render.py`(185)+`closed_loop.py`(154)+`factory.py`(261);`run_lines/prepare_lines`→`lines/__init__`、`_run_stage`→`run.py`(`drive` 改用它)。旧文件已删,`python -m pipeline.factory …` 为新入口。
> **★行为零改变(双重确认)**:① **逐字节**——`inspect.getsource` 老 26 函数 vs 新模块 26/26 byte-identical;`prepare_lines` 仅 def 名差;`build_to_target` 仅多一行惰性 import,余逐字不变;`LEAK_BANNED/ART/SCENARIOS/PHRASE_SYS/STAGES` 全等。② **测试**——lines 13/13、world_state 47/47、targetspec 9/9、closed_loop 23/23 自检全绿;离线 `--only input` 走通 drive→_run_stage 记账链。
> **★对 §3 的一处实现偏离**:断 `factory↔closed_loop` 环用了**惰性 import**(`build_to_target` 体内 `from pipeline.factory import stage_*, ART`)而非签名注入 `stages` 参数。理由:惰性 import 让 `build_to_target` **函数体逐字不变** → 行为可证零改变(byte-compare 直接过),比 DI 重写函数体更稳;代价是 `closed_loop` 运行时(非导入时)知道 `factory`,可接受。若日后要更纯,再换回 DI(廉价)。

---

## §0 病灶(为什么拆)
**行数不是病,关注点数才是**:`world_state.py` 599 行很健康(一个内聚的状态机引擎,单一关注点);`run_factory_v2.py` 740 行**不健康**——一个文件里挤了 **6 个互不相干的子系统**:

| 子系统 | 现有函数 |
|---|---|
| ① 世界生成(含 W.3 修复轮) | `_world_system` `build_world` |
| ② 媒介渲染 | `_corpus_system` `_filler_system` `_session_facts` `_tracked_blocklist` `_render_conflict_docs` `_chunk` `render_corpus` `phrase_questions` |
| ③ 产线派发 | `run_lines` `_prepare_lines` |
| ④ stage 包装 + 注册 | `stage_*`×8 `ART` `STAGES` |
| ⑤ 闭环控制 | `_orders_by_line` `_order_deficit` `_grow_for_supply` `_floor_status` `_run_stage` `build_to_target` |
| ⑥ 场景数据 + CLI | `SCENARIOS` `LEAK_BANNED` `_print_runs` `main` |

设计文档说它该是"工厂特定 stage 逻辑 + STAGES 注册 + CLI"——已远超。它正变成上帝文件,**而 `run.py` / `lines/` 当初抽出去就是为防这个角色**。
**好消息**:这 6 块函数**基本自包含**(`build_world`/`render_corpus`/`build_to_target` 互不调内部)→ 并置非纠缠 → 拆分低风险。

---

## §1 目标结构(单一关注点)
延续已有约定——**每个重 stage 的逻辑 = 自己的模块**(`central_office.py`=议会、`grounding.py`=B闸、`well_posed.py`=A闸已是此样),把剩下三块抽出去:

```
pipeline/
  run.py            基建(Run/Stage/drive/Tracer)  ＋ 收编 _run_stage(让 drive 与闭环共用一份记账)
  world_state.py    状态机引擎(不动)
  prompts.py        prompt 注册表(不动)
  central_office.py 议会 → 白皮书(不动)
  world_gen.py      ★新:世界生成 stage 逻辑(build_world + W.3 修复 + _world_system)        ~95 行
  render.py         ★新:文本渲染层(render_corpus + 助手 + phrase_questions + LEAK_BANNED)  ~190 行
  well_posed.py     边A闸(不动)
  grounding.py      边B闸(不动)
  targetspec.py     TargetSpec + invert_rate(不动)
  closed_loop.py    ★新:闭环旋钮控制(build_to_target + floor/deficit/grow/orders_by_line)   ~150 行
  lines/__init__.py 产线注册表  ＋ 收编 run_lines + prepare_lines(注册表级编排)
  factory.py        ★改名(原 run_factory_v2):薄装配点 = SCENARIOS + stage_*薄包装 + STAGES + CLI  ~230 行
```
> `render.py` 的关注点是连贯的:**把结构化产物渲染成自然语言文本**——世界事实→语料文档、订单意图→题面,都是 LLM 渲染。

净效果:**740 → factory ~230**(只剩"场景输入 + stage 包装 + 注册 + CLI"),其余落进 4 个单一关注点模块,与 central_office/grounding/well_posed 同构。

---

## §2 搬迁映射表(谁去哪)

| 现 `run_factory_v2` | → 目标模块 | 说明 |
|---|---|---|
| `_world_system` `build_world`(含 W.3 修复) | **`world_gen.py`** | 世界生成 stage 逻辑;leaf(只依赖 world_state/prompts/config) |
| `_corpus_system` `_filler_system` `_session_facts` `_tracked_blocklist` `_render_conflict_docs` `_chunk` `render_corpus` `phrase_questions` `LEAK_BANNED` | **`render.py`** | 文本渲染层;leaf(依赖 world_state/prompts/config/lines) |
| `run_lines` `_prepare_lines` | **`lines/__init__.py`** | 并入 `feasible_lines`/`dependency_graph` 旁 = 注册表级产线编排 |
| `_orders_by_line` `_order_deficit` `_grow_for_supply` `_floor_status` `build_to_target` | **`closed_loop.py`** | 闭环控制;`build_to_target` 改签名收 `stages` 参数(见 §3) |
| `_run_stage` | **`run.py`** | stage 记账上移,`drive()` 改用它 → 一份(消之前评审指出的两份拷贝) |
| `SCENARIOS` `ART` `stage_*`×8 `STAGES` `_print_runs` `main` | **`factory.py`**(改名) | 薄装配点 + CLI |

---

## §3 依赖层次(无环;化解 closed_loop ↔ stages 的循环)
```
        run.py · world_state.py · prompts.py · config.py            ← 基座
                          ↑
 world_gen · render · central_office · well_posed · grounding · targetspec · lines/   ← stage 逻辑叶子(互不依赖)
                          ↑
                   closed_loop.py                                   ← 依赖 run+targetspec+grounding;★STAGES 注入,不 import factory
                          ↑
                    factory.py                                      ← 装配:stage_*包装各叶子 + STAGES + 接 closed_loop + CLI
```
**唯一要小心的点**:现在 `build_to_target` 直接引用 `stage_world/stage_orders/…`(在 factory 里),而 `factory.main` 又调 `build_to_target` → 循环。
**化解 = 依赖注入**:`build_to_target(run, spec, stages, …)` 收 `STAGES` 作参数,按名查表跑(`_run_stage(run, stages_by_name[nm])`),不再硬引用具体 stage。→ `closed_loop` 不 import `factory`,环断开。`factory.main` 传 `STAGES` 进去即可。

---

## §4 命名(`run_factory_v2` 那个怪名)
- **`_v2` 是历史残留**(V10/V11 时期的版本号),早无意义 → 去掉。
- **`run_factory` → `factory.py`**:它就是"benchmark 工厂的装配点 + CLI 入口"。`run_` 前缀冗余(基建已叫 `run.py`,易混)。
- **CLI 入口**:`python -m pipeline.run_factory_v2 …` → `python -m pipeline.factory …`。
- **影响面(执行时一并改)**:① 本文件 docstring 的用法示例;② grep 全仓 `run_factory_v2` 的引用(docstring/docs/可能的 importer——注:`tools/monitor*` 故意硬编码 STAGE_ORDER 没 import 它,影响面小);③ docs 里的命令示例;④ 你的跑批脚本/习惯命令。

---

## §5 迁移步骤(每步 self-test 绿才进下一步,纯搬迁零行为改变)
1. **`world_gen.py`**:搬 `build_world` + `_world_system`;factory import 之。自检:同种子/同白皮书造世界产物对比不变(或离线 `--to world` 对比)。
2. **`render.py`**:搬 `render_corpus` + 6 助手 + `phrase_questions` + `LEAK_BANNED`。
3. **`lines/__init__.py`**:收编 `run_lines` + `prepare_lines`(改名去下划线);factory 的 `stage_orders/stage_world` 改调 `lines.run_lines/prepare_lines`。自检:lines 注册表自检仍绿。
4. **`run.py`**:`_run_stage` 上移;`drive()` 改用它(DRY)。自检:离线 `--only input` + 各 stage 记账(start_stage/mark)不变。
5. **`closed_loop.py`**:搬 `build_to_target` + 4 助手;`build_to_target` 加 `stages` 参数(DI);`factory.main` 传 `STAGES`。
6. **`factory.py`**:改名 + 瘦身(只留 SCENARIOS/ART/stage_*/STAGES/CLI;import 上述新模块);改 `python -m` 入口。
7. **善后**:grep 改所有 `run_factory_v2` 引用(docstring/docs/importer);删旧文件。
8. **全量验证**:三套 lines 自检 + world_state + targetspec + 导入链 + 离线冒烟(`--only input`/`--to world`)+ 闭环冒烟(`--min-questions` 小跑,可选真车)。

---

## §6 不变 / 风险
- **不动**:world_state / prompts / central_office / well_posed / grounding / targetspec / lines 各线实现。stage 序列、产物格式、CLI 行为全不变。
- **behavior-preserving**:纯搬迁 + DI;函数自包含 → 低风险,self-test 是安全网(和 lines 抽取同款:逐字段/产物对比 + 全自检绿才算过)。
- **风险点**:① closed_loop↔stages 循环(§3 已用 DI 化解);② 改名的 blast radius(§4,grep 一遍即清);③ `render.py` 把 corpus 渲染 + 题面 phrasing 并一个模块——若日后觉得该分,再拆 `phrasing.py`(廉价)。
- **收益**:factory 回到 ~230 行薄装配点;L4/L6/L7 + 难度 + L2 世界 multi-agent 再落地时,各进自己的叶子模块,**factory 不再膨胀**。
