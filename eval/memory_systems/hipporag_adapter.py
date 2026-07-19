"""HippoRAG adapter backed by the official local HippoRAG package."""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from eval.memory_systems.base import MemorySystem
from eval.memory_systems._utils import DEFAULT_EMBEDDING_MODEL, DEFAULT_LLM_MODEL, ensure_hf_home


ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACE_ROOT = ROOT.parent
HIPPORAG_SRC = WORKSPACE_ROOT / "HippoRAG" / "src"
if HIPPORAG_SRC.exists() and str(HIPPORAG_SRC) not in sys.path:
    sys.path.insert(0, str(HIPPORAG_SRC))

# Runtime directory used for HippoRAG save_dir – must always live under the
# package output tree for safety.
_RUNTIME_ROOT = ROOT / "output" / "runtime" / "hipporag"


class HippoRAGAdapter(MemorySystem):
    """MemorySystem wrapper over official HippoRAG index/retrieve APIs."""

    def __init__(
        self,
        top_k: int = 5,
        llm_model: str | None = None,
        embedding_model: str | None = None,
        **kwargs,
    ):
        ensure_hf_home(ROOT)
        self.top_k = top_k
        self.llm_model = (
            llm_model
            or os.getenv("INGEST_LLM_MODEL")
            or os.getenv("MODEL")
            or DEFAULT_LLM_MODEL
        )
        self.llm_base_url = os.getenv("INGEST_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        self.embedding_model = (
            embedding_model
            or os.getenv("HIPPORAG_EMBEDDING_MODEL")
            or f"Transformers/{DEFAULT_EMBEDDING_MODEL}"
        )
        self._docs: list[str] = []
        self._session_docs: list[dict[str, Any]] = []
        self._last_context = ""
        self._built = False
        self._run_id = uuid.uuid4().hex[:8]
        self._save_dir = _RUNTIME_ROOT / self._run_id
        self._hipporag = None
        self._init_hipporag()

    def _init_hipporag(self) -> None:
        try:
            from hipporag import HippoRAG
            from hipporag.utils.config_utils import BaseConfig
        except Exception as exc:
            raise ImportError(
                "HippoRAG official dependencies are not available. "
                "Run this adapter in .conda-hipporag or install HippoRAG online dependencies."
            ) from exc

        self._save_dir.mkdir(parents=True, exist_ok=True)
        config = BaseConfig()
        config.save_dir = str(self._save_dir)
        config.llm_name = self.llm_model
        config.llm_base_url = self.llm_base_url
        config.embedding_model_name = self.embedding_model
        config.force_index_from_scratch = True
        config.force_openie_from_scratch = True
        config.preprocess_chunk_max_token_size = None
        config.embedding_batch_size = 8
        config.retrieval_top_k = max(1, self.top_k)
        config.linking_top_k = max(1, self.top_k)
        config.qa_top_k = max(1, self.top_k)
        # DeepSeek-compatible endpoints may not support OpenAI response_format.
        config.response_format = None
        self._hipporag = HippoRAG(global_config=config)

    def reset(self) -> None:
        self._docs = []
        self._session_docs = []
        self._last_context = ""
        self._built = False
        # Safety: only clean up save_dirs under the designated runtime root.
        if self._save_dir.exists():
            try:
                self._save_dir.relative_to(_RUNTIME_ROOT)
            except ValueError:
                raise RuntimeError(
                    f"HippoRAG save_dir {self._save_dir} is outside the runtime "
                    f"root {_RUNTIME_ROOT} – refusing to delete."
                )
            shutil.rmtree(self._save_dir, ignore_errors=True)
        self._run_id = uuid.uuid4().hex[:8]
        self._save_dir = _RUNTIME_ROOT / self._run_id
        self._init_hipporag()

    def ingest_session(self, session: dict) -> dict:
        sid = session["session_id"]
        date = session.get("date", "")
        docs = session.get("docs", [])
        for index, doc in enumerate(docs, start=1):
            text = f"[session={sid} date={date} doc={index}]\n{doc}"
            self._docs.append(text)
            self._session_docs.append(
                {"session_id": sid, "date": date, "doc_id": f"s{sid}_d{index}", "text": text}
            )
        self._built = False
        return {"n_docs": len(docs)}

    def finalize_ingest(self, on_progress=None) -> None:
        if not self._docs or self._built:
            return
        self._hipporag.index(self._docs)
        self._built = True
        if on_progress:
            on_progress(1, 1)

    def retrieve(self, question: str, top_k: int = None) -> str:
        if not self._docs:
            self._last_context = "(无检索结果)"
            return self._last_context
        if not self._built:
            self.finalize_ingest()
        k = top_k or self.top_k
        try:
            results = self._hipporag.retrieve([question], num_to_retrieve=k)
        except AssertionError as exc:
            # HippoRAG's graph/PPR path can assert when extracted facts do not
            # map back to phrase nodes on very small smoke graphs. Only fall
            # back to DPR for that specific case; re-raise unexpected asserts.
            msg = str(exc)
            if "phrase" not in msg.lower() and "node" not in msg.lower() and "graph" not in msg.lower():
                raise
            results = self._hipporag.retrieve_dpr([question], num_to_retrieve=k)
        if not results:
            self._last_context = "(无检索结果)"
            return self._last_context
        solution = results[0]
        docs = list(getattr(solution, "docs", []) or [])
        scores = list(getattr(solution, "doc_scores", []) or [])
        lines = []
        for index, doc in enumerate(docs[:k]):
            score = scores[index] if index < len(scores) else ""
            score_text = f" score={score:.3f}" if isinstance(score, (int, float)) else ""
            lines.append(f"[hipporag:official{score_text}] {doc}")
        self._last_context = "\n\n".join(lines) if lines else "(无检索结果)"
        return self._last_context

    def get_memory_snapshot(self) -> dict:
        return {
            "backend": "official_hipporag",
            "llm_model": self.llm_model,
            "embedding_model": self.embedding_model,
            "save_dir": str(self._save_dir),
            "n_docs": len(self._docs),
            "built": self._built,
            "text": "\n---\n".join(doc["text"] for doc in self._session_docs) or "(empty)",
        }
