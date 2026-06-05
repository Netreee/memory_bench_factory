# Run / Stage 系统设计(工程层)—— 与 redesign_factory_v2 算法对齐

> **定位**:本文是【工程 / 可观测性层】的设计,服务于 `redesign_factory_v2.md` 的【算法层】。
> 一句话分工:**算法定义"造 benchmark 要哪些步、每步算什么";本文定义"这些步怎么被驱动、产出怎么规整、怎么单独调试一步"。**
> **对齐铁律**:**工程的 Stage 序列 ⟺ 算法的数据流(redesign §1)**。每个算法环节 = 一个 Stage;一次"造一个 benchmark" = 一个 Run。
> **本文是蓝图**;落地只重构编排层(原 `run_factory_v2.py`,后已拆分改名为 `pipeline/factory.py` 薄装配点),各 stage 内部逻辑原样搬入(behavior-preserving)。

---

## §0 要解决的工程痛点(主理人指出)

1. **run 是 monolith**:`run_factory` 一次必跑全程(议会→世界→产线→出题→渲染),**不能"只跑前几步",也不能"单独重跑某一步"**。证据=我被迫写过 `council_smoke.py`、`phrase_l2.py` 这种一次性脚本去单跑一阶段——这就是症状。
2. **run 身份混乱**:`--tag` 手动、默认=scenario,**撞名**(office / office_v3 / office_l2 是我手动绕的);无 run-id、无时间戳、无"列出所有 run"。
3. **产出半规整**:per-run 目录有了(00–06 + prompts.jsonl + run.log + manifest),但**无 run-id/时间戳、无索引、stage 边界不显式**,找东西靠记。

---

## §1 ★工程 ↔ 算法 对齐表(全文核心)

算法数据流(redesign §1)逐环节映射到一个工程 Stage:

| 算法环节(redesign) | 工程 Stage | needs(读) | 产出 artifact | 算法上谁干(as-built) |
|---|---|---|---|---|
| 场景输入 | `input` | — | `00_input.json` | SCENARIOS[key] |
| §3 中央议会 → 白皮书 | `whitepaper` | input | `01_whitepaper.json` | `central_office`(6视角→代码装配→批判) |
| §5 共享世界 | `world` | whitepaper | `02_world.json` | `build_world` + `_augment_relations` |
| §6 多产线 → 订单 | `orders` | whitepaper, world | `03_orders.json` | `run_lines`(遍历宪法 active_lines,每线代码 gt) |
| §6 出题(LLM出题面) | `questions` | orders | `04_questions.json` | `phrase_questions`(两段式,桥实体不进题面) |
| §7 媒介渲染 → 语料 | `corpus` | world, orders | `05_corpus.json` | `render_corpus`(媒介 renderer + 逐周 checkpoint) |
| §8 四闸 + 完整性批判(未来 P5) | `validate` | corpus, questions | `06_gates.json` | (待实现) |
| §8 诊断 + 区分度测量(未来 P5) | `diagnose` | validate | `07_diagnosis.json` | (待实现) |

**对齐带来的关键性质**:
- **Stage 边界 = 算法 artifact 边界**(白皮书/世界/订单/题/语料)→ 每个算法中间产物**天生是一个可单独 inspect / 单独重跑的 stage 产出**。
- **算法长大 ⇏ Stage 序列变**:新增产线(L2–L7)= `orders` stage **内部**多遍历一条线;新媒介(对话/交易流)= `corpus` stage 换 renderer;诊断 = **追加** `validate`/`diagnose` 两个 stage。扩展**插进去**,不重写编排。

> **一处 as-built 说明**:算法 §6 把"产线出 (题, gt)"写成一体;as-built 里**出题面(`questions`)被拆成独立 stage**(在 `orders` 之后),因为题面是 LLM 润色、与代码 gt/订单解耦,单独成步才好单独重跑。

### §1.5 ★Stage × Line 二维心智模型(对齐的关键洞察)

算法里有两个**正交**的轴,工程必须分清:

