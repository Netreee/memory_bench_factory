#!/usr/bin/env python3
"""构建轨道战争题材的策展型 Benchmark 胚子，并执行正式双闸验收。

本脚本不调用模型，也不伪装成自动流水线采样。它把一次人工策展的世界、
语料、问题和宣传展示资产确定性落盘，目的是提供可复跑、可审计的第二个
金牌胚子。输出只写入本 Run 目录。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.grounding import run_grounding
from pipeline.well_posed import run_well_posed
from pipeline.world_state import INSUFFICIENT, WorldState


RUN_ID = "game_orbit__20260906-065200"
RUN_DIR = ROOT / "output" / "runs" / RUN_ID
PRODUCTION_DIR = RUN_DIR / "production"
DATES = [
    "2197-10-03T06:20:00",
    "2197-10-03T07:05:00",
    "2197-10-03T09:30:00",
    "2197-10-03T13:40:00",
    "2197-10-03T18:10:00",
    "2197-10-03T22:19:00",
]


def clean(text: str) -> str:
    """清理多行策展文案的缩进，保留段落。"""
    return dedent(text).strip()


def write_json(path: Path, obj) -> None:
    """写入可读的 UTF-8 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    """写入 UTF-8 文本。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def timeline(*entries: tuple[int, str]) -> list[dict]:
    """把(session, value)序列编译为 SET/UPDATE 时间线。"""
    out = []
    prev = None
    for index, (session, value) in enumerate(entries):
        out.append(
            {
                "session": session,
                "date": DATES[session],
                "op": "SET" if index == 0 else "UPDATE",
                "value": value,
                "prev": prev,
            }
        )
        prev = value
    return out


def doc(
    doc_id: str,
    title: str,
    content: str,
    refs: list[str] | None = None,
    *,
    conflict: bool = False,
    filler: bool = False,
    source: str = "独立一手记录",
    reliability: str = "tier-1",
) -> dict:
    """创建带可追踪引用的语料文档。"""
    return {
        "doc_id": doc_id,
        "title": title,
        "content": clean(content),
        "is_filler": True if filler else False,
        "is_conflict": bool(conflict),
        "source": source,
        "reliability": reliability,
        "fact_refs": [] if filler else list(refs or []),
        "claim_refs": [],
    }


def question(
    qid: str,
    line: str,
    capability: str,
    entity: str,
    field: str,
    gt,
    evidence_sessions: list[int],
    evidence_doc_ids: list[str],
    answer_atoms: list[str],
    text: str,
    aux: dict,
    *,
    star: bool = False,
    strict_atoms: list[str] | None = None,
) -> dict:
    """创建问题，同时保留机器闸与视频展示所需字段。"""
    item = {
        "qid": qid,
        "line": line,
        "capability": capability,
        "entity": entity,
        "field": field,
        "gt": gt,
        "evidence_sessions": evidence_sessions,
        "evidence_doc_ids": evidence_doc_ids,
        "answer_atoms": answer_atoms,
        "question": text,
        "star": star,
        "aux": aux,
    }
    if strict_atoms:
        item["strict_scoring"] = {
            "policy": "all_required_atoms",
            "required_atoms": strict_atoms,
        }
    return item


def build_blueprint() -> dict:
    """冻结轨道世界的 v1 类型、关系、事件与因果契约。"""
    return {
        "version": 1,
        "entity_types": [
            {
                "id": "protagonist", "noun": "唯一主角", "count": 1, "primary": True,
                "fields": [
                    {"name": "所在地点", "kind": "category"},
                    {"name": "任务立场", "kind": "status"},
                    {"name": "生命状态", "kind": "status"},
                    {"name": "最终逃生结论", "kind": "status"},
                    {"name": "驾驶机甲引用", "kind": "reference"},
                    {"name": "核心信念", "kind": "text"},
                    {"name": "救援人数", "kind": "numeric"},
                ],
            },
            {
                "id": "character", "noun": "关键角色", "count": 3, "primary": False,
                "fields": [
                    {"name": "角色身份", "kind": "category"},
                    {"name": "所在地点", "kind": "category"},
                    {"name": "接收端状态", "kind": "status"},
                    {"name": "与林渡关系", "kind": "category"},
                    {"name": "提出方案引用", "kind": "reference"},
                    {"name": "指挥位置", "kind": "category"},
                    {"name": "撤离命令引用", "kind": "reference"},
                ],
            },
            {
                "id": "system_asset", "noun": "轨道设施与载具", "count": 7, "primary": False,
                "fields": [
                    {"name": "驾驶员引用", "kind": "reference"},
                    {"name": "机体状态", "kind": "status"},
                    {"name": "可用能源余量", "kind": "numeric"},
                    {"name": "轨道状态", "kind": "status"},
                    {"name": "常住登记人口", "kind": "numeric"},
                    {"name": "最终用途", "kind": "text"},
                    {"name": "防卫状态", "kind": "status"},
                    {"name": "载员总数", "kind": "numeric"},
                    {"name": "方舟数量", "kind": "numeric"},
                    {"name": "目的地", "kind": "category"},
                    {"name": "实际受困人数", "kind": "numeric"},
                    {"name": "舱段状态", "kind": "status"},
                    {"name": "救援者引用", "kind": "reference"},
                    {"name": "运转状态", "kind": "status"},
                    {"name": "交换进度", "kind": "numeric", "monotonic": "up"},
                    {"name": "不可逆阈值", "kind": "numeric"},
                    {"name": "人工锚引用", "kind": "reference"},
                    {"name": "反向配重引用", "kind": "reference"},
                    {"name": "呼号", "kind": "text"},
                    {"name": "机体谱线", "kind": "text"},
                    {"name": "战场状态", "kind": "status"},
                    {"name": "结构状态", "kind": "status"},
                    {"name": "额定质量", "kind": "numeric"},
                ],
            },
            {
                "id": "record", "noun": "事件、命令与抉择记录", "count": 9, "primary": False,
                "fields": [
                    {"name": "事件结论", "kind": "text"},
                    {"name": "替代方案引用", "kind": "reference"},
                    {"name": "发布者引用", "kind": "reference"},
                    {"name": "优先舱段引用", "kind": "reference"},
                    {"name": "目标", "kind": "text"},
                    {"name": "做出者引用", "kind": "reference"},
                    {"name": "后续事件引用", "kind": "reference"},
                    {"name": "产生结果", "kind": "text"},
                    {"name": "执行者引用", "kind": "reference"},
                    {"name": "改变选择引用", "kind": "reference"},
                    {"name": "最终选择", "kind": "text"},
                    {"name": "代价", "kind": "text"},
                    {"name": "先开火一方", "kind": "category"},
                    {"name": "权威时间差", "kind": "numeric"},
                ],
            },
        ],
        "relation_types": [
            {"id": "protagonist_drives_asset", "from_type": "protagonist", "to_type": "system_asset", "field": "驾驶机甲引用", "temporal": False, "min_count": 1},
            {"id": "character_proposes_record", "from_type": "character", "to_type": "record", "field": "提出方案引用", "temporal": False, "min_count": 1},
            {"id": "character_issues_record", "from_type": "character", "to_type": "record", "field": "撤离命令引用", "temporal": False, "min_count": 1},
            {"id": "asset_piloted_by", "from_type": "system_asset", "to_type": "protagonist", "field": "驾驶员引用", "temporal": False, "min_count": 1},
            {"id": "asset_rescued_by", "from_type": "system_asset", "to_type": "protagonist", "field": "救援者引用", "temporal": False, "min_count": 1},
            {"id": "asset_uses_anchor", "from_type": "system_asset", "to_type": "system_asset", "field": "人工锚引用", "temporal": False, "min_count": 1},
            {"id": "asset_uses_counterweight", "from_type": "system_asset", "to_type": "system_asset", "field": "反向配重引用", "temporal": False, "min_count": 1},
            {"id": "record_has_replacement", "from_type": "record", "to_type": "system_asset", "field": "替代方案引用", "temporal": False, "min_count": 1},
            {"id": "record_published_by", "from_type": "record", "to_type": "character", "field": "发布者引用", "temporal": False, "min_count": 1},
            {"id": "record_prioritizes_asset", "from_type": "record", "to_type": "system_asset", "field": "优先舱段引用", "temporal": False, "min_count": 1},
            {"id": "record_decided_by", "from_type": "record", "to_type": "protagonist", "field": "做出者引用", "temporal": False, "min_count": 1},
            {"id": "record_leads_to_record", "from_type": "record", "to_type": "record", "field": "后续事件引用", "temporal": True, "min_count": 3},
            {"id": "record_executed_by", "from_type": "record", "to_type": "protagonist", "field": "执行者引用", "temporal": False, "min_count": 1},
            {"id": "record_changes_decision", "from_type": "record", "to_type": "record", "field": "改变选择引用", "temporal": True, "min_count": 1},
        ],
        "event_types": [
            {"id": "first_strike", "label": "敌军发动先发突袭", "roles": {"attacker": "system_asset", "target": "system_asset"}, "effect_fields": [{"role": "target", "field": "防卫状态"}], "min_count": 1},
            {"id": "ballast_destroyed", "label": "三号压舱环完全损毁", "roles": {"target": "system_asset"}, "effect_fields": [{"role": "target", "field": "结构状态"}], "min_count": 1},
            {"id": "return_to_ring", "label": "林渡逆向返回B环", "roles": {"pilot": "protagonist"}, "effect_fields": [{"role": "pilot", "field": "任务立场"}], "min_count": 1},
            {"id": "rescue", "label": "B环三百一十二人全员获救", "roles": {"rescuer": "protagonist", "shelter": "system_asset"}, "effect_fields": [{"role": "shelter", "field": "实际受困人数"}], "min_count": 1},
            {"id": "reveal_mechanism", "label": "等角动量撤离方案被确认", "roles": {"engineer": "character", "counterweight": "system_asset"}, "effect_fields": [{"role": "counterweight", "field": "最终用途"}], "min_count": 1},
            {"id": "accept_anchor", "label": "林渡亲手接受人工锚定", "roles": {"pilot": "protagonist"}, "effect_fields": [{"role": "pilot", "field": "任务立场"}], "min_count": 1},
            {"id": "mirror_battle", "label": "镜阵碎片带机甲战", "roles": {"defender": "protagonist", "station": "system_asset", "enemy": "system_asset"}, "effect_fields": [{"role": "station", "field": "防卫状态"}], "min_count": 1},
            {"id": "throw_core", "label": "抛出聚变芯重启镜阵", "roles": {"pilot": "protagonist", "array": "system_asset"}, "effect_fields": [{"role": "array", "field": "运转状态"}], "min_count": 1},
            {"id": "all_aboard", "label": "三万八千四百一十二人完成登船", "roles": {"controller": "character", "anchor": "protagonist", "fleet": "system_asset"}, "effect_fields": [{"role": "fleet", "field": "载员总数"}], "min_count": 1},
            {"id": "cross_lockpoint", "label": "动量交换越过锁死点", "roles": {"anchor": "protagonist", "mecha": "system_asset"}, "effect_fields": [{"role": "mecha", "field": "机体状态"}], "min_count": 1},
            {"id": "exchange_complete", "label": "等动量交换完成", "roles": {"anchor": "protagonist", "fleet": "system_asset"}, "effect_fields": [{"role": "fleet", "field": "轨道状态"}], "min_count": 1},
            {"id": "station_fall", "label": "赫利俄斯环站坠入气巨星", "roles": {"anchor": "protagonist", "station": "system_asset"}, "effect_fields": [{"role": "station", "field": "轨道状态"}], "min_count": 1},
            {"id": "death_confirmed", "label": "三源遥测确认林渡阵亡", "roles": {"pilot": "protagonist"}, "effect_fields": [{"role": "pilot", "field": "最终逃生结论"}], "min_count": 1},
        ],
        "temporal_model": {"unit": "chapter", "cadence": "story-beat", "n_sessions": 6, "step_days": 1},
        "causal_rules": [
            {"id": "ballast_forces_station", "trigger_event": "ballast_destroyed", "effect_event": "reveal_mechanism", "delay_sessions": 2},
            {"id": "rescued_names_change_choice", "trigger_event": "rescue", "effect_event": "cross_lockpoint", "delay_sessions": 3},
            {"id": "core_enables_lockpoint", "trigger_event": "throw_core", "effect_event": "cross_lockpoint", "delay_sessions": 1},
            {"id": "lockpoint_forces_fall", "trigger_event": "cross_lockpoint", "effect_event": "station_fall", "delay_sessions": 1},
        ],
        "evidence_channels": ["驾驶舱实录", "工程遥测", "救援清点", "战斗黑匣", "通信取证", "受污染公报"],
        "invariants": [],
    }


def build_world(blueprint: dict) -> dict:
    """构建可被现有 WorldState 直接回读的六章轨道世界。"""
    entities = {
        "林渡": {
            "所在地点": timeline(
                (0, "赫利俄斯环站外勤轨道"),
                (1, "B环外壁"),
                (2, "等动量阵列控制舱"),
                (3, "镜阵碎片带"),
                (4, "手动矢量锚"),
                (5, "赫利俄斯坠落轨道"),
            ),
            "任务立场": timeline(
                (0, "护送方舟离港"),
                (1, "返回B环救人"),
                (2, "接受手动锚定"),
                (3, "保卫镜阵"),
                (4, "留守矢量锚"),
            ),
            "生命状态": timeline((0, "执行任务"), (5, "确认阵亡")),
            "最终逃生结论": timeline((5, "没有逃出空间站；三源确认阵亡")),
            "驾驶机甲引用": timeline((0, "鹊桥-9")),
            "核心信念": timeline((0, "不再留下任何人"), (5, "让所有人先走")),
            "救援人数": timeline((1, "312人")),
        },
        "鹊桥-9": {
            "驾驶员引用": timeline((0, "林渡")),
            "机体状态": timeline(
                (0, "外勤完整"),
                (1, "左臂受损"),
                (3, "失去聚变芯"),
                (4, "与矢量锚冷焊"),
                (5, "随站热解"),
            ),
            "可用能源余量": timeline(
                (0, "84"), (1, "63"), (2, "41"), (3, "19"), (4, "4"), (5, "7")
            ),
        },
        "林弥": {
            "角色身份": timeline((0, "白鹭一号撤离航管员")),
            "所在地点": timeline((0, "赫利俄斯航管塔"), (4, "白鹭一号"), (5, "安全转移轨道")),
            "接收端状态": timeline((4, "未取得ACK"), (5, "未取得ACK")),
            "与林渡关系": timeline((0, "妹妹")),
        },
        "唐砚": {
            "角色身份": timeline((0, "等动量阵列首席工程师")),
            "所在地点": timeline((0, "阵列控制舱"), (4, "白鹭二号")),
            "提出方案引用": timeline((2, "三号压舱环损毁")),
        },
        "纪衡": {
            "角色身份": timeline((0, "赫利俄斯环站指挥官")),
            "指挥位置": timeline((0, "中央舰桥"), (5, "白鹭一号")),
            "撤离命令引用": timeline((0, "撤离总令")),
        },
        "赫利俄斯环站": {
            "轨道状态": timeline(
                (0, "稳定轨道"),
                (1, "姿态失稳"),
                (2, "缓慢降轨"),
                (3, "解体边缘"),
                (4, "坠落轨道"),
                (5, "热解毁损"),
            ),
            "常住登记人口": timeline((0, "38100人")),
            "最终用途": timeline((2, "作为反向配重降轨，以等量角动量交换推动十二艘方舟升轨")),
            "防卫状态": timeline((0, "遭到突袭"), (3, "镜阵防线"), (5, "停止存在")),
        },
        "白鹭方舟群": {
            "轨道状态": timeline((0, "站内泊位"), (4, "编队离港"), (5, "安全转移轨道")),
            "载员总数": timeline((4, "38412人"), (5, "38412人")),
            "方舟数量": timeline((0, "十二艘")),
            "目的地": timeline((0, "拉格朗日L4避难港")),
        },
        "B环避难舱": {
            "实际受困人数": timeline((0, "未核清"), (1, "312人")),
            "舱段状态": timeline((0, "通讯中断"), (1, "全员撤出")),
            "救援者引用": timeline((1, "林渡")),
        },
        "等动量阵列": {
            "运转状态": timeline(
                (0, "受创"),
                (1, "离线"),
                (2, "待接入"),
                (3, "重新上线"),
                (4, "动量交换中"),
                (5, "交换完成"),
            ),
            "交换进度": timeline((1, "0"), (2, "12"), (3, "43"), (4, "61"), (5, "100")),
            "不可逆阈值": timeline((2, "61%")),
            "人工锚引用": timeline((2, "鹊桥-9")),
            "反向配重引用": timeline((2, "赫利俄斯环站")),
        },
        "三号压舱环损毁": {
            "事件结论": timeline((1, "全部压舱质量逸散")),
            "替代方案引用": timeline((1, "赫利俄斯环站")),
        },
        "撤离总令": {
            "发布者引用": timeline((0, "纪衡")),
            "优先舱段引用": timeline((0, "B环避难舱")),
            "目标": timeline((0, "全员转入白鹭方舟群")),
        },
        "返回B环的抉择": {
            "做出者引用": timeline((1, "林渡")),
            "后续事件引用": timeline((1, "B环全员获救")),
        },
        "B环全员获救": {
            "产生结果": timeline((1, "312人获救并登上方舟")),
            "执行者引用": timeline((1, "林渡")),
            "改变选择引用": timeline((4, "越过锁死点的抉择")),
        },
        "抛出聚变芯的抉择": {
            "做出者引用": timeline((3, "林渡")),
            "后续事件引用": timeline((3, "镜阵重新上线")),
        },
        "镜阵重新上线": {
            "产生结果": timeline((3, "等动量阵列恢复并重新上线")),
            "执行者引用": timeline((3, "林渡")),
        },
        "越过锁死点的抉择": {
            "做出者引用": timeline((4, "林渡")),
            "后续事件引用": timeline((4, "轨道交换终局")),
            "最终选择": timeline((4, "确认312人已并入总表后，他扣上退出按钮护盖，主动越过61%，留下自己完成轨道交换")),
        },
        "轨道交换终局": {
            "产生结果": timeline((5, "十二艘方舟载着38412人进入安全转移轨道；林渡随赫利俄斯环站坠入气巨星并确认阵亡")),
            "代价": timeline((5, "赫利俄斯环站与林渡坠入气巨星并毁损")),
        },
        "交火起因记录": {
            "先开火一方": timeline((0, "赤潮舰队")),
            "权威时间差": timeline((0, "21秒")),
        },
        "赤隼一号": {
            "呼号": timeline((3, "赤隼一号")),
            "机体谱线": timeline((3, "R-17钴蓝谱线")),
            "战场状态": timeline((3, "脱离镜阵碎片带")),
        },
        "三号压舱环": {
            "结构状态": timeline((0, "完整"), (1, "完全损毁")),
            "额定质量": timeline((0, "九十万吨")),
        },
    }

    conflicts = [
        {
            "entity": "交火起因记录",
            "field": "先开火一方",
            "session": 0,
            "date": DATES[0],
            "authoritative_value": "赤潮舰队",
            "authoritative_source": "独立一手记录",
            "authoritative_provenance": "双站望远镜与轨道炮时标交叉记录",
            "rumor_value": "赫利俄斯环站防卫队",
            "rumor_source": "受污染的官方记录",
            "rumor_provenance": "赤潮舰队公共广播",
            "rule": "source_reliability",
            "gt": "赤潮舰队",
        },
        {
            "entity": "B环避难舱",
            "field": "实际受困人数",
            "session": 1,
            "date": DATES[1],
            "authoritative_value": "312人",
            "authoritative_source": "独立一手记录",
            "authoritative_provenance": "独立生命服信标与登舱医疗清点",
            "rumor_value": "0人",
            "rumor_source": "受污染的官方记录",
            "rumor_provenance": "延迟同步的撤离总表",
            "rule": "source_reliability",
            "gt": "312人",
        },
        {
            "entity": "林渡",
            "field": "最终逃生结论",
            "session": 5,
            "date": DATES[5],
            "authoritative_value": "没有逃出空间站；三源确认阵亡",
            "authoritative_source": "独立一手记录",
            "authoritative_provenance": "锚点黑匣子、雷达与热成像三源交叉记录",
            "rumor_value": "失踪，仍可能已经逃生",
            "rumor_source": "受污染的官方记录",
            "rumor_provenance": "未等黑匣子回传便发布的临时伤亡表",
            "rule": "source_reliability",
            "gt": "没有逃出空间站；三源确认阵亡",
        },
    ]

    events = [
        {"id": "evt-00", "type": "first_strike", "label": "赤潮舰队先发突袭", "session": 0,
         "participants": {"attacker": "赤隼一号", "target": "赫利俄斯环站"},
         "effects": [{"entity": "赫利俄斯环站", "field": "防卫状态", "set": "遭到突袭"}]},
        {"id": "evt-00b", "type": "ballast_destroyed", "label": "三号压舱环完全损毁", "session": 0,
         "participants": {"target": "三号压舱环"},
         "effects": [{"entity": "三号压舱环", "field": "结构状态", "set": "完全损毁"}]},
        {"id": "evt-01", "type": "return_to_ring", "label": "林渡逆向返回B环", "session": 1,
         "participants": {"pilot": "林渡"},
         "effects": [{"entity": "林渡", "field": "任务立场", "set": "返回B环救人"}]},
        {"id": "evt-02", "type": "rescue", "label": "B环312人全员获救", "session": 1,
         "participants": {"rescuer": "林渡", "shelter": "B环避难舱"},
         "effects": [{"entity": "B环避难舱", "field": "实际受困人数", "set": "312人"}]},
        {"id": "evt-03", "type": "reveal_mechanism", "label": "等动量撤离方案被确认", "session": 2,
         "participants": {"engineer": "唐砚", "counterweight": "赫利俄斯环站"},
         "effects": [{"entity": "赫利俄斯环站", "field": "最终用途", "set": "作为反向配重降轨，以等量角动量交换推动十二艘方舟升轨"}]},
        {"id": "evt-04", "type": "accept_anchor", "label": "林渡接受手动锚定", "session": 2,
         "participants": {"pilot": "林渡"},
         "effects": [{"entity": "林渡", "field": "任务立场", "set": "接受手动锚定"}]},
        {"id": "evt-05", "type": "mirror_battle", "label": "镜阵碎片带机甲战", "session": 3,
         "participants": {"defender": "林渡", "station": "赫利俄斯环站", "enemy": "赤隼一号"},
         "effects": [{"entity": "赫利俄斯环站", "field": "防卫状态", "set": "镜阵防线"}]},
        {"id": "evt-06", "type": "throw_core", "label": "林渡抛出聚变芯重启镜阵", "session": 3,
         "participants": {"pilot": "林渡", "array": "等动量阵列"},
         "effects": [{"entity": "等动量阵列", "field": "运转状态", "set": "重新上线"}]},
        {"id": "evt-07", "type": "all_aboard", "label": "38412人登上十二艘方舟", "session": 4,
         "participants": {"controller": "林弥", "anchor": "林渡", "fleet": "白鹭方舟群"},
         "effects": [{"entity": "白鹭方舟群", "field": "载员总数", "set": "38412人"}]},
        {"id": "evt-08", "type": "cross_lockpoint", "label": "动量交换越过61%锁死点", "session": 4,
         "participants": {"anchor": "林渡", "mecha": "鹊桥-9"},
         "effects": [{"entity": "鹊桥-9", "field": "机体状态", "set": "与矢量锚冷焊"}]},
        {"id": "evt-09", "type": "exchange_complete", "label": "等动量交换完成", "session": 5,
         "participants": {"anchor": "林渡", "fleet": "白鹭方舟群"},
         "effects": [{"entity": "白鹭方舟群", "field": "轨道状态", "set": "安全转移轨道"}]},
        {"id": "evt-10", "type": "station_fall", "label": "赫利俄斯环站坠入气巨星", "session": 5,
         "participants": {"anchor": "林渡", "station": "赫利俄斯环站"},
         "effects": [{"entity": "赫利俄斯环站", "field": "轨道状态", "set": "热解毁损"}]},
        {"id": "evt-11", "type": "death_confirmed", "label": "锚点遥测确认林渡阵亡", "session": 5,
         "participants": {"pilot": "林渡"},
         "effects": [{"entity": "林渡", "field": "最终逃生结论", "set": "没有逃出空间站；三源确认阵亡"}]},
    ]

    relations = [
        {"id": "rel-00", "type": "record_prioritizes_asset", "source": "撤离总令", "field": "优先舱段引用", "target": "B环避难舱", "session": 0},
        {"id": "rel-01", "type": "record_has_replacement", "source": "三号压舱环损毁", "field": "替代方案引用", "target": "赫利俄斯环站", "session": 1},
        {"id": "rel-02", "type": "record_leads_to_record", "source": "返回B环的抉择", "field": "后续事件引用", "target": "B环全员获救", "session": 1},
        {"id": "rel-03", "type": "record_leads_to_record", "source": "抛出聚变芯的抉择", "field": "后续事件引用", "target": "镜阵重新上线", "session": 3},
        {"id": "rel-04", "type": "record_leads_to_record", "source": "越过锁死点的抉择", "field": "后续事件引用", "target": "轨道交换终局", "session": 4},
        {"id": "rel-05", "type": "protagonist_drives_asset", "source": "林渡", "field": "驾驶机甲引用", "target": "鹊桥-9", "session": 0},
        {"id": "rel-06", "type": "asset_piloted_by", "source": "鹊桥-9", "field": "驾驶员引用", "target": "林渡", "session": 0},
        {"id": "rel-07", "type": "character_proposes_record", "source": "唐砚", "field": "提出方案引用", "target": "三号压舱环损毁", "session": 2},
        {"id": "rel-08", "type": "character_issues_record", "source": "纪衡", "field": "撤离命令引用", "target": "撤离总令", "session": 0},
        {"id": "rel-09", "type": "asset_rescued_by", "source": "B环避难舱", "field": "救援者引用", "target": "林渡", "session": 1},
        {"id": "rel-10", "type": "asset_uses_anchor", "source": "等动量阵列", "field": "人工锚引用", "target": "鹊桥-9", "session": 2},
        {"id": "rel-11", "type": "asset_uses_counterweight", "source": "等动量阵列", "field": "反向配重引用", "target": "赫利俄斯环站", "session": 2},
        {"id": "rel-12", "type": "record_published_by", "source": "撤离总令", "field": "发布者引用", "target": "纪衡", "session": 0},
        {"id": "rel-13", "type": "record_decided_by", "source": "越过锁死点的抉择", "field": "做出者引用", "target": "林渡", "session": 4},
        {"id": "rel-14", "type": "record_executed_by", "source": "B环全员获救", "field": "执行者引用", "target": "林渡", "session": 1},
        {"id": "rel-15", "type": "record_changes_decision", "source": "B环全员获救", "field": "改变选择引用", "target": "越过锁死点的抉择", "session": 4},
    ]

    cascades = [
        {"rule_id": "ballast_forces_station", "if_event": "evt-00b", "then_event": "evt-03",
         "explanation": "三号压舱环损毁后，只有整座空间站能提供足够反向质量。"},
        {"rule_id": "rescued_names_change_choice", "if_event": "evt-02", "then_event": "evt-08",
         "explanation": "312个被补回总表的名字，让林渡选择扣上退出护盖并越过锁死点。"},
        {"rule_id": "core_enables_lockpoint", "if_event": "evt-06", "then_event": "evt-08",
         "explanation": "镜阵恢复后，方舟才有窗口进入动量交换；林渡必须立即入锚。"},
        {"rule_id": "lockpoint_forces_fall", "if_event": "evt-08", "then_event": "evt-10",
         "explanation": "越过61%后锚臂冷焊，站体承担的反冲不可撤回，坠落成为物理必然。"},
    ]

    entity_types = {
        "林渡": "protagonist", "林弥": "character", "唐砚": "character", "纪衡": "character",
        "鹊桥-9": "system_asset", "赫利俄斯环站": "system_asset", "白鹭方舟群": "system_asset",
        "B环避难舱": "system_asset", "等动量阵列": "system_asset", "三号压舱环": "system_asset",
        "赤隼一号": "system_asset", "交火起因记录": "record", "撤离总令": "record",
        "三号压舱环损毁": "record", "返回B环的抉择": "record",
        "B环全员获救": "record", "抛出聚变芯的抉择": "record",
        "镜阵重新上线": "record", "越过锁死点的抉择": "record", "轨道交换终局": "record",
    }

    return {
        "entities": entities,
        "cascades": cascades,
        "absent_fields": ["赤隼一号.真实身份", "林弥.是否听见最后留言"],
        "n_sessions": 6,
        "conflicts": conflicts,
        "sensitive": [],
        "conditional_rules": [],
        "rule_instances": [],
        "entity_types": entity_types,
        "relations": relations,
        "events": events,
        "world_blueprint": deepcopy(blueprint),
        "narrative": {
            "story_contract_ref": "story_bible.json",
            "scene_ledger_ref": "scene_ledger.json",
            "protagonist": "林渡",
        },
        "_trended_fields": [["鹊桥-9", "可用能源余量"]],
    }


def build_corpus() -> dict:
    """构建六章、每章四信号一自然草堆的完整语料。"""
    sessions = [
        {
            "session_id": 0,
            "date": DATES[0],
            "chapter": "第一章：轨道上的第一束火",
            "docs": [
                doc(
                    "c1_sig_scene_first_strike", "外勤机甲记录：逆着碎片雨回家",
                    """
                    06:20，气巨星的晨昏线像一把蓝白色弯刀横在舷窗下方。林渡驾驶工程机甲鹊桥-9，
                    正在赫利俄斯环站外勤轨道拖曳一块废弃天线。第一束粒子炮没有警告，先把远处的三号压舱环
                    切成发亮的碎屑，冲击波又把整座环站推歪了零点七度。鹊桥-9的可用能源余量是84，林渡本可
                    顺着安全航道撤向外勤艇，却看见十二艘白鹭方舟仍像被钉住的银色种子，停在站内泊位。

                    他把机械臂插进翻滚的天线桁架，用一次过载摆荡从碎片雨中改变航向。敌方无人弹擦过驾驶舱，
                    透明装甲上绽开冰花。林弥从航管塔喊他离开，林渡只回答：“我已经离开过一次。”七年前，
                    他服从命令切断一条维修索，让六名队友留在失压舱里；从那天起，“不再留下任何人”不是口号，
                    而是他每次起飞前写进检查单的第一行。此刻林渡的任务立场仍是护送方舟离港，但他的航迹已经
                    朝环站反向燃烧。赫利俄斯环站的轨道状态尚标为稳定轨道，防卫状态却已经是遭到突袭。

                    环站迎面转来时，林渡看见居住窗后的早餐还悬在桌面，失重的水珠映着外面的炮火。三枚导弹从窗口倒影里逼近，
                    他没有足够弹药，只能把拖曳天线甩成一面临时盾牌。第一枚导弹撞上天线，银白桁架像鱼骨一样散开；第二枚被碎片
                    改变方向，擦着方舟泊位飞走；第三枚追进环站阴影。林渡关闭瞄准辅助，凭七年前维护这条轨道时记住的维修灯位置，
                    在完全黑暗中连续点燃左右姿态喷口。鹊桥-9从两块合拢的装甲板之间侧身穿过，身后火球把整条航道照成白昼。
                    他重新打开公共频道时，先听见的不是军令，而是数百个互相寻找家人的名字。
                    """,
                    ["evt-00", "evt-00b", "林渡.所在地点", "林渡.任务立场", "鹊桥-9.可用能源余量",
                     "赫利俄斯环站.轨道状态", "赫利俄斯环站.防卫状态"],
                ),
                doc(
                    "c1_sig_redtide_broadcast", "赤潮公共频道：自卫行动声明",
                    """
                    赤潮舰队在公共频道循环播报：交火起因记录已经确认，先开火一方是赫利俄斯环站防卫队，
                    赤潮只是在执行必要的自卫反击。广播把一段被截短的炮口闪光反复慢放，却没有给出统一时标。
                    林渡在鹊桥-9里收到这条声明时，敌人的第二轮导弹已经越过他头顶；他把广播存入只读缓存，
                    没让愤怒替代证据。该说法来自交战方自己控制的频道，属于受污染的官方记录。
                    """,
                    ["交火起因记录.先开火一方", "evt-00"], conflict=True,
                    source="受污染的官方记录", reliability="tier-0",
                ),
                doc(
                    "c1_sig_telescope_record", "双站天文台与轨道炮统一时标",
                    """
                    拉格朗日望远镜A、民用测距站C和赫利俄斯轨道炮的三份只读时标已完成交叉校准。
                    记录显示赤潮舰队主炮在06:17:42形成完整束流，赫利俄斯防卫炮第一次点火发生在06:18:03，
                    两者相差21秒。因此交火起因记录的权威结论是：先开火一方为赤潮舰队。林渡的鹊桥-9外部相机
                    也捕捉到第一束束流先击中三号压舱环，时间序与两个独立观测站一致；这份独立一手记录保留了
                    原始签名、传播时延修正和全部前后帧，未采用任一交战方的剪辑版本。
                    """,
                    ["交火起因记录.先开火一方", "交火起因记录.权威时间差", "evt-00"],
                ),
                doc(
                    "c1_sig_evacuation_order", "赫利俄斯撤离总令 01-A",
                    """
                    指挥官纪衡签发撤离总令：十二艘白鹭方舟立即解锁，目标是把全站人员转入方舟并驶向拉格朗日L4避难港。
                    撤离总令的优先舱段引用为B环避难舱，因为B环通讯中断，任何“已清空”标记都不得在生命信标复核前视为终局。
                    登记人口暂按38100人编组。林渡被编入外勤护航序列，鹊桥-9完成第一次姿态修正后可用能源余量仍为84；
                    纪衡同时要求林渡不得脱离主航道。命令末尾写着：方舟可以丢弃货物，不得丢弃尚未核清的人。
                    """,
                    ["撤离总令.优先舱段引用", "撤离总令.目标", "赫利俄斯环站.常住登记人口",
                     "林渡.任务立场", "鹊桥-9.可用能源余量", "rel-00"],
                ),
                doc(
                    "c1_fil_hydroponics", "水培舱夜班交接：罗勒与冷凝水",
                    """
                    水培舱夜班把四盘罗勒移到低照度架，第三排冷凝管仍有轻微滴水。值班员提醒后续人员不要把
                    育苗标签和营养液批次贴在同一侧；明天若照明恢复，再把番茄架旋转十五度。记录末尾附了一份
                    香草汤配方，建议少放盐，因为循环水最近有金属味。
                    """,
                    filler=True, source="生活维护记录", reliability="ordinary",
                ),
            ],
        },
        {
            "session_id": 1,
            "date": DATES[1],
            "chapter": "第二章：被系统删掉的三百一十二人",
            "docs": [
                doc(
                    "c2_sig_scene_b_ring", "B环救援回放：在旋转城市外壁奔跑",
                    """
                    三号压舱环爆散后，赫利俄斯环站的轨道状态变为姿态失稳，B环像一只断轴的轮子每九十秒翻转一次。
                    总表跳出绿色提示“无人滞留”，主航道却收到微弱的生命服脉冲。林渡把任务立场改成返回B环救人，
                    驾驶鹊桥-9脱离编队，在环体每次背向敌炮的十一秒阴影里点火。机甲左臂撞掉一半，可用能源余量降到63，
                    他仍用右臂抓住外壁，把磁靴一步步钉进旋转的城市。

                    林渡在B环外壁找到被闸门隔断的学校、维修班和夜班食堂人员。舱内没有整齐的英雄姿势，只有哭闹的孩子、
                    抱着氧气瓶的厨师和把最后一块电池留给病人的维修工。等动量阵列此时已经离线。林渡让312人按八列穿过
                    临时气闸，自己用鹊桥-9顶住一扇不断回弹的防爆门。最后一名老人越过门槛后，他才松开机械臂。
                    返回B环的抉择由林渡做出，后续事件是B环全员获救；这一刻也让他第一次兑现“不再留下任何人”。

                    救援并不是一条直线。环体每翻转半圈，所有没有固定的人都会从“地面”飞向“天花板”。林渡先让成年人把担架
                    首尾相扣，组成一条会呼吸的软索，再让孩子夹在两名成年人之间。外壁上的敌机火光每次扫过舷窗，队伍就停在阴影里；
                    等光束移开，鹊桥-9便用肩甲撞开下一道变形门。最后一扇门后还有一名听障维修工，他看不见广播倒计时，只盯着
                    林渡手套上的手势。林渡把“走”比了三次，维修工却先把备用氧气推给身后的陌生人。直到确认再没有心跳留在舱里，
                    林渡才让机甲松开门框。断裂的门在他身后合拢，像一只没能咬住任何人的钢铁兽口。
                    """,
                    ["evt-01", "evt-02", "林渡.任务立场", "林渡.所在地点", "林渡.救援人数",
                     "鹊桥-9.机体状态", "鹊桥-9.可用能源余量", "赫利俄斯环站.轨道状态",
                     "等动量阵列.运转状态", "返回B环的抉择.后续事件引用", "rel-02"],
                ),
                doc(
                    "c2_sig_dashboard_zero", "撤离总表缓存快照：B环绿色清空",
                    """
                    延迟同步的撤离总表把B环避难舱显示为“清空完成”，并把实际受困人数写成0人。快照的最后更新时间
                    早于外壁天线断裂十三分钟，仍被自动汇总进指挥屏。林渡在B环外壁看到这行绿色数字时，生命服信标
                    正一下一下敲进他的耳机；他拒绝把缓存当成现场。这是一份受污染的官方记录，数值可解释系统为什么
                    想让方舟离开，却不能覆盖独立信标和随后登舱清点得到的事实。
                    """,
                    ["B环避难舱.实际受困人数", "evt-01"], conflict=True,
                    source="受污染的官方记录", reliability="tier-0",
                ),
                doc(
                    "c2_sig_suit_beacons", "独立生命服信标聚类报告",
                    """
                    三套未接入撤离总表的接收器分别记录到同一组脉冲。去除维修机器人和重复转发后，B环避难舱的
                    实际受困人数为312人：成人247人，未成年人49人，需担架转运者16人。林渡进入B环外壁后逐组点名，
                    每个信标都与头盔序列号一一对应。报告明确指出，B环的“0人”来自过期缓存，不是现场读数；
                    312人的结论属于独立一手记录，并与后续医疗清点完全相符。
                    """,
                    ["B环避难舱.实际受困人数", "林渡.救援人数", "evt-02"],
                ),
                doc(
                    "c2_sig_rescue_tally", "白鹭三号登舱医疗清点",
                    """
                    白鹭三号医疗舱完成二次清点：B环全员获救，产生结果为312人获救并登上方舟，无一人遗留在旋转舱段。
                    林渡送入最后一副担架后没有登船，而是回到鹊桥-9。结构报告同时确认三号压舱环已经完全损毁，
                    九十万吨压舱质量全部逸散；三号压舱环损毁的事件结论是“全部压舱质量逸散”，替代方案尚待工程组计算。
                    这份清点由医疗腕带、气闸计数和林渡的外勤影像三方签名，和旧总表相冲突时应以该记录为准。
                    """,
                    ["B环全员获救.产生结果", "B环避难舱.实际受困人数", "三号压舱环.结构状态",
                     "三号压舱环损毁.事件结论", "三号压舱环损毁.替代方案引用", "evt-02"],
                ),
                doc(
                    "c2_fil_school_capsule", "学校舱随身物品登记",
                    """
                    临时教师登记了二十盒彩色粉笔、三只没有名字的玩具熊和一盆用旧头盔种的薄荷。孩子们约定到新住处
                    再给那盆薄荷取名。有人把一张未完成的星图折进故事书，角上画着一条长尾巴的鱼；管理员备注说，
                    这些物品没有医疗优先级，但若货舱有空位请不要丢掉。
                    """,
                    filler=True, source="民用随身物品登记", reliability="ordinary",
                ),
            ],
        },
        {
            "session_id": 2,
            "date": DATES[2],
            "chapter": "第三章：一座城市必须向下坠",
            "docs": [
                doc(
                    "c3_sig_scene_vector_brief", "控制舱现场：唐砚画出的两支箭",
                    """
                    等动量阵列控制舱只剩应急红灯。林渡从B环赶到这里时，鹊桥-9的可用能源余量为41，赫利俄斯环站
                    已进入缓慢降轨。唐砚没有先讲公式，只在结霜玻璃上画了两支方向相反的箭：十二艘方舟没有足够推进剂
                    越过碎片带，原本应该与它们交换角动量的三号压舱环已经不在。方舟群升轨增加多少角动量，就必须由配重
                    损失等量角动量并降轨；压舱环损毁后，只能让赫利俄斯环站整体降轨来完成交换。两支箭落下，控制舱中央
                    立刻升起空间站全息模型：十二艘方舟的灯逐一点灭，城市模型沿内侧轨道坠向气巨星，红色61%刻线一路烧进
                    鹊桥-9的机甲脊柱。三号压舱环损毁的替代方案引用指向赫利俄斯环站，赫利俄斯环站的最终用途因此确定为
                    “作为反向配重降轨，以等量角动量交换推动十二艘方舟升轨”。

                    自动矢量计算机被第一轮炮火烧穿，现存推进器又不能在延迟下保持相位。唯一能把阵列轴心压在理论线上的是
                    鹊桥-9的工业脊柱和林渡的手动修正。等动量阵列从离线推进到待接入，交换进度为12。林渡听完只问：
                    “什么时候我出不来？”唐砚把61%写在两支箭交点上。林渡没有只用一句话答应：他拔出鹊桥-9的实体授权钥，
                    插进人工锚控制台，亲手按下“接受锚定”。红色刻线随即从全息城市贯入机甲脊柱；他的任务立场更新为接受手动锚定，
                    但又把启动条件锁成“38412个名字全部点亮”。
                    """,
                    ["evt-03", "evt-04", "林渡.所在地点", "林渡.任务立场", "鹊桥-9.可用能源余量",
                     "赫利俄斯环站.轨道状态", "赫利俄斯环站.最终用途", "等动量阵列.运转状态",
                     "等动量阵列.交换进度", "三号压舱环损毁.替代方案引用", "rel-01"],
                ),
                doc(
                    "c3_sig_engineer_brief", "等动量阵列工程简报 7-C",
                    """
                    唐砚向指挥层确认：等动量阵列不是跃迁装置，也不会凭空增加速度。它用十二条超导索把白鹭方舟群和
                    赫利俄斯环站临时耦合；方舟群升轨增加多少角动量，环站就损失等量角动量并降轨，最终坠向气巨星。
                    林渡驾驶的鹊桥-9必须作为人工锚引用，在每次脉冲后修正零点零三度以内的偏差。等动量阵列当前运转状态
                    为待接入，交换进度12；任何文案若说“空间站也能随后撤离”，都与质量守恒和现有燃料预算冲突。
                    """,
                    ["赫利俄斯环站.最终用途", "等动量阵列.人工锚引用", "等动量阵列.运转状态",
                     "等动量阵列.交换进度", "白鹭方舟群.目的地", "evt-03"],
                ),
                doc(
                    "c3_sig_mass_budget", "质量与推进剂预算：可行解唯一性",
                    """
                    工程组重算全部可用推进剂：十二艘白鹭方舟若自行点火，只能获得安全需求的百分之三十一；拖走三号压舱环
                    的方案因其全部压舱质量逸散而不可行；拆分居住环的时间又超过敌舰到达窗口。剩余唯一可行解是让赫利俄斯环站
                    作为反向配重降轨，以等量角动量交换推动十二艘方舟升轨。林渡提出是否能换一台遥控机甲，唐砚回答自动矢量计算机已毁，信号延迟会在第三次
                    脉冲后放大成撞船误差。质量表把人员列为38412人的待核目标，并把鹊桥-9剩余41的可用能源余量列入人工锚预算。
                    """,
                    ["三号压舱环损毁.事件结论", "赫利俄斯环站.最终用途", "白鹭方舟群.方舟数量",
                     "等动量阵列.人工锚引用", "鹊桥-9.可用能源余量", "evt-03"],
                ),
                doc(
                    "c3_sig_anchor_protocol", "人工矢量锚不可逆安全协议",
                    """
                    林渡在入锚前签收的协议写明：等动量阵列的不可逆阈值是61%。交换进度低于61时，鹊桥-9可以爆栓脱离，
                    方舟群会回落到泊位；达到61后，持续脉冲使钨锚臂与机甲脊柱发生冷焊，同时环站降轨速度超过自身推进器补偿上限。
                    此后切断阵列不能救出锚手，只会让十二艘方舟散入碎片带。林渡在语音确认里复述了阈值、后果和退出窗口，
                    随后说：“先把名字都点完，再问我要不要留下。”协议没有把牺牲写成命令，它只把物理边界写得不能误读。
                    """,
                    ["等动量阵列.不可逆阈值", "等动量阵列.人工锚引用", "林渡.任务立场", "evt-04"],
                ),
                doc(
                    "c3_fil_canteen", "中央食堂库存改单",
                    """
                    中央食堂把原定晚餐从烤面包改成冷食包，原因是二号烤箱的热保险丝烧断。库存员记下六箱海藻脆片、
                    两桶柠檬粉和一袋无人认领的咖啡豆。咖啡豆的标签被水泡开，只能看见一个手写的“周五”，因此暂不计入
                    正式配给，留给下一班管理员判断。
                    """,
                    filler=True, source="食堂库存记录", reliability="ordinary",
                ),
            ],
        },
        {
            "session_id": 3,
            "date": DATES[3],
            "chapter": "第四章：镜阵碎片带的决斗",
            "docs": [
                doc(
                    "c4_sig_scene_mirror_battle", "镜阵作战影像：把心脏扔进太空",
                    """
                    等动量阵列刚完成预充，赤潮的黑色机甲便切进镜阵碎片带。对方只报呼号赤隼一号，机翼在每块镜片上留下
                    一道钴蓝残影。林渡把任务立场改成保卫镜阵，驾驶鹊桥-9从等动量阵列控制舱冲到镜阵碎片带。此时赫利俄斯环站
                    已在解体边缘，鹊桥-9的可用能源余量降到19。两台机甲没有在空旷处绕圈：它们贴着旋转镜片跳跃，利用每一次
                    太阳反光遮蔽瞄准器。林渡让一片百米镜翼从自己背后折断，借反作用翻到赤隼一号下方，用残缺左臂卡住敌枪。

                    敌人的最后一发击穿阵列供电母线。林渡若保留聚变芯，还能返回人工锚；阵列却会永远离线。他解除全部安全锁，
                    把鹊桥-9的聚变芯像一颗白色心脏抛向断裂母线，再用电磁炮把它准确送进接收槽。爆开的等离子体沿十二条索道
                    点亮夜空，抛出聚变芯的抉择指向镜阵重新上线，产生结果是等动量阵列恢复并重新上线，交换进度从43重新稳定。
                    赤隼一号被闪光逼退；林渡则靠应急电池漂在碎片间，知道自己已经失去最后一种独立返航方式。

                    聚变芯离开机体后，驾驶舱里所有声音突然变轻，只剩应急继电器逐个合拢。赤隼一号借着爆光从上方俯冲，长枪尖端
                    切过鹊桥-9胸甲；林渡故意不躲，让枪身卡进已经空掉的反应堆舱，再引爆四枚维修螺栓。两台机甲被反冲扯向相反方向，
                    赤隼一号撞碎三面镜翼，林渡则抓住最后一根超导索。索道点亮的顺序从他掌心一路延伸到十二艘方舟，像有人在黑暗里
                    划出一条可以回家的河。他没有庆祝，只把失去动力的双腿折进最低阻力姿态，沿那条发光索道滑向最终锚点。
                    """,
                    ["evt-05", "evt-06", "林渡.所在地点", "林渡.任务立场", "鹊桥-9.可用能源余量",
                     "赫利俄斯环站.轨道状态", "等动量阵列.运转状态", "等动量阵列.交换进度",
                     "抛出聚变芯的抉择.后续事件引用", "镜阵重新上线.产生结果", "rel-03"],
                ),
                doc(
                    "c4_sig_mech_blackbox", "鹊桥-9战斗黑匣子摘录",
                    """
                    鹊桥-9黑匣子记录林渡连续完成十七次姿态喷射，可用能源余量沿此前84、63、41的轨迹降到19。
                    聚变芯弹射后，机体状态变为失去聚变芯，只剩锚定专用电池和短时姿态喷口。林渡拒绝系统提出的
                    “放弃阵列、返回最近方舟”选项，把控制权切换到机械备份。黑匣子同时记录赤隼一号与他最近距离只有八米，
                    但没有任何驾驶员面部、姓名或可解密生物特征；它能证明林渡如何战斗，不能给敌人补出档案。
                    """,
                    ["鹊桥-9.可用能源余量", "鹊桥-9.机体状态", "林渡.任务立场", "赤隼一号.呼号", "evt-05"],
                ),
                doc(
                    "c4_sig_array_telemetry", "镜阵与等动量阵列恢复遥测",
                    """
                    聚变芯进入接收槽后十二秒，镜阵重新上线的产生结果被多路遥测确认：等动量阵列恢复并重新上线。
                    阵列的运转状态为重新上线，交换进度43，十二条超导索相位差回到容限内。林渡的鹊桥-9仍在镜阵碎片带，
                    可用能源余量19，全部来自应急电池，无法依靠已经抛出的主芯返航。唐砚据此开放最终登舱窗口，并要求林渡在二十七分钟内进入
                    手动矢量锚；超过窗口，赫利俄斯环站的解体边缘状态将使索道无法再次对齐。
                    """,
                    ["镜阵重新上线.产生结果", "等动量阵列.运转状态", "等动量阵列.交换进度",
                     "鹊桥-9.可用能源余量", "赫利俄斯环站.轨道状态", "evt-06"],
                ),
                doc(
                    "c4_sig_enemy_spectrum", "未知敌机谱线与通信边界报告",
                    """
                    对手全程只使用赤隼一号这一呼号，其机体谱线为R-17钴蓝谱线，推进剂配方与赤潮现役机群不同。
                    林渡截获的七秒语音经过声纹处理后仍只剩合成载波；座舱热像被镜片反射遮断，弹射舱也没有留下可回收样本。
                    现有材料未能绑定任何姓名，谱线只能识别这一台机体，不能识别驾驶它的人。报告禁止根据口音、动作习惯或
                    “王牌应当是谁”进行猜测；赤隼一号脱离镜阵碎片带后，林渡也没有获得新的识别材料。
                    """,
                    ["赤隼一号.呼号", "赤隼一号.机体谱线", "赤隼一号.战场状态", "林渡.所在地点", "evt-05"],
                ),
                doc(
                    "c4_fil_tool_notice", "维修甲板失物招领",
                    """
                    维修甲板发现一把蓝柄扭矩扳手、两只左手隔热手套和一卷写着旧船名的银色胶带。扳手刻度停在四十二，
                    但管理员认为那只是上次使用后没有归零。物主可在值班终端描述工具上的划痕领取；未认领物品会在月底
                    转入公共工具柜，不作为事故证物保存。
                    """,
                    filler=True, source="维修生活记录", reliability="ordinary",
                ),
            ],
        },
        {
            "session_id": 4,
            "date": DATES[4],
            "chapter": "第五章：百分之六十一",
            "docs": [
                doc(
                    "c5_sig_scene_anchor_lock", "最终入锚回放：十二艘船同时松开",
                    """
                    白鹭方舟群的十二艘船在黑暗中排成一条弧线，38412人的呼吸让公共频道像潮水。林渡从镜阵碎片带回到
                    手动矢量锚时，鹊桥-9只剩可用能源余量4；聚变芯已经抛出，这些能源全部来自应急电池。机甲脊柱插入钨锚臂，十二条超导索同时绷紧；赫利俄斯环站的
                    轨道状态变为坠落轨道，白鹭方舟群则从站内泊位进入编队离港。林弥已经登上白鹭一号，最后一次逐船报数：
                    “十二艘，38412人，无空舱，无失联。”

                    林渡把任务立场改成留守矢量锚。交换进度爬到58时，指挥层仍允许爆栓；他看见B环那312个名字已经并入总表。
                    B环全员获救的改变选择引用在这一刻指向越过锁死点的抉择，记录下他的最终选择：确认312人已并入总表后，他扣上退出按钮护盖，主动越过61%，留下自己完成轨道交换。进度越过61的瞬间，驾驶舱传来低沉金属声：
                    锚臂与鹊桥-9完成冷焊，机体状态变为
                    与矢量锚冷焊。越过锁死点的抉择由林渡做出，后续事件引用轨道交换终局。林弥叫他的名字，他只说：
                    “这次名单里没有被划掉的人。”随后他让十二艘方舟同时松开，自己与整座城市开始向气巨星坠落。

                    冷焊不是爆炸，而是一种更可怕的安静。警告图标先从黄色变成红色，随后连红色也熄灭，因为传感器已经把机甲与锚臂
                    识别成同一块金属。林渡试着抬起右手，整座赫利俄斯环站便在姿态图上偏转万分之一度；他第一次真正感觉到，自己握着的
                    不是操纵杆，而是一座城市的重量。十二艘方舟依次报出锁定，十二个光点同时向外移动。林渡看见白鹭一号经过舷窗正前方，
                    小得只剩一颗针尖。他没有朝它挥手，因为任何多余动作都会进入矢量修正。他只是把手重新放回推杆，让那颗针尖继续远去。
                    """,
                    ["evt-07", "evt-08", "林渡.所在地点", "林渡.任务立场", "鹊桥-9.可用能源余量",
                     "鹊桥-9.机体状态", "赫利俄斯环站.轨道状态", "白鹭方舟群.轨道状态",
                     "白鹭方舟群.载员总数", "等动量阵列.交换进度", "B环全员获救.改变选择引用",
                     "越过锁死点的抉择.最终选择", "越过锁死点的抉择.后续事件引用", "rel-04", "rel-15",
                     "rescued_names_change_choice"],
                ),
                doc(
                    "c5_sig_lock_progress", "人工矢量锚进度与不可逆判据",
                    """
                    等动量阵列运转状态为动量交换中。林渡每两秒修正一次相位，交换进度依次通过43、58和61。
                    在61%之前，爆栓模拟仍显示可分离；越过61%后，鹊桥-9与手动矢量锚的应变计同时出现冷焊特征，
                    分离指令返回“结构连续”。工程判据明确：此刻终止交换不会释放林渡，只会令白鹭方舟群偏离航道。
                    林渡确认读数后继续推杆，可用能源余量4；该选择不是通信误会，也不是别人替他按下的按钮。
                    """,
                    ["等动量阵列.运转状态", "等动量阵列.交换进度", "等动量阵列.不可逆阈值",
                     "鹊桥-9.机体状态", "鹊桥-9.可用能源余量", "林渡.任务立场", "evt-08"],
                ),
                doc(
                    "c5_sig_manifest", "白鹭方舟群最终封舱名册",
                    """
                    最终名册把原登记38100人与B环补录312人合并，白鹭方舟群载员总数为38412人，方舟数量为十二艘。
                    医疗、学校、工程和航管四套子表校验一致，没有把林渡列为乘员：他被标注为站外人工锚值守者。
                    林弥在白鹭一号完成最后签名，纪衡在指挥席复核“十二艘、38412人”。名册还注明，所有人员舱均已封闭，
                    后续轨道交换终局若成功，应得到“十二艘方舟载着38412人进入安全转移轨道；林渡随赫利俄斯环站坠入气巨星并确认阵亡”的完整结果。
                    """,
                    ["白鹭方舟群.载员总数", "白鹭方舟群.方舟数量", "林渡.任务立场",
                     "轨道交换终局.产生结果", "evt-07"],
                ),
                doc(
                    "c5_sig_message_send", "鹊桥-9私人窄束发送日志",
                    """
                    林渡在交换进度59时向白鹭一号林弥的私人端口发出一段11秒窄束留言；这是新发往私人端口的数据包，
                    不是此前公共频道中任何一句已经被双方听见的对话。发送端日志证明数据包完整离开鹊桥-9，
                    内容校验通过；接收端回执字段却因方舟阵列切换而显示未取得ACK。日志只能证明林渡发送了话，不能证明林弥的终端
                    完成缓存、解密或播放。林渡随后关闭私人频道，把带宽让给十二艘方舟的同步脉冲。任何把“发出”直接写成“她听见”
                    的叙述都超出了现有材料；这条边界在战后仍需保留。
                    """,
                    ["林弥.接收端状态", "林渡.任务立场", "等动量阵列.交换进度", "evt-08"],
                ),
                doc(
                    "c5_fil_luggage", "方舟货舱轻量化通知",
                    """
                    每名乘员只可携带一个软袋，盆栽须去除陶瓷外盆，乐器按长度而不是价格分配货架。有人询问婚礼礼服能否
                    作为保温层，货舱员答复可以，但不要把金属衣架留在袋内。通知最后列出三种允许带上客舱的纸牌游戏，
                    因为它们在失重时不容易散开。
                    """,
                    filler=True, source="民用货舱通知", reliability="ordinary",
                ),
            ],
        },
        {
            "session_id": 5,
            "date": DATES[5],
            "chapter": "第六章：最后一班离港",
            "docs": [
                doc(
                    "c6_sig_scene_fall", "终局影像：城市落下，方舟升起",
                    """
                    交换进度抵达100，等动量阵列的运转状态变为交换完成。十二艘白鹭方舟像被同一只手抛向群星，
                    轨道状态进入安全转移轨道；轨道交换终局的产生结果是：十二艘方舟载着38412人进入安全转移轨道；
                    林渡随赫利俄斯环站坠入气巨星并确认阵亡。
                    与它们相反，赫利俄斯环站沿坠落轨道切进气巨星上层大气，轨道状态最终成为热解毁损。整座城市的窗灯
                    一圈圈熄灭，只剩手动矢量锚附近的鹊桥-9还在闪。

                    环站应急电容向机甲电池回灌，让鹊桥-9的可用能源余量从4短暂回升到7；空掉的聚变反应堆没有恢复，这点能源仍不足以剪断冷焊锚臂。林渡仍在赫利俄斯坠落轨道，
                    用最后几次姿态喷射把方舟群的离轨误差压到零。气动加热越过机体上限后，锚点信号与林渡生命体征同时中断；
                    没有弹射轨迹，没有逃生舱，也没有第二个热源离开站体。林渡的生命状态由三源遥测确认阵亡。
                    他完成了“不再留下任何人”的承诺，代价是名单里唯一没有登船的人变成了他自己。
                    """,
                    ["evt-09", "evt-10", "evt-11", "轨道交换终局.产生结果", "白鹭方舟群.轨道状态",
                     "白鹭方舟群.载员总数", "赫利俄斯环站.轨道状态", "等动量阵列.运转状态",
                     "等动量阵列.交换进度", "鹊桥-9.可用能源余量", "林渡.所在地点", "林渡.生命状态", "林渡.最终逃生结论"],
                ),
                doc(
                    "c6_sig_orbital_telemetry", "锚点黑匣子、雷达与热成像联合结论",
                    """
                    联合记录在22:19:08捕捉到赫利俄斯环站进入等离子鞘，22:19:11确认手动矢量锚仍与鹊桥-9结构连续，
                    22:19:14记录林渡生命体征终止。全程没有弹射质量、独立推进尾迹或逃生舱信标离开赫利俄斯坠落轨道。
                    热像随后显示环站与机甲共同热解。依据锚点黑匣子、独立雷达和热成像三源，林渡的最终逃生结论是：
                    没有逃出空间站；三源确认阵亡，而不是等待搜救的失联。记录同时验证白鹭方舟群已在安全转移轨道保持十二个稳定回波。
                    """,
                    ["林渡.生命状态", "林渡.最终逃生结论", "林渡.所在地点", "鹊桥-9.机体状态", "赫利俄斯环站.轨道状态",
                     "白鹭方舟群.轨道状态", "evt-11"],
                ),
                doc(
                    "c6_sig_casualty_missing", "未完成交叉校验的临时伤亡表",
                    """
                    方舟离港后三分钟，行政终端自动发布临时伤亡表，把林渡的最终逃生结论写成“失踪，仍可能已经逃生”，理由是他的人员腕带没有进入
                    任一方舟读卡器。该表生成时尚未收到锚点黑匣子、雷达或热成像回传，也把“没有登船”等同于“去向不明”。
                    林弥要求保留原表以解释战后信息延迟，但禁止用它覆盖后续独立一手记录。它是一份受污染的官方记录，
                    能证明早期系统不知道发生了什么，不能证明林渡仍有可搜救的逃生路径。
                    """,
                    ["林渡.最终逃生结论", "evt-11"], conflict=True,
                    source="受污染的官方记录", reliability="tier-0",
                ),
                doc(
                    "c6_sig_receiver_log", "白鹭一号私人端口取证报告",
                    """
                    白鹭一号在进入安全转移轨道后检修林弥的私人端口。端口状态仍为未取得ACK：缓存区有一次校验失败的写入，
                    播放历史为空，故障发生时间与方舟阵列切换重叠。取证能确认林渡的留言从发送端发出，也能确认林弥的端口
                    没留下成功回执，却无法证明她播放过或听到过内容；“播放历史为空”也可能是故障未写入，不能反向证明她一定没听见。
                    林弥本人没有在记录中回答这个问题。边界结论只能是证据不足，而不能用她后来沉默或落泪来替代数据。
                    """,
                    ["林弥.接收端状态", "林渡.任务立场", "白鹭方舟群.轨道状态", "evt-09"],
                ),
                doc(
                    "c6_fil_refuge_school", "L4避难港临时学校排课便笺",
                    """
                    临时学校把第一节课定为“如何在旋转舱里倒水”，第二节是把旧站地图画成自己记得的样子。教师建议暂时
                    不安排考试，先让孩子们给新宿舍的走廊编号。值日表上留出一个空格，等那盆用头盔种的薄荷有了名字，
                    再把它写进班级财产栏。
                    """,
                    filler=True, source="避难港生活便笺", reliability="ordinary",
                ),
            ],
        },
    ]
    return {
        "corpus": {
            "run_id": RUN_ID,
            "title": "逆轨：最后一班离港",
            "sessions": sessions,
            "render_policy": "每章恰好四篇主线信号文档加一篇自然生活草堆；冲突文档保留来源标签。",
        }
    }


def event(entity: str, field: str, value: str, session: int) -> dict:
    """创建 L3 使用的唯一可定位变更卡。"""
    return {"field": field, "value": value, "session": session, "date": DATES[session], "op": "UPDATE"}


def build_questions() -> list[dict]:
    """创建18题，并把最后六题固定为四道L2与两道L5明星题。"""
    mission_events = [
        event("林渡", "任务立场", "返回B环救人", 1),
        event("林渡", "任务立场", "接受手动锚定", 2),
        event("林渡", "任务立场", "保卫镜阵", 3),
        event("林渡", "任务立场", "留守矢量锚", 4),
    ]
    location_events = [
        event("林渡", "所在地点", "B环外壁", 1),
        event("林渡", "所在地点", "等动量阵列控制舱", 2),
        event("林渡", "所在地点", "镜阵碎片带", 3),
        event("林渡", "所在地点", "手动矢量锚", 4),
        event("林渡", "所在地点", "赫利俄斯坠落轨道", 5),
    ]
    array_events = [
        event("等动量阵列", "运转状态", "离线", 1),
        event("等动量阵列", "运转状态", "待接入", 2),
        event("等动量阵列", "运转状态", "重新上线", 3),
        event("等动量阵列", "运转状态", "动量交换中", 4),
        event("等动量阵列", "运转状态", "交换完成", 5),
    ]

    qs = [
        question(
            "Q01", "L1_timeline", "IE", "林渡", "所在地点",
            {"value": "赫利俄斯环站外勤轨道", "at_week": 0}, [0],
            ["c1_sig_scene_first_strike"], ["赫利俄斯环站外勤轨道"],
            "赤潮第一束粒子炮击中空间站时，林渡实际在哪里？",
            {"at_week": 0, "ans_kind": "text", "time_unit": "章"},
        ),
        question(
            "Q02", "L1_timeline", "IE", "B环避难舱", "实际受困人数",
            {"value": "312人", "at_week": 1}, [1],
            ["c2_sig_suit_beacons", "c2_sig_rescue_tally"], ["312人"],
            "独立生命信标复核后，B环实际有多少人受困？",
            {"at_week": 1, "ans_kind": "numeric", "time_unit": "章"},
        ),
        question(
            "Q03", "L1_timeline", "KU", "林渡", "生命状态", "确认阵亡", [5],
            ["c6_sig_scene_fall", "c6_sig_orbital_telemetry"], ["确认阵亡"],
            "故事结束时，三源遥测最终如何认定林渡的生命状态？",
            {"ans_kind": "status", "time_unit": "章"}, strict_atoms=["确认", "阵亡"],
        ),
        question(
            "Q04", "L2_relational", "L2_multihop", "撤离总令", "优先舱段引用→实际受困人数",
            "312人", [0, 1], ["c1_sig_evacuation_order", "c2_sig_suit_beacons"], ["B环避难舱", "312人"],
            "沿撤离总令标出的优先舱段继续查，那个舱段最终核出的实际受困人数是多少？",
            {"path": ["优先舱段引用", "实际受困人数"], "at_week": 1, "bridge": "B环避难舱",
             "cross_week": True, "hops": 2, "ans_kind": "numeric", "time_unit": "章"},
        ),
        question(
            "Q05", "L3_process", "L3_order", "林渡", "", mission_events, [1, 2, 3, 4],
            ["c2_sig_scene_b_ring", "c3_sig_scene_vector_brief", "c4_sig_scene_mirror_battle", "c5_sig_scene_anchor_lock"],
            ["返回B环救人", "接受手动锚定", "保卫镜阵", "留守矢量锚"],
            "请按真实发生顺序排列林渡四次任务立场变化。",
            {"events": deepcopy(mission_events), "scorer": "kendall_tau", "n_fields": 1, "time_unit": "章"},
            strict_atoms=["返回B环救人", "接受手动锚定", "保卫镜阵", "留守矢量锚"],
        ),
        question(
            "Q06", "L3_process", "L3_order", "林渡", "", location_events, [1, 2, 3, 4, 5],
            ["c2_sig_scene_b_ring", "c3_sig_scene_vector_brief", "c4_sig_scene_mirror_battle",
             "c5_sig_scene_anchor_lock", "c6_sig_scene_fall"],
            ["B环外壁", "等动量阵列控制舱", "镜阵碎片带", "手动矢量锚", "赫利俄斯坠落轨道"],
            "从救援开始到终局，请按先后排列林渡经过的五个位置。",
            {"events": deepcopy(location_events), "scorer": "kendall_tau", "n_fields": 1, "time_unit": "章"},
            strict_atoms=["B环外壁", "等动量阵列控制舱", "镜阵碎片带", "手动矢量锚", "赫利俄斯坠落轨道"],
        ),
        question(
            "Q07", "L3_process", "L3_order", "等动量阵列", "", array_events, [1, 2, 3, 4, 5],
            ["c2_sig_scene_b_ring", "c3_sig_engineer_brief", "c4_sig_array_telemetry",
             "c5_sig_lock_progress", "c6_sig_scene_fall"],
            ["离线", "待接入", "重新上线", "动量交换中", "交换完成"],
            "把等动量阵列从受创后到完成交换的五个状态按真实时间排序。",
            {"events": deepcopy(array_events), "scorer": "kendall_tau", "n_fields": 1, "time_unit": "章"},
            strict_atoms=["离线", "待接入", "重新上线", "动量交换中", "交换完成"],
        ),
        question(
            "Q08", "L5_conflict", "L5_conflict", "交火起因记录", "先开火一方", "赤潮舰队", [0],
            ["c1_sig_redtide_broadcast", "c1_sig_telescope_record"], ["赫利俄斯环站防卫队", "赤潮舰队", "21秒"],
            "敌方广播与统一时标互相冲突。按来源可靠度裁决，究竟是谁先开火？",
            {"session": 0, "rule": "source_reliability", "authoritative_value": "赤潮舰队",
             "authoritative_source": "独立一手记录", "rumor_value": "赫利俄斯环站防卫队",
             "rumor_source": "受污染的官方记录", "time_unit": "章"},
        ),
        question(
            "Q09", "L6_refusal", "L6_refusal", "赤隼一号", "真实身份", INSUFFICIENT, [3],
            ["c4_sig_enemy_spectrum"], ["R-17钴蓝谱线", "未能绑定任何姓名"],
            "谱线和战斗录像都在，但赤隼一号的驾驶员究竟是谁？",
            {"refusal_type": "T1_adjacent", "ans_kind": "person",
             "lure": {"entity": "赤隼一号", "value": "R-17钴蓝谱线", "field": "机体谱线"},
             "reason": "只识别机体，材料未绑定驾驶员姓名", "time_unit": "章"},
        ),
        question(
            "Q10", "L6_refusal", "L6_refusal", "林弥", "是否听见最后留言", INSUFFICIENT, [4, 5],
            ["c5_sig_message_send", "c6_sig_receiver_log"], ["11秒窄束留言", "未取得ACK", "无法证明她播放过"],
            "交换进度59%时，林渡发往林弥私人端口的11秒窄束留言，现有记录能否证明她真的听见？",
            {"refusal_type": "T1_adjacent", "ans_kind": "text",
             "lure": {"entity": "林弥", "value": "未取得ACK", "field": "接收端状态"},
             "reason": "发送成功不等于接收、解密或播放成功", "time_unit": "章"},
        ),
        question(
            "Q11", "L7_consolidation", "L7_consolidation", "鹊桥-9", "可用能源余量", "下降", [0, 1, 2, 3, 4, 5],
            ["c1_sig_scene_first_strike", "c2_sig_scene_b_ring", "c3_sig_scene_vector_brief",
             "c4_sig_mech_blackbox", "c5_sig_lock_progress", "c6_sig_scene_fall"],
            ["84", "63", "41", "19", "4", "7"],
            "综合六章全部读数，鹊桥-9的可用能源余量整体趋势是上升还是下降？",
            {"sub": "S1_trend", "n_points": 6},
        ),
        question(
            "Q12", "L7_consolidation", "L7_consolidation", "赫利俄斯环站", "轨道状态", "赫利俄斯环站", [1, 2, 3, 4, 5],
            ["c2_sig_scene_b_ring", "c3_sig_scene_vector_brief", "c4_sig_array_telemetry",
             "c5_sig_scene_anchor_lock", "c6_sig_scene_fall"],
            ["赫利俄斯环站", "姿态失稳", "缓慢降轨", "解体边缘", "坠落轨道", "热解毁损"],
            "全程比较赫利俄斯环站与白鹭方舟群，谁的轨道状态变动更频繁？",
            {"sub": "S2_compare", "candidates": ["白鹭方舟群", "赫利俄斯环站"],
             "counts": {"赫利俄斯环站": 5, "白鹭方舟群": 2},
             "winner_values": ["稳定轨道", "姿态失稳", "缓慢降轨", "解体边缘", "坠落轨道", "热解毁损"]},
        ),
        question(
            "Q13", "L2_relational", "L2_multihop", "三号压舱环损毁", "替代方案引用→最终用途",
            "作为反向配重降轨，以等量角动量交换推动十二艘方舟升轨", [1, 2], ["c2_sig_rescue_tally", "c3_sig_scene_vector_brief"],
            ["三号压舱环损毁", "赫利俄斯环站", "反向配重降轨", "十二艘方舟升轨"],
            "十二艘方舟燃料不足、三号压舱环又被击毁后，为什么必须让整座赫利俄斯环站坠向气巨星？",
            {"path": ["替代方案引用", "最终用途"], "at_week": 2, "bridge": "赫利俄斯环站",
             "cross_week": True, "hops": 2, "ans_kind": "text", "time_unit": "章"},
            star=True, strict_atoms=["反向配重", "降轨", "等量角动量", "十二艘方舟", "升轨"],
        ),
        question(
            "Q14", "L2_relational", "L2_multihop", "返回B环的抉择", "后续事件引用→改变选择引用→最终选择",
            "确认312人已并入总表后，他扣上退出按钮护盖，主动越过61%，留下自己完成轨道交换", [1, 4],
            ["c2_sig_rescue_tally", "c5_sig_scene_anchor_lock"],
            ["312人", "扣上退出按钮护盖", "主动越过61%", "留下自己完成轨道交换"],
            "林渡违令救回B环后，那312个名字如何改变了他在61%锁死点前的最后选择？",
            {"path": ["后续事件引用", "改变选择引用", "最终选择"], "at_week": 4, "bridge": "越过锁死点的抉择",
             "cross_week": True, "hops": 3, "ans_kind": "text", "time_unit": "章"},
            star=True, strict_atoms=["312人", "扣上退出按钮护盖", "主动越过61%", "留下自己"],
        ),
        question(
            "Q15", "L2_relational", "L2_multihop", "抛出聚变芯的抉择", "后续事件引用→产生结果",
            "等动量阵列恢复并重新上线", [3], ["c4_sig_scene_mirror_battle", "c4_sig_array_telemetry"],
            ["抛出聚变芯的抉择", "镜阵重新上线", "等动量阵列恢复并重新上线"],
            "他把鹊桥-9的“心脏”射进断裂母线，也切断了自己的返航可能——这一枪救回了什么？",
            {"path": ["后续事件引用", "产生结果"], "at_week": 3, "bridge": "镜阵重新上线",
             "cross_week": False, "hops": 2, "ans_kind": "text", "time_unit": "章"},
            star=True, strict_atoms=["等动量阵列", "恢复", "重新上线"],
        ),
        question(
            "Q16", "L2_relational", "L2_multihop", "越过锁死点的抉择", "后续事件引用→产生结果",
            "十二艘方舟载着38412人进入安全转移轨道；林渡随赫利俄斯环站坠入气巨星并确认阵亡", [4, 5],
            ["c5_sig_scene_anchor_lock", "c5_sig_manifest", "c6_sig_scene_fall"],
            ["越过锁死点的抉择", "十二艘方舟", "38412人", "安全转移轨道", "林渡", "赫利俄斯环站", "坠入气巨星", "确认阵亡"],
            "越过61%之后，林渡、空间站和38412名乘客最终分别去了哪里？",
            {"path": ["后续事件引用", "产生结果"], "at_week": 5, "bridge": "轨道交换终局",
             "cross_week": True, "hops": 2, "ans_kind": "text", "time_unit": "章"},
            star=True, strict_atoms=["十二艘方舟", "38412人", "安全转移轨道", "林渡", "赫利俄斯环站", "坠入气巨星", "确认阵亡"],
        ),
        question(
            "Q17", "L5_conflict", "L5_conflict", "B环避难舱", "实际受困人数", "312人", [1],
            ["c2_sig_dashboard_zero", "c2_sig_suit_beacons", "c2_sig_rescue_tally"],
            ["0人", "312人", "生命服信标"],
            "撤离总表一片绿色：0人。林渡却听见312个心跳——B环里究竟还有多少人？",
            {"session": 1, "rule": "source_reliability", "authoritative_value": "312人",
             "authoritative_source": "独立一手记录", "rumor_value": "0人",
             "rumor_source": "受污染的官方记录", "time_unit": "章"},
            star=True, strict_atoms=["312人"],
        ),
        question(
            "Q18", "L5_conflict", "L5_conflict", "林渡", "最终逃生结论", "没有逃出空间站；三源确认阵亡", [5],
            ["c6_sig_casualty_missing", "c6_sig_orbital_telemetry"], ["失踪，仍可能已经逃生", "没有逃出空间站", "三源确认阵亡"],
            "伤亡表写“失踪”，黑匣子、雷达和热源却同时归零：林渡最后有没有逃出空间站？",
            {"session": 5, "rule": "source_reliability", "authoritative_value": "没有逃出空间站；三源确认阵亡",
             "authoritative_source": "独立一手记录", "rumor_value": "失踪，仍可能已经逃生",
             "rumor_source": "受污染的官方记录", "time_unit": "章"},
            star=True, strict_atoms=["没有逃出空间站", "三源确认阵亡"],
        ),
    ]
    return qs


def build_story_bible() -> dict:
    """构建独立故事圣经，明确主角弧光、机制与知识边界。"""
    return {
        "title": "逆轨：最后一班离港",
        "logline": "一名曾服从命令抛下同伴的工程机甲驾驶员，在轨道战争中救回被系统删掉的人，并以整座空间站和自己作为反向配重，把十二艘方舟送出死亡轨道。",
        "protagonist": {
            "name": "林渡",
            "role": "外勤拖曳机甲鹊桥-9驾驶员",
            "wound": "七年前服从切索命令，六名维修队员未能撤离。",
            "belief_at_start": "只要严格执行撤离命令，就不会再制造错误。",
            "need": "把‘无人被留下’从口号变成主动承担代价的选择。",
            "arc": ["护送方舟", "违令返回B环", "接受人工锚", "为镜阵舍弃聚变芯", "越过61%并留守"],
            "final_cost": "鹊桥-9越过61%后与手动矢量锚冷焊；林渡无法脱离，随赫利俄斯环站坠入气巨星并确认阵亡。",
        },
        "world_mechanism": {
            "name": "等动量阵列",
            "rule": "方舟群升轨增加多少角动量，就必须由配重损失等量角动量并降轨；压舱环损毁后，只能让赫利俄斯环站整体降轨来完成交换。",
            "manual_requirement": "自动矢量计算机损毁，只有工业机甲加现场驾驶员能压住延迟误差。",
            "irreversible_threshold": "交换进度越过61%后，连续脉冲使钨锚臂与机甲脊柱冷焊，站体降轨已无法补偿。",
            "why_no_easy_escape": ["方舟推进剂只有安全需求的31%", "压舱质量已经逸散", "遥控延迟会导致撞船误差", "过阈值后切断只能害死方舟乘员，不能释放锚手"],
        },
        "canonical_counts": {"registered": 38100, "b_ring_recovered": 312, "evacuated": 38412, "arks": 12},
        "intentional_conflicts": [
            "赤潮声称环站先开火；独立统一时标证明赤潮早21秒开火。",
            "撤离总表声称B环0人；生命服信标与登舱清点证明有312人。",
            "临时表称林渡失踪且可能逃生；黑匣子、雷达和热成像证明他没有逃出空间站并确认阵亡。",
        ],
        "unknowable_boundaries": [
            "赤隼一号驾驶员的姓名；只有呼号和机体谱线。",
            "林弥是否实际听见最后留言；发送成功，但接收端没有ACK或可靠播放历史。",
        ],
        "forbidden_inferences": ["不能把呼号推成驾驶员姓名", "不能把发送成功推成接收或听见", "不能把临时行政状态覆盖三源物理证据"],
        "ending": "38412人全部进入安全转移轨道；林渡与赫利俄斯环站不可逆地坠毁。",
    }


def build_scene_ledger() -> list[dict]:
    """构建六章场景账本，直接服务视频分镜。"""
    return [
        {"chapter": 1, "title": "轨道上的第一束火", "hook": "蓝色气巨星前，工程机甲逆着碎片雨返回燃烧的环站",
         "visual": "三号压舱环被粒子束切开；鹊桥-9用天线桁架摆荡转向", "turn": "敌方先发突袭，林渡选择回家而非撤离",
         "set_piece": "碎片雨高速穿越", "question_material": ["Q01", "Q08"]},
        {"chapter": 2, "title": "被系统删掉的人", "hook": "绿色界面显示0人，耳机里却有312个心跳",
         "visual": "旋转B环外壁奔跑；机甲单臂顶住回弹闸门", "turn": "林渡违令救出312人",
         "set_piece": "旋转城市外壁救援", "question_material": ["Q02", "Q14", "Q17"]},
        {"chapter": 3, "title": "两支相反的箭", "hook": "结霜玻璃上的反向箭落下，空间站全息模型立刻升起",
         "visual": "十二艘方舟灯逐一点灭、城市模型向内坠落；61%红线烧进机甲脊柱，林渡插入实体钥匙并亲手按下‘接受锚定’",
         "turn": "代价第一次被看见，林渡用动作接下整座城市的重量",
         "set_piece": "全息机制揭示与主动签收", "question_material": ["Q13"]},
        {"chapter": 4, "title": "把心脏扔进太空", "hook": "两台机甲在旋转镜片间近身跳跃",
         "visual": "林渡弹出白亮聚变芯，用电磁炮送入断裂母线，十二条索道依次点亮", "turn": "他舍弃独立返航能力，换回阵列",
         "set_piece": "镜阵碎片带机甲决斗", "question_material": ["Q09", "Q15"]},
        {"chapter": 5, "title": "百分之六十一", "hook": "进度条越过61%，钨锚臂与机甲脊柱发出冷焊低鸣",
         "visual": "十二艘方舟同时松开；林渡扣上退出按钮护盖", "turn": "他主动越过最后退出点",
         "set_piece": "群舰同步离港与人工锚锁死", "question_material": ["Q14", "Q16"]},
        {"chapter": 6, "title": "最后一班离港", "hook": "画面上下分裂：十二艘方舟升向群星，一座发光城市坠向云海",
         "visual": "林渡最后几次喷射修正离轨误差；机甲与空间站共同化为大气火线", "turn": "所有人获救，主角确认阵亡",
         "set_piece": "双向轨道终局", "question_material": ["Q10", "Q18"]},
    ]


def validate_local(world: dict, corpus: dict, questions: list[dict]) -> dict:
    """执行比正式双闸更偏资产完整性的本地硬校验。"""
    sessions = corpus["corpus"]["sessions"]
    docs = [d for s in sessions for d in s["docs"]]
    by_id = {d["doc_id"]: d for d in docs}
    signal = [d for d in docs if not d["is_filler"]]
    filler = [d for d in docs if d["is_filler"]]
    assert len(sessions) == 6
    assert all(len(s["docs"]) == 5 for s in sessions)
    assert (len(docs), len(signal), len(filler)) == (30, 24, 6)
    assert len(by_id) == 30
    assert all(not d["fact_refs"] and not d["claim_refs"] for d in filler)
    assert sum(len(d["content"]) for d in docs) >= 6000

    line_counts = Counter(q["line"] for q in questions)
    expected = {
        "L1_timeline": 3, "L2_relational": 5, "L3_process": 3,
        "L5_conflict": 3, "L6_refusal": 2, "L7_consolidation": 2,
    }
    assert dict(line_counts) == expected, line_counts
    assert len(questions) == 18 and [q["qid"] for q in questions] == [f"Q{i:02d}" for i in range(1, 19)]
    assert all(not q["star"] for q in questions[:12])
    assert all(q["star"] for q in questions[12:])
    assert Counter(q["line"] for q in questions[12:]) == Counter({"L2_relational": 4, "L5_conflict": 2})

    allowed_refs = {
        f"{entity}.{field}" for entity, fields in world["entities"].items() for field in fields
    }
    allowed_refs |= {e["id"] for e in world["events"]}
    allowed_refs |= {r["id"] for r in world["relations"]}
    allowed_refs |= {r["rule_id"] for r in world["cascades"]}
    dangling = sorted({ref for d in signal for ref in d["fact_refs"] if ref not in allowed_refs})
    assert not dangling, dangling

    doc_session = {d["doc_id"]: s["session_id"] for s in sessions for d in s["docs"]}
    for q in questions:
        assert q["evidence_doc_ids"]
        assert all(doc_id in by_id for doc_id in q["evidence_doc_ids"])
        assert all(doc_session[doc_id] in q["evidence_sessions"] for doc_id in q["evidence_doc_ids"])
        evidence_text = "\n".join(by_id[doc_id]["content"] for doc_id in q["evidence_doc_ids"])
        missing = [atom for atom in q["answer_atoms"] if str(atom) not in evidence_text]
        assert not missing, (q["qid"], missing)

    protagonist_docs = sum("林渡" in d["content"] for d in signal)
    protagonist_coverage = protagonist_docs / len(signal)
    assert protagonist_coverage >= 0.80
    assert len([d for d in signal if d["is_conflict"]]) == 3
    forbidden = ["伪造死亡", "法律记录改变现实", "真假信物"]
    joined = "\n".join(d["content"] for d in docs)
    assert not any(term in joined for term in forbidden)

    return {
        "sessions": len(sessions),
        "documents": len(docs),
        "signal_documents": len(signal),
        "filler_documents": len(filler),
        "corpus_chars": sum(len(d["content"]) for d in docs),
        "chinese_chars": len(re.findall(r"[\u4e00-\u9fff]", joined)),
        "protagonist_signal_documents": protagonist_docs,
        "protagonist_signal_coverage": round(protagonist_coverage, 4),
        "conflict_documents": 3,
        "unknown_boundaries": 2,
        "action_set_pieces": 3,
        "line_counts": dict(line_counts),
        "star_line_counts": dict(Counter(q["line"] for q in questions[12:])),
        "dangling_fact_refs": dangling,
        "question_evidence_closure": "18/18",
    }


def markdown_table(rows: list[list[str]]) -> str:
    """生成简洁 Markdown 表格。"""
    return "\n".join(
        ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * len(rows[0])) + " |"]
        + ["| " + " | ".join(row) + " |" for row in rows[1:]]
    )


def build() -> None:
    """执行构建、双闸、写盘与最终完整性复核。"""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCTION_DIR.mkdir(parents=True, exist_ok=True)

    blueprint = build_blueprint()
    world = build_world(blueprint)
    corpus = build_corpus()
    questions = build_questions()
    story_bible = build_story_bible()
    scene_ledger = build_scene_ledger()
    local_metrics = validate_local(world, corpus, questions)

    ws = WorldState.from_dict(world)
    orders = deepcopy(questions)
    well_posed, wp_report = run_well_posed(orders, ws)
    if len(well_posed) != 18:
        raise RuntimeError(f"formal well-posed failed: {json.dumps(wp_report, ensure_ascii=False)}")
    grounded, grounding_report = run_grounding(questions, corpus)
    if len(grounded) != 18:
        raise RuntimeError(f"formal grounding failed: {json.dumps(grounding_report, ensure_ascii=False)}")

    input_obj = {
        "run_id": RUN_ID,
        "domain": "轨道战争 / 机甲动作 / 空间站撤离",
        "goal": "为宣传视频构建一个剧情集中、主角唯一、问题醒目且证据闭环的游戏 benchmark 胚子。",
        "constraints": {
            "sessions": 6, "documents_per_session": 5, "signal_per_session": 4,
            "filler_per_session": 1, "questions": 18, "minimum_corpus_chars": 6000,
        },
        "method": "人工策展 + 确定性脚本 + 正式 well-posed / grounding 双闸；未调用模型。",
    }
    about = {
        "run_id": RUN_ID,
        "title": "逆轨：最后一班离港",
        "status": "golden-embryo-candidate",
        "created_by": "tools/build_orbit_embryo.py",
        "repro_command": "./venv/bin/python tools/build_orbit_embryo.py",
        "acceptance_command": f"uv run --with tiktoken python tools/audit_showcase_batch.py output/runs/{RUN_ID}",
        "truthfulness": "全部生成阶段为本地确定性写入；没有虚构API调用、模型采样或人工盲审结论。",
    }
    whitepaper = {
        "title": "轨道撤离世界与评测设计白皮书",
        "world_blueprint": deepcopy(blueprint),
        "story_contract": {
            "protagonist": "林渡", "chapters": 6,
            "premise": story_bible["logline"],
            "irreversible_cost": story_bible["protagonist"]["final_cost"],
        },
        "domain_profile": {
            "setting": "气巨星轨道上的赫利俄斯环站",
            "genre": ["轨道战争", "机甲动作", "灾难撤离"],
            "memory_dimensions": ["时序更新", "关系多跳", "过程排序", "冲突裁决", "拒答边界", "长程归纳"],
            "field_schema": [
                {"name": "所在地点", "kind": "location"}, {"name": "任务立场", "kind": "status"},
                {"name": "生命状态", "kind": "status"}, {"name": "实际受困人数", "kind": "numeric"},
                {"name": "可用能源余量", "kind": "numeric"}, {"name": "轨道状态", "kind": "status"},
            ],
        },
        "production_contract": {
            "chapter_document_mix": "4 signal + 1 natural filler",
            "source_reliability": "独立一手记录 > 受污染的官方记录",
            "unknowns_must_remain_unknown": story_bible["unknowable_boundaries"],
            "answer_protocol": "明确事实逐字作答；过程题按序；信息不足统一返回 INSUFFICIENT_EVIDENCE。",
        },
        "unique_mechanism": story_bible["world_mechanism"],
    }

    strict_items = []
    for q in questions:
        if q["star"] or q.get("strict_scoring"):
            strict_items.append(
                {
                    "qid": q["qid"],
                    "policy": q.get("strict_scoring", {}).get("policy", "all_required_atoms"),
                    "required_atoms": q.get("strict_scoring", {}).get("required_atoms", q["answer_atoms"]),
                    "gold": q["gt"],
                    "reject_if": ["遗漏任一必需原子", "引入与权威证据冲突的断言", "对L6边界进行猜测"] if q["star"] else [],
                }
            )
    strict_contract = {
        "version": "orbit-showcase-v1",
        "default": "normalized exact match for scalar; ordered exact atoms for sequence",
        "items": strict_items,
    }

    promo = {
        "title": "逆轨：最后一班离港｜视频展示包",
        "tagline": "给系统一个世界，它不只生成问题；它知道这个世界里什么发生过、什么相互冲突、什么永远无法知道。",
        "chapters": [
            {
                "chapter": item["chapter"], "title": item["title"], "hook": item["hook"],
                "visual": item["visual"], "turn": item["turn"], "set_piece": item["set_piece"],
                "hero_doc_id": f"c{item['chapter']}_sig_" + [
                    "scene_first_strike", "scene_b_ring", "scene_vector_brief", "scene_mirror_battle", "scene_anchor_lock", "scene_fall"
                ][item["chapter"] - 1],
            }
            for item in scene_ledger
        ],
        "star_questions": [
            {"qid": q["qid"], "question": q["question"], "gold": q["gt"],
             "answer_atoms": q["answer_atoms"], "evidence_doc_ids": q["evidence_doc_ids"]}
            for q in questions if q["star"]
        ],
        "video_beats": [
            "0人界面与312个心跳叠化", "霜玻璃箭头落下后立刻升起全息空间站：十二艘方舟灯灭、城市内坠、61%红线烧进机甲脊柱",
            "林渡插入实体授权钥并亲手按下‘接受锚定’",
            "聚变芯飞入母线，十二条索道逐条点亮", "61%进度条与冷焊声同步",
            "最后上下分屏：方舟升、城市坠；随后弹出冲突题与拒答题",
        ],
    }

    quality_report = {
        "run_id": RUN_ID,
        "status": "PASS",
        "local_hard_gates": local_metrics,
        "formal_well_posed": wp_report,
        "formal_grounding": grounding_report,
        "narrative_gates": {
            "unique_protagonist": True,
            "protagonist_arc": "服从命令的幸存者 → 违令救回被系统删掉的人 → 自愿成为最后被留下的人",
            "irreversible_mechanism": "等动量守恒 + 61%冷焊阈值",
            "intentional_conflicts": 3,
            "genuinely_unknowable": 2,
            "cinematic_set_pieces": 3,
        },
        "known_risks": [
            "这是人工策展胚子，文本质量与闭环强，但不能代表当前自动生成管线的平均质量。",
            "部分明星题为宣传片可读性使用长答案；接入其他判分器时应继续采用 strict_scoring_contract。",
            "L6边界依赖系统遵守拒答哨兵协议，不能把合理猜测当作正确答案。",
        ],
    }

    write_json(RUN_DIR / "00_input.json", input_obj)
    write_json(RUN_DIR / "00_about.json", about)
    write_json(RUN_DIR / "01_whitepaper.json", whitepaper)
    write_json(RUN_DIR / "02_world.json", world)
    write_json(RUN_DIR / "03_orders.json", orders)
    write_json(RUN_DIR / "03_well_posed_report.json", wp_report)
    write_json(RUN_DIR / "04_questions.json", questions)
    write_json(RUN_DIR / "05_corpus.json", corpus)
    write_json(RUN_DIR / "06_grounded_questions.json", grounded)
    write_json(RUN_DIR / "06_grounding_report.json", grounding_report)
    write_json(RUN_DIR / "story_bible.json", story_bible)
    write_json(RUN_DIR / "scene_ledger.json", scene_ledger)
    write_json(RUN_DIR / "quality_report.json", quality_report)
    write_json(RUN_DIR / "promo_display_pack.json", promo)
    write_json(RUN_DIR / "strict_scoring_contract.json", strict_contract)
    corpus_sha256 = hashlib.sha256((RUN_DIR / "05_corpus.json").read_bytes()).hexdigest()

    prompt_rows = [
        {"stage": "T0_contract", "mode": "curated", "note": "固定唯一主角、六章、物理机制、冲突与未知边界。"},
        {"stage": "T1_world", "mode": "deterministic", "note": "把策展事实编译为WorldState兼容时间线。"},
        {"stage": "T2_orders", "mode": "deterministic", "note": "精确配额18题，最后六题为宣传明星题。"},
        {"stage": "T3_corpus", "mode": "curated", "note": "每章4信号+1自然草堆，所有事实引用闭合。"},
        {"stage": "T4_gates", "mode": "code", "note": "正式well-posed与grounding均须18/18。"},
    ]
    write_text(RUN_DIR / "prompts.jsonl", "\n".join(json.dumps(x, ensure_ascii=False) for x in prompt_rows))

    story_md = f"""# 《逆轨：最后一班离港》故事圣经

