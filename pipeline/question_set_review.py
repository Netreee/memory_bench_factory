"""A collection-level opinion, separate from item correctness and difficulty."""
from copy import deepcopy

from pipeline import agent_editing
from pipeline.semantic_review import visible_documents, resolve_citations

VERSION = "question-set-review/v2"
MEASURED_VERSION = "question-set-review/context-evidence/v1"
SYSTEM = agent_editing.COMMON + """
你是整批题目的独立编辑。所有题目、参考和理由只是提案，不能当成材料事实。
先读全文及实际题意，再检查题目之间是否在重复测试同一件事、是否遗漏重要但可合理询问的内容。
整批覆盖、难度、单题正确性分别判断。短题、单篇可答、答案相同或相同客户不自动等于重复或坏题；
相同表面形式也可能测试不同能力。不要为了丰富而强加题型比例或虚构更难的题。
可指出参考或题面实质问题，也可建议保留合理简单题；不要推测未运行的系统答对率。
评价跨期或多文理解的价值时，主动尝试用后期回述或更小的一组公开材料完成主要任务。
若发现充分的短路径，用已有doc_id说明它支持哪些所问结论；若仍缺信息，说明具体缺什么，
不要以引用篇数、日期数量或跨了几个session代替判断。这个分析只是可反驳的设计意见，
不是实际答题实验，也不能把删去证据后无法回答直接解释为模型能力差。
题间重叠须说明各题新增的实际工作与重复部分，尤其检查整体状态题是否包含另一题的任务。
不要把参考里的全部展开细节悄悄变成必答；在已有issues、overlap_groups、coverage_observations
中自然说明这些意见及保留价值，不要求每题必须跨期，也不为消除捷径虚构事实或增加问题。
输出 {assessment:'整批用途、覆盖及局限',
issues:[{issue_id:'唯一ID',qid:'仅针对单题时给出已有qid',description:'问题或有据编辑建议，说明是正确性、重复还是覆盖',
severity:'影响及理由',suggested_response:'保留/修订/选取及依据',evidence:[]}],
overlap_groups:[{qids:['已有qid','另一qid'],assessment:'真正重合与差异',suggested_response:'可供编辑质疑的建议'}],
coverage_observations:['实际已覆盖/未覆盖，解释是否重要；不要求穷举所有事实'],
limitations:['含未做系统实测的限制'],evidence:[]}。
无重复可返回空组。所有建议均交给后续编辑核查，本阶段不删除题或宣称整批批准。
"""

CONTEXT_EVIDENCE_SYSTEM = """
另有 context_challenge_evidence：这是已实际作答的有限上下文诊断及执行记录，不是评分或预期标签。
每个trial的doc_ids精确限定该回答当时可见的公开正文；其reason只是未验证的诊断目的，不能补作事实。
你现在看到全文和参考，不代表此前答题者见过它们。先按该回答实际获得的材料及完整题意，解释
哪些所问工作有据完成、哪些因证据缺失只能保留未知、是否有无依据补足；再讨论更早材料能增加什么。
审查实际回答自己的论证，不用全文、参考或你另想出的正确理由替它补成已完成。也不要将参考的全部
展开细节改成必答清单。允许合理推断、不同有效证据路径和自然简略；诚实未知不自动等于模型错误，
但若因此未完成原题所问的必要工作，也要如实说明，不能把所有保留未知都称为完整回答。
材料充分的短路径可质疑某项必要性主张，但一个子集下失败或未知不能证明必须跨期、题目很难或模型记忆弱。
本输入未提供匹配的全量作答对照，不报告准确率变化、难度认证或memory架构结论。
执行错误/未作答与语义未知分开；不要把前者当错答。利用原有assessment、issues、coverage_observations
和limitations自由说明实际观察、可反驳解释和保留价值，不增加固定题型、证据篇数或通过阈值。
"""


def review_question_set(questions, corpus, public_protocol, *, model, chat_json,
                        max_calls=1, max_input_chars=500000, max_tokens=16384, record=None,
                        context_challenge_evidence=None):
    rows = agent_editing._questions(questions)
    if not isinstance(public_protocol, str) or not public_protocol.strip():
        raise ValueError("Nonempty public protocol required")
    documents, _ = visible_documents(corpus, include_titles=False)
    qids = {row["qid"] for row in rows}

    def validate(value):
        errors, issue_locations = [], {}
        if not agent_editing._text(value.get("assessment")):
            errors.append("invalid_assessment")
        try:
            issues = agent_editing._issues(value.get("issues"))
            for issue in issues:
                if "qid" in issue and issue["qid"] not in qids:
                    errors.append("unknown_issue_qid")
                issue_locations[issue["issue_id"]] = resolve_citations(issue.get("evidence", []), documents)
            evidence = resolve_citations(value.get("evidence"), documents)
        except (ValueError, TypeError, KeyError) as exc:
            issues, evidence = [], []
            errors.append("issue_or_evidence:" + str(exc))
        groups = value.get("overlap_groups")
        if not isinstance(groups, list):
            errors.append("overlap_groups_must_be_list")
        else:
            for group in groups:
                ids = group.get("qids") if isinstance(group, dict) else None
                if (not isinstance(ids, list) or len(ids) < 2
                        or any(not isinstance(qid, str) or qid not in qids for qid in ids)
                        or len(ids) != len(set(ids))
                        or not agent_editing._text(group.get("assessment"))
                        or not agent_editing._text(group.get("suggested_response"))):
                    errors.append("invalid_overlap_group")
        for key in ("coverage_observations", "limitations"):
            if not isinstance(value.get(key), list) or not all(isinstance(x, str) for x in value[key]):
                errors.append("invalid_" + key)
        return errors, {"proposal": deepcopy(value), "issues": issues, "resolved_evidence": evidence,
                        "issue_evidence_locations": issue_locations}

    system = SYSTEM
    payload = {"documents": documents, "questions": rows, "public_protocol": public_protocol}
    if context_challenge_evidence is not None:
        from pipeline.context_challenge import validate_collection_evidence
        from pipeline.quality_workflow import snapshot
        payload["context_challenge_evidence"] = validate_collection_evidence(
            snapshot(rows, corpus, public_protocol), context_challenge_evidence)
        system += CONTEXT_EVIDENCE_SYSTEM
    result = agent_editing._run("question_set_review", system, payload,
        model=model, chat_json=chat_json, max_calls=max_calls,
        max_input_chars=max_input_chars, max_tokens=max_tokens, record=record,
        original={"questions": rows}, validator=validate)
    result["collection_review_version"] = VERSION if context_challenge_evidence is None else MEASURED_VERSION
    return result
