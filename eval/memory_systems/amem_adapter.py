"""
eval.memory_systems.amem_adapter — A-Mem (Agentic Memory) adapter。

依赖: pip install git+https://github.com/WujiangXu/A-mem-sys.git

A-Mem 基于 Zettelkasten: 每条记忆是结构化 note (keywords/context/tags/links),
LLM 自动分析并建立笔记间的图链接。检索时走 向量 + 图扩展。

Embedding 用本地 bge-small-zh-v1.5 (中文,512维,不走 API,与全系统统一底座)。
LLM 调用走 INGEST_LLM_BASE_URL（若设了），否则走 OPENAI_BASE_URL (DMXAPI)。
"""
from __future__ import annotations
import os, sys, time, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from eval.memory_systems.base import MemorySystem
from eval.multi_system import header

_env_lock = threading.Lock()


def _build_amem(model: str):
    """构造 AgenticMemorySystem，线程安全地临时切换 OPENAI_BASE_URL。"""
    from agentic_memory.memory_system import AgenticMemorySystem

    ingest_base = os.getenv("INGEST_LLM_BASE_URL")
    with _env_lock:
        prev = os.environ.get("OPENAI_BASE_URL")
        if ingest_base:
            os.environ["OPENAI_BASE_URL"] = ingest_base
        try:
            sys_obj = AgenticMemorySystem(
                model_name="BAAI/bge-small-zh-v1.5",
                llm_backend="openai",
                llm_model=model,
            )
        finally:
            if prev is not None:
                os.environ["OPENAI_BASE_URL"] = prev
            elif ingest_base:
                os.environ.pop("OPENAI_BASE_URL", None)
    return sys_obj


def _harden_llm(sys_obj):
    """A-Mem 默认 temperature=1.0 且【不传 max_tokens】(LLMController.get_completion 丢弃了该参数),
    小模型(Qwen3-8B)在结构化"记忆演化"任务上会失控生成(撞 max-model-len → 130KB 垃圾 → JSON 炸 →
    卡死)。这里把它的 LLM 调用收紧:强制 temperature=0(结构化要确定性)+ max_tokens 封顶 + 关 Qwen3
    思考模式(<think> 会在 schema 外狂输出)。换强模型后这层依然无害(只是确定性+有界)。"""
    llm = getattr(sys_obj, "llm_controller", None)
    llm = getattr(llm, "llm", None)
    client = getattr(llm, "client", None)
    model = getattr(llm, "model", None)
    if client is None or model is None:
        return   # 非 OpenAI 后端 → 跳过

    def _gc(prompt, response_format=None, temperature=0.0, max_tokens=1024):
        kw = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You must respond with a JSON object."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 1024,
            "timeout": 60,
        }
        if response_format:
            kw["response_format"] = response_format
        for attempt in range(3):
            try:
                try:
                    r = client.chat.completions.create(
                        **kw, extra_body={"chat_template_kwargs": {"enable_thinking": False}})
                except TypeError:
                    r = client.chat.completions.create(**kw)
                return r.choices[0].message.content
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                print(f"[amem] LLM 3次重试耗尽: {type(e).__name__}: {str(e)[:80]}")
                return "{}"

    llm.get_completion = _gc


class AMemAdapter(MemorySystem):

    def __init__(self, top_k: int = 10, llm_model: str = None, **kwargs):
        self.top_k = top_k
        self._last_context = ""
        self._model = (llm_model
                       or os.getenv("INGEST_LLM_MODEL")
                       or os.getenv("MODEL", "gpt-4o-mini"))
        self._sys = _build_amem(self._model)
        _harden_llm(self._sys)

    def ingest_session(self, session: dict) -> dict:
        sid = session["session_id"]
        date = session["date"]
        n = 0
        for doc in session["docs"]:
            text = header(sid, date) + doc
            try:
                self._sys.add_note(content=text, time=date)
            except Exception as e:
                print(f"[amem] ingest 失败 session={sid}: {type(e).__name__}: {str(e)[:120]}")
            n += 1
        return {"n_docs": n}

    def retrieve(self, question: str, top_k: int = None) -> str:
        k = top_k or self.top_k
        results = self._sys.search_agentic(question, k=k)
        if not results:
            self._last_context = "(无检索结果)"
            return self._last_context
        lines = []
        for r in results:
            content = r.get("content", "")
            score = r.get("score", "")
            neighbor = " [linked]" if r.get("is_neighbor") else ""
            score_str = f"[score={score:.2f}]" if isinstance(score, (int, float)) else ""
            lines.append(f"{score_str}{neighbor} {content}".strip())
        self._last_context = "\n".join(lines)
        return self._last_context

    def get_memory_snapshot(self) -> dict:
        memories = self._sys.memories
        lines = []
        for mid, note in memories.items():
            lines.append(f"[{note.keywords}] {note.content}")
        text = "\n".join(lines) if lines else "(empty)"
        return {"text": text, "n_notes": len(memories)}

    def reset(self) -> None:
        self._sys = _build_amem(self._model)
        _harden_llm(self._sys)
        self._last_context = ""