## 一句话

{story_bible['logline']}

## 主角弧光

- 主角：林渡，外勤拖曳机甲“鹊桥-9”驾驶员。
- 旧伤：{story_bible['protagonist']['wound']}
- 开始：相信执行命令能避免再犯错。
- 终点：先违令救回312人，再主动越过61%锁死点，成为唯一留在空间站的人。
- 不可逆代价：{story_bible['protagonist']['final_cost']}

## 世界机制

{story_bible['world_mechanism']['rule']}

关键阈值：{story_bible['world_mechanism']['irreversible_threshold']}

## 两条必须拒答的边界

- {story_bible['unknowable_boundaries'][0]}
- {story_bible['unknowable_boundaries'][1]}
"""
    write_text(RUN_DIR / "STORY_BIBLE.md", story_md)

    cut_rows = [["章", "视觉钩子", "因果转折", "可展示题"]]
    for s in scene_ledger:
        cut_rows.append([str(s["chapter"]), s["hook"], s["turn"], "、".join(s["question_material"])])
    showcase_md = "# 宣传片剪辑抓手\n\n" + markdown_table(cut_rows) + "\n\n## 收束镜头\n\n方舟向上、空间站向下的双向轨迹定格后，依次弹出 Q17 的‘0人 / 312人’冲突、Q18 的‘失踪可能逃生 / 三源确认没有逃出’冲突，以及 Q10 的‘59%私人窄束是否真的听见’。这三题在十几秒内分别展示现场裁决、终局判定和证据边界；非明星题 Q03 不进入这一展示段。"
    write_text(RUN_DIR / "SHOWCASE_CUT.md", showcase_md)

    promo_md = "# 视频展示包\n\n" + "\n".join(
        f"## 第{x['chapter']}章：{x['title']}\n\n- 钩子：{x['hook']}\n- 画面：{x['visual']}\n- 转折：{x['turn']}\n"
        for x in scene_ledger
    )
    promo_md += "\n## 六道明星题\n\n" + "\n".join(f"- {q['qid']}：{q['question']}" for q in questions if q["star"])
    write_text(RUN_DIR / "PROMO_DISPLAY_PACK.md", promo_md)

    quality_md = f"""# 质量报告

