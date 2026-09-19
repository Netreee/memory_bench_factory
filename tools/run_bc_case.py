"""Run a bounded, frozen-material B/C quality case without publishing it.

The caller supplies a plan and an existing case directory. Each phase writes a
new directory; interrupted or failed phases cannot be silently restarted. No
configuration or provider is loaded without --execute. Review conclusions are
observations for independent inspection, not automatic quality acceptance.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def claim_phase(case, phase, plan, inputs, sources):
    path = case / phase
    path.mkdir(exist_ok=False)
    manifest = {"phase": phase, "result_scope": "research_only", "publication_effect": "none",
                "plan_sha256": sha(case / "plan.json"), "plan": plan,
                "input_sha256": {str(p.relative_to(case)): sha(p) for p in inputs},
                "source_sha256": {s: sha(ROOT / s) for s in sources},
                "started_unix": time.time(), "overwrite_or_automatic_restart": False}
    save(path / "manifest.json", manifest)
    return path


def provider(directory, *, transport=None):
    # Only the prospective CLI supplies this opt-in configuration. Old replay
    # drivers keep their default path and do not import the profile dependency.
    if transport is not None:
        from llm_transport import validate_transport
        transport = validate_transport(transport)
    import config
    from llm_trace import redact, trace_scope
    secrets = config._trace_secrets()
    lock = threading.Lock()
    def record(event):
        with lock, (directory / "calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(redact(event, secrets), ensure_ascii=False) + "\n")
    def call(step, messages, **params):
        if params.get("retries") != 1:
            raise ValueError("Every case call must have exactly one provider attempt")
        if "transport" in params:
            raise ValueError("Transport belongs to the frozen provider, not an individual agent")
        with trace_scope(directory / "attempts.jsonl", step):
            if transport is None:
                return config.chat_json(messages, **params)
            return config.chat_json(messages, **params, transport=deepcopy(transport))
    return call, record, lambda value: redact(value, secrets)


def validate_public_freeze(case):
    freeze = read(case / "material_freeze.json")
    for name, expected in freeze["file_sha256"].items():
        if sha(case / name) != expected:
            raise ValueError("Frozen public input changed: " + name)
    if freeze["status"] != "complete_transport_only":
        raise ValueError("Incomplete material scope cannot be presented as a whole case")


def run_materials(case, plan, execute, replay_path=None):
    from pipeline.material_series import prepare_material_series, generate_material_series
    seed, intent = read(case / "seed.json"), read(case / "design_intent.json")
    protocol = (case / "protocol.txt").read_text(encoding="utf-8")
    kwargs = dict(model=plan["material_model"], batch_specs=plan["material_batches"], public_protocol=protocol)
    prepared = prepare_material_series(seed, intent, **kwargs)
    replay_manifest = None
    if replay_path is not None:
        replay_path = Path(replay_path).resolve()
        if replay_path.parent != case.resolve():
            raise ValueError("Replay manifest must be frozen inside the derived case")
        replay_manifest = read(replay_path)
        for source in replay_manifest["source_files"]:
            if sha(source["path"]) != source["sha256"]:
                raise ValueError("Replay source changed: " + source["path"])
        from pipeline.material_replay import MaterialReplay
        # Validate entries even for a dry run. This callable is never invoked.
        MaterialReplay(replay_manifest["entries"], lambda *a, **k: None)
    if not execute:
        print(json.dumps({"phase":"materials", "prepared":True, "batches":len(plan["material_batches"]),
                          "calls_permitted":0, "result_scope":"research_only"}))
        return
    inputs = [case / name for name in ("seed.json", "design_intent.json", "protocol.txt", "quality_review_protocol.json")]
    sources = ["tools/run_bc_case.py", "pipeline/material_series.py", "pipeline/material_proposals.py", "config.py", "llm_trace.py"]
    if replay_manifest is not None:
        inputs.append(replay_path)
        sources.append("pipeline/material_replay.py")
    directory = claim_phase(case, "materials", plan, inputs, sources)
    call, record, clean = provider(directory)
    replay = None
    if replay_manifest is not None:
        replay = MaterialReplay(replay_manifest["entries"], call, record=record)
        call = replay
    def checkpoint(snapshot):
        index, batch = snapshot["batch_index"], snapshot["batch_report"]
        save(directory / f"batch_{index:02d}.json", clean(batch))
        print(json.dumps({"phase":"materials", "batch":index+1, "execution":batch.get("execution"),
                          "quantity":batch.get("quantity")},ensure_ascii=False),flush=True)
    report = generate_material_series(seed, intent, **kwargs, chat_json=call, record=record,
        on_batch=checkpoint, max_calls=plan["budgets"]["material_calls"],
        max_input_chars=plan["max_input_chars"], max_tokens=plan["material_max_tokens"])
    if replay is not None:
        report["execution_lineage"] = {"replay_manifest_sha256": sha(replay_path),
            "summary": replay.summary(), "source_case": replay_manifest["source_case"],
            "interpretation": "calls_used counts logical batches, including replay; real provider attempts are separate. Original failure remains recorded."}
    save(directory / "report.json", clean(report))
    if not report["downstream_ready"]:
        raise RuntimeError("Material phase incomplete; all raw reports retained, no public corpus exported")
    save(case / "corpus.json", {"corpus":report["corpus"], "result_scope":"research_only"})
    save(case / "about.json", {"public_protocol":protocol, "result_scope":"research_only"})
    save(case / "material_freeze.json", {"status":"complete_transport_only", "semantic_approval":False,
        "file_sha256":{name:sha(case/name) for name in ("corpus.json","about.json","protocol.txt")},
        "source_report_sha256":sha(directory/"report.json"), "scale_observations":report.get("scale_observations"),
        "public_projection":"Only corpus and fixed public protocol; writer notes and design intent excluded"})
    print(json.dumps({"phase":"materials", "status":"frozen", "scale":report.get("scale_observations")},ensure_ascii=False),flush=True)


def run_questions(case, plan, execute):
    from pipeline.question_series import propose_question_series
    validate_public_freeze(case)
    if not execute:
        print(json.dumps({"phase":"questions", "prepared":True, "calls_permitted":0}))
        return
    directory = claim_phase(case, "questions", plan,
        [case/name for name in ("seed.json","question_intent.json","corpus.json","about.json","material_freeze.json")],
        ["tools/run_bc_case.py","pipeline/question_series.py","pipeline/question_proposals.py","pipeline/semantic_review.py","config.py"])
    call, record, clean = provider(directory)
    def checkpoint(snapshot):
        index, batch = snapshot["batch_index"], snapshot["batch_report"]
        save(directory/f"batch_{index:02d}.json",clean(batch))
        print(json.dumps({"phase":"questions","batch":index+1,"execution":batch.get("execution"),
                          "quantity":batch.get("quantity")},ensure_ascii=False),flush=True)
    report = propose_question_series(read(case/"seed.json"), read(case/"question_intent.json"),
        read(case/"corpus.json"), read(case/"about.json")["public_protocol"],
        model=plan["question_model"], batch_specs=plan["question_batches"], chat_json=call,
        max_calls=plan["budgets"]["question_calls"], max_input_chars=plan["max_input_chars"],
        max_tokens=plan["question_max_tokens"], record=record, on_batch=checkpoint)
    save(directory/"report.json",clean(report))
    save(case/"questions.json",{"questions":report["questions"],"result_scope":"research_only"})
    save(case/"question_freeze.json",{"file_sha256":{"questions.json":sha(case/"questions.json")},
        "source_report_sha256":sha(directory/"report.json"),"quality_approval":False,
        "all_readable_candidates_retained":True})
    print(json.dumps({"phase":"questions","status":"frozen","candidates":len(report["questions"])},ensure_ascii=False),flush=True)


def run_reviews(case, plan, execute, workers):
    from pipeline.semantic_review import prepare_review, review_questions
    validate_public_freeze(case)
    qfreeze=read(case/"question_freeze.json")
    if sha(case/"questions.json") != qfreeze["file_sha256"]["questions.json"]:
        raise ValueError("Frozen questions changed")
    questions=read(case/"questions.json")["questions"]
    corpus=read(case/"corpus.json")
    protocol=read(case/"about.json")["public_protocol"]
    kwargs=dict(reviewer_model=plan["reviewer_model"],reader_model=plan["reader_model"])
    combined=prepare_review(questions,corpus,protocol,**kwargs)
    # No semantic prefilter: every readable candidate is retained in this report.
    per_item=[]
    remaining=plan["budgets"]["review_calls"]
    for _ in questions:
        allocated=min(2,remaining)
        per_item.append(allocated)
        remaining-=allocated
    if not execute:
        print(json.dumps({"phase":"review","prepared":True,"candidates":len(questions),
                          "maximum_calls":sum(per_item),"calls_permitted":0}))
        return
    directory=claim_phase(case,"reviews",plan,
        [case/name for name in ("corpus.json","about.json","questions.json","material_freeze.json","question_freeze.json","quality_review_protocol.json")],
        ["tools/run_bc_case.py","pipeline/semantic_review.py","eval/provenance.py","config.py","llm_trace.py"])
    def one(pair):
        index,question=pair
        part_dir=directory/f"item_{index+1:03d}"
        part_dir.mkdir()
        call,record,clean=provider(part_dir)
        report=review_questions([question],corpus,protocol,**kwargs,chat_json=call,record=record,
            max_calls=per_item[index],max_input_chars=plan["max_input_chars"],max_tokens=plan["review_max_tokens"])
        save(part_dir/"report.json",clean(report))
        item=report["items"][0]
        print(json.dumps({"phase":"review","index":index+1,"qid":question.get("qid"),
            "state":item["review_state"],"execution":item["execution"],"validity":item.get("item_validity"),
            "reference_status":item.get("reference_status")},ensure_ascii=False),flush=True)
        return index,report
    with ThreadPoolExecutor(max_workers=workers) as pool:
        parts=list(pool.map(one,enumerate(questions)))
    for index,part in parts:
        expected=combined["items"][index]
        actual=deepcopy(part["items"][0])
        if actual["binding"] != expected["binding"] or actual["source_qid"] != expected["source_qid"]:
            raise ValueError("Partition binding mismatch")
        actual["candidate_id"]=expected["candidate_id"]
        combined["items"][index]=actual
        combined["calls_used"]+=part["calls_used"]
        for event in part["records"]:
            combined["records"].append({**event,"candidate_id":expected["candidate_id"],
                "partition_candidate_id":event.get("candidate_id"),"partition_report":f"item_{index+1:03d}/report.json"})
    combined["mode"]="shadow"
    combined["result_scope"]="research_only"
    combined["aggregation_policy"]="All candidate partitions in original order; no review winner selection"
    combined["budget"]={"max_calls":plan["budgets"]["review_calls"],"max_tokens":plan["review_max_tokens"],
                        "max_input_chars":plan["max_input_chars"],"per_candidate_calls":per_item}
    combined["summary"]={"candidates":len(questions),
        "completed":sum(i["review_state"]=="completed" for i in combined["items"]),
        "pending":sum(i["review_state"]=="pending" for i in combined["items"])}
    save(directory/"report.json",combined)
    print(json.dumps({"phase":"review","status":"recorded_not_quality_approved",**combined["summary"]},ensure_ascii=False),flush=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case",type=Path,required=True)
    parser.add_argument("--phase",choices=("materials","questions","review"),required=True)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--workers",type=int,default=3)
    parser.add_argument("--material-replay", type=Path,
                        help="Explicit frozen replay manifest for a derived material case")
    args=parser.parse_args(argv)
    case=args.case.resolve()
    runs=(ROOT/"output/runs").resolve()
    if case==runs or runs in case.parents:
        parser.error("The quality experiment must not write into historical run directories")
    if not 1<=args.workers<=4:
        parser.error("workers must be between 1 and 4")
    plan=read(case/"plan.json")
    if plan.get("result_scope")!="research_only":
        parser.error("This driver only supports explicitly research-only cases")
    if args.material_replay is not None and args.phase != "materials":
        parser.error("Material replay is only valid for the materials phase")
    if args.phase=="materials":run_materials(case,plan,args.execute,args.material_replay)
    elif args.phase=="questions":run_questions(case,plan,args.execute)
    else:run_reviews(case,plan,args.execute,args.workers)


if __name__=="__main__":
    main()
