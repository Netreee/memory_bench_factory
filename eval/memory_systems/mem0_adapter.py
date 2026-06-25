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

from eval.memory_systems.base import MemorySystem
from eval.multi_system import header


class Mem0Adapter(MemorySystem):

    def __init__(self, top_k: int = 20, **kwargs):
        self.top_k = top_k
        self._qdrant_host = os.getenv("QDRANT_HOST", "localhost")
        self._qdrant_port = int(os.getenv("QDRANT_PORT", "6333"))
        self._collection = f"mem0_{uuid.uuid4().hex[:8]}"
        self._user_id = f"eval_{uuid.uuid4().hex[:8]}"
        self._last_context = ""
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
        # 注入 socket 超时：mem0 的 OpenAIConfig 不支持 timeout 参数，
        # DMXAPI 死连接会让 generate_response 永久挂起。
        self._inject_llm_timeout()
        # 关 thinking 不在 adapter 里各自做了：ingest LLM 统一走 embed_server 网关
        # (INGEST_LLM_BASE_URL → :9800),no-think / 剥 <think> / 超时重试 都在网关
        # 一处实现(services/embed_server.py)。mem0 指向网关即自动 no-think,见
        # services/run_eval.sh。

    def _inject_llm_timeout(self):
        """给 mem0 内部 OpenAI client 补上 socket 超时，防 DMXAPI 死连接钉死进程。"""
        try:
            llm = self.memory.llm
            client = getattr(llm, 'client', None)
            if client and hasattr(client, '_client'):
                client._client._timeout = httpx.Timeout(60.0, connect=10.0)
        except Exception:
            pass

    def ingest_session(self, session: dict) -> dict:
        sid = session["session_id"]
        date = session["date"]
        n = 0
        for doc in session["docs"]:
            text = header(sid, date) + doc
            try:
                self.memory.add(text, user_id=self._user_id)
            except Exception as e:
                print(f"[mem0] ingest 失败 session={sid}: {type(e).__name__}: {str(e)[:80]}")
            n += 1
        return {"n_docs": n}

    def retrieve(self, question: str, top_k: int = None) -> str:
        k = top_k or self.top_k
        results = self.memory.search(query=question,
                                     filters={"user_id": self._user_id},
                                     limit=k)
        memories = results.get("results", [])
        if not memories:
            self._last_context = "(无检索结果)"
            return self._last_context

        lines = []
        for m in memories:
            text = m.get("memory", "")
            score = m.get("score", 0)
            lines.append(f"[score={score:.2f}] {text}")
        self._last_context = "\n".join(lines)
        return self._last_context

    def get_memory_snapshot(self) -> dict:
        all_mems = self.memory.get_all(filters={"user_id": self._user_id})
        memories = all_mems.get("results", [])
        text = "\n".join(m.get("memory", "") for m in memories)
        return {"text": text or "(empty)", "n_memories": len(memories)}

    def reset(self) -> None:
        try:
            from qdrant_client import QdrantClient
            qc = QdrantClient(host=os.getenv("QDRANT_HOST", "localhost"),
                              port=int(os.getenv("QDRANT_PORT", "6333")))
            qc.delete_collection(self._collection)
        except Exception:
            pass
        self._collection = f"mem0_{uuid.uuid4().hex[:8]}"
        self._user_id = f"eval_{uuid.uuid4().hex[:8]}"
        self._last_context = ""
        self._rebuild_memory()
