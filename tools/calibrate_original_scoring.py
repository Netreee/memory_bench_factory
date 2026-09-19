"""Small, frozen answer-policy calibration through the original evaluator.

This is a component experiment. It neither generates nor releases benchmarks.
Expected labels remain outside every model request.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reference-mode", choices=["isolated", "two_role"], default="isolated")
    ap.add_argument("--grading-mode", choices=["direct", "independent_reader"], default="direct")
    ap.add_argument("--reference-review", type=Path, help="Reuse an exact existing review; SemanticJudge revalidates its input and policy bindings")
    ap.add_argument("--reasoning-effort", choices=["low", "medium"], default="low")
    ap.add_argument("--model", choices=["gpt-5.4-mini", "glm-4.7-nothinking"], default="gpt-5.4-mini")
    args = ap.parse_args()
    frozen = json.loads(args.cases.read_text(encoding="utf-8"))
    if len(frozen["predictions"]) > 6:
        ap.error("Calibration admits at most six frozen answers")
    args.out.mkdir(parents=True, exist_ok=False)
    from pipeline.factory import ANSWER_PROTOCOL
    from pipeline.semantic_review import review_questions
    from eval.provenance import public_protocol
    from eval.semantic_judge import SemanticJudge, DIRECT_METHOD
    from eval.answer_task_review import POLICY_VERSION, VERSION as READER_METHOD
    from llm_trace import trace_scope
    import config
    model = args.model
    protocol = public_protocol({"answer_protocol": ANSWER_PROTOCOL})
    question, corpus = frozen["question"], frozen["corpus"]
    transport = {"version": "chat-completions-transport/v1", "default_profile": "low",
        "model_profiles": {model: "low"}, "profiles": {"low": {
            "token_limit_parameter": "max_completion_tokens", "reasoning_effort": args.reasoning_effort,
            "omit_parameters": ["temperature", "top_p"], "response_format": {"type": "json_object"},
            "http_timeout_seconds": 150, "deadline_seconds": 150}}}
    if model == "glm-4.7-nothinking":
        transport["profiles"]["low"] = {
            "token_limit_parameter": "max_tokens", "response_format": {"type": "json_object"},
            "http_timeout_seconds": 150, "deadline_seconds": 150}
    price_input, price_output = {"gpt-5.4-mini": (3.75, 22.5),
                                 "glm-4.7-nothinking": (2.37, 2.37 * 4.683544)}[model]
    method = READER_METHOD if args.grading_mode == "independent_reader" else DIRECT_METHOD
    answer_calls = len(frozen["predictions"]) * (2 if args.grading_mode == "independent_reader" else 1)
    physical_limit = (0 if args.reference_review else 3) + answer_calls
    calls, stopped = 0, False
    def save(name, value):
        (args.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    save("frozen_inputs.json", {**frozen, "public_protocol": protocol})
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(), "model": model,
        "source_hash": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "scope": "Six pre-authored answers on a small original-material component fixture; not production accuracy",
        "max_physical_calls": physical_limit, "transport": transport, "reference_mode": args.reference_mode,
        "grading_mode": args.grading_mode,
        "reused_reference_review": str(args.reference_review.resolve()) if args.reference_review else None,
        "reused_reference_sha256": hashlib.sha256(args.reference_review.read_bytes()).hexdigest() if args.reference_review else None,
        "status": "running"}
    save("manifest.json", manifest)
    def caller(step, messages, **kw):
        nonlocal calls, stopped
        if stopped or calls >= physical_limit or kw.get("model") != model:
            raise RuntimeError("Calibration stopped or request/model limit exceeded")
        calls += 1
        print(f"{calls}/{physical_limit} {step}", flush=True)
        kw.update(retries=1, strict_json=True, max_tokens=6144, transport=transport)
        try:
            with trace_scope(args.out / "llm_attempts.jsonl", step):
                return config.chat_json(messages, **kw)
        except Exception:
            stopped = True
            raise
    try:
        review = (json.loads(args.reference_review.read_text(encoding="utf-8")) if args.reference_review else
            review_questions([question], corpus, protocol, reviewer_model=model,
                reader_model=model, reference_auditor_model=model if args.reference_mode == "isolated" else None, chat_json=caller,
                max_calls=3, max_tokens=6144))
        save("reference_review.json", review)
        judge = SemanticJudge(review, [question], corpus, protocol, model=model, chat_json=caller,
            max_calls=answer_calls, max_tokens=6144, scoring_policy=POLICY_VERSION, grading_method=method)
        rows = []
        for item in frozen["predictions"]:
            grade = judge(question, item["answer"])
            rows.append({**item, "grade": grade, "matches_frozen_expectation":
                         grade["verdict"] == item["expected_verdict"]})
            save("results.json", rows)
        manifest.update(status="completed" if all(row["grade"]["verdict"] in {"correct", "incorrect"} for row in rows)
                        else "judgements_pending", matches=sum(row["matches_frozen_expectation"] for row in rows),
            answer_count=len(rows), verdicts=[row["grade"]["verdict"] for row in rows])
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "responses": 0}
        path = args.out / "llm_attempts.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
            event = json.loads(line)
            if event.get("event") == "response":
                usage["responses"] += 1
                for key in ("prompt_tokens", "completion_tokens"):
                    usage[key] += (event.get("response", {}).get("usage") or {}).get(key, 0) or 0
        manifest.update(physical_calls=calls, usage=usage, stopped=stopped,
            uncached_public_price_estimate_cny=(usage["prompt_tokens"] * price_input + usage["completion_tokens"] * price_output)/1e6,
            pricing_source="https://rmb.dmxapi.cn/", pricing_basis="public rate estimate, not an invoice")
        save("manifest.json", manifest)
        print(json.dumps(manifest, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