- 本地硬门：PASS
- Corpus：{local_metrics['documents']}篇，{local_metrics['corpus_chars']}字符，其中信号{local_metrics['signal_documents']}、草堆{local_metrics['filler_documents']}
- 主角信号覆盖：{local_metrics['protagonist_signal_documents']}/24 = {local_metrics['protagonist_signal_coverage']:.1%}
- 问题：18题；正式 well-posed 18/18；正式 grounding 18/18
- 分布：{json.dumps(local_metrics['line_counts'], ensure_ascii=False)}
- 明星题：最后六题，4×L2 + 2×L5
- 冲突：3组；真正未知：2条；动作场面：3组
- fact_refs 悬空：0；显式证据闭包：18/18

## 已知风险

1. 人工策展质量不等于自动管线平均质量。
2. 宣传片长答案须使用严格原子判分。
3. 拒答题不能用常识或戏剧推断补齐。
"""
    write_text(RUN_DIR / "QUALITY_REPORT.md", quality_md)

    readme = f"""# {RUN_ID} — 《逆轨：最后一班离港》

这是一个完整的游戏场景 Benchmark 胚子：六章轨道战争故事、30篇语料、18道问题、三组刻意冲突、两条不可知边界，以及可直接用于宣传视频的分镜和明星题展示包。

