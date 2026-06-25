# Perplexity Deep Research：完全 implicit clarification

> 来源：`https://www.perplexity.ai/hub/blog/introducing-perplexity-deep-research`
> 抓取时间：2026-05-29

## 1. 核心架构

Perplexity Deep Research 是一个 **agentic RAG loop**：
1. retrieve（hybrid BM25 + dense embedding）
2. read & reason about what's missing
3. retrieve again
4. iterate "dozens of searches, hundreds of sources"

## 2. Clarification 怎么处理？**完全 implicit**

Perplexity 不向用户问 clarification 问题。**它把 clarification "对自己问"**：

> "The system effectively asks itself clarification questions about what additional information is needed."

具体机制是：模型自己生成 sub-queries，相当于把"我应该问用户什么"翻译成"我应该 search 什么"，绕过了用户交互。

## 3. 对我们的启示

**这对 memory benchmark 合成不合适**。原因：

- Perplexity 的输入空间足够窄（事实型查询），自己 search 能补回信息
- 我们的输入是 ScenarioSpec —— **关键信息（评测视角、记忆主体、时间跨度）用户脑子里有但没说出来**，**无论搜什么都搜不出来**
- 强行 implicit 推断 = 死症 1（pipeline 自己脑补，跑偏全靠运气）

→ **我们必须显式 clarify 用户，至少要问 2-4 个核心问题**
