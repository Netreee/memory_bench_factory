"""
eval.memory_systems.zep_adapter — Zep 时序知识图 adapter。

两种模式:
  1. 自托管 Zep CE(docker-compose.eval.yml):ZEP_BASE_URL=http://localhost:8998
     LLM/embedding 走 DMXAPI,不需要 Zep Cloud key。
  2. Zep Cloud:ZEP_API_KEY(需花钱买 Zep Cloud 账号)

优先自托管(零额外费用)。adapter 自动根据有无 ZEP_API_KEY 选模式。
"""
from __future__ import annotations
import os, sys, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import requests as _req
from eval.memory_systems.base import MemorySystem
from eval.memory_systems._utils import header

CHUNK_LIMIT = 2400


def _split_text(text: str, limit: int = CHUNK_LIMIT) -> list:
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            if cur.strip():
                chunks.append(cur.strip())
            cur = line
        else:
            cur += "\n" + line if cur else line
    if cur.strip():
        chunks.append(cur.strip())
    return chunks or [text[:limit]]


class ZepAdapter(MemorySystem):
    """自动选择 Zep CE(自托管)或 Zep Cloud。"""

    def __init__(self, top_k: int = 10, ingest_wait: float = 2.0, **kwargs):
        self.top_k = top_k
        self.ingest_wait = ingest_wait
        self._last_context = ""

        cloud_key = os.getenv("ZEP_API_KEY")
        if cloud_key:
            self._mode = "cloud"
            self._init_cloud(cloud_key)
        else:
            self._mode = "ce"
            self._init_ce()

    # ── Zep CE(自托管,HTTP REST)──────────────────────────────────────────
    def _init_ce(self):
        self._base = os.getenv("ZEP_BASE_URL", "http://localhost:8998").rstrip("/")
        self._session = _req.Session()
        self._session.headers["Content-Type"] = "application/json"
        self._user_id = f"eval_{uuid.uuid4().hex[:8]}"
        self._session_id = f"sess_{uuid.uuid4().hex[:8]}"
        # 创建 user + session
        try:
            self._session.post(f"{self._base}/api/v1/users",
                               json={"user_id": self._user_id}, timeout=10)
            self._session.post(f"{self._base}/api/v1/sessions",
                               json={"session_id": self._session_id,
                                     "user_id": self._user_id},
                               timeout=10)
        except Exception as e:
            print(f"[zep-ce] 初始化警告: {e}")

    def _ingest_ce(self, session: dict) -> dict:
        sid = session["session_id"]
        date = session["date"]
        n = 0
        for doc in session["docs"]:
            text = header(sid, date) + doc
            chunks = _split_text(text)
            for chunk in chunks:
                msgs = [
                    {"role_type": "user", "role": "user", "content": chunk},
                    {"role_type": "assistant", "role": "assistant", "content": "已记录。"},
                ]
                try:
                    self._session.post(
                        f"{self._base}/api/v1/sessions/{self._session_id}/memory",
                        json={"messages": msgs}, timeout=30)
                except Exception as e:
                    print(f"[zep-ce] ingest 失败 session={sid}: {e}")
            n += 1
        if self.ingest_wait > 0:
            time.sleep(self.ingest_wait)
        return {"n_docs": n}

    def _retrieve_ce(self, question: str, top_k: int) -> str:
        """CE 模式: 真语义检索 (/search)。
        embedding 已切本地 bge-small-zh-v1.5 (通过 embed_server)，不再超时。
        """
        try:
            r = self._session.post(
                f"{self._base}/api/v1/sessions/{self._session_id}/search",
                json={"text": question, "limit": top_k},
                timeout=30)
            r.raise_for_status()
            # Zep CE 的 /search 直接返回 JSON 数组 [{message:{content}}, ...]，
            # 不是 {"results": [...]}。旧代码 .get("results") 在 list 上抛 AttributeError
            # → 每题都"(检索失败)"→ 答"信息不足"→ 全错。两种结构都兼容。
            payload = r.json()
            results = payload if isinstance(payload, list) else payload.get("results", [])
            lines = [
                ((it.get("message") or {}).get("content") or it.get("content") or "")
                for it in results
            ]
            return "\n".join(x for x in lines if x) or "(无检索结果)"
        except Exception as e:
            return f"(检索失败: {type(e).__name__})"

    def _snapshot_ce(self) -> dict:
        try:
            r = self._session.get(
                f"{self._base}/api/v1/sessions/{self._session_id}/memory",
                timeout=15)
            r.raise_for_status()
            data = r.json()
            facts = [f.get("fact", "") for f in (data.get("facts") or [])]
            return {"text": "\n".join(facts) if facts else "(empty)",
                    "n_facts": len(facts)}
        except Exception:
            return {"text": "(error)", "n_facts": 0}

    def _reset_ce(self):
        try:
            self._session.delete(
                f"{self._base}/api/v1/sessions/{self._session_id}/memory",
                timeout=10)
        except Exception:
            pass
        self._session_id = f"sess_{uuid.uuid4().hex[:8]}"
        try:
            self._session.post(f"{self._base}/api/v1/sessions",
                               json={"session_id": self._session_id,
                                     "user_id": self._user_id},
                               timeout=10)
        except Exception as e:
            print(f"[zep-ce] reset 创建新 session 失败: {e}")

    # ── Zep Cloud(pip zep-cloud)──────────────────────────────────────────
    def _init_cloud(self, api_key):
        from zep_cloud.client import Zep
        from zep_cloud.types import CreateUserRequest
        self.client = Zep(api_key=api_key)
        self._user_id = f"eval_{uuid.uuid4().hex[:8]}"
        self._session_id = f"sess_{uuid.uuid4().hex[:8]}"
        try:
            self.client.user.add(CreateUserRequest(user_id=self._user_id))
        except Exception:
            pass

    def _ingest_cloud(self, session: dict) -> dict:
        from zep_cloud import Message
        sid = session["session_id"]
        date = session["date"]
        n = 0
        for doc in session["docs"]:
            text = header(sid, date) + doc
            chunks = _split_text(text)
            for chunk in chunks:
                msgs = [
                    Message(role_type="user", role="user", content=chunk),
                    Message(role_type="assistant", role="assistant", content="已记录。"),
                ]
                try:
                    self.client.memory.add(self._session_id, messages=msgs)
                except Exception as e:
                    print(f"[zep-cloud] ingest 失败 session={sid}: {e}")
            n += 1
        if self.ingest_wait > 0:
            time.sleep(self.ingest_wait)
        return {"n_docs": n}

    def _retrieve_cloud(self, question: str, top_k: int) -> str:
        try:
            result = self.client.memory.search(
                self._session_id, text=question, limit=top_k,
                search_type="mmr", search_scope="facts")
            lines = []
            if hasattr(result, 'results') and result.results:
                for r in result.results:
                    if hasattr(r, 'fact') and r.fact:
                        lines.append(f"[fact] {r.fact}")
                    elif hasattr(r, 'content') and r.content:
                        lines.append(r.content)
            return "\n".join(lines) if lines else "(无检索结果)"
        except Exception as e:
            return f"(检索失败: {type(e).__name__})"

    def _snapshot_cloud(self) -> dict:
        try:
            mem = self.client.memory.get(self._session_id)
            facts = [f.fact for f in (mem.facts or []) if f.fact]
            return {"text": "\n".join(facts) if facts else "(empty)",
                    "n_facts": len(facts)}
        except Exception:
            return {"text": "(error)", "n_facts": 0}

    def _reset_cloud(self):
        try:
            self.client.memory.delete(self._session_id)
        except Exception:
            pass
        self._session_id = f"sess_{uuid.uuid4().hex[:8]}"

    # ── 统一接口(按 mode 分派)────────────────────────────────────────────
    def ingest_session(self, session: dict) -> dict:
        if self._mode == "cloud":
            return self._ingest_cloud(session)
        return self._ingest_ce(session)

    def retrieve(self, question: str, top_k: int = None) -> str:
        k = top_k or self.top_k
        if self._mode == "cloud":
            ctx = self._retrieve_cloud(question, k)
        else:
            ctx = self._retrieve_ce(question, k)
        self._last_context = ctx
        return ctx

    def get_memory_snapshot(self) -> dict:
        if self._mode == "cloud":
            return self._snapshot_cloud()
        return self._snapshot_ce()

    def finalize_ingest(self, on_progress=None) -> None:
        """等待 Zep 异步 summarizer 完成。CE 模式轮询 summary;Cloud 模式固定等待。"""
        if self._mode == "cloud":
            time.sleep(10)
            return
        for i in range(12):
            try:
                r = self._session.get(
                    f"{self._base}/api/v1/sessions/{self._session_id}/memory",
                    timeout=10)
                if r.ok:
                    data = r.json()
                    if data.get("summary") and data["summary"].get("content"):
                        return
            except Exception:
                pass
            if on_progress:
                on_progress(i + 1, 12)
            time.sleep(5)

    def reset(self) -> None:
        if self._mode == "cloud":
            self._reset_cloud()
        else:
            self._reset_ce()
        self._last_context = ""