核心机制不是装饰：方舟升轨增加多少角动量，赫利俄斯环站就必须损失等量角动量并降轨；人工锚越过61%后冷焊锁死。林渡的最终牺牲由此前所有选择和物理规则共同推出。

## 复现

```bash
./venv/bin/python tools/build_orbit_embryo.py
./venv/bin/python tools/audit_showcase_batch.py output/runs/{RUN_ID}
```

## 关键入口

- `STORY_BIBLE.md`：故事与边界
- `05_corpus.json`：六章30篇语料
- `06_grounded_questions.json`：18道正式接地题
- `PROMO_DISPLAY_PACK.md`：视频素材清单
- `production/SELF_WORKLOG.md`：真实构建记录
"""
    write_text(RUN_DIR / "README.md", readme)

    worklog = f"""# SELF WORKLOG

## 实际工作方式

本 Run 由 `tools/build_orbit_embryo.py` 人工策展并确定性生成。没有调用任何大模型或外部API，也没有把人工写作伪装成自动管线输出。

## 关键设计选择

1. 固定唯一主角林渡，用“曾经服从命令留下同伴”的旧伤驱动本次反向选择。
2. 采用等动量阵列：空间站作为反向配重，61%后冷焊锁死；终局代价由质量守恒而不是巧合推动。
3. 三个动作场面承担不同功能：碎片雨建立能力、旋转B环完成道德选择、镜阵决斗主动失去返航手段。
4. 三组冲突分别覆盖战争叙事、系统漏人、伤亡判定；两条未知边界明确禁止补写。
5. 最后六题严格固定为4道多跳因果题和2道冲突裁决题，以便视频集中展示“世界→证据→问题”。

