"""Rebuild the three curated seed JSON files from reviewed local source hashes.

This script hashes source bytes; it does not parse attachments or call an LLM.
Source text is deliberately not copied into prompts. The short synthetic examples
and the mechanism summaries below were curated from the documented second read.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/real_seed/yukai_2026-09-15/extracted"
OUT = ROOT / "seeds"


def source(sid: str, folder: Path, prefix: str, role: str, title: str = "") -> dict:
    matches = [p for p in folder.iterdir() if p.is_file() and p.name.startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f"Expected one source for {folder / prefix}, found {len(matches)}")
    path = matches[0]
    return {"id": sid, "path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "role": role, "title": title or path.name}


def ref(sid: str, locator: str) -> dict:
    return {"source_id": sid, "locator": locator}


def field(name: str, kind: str = "category", **extra) -> dict:
    return {"name": name, "kind": kind, **extra}


def entity(tid: str, noun: str, count: int, fields: list[dict]) -> dict:
    return {"id": tid, "noun": noun, "count": count, "fields": fields}


def relation(rid: str, src: str, dst: str, name: str, temporal: bool = False) -> dict:
    return {"id": rid, "from_type": src, "to_type": dst, "field": name,
            "temporal": temporal, "min_count": 1}


def event(eid: str, label: str, roles: dict, effects: list[tuple[str, str]],
          bindings: list[tuple[str, str, str]] | None = None) -> dict:
    result = {"id": eid, "label": label, "roles": roles,
              "effect_fields": [{"role": role, "field": name} for role, name in effects],
              "min_count": 1}
    if bindings:
        result["relation_bindings"] = [
            {"relation": rid, "from_role": src, "to_role": dst}
            for rid, src, dst in bindings
        ]
    return result


def cause(cid: str, trigger: str, effect: str, delay: int, shared_roles: list[str]) -> dict:
    return {"id": cid, "trigger_event": trigger, "effect_event": effect,
            "delay_sessions": delay, "shared_roles": shared_roles}


def mechanism(mid: str, description: str, refs: list[dict], entities: list[str],
              relations: list[str], events: list[str], causes: list[str]) -> dict:
    return {"id": mid, "description": description, "source_refs": refs,
            "required_entity_types": entities, "required_relation_types": relations,
            "required_event_types": events, "required_causal_rules": causes}


def example(title: str, content: str, kind: str, date: str, mechanisms: list[str]) -> dict:
    return {"title": title, "content": content, "doc_type": kind, "date": date,
            "origin": "synthetic", "source_refs": [], "mechanism_refs": mechanisms}


def insurance() -> dict:
    folder = RAW / "0915保险CUA返修"
    return {
        "schema_version": 1,
        "seed_id": "insurance_annual_review_v1",
        "family": "insurance_annual_review",
        "title": "保险年度档案：版本采用与分析失效",
        "description": "由年度档案任务设计启发的合成工作世界。保留来源版本、财年口径与分析依赖机制，"
                       "生成新公司、新记录和新数值；原年报与评级原件未提供，不重放或认证设计卡中的真实数值。",
        "sources": [
            source("insurance_task", folder, "insurance-cua", "corpus"),
            source("insurance_design", folder, "United_Ajod_最终", "builder_only"),
            source("insurance_rubric", folder, "United_Ajod_rubric", "evaluator_only",
                   "未冻结评分稿；仅登记来源，不进入机制、示例或生成提示词"),
        ],
        "task": {
            "objective": "维护保险客户年度档案，在多份资料逐步到达时更新采用依据、财务记录和分析确认状态。",
            "instructions": "生成合成公司与来源，金额统一为 NPR million。来源记录应分别记录报告期间、版本性质和发布日期；"
                            "同一指标的暂定版、最终版和比较期重述不能只按收到先后裁决。分析记录必须关联客户与采用来源，"
                            "来源采用发生变化后记录分析是否需要重核，完成复核后再确认。保留合法等价留痕路径；"
                            "不要求候选编号、双观察记录或固定工作表。原任务研究截止日仅是机制出处，"
                            "生成世界可使用明确标注的合成日历，不把未提供的原年报数值当真值。",
        },
        "exemplars": [
            example("澄川保险的资料登记", "本例公司、文档和数值均为合成。澄川保险的来源记录《年度预报甲》："
                    "来源客户=澄川保险；报告期间=FY2024；版本性质=暂定；来源发布日期=2025-01-06；"
                    "税后利润=120 NPR million。分析记录《澄川年度复核》的采用来源为《年度预报甲》，分析确认状态=已确认。",
                    "资料登记", "2025-01-06", ["version_adoption"]),
            example("终版到达后的采用更新", "本例为合成后续记录。澄川保险的《年度终报乙》已收到："
                    "报告期间=FY2024；版本性质=最终；来源发布日期=2025-01-13；税后利润=96 NPR million。"
                    "《澄川年度复核》的采用来源改为《年度终报乙》，采用口径=最终年度报告；分析确认状态=待重核。"
                    "旧分析采用的是预报，不应继续标作已确认。", "采用变更单", "2025-01-13",
                    ["version_adoption", "analysis_reconfirmation"]),
            example("重核完成", "本例为合成复核记录。《澄川年度复核》已检查新采用来源及相关计算输入，"
                    "分析确认状态=已确认；澄川保险的档案阶段=待复核。该条仅记录工作流状态，未提供或认证真实公司的财务结果。",
                    "复核记录", "2025-01-20", ["analysis_reconfirmation"]),
        ],
        "mechanisms": [
            mechanism("version_adoption", "来源记录的版本性质、报告期间与发布日期共同决定采用口径；"
                      "收到资料和采用资料分成不同事件，保持旧来源可追踪。不得将设计卡的具体 Oracle 数值复制为事实。",
                      [ref("insurance_task", "Markdown 第 19–24 行：来源、财年、版本、单位及引用可追溯"),
                       ref("insurance_design", "第 3 节时间与口径；第 7 节暂定/最终版本与比较期重述机制")],
                      ["insurer", "source_record", "analysis_record"],
                      ["source_for_insurer", "analysis_uses_source"],
                      ["report_received", "adoption_revised"], ["receipt_to_adoption"]),
            mechanism("analysis_reconfirmation", "采用来源变化后，下游分析进入待重核；复核事件更新确认状态和档案阶段。"
                      "这里要求可观察的状态依赖，不声称第一阶段已实现利润桥、ROE 或依赖计算执行器。",
                      [ref("insurance_design", "第 6 节子步骤 13；第 8 节分析有效性等价路径；第 9 节成功判据")],
                      ["insurer", "analysis_record"], ["analysis_for_insurer"],
                      ["adoption_revised", "analysis_rechecked"], ["adoption_to_recheck"]),
        ],
        "blueprint_requirements": {
            "entity_types": [
                entity("insurer", "保险客户", 2, [field("所属市场"), field("档案阶段", "status")]),
                entity("source_record", "财务来源记录", 4, [field("来源客户", "reference"),
                       field("报告期间"), field("版本性质"), field("来源发布日期", "date"),
                       field("税后利润", "numeric", unit="NPR million"), field("资料接收状态", "status")]),
                entity("analysis_record", "年度分析记录", 2, [field("分析客户", "reference"),
                       field("采用来源", "reference"), field("采用口径"), field("分析确认状态", "status")]),
            ],
            "relation_types": [relation("source_for_insurer", "source_record", "insurer", "来源客户"),
                               relation("analysis_for_insurer", "analysis_record", "insurer", "分析客户"),
                               relation("analysis_uses_source", "analysis_record", "source_record", "采用来源", True)],
            "event_types": [
                event("report_received", "报告到达登记", {"source": "source_record"}, [("source", "资料接收状态")]),
                event("adoption_revised", "采用口径复核", {"analysis": "analysis_record", "source": "source_record"},
                      [("analysis", "采用口径"), ("analysis", "分析确认状态")],
                      [("analysis_uses_source", "analysis", "source")]),
                event("analysis_rechecked", "分析重新确认", {"analysis": "analysis_record", "client": "insurer"},
                      [("analysis", "分析确认状态"), ("client", "档案阶段")],
                      [("analysis_for_insurer", "analysis", "client")]),
            ],
            "causal_rules": [cause("receipt_to_adoption", "report_received", "adoption_revised", 1, ["source"]),
                             cause("adoption_to_recheck", "adoption_revised", "analysis_rechecked", 1, ["analysis"])],
            "evidence_channels": ["资料登记", "采用变更单", "复核记录"],
            "temporal_model": {"min_sessions": 4},
        },
    }


def legal() -> dict:
    folder = RAW / "研判样例数据（多附件）/法律"
    return {
        "schema_version": 1, "seed_id": "legal_merchant_review_v1", "family": "legal_merchant_review",
        "title": "商户复核：证据充分性与处置条件",
        "description": "由商户复核提示词和监管案例启发的合成运营工单。报道提供风险分类和证据关系，"
                       "不提供完整的真实商户后续轨迹；所有新增补证和处置事件均为合成。",
        "sources": [source("legal_task", folder, "prompt", "corpus"),
                    source("legal_cases", folder, "附件1.", "corpus"),
                    source("legal_commentary", folder, "附件2.", "corpus"),
                    source("legal_rules", folder, "附件3.", "corpus"),
                    source("legal_investigation", folder, "附件4.", "corpus"),
                    source("legal_clipped", folder, "附件5.", "builder_only",
                           "裁切文档，仅登记完整性问题；正文禁用于示例/事实，且与附件4同案报道重合")],
        "task": {
            "objective": "根据逐步到达的风险线索和补充材料维护商户复核工单，记录证据充分性、负责团队、允许动作与复查结论。",
            "instructions": "生成新商户和合成材料；地址不一致、门头异常、转单投诉只是待核验线索。"
                            "证据未提供不等于违法已成立，也不等于问题不存在。区分证照有效但公示未更新、实际场所未覆盖等不同情形。"
                            "工单应关联商户，补充材料应关联工单；新增材料后再复核结论与处置阶段。"
                            "平台运营动作与监管处罚分开；不捏造真实处罚编号、真实坐标或已经完成的真实整改。"
                            "附件5的裁切正文不得使用，附件4和5的同案叙述不得算作两个独立原始证据。",
        },
        "exemplars": [
            example("青禾食坊的初次工单", "以下商户与工作记录为合成。商户青禾食坊出现线上地址与骑手取餐地址不一致的反馈。"
                    "工单《青禾地址核验》：关联商户=青禾食坊；证据充分性=待补证；处置阶段=资料核验；责任团队=商户运营。"
                    "复查结论=尚不能确认具体原因。", "风险工单", "2025-01-06", ["evidence_sufficiency", "scoped_action"]),
            example("补充材料到达", "本条为合成补证记录。《青禾新址材料》：关联工单=《青禾地址核验》；材料种类=新址证照与现场照片；"
                    "材料完整性=已收齐待核验；信息来源=商户提交。材料收到尚不表示平台已经核验通过。",
                    "补证登记", "2025-01-13", ["evidence_sufficiency"]),
            example("复核后的动作调整", "本条为合成复核结果。《青禾地址核验》已核对新址材料，证据充分性=本次地址问题证据充分；"
                    "复查结论=新址证照有效而平台公示未更新；处置阶段=待更新公示；责任团队=商户运营。"
                    "待确认公示完成后再关闭该问题；未作监管处罚决定。", "复核记录", "2025-01-20",
                    ["evidence_sufficiency", "scoped_action"]),
        ],
        "mechanisms": [
            mechanism("evidence_sufficiency", "从线索到已核验结论需要材料到达与复核两步；知识状态作为明确的业务字段，"
                      "不将待补证状态映射为无此项。不同异常原因可以得到不同结论。",
                      [ref("legal_task", "RTF 正文：只能据已知材料生成待核实工单，禁止捏造商户事实及整改完成状态"),
                       ref("legal_cases", "PDF 第1–2页：搬迁已办新证但平台未更新、冒用资质等不同案例")],
                      ["merchant", "review_ticket", "evidence_record"],
                      ["ticket_for_merchant", "evidence_for_ticket"],
                      ["risk_registered", "evidence_received", "review_updated"], ["evidence_to_review"]),
            mechanism("scoped_action", "运营责任和允许动作随已核验结论更新，工单保留恢复/关闭前仍需满足的条件。"
                      "新闻中的调查过程仅启发证据关系，不能替代真实商户台账或证明合成商户受罚。",
                      [ref("legal_task", "RTF 正文：动作、责任团队、复查条件与待核实字段要求"),
                       ref("legal_investigation", "PDF 第1–6页：投诉、订单流转、账号关联及交叉核验过程；非原始调查数据库")],
                      ["merchant", "review_ticket"], ["ticket_for_merchant"],
                      ["review_updated", "action_updated"], ["review_to_action"]),
        ],
        "blueprint_requirements": {
            "entity_types": [
                entity("merchant", "平台商户", 3, [field("商户类型"), field("经营展示状态", "status")]),
                entity("review_ticket", "商户复核工单", 3, [field("关联商户", "reference"), field("证据充分性", "status"),
                       field("处置阶段", "status"), field("责任团队"), field("复查结论")]),
                entity("evidence_record", "补充材料记录", 3, [field("关联工单", "reference"), field("材料种类"),
                       field("材料完整性", "status"), field("信息来源")]),
            ],
            "relation_types": [relation("ticket_for_merchant", "review_ticket", "merchant", "关联商户"),
                               relation("evidence_for_ticket", "evidence_record", "review_ticket", "关联工单")],
            "event_types": [
                event("risk_registered", "风险工单登记", {"ticket": "review_ticket"}, [("ticket", "证据充分性")]),
                event("evidence_received", "补充材料接收", {"evidence": "evidence_record", "ticket": "review_ticket"},
                      [("evidence", "材料完整性")], [("evidence_for_ticket", "evidence", "ticket")]),
                event("review_updated", "证据复核完成", {"ticket": "review_ticket", "evidence": "evidence_record"},
                      [("ticket", "证据充分性"), ("ticket", "复查结论")],
                      [("evidence_for_ticket", "evidence", "ticket")]),
                event("action_updated", "工单动作调整", {"ticket": "review_ticket", "merchant": "merchant"},
                      [("ticket", "处置阶段"), ("ticket", "责任团队"), ("merchant", "经营展示状态")],
                      [("ticket_for_merchant", "ticket", "merchant")]),
            ],
            "causal_rules": [cause("evidence_to_review", "evidence_received", "review_updated", 1, ["ticket", "evidence"]),
                             cause("review_to_action", "review_updated", "action_updated", 1, ["ticket"])],
            "evidence_channels": ["风险工单", "补证登记", "复核记录"], "temporal_model": {"min_sessions": 4},
        },
    }


def finance() -> dict:
    folder = RAW / "研判样例数据（多附件）/金融"
    return {
        "schema_version": 1, "seed_id": "finance_product_scope_v1", "family": "finance_product_scope",
        "title": "金融资料复核：份额币种日期与缺失输入",
        "description": "由内部培训及测算任务启发的合成资料复核世界。保留产品/份额/币种/数据日期的联合归属，"
                       "并以明确的准备状态区分已披露零值、缺失与口径不匹配。第一阶段不提供新增金融计算题型或公式执行器。",
        "sources": [source("finance_task", folder, "prompt", "corpus"),
                    source("finance_hang_seng", folder, "附件一_", "corpus"),
                    source("finance_bosera", folder, "附件二_", "corpus"),
                    source("finance_global_x", folder, "附件三_", "corpus"),
                    source("finance_premia", folder, "附件四_", "corpus"),
                    source("finance_china_amc", folder, "附件五_", "corpus"),
                    source("finance_efund", folder, "附件六_", "corpus"),
                    source("finance_boc", folder, "附件七_", "corpus")],
        "task": {
            "objective": "维护供内部培训使用的产品资料和测算准备记录，在份额、币种、日期及字段口径明确后更新准备状态与补数清单。",
            "instructions": "生成合成产品、份额及观测记录；不将公开文件替代内部持仓、净值、同步价格或客户成本数据。"
                            "交易币种、估值币种、派息币种分别记录，累积份额和派息份额分开。"
                            "数据日期不是文件发布日期；同文件不同字段可有不同截止日。有效久期不自动等于修正久期。"
                            "已披露零值不等于缺失，缺费用不能填零；没有匹配输入时记录待补或口径不匹配，不能承诺计算已完成。"
                            "本种子只增强输入到白皮书的字段、关系与事件合同，不新增收益、久期或折溢价计算执行器；"
                            "不生成交易建议、收益承诺或真实客户结论。",
        },
        "exemplars": [
            example("岚桥产品的份额档案", "以下产品和份额均为合成。岚桥短债产品的岚桥港币派息份额：所属产品=岚桥短债；"
                    "交易币种=HKD；估值币种=USD；派息币种=HKD；分派方式=派息。"
                    "另一份额岚桥美元累积份额：所属产品=岚桥短债；交易币种=USD；估值币种=USD；派息币种=不适用；分派方式=累积。",
                    "份额资料卡", "2025-01-06", ["scope_binding"]),
            example("零值披露与不同口径", "本条为合成资料。《岚桥月末久期观测》：观测份额=岚桥港币派息份额；"
                    "数据日期=2025-01-03；指标口径=有效久期；有效久期=0.00 年；输入状态=已披露。"
                    "这不是修正久期已披露的证明。《岚桥测算准备单》的测算份额为岚桥港币派息份额，"
                    "参考观测为《岚桥月末久期观测》，口径核对状态=指标口径不匹配；测算准备状态=需补修正久期。",
                    "输入复核单", "2025-01-13", ["scope_binding", "missing_input_gate"]),
            example("日期与费用核验", "本条为合成复核记录。《岚桥测算准备单》的价格日期与净值日期尚未对齐，"
                    "口径核对状态=样本日期不一致；费用输入状态=未提供；测算准备状态=待补同日数据及费用。"
                    "费用未提供不能解释为零费用，此条没有给出敏感度或收益率计算结果。",
                    "补数清单", "2025-01-20", ["missing_input_gate"]),
        ],
        "mechanisms": [
            mechanism("scope_binding", "值必须绑定产品、份额、指标口径、币种和数据日期；相邻表格、另一份额或另一截止日的值不能直接补位。"
                      "资料发布与输入复核形成独立事件，复核状态显式进入世界。",
                      [ref("finance_hang_seng", "PDF 第1、3–4页：交易/基本/派息货币与市场价格、净值的区别"),
                       ref("finance_global_x", "PDF 第1、4页：交易柜台币种和分派币种"),
                       ref("finance_bosera", "PDF 第1页：上市累积股份类别"),
                       ref("finance_china_amc", "PDF 第3页：财务净值、人员及其他内容采用不同截止日期"),
                       ref("finance_efund", "PDF 第3–4页：A/C与人民币/美元份额代码、财务表单位")],
                      ["product", "share_class", "metric_observation", "worksheet_record"],
                      ["share_of_product", "observation_for_share", "worksheet_for_share", "worksheet_uses_observation"],
                      ["observation_registered", "scope_reviewed"], ["observation_to_scope_review"]),
            mechanism("missing_input_gate", "零值、未提供和口径不匹配是不同输入状态；所需资料不足时输出准备状态与补数任务。"
                      "包内 Premia 的零有效久期只支持零非缺失这一机制，不被改写成任何合成产品的真实修正久期。"
                      "第一阶段约束结构和状态，不声称自动验证金融公式。",
                      [ref("finance_task", "RTF 正文 Excel 要求：缺修正久期、缺凸性、缺或零净值、日期不一致、缺费用的处理"),
                       ref("finance_premia", "PDF 第2页：基金特点表有效久期与凸性0.00；截至2026-05-31，基金与指数表分开")],
                      ["metric_observation", "worksheet_record"], ["worksheet_uses_observation"],
                      ["scope_reviewed", "readiness_updated"], ["scope_to_readiness"]),
        ],
        "blueprint_requirements": {
            "entity_types": [
                entity("product", "债券投资产品", 2, [field("产品类别")]),
                entity("share_class", "产品份额", 3, [field("所属产品", "reference"), field("交易币种"),
                       field("估值币种"), field("派息币种"), field("分派方式")]),
                entity("metric_observation", "指标观测记录", 3, [field("观测份额", "reference"), field("数据日期", "date"),
                       field("指标口径"), field("有效久期", "numeric", unit="年"), field("输入状态", "status")]),
                entity("worksheet_record", "测算准备记录", 2, [field("测算份额", "reference"), field("参考观测", "reference"),
                       field("口径核对状态", "status"), field("费用输入状态", "status"), field("测算准备状态", "status")]),
            ],
            "relation_types": [relation("share_of_product", "share_class", "product", "所属产品"),
                               relation("observation_for_share", "metric_observation", "share_class", "观测份额"),
                               relation("worksheet_for_share", "worksheet_record", "share_class", "测算份额"),
                               relation("worksheet_uses_observation", "worksheet_record", "metric_observation", "参考观测", True)],
            "event_types": [
                event("observation_registered", "指标资料登记", {"observation": "metric_observation"}, [("observation", "输入状态")]),
                event("scope_reviewed", "输入口径核对", {"observation": "metric_observation", "worksheet": "worksheet_record"},
                      [("worksheet", "口径核对状态")],
                      [("worksheet_uses_observation", "worksheet", "observation")]),
                event("readiness_updated", "测算准备更新", {"worksheet": "worksheet_record"},
                      [("worksheet", "费用输入状态"), ("worksheet", "测算准备状态")]),
            ],
            "causal_rules": [cause("observation_to_scope_review", "observation_registered", "scope_reviewed", 1, ["observation"]),
                             cause("scope_to_readiness", "scope_reviewed", "readiness_updated", 1, ["worksheet"])],
            "evidence_channels": ["份额资料卡", "输入复核单", "补数清单"], "temporal_model": {"min_sessions": 4},
        },
    }


def main() -> None:
    for build in (insurance, legal, finance):
        pack = build()
        path = OUT / f"{build.__name__}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{path.relative_to(ROOT).as_posix()}: {len(pack['sources'])} sources, "
              f"{len(pack['mechanisms'])} mechanisms, {len(pack['exemplars'])} synthetic exemplars")


if __name__ == "__main__":
    main()
