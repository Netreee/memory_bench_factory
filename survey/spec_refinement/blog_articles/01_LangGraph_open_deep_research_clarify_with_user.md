# LangGraph `open_deep_research`：`clarify_with_user` 节点完整拆解

> 来源：`https://github.com/langchain-ai/open_deep_research` (`src/open_deep_research/state.py` + `prompts.py`)
> 抓取时间：2026-05-29
> 关键发现：**整个 Deep Research 工作流的 0 号入口就是 `clarify_with_user`，它用 Pydantic 强约束的三字段结构化输出来决定"问 / 不问"。这是当前最干净、最可复用的开源实现。**

---

## 1. 关键 Pydantic Schema（state.py）

```python
class ClarifyWithUser(BaseModel):
    """Model for user clarification requests."""
    need_clarification: bool = Field(
        description="Whether the user needs to be asked a clarifying question.",
    )
    question: str = Field(
        description="A question to ask the user to clarify the report scope",
    )
    verification: str = Field(
        description="Verify message that we will start research after the user has provided the necessary information.",
    )

class ResearchQuestion(BaseModel):
    """Research question and brief for guiding research."""
    research_brief: str = Field(
        description="A research question that will be used to guide the research.",
    )
```

**设计要点**：
- `need_clarification` 是 boolean —— 三元（问/不问/再问）退化为二元，简化路由
- `question` 与 `verification` 互斥（前者为追问、后者为"我开始干活了"的确认）
- `research_brief` 是**单一字符串**字段，不是 JSON spec —— LangChain 选择"自由文本 brief"而不是"严格结构化 spec"，靠下游 LLM 理解，这是个有意的工程权衡

---

## 2. clarify_with_user 完整 Prompt（verbatim）

```
These are the messages that have been exchanged so far from the user asking for the report:
<Messages>
{messages}
</Messages>

Today's date is {date}.

Assess whether you need to ask a clarifying question, or if the user has already
provided enough information for you to start research.
IMPORTANT: If you can see in the messages history that you have already asked a
clarifying question, you almost always do not need to ask another one. Only ask
another question if ABSOLUTELY NECESSARY.

If there are acronyms, abbreviations, or unknown terms, ask the user to clarify.
If you need to ask a question, follow these guidelines:
- Be concise while gathering all necessary information
- Make sure to gather all the information needed to carry out the research task
  in a concise, well-structured manner.
- Use bullet points or numbered lists if appropriate for clarity. Make sure that
  this uses markdown formatting and will be rendered correctly if the string
  output is passed to a markdown renderer.
- Don't ask for unnecessary information, or information that the user has
  already provided. If you can see that the user has already provided the
  information, do not ask for it again.

Respond in valid JSON format with these exact keys:
"need_clarification": boolean,
"question": "<question to ask the user to clarify the report scope>",
"verification": "<verification message that we will start research>"
```

**关键观察**：
- **"几乎不再问第二轮"** —— 这是 LangChain 的硬约束。后面也有 `verification` 互检防止死循环。
- **"专门点名 acronym / 未知缩写要问"** —— 这是触发追问最常见的可靠信号
- **没有显式 ambiguity score**，完全靠 LLM 自我判定 `need_clarification`

---

## 3. write_research_brief 完整 Prompt（verbatim）

```
You will be given a set of messages that have been exchanged so far between
yourself and the user. Your job is to translate these messages into a more
detailed and concrete research question that will be used to guide the research.

Guidelines:
1. Maximize Specificity and Detail
   - Include all known user preferences and explicitly list key attributes or
     dimensions to consider.
   - It is important that all details from the user are included in the
     instructions.

2. Fill in Unstated But Necessary Dimensions as Open-Ended
   - If certain attributes are essential for a meaningful output but the user
     has not provided them, explicitly state that they are open-ended or
     default to no specific constraint.

3. Avoid Unwarranted Assumptions
   - If the user has not provided a particular detail, do not invent one.
   - Instead, state the lack of specification and guide the researcher to
     treat it as flexible or accept all possible options.

4. Use the First Person
   - Phrase the request from the perspective of the user.

5. Sources
   - If specific sources should be prioritized, specify them in the research
     question.
   - For product and travel research, prefer linking directly to official or
     primary websites ...
```

**关键启示**：
- "**Fill in Unstated But Necessary Dimensions as Open-Ended**" —— 不要求每个维度都拿到答案，缺的就标"open-ended"。这才是工程上能跑的关键。
- "**Avoid Unwarranted Assumptions**" —— 不允许 LLM 自己脑补未问到的字段。

---

## 4. 整体 4 节点流水线

```
[clarify_with_user]
   ├─ need_clarification=True  → 输出 question，等用户回复
   └─ need_clarification=False → 进入下一节点
      ↓
[write_research_brief]
   → 输出 research_brief (string)
      ↓
[supervisor / orchestrator]
   → 用 ConductResearch tool 并行 spawn 多个 sub-agent
      ↓
[final_report_generation]
```

**对我们的启示**：这套模式完全适合 `memory_bench_factory` 的 Stage 0：
- `clarify_with_user` → 我们的 `clarify_scenario_spec`
- `write_research_brief` → 我们的 `compile_refined_scenario_spec`（输出结构化 ScenarioSpec dict）
- 后续 supervisor → 我们已有的 Stage A-E
