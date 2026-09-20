"""不依赖旧实现的离线控制面 diagnostic runner。

它只验证 benchmark 读取、逐题迭代和规范化产物写出，不生成可计分答案。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run(run_dir: Path, out_dir: Path, limit: int = 0) -> dict:
    run_dir = Path(run_dir)
    out_dir = Path(out_dir)
    corpus_obj = _read_json(run_dir / "05_corpus.json")
    corpus = corpus_obj.get("corpus", corpus_obj)
    sessions = corpus["sessions"]
    question_obj = _read_json(run_dir / "06_grounded_questions.json")
    questions = question_obj if isinstance(question_obj, list) else question_obj["questions"]
    selected = questions[:limit] if limit else questions

    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as fh:
        for index, question in enumerate(selected):
            text = question["question"]
            question_id = question.get("question_id") or hashlib.sha256(
                text.encode("utf-8")
            ).hexdigest()[:16]
            record = {
                "schema": "agent-harnesses.result/v1",
                "question_id": question_id,
                "question_index": index,
                "status": "diagnostic_only",
                "answer": None,
                "judgeable": False,
                "correct": None,
                "error_type": None,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "schema": "agent-harnesses.diagnostic-summary/v1",
        "backend": "builtin_diagnostic",
        "n_sessions": len(sessions),
        "n_docs": sum(len(session["docs"]) for session in sessions),
        "n_questions_available": len(questions),
        "n_questions_checked": len(selected),
        "scorable": False,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline control-plane diagnostic")
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            run(Path(args.run), Path(args.out), args.limit),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
