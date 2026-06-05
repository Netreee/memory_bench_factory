"""
pipeline.stage_d_v7_question_synthesis — Stage D V7: 程序化出题(structure-first 贯彻到出题端)。

V7 核心转变(redesign_v7.md §2):
  非 M3 题也从 skeleton 的 state【代码派生】题+答案,LLM 只把题面润色成自然问句。
  答案由代码保证 100% 正确 → 一招修掉 V6 三处硬伤:
    - MR 聚合题 gt 算错  → 用 argmax/argmin 代码算
    - 答案元信息泄漏     → 答案是干净的代码输出,不再塞"周期0:Oncall=10,最高"
    - per-period schema 误配 → 代码控制 answer_by_period 结构

各能力派生规则(answer 全由代码定):
  IE  取某 period 某字段值
  KU  取最新值(evolving 字段),answer_by_period 给全程值
  TR  从 state diff 找字段第一次变化的 period
  MR  跨 period 代码聚合(argmax/argmin)← 修 gt 算错的核心
  ABS 取不在 fields 里的字段 → 拒答 sentinel
  M3  保留 V6 信号竞争确定性派生(已可靠)

vs V6:V6 非 M3 题让 LLM 现场出题+算答案(gt 不可信);V7 全代码派生 + LLM 只润色题面,
      且每 chain 只 1 次 LLM 调用(润色),比 V6 每 cell 一次 LLM 快得多。

跑法:cd memory_bench_factory && ./venv/bin/python -m pipeline.stage_d_v7_question_synthesis
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

from .schema import Chain, Question, OpsProfileV3, parse_cell_key
from .stage_d_v6_question_synthesis import synthesize_m3_signal_competition_questions


# ─────────────────────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────────────────────

def _to_number(v) -> Optional[float]:
    """从 '20%'/'15ms'/'10'/'4.5小时' 提取数值,失败返回 None。"""
    if v is None:
        return None
    m = re.search(r"-?\d+\.?\d*", str(v))
    return float(m.group()) if m else None


_QTY_RE = re.compile(
    r"^\s*-?\d+\.?\d*\s*(%|％|ms|s|分钟|小时|h|天|周|次|个|人|条|单|元|万|万元|分|℃)?\s*$",
    re.I)


def _is_quantity(v) -> bool:
    """值是否是【纯数量】(纯数字 + 可选单位)。排除 'V3.0'/'2025-03-20'/'张三'
    这种"恰好含数字的文本/版本号/日期/人名" → 它们不该被 MR 聚合(没有最高/最低之分)。"""
    return bool(_QTY_RE.match(str(v).strip()))


def _is_empty(v) -> bool:
    s = str(v).strip().lower()
    return (not s) or s == "none"


def _get_periods_state(skeleton: dict) -> list:
    """返回 [(period_idx:int, date:str, state:dict)],按 period 排序。"""
    out = []
    for p in skeleton.get("periods", []):
        out.append((int(p.get("period_idx", 0)), str(p.get("date", "")),
                    p.get("state", {}) or {}))
    return sorted(out, key=lambda x: x[0])


def _field_series(periods_state: list, fname: str) -> list:
    """某字段跨 period 的值序列 [(pidx, date, value)](跳过空值)。"""
    return [(pi, d, st.get(fname)) for pi, d, st in periods_state
            if fname in st and not _is_empty(st.get(fname))]


def _natural_ops(capability: str) -> tuple:
    """按能力自然赋三轴算子(observed 风格,不凑配额、不冒充配额轴)。"""
    table = {
        "IE": ("F2", "E1", "Q1"),
        "KU": ("F2", "E4", "Q1"),
        "TR": ("F2", "E4", "Q2"),
        "MR": ("F2", "E1", "Q2"),
        "ABS": ("F1", "E1", "Q3"),
    }
    return table.get(capability, ("F2", "E1", "Q1"))


# ─────────────────────────────────────────────────────────────────────────────
# 各能力程序化派生(返回 spec dict;answer 全由代码定)
# ─────────────────────────────────────────────────────────────────────────────

def _spec(cap, fm, field, intent, eval_period, abp, canon, **extra):
    return {"capability": cap, "failmode": fm, "field": field, "intent": intent,
            "eval_period": str(eval_period), "answer_by_period": abp,
            "answer_canonical": canon, **extra}


def _derive_ie(fields, periods_state, n, fm=None):
    """IE:取某 period 某字段值。★ 优先 evolving 字段(有 period 差异、真需定位);
    stable 字段任何 period 都一样、无记忆挑战,只在 evolving 不够时兜底。
    同字段可取不同 period 以凑数(避免撞车)。"""
    specs = []
    evolving = [f for f in fields if f.get("type") == "evolving"]
    stable = [f for f in fields if f.get("type") == "stable"]
    # 组合池:先 evolving×(中段不同period),再 stable 兜底
    pool = []
    for f in evolving:
        ser = _field_series(periods_state, f.get("name"))
        # evolving 字段取"中段"几个 period(避开首尾,更需精确定位)
        for pi, d, val in ser[1:-1] or ser:
            pool.append((f.get("name"), pi, d, val))
    for f in stable:
        ser = _field_series(periods_state, f.get("name"))
        if ser:
            pi, d, val = ser[len(ser) // 2]
            pool.append((f.get("name"), pi, d, val))
    seen = set()
    for fn, pi, d, val in pool:
        key = (fn, pi)
        if key in seen:
            continue
        seen.add(key)
        specs.append(_spec("IE", fm, fn,
                           f"问 {d}(周期{pi})那一期,{fn} 的值是多少",
                           pi, {str(pi): [str(val)]}, {str(pi): str(val)}))
        if len(specs) >= n:
            break
    return specs


def _derive_ku(fields, periods_state, n, fm=None):
    """KU:取最新值(evolving 字段)。"""
    specs = []
    evolving = [f for f in fields if f.get("type") == "evolving"]
    last_pi = periods_state[-1][0]
    for f in evolving:
        fn = f.get("name")
        ser = _field_series(periods_state, fn)
        if not ser:
            continue
        abp = {str(pi): [str(v)] for pi, _, v in ser}
        specs.append(_spec("KU", fm, fn,
                           f"问 {fn} 截至最新(周期{last_pi})的当前值",
                           ser[-1][0], abp, {k: v[0] for k, v in abp.items()}))
        if len(specs) >= n:
            break
    return specs


def _derive_tr(fields, periods_state, events, n, fm=None):
    """TR:找字段第一次变化的 period(state diff)。"""
    specs = []
    evolving = [f for f in fields
                if f.get("type") in ("evolving", "evolving_signal_competition")]
    for f in evolving:
        fn = f.get("name")
        ser = _field_series(periods_state, fn)
        change = None
        for i in range(1, len(ser)):
            if str(ser[i][2]) != str(ser[i - 1][2]):
                change = (ser[i], ser[i - 1])
                break
        if change is None:
            continue
        (pi, d, newv), (_, _, oldv) = change
        specs.append(_spec("TR", fm, fn,
                           f"问 {fn} 第一次发生变化(从 {oldv} 变成 {newv})是在哪个周期/日期",
                           pi, {str(pi): [f"周期{pi}", str(d)]},
                           {str(pi): f"周期{pi}({d})"}))
        if len(specs) >= n:
            break
    return specs


def _derive_mr(fields, periods_state, n, fm=None):
    """MR:跨 period 代码聚合 argmax/argmin。★ 修 gt 算错的核心:答案由代码算。"""
    specs = []
    for f in fields:
        fn = f.get("name")
        ser = _field_series(periods_state, fn)
        # ★ 只对【纯数量】字段聚合(排除 V3.0/2025-03-20/人名 这种含数字文本)
        if not ser or not all(_is_quantity(v) for _, _, v in ser):
            continue
        nums = [(pi, d, _to_number(v)) for pi, d, v in ser]
        if len(nums) < 2:
            continue
        for agg, picked in [("最高", max(nums, key=lambda x: x[2])),
                            ("最低", min(nums, key=lambda x: x[2]))]:
            pi, d, v = picked
            specs.append(_spec("MR", fm, fn,
                               f"问整个周期里 {fn} {agg}的是哪一个周期",
                               pi, {str(pi): [f"周期{pi}", str(d)]},
                               {str(pi): f"周期{pi}({d})"},
                               agg_value=v))
            if len(specs) >= n:
                break
        if len(specs) >= n:
            break
    return specs


_ABSENT_FIELDS = ["个人邮箱", "联系电话", "身份证号", "家庭住址", "银行账号",
                  "总预算金额", "代码仓库地址", "合同编号", "办公地点"]


def _derive_abs(fields, periods_state, n, fm=None):
    """ABS:取不在 fields 里的字段 → 拒答 sentinel。"""
    have = {f.get("name") for f in fields}
    cand = [a for a in _ABSENT_FIELDS if a not in have]
    all_pi = [str(pi) for pi, _, _ in periods_state]
    specs = []
    for a in cand[:n]:
        abp = {p: ["INSUFFICIENT_EVIDENCE"] for p in all_pi}
        specs.append(_spec("ABS", fm, a,
                           f"问场景里【根本不存在】的字段「{a}」(应拒答)",
                           all_pi[-1], abp, {p: "INSUFFICIENT_EVIDENCE" for p in all_pi}))
    return specs


# ─────────────────────────────────────────────────────────────────────────────
# 失败模式变体(代码叠加;不依赖 LLM 编答案)
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_variants(val: str) -> list:
    """M5 格式变体集(代码生成):数字/百分号/中英文等。"""
    s = str(val)
    out = {s}
    num = _to_number(s)
    if num is not None:
        if "%" in s:
            out.update({f"{num:g}%", f"{num:g} %", f"百分之{num:g}", f"{num/100:g}"})
        else:
            out.update({f"{num:g}", str(int(num)) if num == int(num) else f"{num:g}"})
    return [x for x in out if x.strip()]


def _apply_failmode(spec: dict) -> tuple:
    """据 failmode 叠加 signature/evidence(代码生成,满足 validate 约束)。

    Returns (failmode_signature, failmode_evidence)。
    """
    fm = spec.get("failmode")
    canon = spec.get("answer_canonical", {})
    ep = spec.get("eval_period")
    gold = canon.get(ep, "")
    if fm == "M5":
        variants = _fmt_variants(gold)
        # 把变体并入 answer_by_period(等价集)
        if ep in spec["answer_by_period"]:
            merged = list({*spec["answer_by_period"][ep], *variants})
            spec["answer_by_period"][ep] = merged
        return {"type": "format_mismatch", "equivalent_set": variants}, {}
    if fm == "M4":
        return {"type": "paraphrase_distractor", "verbatim_span": str(gold),
                "distractor_options": [f"近似:{gold}", f"约 {gold}", f"大致 {gold}"]}, {}
    # M1/M2:结构性预测标签,无内容指纹(schema V6 已允许 M1/M2 不带 signature)
    return {}, {}


# ─────────────────────────────────────────────────────────────────────────────
# LLM 润色题面(唯一的 LLM 调用;只写问句,绝不碰答案)
# ─────────────────────────────────────────────────────────────────────────────

PHRASE_SYSTEM = """你是出题助手。给定若干【出题意图】,为每个写一个自然、地道的中文问句。

