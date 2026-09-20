# memory_bench_factory

记忆 benchmark 生成器:给定一个场景描述 + 少量示例文档,端到端生成一套用于评测「带记忆的 LLM / Agent」的题库(语料 + 带标准答案的问题)。标准答案由代码从一个状态机世界机械算出,不依赖 LLM 判分。

## 环境

Python 3.10+,依赖很少:

```bash
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements-minimal.txt
cp .env.example .env    # 填 OPENAI_API_KEY / OPENAI_BASE_URL / MODEL / LLM_CONCURRENCY
```

## 运行

```bash
# 离线自检(不打 API)
./venv/bin/python -m pipeline.lines
./venv/bin/python pipeline/world_state.py
./venv/bin/python tests/world_blueprint_selftest.py

# 出一套题(打 API,先配好 .env)
./venv/bin/python -m pipeline.factory --scenario office --min-questions 24 --target-mtokens 0.1
```

`--help` 看全部参数,`--list-runs` 看历史。产物落在 `output/runs/<run_id>/`(逐 stage 落盘,`output/` 不进 git)。

## 评测端（`agent_harnesses/`）

原 `agent-harnesses` 仓库的评测控制面已并入本仓库：`agent_harnesses/` 负责配置校验 → run plan → 分发到各 harness（native CLI adapter）；判分真源仍是本仓库的 `eval/judge.py`，控制面按文件路径动态加载它并记录 `factory_commit` + `judge_sha256` 作为判分版本。代码合并自 `agent-harnesses@9859368`（`feat/parallel-execution`），只保留核心逻辑与运行配置，不含其历史 `docs/`、`runs/`、`input/`。

配置分三层：`configs/systems.toml`（稳定的 harness/memory-system 注册表，只跟踪进 git）、experiment TOML（一次运行的 benchmark/模型/endpoint/limit，默认不进 git）、`configs/env/secrets.env`（endpoint 密钥，不进 git）。

```bash
cp configs/env/secrets.env.example configs/env/secrets.env   # 填密钥与 endpoint，只做一次

python3 -m agent_harnesses systems                       # 列出注册表中被测对象
python3 -m agent_harnesses validate  --experiment configs/experiments/templates/native-smoke.toml
python3 -m agent_harnesses preflight --experiment configs/experiments/templates/native-smoke.toml
python3 -m agent_harnesses run --experiment configs/experiments/templates/native-smoke.toml            # 默认只出计划
python3 -m agent_harnesses run --experiment configs/experiments/templates/native-smoke.toml --execute  # 显式执行
python3 -m agent_harnesses score --out output/eval/<experiment>/<scenario>/<target>/<run_dir> --no-llm
```

约束：

- **benchmark 数据不随本仓库分发**。模板里的 `benchmarks[].path` 指向工作区保留的历史候选快照（`UNMET`，仅够 smoke）；正式实验改成 factory 生成产物 `output/runs/<run_id>/` 或任意相对/绝对路径。
- 评测产物写在 `output/eval/`（与工厂生成产物 `output/runs/` 并列，均不进 git）；模板已按此设置 `output_root`。
- `questions_in_parallel` / `parallel_plans` 默认均为 `1`（完全串行），并发度不进 fingerprint，可随 `--resume` 调整。
- 回归测试：`python3 -m unittest discover -s tests/harness`；唯一随仓库分发的 benchmark 夹具在 `tests/harness/fixtures/`，用途与边界见该目录 README。
- **Memory System Track 仍是占位**：`agent_harnesses/tracks/memory.py` 主动拒绝执行，需先冻结 answering model、context budget 与 retrieval witness 才接入 `eval/memory_systems/`。

## World-first 白皮书

新场景先由领域架构师定义并经反方换皮评审冻结 `world_blueprint`：实体类型与字段归属、关系拓扑、领域事件及效果、因果链、时间制度、证据渠道。之后才把 L1–L10 映射到世界自然存在的结构；能力线在 typed world 中只读，不得补字段或改写时间线。显式蓝图不合法会在白皮书阶段直接失败，历史无蓝图产物才使用 legacy 适配。

`01_whitepaper.json` 同时保留 `world_review` 审议记录和最终可执行 `world_blueprint`，因此可以先人工判断“这个世界是不是换皮”，再批准进入 world/corpus 生成。

## 目录

- `pipeline/` —— 主代码:8 段流水线(input → whitepaper → world → orders → well_posed → questions → corpus → grounding)+ `lines/`(10 条能力产线)+ `world_blueprint.py`(场景骨架契约)+ `world_state.py`(真值状态机,gt 在此机械算)。
- `eval/` —— 判分真源(`judge.py`)与记忆系统适配器;`agent_harnesses/` 通过动态加载调用它。
- `agent_harnesses/` —— 评测执行控制面(config 校验 / run plan / native CLI adapter / 判分聚合),入口 `python3 -m agent_harnesses`;运行配置在 `configs/`。
- `tests/` —— 离线自检;`tests/harness/` 是评测控制面的回归测试与夹具。
- `survey/` —— 调研报告(只跟踪 `.md`,PDF 不进 git)。
- `docs/anchors/` —— 调研 / 文献报告(related_work、novelty_lit_review 等)。
- `tools/`、`vis/` —— 辅助工具与可视化。
