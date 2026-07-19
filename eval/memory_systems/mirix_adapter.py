"""MIRIX adapter backed by the official ``MirixClient``.

MIRIX is a service-backed multi-agent memory system. This adapter initializes a
meta-agent through the official client, writes sessions via ``add``, and
retrieves through ``retrieve_with_conversation``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from eval.memory_systems.base import MemorySystem


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
MIRIX_SRC = WORKSPACE_ROOT / "MIRIX"
if MIRIX_SRC.exists() and str(MIRIX_SRC) not in sys.path:
    sys.path.insert(0, str(MIRIX_SRC))


def _require_mirix_client():
    try:
        from mirix import MirixClient
    except ImportError as exc:
        raise RuntimeError(
            "MIRIXAdapter requires the official MIRIX client package/source. "
            "Install the local MIRIX package in the active environment, e.g. "
            "`python -m pip install -e MIRIX --no-deps`, plus its client dependencies."
        ) from exc
    return MirixClient


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("MIRIXAdapter cannot run inside an already-running event loop.")


def _content_text(message: str) -> list[dict[str, str]]:
    return [{"type": "text", "text": message}]


def _memory_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("summary", "caption", "name", "title", "description", "value", "text"):
            if item.get(key):
                return str(item[key])
        return str(item)
    return str(item)


class MIRIXAdapter(MemorySystem):
    """Official MIRIX client adapter for multi-component service memory."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        user_id: str | None = None,
        top_k: int = 5,
        initialize: bool | None = None,
        **kwargs,
    ):
        MirixClient = _require_mirix_client()
        self.base_url = base_url or os.environ.get("MIRIX_BASE_URL") or os.environ.get("MIRIX_API_URL", "http://localhost:8531")
        self.api_key = api_key or os.environ.get("MIRIX_API_KEY")
        self.user_id = user_id or os.environ.get("MIRIX_USER_ID") or f"mbf-smoke-{uuid.uuid4().hex[:12]}"
        self.top_k = top_k
        self._client = MirixClient(
            api_key=self.api_key,
            base_url=self.base_url,
            write_scope="memory_bench_factory",
            read_scopes=["memory_bench_factory"],
            timeout=int(os.environ.get("MIRIX_TIMEOUT_SECONDS", "1800")),
        )
        self._initialize = initialize if initialize is not None else os.environ.get("MIRIX_SKIP_INITIALIZE") != "1"
        self.reset()

    def reset(self) -> None:
        self._last_context = ""
        self._initialized = False
        self._ingested: list[dict[str, Any]] = []

    def _config(self) -> dict:
        llm_model = os.environ.get("MIRIX_MODEL") or os.environ.get("INGEST_LLM_MODEL") or os.environ.get("MODEL")
        llm_base_url = os.environ.get("MIRIX_LLM_BASE_URL") or os.environ.get("INGEST_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        llm_api_key = os.environ.get("MIRIX_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or self.api_key
        embedding_model = os.environ.get("MIRIX_EMBEDDING_MODEL", "text-embedding-3-small")
        embedding_dim = int(os.environ.get("MIRIX_EMBEDDING_DIM", "1536"))

        return {
            "llm_config": {
                "model": llm_model or "gpt-4o-mini",
                "model_endpoint_type": os.environ.get("MIRIX_MODEL_ENDPOINT_TYPE", "openai"),
                "api_key": llm_api_key,
                "model_endpoint": llm_base_url or "https://api.openai.com/v1",
                "context_window": int(os.environ.get("MIRIX_CONTEXT_WINDOW", "128000")),
            },
            "embedding_config": {
                "embedding_model": embedding_model,
                "embedding_endpoint_type": os.environ.get("MIRIX_EMBEDDING_ENDPOINT_TYPE", "openai"),
                "api_key": os.environ.get("MIRIX_EMBEDDING_API_KEY") or llm_api_key,
                "embedding_endpoint": os.environ.get("MIRIX_EMBEDDING_BASE_URL") or llm_base_url or "https://api.openai.com/v1",
                "embedding_dim": embedding_dim,
            },
            "meta_agent_config": {
                "agents": [
                    {
                        "core_memory_agent": {
                            "blocks": [
                                {"label": "human", "value": "Memory Bench Factory smoke-test user."},
                                {"label": "persona", "value": "Store and retrieve benchmark facts faithfully."},
                            ]
                        }
                    },
                    "resource_memory_agent",
                    "semantic_memory_agent",
                    "episodic_memory_agent",
                    "procedural_memory_agent",
                    "knowledge_vault_memory_agent",
                ],
            },
        }

    def _ensure_initialized(self) -> None:
        if self._initialized or not self._initialize:
            return
        _run(self._client.initialize_meta_agent(config=self._config()))
        self._initialized = True

    def ingest_session(self, session: dict) -> dict:
        self._ensure_initialized()
        sid = session["session_id"]
        date = session.get("date", "")
        docs = session.get("docs", [])
        for index, doc in enumerate(docs, start=1):
            text = f"session_id={sid}\ndate={date}\n\n{doc}"
            _run(
                self._client.add(
                    user_id=self.user_id,
                    messages=[{"role": "user", "content": _content_text(text)}],
                    chaining=False,
                    async_add=False,
                    filter_tags={"scope": "memory_bench_factory", "session_id": str(sid)},
                )
            )
            self._ingested.append({"session_id": sid, "date": date, "doc_id": f"s{sid}_d{index}", "text": doc})
        return {"user_id": self.user_id, "n_docs": len(docs), "initialized": self._initialized}

    def retrieve(self, question: str, top_k: int = None) -> str:
        self._ensure_initialized()
        k = top_k or self.top_k
        result = _run(
            self._client.retrieve_with_conversation(
                user_id=self.user_id,
                messages=[{"role": "user", "content": _content_text(question)}],
                limit=k,
                filter_tags={"scope": "memory_bench_factory"},
            )
        )
        memories = result.get("memories", {}) if isinstance(result, dict) else {}
        lines: list[str] = []
        for memory_type, data in memories.items():
            if not isinstance(data, dict):
                continue
            items = data.get("items") or data.get("relevant") or data.get("recent") or []
            for item in items[:k]:
                lines.append(f"<{memory_type}_memory>\n{_memory_text(item)}\n</{memory_type}_memory>")
                if len(lines) >= k:
                    break
            if len(lines) >= k:
                break
        self._last_context = "\n\n".join(lines) if lines else "(无检索结果)"
        return self._last_context

    def get_memory_snapshot(self) -> dict:
        snapshot: dict[str, Any] = {
            "user_id": self.user_id,
            "base_url": self.base_url,
            "n_ingested": len(self._ingested),
            "text": "\n---\n".join(item["text"] for item in self._ingested),
        }
        if self._initialized:
            try:
                snapshot["components"] = _run(self._client.list_memory_components(user_id=self.user_id, memory_type="all", limit=50))
            except Exception as exc:  # keep audit snapshot best-effort
                snapshot["component_error"] = str(exc)
        return snapshot