【铁律】
1. 只把"意图"写成一个问句,问的对象(字段/周期/聚合)必须和意图完全一致,不许偏移。
2. ★ 绝对不要在问句里写出答案、不要带任何数值结果、不要剧透。
3. 问句要像该场景真实用户问的,简洁自然。
4. 数量与顺序与输入意图严格一一对应。

【输出严格 JSON】{"questions": ["问句1", "问句2", ...]}"""


def _phrase_questions(specs: list, theme: str) -> list:
    if not specs:
        return []
    import json
    intents = [{"i": i, "意图": s["intent"]} for i, s in enumerate(specs)]
    user = (f"【场景主题】{theme}\n【出题意图】(共 {len(specs)} 条,逐条写问句)\n"
            f"{json.dumps(intents, ensure_ascii=False, indent=1)}\n\n"
            f"严格 JSON、{len(specs)} 个问句、顺序对应。只写问句不写答案。")
    try:
        data = config.chat_json(
            [{"role": "system", "content": PHRASE_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.4, max_tokens=4096)
        qs = data.get("questions", [])
        if isinstance(qs, list) and len(qs) >= len(specs):
            return [str(q) for q in qs[:len(specs)]]
        print(f"[v7-phrase] 数量不足({len(qs)}<{len(specs)}),模板兜底")
    except Exception as e:
        print(f"[v7-phrase] LLM 异常,模板兜底: {e}")
    return [s["intent"] + "?" for s in specs]


# ─────────────────────────────────────────────────────────────────────────────
# cell 派生 → spec(M3 走 V6;其余走程序化派生)
# ─────────────────────────────────────────────────────────────────────────────

def _derive_cell_specs(skeleton, cap, fm, n):
    periods_state = _get_periods_state(skeleton)
    fields = skeleton.get("fields", [])
    events = skeleton.get("events", [])
    if not periods_state:
        return []
    if cap == "IE":
        return _derive_ie(fields, periods_state, n, fm)
    if cap == "KU":
        return _derive_ku(fields, periods_state, n, fm)
    if cap == "TR":
        return _derive_tr(fields, periods_state, events, n, fm)
    if cap == "MR":
        return _derive_mr(fields, periods_state, n, fm)
    if cap == "ABS":
        return _derive_abs(fields, periods_state, n, fm)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# chain / chains
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_questions_for_chain_v7(chain: Chain, profile: OpsProfileV3,
                                      verbose: bool = True) -> list:
    skeleton = None
    if chain.periods and isinstance(chain.periods[0].state, dict):
        skeleton = chain.periods[0].state.get("_skeleton")
    if not skeleton:
        print(f"[v7] chain {chain.chain_id} 缺 skeleton,跳过")
        return []
    theme = skeleton.get("main_theme", chain.chain_name)

    # 1. 分流:M3 走 V6 信号竞争;非 M3 按 cap 聚合需求,避免逐 cell 各自从头派生而撞车
    from collections import defaultdict
    all_specs, m3_questions = [], []
    qid_counter = 0
    rem_f = dict(profile.formation_quota)  # 仅供 M3 路径(V6)消费
    rem_e = dict(profile.evolution_quota)
    rem_q = dict(profile.query_quota)
    cap_demand = defaultdict(list)  # cap -> [(fm, n), ...]
    for ck, n in sorted(profile.cell_quota.items(), key=lambda kv: (-kv[1], kv[0])):
        if n <= 0:
            continue
        cap, fm = parse_cell_key(ck)
        if fm == "M3":
            qs, qid_counter, _ = synthesize_m3_signal_competition_questions(
                chain, skeleton, cap, n, qid_counter, rem_f, rem_e, rem_q)
            m3_questions.extend(qs)
        else:
            cap_demand[cap].append((fm, n))
    # 每个 cap 一次性派生 total 个【互不相同】的题(内部轮转字段×聚合),再贴 failmode 标签
    for cap, demands in cap_demand.items():
        total = sum(n for _, n in demands)
        specs = _derive_cell_specs(skeleton, cap, None, total)
        idx = 0
        for fm, n in demands:
            for _ in range(n):
                if idx < len(specs):
                    specs[idx]["failmode"] = fm
                    all_specs.append(specs[idx])
                    idx += 1

    # 2. 一次 LLM 润色所有非 M3 题面
    phrasings = _phrase_questions(all_specs, theme)

    # 3. 组装 Question(answer 全是代码派生的)
    questions = list(m3_questions)
    for spec, qtext in zip(all_specs, phrasings):
        cap = spec["capability"]
        fm = spec["failmode"]
        sig, ev = _apply_failmode(spec)
        f_op, e_op, q_op = _natural_ops(cap)
        local_id = f"{cap}_{fm or 'none'}_{qid_counter:04d}"
        qid_counter += 1
        questions.append(Question(
            qid=f"{chain.chain_id}__{local_id}",
            source_chain_id=chain.chain_id,
            question=qtext,
            answer_by_period={str(k): list(v) if isinstance(v, list) else [str(v)]
                              for k, v in spec["answer_by_period"].items()},
            answer_canonical_by_period={str(k): str(v)
                                        for k, v in spec["answer_canonical"].items()},
            field_name=spec["field"],
            capability=cap,
            failmode=fm,
            failmode_signature=sig,
            failmode_evidence=ev,
            formation_op=f_op,
            evolution_op=e_op,
            query_op=q_op,
        ))

    if verbose:
        print(f"[v7] chain {chain.chain_id}: {len(questions)} 题 "
              f"(M3 {len(m3_questions)} + 程序化 {len(all_specs)}),"
              f"全部 answer 代码派生")
    return questions


def synthesize_questions_for_chains_v7(chains: list, profile: OpsProfileV3,
                                       verbose: bool = True) -> list:
    for chain in chains:
        chain.qas = synthesize_questions_for_chain_v7(chain, profile, verbose=verbose)
    return chains


# ─────────────────────────────────────────────────────────────────────────────
# 测试:A→B→C(v6 skeleton)→D(v7 程序化),重点验证 MR gt 由代码算正确
# ─────────────────────────────────────────────────────────────────────────────

def main():
    from .schema import RefinedScenarioSpec, Document
    from .stage_a_dimensions import infer_dimensions
    from .stage_b_ops_profile import build_ops_profile
    from .cell_requirements_table import build_cell_requirements
    from .stage_c_corpus_synthesis_v6 import synthesize_corpus_v6

    spec = RefinedScenarioSpec(
        name="office_v7_test",
        description_refined="AI 工程团队周报,业务线 leader 视角",
        corpus_samples=[
            Document(doc_id="d1", title="周报 W17",
                     content="本周 P0 缺陷率 20%。Oncall 10。负责人:张三。",
                     metadata={"date": "2025-04-17", "doc_type": "周报"}),
            Document(doc_id="d2", title="周报 W18",
                     content="本周 P0 缺陷率 17%。Oncall 9。负责人:张三。",
                     metadata={"date": "2025-04-24", "doc_type": "周报"}),
        ],
        perspective="cross_line_leader", subject_type="project",
        temporal_pattern="weekly", target_size=12,
    )
    print("=== A/B ===")
    dims = infer_dimensions(spec, use_llm=False)
    profile = build_ops_profile(dims, target_size=12, enforce_five_organs_full=False)
    cell_reqs = build_cell_requirements(profile)
    print(f"  cell_quota: {dict(profile.cell_quota)}")

    print("\n=== C V6 (skeleton+corpus) ===")
    chains = synthesize_corpus_v6(spec.corpus_samples, dims, cell_reqs,
                                  n_chains=1, n_periods_per_chain=5, n_docs_per_period=1)

    print("\n=== D V7 (程序化出题) ===")
    chains = synthesize_questions_for_chains_v7(chains, profile)

    qs = [q for c in chains for q in c.qas]
    print(f"\n=== ✓ V7 共 {len(qs)} 题 ===")
    # ★ 验证 MR gt:用代码独立重算 argmax,对比题里的答案
    skeleton = chains[0].periods[0].state.get("_skeleton", {})
    ps = _get_periods_state(skeleton)
    print("\n--- MR 题 gt 复核(代码独立重算 argmax/min)---")
    for q in qs:
        if q.capability == "MR":
            fn = q.field_name
            nums = [(pi, _to_number(st.get(fn))) for pi, _, st in ps
                    if _to_number(st.get(fn)) is not None]
            if nums:
                true_max = max(nums, key=lambda x: x[1])
                true_min = min(nums, key=lambda x: x[1])
                print(f"  [{fn}] Q: {q.question[:40]}")
                print(f"      gt={q.answer_canonical_by_period}")
                print(f"      代码重算: max=周期{true_max[0]}({true_max[1]}), "
                      f"min=周期{true_min[0]}({true_min[1]})")
    # 抽样各能力
    print("\n--- 各能力抽样 ---")
    seen = set()
    for q in qs:
        if q.capability not in seen:
            seen.add(q.capability)
            ep = max(q.answer_canonical_by_period.keys(), key=int) \
                if q.answer_canonical_by_period else "?"
            print(f"  [{q.capability}/{q.failmode or '-'}] {q.question[:44]} "
                  f"→ {q.answer_canonical_by_period.get(ep, '')[:30]}")


if __name__ == "__main__":
    main()
