"""Prepare or explicitly execute one corpus-first research question batch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.provenance import load_public_protocol
from pipeline.question_proposals import prepare_proposals, propose_questions


def _load(path):
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(text) if path.suffix.lower() == ".json" else text


def _new_output(path: Path, inputs: list[Path]) -> Path:
    path = path.expanduser().resolve()
    if path.exists() or any(path == source.expanduser().resolve() for source in inputs):
        raise ValueError("Output must be new; inputs and existing results cannot be overwritten")
    runs = (ROOT / "output" / "runs").resolve()
    if path == runs or runs in path.parents or any(
        (parent / "manifest.json").exists() and
        ((parent / "00_input.json").exists() or (parent / "02_world.json").exists())
        for parent in path.parents
    ):
        raise ValueError("Proposal outputs must be outside historical run directories")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True, help="Public protocol text/JSON or an about artifact")
    parser.add_argument("--intent", type=Path, help="Design intent text or JSON; never public evidence")
    parser.add_argument("--seed", type=Path, help="Complete seed JSON as a design target; never public evidence")
    parser.add_argument("--output", type=Path, required=True, help="New JSON report outside historical runs")
    parser.add_argument("--model", required=True)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--include-titles", action="store_true", help="Only if titles are public to the solver")
    parser.add_argument("--execute", action="store_true", help="Permit one model batch call; default prepares only")
    parser.add_argument("--max-calls", type=int, help="Required with execute; only 0 or 1 is supported")
    parser.add_argument("--max-input-chars", type=int, default=200000)
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args(argv)
    if args.intent is None and args.seed is None:
        parser.error("Provide --intent and/or --seed")
    if args.execute and args.max_calls is None:
        parser.error("--execute requires --max-calls 0 or 1")
    if args.max_calls is not None and args.max_calls not in (0, 1):
        parser.error("--max-calls must be 0 or 1")
    if args.count < 1 or args.max_input_chars < 1 or args.max_tokens < 1:
        parser.error("Count and input/output budgets must be positive")
    try:
        inputs = [p for p in (args.corpus, args.protocol, args.intent, args.seed) if p is not None]
        output = _new_output(args.output, inputs)
        trace = _new_output(output.with_name(output.name + ".calls.jsonl"), inputs)
        attempts = _new_output(output.with_name(output.name + ".attempts.jsonl"), inputs)
        corpus, protocol = _load(args.corpus), _load(args.protocol)
        if isinstance(protocol, dict) and ("answer_protocol" in protocol or "public_protocol" in protocol):
            protocol = load_public_protocol(args.protocol)
        intent = _load(args.intent) if args.intent else None
        seed = json.loads(args.seed.read_text(encoding="utf-8-sig")) if args.seed else None
        kwargs = {"seed": seed, "model": args.model, "count": args.count,
                  "include_titles": args.include_titles}
        prepared = prepare_proposals(corpus, protocol, intent, **kwargs)
        prepared["budget"] = {"max_calls": args.max_calls, "max_input_chars": args.max_input_chars,
                              "max_tokens": args.max_tokens}
        output.parent.mkdir(parents=True, exist_ok=True)
        # Reserve the new report before any provider call; an existing path can
        # never be overwritten, even if it appears after preflight validation.
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

                    report = propose_questions(corpus, protocol, intent, chat_json=chat_json,
                        max_calls=args.max_calls, max_input_chars=args.max_input_chars,
                        max_tokens=args.max_tokens, record=record, **kwargs)
                    report = redact(report, secrets)
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        print(json.dumps({"output": str(output), "mode": report["mode"], "calls_used": report["calls_used"],
                          "execution": report["execution"]["status"], "candidates": len(report["candidates"]),
                          "review_state": report["review_state"], "publication_effect": "none"}))
        return 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