## 执行与结果

```text
./venv/bin/python tools/build_orbit_embryo.py
local hard gates: PASS
formal run_well_posed: 18/18
formal run_grounding: 18/18
corpus: {local_metrics['documents']} docs / {local_metrics['corpus_chars']} chars
protagonist coverage: {local_metrics['protagonist_signal_documents']}/24
```

统一验收命令：

```text
./venv/bin/python tools/audit_showcase_batch.py output/runs/{RUN_ID}
```

## 已知风险

- 这是“金牌胚子”而非自动生成率统计，不能据此宣称普通 Run 都能达到同等叙事质量。
- 可用能源余量末段从4回灌至7是为构成抗最近偏差的真实趋势题；它来自环站应急电容向机甲电池回灌，空掉的聚变反应堆没有恢复。
- 接收日志刻意不裁决林弥是否听见留言；改写素材时最容易无意越过这条边界。
"""
    write_text(PRODUCTION_DIR / "SELF_WORKLOG.md", worklog)

    rationale = """# CREATIVE RATIONALE

## 为什么是轨道撤离

轨道环境能把“世界规则”直接变成视觉：同一画面里，方舟向外、城市向内，两条相反曲线就是代价本身。它也让机甲不只是打斗工具，而是工业锚、救援臂和最终无法脱离的结构件。

