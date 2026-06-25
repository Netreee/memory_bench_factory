"""
eval.memory_systems.memos_adapter — MemOS (MemTensor) 记忆系统 adapter。

纯 HTTP API,无额外 pip 依赖(只用 requests)。
环境:MEMOS_API_KEY(云)或 MEMOS_BASE_URL(自托管,默认 http://localhost:5230)。
"""
from __future__ import annotations
import os, sys, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import requests
from eval.memory_systems.base import MemorySystem
from eval.multi_system import header


class MemOSAdapter(MemorySystem):

    def __init__(self, top_k: int = 20, **kwargs):
        self.top_k = top_k
        self.api_key = kwargs.get("api_key") or os.getenv("MEMOS_API_KEY")
        default_base = ("https://memos.memtensor.cn/api/openmem/v1" if self.api_key
                        else "http://localhost:5230/api/openmem/v1")
        self.base_url = (kwargs.get("base_url") or os.getenv("MEMOS_BASE_URL", default_base)).rstrip("/")
        self._user_id = f"eval_{uuid.uuid4().hex[:8]}"
        self._session = requests.Session()
        self._session.headers["Content-Type"] = "application/json"
        if self.api_key:
            self._session.headers["Authorization"] = f"Token {self.api_key}"
        self._last_context = ""
        self._conv_ids: list = []

    def _conv_id(self, date: str) -> str:
        return date.replace("-", "")

    def ingest_session(self, session: dict) -> dict:
        sid = session["session_id"]
        date = session["date"]
        conv_id = self._conv_id(date)
        self._conv_ids.append(conv_id)

        messages = []
        for doc in session["docs"]:
            text = header(sid, date) + doc
            messages.append({"role": "user", "content": text, "chat_time": date})
            messages.append({"role": "assistant", "content": "已记录。"})

        payload = {
            "user_id": self._user_id,
            "conversation_id": conv_id,
            "messages": messages,
        }
        try:
            r = self._session.post(f"{self.base_url}/add/message",
                                   json=payload, timeout=60)
            r.raise_for_status()
            data = r.json()
            if data.get("code") != 0:
                print(f"[memos] ingest 警告 session={sid}: {data.get('message')}")
        except Exception as e:
            print(f"[memos] ingest 失败 session={sid}: {type(e).__name__}: {str(e)[:80]}")
        return {"n_docs": len(session["docs"])}

    def retrieve(self, question: str, top_k: int = None) -> str:
        k = top_k or self.top_k
        payload = {
            "user_id": self._user_id,
            "query": question,
            "memory_limit_number": min(k, 25),
            "include_preference": True,
            "preference_limit_number": min(k, 25),
        }
        lines = []
        try:
            r = self._session.post(f"{self.base_url}/search/memory",
                                   json=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
            if data.get("code") == 0:
                d = data.get("data", {})
                for m in d.get("memory_detail_list", []):
                    val = m.get("memory_value", "")
                    key = m.get("memory_key", "")
                    text = f"{key}: {val}" if key and val else (val or key)
                    if text.strip():
                        score = m.get("relativity", 0)
                        lines.append(f"[score={score:.2f}] {text.strip()}")
                for p in d.get("preference_detail_list", []):
                    pref = p.get("preference", "")
                    if pref.strip():
                        lines.append(f"[pref] {pref.strip()}")
        except Exception as e:
            self._last_context = f"(检索失败: {type(e).__name__})"
            return self._last_context

        if not lines:
            self._last_context = "(无检索结果)"
        else:
            self._last_context = "\n".join(lines)
        return self._last_context

    def get_memory_snapshot(self) -> dict:
        all_msgs = []
        for cid in self._conv_ids:
            try:
                r = self._session.post(f"{self.base_url}/get/message",
                                       json={"user_id": self._user_id,
                                             "conversation_id": cid},
                                       timeout=15)
                r.raise_for_status()
                data = r.json()
                if data.get("code") == 0:
                    all_msgs.extend(data.get("data", {}).get("message_detail_list", []))
            except Exception:
                pass
        text = "\n".join(
            m.get("content", "") for m in all_msgs if m.get("content"))
        return {"text": text or "(empty)", "n_messages": len(all_msgs)}

    def reset(self) -> None:
        for cid in self._conv_ids:
            try:
                self._session.post(f"{self.base_url}/delete/message",
                                   json={"user_id": self._user_id,
                                         "conversation_id": cid},
                                   timeout=10)
            except Exception:
                pass
        self._conv_ids = []
        self._user_id = f"eval_{uuid.uuid4().hex[:8]}"
        self._last_context = ""
