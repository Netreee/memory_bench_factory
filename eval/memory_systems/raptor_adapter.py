"""RAPTOR adapter backed by the official local RAPTOR implementation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from eval.memory_systems.base import MemorySystem
from eval.memory_systems._utils import DEFAULT_EMBEDDING_MODEL, ensure_hf_home


ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACE_ROOT = ROOT.parent
RAPTOR_ROOT = WORKSPACE_ROOT / "raptor"
if RAPTOR_ROOT.exists() and str(RAPTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(RAPTOR_ROOT))


class _SmokeSummarizationModel:
    """Small deterministic summarizer implementing RAPTOR's model protocol."""

    def summarize(self, context, max_tokens=150):
        text = " ".join(str(context or "").split())
        return text[: max(200, int(max_tokens) * 6)]


class _SmokeQAModel:
    """QA model is unused by smoke retrieval but required by RAPTOR config."""

    def answer_question(self, context, question):
        return str(context or "")


class SmokeEmbeddingModel:
    """Sentence-transformers embedding model for RAPTOR.

    Defined at module level so it is not re-created on every reset() call.
    """

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)

    def create_embedding(self, text):
        return self.model.encode(str(text or ""), convert_to_numpy=True)


class RaptorAdapter(MemorySystem):
    """MemorySystem wrapper over official RAPTOR tree build/retrieve APIs."""

    def __init__(
        self,
        top_k: int = 5,
        embedding_model: str | None = None,
        max_tokens: int = 100,
        **kwargs,
    ):
        ensure_hf_home(ROOT)
        self.top_k = top_k
        self.embedding_model_name = (
            embedding_model
            or os.getenv("RAPTOR_EMBEDDING_MODEL")
            or DEFAULT_EMBEDDING_MODEL
        )
        self.max_tokens = max_tokens
        self._docs: list[str] = []
        self._session_docs: list[dict[str, Any]] = []
        self._last_context = ""
        self._ra = None
        self._built = False
        self._init_raptor()

    def _init_raptor(self) -> None:
        try:
            from raptor import (
                BaseEmbeddingModel,
                BaseQAModel,
                BaseSummarizationModel,
                RetrievalAugmentation,
                RetrievalAugmentationConfig,
            )
        except Exception as exc:
            raise ImportError(
                "RAPTOR official dependencies are not available. "
                "Run this adapter in .conda-raptor or install raptor/requirements.txt."
            ) from exc

        class _RaptorEmbeddingModel(SmokeEmbeddingModel, BaseEmbeddingModel):
            pass

        class _RaptorSummarizationModel(_SmokeSummarizationModel, BaseSummarizationModel):
            pass

        class _RaptorQAModel(_SmokeQAModel, BaseQAModel):
            pass

        embedding = _RaptorEmbeddingModel(self.embedding_model_name)
        config = RetrievalAugmentationConfig(
            embedding_model=embedding,
            summarization_model=_RaptorSummarizationModel(),
            qa_model=_RaptorQAModel(),
            tb_max_tokens=self.max_tokens,
            tb_num_layers=3,
            tb_summarization_length=100,
            tb_top_k=max(2, self.top_k),
            tr_top_k=self.top_k,
        )
        self._ra = RetrievalAugmentation(config=config)

    def reset(self) -> None:
        self._docs = []
        self._session_docs = []
        self._last_context = ""
        self._built = False
        self._init_raptor()

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
        if not self._docs:
            return
        combined = "\n\n".join(self._docs)
        self._ra.add_documents(combined)
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
        context = self._ra.retrieve(
            question,
            top_k=k,
            max_tokens=1800,
            collapse_tree=True,
            return_layer_information=False,
        )
        self._last_context = str(context or "").strip() or "(无检索结果)"
        return self._last_context

    def get_memory_snapshot(self) -> dict:
        return {
            "backend": "official_raptor",
            "embedding_model": self.embedding_model_name,
            "n_docs": len(self._docs),
            "built": self._built,
            "text": "\n---\n".join(doc["text"] for doc in self._session_docs) or "(empty)",
        }

