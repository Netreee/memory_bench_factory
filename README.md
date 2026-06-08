# memory_bench_factory

**一句话:** 这是一个**记忆 benchmark 生成器(元工厂)**——给它一个场景 + 几条 few-shot,它端到端产出一整套用来**评测「带记忆的 LLM/Agent」**的题库(语料 + 带标准答案的问题),标准答案由**代码机械算**、不靠 LLM 判分。

它不是某一个 benchmark,而是「造 benchmark 的机器」:换个场景(办公/客服/医疗…)就能产出对应领域的评测题,域知识只从场景白皮书来。

---

## 核心理念(先记住这几条,代码处处在为它们服务)

1. **端到端生成 + 独立盲审闭环**:一次 run 从场景一路跑到成品题库;质量靠独立的盲审(对抗式 QA)迭代,不靠作者自评。
2. **对「补丁 / 打地鼠式修复」零容忍**:bug 要从**源头**修;给闸门打补丁只会让同一个 bug 换个波次复发(`docs/anchors/pitfalls_4lines.md` 记的就是这本血账)。
3. **代码可证伪 gt**:每道题的标准答案(ground truth)由 `world_state.py` 这台**会算账的真值状态机**机械算出,可单测、可复跑——评分不交给 LLM 拍脑袋。
4. **跨场景泛化**:产线逻辑与领域解耦;领域知识只从**白皮书**(由场景 + few-shot 生成)进入,所以同一套代码能产不同领域的题。

---

## 仓库地图

### `pipeline/` —— 主代码(产线)
一次「造一个 benchmark」= 一个 **Run**(落在 `output/runs/<id>/`),数据流是 8 个 **stage**:

```
input → whitepaper(议会) → world → orders → well_posed(边A闸) → questions → corpus → grounding(边B闸)
        [LLM]              [LLM]   [代码]   [代码]              [LLM]      [LLM]    [代码]
```

| 文件 | 职责(一句话) |
|---|---|
| `factory.py` | **薄装配点 + CLI**:把各 stage 串成 pipeline、注册 STAGES;给了 `--min-questions` 就走**闭环**(调 `closed_loop`)。**主入口。** |
| `lines/` | **5 条能力产线**(详见下表):每条线懂自己的题怎么点菜、gt 怎么算、怎么接地。 |
| `world_state.py` | **真值状态机(地基)**:每个「实体×字段」一条时间线(SET/UPDATE/DELETE/EXPIRE);各能力的 gt 全在此机械算。纯代码、可单测。 |
| `world_gen.py` | **共享世界生成**:LLM 填世界表 → 组装成状态机 → CRITIC 修复轮。 |
| `render.py` | **文本渲染层**:把结构化世界事实渲成语料文档、把订单意图渲成题面。 |
| `grounding.py` | **边 B 闸(接地)**:校验每道题的 gold 能否在它的证据文档里**逐字 + 就近**找到,找不到就弃题。纯代码、确定性。 |
| `well_posed.py` | **边 A 闸(良定义)**:出题前校验「题面↔答案」是否唯一正确解,剔掉 ill-posed 题。与边 B 正交、串联成护城河。 |
| `closed_loop.py` | **闭环旋钮 driver**:反推世界规模/配额 →①供给环→渲染→接地→②实测纠偏,直到产够目标题数。 |
| `central_office.py` | **中央办公室(并行议会)**:场景 + few-shot → 6 个视角并行 → 综合 + 批判 → **白皮书**(下游的领域宪法)。 |
| `targetspec.py` | **闭环 TargetSpec**:把「给 tokens、题数随缘」升成「给目标题数、反推世界规模」的纯代码率模型。 |
| `prompts.py` | 所有 LLM prompt 的**单一注册表 + 渲染层**(代码侧只调 `render("<stage>.<role>", ...)`)。 |
| `run.py` | **Run/Stage 通用基建**(域无关):manifest / run.log / 幂等跳过 / `--from/--to/--only`。 |

**5 条能力产线 `pipeline/lines/`**(加一条线 = 新增 `Lx.py` 子类 + 在 `__init__.LINES` 加一行,别处不动):

| 线 | 能力 |
|---|---|
| `L1_timeline.py` | 时间线:某时刻的值 / 最新值 / 跨周极值 / 首次变化时刻(IE/KU/MR/TR) |
| `L2_relational.py` | 关系多跳:沿软外键逐跳查值(multi-hop) |
| `L3_process.py` | 过程/排序:事件按时间排序 |
| `L4_preference.py` | 偏好 |
| `L5_conflict.py` | 冲突:相互矛盾的信息按时间锁定取舍 |

> `lines/base.py` = `ProductionLine` 抽象基类 + `Order` 契约(四件套 `prepare/enumerate/gt/intent`);`lines/__init__.py` = 产线注册表(唯一真源)。

