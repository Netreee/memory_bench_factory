# Anthropic Claude Research：多 agent 编排，clarify 隐式化

> 来源：`https://www.anthropic.com/engineering/multi-agent-research-system` (Anthropic 工程博客，2025-06)
> 抓取时间：2026-05-29

## 1. 架构核心

- **Orchestrator-worker 模式**：lead agent (Claude Opus 4) 分析 query → 制定策略 → spawn 多个 sub-agent (Claude Sonnet 4) 并行探索
- **Sub-agent 各自隔离的 context window**，最后聚合到 lead agent 写 final report
- Multi-agent 比 single-agent 在 Anthropic 内部 research eval 上**好 90.2%**
- "**Token usage by itself explains 80% of the variance**" —— 多 agent 主要是因为能 spend more tokens

## 2. Clarify 阶段的处理方式（与 OpenAI/LangGraph 不同！）

Anthropic 没有显式的 `clarify_with_user` 节点。**Lead agent 在第一轮 reasoning 时自己决定是直接干活还是先问**。可以理解为 "implicit clarification inside the orchestrator"。

代价：
- 优势：减少一个硬节点，工程更简单，模型能自己决定何时 clarify
- 劣势：clarify 与 plan 混在一起，不可控、不可复用、不能 audit

## 3. 对我们的启示

- 如果我们的 pipeline 用 multi-agent + 大模型（Opus 级）作为 orchestrator，可以学 Anthropic 的"implicit clarify"做法
- 如果我们想要**可 audit 的、可复现的、可 ablation 的** clarify 阶段（SIGMOD 投稿肯定需要），**应该走 LangGraph / OpenAI cookbook 的显式节点设计**
- Anthropic 还报告：sub-agent 之间的 context isolation 非常关键 —— 这对我们 Stage A/B/C/D 的解耦也有参考价值
