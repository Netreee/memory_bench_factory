# memory_bench_factory

从结构化 seed 生成长期记忆 benchmark。一个 run 依次产生领域白皮书、可执行世界、公开信息安排、问题、跨期语料和逐题质量结论。世界与语料提供可追溯事实；LLM Agent 负责生成、阅读和语义审查；程序负责阶段边界、身份绑定、断点恢复和结果汇总。

## 最终流水线

1. `seed → input`：校验并冻结 `seeds/*.json`。
2. `whitepaper`：生成世界蓝图、证据渠道和产线能力映射，并检查业务可执行性。
3. `world`：分步建立实体、关系、事件和时间线；每个小批次落盘，可从检查点继续。
4. `disclosure`：确定信息何时、通过什么途径公开，并完成世界级语义审阅。
5. `orders / well_posed`：为可支持的能力线产生候选任务，先剔除定义不清的任务。
6. `questions`：生成题目和标准答案，对题面语义逐题检查。
7. `corpus`：按期渲染信号文档与干扰文档，并检查正文能否支撑声明。
8. `grounding`：逐题阅读相关语料，给出 `released`、`rejected` 或 `pending_review`；单题失败不终止其他题。
9. `quality`：只核对逐题状态分区和产物散列，生成 `07_release.json`。它不重新判断题目语义，也不会因为题量不足或其他题被淘汰而否决已经通过的题。

当前 seed 流程主要覆盖 L1–L8；L9、L10 需要补充相应的材料构造后再批量启用。

## 安装

Python 3.10 以上：

```bash
python -m venv venv
./venv/Scripts/python -m pip install -r requirements-minimal.txt
copy .env.example .env
```

Linux/macOS 将解释器路径改为 `./venv/bin/python`，复制命令改为 `cp`。在 `.env` 中配置模型接口；密钥、运行输出和虚拟环境均不会进入 Git。

## Seed 校验与运行

离线校验仓库内 seed：

```bash
./venv/Scripts/python tools/validate_seed_packs.py
```

运行单个世界：

```bash
./venv/Scripts/python -m pipeline.factory \
  --seed-pack seeds/insurance.json \
  --min-questions 200 \
  --target-mtokens 0.1
```

查看参数和已有运行：

```bash
./venv/Scripts/python -m pipeline.factory --help
./venv/Scripts/python -m pipeline.factory --list-runs
```

中断后使用同一个 run 继续，已完成阶段和逐题检查点会被复用：

```bash
./venv/Scripts/python -m pipeline.factory --run <run_id>
```

批量运行入口为 `tools/run_original_bc_batch.py`，小规模端到端检查入口为 `tools/run_original_bc_smoke.py`。两个入口都调用同一条生产流水线。

## 主要产物

运行目录位于 `output/runs/<run_id>/`：

| 文件 | 内容 |
| --- | --- |
| `00_seed_pack.json` | 冻结后的 seed |
| `01_whitepaper.json` | 世界蓝图、质量约定和产线映射 |
| `02_world.json` | 实例化世界与时间线 |
| `02_disclosure.json` | 信息公开安排 |
| `04_questions.json` | 全部候选题与答案 |
| `05_corpus.json` | 分期语料 |
| `06_semantic_review.json` | 逐题 Agent 审阅证据 |
| `06_grounding_report.json` | 通过、淘汰、待审和范围排除的分区 |
| `06_grounded_questions.json` | 当前可直接用于评测的题目子集 |
| `07_release.json` | 上述分区与文件身份的轻量汇总 |

题量目标属于生产计划。审查会正常减少最终题量；只要逐题分区完整且至少有一道 `released` 题，通过的子集就可使用。

## 目录

- `pipeline/`：唯一生产流水线及 L1–L10 能力线。
- `eval/`：用发布子集评测记忆系统。
- `agent_harnesses/`：Native Agent Track 与 Memory System Track 的活动评测控制面。
- `seeds/`：结构化 seed。
- `tools/`：校验、批量运行、恢复、监控和导出工具。
- `tests/`：离线回归与故障注入。
- `skills/realfiles-to-seedjson/`：把真实资料转换成 seed JSON 的操作规范。
- `frontend/`：运行状态展示界面。

## 最小验证

```bash
./venv/Scripts/python -m compileall -q pipeline eval tools tests
./venv/Scripts/python -B -X utf8 tests/world_agent_json_recovery_selftest.py
./venv/Scripts/python -B -X utf8 tests/grounding_candidate_isolation_selftest.py
./venv/Scripts/python -B -X utf8 tests/release_summary_selftest.py
```