### `docs/anchors/` —— 设计文档(读这里建立心智模型)
**推荐阅读顺序:**
1. **`pipeline_full_map.md`** —— 严格照代码写的完整全貌(8 stage + 每个 gt + 每处补丁),读完对系统有完整心智模型。**先读这篇。**
2. **`redesign_factory_v2.3.md`** —— 当前设计的权威版(v2/v2.1/v2.2 是演进史,看最新的 v2.3 即可)。
3. **`pitfalls_4lines.md`** —— 5 波盲审的「问责账」:每个 bug 怎么反复复发、最后怎么真死。理解理念②的最佳材料。
4. **`edge_a/L1_well_posed.md`…`L5_well_posed.md`** —— 各线良定义闸的判据(对抗式 QA 实测背书)。
5. 其余:`closed_loop_targetspec_design.md`(闭环)、`run_system_design.md`(Run 基建)、`benchmark_landscape.md` / `related_work.md`(竞品对标)、`00_INDEX.md`(全仓文档总索引)。

### `tests/` —— 离线自检
`closed_loop_selftest.py`、`incremental_render_selftest.py`。另外许多模块自带自检入口(见下「怎么跑」)。

### `eval/` —— 评测侧(消费产物)
`judge.py` / `memory_interface.py` / `multi_system.py` / `baseline_r1.py`:拿生成好的题库去**评测**记忆系统的脚手架。

### `survey/` —— 调研材料
按主题分目录(`memory_taxonomy` / `multiagent_synthesis` / `verifier_metrics` / `ingest_corpora` / `spec_refinement`)。**只有文字报告进 git**:各 `REPORT.md`、`全景报告.md`、各框架 `README.md`;**PDF 原文(~267M)被 .gitignore 排除**(需要的话各 PDF 文件名里都带 arXiv 号,可自行下载)。建议从各目录的 `REPORT.md` 读起。

### 历史遗留(**别删**,但不进 git / 共享时可忽略)
- `legacy/` —— 旧版本 cruft(`run_pipeline_v3..v10.py`、`stage_*.py` 等历史迭代)。已被 .gitignore 排除,本地保留备查,**当前产线一律以 `pipeline/` 为准**。
- `probe_l3_wp.py`(根目录)—— 一次性探针脚本。
- `调研沉淀.md`(根目录)—— 第一轮调研沉淀,结论已被 `docs/anchors/related_work.md` + `benchmark_landscape.md` 取代,留作历史。
- `tools/`(`monitor.py` / `monitor_web.py` / `diversity_metrics.py`)、`vis/`(架构图生成脚本)—— 辅助工具,非产线核心。

> ⚠️ `pipeline/__init__.py` 顶部的 docstring 还残留着老的 `stage_a_*.py / scenario_loader.py` 文件名(那些已搬进 `legacy/`)。**以 `docs/anchors/pipeline_full_map.md` 为准**,别照 `__init__.py` 的注释找文件。

---

## 环境搭建

需要 **Python 3.9+**(开发用的 3.9.6)。本仓库没有 `requirements.txt`,第三方依赖很少:

```bash
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install openai python-dotenv httpx
```

> 三个依赖:`openai`(OpenAI 兼容客户端)、`python-dotenv`(读 `.env`)、`httpx`(超时控制)。其余全是标准库。

**配置 `.env`**(所有 LLM 调用都从 `config.py` 读这几个变量):

```bash
cp .env.example .env
# 然后编辑 .env,填入你自己的值:
#   OPENAI_API_KEY    —— 你的 key
#   OPENAI_BASE_URL   —— OpenAI 官方或代理/中转地址
#   MODEL             —— 出题用的模型名
#   LLM_CONCURRENCY   —— 在飞并发上限(按额度调,默认 8)
```

> ★ `.env` 已被 .gitignore 排除,**真 key 绝不要提交**。模板见 `.env.example`。

---

## 怎么跑

**1) 离线自检(不打 API,先确认环境 OK):**

```bash
# 产线注册表自检(5 条线是否完好)
./venv/bin/python -m pipeline.lines

# 真值状态机自检(gt 算得对不对)
./venv/bin/python pipeline/world_state.py

# 接地闸 / 良定义闸 干跑
./venv/bin/python -m pipeline.grounding

# 闭环 / 增量渲染自检
./venv/bin/python tests/closed_loop_selftest.py
./venv/bin/python tests/incremental_render_selftest.py
```

单独看某条线(可选):`./venv/bin/python -m pipeline.lines.L1_timeline` 等。

**2) 出一套题(会打 API,先确保 `.env` 配好):**

```bash
./venv/bin/python -m pipeline.factory --scenario office --min-questions 24 --target-mtokens 0.1
```

- `--scenario office` 选场景;`--min-questions` 给目标题数(触发闭环);`--target-mtokens` 给 token 预算。
- 想看可用旋钮:`./venv/bin/python -m pipeline.factory --help`;看历史 run:`--list-runs`。
- 大批量建议挂后台:`nohup ./venv/bin/python -u -m pipeline.factory --scenario office --target-mtokens 1.0 > /tmp/f.log 2>&1 &`。

**3) 产物在哪:** `output/runs/<run_id>/`(`run_id = <scenario>__<时间戳>`),逐 stage 落盘 `00_input … 06_grounded_questions.json`,外加 `manifest` / `run.log` / `prompts.jsonl`。
`output/` 整个被 .gitignore 排除(每次 run 可重生)。

---

## 给接手同学的话

读代码前先把上面「核心理念」四条记牢,再按 `docs/anchors/` 的推荐顺序读文档——尤其 `pitfalls_4lines.md`,它会告诉你**哪些坑已经踩过、为什么不能用打补丁的方式绕**。改任何产线逻辑前,先跑一遍上面的离线自检确认基线是绿的。
