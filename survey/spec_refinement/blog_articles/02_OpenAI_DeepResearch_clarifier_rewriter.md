# OpenAI Deep Research：四阶段 handoff 流水线

> 来源：OpenAI Cookbook (`/examples/deep_research_api/introduction_to_deep_research_api_agents`)
> 抓取时间：2026-05-29
> 关键发现：**ChatGPT 端 deep research 是"自动 clarify"，API 端则把 clarify 拆出来给开发者自己实现 —— 官方 cookbook 给出了"三角色 agent"参考实现：Triage → Clarifier → Instruction Rewriter → Research。**

---

## 1. 架构概览（4-agent handoff）

```
User Query
    ↓
[Triage Agent]            → 判断是否真的需要 clarify
    ↓ (handoff)
[Clarifying Agent]        → 输出 questions: List[str]
    ↓ (user answers)
[Instruction Agent]       → 重写成 "research instructions"
    ↓ (handoff)
[Research Agent]          → 跑 web search + MCP tools
```

四个 agent 用 OpenAI Agents SDK 的 `handoffs` 参数串起来。

---

## 2. Clarifier 系统提示（精简版）

```
If the user hasn't specifically asked for research (unlikely), ask them what
research they would like you to do.

GUIDELINES:
1. **Be concise while gathering all necessary information**
   Ask 2–3 clarifying questions to gather more context for research.
   - Make sure to gather all the information needed to carry out the research
     task in a concise, well-structured manner.
   - Use bullet points or numbered lists if appropriate for clarity.
   - Don't ask for unnecessary information, or information that the user has
     already provided.

2. **Maintain a Friendly and Non-Condescending Tone**

3. **Adhere to Safety Guidelines**

Your job is NOT to complete the task yet, but instead to ask clarifying questions
that would help you or another researcher produce a more specific, efficient,
and relevant answer.
```

**结构化输出 schema**：

```python
class Clarifications(BaseModel):
    questions: List[str]
```

**比 LangGraph 简单**：没有 `need_clarification` 布尔位 —— OpenAI 假定走到 Clarifier 这步一定要问，要不要问的判断前置到 Triage Agent。

---

## 3. Instruction Rewriter 提示（精简版）

```
Based on the following guidelines, take the users query, and rewrite it into
detailed research instructions. OUTPUT ONLY THE RESEARCH INSTRUCTIONS, NOTHING ELSE.

GUIDELINES:
- Maximize specificity and detail (incorporate known user preferences,
  explicitly list key attributes or dimensions)
- Fill in unstated but necessary dimensions as open-ended (if essential
  attributes are missing, declare them open-ended)
- Avoid unwarranted assumptions
- Use first person
- Prefer official sources over aggregators
- Include tables and structured formatting in output expectations
- Output in user's source language
```

这套话术与 LangGraph 几乎逐句对应 —— 强烈暗示**业界对"refine 模糊 query"的最佳实践已经收敛**。

---

## 4. ChatGPT 端 vs API 端的关键区别

| | ChatGPT Deep Research | OpenAI Deep Research API |
|---|---|---|
| Clarifier 自动触发 | 是 | 否（需开发者实现） |
| 几轮追问 | 1 轮（2-3 个 bundled questions） | 由开发者定义 |
| 模型 | 内部 GPT-4.1 作为 clarifier，o3/o4-mini 作为 researcher | 由开发者选 |
| 输出格式 | 自由文本带引用 | JSON / string |

**对我们的启示**：**模糊的 ScenarioSpec 必须在第 0 步显式 clarify，否则下游全错** —— 这正是 OpenAI 在 ChatGPT 端硬塞 clarifier 的原因。

---

## 5. 关键设计原则汇总（来自 cookbook）

1. **"Two stages of refinement"**：Clarify（从用户拿信息）+ Rewrite（把信息编译成可被下游消费的 spec）—— 不要把两件事合在一个 prompt 里
2. **"Be a translator, not an inventor"**：Rewriter 只能转译，不能脑补
3. **"Open-ended is fine"**：缺的字段就标 open-ended，不要硬填默认值
4. **"2-3 questions cap"**：一次只问 2-3 个，避免问卷感