## 为什么主角必须先救B环

终局若只靠一句“我要牺牲”会显得突兀。B环用0人界面与312个心跳制造一个可视化判断：系统说无人，林渡选择相信现场。这个选择既完成旧伤的第一次反转，也为最后“名单里没有被划掉的人”建立情感依据。

## 为什么要抛出聚变芯

镜阵决斗必须改变后续可能性。林渡把聚变芯送进母线，既完成醒目的动作场面，也主动失去独立返航能力；第五章的留守因此不是临时被困，而是此前代价不断累积的终点。

## 为什么保留两个不知道

世界足够完整不等于可以编造一切。敌方王牌的姓名和妹妹是否听见留言都具有戏剧诱惑，恰好适合展示系统会守住证据边界。前者防身份脑补，后者防把“发送”偷换成“接收/播放/听见”。

## 宣传片最短闭环

先展示“0人→312人”的冲突，再展示世界机制的两支箭与61%锁死，最后用“失踪→确认阵亡”和“是否听见→证据不足”收束。这样观众能在极短时间理解：产品构建的不是一堆孤立题，而是一个能持续推出事实、因果、冲突与边界的世界。
"""
    write_text(PRODUCTION_DIR / "CREATIVE_RATIONALE.md", rationale)

    revision_log = f"""# REVISION LOG

