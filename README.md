# memory_bench_factory

记忆 benchmark 生成器:给定一个场景描述 + 少量示例文档,端到端生成一套用于评测「带记忆的 LLM / Agent」的题库(语料 + 带标准答案的问题)。标准答案由代码从一个状态机世界机械算出,不依赖 LLM 判分。

## 环境

Python 3.9+,依赖很少:

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

# 出一套题(打 API,先配好 .env)
./venv/bin/python -m pipeline.factory --scenario office --min-questions 24 --target-mtokens 0.1
```

`--help` 看全部参数,`--list-runs` 看历史。产物落在 `output/runs/<run_id>/`(逐 stage 落盘,`output/` 不进 git)。

## 目录

- `pipeline/` —— 主代码:8 段流水线(input → whitepaper → world → orders → well_posed → questions → corpus → grounding)+ `lines/`(7 条能力产线)+ `world_state.py`(真值状态机,gt 在此机械算)。
- `eval/` —— 用生成的题库评测记忆系统的脚手架。
- `tests/` —— 离线自检。
- `survey/` —— 调研报告(只跟踪 `.md`,PDF 不进 git)。
- `docs/anchors/` —— 调研 / 文献报告(related_work、novelty_lit_review 等)。
- `tools/`、`vis/` —— 辅助工具与可视化。