- **Line(产线 / 能力)= 纵轴**:L1 时间线 / L2 关系多跳 / … / L7 摘要。算法 §10 的"产线准入清单"(① 世界子结构 ② 代码 gt ③ 四闸适配 ④ 媒介承载)说明**一条产线是横跨多道工序的**。
- **Stage(工序)= 横轴**:whitepaper / world / **orders** / questions / **corpus** / **validate**。

```
              orders(gt)   corpus(媒介承载)  validate(四闸)
   L1 时间线      ✓              ✓               ✓
   L2 关系多跳    ✓              ✓               ✓
   L5 冲突        ✓              ✓               ✓
   ……(白皮书激活哪几条,就点亮哪几行)
```

**所以**:`orders` / `corpus` / `validate` 这几个 stage 内部都是"**遍历白皮书激活的 active_lines**,对每条产线做本工序该做的事"。

- **加一条新产线(L3–L7)= 矩阵多一行**:`orders` 多算一条 gt、`corpus` 多承载一种媒介、`validate` 多套一组闸 —— **Stage 序列(列)不变**。
- 这就是"算法长大、Stage 序列不动"的精确含义:**算法在纵向(产线)扩张,工程在横向(工序)稳定**。
- 调试也据此定位:"L2 出的 gt 不对" → `--only orders` 看 03_orders;"L2 题面泄漏" → `--only questions`;互不干扰。

---

## §2 Stage 抽象

```python
@dataclass
class Stage:
    name: str
    needs: list[str]        # 依赖的前序 stage(artifact 名)
    fn: Callable            # fn(run) -> None:从 run 读 needs 的 artifact,写本步 artifact,记进度
    artifact: str           # 产出文件名(NN_<name>.json)

STAGES = [input, whitepaper, world, orders, questions, corpus]   # + 未来 validate, diagnose
```
- **stage = `fn(run)`**:`obj = run.read("world"); …; run.write("orders", orders)`;期间 `run.log(...)`、`run.tracer.chat_json(...)`。
- **依赖声明 `needs`**:驱动器据此校验"能不能从这步起跑"(缺前序 artifact 就报错并提示先跑哪步)。
- **幂等 = resume**:artifact 已存在且非 `--force` → 跳过。
- **DAG 而非纯链**:`questions` 与 `corpus` 都只依赖 `orders`(corpus 另需 world),二者**互不依赖**(可任意序/并行)。`needs` 表达这一点;驱动器默认拓扑序跑。

---

## §3 Run 对象(一次"造一个 benchmark"的全部状态)

```
run_id = "<scenario>__<YYYYMMDD-HHMMSS>"        # 唯一、可排序;--tag 仅人类标签
output/runs/<run_id>/
  manifest.json     # ★单一真相(见 §4)
  run.log           # 进度日志(已有的 tee logger)
  prompts.jsonl     # ★LLM 审计材料:每次调用的 system+user+出参(已有 Tracer)
  00_input.json 01_whitepaper.json 02_world.json 03_orders.json 04_questions.json 05_corpus.json
```
`Run` 统一管:目录、`logger`(tee→run.log)、`tracer`(→prompts.jsonl)、`manifest` 读写、artifact 读写(`run.read(name)` / `run.write(name, obj)` / `run.has(name)`)。**stage 只跟 Run 打交道**,不直接碰路径。

---

## §4 manifest.json schema(单一真相,工程 + 算法元数据都在)

```jsonc
{
  "run_id": "office__20260603-191200", "scenario": "office", "tag": "并行验证",
  "created": "2026-06-03T19:12:00", "status": "running|done|failed",
  "config": { "target_tokens": 200000, "from": "input", "to": "corpus" },
  "stages": {                                   // 工程:每步状态
    "whitepaper": { "done": true, "ts": "...", "elapsed_s": 41, "artifact": "01_whitepaper.json" },
    "world":      { "done": true, "elapsed_s": 95, ... },
    "corpus":     { "done": false }
  },
  "algo": {                                     // ★算法元数据:让 run 自描述到算法层
    "active_lines": ["L1_timeline","L2_relational","L5_conflict"],
    "medium": "documents", "entities": 23, "sessions": 10,
    "orders_by_line": {"L1_timeline":31,"L2_relational":42}, "questions": 73
  },
  "llm_calls": 312
}
```
→ `--list-runs` 扫所有 `manifest.json`,一行列出 `run_id / scenario / tag / status / active_lines / questions`,**找 run 不再靠记**。