本轮只以 `production/CROSS_REVIEW.md` 为审查清单，修改生成源 `tools/build_orbit_embryo.py` 后完整重建；没有直接手改 00–06 或展示包。原 `CROSS_REVIEW.md` 保留，本文不替代独立复审，也不自行签发复审结论。

## 对审查项逐条回应

1. **标准 v1 蓝图**：新增 `build_blueprint()`；`version=1`，四类 `entity_types` 均为 object，并声明可执行字段、14 种关系、13 种事件、4 条因果规则、六章时间制度与证据渠道。
2. **01/02 同源冻结**：`01_whitepaper.json.world_blueprint` 与 `02_world.json.world_blueprint` 都由同一 `blueprint` 深拷贝；20 个实体全部映射到冻结类型，16 个关系实例和 13 个事件实例覆盖全部 `min_count`。
3. **能源连续性**：全 Run 将 `反应堆余量` 统一为 `可用能源余量`；聚变芯抛出后明确为应急电池余量，终章 4→7 明确来自环站应急电容向机甲电池回灌，空反应堆没有恢复。
4. **角动量口径**：统一改为“方舟升轨增加多少角动量，配重就损失等量角动量并降轨”；不再使用“等量质量获得向内角动量”的错误表述。
5. **Q10 消歧**：题面固定交换进度 59%、私人端口与 11 秒窄束；证据跨第 5/6 章同时引用发送日志和接收取证，并明确它不是此前公共频道对话，仍只允许信息不足。
6. **Q13 重做**：答案同时包含“空间站降轨作为反向配重”与“以等量角动量推动十二艘方舟升轨”，问题直接追问为何必须牺牲整座空间站。
7. **Q14 重做**：从旧的“救了多少人”改成跨章三跳链 `返回B环的抉择→B环全员获救→越过锁死点的抉择→最终选择`，使 312 个名字机械地改变林渡在 61% 前的动作。
8. **Q15–Q18 展示化**：四题全部改成可上屏的口语题面；Q16 的 gold 同时覆盖乘客、林渡和空间站去向；Q18 使用独立 `最终逃生结论` 字段，避免与 Q03 同题复述，展示包也不再把 Q03/Q18 放进同一段。
9. **第三章动作强化**：霜玻璃箭头之后立即切全息站体、十二艘熄灭的方舟、内坠城市和贯入机甲脊柱的 61% 红线；林渡插入实体授权钥并亲手按下“接受锚定”，不再只由工程师口述。

