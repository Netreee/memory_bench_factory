"""Letta adapter backed by the official ``letta-client`` SDK.

Letta is a service-backed memory system. This adapter does not emulate Letta in
process: it creates or reuses a Letta agent, writes session documents as
archival passages, and retrieves with Letta's passage search endpoint.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from eval.memory_systems.base import MemorySystem


def _require_letta():
    try:
        from letta_client import Letta
    except ImportError as exc:
        raise RuntimeError(
            "LettaAdapter requires the official letta-client package. "
            "Install it in the active environment, e.g. `python -m pip install letta-client`."
        ) from exc
    return Letta


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _passage_text(result: Any) -> str:
    passage = _value(result, "passage", result)
    text = _value(passage, "text")
    if text:
        return str(text)
    return str(passage)


class LettaAdapter(MemorySystem):
    """Official Letta SDK adapter for service-backed archival memory."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        agent_id: str | None = None,
        top_k: int = 5,
        **kwargs,
    ):
        Letta = _require_letta()
        self.base_url = base_url or os.environ.get("LETTA_BASE_URL", "http://localhost:8283")
        self.api_key = api_key or os.environ.get("LETTA_API_KEY") or "local"
        self._provided_agent_id = agent_id or os.environ.get("LETTA_AGENT_ID")
        self.top_k = top_k
        self._client = Letta(api_key=self.api_key, base_url=self.base_url)
        self._run_tag = f"mbf-smoke-{uuid.uuid4().hex[:12]}"
        self.reset()

    def reset(self) -> None:
        self._agent_id = self._provided_agent_id
        self._created_agent = False
        self._last_context = ""
        self._ingested: list[dict[str, Any]] = []

    def _ensure_agent(self) -> str:
        if self._agent_id:
            return self._agent_id

        model = os.environ.get("LETTA_MODEL") or os.environ.get("MODEL")
        kwargs: dict[str, Any] = {
            "name": f"memory-bench-smoke-{self._run_tag}",
            "include_base_tools": False,
            "include_default_source": True,
            "memory_blocks": [
                {"label": "human", "value": "Memory Bench Factory smoke-test user."},
                {"label": "persona", "value": "Store and retrieve benchmark facts faithfully."},
            ],
            "tags": [self._run_tag],
        }
        if model:
            kwargs["model"] = model

        agent = self._client.agents.create(**kwargs)
        self._agent_id = str(_value(agent, "id"))
        self._created_agent = True
        return self._agent_id

    def ingest_session(self, session: dict) -> dict:
        agent_id = self._ensure_agent()
        sid = session["session_id"]
        date = session.get("date", "")
        docs = session.get("docs", [])
        for index, doc in enumerate(docs, start=1):
            tags = [self._run_tag, f"session:{sid}", f"doc:{index}"]
            text = f"session_id={sid}\ndate={date}\n\n{doc}"
            self._client.agents.passages.create(agent_id, text=text, tags=tags)
            self._ingested.append({"session_id": sid, "date": date, "doc_id": f"s{sid}_d{index}", "text": doc})
        return {"agent_id": agent_id, "n_docs": len(docs), "run_tag": self._run_tag}

    def retrieve(self, question: str, top_k: int = None) -> str:
        k = top_k or self.top_k
        agent_id = self._ensure_agent()
        results = self._client.passages.search(agent_id=agent_id, query=question, limit=k, tags=[self._run_tag])
        rows = list(results) if results is not None else []
        if not rows:
            self._last_context = "(无检索结果)"
            return self._last_context

        lines = []
        for row in rows[:k]:
            score = _value(row, "score")
            score_text = f" score={score:.3f}" if isinstance(score, (int, float)) else ""
            lines.append(f"[letta:passage{score_text} agent={agent_id}] {_passage_text(row)}")
        self._last_context = "\n\n".join(lines)
        return self._last_context

    def get_memory_snapshot(self) -> dict:
        return {
            "agent_id": self._agent_id,
            "base_url": self.base_url,
            "run_tag": self._run_tag,
            "n_ingested": len(self._ingested),
            "text": "\n---\n".join(item["text"] for item in self._ingested),
        }
