"""Bounded experiment wrapper around pipeline.factory.main, with no new stages."""
import argparse
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import hashlib
import inspect
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


REUSE_STAGES = {
    "whitepaper": ("input", "whitepaper"),
    "world": ("input", "whitepaper", "world"),
    "questions": ("input", "whitepaper", "world", "orders", "well_posed", "questions"),
    "corpus": ("input", "whitepaper", "world", "orders", "well_posed", "questions", "corpus"),
}
REUSE_NEXT = {"whitepaper": "world", "world": "orders", "questions": "corpus", "corpus": "grounding"}
_TRACER_STEP = ContextVar("original_bc_smoke_tracer_step", default=None)
_JSON_REQUEST_ACTIVE = ContextVar("original_bc_smoke_json_request_active", default=False)
_CALL_USAGE = ContextVar("original_bc_smoke_call_usage", default=None)


def quantity_arguments(args):
    """Forward quantity controls to the existing factory/closed_loop stages."""
    if args.min_questions is None:
        return ["--question-budget", str(args.question_budget if args.question_budget is not None else 4)]
    result = ["--min-questions", str(args.min_questions), "--max-rounds", str(args.max_rounds),
              "--max-world-entities", str(args.max_world_entities)]
    if args.total_only:
        result.append("--total-only")
    if args.time_span_weeks is not None:
        result += ["--time-span-weeks", str(args.time_span_weeks)]
    for value in args.per_line or []:
        result += ["--per-line", value]
    return result


def usage_cost(usage, input_price, output_price):
    """Missing/invalid provider usage cannot release a conservative reservation."""
    if not isinstance(usage, dict):
        return None
    counts = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
    if any(type(n) is not int or n < 0 for n in counts):
        return None
    return (counts[0] * input_price + counts[1] * output_price) / 1e6


def implementation_hashes():
    hashes = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (ROOT / "pipeline", ROOT / "eval") for path in folder.rglob("*.py")}
    hashes.update({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                   for name in ("config.py", "llm_transport.py", "llm_trace.py", "tools/run_original_bc_smoke.py")})
    return hashes


