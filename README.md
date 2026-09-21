# memory_bench_factory

记忆 benchmark 生成器:给定一个场景描述 + 少量示例文档,端到端生成一套用于评测「带记忆的 LLM / Agent」的题库(语料 + 带标准答案的问题)。标准答案由代码从一个状态机世界机械算出,不依赖 LLM 判分。

## 环境

Python 3.10+,依赖很少:

```bash
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install openai python-dotenv httpx
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

## 真实任务种子 → 白皮书

现有 `input → whitepaper` 支持策展种子 JSON。种子的实体、字段、关系、事件、业务对象绑定和因果要求先通过机械审查，再冻结世界蓝图和映射能力；后续世界实例化、扩量、续跑继续校验同一合同。

```bash
# 离线检查三个种子；不读取 .env、不调用模型
python tools/validate_seed_packs.py
# 本地持有原件时可同时验证来源散列
python tools/validate_seed_packs.py --verify-sources

# 调用已配置的模型，先审阅白皮书
python -m pipeline.factory --seed-pack seeds/insurance.json --to whitepaper
# 也可替换成 seeds/legal.json 或 seeds/finance.json
# 继续同一 run，无需再次提供原种子路径
python -m pipeline.factory --run <run_id> --from world
```

`--seed-pack` 与 `--scenario` 二选一。每个 run 将种子冻结为 `00_seed_pack.json`，记录来源身份并输出 `01_seed_audit.json`、`02_seed_audit.json`；已有 run 不允许换种子版本，更新包后应启动新 run。白皮书的 `seed_contract` 排除评分侧来源，结构丢失或世界实例不符合合同会明确失败。

当前实现保证种子的**可执行结构承接**；新增公式执行器、资料发布/获知的多时间语义和知识状态评分尚未实现。原任务与合成示例的边界、合同格式和三包来源见 [seeds/README.md](seeds/README.md)。

种子融合程度的分层指标、保险场景实测结果，以及后续对照实验方案见 [真实任务 Seed 的融合程度与评估方法](SEED_INTEGRATION_EVALUATION.md)。本轮结构与流程检查通过，业务机制题覆盖和数据质量尚未通过验收。

```bash
python tests/seed_contract_selftest.py
python tests/seed_world_selftest.py
python tests/seed_run_selftest.py
```

## World-first 白皮书

新场景先由领域架构师定义并经反方换皮评审冻结 `world_blueprint`：实体类型与字段归属、关系拓扑、领域事件及效果、因果链、时间制度、证据渠道。之后才把 L1–L10 映射到世界自然存在的结构；能力线在 typed world 中只读，不得补字段或改写时间线。显式蓝图不合法会在白皮书阶段直接失败，历史无蓝图产物才使用 legacy 适配。

`01_whitepaper.json` 同时保留 `world_review` 审议记录和最终可执行 `world_blueprint`，因此可以先人工判断“这个世界是不是换皮”，再批准进入 world/corpus 生成。

## 目录

- `pipeline/` —— 主代码:8 段流水线(input → whitepaper → world → orders → well_posed → questions → corpus → grounding)+ `lines/`(10 条能力产线)+ `world_blueprint.py`(场景骨架契约)+ `world_state.py`(真值状态机,gt 在此机械算)。
- `eval/` —— 用生成的题库评测记忆系统的脚手架。
- `tests/` —— 离线自检。
- `survey/` —— 调研报告(只跟踪 `.md`,PDF 不进 git)。
- `docs/anchors/` —— 调研 / 文献报告(related_work、novelty_lit_review 等)。
- `tools/`、`vis/` —— 辅助工具与可视化。
