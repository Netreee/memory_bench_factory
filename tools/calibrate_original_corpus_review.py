"""Bounded controls for the existing corpus.review call; never generates a run."""
from __future__ import annotations

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
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--model", choices=["gpt-5.4-mini", "glm-4.7-nothinking"], default="gpt-5.4-mini")
    parser.add_argument("--reasoning-effort", choices=["low", "medium"], default="low")
    parser.add_argument("--max-output-tokens", type=int, choices=[4096, 8192], default=4096)
    args = parser.parse_args()
    base = args.manifest.resolve().parent
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    entries = manifest["cases"]
    names = [entry["id"] for entry in entries]
    if (len(names) != len(set(names)) or any(Path(name).name != name for name in names)
            or set(args.case) - set(names)):
        parser.error("Controls need unique simple IDs; requested IDs must exist")
    selected = [entry for entry in entries if not args.case or entry["id"] in args.case]
    if not selected or len(selected) > 6:
        parser.error("Select one to six frozen controls")
    from pipeline.world_state import WorldState
    from pipeline.corpus_contract import fidelity_requirements, canonical_context
    loaded, adaptations, input_hashes = [], [], {str(args.manifest.resolve()): digest(args.manifest)}
    for entry in selected:
        path = (base / entry["file"]).resolve()
        if not path.is_relative_to(base):
            parser.error("Case files must stay within the frozen controls directory")
        case = json.loads(path.read_text(encoding="utf-8"))
        input_hashes[str(path)] = digest(path)
        if entry.get("sha256") and entry["sha256"] != input_hashes[str(path)]:
            parser.error("Frozen case differs from the manifest hash")
        if case["id"] != entry["id"]:
            parser.error("Case identity differs from manifest")
        world = case.get("world")
        if world is None:
            world_path = (base / case["world_file"]).resolve()
            if not world_path.is_relative_to(base):
                parser.error("World files must stay within the controls directory")
            world = json.loads(world_path.read_text(encoding="utf-8"))
            input_hashes[str(world_path)] = digest(world_path)
        ws, session = WorldState.from_dict(world), case["session"]
        requirements = fidelity_requirements(ws, session, facts=case.get("facts"), events=case.get("events"))
        frozen_context = case.get("context") or {}
        entities = sorted({item["entity"] for item in frozen_context.get("facts", [])}) or None
        context = canonical_context(ws, session, entities=entities)
        # Recompute version/schema/as-of metadata through the same production
        # helper. Preserve the frozen group's actual source obligations only.
        context["source_assertions"] = frozen_context.get("source_assertions", [])
        canonical_bytes = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                   separators=(",", ":")).encode("utf-8")
        adaptations.append({"id": case["id"],
            "frozen_context_sha256": hashlib.sha256(canonical_bytes(frozen_context)).hexdigest(),
            "current_context_sha256": hashlib.sha256(canonical_bytes(context)).hexdigest(),
            "method": "Current canonical_context over the frozen world and entity scope; source assertions preserved"})
        if not isinstance(case.get("docs"), list) or not case["docs"]:
            parser.error("Each control needs actual candidate documents")
        loaded.append((case, ws, requirements, context))
    if args.validate_only:
        print(json.dumps({"controls": len(loaded), "ids": [x[0]["id"] for x in loaded], "api_calls": 0}, ensure_ascii=False))
        return
    if args.out is None or args.out.exists():
        parser.error("Choose a fresh output directory")
    import config
    from pipeline.run import Tracer
    from pipeline import corpus_contract
    config.MODEL = config.REVIEWER_MODEL = config.DISCRIMINATOR_MODEL = args.model
    transport = {"version": "chat-completions-transport/v1", "default_profile": "calibration",
        "model_profiles": {args.model: "calibration"}, "profiles": {"calibration": {
            "token_limit_parameter": "max_completion_tokens", "reasoning_effort": args.reasoning_effort,
            "omit_parameters": ["temperature", "top_p"], "http_timeout_seconds": 150,
            "deadline_seconds": 150}}}
    if args.model == "glm-4.7-nothinking":
        transport["profiles"]["calibration"] = {"token_limit_parameter": "max_tokens",
            "http_timeout_seconds": 150, "deadline_seconds": 150}

    class OneReview(Tracer):
        def chat_json(self, step, messages, **kwargs):
            if self.n or step != "corpus.review" or (kwargs.get("model") or config.MODEL) != args.model:
                raise RuntimeError("Control permits one original corpus.review with the selected cheap model")
            kwargs.update(model=args.model, transport=transport, retries=1, strict_json=True,
                          max_tokens=args.max_output_tokens)
            return super().chat_json(step, messages, **kwargs)

    args.out.mkdir(parents=True)
    # This tool executes the original review component only. Unrelated world
    # authors may be improved in parallel without changing this experiment.
    source_paths = [ROOT / name for name in (
        "pipeline/corpus_contract.py", "pipeline/world_state.py", "pipeline/world_blueprint.py",
        "pipeline/value_types.py", "pipeline/run.py", "config.py", "llm_transport.py", "llm_trace.py")]
    source_paths.append(Path(__file__))
    source_hashes = {path.relative_to(ROOT).as_posix(): digest(path) for path in source_paths}
    profile = {"scope": "Frozen development controls, not independent benchmark accuracy or generated production output",
        "model": args.model, "transport": transport, "maximum_calls": len(loaded),
        "input_hashes": input_hashes, "source_hashes": source_hashes,
        "context_adaptations": adaptations,
        "blind_reads": "No extra blind-reader call; no fabricated blind answers",
        "expected_labels_in_prompt": False, "production_published": False}
    (args.out / "experiment_profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    for case, ws, requirements, context in loaded:
        folder = args.out / case["id"]
        folder.mkdir()
        (folder / "frozen_control.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
        report = corpus_contract.review_documents(OneReview(SimpleNamespace(dir=folder)), ws,
            case["session"], case["docs"], context=context, requirements=requirements, blind_reads=[],
            required_public_rule_ids=case.get("required_public_rule_ids", ()))
        (folder / "review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        expected = case["expected"]["status"]
        row = {"id": case["id"], "expected": expected, "actual": report.get("status"),
               "matches_expected_status": expected == report.get("status"),
               "semantic_reason_requires_independent_reading": True}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    unchanged = all(Path(path).is_file() and digest(Path(path)) == value for path, value in input_hashes.items())
    drift = [path for path, value in source_hashes.items() if digest(ROOT / path) != value]
    summary = {"rows": rows, "inputs_unchanged": unchanged, "source_drift": drift,
               "not_a_general_accuracy_estimate": True}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if not unchanged or drift:
        raise RuntimeError("Input or implementation changed during calibration")


if __name__ == "__main__":
    main()
