"""
pipeline.run_pipeline_v10 — V10 端到端(0→A→B→C→点菜→D→渲染+草堆→E→F四闸→G有效性)。

按【单场景单进程】设计,便于并行:
  ./venv/bin/python -m pipeline.run_pipeline_v10 <scenario_key>   # 跑一个场景
  scenario_key ∈ {office, medical, crm, itops, legal}
产物:output/v10_bench_<key>.json(world+corpus+questions+reject_log+validity)
"""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.schema import RefinedScenarioSpec, Document
from pipeline.stage_a_dimensions import infer_dimensions
from pipeline.stages_v8 import stage_0_refine, stage_b_capability_plan
from pipeline.stages_v10 import (
    stage_c_world_v10, stage_d_corpus_v10, stage_d_add_filler_v10, stage_d0_filler_palette,
    stage_e_synthesize_v10, stage_f_validate_v10, stage_g_validity_v10,
)
from pipeline.order_gen import generate_orders

OUT_DIR = Path(__file__).resolve().parent.parent / "output"


# ─────────────────────────────────────────────────────────────────────────────
# 5 个场景(描述 + few-shot 文档 + 规模旋钮)。office 顶到 ~100,其余中等规模验泛化。
# ─────────────────────────────────────────────────────────────────────────────
SCENARIOS = {
    "verify": {   # ★V11 小场景:快、便宜,专门验证新能力(ORDER/DURATION/PREEXPIRE)+ 非单调 + filler
        "n_entities": 3, "n_sessions": 6, "target_size": 24, "n_filler": 2,
        "description": (
            "评测记忆体在『公司多部门周报』场景的表现。跨业务线 leader 每周看多个部门的周报,关注各部门 "
            "P0缺陷率、Oncall数量、负责人、汇报对象等随周变化的字段(数值有涨有跌、负责人会换、某些指标会停统计),"
            "要以最新信息为准。"),
        "docs": [
            {"doc_id": "d1", "title": "AI工程周报 W17", "content": "本周 P0缺陷率 20%,Oncall 10。负责人:张三。",
             "metadata": {"date": "2025-04-17", "doc_type": "周报"}},
            {"doc_id": "d2", "title": "数据平台周报 W17", "content": "本周 SLA 99.1%,值班 8 人。负责人:王五。",
             "metadata": {"date": "2025-04-17", "doc_type": "周报"}},
        ],
    },
    "office": {
        "n_entities": 5, "n_sessions": 10, "target_size": 60, "n_filler": 4,
        "description": (
            "评测记忆体在『公司多部门周报』场景的表现。跨业务线 leader 每周看多个部门(AI工程部、"
            "数据平台部、算法研究部等)的周报,关注各部门 P0缺陷率、Oncall数量、负责人、汇报对象等"
            "随周变化的字段,要以最新信息为准;负责人变动有时带动汇报关系变化。"),
        "docs": [
            {"doc_id": "d1", "title": "AI工程周报 W17", "content": "本周 P0缺陷率 20%,Oncall 10。负责人:张三。",
             "metadata": {"date": "2025-04-17", "doc_type": "周报"}},
            {"doc_id": "d2", "title": "数据平台周报 W17", "content": "本周 SLA 99.1%,值班 8 人。负责人:王五。",
             "metadata": {"date": "2025-04-17", "doc_type": "周报"}},
        ],
    },
    "medical": {
        "n_entities": 5, "n_sessions": 10, "target_size": 60, "n_filler": 4,
        "description": (
            "评测记忆体在『慢病患者多次就诊病程跟踪』场景的表现。医生跨多次门诊追踪多位患者的"
            "血压、血糖、用药方案、诊断结论、主治医师等随时间演化的字段;某些指标会停止监测、"
            "用药会调整,要以最新一次就诊为准。"),
        "docs": [
            {"doc_id": "d1", "title": "门诊记录 0312", "content": "患者A本次血压 150/95,空腹血糖 7.8,主治:李医生。予二甲双胍。",
             "metadata": {"date": "2025-03-12", "doc_type": "门诊记录"}},
            {"doc_id": "d2", "title": "检查报告 0312", "content": "患者B糖化血红蛋白 8.2%,诊断:2型糖尿病。主治:周医生。",
             "metadata": {"date": "2025-03-12", "doc_type": "检查报告"}},
        ],
    },
    "crm": {
        "n_entities": 5, "n_sessions": 10, "target_size": 60, "n_filler": 4,
        "description": (
            "评测记忆体在『B2B 销售客户跟进』场景的表现。销售跨多周跟进多个客户,关注每个客户的"
            "商机阶段、对接人、报价金额、成交意向、负责销售等随时间演化的字段;客户会流失/搁置,"
            "对接人会换,要以最新跟进为准。"),
        "docs": [
            {"doc_id": "d1", "title": "客户跟进 0405", "content": "客户甲公司本周进入商务谈判阶段,报价 80万,对接人:陈总。负责销售:小林。",
             "metadata": {"date": "2025-04-05", "doc_type": "跟进记录"}},
            {"doc_id": "d2", "title": "客户跟进 0405", "content": "客户乙公司意向转弱,阶段回到需求确认。对接人:刘经理。",
             "metadata": {"date": "2025-04-05", "doc_type": "跟进记录"}},
        ],
    },
    "itops": {
        "n_entities": 5, "n_sessions": 10, "target_size": 60, "n_filler": 4,
        "description": (
            "评测记忆体在『多服务 IT 运维事件跟踪』场景的表现。SRE 跨多周跟踪多个线上服务的"
            "告警级别、值班人、当前版本、SLA达成率、负责人等随时间演化的字段;某服务会下线、"
            "版本会升级,要以最新状态为准。"),
        "docs": [
            {"doc_id": "d1", "title": "运维周报 W20", "content": "支付服务本周告警 P1,值班:小赵,版本 v2.3.1,SLA 99.95%。负责人:孙工。",
             "metadata": {"date": "2025-05-19", "doc_type": "运维周报"}},
            {"doc_id": "d2", "title": "值班通报 W20", "content": "搜索服务本周无重大告警,版本 v1.8.0。负责人:钱工。",
             "metadata": {"date": "2025-05-19", "doc_type": "值班通报"}},
        ],
    },
    "legal": {
        "n_entities": 5, "n_sessions": 10, "target_size": 60, "n_filler": 4,
        "description": (
            "评测记忆体在『律所多案件进展跟踪』场景的表现。律师跨多个阶段跟踪多个案件的"
            "案件状态、承办律师、诉请金额、开庭/举证期限、对方代理等随时间演化的字段;"
            "案件会和解/撤诉,承办律师会变更,要以最新进展为准。"),
        "docs": [
            {"doc_id": "d1", "title": "案件进展 0508", "content": "甲诉乙案本阶段进入一审审理,诉请金额 120万,承办:张律师。",
             "metadata": {"date": "2025-05-08", "doc_type": "案件进展"}},
            {"doc_id": "d2", "title": "案件进展 0508", "content": "丙合同纠纷案完成举证,承办:王律师。对方代理:某所李律师。",
             "metadata": {"date": "2025-05-08", "doc_type": "案件进展"}},
        ],
    },
}


