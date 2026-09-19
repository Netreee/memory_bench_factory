"""Prepare or explicitly execute a single seed-to-material research batch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.provenance import load_public_protocol
from pipeline.material_proposals import prepare_materials, propose_materials


def _load(path):
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(text) if path.suffix.lower() == ".json" else text


def _new_output(path: Path, inputs: list[Path]) -> Path:
    path = path.expanduser().resolve()
    if path.exists() or any(path == source.expanduser().resolve() for source in inputs):
        raise ValueError("Output must be new; input and result overwrite is forbidden")
    runs = (ROOT / "output" / "runs").resolve()
    if path == runs or runs in path.parents or any(
        (parent / "manifest.json").exists() and any((parent / name).exists()
            for name in ("00_input.json", "02_world.json", "05_corpus.json", "06_grounded_questions.json"))
        for parent in path.parents
    ):
        raise ValueError("Material output must be outside historical run directories")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True, help="Complete seed JSON; generator design input only")
    parser.add_argument("--intent", type=Path, required=True, help="Natural-language design intent as text or JSON")
    parser.add_argument("--protocol", type=Path, help="Fixed caller public protocol; writer proposals never override it")
    parser.add_argument("--model", required=True)
    parser.add_argument("--count", type=int, default=6, help="Target number of document bodies, not sessions")
    parser.add_argument("--output", type=Path, required=True, help="New research report outside historical runs")
    parser.add_argument("--execute", action="store_true", help="Explicitly allow the model call; default prepares only")
    parser.add_argument("--max-calls", type=int, help="Required with --execute; only 0 or 1 is supported")
    parser.add_argument("--max-input-chars", type=int, default=200000)
    parser.add_argument("--max-tokens", type=int, default=8192)
    args = parser.parse_args(argv)
    if args.execute and args.max_calls is None:
        parser.error("--execute requires --max-calls 0 or 1")
    if args.max_calls is not None and args.max_calls not in (0, 1):
        parser.error("--max-calls must be 0 or 1")
    if args.count < 1 or args.max_input_chars < 1 or args.max_tokens < 1:
        parser.error("Count and input/output budgets must be positive")
    try:
        inputs = [p for p in (args.seed, args.intent, args.protocol) if p is not None]
        output = _new_output(args.output, inputs)
        trace = _new_output(output.with_name(output.name + ".calls.jsonl"), inputs)
        attempts = _new_output(output.with_name(output.name + ".attempts.jsonl"), inputs)
        seed = json.loads(args.seed.read_text(encoding="utf-8-sig"))
        intent = _load(args.intent)
        protocol = _load(args.protocol) if args.protocol else None
        if isinstance(protocol, dict) and any(k in protocol for k in ("answer_protocol", "public_protocol")):
            protocol = load_public_protocol(args.protocol)
        kwargs = {"model": args.model, "count": args.count, "public_protocol": protocol}
        prepared = prepare_materials(seed, intent, **kwargs)
        prepared["budget"] = {"max_calls": args.max_calls, "max_input_chars": args.max_input_chars,
                              "max_tokens": args.max_tokens}
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            if not args.execute or args.max_calls == 0:
                report = prepared
            else:
                import config
                from llm_trace import redact, trace_scope
                secrets = config._trace_secrets()
                with trace.open("x", encoding="utf-8") as sink, attempts.open("x", encoding="utf-8"):
                    def record(event):
                        sink.write(json.dumps(redact(event, secrets), ensure_ascii=False, allow_nan=False) + "\n")
                        sink.flush()

                    def chat_json(step, messages, **params):
                        with trace_scope(attempts, step):
                            return config.chat_json(messages, **params)

                    report = propose_materials(seed, intent, chat_json=chat_json,
                        max_calls=args.max_calls, max_input_chars=args.max_input_chars,
                        max_tokens=args.max_tokens, record=record, **kwargs)
                    report = redact(report, secrets)
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        print(json.dumps({"output": str(output), "mode": report["mode"], "calls_used": report["calls_used"],
            "execution": report["execution"]["status"], "candidates": len(report["candidates"]),
            "downstream_ready": report["downstream_ready"], "publication_effect": "none"}))
        return 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