def reuse_original_stages(source, directory, through):
    """Freeze completed original inputs, including seed identity, before a comparison."""
    from pipeline.seed_run import validate_seed_identity
    from pipeline import world_semantics
    previous = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    stages = set(REUSE_STAGES[through])
    if (previous.get("status") == "running" or any(
            not previous.get("stages", {}).get(name, {}).get("done") for name in stages)):
        raise ValueError("Source must be stopped with all selected original stages completed")
    view = SimpleNamespace(manifest=previous,
        has=lambda name: (source / name).is_file(),
        read=lambda name: json.loads((source / name).read_text(encoding="utf-8")))
    pack = validate_seed_identity(view, view.read("01_whitepaper.json"))
    if pack is None and previous.get("scenario") != "bc_small_office":
        raise ValueError("Only the bounded office scenario or a verified frozen seed is supported")
    names = {previous["stages"][name]["artifact"] for name in stages} | {"00_about.json"}
    if pack is not None:
        names |= {"00_seed_pack.json", "01_seed_audit.json"}
        if pack["schema_version"] == 2:
            from pipeline.seed_run import AUDIT_ARTIFACT, GENERATION_ARTIFACT
            names |= {AUDIT_ARTIFACT, GENERATION_ARTIFACT}
        if "world" in stages:
            names.add("02_seed_audit.json")
    if "questions" in stages:
        names.add("04_wording_report.json")
    if through == "questions" and (source / "05_corpus.ckpt.json").is_file():
        # The original corpus stage validates its input/target/scope identity
        # before resuming. Copy the exact bytes and include their provenance.
        names.add("05_corpus.ckpt.json")
    if "world" in stages and world_semantics.enabled(view.read("01_whitepaper.json"), previous.get("config", {})):
        from pipeline.world_state import WorldState
        review = view.read(world_semantics.REVIEW_ARTIFACT)
        errors = world_semantics.validate_review(review, view.read("01_whitepaper.json"),
            WorldState.from_dict(view.read("02_world.json")), task_input=view.read("00_input.json"))
        if review.get("status") != "passed" or errors:
            raise ValueError("Source world has no current passing business review")
        names.add(world_semantics.REVIEW_ARTIFACT)
    if any(Path(name).name != name or not (source / name).is_file() for name in names):
        raise ValueError("Source artifacts must be existing simple file names")
    # Read every artifact before creating the destination; invalid inputs leave no partial run.
    artifacts = {name: (source / name).read_bytes() for name in sorted(names)}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()}
    previous.update(run_id=directory.name, status="created", current_stage=None, llm_calls=0,
        created=datetime.now().isoformat(), stages={k:v for k,v in previous["stages"].items() if k in stages},
        derived_from={"operation": "resume_original_stages", "run_id": source.name,
                      "input_files": hashes, "reused_through": through})
    cleared = ["grounding", "quality", "met_status", "per_line_final"]
    if through not in ("questions", "corpus"):
        cleared += ["orders", "orders_by_line", "question_supply", "well_posed", "questions", "corpus"]
    elif through == "questions":
        cleared += ["corpus", "docs", "chars", "diversity", "render_strategy"]
    if through == "whitepaper":
        cleared += ["entities", "sessions", "world_semantic_review"]
    for key in cleared:
        previous.get("algo", {}).pop(key, None)
    if through != "corpus":
        previous["derived_from"]["cleared_transient_config"] = {
            key: previous.get("config", {}).pop(key) for key in
            ("augment", "render_only", "render_only_pairs", "quotas")
            if key in previous.get("config", {})}
    directory.mkdir(parents=True)
    for name, data in artifacts.items():
        (directory / name).write_bytes(data)
    (directory / "manifest.json").write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
    return previous


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True)
    ap.add_argument("--to", default="quality", choices=["input", "whitepaper", "world", "orders",
                    "well_posed", "questions", "corpus", "grounding", "quality"])
    ap.add_argument("--max-calls", type=int, default=48)
    ap.add_argument("--max-cny", type=float, default=15)
    ap.add_argument("--json-attempts", type=int, choices=range(1, 6), default=3,
                    help="Maximum provider attempts per JSON request; each attempt consumes the same call/cost budget")
    ap.add_argument("--model", choices=["gpt-5.4-mini", "glm-4.7-nothinking", "glm-5.3-flash"], default="gpt-5.4-mini")
    ap.add_argument("--min-output-tokens", type=int, default=0)
    ap.add_argument("--max-output-tokens", type=int, default=16384)
    ap.add_argument("--call-timeout-seconds", type=float, default=150)
    ap.add_argument("--disclosure-format-attempts", type=int, choices=range(2, 7), default=2)
    ap.add_argument("--reasoning-effort", choices=["low", "medium", "high", "max"], default="low")
    ap.add_argument("--world-review-reasoning-effort", choices=["low", "medium"], default=None,
                    help="Mini-only experiment: override reasoning for exact world.semantic_review calls; keep their token limit and timeout")
    ap.add_argument("--corpus-review-reasoning-effort", choices=["low", "medium"], default=None,
                    help="Mini-only experiment: override reasoning for exact corpus.review calls; keep their token limit")
    ap.add_argument("--corpus-review-max-tokens", type=int, choices=[4096, 8192], default=None,
                    help="Mini-only experiment: output limit for exact corpus.review calls; reserve the same limit before dispatch")
    ap.add_argument("--reference-audit-prompt", type=Path,
                    help="Experimental override for the existing audit role; copied and hashed into this run")
    ap.add_argument("--seed-pack", type=Path, help="Use the original seed entry instead of the small office scenario")
    ap.add_argument("--world-semantic-review", action="store_true",
                    help="Enable the original world business gate for a frozen older whitepaper")
    quantities = ap.add_mutually_exclusive_group()
    quantities.add_argument("--question-budget", type=int)
    quantities.add_argument("--min-questions", type=int)
    ap.add_argument("--total-only", action="store_true")
    ap.add_argument("--per-line", action="append")
    ap.add_argument("--max-rounds", type=int, default=2)
    ap.add_argument("--max-world-entities", type=int, default=80)
    ap.add_argument("--time-span-weeks", type=int)
    ap.add_argument("--settle-reported-usage", action="store_true",
                    help="Reserve before each call; settle valid reported usage afterwards; keep full reservation for missing usage/errors")
    ap.add_argument("--target-mtokens", type=float, default=0.004)
    ap.add_argument("--process-questions", action="store_true",
                    help="Exercise original L3 process proposals in a fresh run")
    ap.add_argument("--source-run", help="Copy selected completed original stages into a fresh run")
    ap.add_argument("--reuse-through", choices=list(REUSE_STAGES), default="corpus",
                    help="Reuse frozen original inputs through whitepaper, world, questions (including a corpus checkpoint), or corpus")
    args = ap.parse_args()
    if not 0 <= args.min_output_tokens <= args.max_output_tokens <= 128000 or args.max_output_tokens < 1:
        ap.error("Output token limits must satisfy 0 <= minimum <= maximum <= 128000")
    if not math.isfinite(args.call_timeout_seconds) or args.call_timeout_seconds <= 0:
        ap.error("Call timeout must be finite and positive")
    from llm_transport import original_run_transport
    try:
        transport = original_run_transport(args.model, args.reasoning_effort, args.call_timeout_seconds)
    except ValueError as exc:
        ap.error(str(exc))
    if args.world_review_reasoning_effort is not None and args.model != "gpt-5.4-mini":
        ap.error("--world-review-reasoning-effort is supported only for gpt-5.4-mini")
    if args.corpus_review_reasoning_effort is not None and args.model != "gpt-5.4-mini":
        ap.error("--corpus-review-reasoning-effort is supported only for gpt-5.4-mini")
    if args.corpus_review_max_tokens is not None and args.model != "gpt-5.4-mini":
        ap.error("--corpus-review-max-tokens is supported only for gpt-5.4-mini")
    audit_prompt = args.reference_audit_prompt.read_text(encoding="utf-8") if args.reference_audit_prompt else None
    if audit_prompt is not None and not audit_prompt.strip():
        ap.error("An experimental audit prompt cannot be empty")
    if args.seed_pack and args.source_run:
        ap.error("A source run supplies its verified frozen seed; do not also select a mutable seed file")
    if args.seed_pack and not args.seed_pack.is_file():
        ap.error("The seed pack must be an existing file")
    if ((args.question_budget is not None and args.question_budget < 1)
            or (args.min_questions is not None and args.min_questions < 1)
            or not math.isfinite(args.target_mtokens) or args.target_mtokens < 0):
        ap.error("Question budget must be positive and corpus size must be nonnegative")
    if (args.total_only or args.per_line or args.time_span_weeks is not None) and args.min_questions is None:
        ap.error("Closed-loop options require --min-questions")
    if args.min_questions is not None and (args.to != "quality" or args.source_run):
        ap.error("Quantity-target production requires a fresh run through quality")
    if not 8 <= args.max_world_entities <= 80 or args.max_rounds < 1:
        ap.error("Invalid world/round limits")
    if args.time_span_weeks is not None and not 6 <= args.time_span_weeks <= 26:
        ap.error("time-span-weeks must be between 6 and 26")
    if Path(args.run).name != args.run or args.max_calls < 1 or not math.isfinite(args.max_cny) or args.max_cny <= 0:
        ap.error("A fresh simple run name and positive limits are required")
    allowed_after_reuse = {"corpus": {"grounding", "quality"},
                          "questions": {"corpus", "grounding", "quality"},
                          "world": {"orders", "well_posed", "questions", "corpus", "grounding", "quality"},
                          "whitepaper": {"world", "orders", "well_posed", "questions", "corpus", "grounding", "quality"}}
    if args.source_run and args.to not in allowed_after_reuse[args.reuse_through]:
        ap.error("The last stage must follow the reused stage")
    directory = ROOT / "output/runs" / args.run
    if directory.exists():
        ap.error("Use a fresh run name; historical experiments are never overwritten")
    previous = None
    if args.source_run:
        if Path(args.source_run).name != args.source_run:
            ap.error("source-run must be a simple existing run name")
        source = ROOT / "output/runs" / args.source_run
        try:
            previous = reuse_original_stages(source, directory, args.reuse_through)
        except (ValueError, OSError) as exc:
            ap.error(str(exc))
    else:
        directory.mkdir(parents=True)
    import config
    model = args.model
    config.MODEL = config.STRUCTURE_MODEL = config.DISCRIMINATOR_MODEL = model
    config.REVIEWER_MODEL = config.JUDGE_MODEL = model
    config.DISCLOSURE_FORMAT_ATTEMPTS = args.disclosure_format_attempts
    config.LLM_CONCURRENCY = 1
    config._LLM_SEM = threading.BoundedSemaphore(1)
    corpus_review_transport = None
    if args.corpus_review_reasoning_effort is not None or args.corpus_review_max_tokens is not None:
        corpus_review_transport = deepcopy(transport)
        if args.corpus_review_reasoning_effort is not None:
            corpus_review_transport["profiles"]["low"]["reasoning_effort"] = args.corpus_review_reasoning_effort
    world_review_transport = None
    if args.world_review_reasoning_effort is not None:
        world_review_transport = deepcopy(transport)
        world_review_transport["profiles"]["low"]["reasoning_effort"] = args.world_review_reasoning_effort
    price_input, price_output = {"gpt-5.4-mini": (3.75, 22.5),
                                 "glm-4.7-nothinking": (2.37, 2.37 * 4.683544),
                                 "glm-5.3-flash": (0.632, 2.212)}[model]
    lock = threading.Lock()
    # Admit only when this request owns the dispatch turn. A failed call must
    # close the ledger before any queued caller can reserve or reach config.
    # JSON parsing holds the same lock; its call into bounded_chat reenters it.
    dispatch_lock = threading.RLock()
    ledger = {"created_utc": datetime.now(timezone.utc).isoformat(), "model": model,
        "transport": transport, "max_calls": args.max_calls, "max_cny": args.max_cny,
        "json_attempts": args.json_attempts,
        "min_output_tokens": args.min_output_tokens, "max_output_tokens": args.max_output_tokens,
        "call_timeout_seconds": args.call_timeout_seconds,
        "disclosure_format_attempts": args.disclosure_format_attempts,
        "json_retry_policy": {"version": "classified/v2", "caller_attempt_limit": "min(caller, runner)",
            "output_truncated": "double actual cap up to frozen maximum, stop when no increase remains",
            "json_syntax": "bounded feedback", "connection_429_5xx": "bounded backoff",
            "empty_deadline_read_timeout": "stop", "model_fallback": False},
        "request_lifecycle": "cancellable_async_http/v1",
        "logical_failure_policy": {"version": "agent-author-replan/v1",
            "recoverable_step": "world.agent.write", "required_kind": "output_truncated",
            "requires_received_response": True, "preserve_existing_stop": True,
            "next_call_checks": ["model", "calls", "estimated_budget"], "all_other_failures": "stop"},
        "deferred_agent_author_failures": [],
        "step_transport_overrides": ({"corpus.review": {
            "reasoning_effort": (args.corpus_review_reasoning_effort
                                 if args.corpus_review_reasoning_effort is not None else "unchanged global setting"),
            "transport": corpus_review_transport,
            "max_tokens": (args.corpus_review_max_tokens
                           if args.corpus_review_max_tokens is not None else "unchanged caller limit")}}
            if corpus_review_transport is not None else {}),
        "admitted_calls": 0, "reserved_upper_estimate_cny": 0.0, "stopped": False,
        "settle_reported_usage": args.settle_reported_usage,
        "budget_consumed_cny": 0.0, "reported_usage_estimate_cny": 0.0,
        "settled_usage_calls": 0, "unsettled_reservation_calls": 0,
        "quantity_arguments": quantity_arguments(args),
        "pricing_source": "https://rmb.dmxapi.cn/", "input_cny_per_m": price_input, "output_cny_per_m": price_output,
        "scope": "Original factory stages with a frozen experimental input. Not a quality guarantee.",
        "input": ({"kind": "seed_pack", "path": str(args.seed_pack.resolve()),
                   "sha256": hashlib.sha256(args.seed_pack.read_bytes()).hexdigest()}
                  if args.seed_pack else
                  {"kind": "frozen_seed", "seed_id": previous["config"]["seed_id"],
                   "digest": previous["config"]["seed_pack_digest"], "artifact": "00_seed_pack.json"}
                  if previous and previous.get("config", {}).get("seed_pack_digest") else
                  {"kind": "scenario", "name": "bc_small_office"}),
        "source_run": args.source_run,
        "reused_through": args.reuse_through if args.source_run else None,
        "budget_basis": "UTF-8 prompt bytes plus framing allowance and maximum completion reservation, not an invoice"}
    if world_review_transport is not None:
        ledger["step_transport_overrides"]["world.semantic_review"] = {
            "reasoning_effort": args.world_review_reasoning_effort,
            "transport": world_review_transport, "max_tokens": "unchanged caller limit"}
    ledger_path = directory / "experiment_profile.json"
    def save():
        temporary = ledger_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(ledger_path)
    actual_chat, actual_json = config.chat, config.chat_json
    chat_signature, json_signature = inspect.signature(actual_chat), inspect.signature(actual_json)
    actual_emit = config.emit
    def observe_usage(event, **fields):
        actual_emit(event, **fields)
        box = _CALL_USAGE.get()
        if event == "response" and box is not None:
            box["usage"] = (fields.get("response") or {}).get("usage")
    if args.settle_reported_usage:
        config.emit = observe_usage
    def bounded_chat(messages, *a, **kw):
        bound = chat_signature.bind(messages, *a, **kw)
        kw = dict(bound.arguments); kw.pop("messages")
        a = ()
        chosen = kw.get("model") or config.MODEL
        requested_cap = kw.get("max_tokens", 4096)
        if args.corpus_review_max_tokens is not None and _TRACER_STEP.get() == "corpus.review":
            requested_cap = args.corpus_review_max_tokens
        cap = min(max(requested_cap, args.min_output_tokens), args.max_output_tokens)
        prompt_bound = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 512
        reserve = (prompt_bound * price_input + cap * price_output) / 1e6
        with dispatch_lock:
            with lock:
                if (chosen != model or ledger["stopped"] or ledger["admitted_calls"] >= args.max_calls
                        or ledger["budget_consumed_cny"] + reserve > args.max_cny):
                    ledger["stopped"] = True; save()
                    raise RuntimeError("Original experiment model/call/estimated budget limit; no provider dispatch")
                ledger["admitted_calls"] += 1
                ledger["reserved_upper_estimate_cny"] += reserve
                ledger["budget_consumed_cny"] += reserve
                save()
            box = {}
            usage_token = _CALL_USAGE.set(box)
            try:
                selected_transport = (corpus_review_transport
                    if corpus_review_transport is not None and _TRACER_STEP.get() == "corpus.review"
                    else world_review_transport
                    if world_review_transport is not None and _TRACER_STEP.get() == "world.semantic_review"
                    else transport)
                kw.update(model=model, max_tokens=cap, transport=selected_transport)
                return actual_chat(messages, *a, **kw)
            except Exception as exc:
                # The outer JSON request owns its bounded retries and this
                # dispatch lock. Let it recover before closing the shared gate.
                if not (_JSON_REQUEST_ACTIVE.get() and config.is_retryable_chat_error(exc)):
                    with lock:
                        ledger["stopped"] = True; save()
                raise
            finally:
                _CALL_USAGE.reset(usage_token)
                settled = usage_cost(box.get("usage"), price_input, price_output) if args.settle_reported_usage else None
                with lock:
                    if settled is not None:
                        ledger["budget_consumed_cny"] += settled - reserve
                        ledger["reported_usage_estimate_cny"] += settled
                        ledger["settled_usage_calls"] += 1
                        if ledger["budget_consumed_cny"] > args.max_cny:
                            ledger["stopped"] = True
                    else:
                        ledger["unsettled_reservation_calls"] += 1
                    save()
    def bounded_json(messages, *a, **kw):
        bound = json_signature.bind(messages, *a, **kw)
        kw = dict(bound.arguments); kw.pop("messages")
        a = ()
        caller_attempts = kw.get("retries", args.json_attempts)
        if type(caller_attempts) is not int or caller_attempts < 1:
            raise ValueError("Caller retries must be a positive integer upper bound")
        cap = min(max(kw.get("max_tokens", 4096), args.min_output_tokens), args.max_output_tokens)
        maximum = min(kw.get("max_output_tokens") or args.max_output_tokens, args.max_output_tokens)
        if args.corpus_review_max_tokens is not None and _TRACER_STEP.get() == "corpus.review":
            cap = min(max(args.corpus_review_max_tokens, args.min_output_tokens), args.max_output_tokens)
            maximum = cap  # An exact step override remains an actual upper bound.
        if maximum < cap:
            raise ValueError("Caller output maximum conflicts with the frozen minimum")
        kw.update(retries=min(caller_attempts, args.json_attempts), max_tokens=cap,
                  max_output_tokens=maximum, strict_json=True, retry_delay_base=1.0)
        with dispatch_lock:
            token = _JSON_REQUEST_ACTIVE.set(True)
            try:
                return actual_json(messages, *a, **kw)
            except Exception as exc:
                with lock:
                    deferred = (_TRACER_STEP.get() == "world.agent.write"
                        and isinstance(exc, config.ChatJSONError)
                        and exc.kind == "output_truncated"
                        and exc.response_received is True and not ledger["stopped"])
                    if deferred:
                        # The Agent receives this failed result and must ask its
                        # planner to narrow/revise the task. Every next physical
                        # call still enters bounded_chat's unchanged admission.
                        ledger["deferred_agent_author_failures"].append({
                            "step": _TRACER_STEP.get(), "kind": exc.kind,
                            "call_id": exc.call_id, "token_cap": exc.token_cap,
                            "usage_status": exc.usage_status, "admitted_calls": ledger["admitted_calls"]})
                    else:
                        ledger["stopped"] = True
                    save()
                raise
            finally:
                _JSON_REQUEST_ACTIVE.reset(token)
    config.chat, config.chat_json = bounded_chat, bounded_json
    from pipeline import factory
    if audit_prompt is not None:
        from pipeline import reference_audit
        reference_audit.SYSTEM = audit_prompt
        (directory / "experiment_reference_audit_prompt.txt").write_text(audit_prompt, encoding="utf-8")
        ledger["experimental_reference_audit_prompt"] = {
            "source": str(args.reference_audit_prompt.resolve()),
            "sha256": hashlib.sha256(audit_prompt.encode("utf-8")).hexdigest(),
            "production_default_changed": False}
    factory.SCENARIOS["bc_small_office"] = {
        "description": "构造小型项目办公室的合成记录，用于验证原生成流程。规模保持很小："
            "两份项目记录、两位负责人、四个记录周，只需要项目状态、预算、负责人关系的少量变化。"
            "采用周报和任命通知两类正文。业务关系要合理，正文与当期事实一致。不要扩成大型企业。",
        "few_shot": [{"title": "项目周报", "date": "2025-01-06", "doc_type": "周报",
            "content": "松河项目本周状态为立项，预算10万元，负责人为许华。"}]}
    save()
    source_hashes = implementation_hashes()
    (directory / "experiment_source_hashes.json").write_text(json.dumps(source_hashes, indent=2), encoding="utf-8")
    sys.argv = ["pipeline.factory", "--run", args.run,
                "--target-mtokens", str(args.target_mtokens), "--to", args.to] + quantity_arguments(args)
    if args.process_questions:
        sys.argv += ["--process-questions"]
    if args.world_semantic_review:
        sys.argv += ["--world-semantic-review"]
    if args.seed_pack:
        sys.argv += ["--seed-pack", str(args.seed_pack.resolve())]
    elif not args.source_run:
        sys.argv += ["--scenario", "bc_small_office"]
    if args.source_run:
        sys.argv += ["--from", REUSE_NEXT[args.reuse_through]]
    from pipeline.run import Tracer
    actual_tracer_json = Tracer.chat_json
    def scoped_tracer_json(self, step, messages, *a, **kw):
        token = _TRACER_STEP.set(step)
        try:
            return actual_tracer_json(self, step, messages, *a, **kw)
        finally:
            _TRACER_STEP.reset(token)
    try:
        Tracer.chat_json = scoped_tracer_json
        factory.main()
        ledger["execution_status"] = "completed"
    except BaseException as exc:
        ledger.update(execution_status="failed", error_type=type(exc).__name__, error=str(exc)[:600])
        raise
    finally:
        if args.settle_reported_usage:
            config.emit = actual_emit
        config.chat, config.chat_json = actual_chat, actual_json
        Tracer.chat_json = actual_tracer_json
        end_hashes = implementation_hashes()
        (directory / "experiment_source_hashes_at_end.json").write_text(
            json.dumps(end_hashes, indent=2), encoding="utf-8")
        ledger["source_drift"] = sorted(name for name in set(source_hashes) | set(end_hashes)
                                         if source_hashes.get(name) != end_hashes.get(name))
        save()


if __name__ == "__main__":
    main()
