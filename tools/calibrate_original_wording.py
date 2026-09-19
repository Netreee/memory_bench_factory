"""Frozen component controls for original phrase.review; no benchmark creation."""
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    base = args.manifest.resolve().parent
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    entries = manifest["cases"]
    names = [entry["id"] for entry in entries]
    if (not 1 <= len(entries) <= 6 or len(set(names)) != len(names)
            or any(Path(name).name != name for name in names)):
        parser.error("Select one to six unique frozen controls with simple IDs")
    cases = []
    input_hashes = {str(args.manifest.resolve()): digest(args.manifest)}
    for entry in entries:
        path = (base / entry["file"]).resolve()
        if not path.is_relative_to(base):
            parser.error("Controls must remain within the manifest directory")
        input_hashes[str(path)] = digest(path)
        if input_hashes[str(path)] != entry["sha256"]:
            parser.error("Frozen control hash mismatch")
        case = json.loads(path.read_text(encoding="utf-8"))
        if (case["id"] != entry["id"] or not isinstance(case["question"], str)
                or not case["question"].strip() or not isinstance(case["contract"], dict)
                or case["expected"]["verdict"] not in {"equivalent", "revise", "unresolved"}):
            parser.error("Invalid control shape")
        cases.append(case)
    if args.validate_only:
        print(json.dumps({"controls": names, "api_calls": 0}, ensure_ascii=False))
        return
    if args.out is None or args.out.exists():
        parser.error("Choose a fresh output directory")
    import config
    from pipeline.run import Tracer
    from pipeline.question_wording import review_wording, WordingReviewExecutionError
    model = "gpt-5.4-mini"
    config.MODEL = config.REVIEWER_MODEL = model
    transport = {"version": "chat-completions-transport/v1", "default_profile": "control",
        "model_profiles": {model: "control"}, "profiles": {"control": {
            "token_limit_parameter": "max_completion_tokens", "reasoning_effort": "low",
            "omit_parameters": ["temperature", "top_p"], "http_timeout_seconds": 150,
            "deadline_seconds": 150}}}

    class OneReview(Tracer):
        def chat_json(self, step, messages, **kwargs):
            if self.n or step != "phrase.review" or kwargs.get("model") != model:
                raise RuntimeError("Control admits one original phrase.review using mini")
            kwargs.update(transport=transport, retries=1, strict_json=True)
            return super().chat_json(step, messages, **kwargs)

    sources = ["pipeline/question_wording.py", "pipeline/semantic_review.py",
               "pipeline/reference_audit.py", "pipeline/run.py", "config.py",
               "llm_transport.py", "llm_trace.py", "tools/calibrate_original_wording.py"]
    source_hashes = {name: digest(ROOT / name) for name in sources}
    args.out.mkdir(parents=True)

    def save(name, data):
        (args.out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    save("experiment_profile.json", {"model": model, "transport": transport,
        "maximum_original_review_calls": len(cases), "input_hashes": input_hashes,
        "source_hashes": source_hashes, "expected_labels_in_prompt": False,
        "scope": "Development wording components; no corpus or whole-benchmark accuracy claim"})
    rows = []
    for case in cases:
        folder = args.out / case["id"]
        folder.mkdir()
        save(case["id"] + "/frozen_control.json", case)
        try:
            report = review_wording(case["question"], case["contract"],
                chat_json=OneReview(SimpleNamespace(dir=folder)).chat_json, model=model)
        except WordingReviewExecutionError as error:
            report = error.report
        save(case["id"] + "/review.json", report)
        actual = (report.get("opinion") or {}).get("verdict", "execution_error")
        row = {"id": case["id"], "expected": case["expected"]["verdict"], "actual": actual,
               "matches_expected": actual == case["expected"]["verdict"],
               "reason_needs_independent_reading": True}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    unchanged = all(Path(name).is_file() and digest(Path(name)) == value
                    for name, value in input_hashes.items())
    drift = [name for name, value in source_hashes.items() if digest(ROOT / name) != value]
    save("summary.json", {"rows": rows, "inputs_unchanged": unchanged, "source_drift": drift,
                          "not_general_accuracy": True})
    if not unchanged or drift:
        raise RuntimeError("Control input or implementation changed during calibration")


if __name__ == "__main__":
    main()