def run_one(key: str, verbose=True) -> dict:
    sc = SCENARIOS[key]
    t0 = time.time()
    log = lambda m: print(f"[{key}] {m}", flush=True)  # noqa: E731
    log(f"=== 开跑 (n_entities={sc['n_entities']}, n_sessions={sc['n_sessions']}, target={sc['target_size']}) ===")

    log("Stage 0 Refine...")
    spec = stage_0_refine(sc["description"], sc["docs"])
    log(f"  fields: {[f.get('name') for f in spec.get('key_fields', [])]}")

    log("Stage A Dimensions...")
    docs = [Document(doc_id=d["doc_id"], title=d["title"], content=d["content"], metadata=d["metadata"])
            for d in sc["docs"]]
    refined = RefinedScenarioSpec(name=f"v10_{key}", description_refined=spec.get("description_refined", ""),
                                  corpus_samples=docs, subject_type=spec.get("subject"),
                                  perspective=spec.get("perspective"), target_size=sc["target_size"])
    dims = infer_dimensions(refined, use_llm=True)

    log("Stage B CapabilityPlan...")
    plan = stage_b_capability_plan(spec, dims, sc["target_size"])
    log(f"  plan: {[(it.get('capability'), it.get('n')) for it in plan.get('items', [])]}")

    log("Stage C 多实体状态机世界...")
    ws, table, issues = stage_c_world_v10(spec, dims, plan, n_entities=sc["n_entities"], n_sessions=sc["n_sessions"])

    log("点菜 generate_orders...")
    plan_req = {it["capability"]: it["n"] for it in plan.get("items", [])}
    plan_req["FORGET"] = max(plan_req.get("FORGET", 0), 4)   # 保证 FORGET 有量喂 FAMA
    for cap, n in {"ORDER": 4, "PREEXPIRE": 3}.items():   # ★V11 新能力配额(DURATION 摘除:几周整数判分天生歧义)
        plan_req[cap] = n
    orders = generate_orders(ws, plan_req)
    log(f"  {len(orders)} 张订单")

    log("Stage D 渲染(mention-on-change)...")
    corpus, leak = stage_d_corpus_v10(ws)
    log("Stage D0 filler palette(领域换皮)...")
    palette = stage_d0_filler_palette(spec)
    log("Stage D-filler 草堆...")
    corpus = stage_d_add_filler_v10(ws, corpus, n_per_session=sc["n_filler"], palette=palette)

    log("Stage E 合成...")
    raw = stage_e_synthesize_v10(orders, corpus)
    log("Stage F 四闸...")
    passed, rejects = stage_f_validate_v10(raw, corpus, ws)
    log(f"  通过 {len(passed)}/{len(raw)};by_cap {dict(Counter(q['capability'] for q in passed))}")

    log("Stage G 有效性...")
    validity = stage_g_validity_v10(passed, corpus, full_oracle=False)   # ★ 关全文 oracle,成本与语料规模无关

    bench = {
        "benchmark_id": f"v10_{key}", "scenario": key, "spec": spec,
        "capability_plan": plan, "world_state": ws.to_dict(), "world_issues": issues,
        "corpus": corpus, "questions": passed, "reject_log": rejects, "validity": validity,
        "orders": [asdict(o) for o in orders],
        "stats": {"raw": len(raw), "passed": len(passed),
                  "by_capability": dict(Counter(q["capability"] for q in passed)),
                  "elapsed_min": round((time.time() - t0) / 60, 1)},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"v10_bench_{key}.json"
    out.write_text(json.dumps(bench, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"✓ {out} ({out.stat().st_size // 1024} KB);通过 {len(passed)} 题,用时 {bench['stats']['elapsed_min']} 分")
    return bench


def main():
    keys = sys.argv[1:] or ["office"]
    if keys == ["all"]:
        keys = list(SCENARIOS)
    for k in keys:
        if k not in SCENARIOS:
            print(f"未知场景 {k};可选 {list(SCENARIOS)}"); continue
        run_one(k)


if __name__ == "__main__":
    main()