---

## §5 CLI(驱动器)

```
python -m pipeline.factory --scenario office [--tag 实验名]
   [--from <stage>] [--to <stage>] [--only <stage>] [--force]
   [--run <run_id>]      # 在已有 run 上继续 / 重跑某步(不新开 run)
   [--target-mtokens 0.2]
python -m pipeline.factory --list-runs
```
- **默认**:新开 run,跑全程 `input→corpus`,跳过已完成 stage。
- `--to world`:只跑到世界(调试中央议会/世界生成用)。
- `--only questions --force --run <id>`:在某次 run 已有 orders 上**单独重跑出题** → **取代 `phrase_l2.py` 这种临时脚本**。
- `--from corpus --run <id>`:世界/订单都现成,只补渲染。

---

## §6 与算法对齐的"为什么"(逐条回应主理人三问)

| 主理人的问 | 本设计的答 |
|---|---|
| 一次 run 是跑整条 pipeline,还是能只跑前几步? | Stage 化 + `--from/--to/--only`:任意区间;单步可重跑(取代临时脚本) |
| 每次 run 的 tag 是什么? | `run_id=<scenario>__<时间戳>`(唯一可排序);`--tag` 人类标签进 manifest;`--list-runs` 可查 |
| 日志/LLM审计/各步留痕/中间产物 能否规整方便找? | 一次 run 一个目录:`manifest`(索引)+ `run.log` + `prompts.jsonl`(LLM审计)+ `NN_<stage>.json`(各步留痕),且 manifest 带算法元数据 |

---

## §7 落地范围 / 迁移步骤(behavior-preserving)

- **只动编排层**(原 `run_factory_v2.py`,现 `pipeline/factory.py`):monolith `run_factory` → `Run` 对象 + `STAGES` 注册 + 驱动循环;**5 段逻辑原样包进 stage 函数**(white­paper/world/orders/questions/corpus 各一)。
- **复用已有**:逐周 checkpoint(挪进 `corpus` stage 内)、tee logger、Tracer/prompts.jsonl、关系增强、并行 `pmap`。
- **不碰**:`central_office.py`(主理人在改)、`prompts.py` / `world_state` / `order_gen` / `constitution`。
- **迁移顺序**:① `Run` 对象 + manifest;② 5 段包成 stage;③ 驱动器 + CLI(--from/--to/--only/--list-runs);④ 自检:跑一遍全程产物 == 旧行为(byte/结构对比);`--only` 各 stage 可单跑;`--list-runs` 列得出。

---

## §8 与 redesign §10 路线图的关系(这套是 P2+ 的工程底座)

- **P3 媒介**:`corpus` stage 内多个 renderer(documents/对话/交易流),按白皮书 `medium` 选 → 不动 Stage 序列。
- **P4 加 L3–L7 产线**:`orders` stage 遍历宪法 `active_lines` 时多几条线 → 不动 Stage 序列。
- **P5 诊断 + 区分度**:**追加** `validate` / `diagnose` 两个 stage(needs=corpus/questions)→ 插进序列尾。
- 结论:**Stage/Run 抽象让算法的每一步扩展"插得进去",而不是每次都重写编排**——这正是"工程和算法对齐"的收益。

---

## 附:目录布局前后对比

```
现在(乱):
  output/factory_v2_office_v3/{00..06, prompts, run.log, manifest}   # tag 手动、撞名、无 run-id
  /private/tmp/factory_*.log                                          # 进度日志散落(已部分收进 run.log)

重构后(规整):
  output/runs/office__20260603-191200/{manifest, run.log, prompts.jsonl, 00_input..05_corpus}
  output/runs/office__20260603-203145/{...}                           # 同场景多次 run 各自带时间戳,不撞
  → --list-runs 一览;一次 run 的"全部"(配置/日志/LLM审计/各步留痕/产物)都在一个 run_id 目录
```
