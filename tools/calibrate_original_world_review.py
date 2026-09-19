"""Bounded cheap-model calls to the original world review on frozen controls."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def simple_id(value):
    return (isinstance(value, str) and value not in ("", ".", "..")
            and Path(value).name == value and not Path(value).is_absolute())


def load_inputs(args, ap):
    hashes, originals = {}, {}
    if args.source_run:
        if not simple_id(args.source_run):
            ap.error("Use a simple source run name")
        source = ROOT / "output/runs" / args.source_run
        originals = {name: (source / name).read_bytes()
                     for name in ("00_input.json", "01_whitepaper.json", "02_world.json")}
        hashes = {str((source / name).resolve()): hashlib.sha256(data).hexdigest()
                  for name, data in originals.items()}
        cases = [{"id": args.source_run, "task_input": json.loads(originals["00_input.json"]),
                  "whitepaper": json.loads(originals["01_whitepaper.json"]),
                  "world": json.loads(originals["02_world.json"])}]
    else:
        base = args.manifest.resolve().parent
        hashes[str(args.manifest.resolve())] = digest(args.manifest)
        entries = json.loads(args.manifest.read_text(encoding="utf-8"))["cases"]
        ids = [entry["id"] for entry in entries]
        if (not 1 <= len(entries) <= 6 or len(set(ids)) != len(ids)
                or not all(simple_id(name) for name in ids)):
            ap.error("Use one to six distinct controls with simple IDs")
        cases = []
        for entry in entries:
            path = (base / entry["file"]).resolve()
            if not path.is_relative_to(base):
                ap.error("Controls must stay within their manifest directory")
            hashes[str(path)] = digest(path)
            if hashes[str(path)] != entry["sha256"]:
                ap.error("Frozen control hash mismatch")
            case = json.loads(path.read_text(encoding="utf-8"))
            if case.get("id") != entry["id"] or not isinstance(case.get("expected"), dict):
                ap.error("Control identity or expected assessment is missing")
            cases.append(case)
    from pipeline.world_state import WorldState
    from pipeline.world_semantics import _project
    for case in cases:
        if any(not isinstance(case.get(key), dict) for key in ("task_input", "whitepaper", "world")):
            ap.error("Each case needs task_input, whitepaper and world objects")
        # Verify readable original inputs without constructing a provider client.
        # Expected outcomes and provenance never enter this model projection.
        _project(case["whitepaper"], WorldState.from_dict(case["world"]), case["task_input"])
    return cases, hashes, originals


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    inputs = ap.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source-run")
    inputs.add_argument("--manifest", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--model", choices=["gpt-5.4-mini", "glm-4.7-nothinking"], default="gpt-5.4-mini")
    ap.add_argument("--review-prompt", type=Path,
                    help="Frozen experimental prompt for the original reviewer; production default stays unchanged")
    ap.add_argument("--reasoning-effort", choices=["low", "medium"], default="low")
    ap.add_argument("--max-output-tokens", type=int, choices=[8192, 16384], default=8192,
                    help="Total completion budget including reasoning; experimental override, never changes production")
    args = ap.parse_args()
    cases, input_hashes, originals = load_inputs(args, ap)
    if args.validate_only:
        print(json.dumps({"controls": [case["id"] for case in cases], "api_calls": 0}, ensure_ascii=False))
        return
    if args.out is None or args.out.exists():
        ap.error("Choose a fresh output directory")
    prompt = None
    if args.review_prompt:
        prompt = args.review_prompt.read_text(encoding="utf-8")
        if not prompt.strip():
            ap.error("Experimental review prompt must be nonempty")
        input_hashes[str(args.review_prompt.resolve())] = digest(args.review_prompt)
    import config
    from pipeline.run import Tracer
    from pipeline.world_state import WorldState
    from pipeline import world_semantics
    world_semantics.MAX_TOKENS = args.max_output_tokens
    if prompt is not None:
        world_semantics.SYSTEM = prompt
    model = args.model
    config.MODEL = config.REVIEWER_MODEL = model
    transport = {"version": "chat-completions-transport/v1", "default_profile": "low",
        "model_profiles": {model: "low"}, "profiles": {"low": {
            "token_limit_parameter": "max_completion_tokens", "reasoning_effort": args.reasoning_effort,
            "omit_parameters": ["temperature", "top_p"], "http_timeout_seconds": 150,
            "deadline_seconds": 150}}}
    if model == "glm-4.7-nothinking":
        transport["profiles"]["low"] = {"token_limit_parameter": "max_tokens",
            "http_timeout_seconds": 150, "deadline_seconds": 150}
    class OneCall(Tracer):
        def chat_json(self, step, messages, **kwargs):
            if self.n or step != "world.semantic_review" or kwargs.get("model") != model:
                raise RuntimeError("Calibration permits one original world review with the selected mini model")
            kwargs.update(transport=transport, retries=1, strict_json=True,
                          max_tokens=min(kwargs.get("max_tokens", args.max_output_tokens), args.max_output_tokens))
            return super().chat_json(step, messages, **kwargs)
    source_names = {"pipeline/" + name for name in world_semantics._implementation_hashes()}
    source_names.update(("pipeline/run.py", "config.py", "llm_transport.py", "llm_trace.py",
                         "tools/calibrate_original_world_review.py"))
    source_hashes = {name: digest(ROOT / name) for name in sorted(source_names)}
    args.out.mkdir(parents=True)
    for name, data in originals.items(): (args.out / name).write_bytes(data)
    if prompt is not None:
        (args.out / "experimental_review_prompt.txt").write_text(prompt, encoding="utf-8")
    profile = {"model": model, "max_calls": len(cases), "transport": transport,
        "max_output_tokens": args.max_output_tokens,
        "production_default_changed": False,
        "receipt_replay": "Requires this recorded prompt and completion-budget runtime profile; not a publication receipt",
        "source_run": args.source_run,
        "input_hashes": input_hashes, "source_hashes": source_hashes,
        "expected_labels_in_prompt": False,
        "scope": "Original world review development controls; no generation, repair, publication or general accuracy claim"}
    if args.review_prompt:
        profile["experimental_prompt"] = {"source": str(args.review_prompt.resolve()),
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "production_default_changed": False}
    (args.out / "experiment_profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = [{"id": case["id"], "status": "not_started", "decision": None,
             "expected": case.get("expected"), "semantic_reason_requires_independent_reading": True}
            for case in cases]

    def changed_sources():
        return [path for path, value in source_hashes.items()
                if not (ROOT / path).is_file() or digest(ROOT / path) != value]

    def inputs_unchanged():
        return all(Path(path).is_file() and digest(Path(path)) == value
                   for path, value in input_hashes.items())

    try:
        for case, row in zip(cases, rows):
            if not inputs_unchanged() or changed_sources():
                raise RuntimeError("Control input or implementation changed before dispatch")
            folder = args.out if args.source_run else args.out / case["id"]
            folder.mkdir(exist_ok=True)
            if not args.source_run:
                (folder / "frozen_control.json").write_text(
                    json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
            row["status"] = "started"
            try:
                report = world_semantics.review_world(case["whitepaper"], WorldState.from_dict(case["world"]),
                    OneCall(SimpleNamespace(dir=folder)), task_input=case["task_input"])
            except BaseException as error:
                row.update(status="runner_interrupted", error_type=type(error).__name__)
                raise
            (folder / "review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            raw = report.get("raw_output")
            row.update(status=report.get("status"),
                       decision=raw.get("decision") if isinstance(raw, dict) else None)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        unchanged, drift = inputs_unchanged(), changed_sources()
        summary = {"rows": rows, "inputs_unchanged": unchanged, "source_drift": drift,
                   "not_a_general_accuracy_estimate": True}
        (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if not unchanged or drift:
        raise RuntimeError("Control input or implementation changed during calibration")


if __name__ == "__main__":
    main()
