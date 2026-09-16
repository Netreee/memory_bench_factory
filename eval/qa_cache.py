"""Context-bound prediction and grading caches; failures are never grades."""
from __future__ import annotations
import hashlib
import json
import threading
from pathlib import Path
from eval.grading import JUDGE_VERSION, is_scored

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "output" / "eval" / "_qa_cache"
_lk = threading.Lock()
CACHE_VERSION = 2


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def bench_id(questions: list, context: dict | None = None) -> str:
    """All question fields plus solver model/corpus/protocol identify a run."""
    return digest({"schema": CACHE_VERSION, "questions": questions, "context": context})[:24]


def qhash(question_or_item, context: dict | None = None, *, grading: bool = True) -> str:
    return digest({"schema": CACHE_VERSION, "question": question_or_item, "context": context,
                   "judge_version": JUDGE_VERSION if grading else None})


def _path(bid: str, system: str, *, predictions: bool = False) -> Path:
    # System labels may be user-supplied model names, not filesystem paths.
    suffix = "__predictions" if predictions else ""
    return CACHE_DIR / f"{digest(bid)[:24]}__{digest(system)[:16]}{suffix}.jsonl"


def _valid_prediction(rec):
    pred = rec.get("pred")
    return (not rec.get("error") and isinstance(pred, str) and bool(pred.strip())
            and not (pred.lstrip().startswith("[") and "ERROR" in pred))


def _load(bid, system, predictions=False):
    path = _path(bid, system, predictions=predictions)
    records = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if (rec.get("_cache_version") == CACHE_VERSION and rec.get("_qh")
                        and _valid_prediction(rec) and (predictions or is_scored(rec))):
                    records[rec["_qh"]] = rec
            except (ValueError, TypeError, AttributeError):
                continue
    return records


def load(bid: str, system: str) -> dict:
    return _load(bid, system)


def load_predictions(bid: str, system: str) -> dict:
    return _load(bid, system, predictions=True)


def _append(bid, system, rec, predictions=False):
    if not _valid_prediction(rec) or (not predictions and not is_scored(rec)):
        return
    if predictions:
        rec = {key: rec[key] for key in ("_qh", "pred", "bridge_extracted") if key in rec}
    row = {**rec, "_cache_version": CACHE_VERSION}
    try:
        with _lk:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with _path(bid, system, predictions=predictions).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    except OSError:
        return False  # cache availability does not change solver/judge validity
    return True


def append(bid: str, system: str, rec: dict) -> None:
    _append(bid, system, rec)


def append_prediction(bid: str, system: str, rec: dict) -> None:
    _append(bid, system, rec, predictions=True)


def done_count(bid: str, system: str) -> int:
    return len(load(bid, system))
