"""
eval.qa_cache — QA 结果断点续传(每题判完即落盘,re-run 跳过已完成)。

为什么:语料 ingest 已被 embedding 缓存解决,但 QA 阶段(每系统 66 题 chat 作答 + 判分)
仍是长任务——实测后台 run 跑 ~10-15 分钟就被 stop,3 系统 ~38 分钟撑不到完。
把【每题判完的结果】按 (bench_id, system, question_hash) 落盘:re-run 跳过已完成的,
多次累积到全跑完。同 embedding 缓存思路,作用于 QA。

存储:output/eval/_qa_cache/<bench_id>__<system>.jsonl —— 每行一条 record(含 pred/correct)。
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "output" / "eval" / "_qa_cache"

_lk = threading.Lock()


def bench_id(questions: list) -> str:
    """稳定标识一套题及其评分合同；题面或评分规则变化都会换缓存命名空间。"""
    h = hashlib.sha1()
    for q in questions:
        payload = {
            "question": q.get("question", ""),
            "capability": q.get("capability"),
            "gt": q.get("gt"),
            "strict_scoring": q.get("strict_scoring"),
        }
        h.update((json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\x00").encode("utf-8"))
    return h.hexdigest()[:16]


def qhash(question_or_item) -> str:
    """单题缓存键；新调用传完整题对象，旧调用传字符串时仍向后兼容。"""
    if isinstance(question_or_item, dict):
        payload = {
            "question": question_or_item.get("question", ""),
            "capability": question_or_item.get("capability"),
            "gt": question_or_item.get("gt"),
            "strict_scoring": question_or_item.get("strict_scoring"),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    else:
        raw = str(question_or_item)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _path(bid: str, system: str) -> Path:
    return CACHE_DIR / f"{bid}__{system}.jsonl"


def load(bid: str, system: str) -> dict:
    """读已完成结果:{qhash: record}。损坏行跳过。"""
    p = _path(bid, system)
    d = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                qh = r.get("_qh")
                if qh:
                    d[qh] = r          # 同题多次 → 后写覆盖(取最新)
            except Exception:
                pass
    return d


def append(bid: str, system: str, rec: dict) -> None:
    """追加一条已判结果(线程安全 + 立即落盘)。仅存 JSON 可序列化字段。"""
    try:
        slim = {k: v for k, v in rec.items()
                if k in ("_qh", "line", "capability", "question", "gt", "aux", "qid",
                         "gold_set", "mode", "judgeable", "pred", "correct", "strict_scoring",
                         "bridge_extracted", "error", "judge_error")}
        with _lk:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(_path(bid, system), "a", encoding="utf-8") as f:
                f.write(json.dumps(slim, ensure_ascii=False) + "\n")
    except Exception:
        pass   # 落盘失败不致命:本轮结果仍在内存,下轮重判


def done_count(bid: str, system: str) -> int:
    return len(load(bid, system))