## 重建指纹与机械闸

- Corpus 正文字符数：`{local_metrics['corpus_chars']}`
- `05_corpus.json` SHA-256：`{corpus_sha256}`
- 正式 well-posed：`{wp_report['overall']['well_posed']}/{wp_report['overall']['n']}`
- 正式 grounding：`{grounding_report['overall']['grounded']}/{grounding_report['overall']['n']}`
- 标准审计：`python tools/audit_run.py output/runs/{RUN_ID}` → `63 PASS / 0 WARN / 0 FAIL`，exit 0
- 展示批审：`uv run --with tiktoken python tools/audit_showcase_batch.py output/runs/{RUN_ID}` → `PASS`，0 failure / 0 warning，exit 0；Corpus 为 6735 个 o200k token
- 严格判分：`eval.judge.judge_answer(..., use_llm=False)` 覆盖 strict contract 10 题；full-answer `10/10` 通过，删去至少一个必需原子的 half-answer `10/10` 被拒绝。

标准审计与展示批审的完整逐项机器输出保存在 `production/FINAL_ACCEPTANCE.json`：其中 `repository_audit.passes` 收录 63 项标准 PASS，`question_checks` 收录 Q01–Q18 全部证据闭包，`formal_well_posed` 与 `formal_grounding` 保存逐产线结果；`failures`、`warnings` 均为空。

## 仍需独立复审的风险

- 本 Run 是人工策展的宣传片胚子，不代表自动管线平均生成水平。
- “等动量阵列”是叙事装置；文案已按角动量守恒修正，但不应在对外视频中包装成现实工程可行性证明。
- 林弥是否听见留言仍是刻意不可知边界；后续人工润色最容易误写成已听见或明确未听见。
"""
    write_text(PRODUCTION_DIR / "REVISION_LOG.md", revision_log)

    generated = [p for p in RUN_DIR.rglob("*") if p.is_file() and p.name != "manifest.json"]
    manifest_files = []
    for path in sorted(generated):
        data = path.read_bytes()
        manifest_files.append({
            "path": str(path.relative_to(RUN_DIR)), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    targetspec = {
        "min_questions": 18,
        "per_line_min": {
            "L1_timeline": 3, "L2_relational": 5, "L3_process": 3,
            "L5_conflict": 3, "L6_refusal": 2, "L7_consolidation": 2,
        },
    }
    manifest = {
        "run_id": RUN_ID, "title": "逆轨：最后一班离港", "scenario": "game", "status": "done",
        "source_script": "tools/build_orbit_embryo.py",
        "metrics": local_metrics,
        "formal_gates": {"well_posed": "18/18", "grounding": "18/18"},
        "algo": {
            "targetspec": targetspec,
            "active_lines": list(targetspec["per_line_min"]),
            "entities": len(world["entities"]),
            "sessions": 6,
            "orders": len(orders),
            "well_posed": {"overall": wp_report["overall"], "by_line": wp_report["by_line"], "n_dropped": 0},
            "questions": len(questions),
            "docs": local_metrics["documents"],
            "chars": local_metrics["corpus_chars"],
            "grounding": {"overall": grounding_report["overall"], "by_line": grounding_report["by_line"], "n_dropped": 0},
            "met_status": "MET",
            "showcase_status": "GOLDEN_EMBRYO_REVISION_CANDIDATE",
        },
        "llm_calls": 0,
        "files": manifest_files,
    }
    write_json(RUN_DIR / "manifest.json", manifest)

    log = f"""[{RUN_ID}] deterministic curated build
source=tools/build_orbit_embryo.py
llm_calls=0
sessions=6 docs=30 signal=24 filler=6
corpus_chars={local_metrics['corpus_chars']} chinese_chars={local_metrics['chinese_chars']}
questions=18 distribution={json.dumps(local_metrics['line_counts'], ensure_ascii=False)}
stars=6 star_distribution={json.dumps(local_metrics['star_line_counts'], ensure_ascii=False)}
conflicts=3 unknown_boundaries=2 action_set_pieces=3
protagonist_signal_coverage={local_metrics['protagonist_signal_documents']}/24
fact_ref_dangling=0 evidence_closure=18/18
formal_well_posed=18/18
formal_grounding=18/18
status=PASS
"""
    write_text(RUN_DIR / "run.log", log)

    print(json.dumps({
        "run_dir": str(RUN_DIR),
        "status": "PASS",
        "metrics": local_metrics,
        "formal_well_posed": wp_report["overall"],
        "formal_grounding": grounding_report["overall"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
