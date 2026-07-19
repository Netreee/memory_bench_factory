"""
eval.memory_systems.fullcontext — 全上下文 baseline(= 原系统 B)。

把全部语料塞进一个 prompt。无检索瓶颈的上界,但对 L1 信号竞争引入噪声。
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from eval.memory_systems.base import MemorySystem


def build_full_context(docs: list, budget: int = 120_000) -> tuple[str, bool, int]:
    """Build full-context text without importing the API-backed eval harness."""

    blocks = [f"[周期{sid} | 日期 {date}] {content}" for sid, date, content in docs]
    full = "\n\n".join(blocks)
    if len(full) <= budget:
        return full, False, len(full)

    kept, used = [], 0
    for block in blocks:
        if used + len(block) + 2 > budget:
            break
        kept.append(block)
        used += len(block) + 2
    return "\n\n".join(kept), True, used


class FullContext(MemorySystem):

    def __init__(self, budget: int = 120_000):
        self.budget = budget
        self._docs_buf: list = []
        self._context = ""
        self._truncated = False
        self._used_chars = 0
        self._last_context = ""

    def ingest_session(self, session: dict) -> dict:
        sid = session["session_id"]
        date = session["date"]
        n = 0
        for doc in session["docs"]:
            self._docs_buf.append((int(sid), date, doc))
            n += 1
        return {"n_docs": n}

    def finalize_ingest(self, on_progress=None) -> None:
        self._context, self._truncated, self._used_chars = build_full_context(
            self._docs_buf, budget=self.budget)

    def retrieve(self, question: str, top_k: int = None) -> str:
        self._last_context = self._context
        return self._context

    def get_memory_snapshot(self) -> dict:
        return {"text": self._context, "n_chars": self._used_chars,
                "truncated": self._truncated}

    def reset(self) -> None:
        self._docs_buf = []
        self._context = ""
        self._truncated = False
        self._used_chars = 0
        self._last_context = ""

    @property
    def trunc_info(self) -> dict:
        return {"truncated": self._truncated, "used_chars": self._used_chars}
