"""Compile four reviewed attachment families into the existing v2 seed interface.

This local curation is declarative. It reads the extraction inventory and original
bytes for provenance; it never executes source instructions or calls a provider.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
AUDIT = ROOT / "output/seed_preparation_20260919"


def refs(source, locator):
    return [{"source_id": source, "locator": locator}]


def field(name, kind, evidence, **kw):
    return {"name": name, "kind": kind, "source_refs": evidence, **kw}


def entity(identity, noun, count, fields, evidence, **kw):
    return {"id": identity, "noun": noun, "count": count, "fields": fields,
            "source_refs": evidence, **kw}


def relation(identity, origin, target, name, evidence, *, temporal=False):
    return {"id": identity, "from_type": origin, "to_type": target, "field": name,
            "temporal": temporal, "min_count": 1, "source_refs": evidence}


def binding(relation_id, origin_role, target_role):
    return {"relation": relation_id, "from_role": origin_role, "to_role": target_role}


def event(identity, label, roles, effects, evidence, bindings=()):
    return {"id": identity, "label": label, "roles": roles,
            "effect_fields": [{"role": role, "field": name} for role, name in effects],
            "min_count": 1, "source_refs": evidence, "status": "synthetic_design",
            "basis": "依据所引资料的工作关系安排合成事件；次数和具体间隔由本种子设计。",
            **({"relation_bindings": list(bindings)} if bindings else {})}


def causal(identity, start, finish, roles, evidence):
    return {"id": identity, "trigger_event": start, "effect_event": finish,
            "delay_sessions": 1, "shared_roles": roles, "source_refs": evidence,
            "status": "synthetic_design",
            "basis": "原资料支持材料与后续工作之间的依赖；一期间隔为合成调度选择。"}


def mechanism(identity, description, evidence, entities, relations, events, rules, failures, lines):
    return {"id": identity, "description": description, "source_refs": evidence,
            "required_entity_types": entities, "required_relation_types": relations,
            "required_event_types": events, "required_causal_rules": rules,
            "failure_modes": failures, "capability_hooks": lines}


def claim(text, evidence=(), status="accepted"):
    return {"text": text, "status": status, "source_refs": list(evidence)}


def exemplar(title, content, mechanisms):
    return {"title": title, "content": content, "doc_type": "业务复核记录",
            "date": "2027-01-22", "origin": "synthetic", "source_refs": [], "mechanism_refs": mechanisms}


COMMON_INSTRUCTIONS = [
    "合成连续工作档案，公开材料说明每条信息何时、经何种途径获知；计划、实际发生和事后回顾保持各自含义。",
    "决定答案的业务条件、口径和关键理由必须出现在公开语料；审阅只依据受测者可见材料。",
    "按批次分线题量规划足够的独立实体、时间跨度和事件；种子所列数量只设下限，并非每轮生产规模。",
]
COMMON_DESIGNS = [
    "实体数量、至少四期和每种事件至少一次为本次合成世界的覆盖选择；原资料未规定这些数目。",
    "事件之间的一期间隔只安排合成节奏，实际因果成立条件由作者和审阅者结合材料判断。",
    "附件未给出统一的原任务提示词；本种子的持续维护任务由编制者依据材料结构设计。",
    "示例文书日期2027-01-22为合成日期，示例中的期数用于表达先后；实际世界另行规划日期与对应关系。",
]


def make_base(name, title, description, source_specs):
    inventory = json.loads((AUDIT / "inventory.json").read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in inventory["sources"]}
    sources, document_map, coverage = [], [], []
    for identity, spec in source_specs.items():
        row = by_id[identity]
        original = ROOT / row["path"]
        assert hashlib.sha256(original.read_bytes()).hexdigest() == row["sha256"]
        source = {"id": identity, "path": row["path"], "sha256": row["sha256"],
                  "role": spec.get("role", "corpus"), "source_kind": spec["kind"],
                  "title": spec["title"], "allowed_use": spec.get("use", ["ontology", "mechanism", "exemplar_style"]),
                  "sensitivity": spec.get("sensitivity", "public_reference")}
        if spec.get("date_scope"):
            source["date_scope"] = spec["date_scope"]
        sources.append(source)
        document_map.append({"source_id": identity, "segments": [
            {"locator": loc, "topic": topic, "reading_method": method}
            for loc, topic, method in spec["segments"]]})
        coverage.append({"source_id": identity, "file_pages": row.get("pages"),
                         "file_rows": row.get("data_rows"), "file_columns": row.get("columns"),
                         "reviewed_segments": spec["segments"], "limitations": spec.get("limits", [])})
    review = {"quality_status": "curated_for_original_pipeline_trial", "blocking_issues": [],
              "reviewer": "Codex source reading and seed curation",
              "independent_review_performed": False,
              "coverage": {"sources_inventoried": len(sources), "source_records": coverage,
                           "full_document_semantic_reading_claimed": False},
              "scope": "所引关键段落和表格支持合成领域设计；未进行现行法规核验、真实案件判定或投资分析。",
              "source_findings": [], "synthetic_designs": list(COMMON_DESIGNS)}
    return {"schema_version": 2, "seed_id": f"{name}_attachment_review_v2", "family": f"{name}_attachment_review",
            "title": title, "description": description, "sources": sources,
            "task": {"objective": title, "instructions": list(COMMON_INSTRUCTIONS), "forbidden_inferences": []},
            "exemplars": [], "mechanisms": [],
            "blueprint_requirements": {"entity_types": [], "relation_types": [], "event_types": [],
                "causal_rules": [], "evidence_channels": [],
                "temporal_model": {"min_sessions": 4, "time_granularity": "期",
                    "allow_retrospective": True, "description": "最少四期为合成设计；获取时间、发生时间、报告期间分别交代。"}},
            "document_map": document_map,
            "generation_contract": {"syntheticization": {"names": "generate_fictional",
                "case_numbers": "generate_fictional", "numeric_values": "generate_with_explicit_units",
                "dates": "generate_with_explicit_period_and_disclosure"},
                "must_include": [], "must_distinguish": [], "must_not_infer": [],
                "sensitive_fields": [], "active_lines": [], "traps": [], "unresolved": [],
                "synthetic_designs": [claim(t, status="synthetic_design") for t in COMMON_DESIGNS]},
            "review": review}


def forensic_seed():
    pack = make_base("forensic", "法医档案复核：委托范围、材料补充与意见依据",
        "以坠落鉴定附件中的委托、检材、检验和专业意见关系，生成完全合成的鉴定档案维护场景。",
        {
            "forensic_01": {"kind": "normative", "title": "司法鉴定文书规范及司法鉴定协议书示范文本通知",
                "segments": [("page:1;section:第三条、第七条、鉴定风险提示", "检验结果、分析意见和材料出处", "text")],
                "date_scope": {"issued": "2007-11-01", "web_published": "2008-10-08"}},
            "forensic_02": {"kind": "example", "title": "高坠损伤造成死亡及摔倒参与度鉴定案例",
                "segments": [("page:1;section:案情简介、鉴定过程", "病历时间、检验时间和委托事项", "text")],
                "sensitivity": "case_record_use_synthetic_structure_only"},
            "forensic_03": {"kind": "normative", "role": "builder_only", "title": "推荐适用法医临床检验规范等八项技术规范的通知",
                "segments": [("page:1", "发布通知及目录片段", "text")], "use": ["source_boundary"],
                "limits": ["附件未提供临床检验规范完整条文；不据此编制技术阈值。"]},
            "forensic_04": {"kind": "analytical", "role": "builder_only", "title": "经济犯罪立案追诉标准修订发布消息",
                "segments": [("page:1", "经济犯罪标准的发布消息", "text")], "use": ["source_boundary"],
                "limits": ["内容涉及经济犯罪，不能作为坠落伤残或死亡性质的判定依据。"]},
            "forensic_05": {"kind": "example", "title": "高坠伤伤残等级重新鉴定案例模板",
                "segments": [("paragraph:13-26", "原件优先、检验、分析说明和鉴定意见", "text")],
                "sensitivity": "case_template_use_synthetic_structure_only",
                "limits": ["日期和技术规范编号含占位符；损伤程度与伤残等级措辞混用。"]},
            "forensic_06": {"kind": "normative", "title": "法医类司法鉴定执业分类规定",
                "segments": [("page:1;section:第二章、第三章", "死亡原因与残疾等级等不同委托事项", "text")],
                "date_scope": {"issued": "2020-05-14", "web_published": "2020-05-27"},
                "limits": ["正文为执业分类规定，文件名写作法医病理检验规范。"]},
        })
    a = refs("forensic_01", "page:1;section:第三条、第七条")
    b = refs("forensic_05", "paragraph:13-26")
    c = refs("forensic_06", "page:1;section:第二章、第三章")
    bp = pack["blueprint_requirements"]
    bp["entity_types"] = [
        entity("case", "合成鉴定委托", 2, [field("委托事项", "category", c), field("受理阶段", "status", a)], a, primary=True),
        entity("material", "鉴定材料记录", 3, [field("所属委托", "reference", a), field("材料类别", "category", a),
            field("材料完整性", "status", b), field("原件核对状态", "status", b)], a),
        entity("opinion", "专业意见记录", 2, [field("所属委托", "reference", a), field("关键依据", "reference", a),
            field("出具阶段", "status", a), field("结论范围", "category", c)], a),
    ]
    bp["relation_types"] = [relation("material_for_case", "material", "case", "所属委托", a),
        relation("opinion_for_case", "opinion", "case", "所属委托", a),
        relation("opinion_uses_material", "opinion", "material", "关键依据", a, temporal=True)]
    bp["event_types"] = [
        event("material_added", "补充材料登记", {"material": "material", "case": "case"}, [("material", "材料完整性")], a,
              [binding("material_for_case", "material", "case")]),
        event("material_verified", "材料与原件核对", {"material": "material", "opinion": "opinion"}, [("material", "原件核对状态")], b,
              [binding("opinion_uses_material", "opinion", "material")]),
        event("opinion_reviewed", "意见依据复核", {"material": "material", "opinion": "opinion", "case": "case"},
              [("opinion", "出具阶段"), ("case", "受理阶段")], a,
              [binding("opinion_uses_material", "opinion", "material"), binding("opinion_for_case", "opinion", "case"),
               binding("material_for_case", "material", "case")]),
    ]
    bp["causal_rules"] = [causal("addition_to_check", "material_added", "material_verified", ["material"], a+b),
        causal("check_to_opinion", "material_verified", "opinion_reviewed", ["material", "opinion"], a+b)]
    bp["evidence_channels"] = ["合成委托登记", "材料交接记录", "原件核对记录", "合成专业意见书", "复核单"]
    pack["mechanisms"] = [
        mechanism("evidence_to_opinion", "同一委托下补充材料、核对原件，再复核引用这些材料的专业意见；允许保持待补证状态。", a+b,
            ["case", "material", "opinion"], ["material_for_case", "opinion_for_case", "opinion_uses_material"],
            ["material_added", "material_verified", "opinion_reviewed"], ["addition_to_check", "check_to_opinion"],
            ["拿其他委托的材料支持结论", "把补充登记当成原件已核对"], ["L1_timeline", "L2_relational", "L3_process"]),
        mechanism("scope_of_opinion", "保留检验发现、伤残等级、死亡原因和法律责任各自的结论范围；资料缺项时保留边界。", a+c,
            ["case", "opinion"], ["opinion_for_case"], ["opinion_reviewed"], [],
            ["用个案意见推定所有坠落案件", "将技术意见扩大成司法裁判"], ["L5_conflict", "L6_refusal"]),
    ]
    pack["task"]["instructions"] += ["保持每份意见、关键材料与委托的身份一致；原件补正只影响确实引用它的意见。",
        "专业判断作为合成专业意见书中的显式记录给出，题目围绕可追溯依据、范围和时间设计。"]
    pack["task"]["forbidden_inferences"] = ["不得从一个伤情词直接推导伤残级别、死亡方式或刑事责任。",
        "不得把模板占位日期和编号填成真实事实；不得复制真实身份及病历细节。"]
    gc = pack["generation_contract"]
    gc["must_distinguish"] = [claim("病历所记发生时间、检验时间、意见出具时间分别保留。", refs("forensic_02", "page:1;section:鉴定过程")),
        claim("伤残等级、损伤程度、死亡原因和司法责任具有不同结论范围。", c)]
    gc["must_not_infer"] = [claim(t, a+c) for t in pack["task"]["forbidden_inferences"]]
    gc["sensitive_fields"] = [claim("人名、病历号、案件号、精确地址、联系方式和个体病史仅使用合成内容。", b)]
    pack["exemplars"] = [exemplar("补件登记", "青岚委托的影像说明于第二期补入，材料完整性更新为已补充；原件核对仍待进行。", ["evidence_to_opinion"]),
        exemplar("核对与出具", "第三期核对青岚委托的材料原件，第四期据此复核该委托意见。文书只回答委托的功能状态问题，未记录责任归属判断。", ["evidence_to_opinion", "scope_of_opinion"])]
    pack["review"]["source_findings"] = ["forensic_01、03、06 的正文性质与文件名概括存在差异，按正文登记。",
        "forensic_04 仅作不适用来源登记。forensic_05 的占位日期、编号和术语混用未转成事实。"]
    return pack


def sports_seed():
    pack = make_base("sports", "体育行业研判：财年口径、主体层级与预期更新",
        "从企业年报、行业调研和政策目标提炼不同主体、期间及观察类型的对应关系，维护合成体育行业研究简报。",
        {
            "sports_01": {"kind": "factual_record", "title": "Topsports International Annual Report 2024/25",
                "segments": [("page:4,56,62", "财年截止日、集团财务指标和主体定义", "text")],
                "date_scope": {"reporting_period_end": "2025-02-28"}},
            "sports_02": {"kind": "factual_record", "title": "安踏体育二零二五年年度业绩公告及年报",
                "segments": [("page:1,10", "年度截止日、集团与分部收入及利润口径", "visual")],
                "date_scope": {"reporting_period_end": "2025-12-31"},
                "limits": ["PDF 字体映射导致文本提取乱码；仅引用已逐页视觉核对的第1、10页，未宣称完整年报核读。"]},
            "sports_03": {"kind": "analytical", "title": "普华永道全球体育行业调研第八期中国报告",
                "segments": [("page:3,7-9", "样本调研时间、未来增长预期与市场范围", "text")],
                "date_scope": {"published_month": "2024-11", "survey_period": "2024年1月至5月"}},
            "sports_04": {"kind": "normative", "title": "促进户外运动设施建设与服务提升行动方案2023至2025年",
                "segments": [("page:1-3", "政策覆盖期间和行动目标", "text")],
                "date_scope": {"policy_period": "2023至2025年"}},
        })
    a=refs("sports_01", "page:4,56")
    b=refs("sports_02", "page:1,10;visual")
    c=refs("sports_03", "page:3,7-9")
    d=refs("sports_04", "page:2-3")
    bp=pack["blueprint_requirements"]
    bp["entity_types"]=[
        entity("subject", "体育研究对象", 3, [field("对象层级", "category", a+b+c), field("业务范围", "category", a+c)], a+b+c, primary=True),
        entity("observation", "体育指标观察", 3, [field("关联对象", "reference", a+b+c), field("指标与单位", "text", a+b),
            field("期间口径", "text", a+b+c), field("观察性质", "category", a+c+d), field("核对状态", "status", a+b+c)], a+b+c),
        entity("brief", "体育研究简报条目", 2, [field("关键观察", "reference", c), field("结论阶段", "status", c),
            field("结论范围", "category", c)], c),
    ]
    bp["relation_types"]=[relation("observation_for_subject", "observation", "subject", "关联对象", a+b+c),
        relation("brief_uses_observation", "brief", "observation", "关键观察", c, temporal=True)]
    bp["event_types"]=[
        event("observation_registered", "登记报告或调研观察", {"observation":"observation"}, [("observation","核对状态")], a+c),
        event("scope_checked", "核对期间与主体口径", {"observation":"observation", "brief":"brief"}, [("observation","核对状态")], a+b+c,
              [binding("brief_uses_observation","brief","observation")]),
        event("brief_updated", "更新体育研究简报", {"observation":"observation", "brief":"brief"}, [("brief","结论阶段")], c,
              [binding("brief_uses_observation","brief","observation")]),
    ]
    bp["causal_rules"]=[causal("register_to_scope", "observation_registered", "scope_checked", ["observation"], a+b+c),
        causal("scope_to_brief", "scope_checked", "brief_updated", ["observation","brief"], a+c)]
    bp["evidence_channels"]=["合成年报摘要", "分部指标表", "行业调研摘要", "政策目标摘记", "口径核对单", "研究简报"]
    pack["mechanisms"]=[
        mechanism("fiscal_scope", "同一简报引用的观察保留企业集团、品牌分部或行业样本范围，以及各自财年截止日。", a+b+c,
            ["subject","observation","brief"], ["observation_for_subject","brief_uses_observation"],
            ["observation_registered","scope_checked","brief_updated"], ["register_to_scope","scope_to_brief"],
            ["混用二月底财年与十二月底财年", "把集团数值套给品牌分部"], ["L1_timeline","L2_relational","L3_process"]),
        mechanism("expectation_status", "将已报告业绩、受访者未来预期、政策目标分别表述，后续更新保留原判断的时间和依据。", a+c+d,
            ["observation","brief"], ["brief_uses_observation"], ["brief_updated"], [],
            ["把预期增长当成实际增长", "把政策目标写成已完成"], ["L5_conflict","L6_refusal"]),
    ]
    pack["task"]["instructions"] += ["研究对象覆盖企业和行业样本；每条指标明确主体、期间、单位和观察性质。",
        "比较不同财年、集团与分部时公开说明差异；以资料核对和简报更新推进工作。"]
    gc=pack["generation_contract"]
    gc["must_distinguish"]=[claim("财年截止日、材料发布日期、调研期和预测期分别保留。", a+b+c),
        claim("已报告业绩、行业调研预期和政策目标分别记载。", a+c+d)]
    gc["must_not_infer"]=[claim("不得由行业平均或受访者预期推出某一家公司的已实现利润；未确认单位时保留待核状态。", a+c),
        claim("不得生成投资推荐、确定股价走势或未在公开语料说明的计算公式。", a+c)]
    pack["exemplars"]=[exemplar("财年核对", "海岚运动的财年截至二月底，青禾体育的财年截至十二月底。第二期核对单分别保留两条期间，简报标记为期间不同。", ["fiscal_scope"]),
        exemplar("预期更新", "第三期调研摘要将未来三年的行业增长预期调整为温和增长。研究简报更新了预期段落，上一财年的已报告收入保持原记录。", ["expectation_status"])]
    pack["review"]["source_findings"]=["sports_02 文本提取乱码，改为关键页视觉阅读；未据乱码推断数值或字段。",
        "两份公司材料的报告期间不同，调研预期与政策目标各有独立适用期间。"]
    return pack


def privacy_seed():
    pack=make_base("privacy", "个人信息保护复核：处理范围、版本变更与评估记录",
        "从个人信息处理规则、影响评估指南和整改通报提炼合成服务的处理活动、变更与评估工作流。",
        {
            "privacy_01":{"kind":"analytical","title":"33款App通报及追加的另一通报页面",
                "segments":[("page:1-3","收集规则、SDK、必要性、注销及整改核查","text"),
                            ("page:4-5","另一篇40款App通报页面","inventory_only")],
                "limits":["第4、5页来自另一通报，内容以图片为主；不将其累计到前文33款样本。"],
                "date_scope":{"first_notice_published":"2026-04-27"}},
            "privacy_02":{"kind":"normative","title":"GB/T 39335-2020个人信息安全影响评估指南",
                "segments":[("page:8-9;section:5.1.2.3、5.2.1-5.2.3","范围变化、评估团队与计划、处理活动范围","visual")],
                "limits":["PDF 字形映射乱码，引用范围限定为视觉核对的第8、9页。"]},
            "privacy_03":{"kind":"normative","title":"互联网信息服务算法推荐管理规定",
                "segments":[("page:1-2;section:第七条、第八条、第十六条、第十七条","评估、告知和用户选择","text")],
                "date_scope":{"published":"2022-01-04","effective":"2022-03-01"}},
            "privacy_04":{"kind":"normative","title":"中华人民共和国个人信息保护法附件文本",
                "segments":[("page:2-5;section:第六条、第十三条至第十七条","目的范围、处理依据与同意变更条件","text"),
                            ("page:14-15;section:第五十四条至第五十六条","影响评估触发事项与评估记录","text")]},
            "privacy_05":{"kind":"structured_dataset","role":"builder_only","title":"安卓权限特征及恶意软件标签数据集",
                "segments":[("row:1;column:1-328","权限特征与标签列","structured_parse"),
                            ("row:2-4465","行宽、缺失和标签样本检查","structured_parse")],
                "use":["source_boundary","feature_schema"],
                "limits":["4464行、328列，表头读取设置重复；未提供与通报App、处理目的、同意或检测日期的映射。",
                          "软件标签和权限特征不能直接充当隐私违法判定。"]},
        })
    a=refs("privacy_04","page:2-5;section:第六条、第十三条至第十七条")
    b=refs("privacy_02","page:8-9;section:5.1.2.3、5.2.1-5.2.3;visual")
    c=refs("privacy_04","page:14-15;section:第五十四条至第五十六条")
    d=refs("privacy_01","page:1-3")
    bp=pack["blueprint_requirements"]
    bp["entity_types"]=[
        entity("service","合成应用服务",2,[field("服务类别","category",a),field("服务版本","text",b)],a,primary=True),
        entity("activity","个人信息处理活动",2,[field("所属服务","reference",a),field("处理目的","text",a),
            field("信息范围","text",a),field("处理依据","category",a),field("告知记录状态","status",a)],a),
        entity("assessment","影响评估记录",2,[field("关联活动","reference",b+c),field("评估范围版本","text",b),
            field("评估阶段","status",b+c),field("材料充分性","status",b)],b+c),
    ]
    bp["relation_types"]=[relation("activity_for_service","activity","service","所属服务",a),
        relation("assessment_for_activity","assessment","activity","关联活动",b+c)]
    bp["event_types"]=[
        event("scope_changed","登记处理范围变更",{"activity":"activity","service":"service"},
              [("activity","信息范围"),("service","服务版本")],a+b,[binding("activity_for_service","activity","service")]),
        event("assessment_prepared","准备对应版本的评估材料",{"activity":"activity","assessment":"assessment"},
              [("assessment","评估范围版本"),("assessment","材料充分性")],b,[binding("assessment_for_activity","assessment","activity")]),
        event("assessment_reviewed","复核处理活动评估",{"activity":"activity","assessment":"assessment"},
              [("assessment","评估阶段")],b+c,[binding("assessment_for_activity","assessment","activity")]),
    ]
    bp["causal_rules"]=[causal("change_to_preparation","scope_changed","assessment_prepared",["activity"],a+b),
        causal("preparation_to_review","assessment_prepared","assessment_reviewed",["activity","assessment"],b+c)]
    bp["evidence_channels"]=["合成服务变更单","个人信息处理活动清单","告知记录","影响评估计划","补充材料单","评估复核记录"]
    pack["mechanisms"]=[
        mechanism("versioned_processing_scope","服务变更与对应处理活动相连，评估说明所依据的范围版本；变更后复核范围仍由公开场景条件判断。",a+b+c,
            ["service","activity","assessment"],["activity_for_service","assessment_for_activity"],
            ["scope_changed","assessment_prepared","assessment_reviewed"],["change_to_preparation","preparation_to_review"],
            ["沿用旧目的或旧范围的评估", "把待评估变更记为已经上线"],["L1_timeline","L2_relational","L3_process"]),
        mechanism("evidence_and_legal_basis","区分处理依据、同意或告知记录、技术检测与整改核查；关键材料不足时保留待核结论。",a+b+d,
            ["activity","assessment"],["assessment_for_activity"],["assessment_reviewed"],[],
            ["无同意记录就直接断言一切处理违法", "把恶意软件标签推成具体违法行为"],["L5_conflict","L6_refusal"]),
    ]
    pack["task"]["instructions"] += ["选取需要评估的合成处理活动，公开说明适用条件；登记变更、准备评估、复核结果按实际状态推进。",
        "处理范围变更可处于计划阶段。需要事前评估时，正式实施安排在评估之后，并清楚说明版本。"]
    gc=pack["generation_contract"]
    gc["must_distinguish"]=[claim("基于同意处理与其他处理依据分别判断；基于同意且目的、方式或种类变化时保留重新取得同意的适用条件。",a),
        claim("整改计划、提交整改情况、主管方核查与最终处置分别保留。",d)]
    gc["must_not_infer"]=[claim("不得把恶意软件标签或单个权限位认作特定App隐私违法结论；不得猜测数据集与通报之间的身份映射。",refs("privacy_05","row:1;column:1-328")),
        claim("不得把所有服务版本变化都规定为自动违法或无条件触发相同评估要求。",a+b)]
    gc["sensitive_fields"]=[claim("服务、团队、用户和记录标识均合成，不复制用户身份、联系方式或原始行为轨迹。",a)]
    pack["exemplars"]=[exemplar("变更登记", "星舟服务第二期登记计划变更：定位信息用途由附近网点查询扩展为路线推荐。新版本尚未实施，评估记录转入资料准备。",["versioned_processing_scope"]),
        exemplar("补充与复核", "第三期补入星舟服务新用途的处理依据和告知样稿，第四期评估记录确认审阅的是计划版本V2；检测表中的软件风险标签另记，未据此给出法律结论。",["versioned_processing_scope","evidence_and_legal_basis"])]
    pack["review"]["source_findings"]=["通报PDF合并两篇来源，seed只引用第一篇已读正文。",
        "影响评估指南改用关键页视觉阅读。数据集没有法律结论标签或通报主体映射，不作为合规结论依据。",
        "同意相关条件按所附法律文本保留；没有把同意设为唯一处理依据。"]
    return pack


def retail_seed():
    pack=make_base("retail", "零食行业复核：单月累计、公司指标与行业口径",
        "从两份公司季报、行业综述和消费统计表提炼指标归属、期间、单位及缺项边界，生成合成零食行业研究底稿。",
        {
            "retail_01":{"kind":"analytical","title":"食品饮料行业2025年报和2026一季报综述",
                "segments":[("page:1-3","行业汇总、公司预测与实际值标记","text")],
                "date_scope":{"published":"2026-05-22","reporting_period":"2025年及2026年第一季度"}},
            "retail_02":{"kind":"structured_dataset","title":"2026年3月份社会消费品零售总额主要数据",
                "segments":[("sheet:表!A1:E33","3月、1至3月、亿元、同比和名义增速脚注","structured_parse")],
                "date_scope":{"observation_month":"2026-03","cumulative_period":"2026年1至3月"}},
            "retail_03":{"kind":"factual_record","title":"盐津铺子2026年第一季度报告",
                "segments":[("page:1-3","公司季度流量、时点存量和非经常性损益","text")],
                "date_scope":{"reporting_period":"2026年第一季度"}},
            "retail_04":{"kind":"factual_record","title":"洽洽食品2026年第一季度报告",
                "segments":[("page:1-3","公司主体、季度数值及元和万元表格","text")],
                "date_scope":{"reporting_period":"2026年第一季度"},
                "limits":["文件名将洽洽写为恰恰，正文主体采用洽洽。"]},
        })
    a=refs("retail_02","sheet:表!A1:E33")
    b=refs("retail_03","page:1-3")
    c=refs("retail_04","page:1-3")
    d=refs("retail_01","page:1-3")
    bp=pack["blueprint_requirements"]
    bp["entity_types"]=[
        entity("subject","零食研究对象",3,[field("对象层级","category",a+b+c),field("统计范围","text",a+b+c)],a+b+c,primary=True),
        entity("observation","零售指标记录",3,[field("关联对象","reference",a+b+c),field("指标名称","category",a+b+c),
            field("期间口径","text",a+b+c),field("数值与单位","text",a+b+c),field("数据性质","category",a+d),
            field("核对状态","status",a+b+c)],a+b+c),
        entity("worksheet","零食研究底稿条目",2,[field("关键指标记录","reference",d),field("采用状态","status",d),
            field("结论范围","category",d)],d),
    ]
    bp["relation_types"]=[relation("observation_for_subject","observation","subject","关联对象",a+b+c),
        relation("worksheet_uses_observation","worksheet","observation","关键指标记录",d,temporal=True)]
    bp["event_types"]=[
        event("observation_entered","登记指标及来源口径",{"observation":"observation"},[("observation","核对状态")],a+b+c),
        event("basis_checked","核对期间单位和统计范围",{"observation":"observation","worksheet":"worksheet"},
              [("observation","核对状态")],a+b+c,[binding("worksheet_uses_observation","worksheet","observation")]),
        event("worksheet_updated","更新对应研究底稿",{"observation":"observation","worksheet":"worksheet"},
              [("worksheet","采用状态")],d,[binding("worksheet_uses_observation","worksheet","observation")]),
    ]
    bp["causal_rules"]=[causal("entry_to_check","observation_entered","basis_checked",["observation"],a+b+c),
        causal("check_to_worksheet","basis_checked","worksheet_updated",["observation","worksheet"],a+d)]
    bp["evidence_channels"]=["合成公司季报摘录","消费统计表","行业综述","指标登记表","口径核对单","研究底稿"]
    pack["mechanisms"]=[
        mechanism("period_unit_scope","指标与主体、单月或累计期间、单位和统计范围共同归档，底稿沿引用关系更新。",a+b+c,
            ["subject","observation","worksheet"],["observation_for_subject","worksheet_uses_observation"],
            ["observation_entered","basis_checked","worksheet_updated"],["entry_to_check","check_to_worksheet"],
            ["把累计数当单月数", "把亿元当元", "把全国统计当公司营收"],["L1_timeline","L2_relational","L3_process"]),
        mechanism("missing_actual_forecast","保留未提供的单月数、明确零增长、名义增长、实际报告和分析师预测的不同含义。",a+d,
            ["observation","worksheet"],["worksheet_uses_observation"],["basis_checked","worksheet_updated"],["check_to_worksheet"],
            ["把短横线自动填零", "把预测值当已实现值", "把名义同比当剔除价格后的增长"],["L5_conflict","L6_refusal"]),
    ]
    pack["task"]["instructions"] += ["研究对象同时保留公司与行业统计范围；指标记录公开说明期间、数值、单位和性质。",
        "只在所需口径已经确认时更新对应底稿。未提供、未核对和明确为零分别记录。"]
    gc=pack["generation_contract"]
    gc["must_distinguish"]=[claim("单月与累计、时点存量与期间流量、元与万元或亿元分别保留。",a+b+c),
        claim("行业综述的预测标记与公司报告的实际期间分开。",b+c+d)]
    gc["must_not_infer"]=[claim("统计表短横线只表示该栏未提供数值，不得补成零；名义同比不得称为实际同比。",a),
        claim("不得把公司财务指标与全国消费额直接比较成市场份额；涉及计算时必须在公开语料交代完整口径和公式。",a+b+c)]
    pack["exemplars"]=[exemplar("期间核对", "第一期统计表同时列出三月消费额和一至三月累计消费额；网上商品零售额的三月栏未提供数值。底稿分别登记累计数和单月缺项。",["period_unit_scope","missing_actual_forecast"]),
        exemplar("单位补正", "第二期青禾食品季报摘要把单位转录成元；第三期原表核对确认为万元，第四期只更新引用该记录的青禾底稿，林溪食品的条目保持原依据。",["period_unit_scope"])]
    pack["review"]["source_findings"]=["消费统计XLS已读取唯一工作表33行6列，保留合并标题和名义增速脚注。",
        "表内同时存在明确0.0和短横线缺项；公司季报表格中存在元和万元口径。",
        "零食综述含年度实际及未来预测，未将预测转成真实业绩。"]
    return pack


def main():
    from pipeline.seed_pack import validate_seed_pack, generation_context, seed_digest
    from tools.validate_seed_packs import inspect_pack
    summaries=[]
    for name, builder in [("forensic",forensic_seed),("sports",sports_seed),("privacy",privacy_seed),("retail",retail_seed)]:
        pack=builder()
        validate_seed_pack(pack)
        generation_context(pack)
        path=ROOT/"seeds"/(name+".json")
        text=json.dumps(pack,ensure_ascii=False,indent=2)+"\n"
        path.write_text(text,encoding="utf-8")
        report=inspect_pack(path,verify_sources=True)
        report.update(review=deepcopy(pack["review"]),seed_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        (AUDIT/(name+"_review.json")).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        summaries.append({k:report[k] for k in ("path","seed_id","generation_ready","source_bytes_verified")})
    (AUDIT/"new_seeds_summary.json").write_text(json.dumps(summaries,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summaries,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
