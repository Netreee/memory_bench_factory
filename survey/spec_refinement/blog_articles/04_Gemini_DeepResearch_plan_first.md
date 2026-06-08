# Gemini Deep Research：plan-first，用户编辑 plan 代替追问

> 来源：`https://gemini.google/overview/deep-research/` + Google AI 开发者文档
> 抓取时间：2026-05-29

## 1. 核心范式：collaborative planning，**不直接追问**

Gemini Deep Research 的 clarification 不是通过追问实现的，而是：

1. 用户发出 query
2. Gemini **立即生成一个 multi-step research plan**（分解成 sub-tasks）
3. 把 plan 整个**展示给用户**，用户可以编辑、增删、调整优先级
4. 用户点"Start Research" → 才真正执行

**关键差异**：
- ChatGPT / Claude / LangGraph：用追问推进 clarify（自然语言对话）
- Gemini：用"编辑 plan"推进 clarify（用户直接改 spec）

## 2. 为什么这种范式有意思

对**专家用户**特别友好 —— 不用回答一堆 metadata 问题，直接看 plan 就知道哪里不对、删一条加两条即可。

对**普通用户**有门槛 —— plan 长得像树形大纲，要看懂才会改。

## 3. 对我们的启示（关键）

**研究者（我们的目标用户）多半是专家**，他们更愿意"看 plan 编辑"而不是"答问卷"。

我们可以**两种范式混用**：
1. 第一轮：clarification questions（解决主体/视角/时间跨度等 metadata）
2. 第二轮：展示 ScenarioDimensions + OpsProfile 草稿，让用户编辑（解决配额 / 失败模式）

这样**两份 LLM call 就够，token 成本可控** —— 不需要 10+ 轮对话。
