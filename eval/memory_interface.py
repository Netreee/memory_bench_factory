"""
eval.memory_interface — 记忆系统接口 + DMXAPI dense 实现。

接口对齐 OfficeMem eval_onpolicy.MemorySystem(ingest/retrieve/reset),
方便后续替换成真 simpleMem(SimpleRAGMemory)或 R1/R2/R3 adaptor。

EmbedMemory:机制等同 simpleMem — DMXAPI 向量 + numpy 余弦,无外部 DB。
  ★ 关键:retrieve 是【选择性 top-k】,这正是信号竞争(M3)赖以触发的检索瓶颈:
    旧值在多 period 文档高频出现 → top-k 更可能命中旧值文档 → R1 合成出旧值(M3)。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


class MemorySystem(ABC):
    """记忆系统抽象接口(对齐 OfficeMem)。"""

    @abstractmethod
    def ingest(self, text: str, doc_id: str = "", metadata: Optional[dict] = None) -> None:
        ...

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 5) -> list:
        """返回 top_k 个文本片段(list[str])。"""
        ...

    @abstractmethod
    def reset(self) -> None:
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Embedding 工具(DMXAPI text-embedding-3-small,dim=1536)
# ─────────────────────────────────────────────────────────────────────────────

def embed_texts(texts: list, model: str = "text-embedding-3-small",
                retries: int = 5) -> list:
    """批量 embed,返回 list[np.ndarray(float32)]。

    DMXAPI 的 connect/handshake 偶发失败(冷连接尤甚)。ingest 长语料时是上百次连发,
    单篇连撞几次就会炸整轮 → 多给几次重试 + 温和退避,扛过短促抖动。
    """
    import time
    last_err = None
    for attempt in range(retries):
        try:
            # ★单次超时(30s):死 socket 下若不设超时,create() 会无限挂起,重试逻辑根本轮不到。
            #   超时 → 抛异常 → 退避重试,才能扛过死连接(根因:不是抖动,是挂起)。
            resp = config.client.embeddings.create(model=model, input=texts, timeout=30)
            return [np.array(d.embedding, dtype=np.float32) for d in resp.data]
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(min(2.0 * (attempt + 1), 8.0))
                continue
    raise RuntimeError(f"embed_texts {retries} 次后仍失败: {last_err}")


def _chunk(text: str, max_chars: int = 220) -> list:
    """按段落/句子粗切片。细切片能放大信号竞争(高频旧值切出更多 chunk)。"""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return [text] if text else []
    # 先按句号/换行切,再贪心合并到 max_chars
    import re
    parts = re.split(r"(?<=[。！？\n])", text)
    chunks, cur = [], ""
    for p in parts:
        if len(cur) + len(p) <= max_chars:
            cur += p
        else:
            if cur.strip():
                chunks.append(cur.strip())
            cur = p
    if cur.strip():
        chunks.append(cur.strip())
    return chunks or [text]


class EmbedMemory(MemorySystem):
    """DMXAPI dense 检索记忆(机制 = simpleMem)。"""

    def __init__(self, model: str = "text-embedding-3-small", chunk: bool = True,
                 chunk_chars: int = 220):
        self.model = model
        self.chunk = chunk
        self.chunk_chars = chunk_chars
        self.reset()

    def reset(self) -> None:
        self._docs: list = []     # list[str] 片段文本
        self._meta: list = []     # list[dict]
        self._vecs: list = []     # list[np.ndarray]

    def ingest(self, text: str, doc_id: str = "", metadata: Optional[dict] = None,
               text_prefix: str = "") -> None:
        """ingest 文档。text_prefix 会拼到【每个 chunk】前(用于注入日期/周期表头,
        让检索片段自带时间戳 → 公平地给 baseline 做 recency 推理的机会)。"""
        pieces = _chunk(text, self.chunk_chars) if self.chunk else [text]
        if not pieces:
            return
        pieces = [text_prefix + p for p in pieces]
        vecs = embed_texts(pieces, self.model)
        for piece, v in zip(pieces, vecs):
            self._docs.append(piece)
            self._meta.append({**(metadata or {}), "doc_id": doc_id})
            self._vecs.append(v)

    def retrieve(self, query: str, top_k: int = 5) -> list:
        if not self._vecs:
            return []
        qv = embed_texts([query], self.model)[0]
        M = np.stack(self._vecs)
        sims = (M @ qv) / (np.linalg.norm(M, axis=1) * (np.linalg.norm(qv) + 1e-8) + 1e-8)
        idx = np.argsort(-sims)[:top_k]
        return [self._docs[i] for i in idx]

    def retrieve_with_meta(self, query: str, top_k: int = 5) -> list:
        """诊断用:返回 [(text, meta, score)]。"""
        if not self._vecs:
            return []
        qv = embed_texts([query], self.model)[0]
        M = np.stack(self._vecs)
        sims = (M @ qv) / (np.linalg.norm(M, axis=1) * (np.linalg.norm(qv) + 1e-8) + 1e-8)
        idx = np.argsort(-sims)[:top_k]
        return [(self._docs[i], self._meta[i], float(sims[i])) for i in idx]
