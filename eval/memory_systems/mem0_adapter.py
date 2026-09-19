"""
eval.memory_systems.mem0_adapter — mem0 记忆系统 adapter。

依赖:pip install mem0ai qdrant-client
服务:docker run -d --name qdrant -p 6333:6333 qdrant/qdrant

Embedding 用本地 BAAI/bge-small-zh-v1.5 (512维,huggingface provider,不走 API)。
LLM 事实抽取走 INGEST_LLM_BASE_URL(若设了),否则走 OPENAI_BASE_URL (DMXAPI)。
"""
from __future__ import annotations
import os, sys, time, uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from eval.memory_systems.base import (MemorySystem, execution_stage, ingest_receipt,
                                      require_response, configuration_fingerprint)
from eval.memory_systems.execution import SDKGuard, validate_json_completion
from eval.multi_system import header


class Mem0Adapter(MemorySystem):

    def __init__(self, top_k: int = 20, **kwargs):
        self.top_k = top_k
        self._qdrant_host = os.getenv("QDRANT_HOST", "localhost")
        self._qdrant_port = int(os.getenv("QDRANT_PORT", "6333"))
        self._collection = f"mem0_{uuid.uuid4().hex[:8]}"
        self._user_id = f"eval_{uuid.uuid4().hex[:8]}"
        self._last_context = ""
        self._guard = SDKGuard()
        with execution_stage("init"):
            self._rebuild_memory()

    def _rebuild_memory(self):
        from mem0 import Memory

        api_key = os.getenv("OPENAI_API_KEY")
        llm_base = os.getenv("INGEST_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        llm_model = os.getenv("INGEST_LLM_MODEL") or os.getenv("MODEL")

        cfg = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "host": self._qdrant_host, "port": self._qdrant_port,
                    "collection_name": self._collection,
                    "embedding_model_dims": 512,
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {"model": "BAAI/bge-small-zh-v1.5"},
            },
        }

        if api_key and llm_base and llm_model:
            cfg["llm"] = {
                "provider": "openai",
                "config": {
                    "model": llm_model,
                    "api_key": api_key,
                    "openai_base_url": llm_base,
                },
            }

        self.memory = Memory.from_config(cfg)
        active_llm = getattr(self.memory.llm, "config", None)
        actual_model = getattr(active_llm, "model", None)
        actual_base = getattr(active_llm, "openai_base_url", None)
        self._evaluation_llm = {
            "provider": cfg.get("llm", {}).get("provider", "sdk_default"),
            "model": (actual_model if isinstance(actual_model, str) else
                      cfg.get("llm", {}).get("config", {}).get("model", "not_exposed")),
            "endpoint_fingerprint": configuration_fingerprint(
                actual_base if isinstance(actual_base, str) else
                cfg.get("llm", {}).get("config", {}).get("openai_base_url")),
            "sdk_defaults": "not_exposed"}
        # 注入 socket 超时：mem0 的 OpenAIConfig 不支持 timeout 参数，
        # DMXAPI 死连接会让 generate_response 永久挂起。
        self._inject_llm_timeout()
        self._guard.watch(self.memory.llm, "generate_response", validate_json_completion)
        for attr, methods in {
            "embedding_model": ("embed", "embed_batch"),
            "vector_store": ("insert", "update", "delete", "search", "get", "list"),
            "db": ("add_history", "batch_add_history", "save_messages"),
        }.items():
            component = getattr(self.memory, attr, None)
            if component is not None:
                for method in methods:
                    self._guard.watch(component, method)
        # 关 thinking 不在 adapter 里各自做了：ingest LLM 统一走 embed_server 网关
        # (INGEST_LLM_BASE_URL → :9800),no-think / 剥 <think> / 超时重试 都在网关
        # 一处实现(services/embed_server.py)。mem0 指向网关即自动 no-think,见
        # services/run_eval.sh。

    def _inject_llm_timeout(self):
        """给 mem0 内部 OpenAI client 补上 socket 超时，防 DMXAPI 死连接钉死进程。"""
        llm = self.memory.llm
        client = getattr(llm, "client", None)
        if client is not None and hasattr(client, "_client"):
            client._client._timeout = httpx.Timeout(60.0, connect=10.0)

    def evaluation_config(self) -> dict:
        return {"configuration_status": "declared", "top_k": self.top_k,
                "llm": dict(self._evaluation_llm),
                "embedding_provider": "huggingface", "embedding_model": "BAAI/bge-small-zh-v1.5",
                "embedding_dimensions": 512, "vector_provider": "qdrant",
                "vector_endpoint_fingerprint": configuration_fingerprint(
                    f"{self._qdrant_host}:{self._qdrant_port}"),
                "sdk_operations": "serialized"}

    @staticmethod
    def _results(value, stage):
        require_response(isinstance(value, dict) and isinstance(value.get("results"), list), stage)
        require_response(all(isinstance(row, dict) for row in value["results"]), stage)
        return value["results"]

    def ingest_session(self, session: dict) -> dict:
        sid, date = session["session_id"], session["date"]
        n = 0
        for doc in session["docs"]:
            with execution_stage("ingest", requested_docs=len(session["docs"]), completed_docs=n):
                result = self._guard.run("ingest", self.memory.add,
                                         header(sid, date) + doc, user_id=self._user_id)
                self._results(result, "ingest")
            n += 1
        return ingest_receipt(n)

    def retrieve(self, question: str, top_k: int = None) -> str:
        self._last_context = ""
        with execution_stage("retrieve"):
            result = self._guard.run("retrieve", self.memory.search, query=question,
                filters={"user_id": self._user_id}, limit=top_k or self.top_k)
            memories = self._results(result, "retrieve")
            lines = []
            for m in memories:
                require_response(isinstance(m.get("memory"), str), "retrieve")
                lines.append(f"[score={m.get('score', 0):.2f}] {m['memory']}")
            self._last_context = "\n".join(lines) or "(无检索结果)"
            return self._last_context

    def get_memory_snapshot(self) -> dict:
        with execution_stage("snapshot"):
            result = self._guard.run("snapshot", self.memory.get_all,
                                     filters={"user_id": self._user_id})
            memories = self._results(result, "snapshot")
            text = "\n".join(m["memory"] for m in memories)
            return {"text": text or "(empty)", "n_memories": len(memories)}

    def reset(self) -> None:
        with execution_stage("reset"):
            from qdrant_client import QdrantClient
            qc = QdrantClient(host=self._qdrant_host, port=self._qdrant_port)
            qc.delete_collection(self._collection)
            self._collection = f"mem0_{uuid.uuid4().hex[:8]}"
            self._user_id = f"eval_{uuid.uuid4().hex[:8]}"
            self._last_context = ""
            self._rebuild_memory()
