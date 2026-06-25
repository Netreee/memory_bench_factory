"""
eval.memory_systems.base — 记忆系统统一接口。

所有被测系统(内置 baseline + 外部 mem0/zep/memOS)都实现此接口,
评测外壳通过它做 ingest → retrieve → 统一答题,隔离"记忆能力"。
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class MemorySystem(ABC):

    @abstractmethod
    def ingest_session(self, session: dict) -> dict:
        """逐 session 喂入语料。
        session = {"session_id": int, "date": "YYYY-MM-DD", "docs": [str, ...]}
        返回 ingest 遥测(结构自定,用于成本统计)。"""

    @abstractmethod
    def retrieve(self, question: str, top_k: int = 5) -> str:
        """对一个问题做原生检索,返回拼好的 context 字符串。
        query budget = 1:多跳系统在内部完成,对外仍是一次调用。"""

    def get_retrieved_context(self) -> str:
        """R-check:返回最近一次 retrieve 的 context。默认缓存实现。"""
        return getattr(self, "_last_context", "")

    @abstractmethod
    def get_memory_snapshot(self) -> dict:
        """W-check:返回全部记忆状态(审计用)。"""

    @abstractmethod
    def reset(self) -> None:
        """清空全部状态。"""

    def finalize_ingest(self, on_progress=None) -> None:
        """所有 session ingest 完后调用一次。
        on_progress(done, total): 可选进度回调。默认 no-op。"""

    def get_diagnostics(self) -> dict:
        """最近一次 retrieve 的诊断信息(如 bridge 实体)。默认空。"""
        return {}
