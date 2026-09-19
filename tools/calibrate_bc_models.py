"""Bounded model calibration on frozen ORIGINAL factory artifacts, not generation."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.provenance import public_protocol
from llm_trace import trace_scope
from pipeline.grounding_review import review_grounding

MODELS = {"glm-5.3-flash": {"input_cny_per_m": 0.632, "output_cny_per_m": 2.212},
          "gpt-5-mini": {"input_cny_per_m": 1.25, "output_cny_per_m": 10.0},
          "gpt-5.4-mini": {"input_cny_per_m": 3.75, "output_cny_per_m": 22.5}}
CASES = [(30, "historical_date_extremum", "contradicted_or_unsupported"),
         (0, "normal_current_state", "supported"),
         (55, "unspecified_repeated_events", "ambiguous_or_unsupported")]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True, choices=MODELS)
    ap.add_argument("--source", type=Path, default=ROOT / "output/runs/seed_insurance_e2e_20260916")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--isolated-reference", action="store_true")
    ap.add_argument("--reasoning-effort", choices=["low", "medium"], default="low")
    ap.add_argument("--reference-audit-prompt", type=Path,
                    help="Experimental replacement for the existing audit system prompt, never a production default")
    ap.add_argument("--question-index", type=int, action="append",
                    help="Zero-based source question index; at most three, default is the frozen insurance group")
    ap.add_argument("--omit-public-rules", action="store_true",
                    help="Component ablation only: remove public rule documents from an in-memory corpus copy")
    args = ap.parse_args()
    audit_prompt = args.reference_audit_prompt.read_text(encoding="utf-8") if args.reference_audit_prompt else None
    if audit_prompt is not None and not audit_prompt.strip():
        ap.error("An experimental audit prompt cannot be empty")
    if args.question_index and (len(args.question_index) > 3 or min(args.question_index) < 0
                                or len(set(args.question_index)) != len(args.question_index)):
        ap.error("Choose one to three distinct nonnegative source indices")
    args.out.mkdir(parents=True, exist_ok=False)
    source = {name: json.loads((args.source / name).read_text(encoding="utf-8"))
              for name in ("00_about.json", "04_questions.json", "05_corpus.json")}
    qs = []
    cases = ([(i, f"source_question_{i}", "independent assessment required") for i in args.question_index]
             if args.question_index else CASES)
    for index, name, expected in cases:
        q = dict(source["04_questions.json"][index]); q.setdefault("qid", f"legacy-insurance-04-{index}")
        qs.append(q)
    corpus = source["05_corpus.json"]
    removed = []
    if args.omit_public_rules:
        for session in corpus.get("corpus", corpus)["sessions"]:
            removed.extend(doc.get("doc_id") for doc in session["docs"] if doc.get("public_rule_refs"))
            session["docs"] = [doc for doc in session["docs"] if not doc.get("public_rule_refs")]
        if not removed:
            ap.error("The requested ablation would remove no public rule document")
    protocol = public_protocol(source["00_about.json"])
    max_calls = len(qs) * (3 if args.isolated_reference else 2)
    manifest = {"purpose": "component calibration on known development cases; not original end-to-end acceptance",
        "created_utc": datetime.now(timezone.utc).isoformat(), "model": args.model,
        "max_physical_calls": max_calls, "max_output_tokens_per_call": 6144,
        "isolated_reference_audit": args.isolated_reference,
        "reasoning_effort": args.reasoning_effort if args.model.startswith("gpt-") else None,
        "ablation": {"omitted_public_rule_doc_ids": removed, "source_artifacts_unchanged": True},
        "source": str(args.source.resolve()), "source_hashes": {
            name: hashlib.sha256((args.source/name).read_bytes()).hexdigest() for name in source},
        "cases": [{"source_index_zero_based": i, "name": name, "expected_observation": e}
                  for i, name, e in cases],
        "pricing": {**MODELS[args.model], "source": "https://rmb.dmxapi.cn/",
                    "basis": "public uncached rates, not an invoice"},
        "status": "running"}
    def save(name, value):
        (args.out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    save("manifest.json", manifest)
    if audit_prompt is not None:
        from pipeline import reference_audit
        reference_audit.SYSTEM = audit_prompt
        (args.out / "experiment_reference_audit_prompt.txt").write_text(audit_prompt, encoding="utf-8")
        manifest["experimental_reference_audit_prompt"] = {
            "source": str(args.reference_audit_prompt.resolve()),
            "sha256": hashlib.sha256(audit_prompt.encode("utf-8")).hexdigest(),
            "production_default_changed": False}
        save("manifest.json", manifest)
    save("frozen_inputs.json", {"questions": qs, "corpus": corpus, "public_protocol": protocol})
    import config
    profile = {"token_limit_parameter": "max_tokens", "http_timeout_seconds": 150,
               "deadline_seconds": 150, "response_format": {"type": "json_object"}}
    if args.model.startswith("gpt-"):
        profile.update(token_limit_parameter="max_completion_tokens", reasoning_effort=args.reasoning_effort,
                       omit_parameters=["temperature", "top_p"])
    transport = {"version": "chat-completions-transport/v1", "profiles": {"calibration": profile},
                 "default_profile": "calibration", "model_profiles": {args.model: "calibration"}}
    save("transport.json", transport)
    calls = 0
    def caller(step, messages, **kwargs):
        nonlocal calls
        if calls >= max_calls or kwargs.get("model") != args.model or kwargs.get("retries") != 1:
            raise RuntimeError("Calibration model or physical request limit violated")
        calls += 1
        print(f"{args.model}: {calls}/{max_calls} {step}", flush=True)
        with trace_scope(args.out / "llm_attempts.jsonl", step):
            return config.chat_json(messages, transport=transport, **kwargs)
    def record(event):
        with (args.out/"review_events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False)+"\n")
    try:
        kept, report, review = review_grounding(qs, corpus, protocol, chat_json=caller,
            model=args.model, max_calls=max_calls, max_tokens=6144, max_input_chars=200000, record=record,
            isolated_reference=args.isolated_reference)
        save("06_semantic_review.json", review); save("06_grounding_report.json", report)
        save("selected_questions.json", kept)
        manifest["status"] = "completed" if report["execution_complete"] else "execution_incomplete"
        manifest["execution_complete"] = report["execution_complete"]
        manifest["semantic_selection"] = report["overall"]
        manifest["observations"] = [{"qid": row["source_qid"], "state": row["review_state"],
            **{key: row.get(key) for key in ("item_validity", "answerability", "reference_status")}}
            for row in review["items"]]
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:400])
        raise
    finally:
        manifest["physical_calls"] = calls
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "responses": 0}
        trace = args.out/"llm_attempts.jsonl"
        for line in trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []:
            event = json.loads(line)
            if event.get("event") == "response":
                usage = event.get("response", {}).get("usage") or {}
                for k in ("prompt_tokens", "completion_tokens"):
                    totals[k] += usage.get(k, 0) or 0
                totals["responses"] += 1
        manifest["usage"] = totals
        manifest["uncached_public_price_estimate_cny"] = (
            totals["prompt_tokens"] * MODELS[args.model]["input_cny_per_m"] +
            totals["completion_tokens"] * MODELS[args.model]["output_cny_per_m"]) / 1e6
        save("manifest.json", manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
