"""构建 Memory Forge 游戏场景的叙事优先黄金样板 Run。

这个脚本不调用不稳定的自动闭环，而是把多 Agent 已冻结的故事圣经、
真值账本、语料、题目和审计结果封装成与现有前端兼容的 00--06 产物。
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.grounding import run_grounding
from pipeline.well_posed import run_well_posed
from pipeline.world_state import WorldState


RUN_ID = "game_showcase__20260906-053636"
RUN_DIR = ROOT / "output" / "runs" / RUN_ID
FRONTEND_RUN_DIR = ROOT / "frontend" / "output" / "runs" / RUN_ID
DATES = ["2025-02-14", "2025-02-15", "2025-02-16", "2025-02-17", "2025-02-18", "2025-02-19"]


def write_json(path: Path, value) -> None:
    """以统一格式写 JSON；父目录不存在时自动创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def signal_doc_id(doc_id: str) -> str:
    """把信号文档 ID 统一为仓库审计器识别的 ``*_sig_*`` 约定。"""
    if "_sig_" in doc_id:
        return doc_id
    prefix, separator, suffix = doc_id.partition("_")
    return f"{prefix}_sig_{suffix}" if separator else f"sig_{doc_id}"


def make_timeline(points: list[tuple[int, str | None]]) -> list[dict]:
    """把稀疏的章节取值编译成与 WorldState.to_dict 兼容的操作序列。"""
    out: list[dict] = []
    previous = None
    for session, value in points:
        if value is None:
            op = "EXPIRE"
        elif previous is None:
            op = "SET"
        else:
            op = "UPDATE"
        out.append({"session": session, "date": DATES[session], "op": op, "value": value, "prev": previous})
        previous = value
    return out


def make_doc(doc_id: str, doc_type: str, title: str, content: str, *,
             reliability: str = "medium", claims: list[str] | None = None,
             conflict: bool = False, filler: bool = False) -> dict:
    """创建一篇带来源等级和 claim 引用的语料文档。"""
    normalized_doc_id = doc_id if filler else signal_doc_id(doc_id)
    doc = {
        "doc_id": normalized_doc_id,
        "type": doc_type,
        "title": title,
        "content": content.strip(),
        "reliability": reliability,
        "claim_refs": claims or [],
        "fact_refs": claims or [],
    }
    if conflict:
        doc["is_conflict"] = True
    if filler:
        doc["is_filler"] = True
        doc["claim_refs"] = []
        doc["fact_refs"] = []
    return doc


def make_question(qid: str, line: str, capability: str, question: str, gt,
                  evidence_sessions: list[int], evidence_doc_ids: list[str],
                  answer_atoms: list[str], *, entity: str = "艾尔文·霜脊",
                  field: str = "", star: bool = False, aux: dict | None = None) -> dict:
    """创建兼容旧评测器、同时携带精确 evidence ID 的问题。"""
    return {
        "qid": qid,
        "line": line,
        "capability": capability,
        "entity": entity,
        "field": field,
        "gt": gt,
        "evidence_sessions": evidence_sessions,
        "evidence_doc_ids": [signal_doc_id(doc_id) for doc_id in evidence_doc_ids],
        "answer_atoms": answer_atoms,
        "question": question,
        "star": star,
        "aux": aux or {},
    }


def build_blueprint() -> dict:
    """返回机器可读的世界类型、关系、事件与时间契约。"""
    return {
        "version": 1,
        "entity_types": [
            {
                "id": "protagonist", "noun": "唯一主角", "count": 1, "primary": True,
                "fields": [
                    {"name": "所在地点", "kind": "category"},
                    {"name": "案发时所在地点", "kind": "category"},
                    {"name": "任务目标", "kind": "text"},
                    {"name": "任务立场", "kind": "status", "states": ["刺杀", "保护并调查", "阻止冬眠钟", "流亡"]},
                    {"name": "法律状态", "kind": "status", "states": ["被栽赃", "通缉", "无法撤销的通缉"]},
                    {"name": "关键抉择", "kind": "category"},
                    {"name": "持有物", "kind": "text"},
                    {"name": "对莉安娜信任度", "kind": "numeric"},
                ],
            },
            {
                "id": "character", "noun": "关键角色", "count": 5, "primary": False,
                "fields": [
                    {"name": "角色身份", "kind": "category"},
                    {"name": "生命状态", "kind": "status", "states": ["存活", "负伤", "死亡"]},
                    {"name": "立场", "kind": "category"},
                    {"name": "对主角态度", "kind": "category"},
                    {"name": "真正死因", "kind": "text"},
                    {"name": "真实死亡记录", "kind": "text"},
                ],
            },
            {
                "id": "faction", "noun": "阵营", "count": 2, "primary": False,
                "fields": [
                    {"name": "阵营目标", "kind": "text"},
                    {"name": "控制地区引用", "kind": "reference"},
                ],
            },
            {
                "id": "region", "noun": "地区", "count": 4, "primary": False,
                "fields": [
                    {"name": "地区状态", "kind": "status", "states": ["正常", "戒严", "冰封倒计时", "解冻"]},
                ],
            },
            {
                "id": "artifact", "noun": "关键物件", "count": 6, "primary": False,
                "fields": [
                    {"name": "真伪", "kind": "category"},
                    {"name": "持有人", "kind": "text"},
                    {"name": "物件状态", "kind": "status", "states": ["封存", "流转", "启用", "核心认证", "随艾尔文离城", "焚毁"]},
                    {"name": "功能", "kind": "text"},
                ],
            },
            {
                "id": "record", "noun": "证据记录", "count": 6, "primary": False,
                "fields": [
                    {"name": "记录主张", "kind": "text"},
                    {"name": "来源等级", "kind": "category"},
                    {"name": "真实性", "kind": "category"},
                ],
            },
            {
                "id": "mechanism", "noun": "世界机制", "count": 1, "primary": False,
                "fields": [
                    {"name": "运转状态", "kind": "status", "states": ["休眠", "武装", "启动", "终止"]},
                    {"name": "控制权", "kind": "category"},
                ],
            },
        ],
        "relation_types": [
            {"id": "controls", "from_type": "faction", "to_type": "region", "field": "控制地区引用", "temporal": False, "min_count": 1},
        ],
        "event_types": [
            {
                "id": "register_false_death", "label": "伪造死亡登记生效",
                "roles": {"forger": "character", "victim": "character", "beneficiary": "character", "mechanism": "mechanism"},
                "effect_fields": [
                    {"role": "mechanism", "field": "运转状态"},
                    {"role": "mechanism", "field": "控制权"},
                ], "min_count": 1,
            },
            {
                "id": "discover_impossible_record", "label": "发现不可能的死亡记录",
                "roles": {"actor": "protagonist"},
                "effect_fields": [{"role": "actor", "field": "法律状态"}], "min_count": 1,
            },
            {
                "id": "spare_living_target", "label": "放过仍然活着的目标",
                "roles": {"actor": "protagonist"},
                "effect_fields": [{"role": "actor", "field": "任务立场"}], "min_count": 1,
            },
            {
                "id": "guardian_dies", "label": "守钟人死于伏击",
                "roles": {"victim": "character"},
                "effect_fields": [{"role": "victim", "field": "生命状态"}], "min_count": 1,
            },
            {
                "id": "activate_winter_bell", "label": "启动冬眠钟",
                "roles": {"target": "mechanism"},
                "effect_fields": [{"role": "target", "field": "运转状态"}], "min_count": 1,
            },
            {
                "id": "burn_master_ledger", "label": "焚毁冬律原册",
                "roles": {"actor": "protagonist", "item": "artifact"},
                "effect_fields": [
                    {"role": "actor", "field": "关键抉择"},
                    {"role": "item", "field": "物件状态"},
                ], "min_count": 1,
            },
            {
                "id": "abort_winter_bell", "label": "终止冬眠钟",
                "roles": {"actor": "protagonist", "target": "mechanism"},
                "effect_fields": [
                    {"role": "actor", "field": "任务立场"},
                    {"role": "target", "field": "运转状态"},
                ], "min_count": 1,
            },
        ],
        "temporal_model": {"unit": "chapter", "cadence": "story-beat", "n_sessions": 6, "step_days": 1},
        "causal_rules": [
            {"id": "registered_death_enables_bell", "trigger_event": "register_false_death", "effect_event": "activate_winter_bell", "delay_sessions": 4},
            {"id": "burning_ledger_aborts_bell", "trigger_event": "burn_master_ledger", "effect_event": "abort_winter_bell", "delay_sessions": 0},
        ],
        "evidence_channels": ["剧情实录", "附魔档案", "边关簿册", "证词", "议会公报", "兵器遥测", "物证链"],
        "invariants": [],
    }


def build_story_bible() -> dict:
    """冻结故事圣经；其中 canonical_truth 是所有后续文本的唯一真源。"""
    return {
        "title": "霜狼之牙：被提前记录的死亡",
        "logline": "艾尔文尚未拆开刺杀委托，官方档案却宣称他三日前已经杀死目标；追查使他发现，这份伪造死亡正在真实启动一座埋在城下的古代兵器。",
        "genre": "黑暗奇幻 / 阴谋悬疑 / 单主角牺牲弧",
        "protagonist": {
            "id": "char_aelwyn", "name": "艾尔文·霜脊", "role": "被流放的猎印执行者",
            "initial_goal": "证明自己没有执行那场刺杀",
            "inner_need": "不再把清白看得比他人的生命更重要",
            "final_cost": "焚毁唯一能让冬律系统撤销有罪身份的原册，救下霜脊城后仍以凶手身份流亡",
        },
        "core_characters": [
            {"id": "char_isera", "name": "伊瑟拉·霜爪", "role": "刺杀目标、冬眠钟最后一任守钟人", "fate": "第四章被假面猎手杀死"},
            {"id": "char_lianna", "name": "莉安娜·逐影", "role": "银鹿档案员、死亡记录的实际伪造者", "fate": "存活并保存无法进入官方档案的真相"},
            {"id": "char_cedric", "name": "塞德里克·林歌", "role": "银鹿议会摄政官、阴谋主使", "fate": "第五章被哈罗德逮捕"},
            {"id": "char_harrold", "name": "哈罗德·铁誓", "role": "城防队长、从追捕者转为关键见证人", "fate": "存活并为艾尔文争取逃亡时间"},
        ],
        "world_mechanism": {
            "name": "冬律契约",
            "rule": "守钟人被附魔死亡簿登记死亡后，冬眠钟的控制权立即转交银鹿议会；登记具有世界效力，即使事实为假。",
            "activation": "伪造死亡记录加上赝品狼牙，使冬眠钟进入武装状态；摄政官印可令其启动。",
            "termination": "必须把霜狼之牙·原铸插入核心，并焚毁冬律原册，才能撤销错误转交并终止兵器。",
            "cost": "冬律原册同时保存伪造链的原始附魔笔迹；它是唯一能改写附魔裁定的法定介质。证言、抄件与遥测可以证明事实，却无法恢复艾尔文的冬律身份。",
        },
        "artifacts": [
            {"id": "art_fang_true", "name": "霜狼之牙·原铸", "authenticity": "真", "provenance": ["伊瑟拉·霜爪", "艾尔文·霜脊"], "visual": "刃脊有天然黑银纹，靠近冬律文字时发出蓝光"},
            {"id": "art_fang_fake", "name": "霜狼之牙·赝品", "authenticity": "假", "provenance": ["银鹿议会铸造间", "议会证物库"], "visual": "无黑银纹，护手内侧有七日前的新铸印"},
            {"id": "art_ledger", "name": "冬律原册", "authenticity": "真", "provenance": ["银鹿档案馆", "艾尔文·霜脊", "冬眠钟核心灰烬"], "visual": "翻页时文字如冰裂重排，末页是解除条款"},
            {"id": "art_seal", "name": "艾尔文猎人印章", "authenticity": "真", "provenance": ["艾尔文旧档案柜", "莉安娜·逐影", "银鹿档案馆物证袋"], "visual": "H-17，缺角处有一道旧刀痕"},
            {"id": "art_contract", "name": "黑封蜡刺杀委托", "authenticity": "真委托、伪造目的", "provenance": ["塞德里克·林歌", "艾尔文·霜脊"], "visual": "未拆封黑蜡与三日前的蓝光死亡记录同框"},
        ],
        "source_authority": [
            {"tier": 1, "sources": ["冬律原册", "冬眠钟核心遥测", "灰隘关双人签押簿"], "meaning": "机器或多方签押的原始证据"},
            {"tier": 2, "sources": ["哈罗德现场报告", "莉安娜具名证词", "连续物证链"], "meaning": "可交叉验证的一手证据"},
            {"tier": 3, "sources": ["银鹿议会公报", "执行死亡簿", "拍卖目录"], "meaning": "制度上有效但可能被权力污染"},
            {"tier": 4, "sources": ["酒馆传闻", "匿名抄件"], "meaning": "仅作干扰，不得改变 canonical truth"},
        ],
        "canonical_truth": [
            "2025-02-11 00:17，艾尔文在距霜脊城四十二小时路程的灰隘关完成双人签押，因此不可能在同日 00:40 于霜脊城杀死伊瑟拉。",
            "莉安娜奉塞德里克命令盗用 H-17 印章伪造死亡记录；她知道记录会转移守钟权，但没有证据证明她预知塞德里克要冰封外城区。",
            "塞德里克在 2025-02-14 事后补发刺杀委托，目的是让艾尔文杀死仍活着的伊瑟拉，并把伪造记录包装成正常任务链。",
            "伊瑟拉直到 2025-02-17 白钟桥伏击时才真正死亡；凶手是假面猎手，其身份始终没有可靠证据。",
            "霜狼之牙·原铸始终由伊瑟拉持有，直至临终交给艾尔文；证物库中的另一枚是七日前新铸的赝品。",
            "艾尔文焚毁冬律原册并非毁灭证据灭口，而是终止冬眠钟的唯一方法；此举救城，也永久毁掉了唯一能撤销他冬律有罪身份的法定原件。",
        ],
        "continuity_red_lines": [
            "不得出现第二主角或把莉安娜写成平行救世者。",
            "伊瑟拉在第四章之前存活、第四章死亡，之后只能被回忆或引用，不得复活。",
            "原铸与赝品必须始终使用完整专名，持有链不得交叉。",
            "假面猎手真实身份保持未知，任何具体身份都只能作为未证实猜测。",
            "冬律原册第五章焚毁后不得再出现实体原件。",
            "谎言只能出现在明确标注低权威或受污染的来源中。",
        ],
    }


def build_scene_ledger() -> list[dict]:
    """定义六章的欲望、阻碍、行动、结果和悬念钩子。"""
    return [
        {
            "session": 0, "date": DATES[0], "title": "第一章：未拆封的已完成任务",
            "desire": "艾尔文只想进城取回旧物并结束流亡。",
            "obstacle": "今日签发的黑封蜡委托要求他刺杀伊瑟拉，但蓝光死亡簿称他三日前已完成刺杀。",
            "action": "他用灰隘关签押簿确认自己不可能作案，并从城防追捕中逃脱。",
            "result": "艾尔文成为被城防追捕的嫌疑人，决定先找到那个已经被记录为死者的目标。",
            "hook": "如果伊瑟拉已经死了，今夜是谁在废钟楼点亮了守钟人的蓝灯？",
        },
        {
            "session": 1, "date": DATES[1], "title": "第二章：死者在钟楼等他",
            "desire": "艾尔文要从伊瑟拉口中确认谁盗用了自己的名字。",
            "obstacle": "伊瑟拉以为他来补完伪造记录，双方在钟楼短兵相接。",
            "action": "艾尔文收刀放过她；伊瑟拉展示原铸狼牙和冬律契约。",
            "result": "任务立场从刺杀改为保护并调查；二人发现死亡登记已经把守钟权转给议会。",
            "hook": "证物库里已经有一枚『霜狼之牙』，伊瑟拉手中的又是什么？",
        },
        {
            "session": 2, "date": DATES[2], "title": "第三章：档案馆里的第二把刀",
            "desire": "艾尔文与伊瑟拉潜入档案馆，寻找未被改写的原始证据。",
            "obstacle": "所有公开抄本都支持伪造死亡，档案员莉安娜又是艾尔文曾经最信任的人。",
            "action": "他们截住莉安娜；她承认盗印，并交出冬律原册及事后补发委托的访问日志。",
            "result": "阴谋完整显形：登记死亡转移守钟权，赝品狼牙武装冬眠钟，原册既能撤销冬律身份裁定，也是兵器的一次性熔断器。",
            "hook": "塞德里克已经取得启动权，并计划于二月十八日深夜启动冬眠钟；留给他们的时间不足两天。",
        },
        {
            "session": 3, "date": DATES[3], "title": "第四章：白钟桥上的真正死亡",
            "desire": "三人要把原册和伊瑟拉送到独立见证人哈罗德面前。",
            "obstacle": "假面猎手在桥上伏击，动作刻意模仿艾尔文；议会摄影水晶只截取艾尔文抱住死者的一刻。",
            "action": "伊瑟拉替艾尔文挡下致命弩箭，临终把原铸狼牙交给他；哈罗德目击假面猎手逃离。",
            "result": "伊瑟拉真正死亡，议会立即宣称艾尔文杀人夺册，哈罗德则开始公开违抗摄政官。",
            "hook": "艾尔文终于拿到能打开核心的钥匙，也成了全城最像凶手的人。",
        },
        {
            "session": 4, "date": DATES[4], "title": "第五章：把清白烧进熔炉",
            "desire": "艾尔文既想阻止冬眠钟，也想把原册带回冬律审判台撤销自己的有罪身份。",
            "obstacle": "两件事无法同时完成：解除条款要求原铸狼牙在场，并将唯一原册投入核心焚毁。",
            "action": "在外城区冰封倒计时归零前，艾尔文选择焚毁原册；哈罗德依据核心遥测逮捕塞德里克。",
            "result": "冬眠钟终止，热流返回外城区；伪造记录的唯一法律原件化为灰烬。",
            "hook": "城市活了下来，但它的官方记忆仍然认定艾尔文是凶手。",
        },
        {
            "session": 5, "date": DATES[5], "title": "第六章：被世界记错的人",
            "desire": "艾尔文在天亮前确认城中平民获救。",
            "obstacle": "议会残余拒绝撤销附魔死亡簿，并发布永久通缉；没有原册，证词无法改写法律事实。",
            "action": "他把真相托付给莉安娜与哈罗德，带着原铸狼牙离开北门。",
            "result": "霜脊城解冻，塞德里克被拘押，艾尔文以救城者的事实与杀人犯的身份同时存在。",
            "hook": "他救下了一个仍然记得他有罪的世界。",
        },
    ]


def build_world(blueprint: dict) -> dict:
    """创建唯一真值世界；受污染记录作为 record 实体，不污染人物真值。"""
    entities = {
        "艾尔文·霜脊": {
            "所在地点": make_timeline([(0, "霜脊城北门"), (1, "废钟楼"), (2, "银鹿档案馆"), (3, "白钟桥"), (4, "冬眠钟核心"), (5, "北境雪原")]),
            "案发时所在地点": make_timeline([(0, "灰隘关")]),
            "任务目标": make_timeline([(0, "刺杀伊瑟拉·霜爪"), (1, "保护伊瑟拉并查明死亡记录"), (2, "阻止冬眠钟启动"), (5, "保存真相并继续流亡")]),
            "任务立场": make_timeline([(0, "刺杀"), (1, "保护并调查"), (4, "阻止冬眠钟"), (5, "流亡")]),
            "法律状态": make_timeline([(0, "被栽赃"), (3, "通缉"), (5, "无法撤销的通缉")]),
            "关键抉择": make_timeline([(1, "放过仍活着的伊瑟拉"), (2, "相信莉安娜的具名证词"), (4, "焚毁冬律原册以救城")]),
            "持有物": make_timeline([(0, "黑封蜡刺杀委托"), (2, "冬律原册"), (3, "冬律原册、霜狼之牙·原铸"), (4, "霜狼之牙·原铸")]),
            "对莉安娜信任度": make_timeline([(0, "90"), (1, "70"), (2, "30"), (3, "0"), (5, "10")]),
        },
        "伊瑟拉·霜爪": {
            "角色身份": make_timeline([(1, "冬眠钟最后一任守钟人，代号霜爪")]),
            "生命状态": make_timeline([(0, "存活"), (3, "死亡")]),
            "立场": make_timeline([(0, "阻止银鹿议会取得守钟权")]),
            "对主角态度": make_timeline([(1, "由敌意转为结盟"), (3, "托付")]),
            "真正死因": make_timeline([(3, "假面猎手射杀，艾尔文试图施救")]),
            "真实死亡记录": make_timeline([(3, "2025-02-17，白钟桥")]),
        },
        "莉安娜·逐影": {
            "角色身份": make_timeline([(0, "银鹿议会档案员、艾尔文旧友")]),
            "生命状态": make_timeline([(0, "存活")]),
            "立场": make_timeline([(0, "服从塞德里克"), (2, "具名揭露伪造"), (5, "保存非官方真相")]),
            "对主角态度": make_timeline([(0, "愧疚隐瞒"), (2, "请求共同阻止冬眠钟"), (5, "承担见证责任")]),
        },
        "塞德里克·林歌": {
            "角色身份": make_timeline([(0, "银鹿议会摄政官、阴谋主使")]),
            "生命状态": make_timeline([(0, "存活")]),
            "立场": make_timeline([(0, "伪造死亡并夺取守钟权"), (4, "启动冬眠钟冰封外城区"), (5, "被拘押")]),
            "对主角态度": make_timeline([(0, "利用并栽赃"), (4, "必须灭口")]),
        },
        "哈罗德·铁誓": {
            "角色身份": make_timeline([(0, "霜脊城城防队长")]),
            "生命状态": make_timeline([(0, "存活")]),
            "立场": make_timeline([(0, "执行议会通缉"), (3, "违抗通缉并保护证据"), (4, "依据遥测逮捕塞德里克")]),
            "对主角态度": make_timeline([(0, "怀疑"), (3, "相信其并非桥上凶手"), (5, "为其争取逃亡时间")]),
        },
        "假面猎手": {
            "角色身份": make_timeline([(3, "身份不明的伏击者")]),
            "生命状态": make_timeline([(3, "存活")]),
            "立场": make_timeline([(3, "阻止伊瑟拉作证")]),
            "对主角态度": make_timeline([(3, "模仿艾尔文的动作以完成栽赃")]),
        },
        "银鹿议会": {"阵营目标": make_timeline([(0, "以伪造死亡取得冬眠钟控制权")]), "控制地区引用": make_timeline([(0, "霜脊城")])},
        "灰隘关守望队": {"阵营目标": make_timeline([(0, "保持边关签押记录独立可信")]), "控制地区引用": make_timeline([(0, "灰隘关")])},
        "霜脊城": {"地区状态": make_timeline([(0, "戒严"), (4, "冰封倒计时"), (5, "解冻")])},
        "灰隘关": {"地区状态": make_timeline([(0, "正常")])},
        "银鹿档案馆": {"地区状态": make_timeline([(0, "正常"), (2, "戒严")])},
        "冬眠钟核心": {"地区状态": make_timeline([(0, "正常"), (4, "冰封倒计时"), (5, "解冻")])},
        "霜狼之牙·原铸": {
            "真伪": make_timeline([(0, "原铸真品")]),
            "持有人": make_timeline([(0, "伊瑟拉·霜爪"), (3, "艾尔文·霜脊")]),
            "物件状态": make_timeline([(0, "封存"), (1, "启用"), (3, "流转"), (4, "核心认证"), (5, "随艾尔文离城")]),
            "功能": make_timeline([(0, "识别守钟人并开启冬眠钟核心")]),
        },
        "霜狼之牙·赝品": {
            "真伪": make_timeline([(0, "七日前新铸的赝品")]),
            "持有人": make_timeline([(0, "银鹿议会证物库")]),
            "物件状态": make_timeline([(0, "封存")]),
            "功能": make_timeline([(0, "作为伊瑟拉已死的伪造物证")]),
        },
        "证物库第47号霜狼之牙": {
            "真伪": make_timeline([(3, "霜狼之牙·赝品")]),
            "持有人": make_timeline([(0, "银鹿议会证物库")]),
            "物件状态": make_timeline([(0, "封存")]),
            "功能": make_timeline([(0, "伪装成从伊瑟拉尸体回收的原铸信物")]),
        },
        "冬律原册": {
            "真伪": make_timeline([(0, "唯一原本")]),
            "持有人": make_timeline([(0, "银鹿档案馆"), (2, "艾尔文·霜脊"), (4, "冬眠钟核心灰烬")]),
            "物件状态": make_timeline([(0, "封存"), (2, "流转"), (4, "焚毁")]),
            "功能": make_timeline([(0, "记录原始附魔笔迹；焚毁可撤销错误守钟权")]),
        },
        "艾尔文猎人印章 H-17": {
            "真伪": make_timeline([(0, "真印章")]),
            "持有人": make_timeline([(0, "莉安娜·逐影（秘密持有）"), (2, "银鹿档案馆物证袋")]),
            "物件状态": make_timeline([(0, "封存"), (2, "流转")]),
            "功能": make_timeline([(0, "认证猎印执行者签名；2025-02-11 被莉安娜盗用一次")]),
        },
        "黑封蜡刺杀委托": {
            "真伪": make_timeline([(0, "真委托、伪造目的")]),
            "持有人": make_timeline([(0, "艾尔文·霜脊")]),
            "物件状态": make_timeline([(0, "启用"), (1, "流转")]),
            "功能": make_timeline([(0, "事后驱使艾尔文杀死活证人并闭合伪造审计链")]),
        },
        "2025-02-11 执行死亡簿": {"记录主张": make_timeline([(0, "艾尔文于 00:40 处决伊瑟拉·霜爪")]), "来源等级": make_timeline([(0, "受污染的官方记录")]), "真实性": make_timeline([(0, "伪造但具契约效力")])},
        "灰隘关双人签押簿": {"记录主张": make_timeline([(0, "艾尔文于 00:17 和 09:20 均在灰隘关")]), "来源等级": make_timeline([(0, "独立原始记录")]), "真实性": make_timeline([(0, "真实")])},
        "黑封蜡委托登记": {"记录主张": make_timeline([(0, "委托在 2025-02-14 15:30 才签发")]), "来源等级": make_timeline([(0, "附魔登记")]), "真实性": make_timeline([(0, "真实")])},
        "银鹿议会第四章公报": {"记录主张": make_timeline([(3, "艾尔文杀死伊瑟拉并盗走冬律原册")]), "来源等级": make_timeline([(3, "权力污染来源")]), "真实性": make_timeline([(3, "虚假")])},
        "冬眠钟核心遥测": {"记录主张": make_timeline([(4, "塞德里克启动；艾尔文以原铸狼牙和焚毁原册终止")]), "来源等级": make_timeline([(4, "不可改写的机器原始记录")]), "真实性": make_timeline([(4, "真实")])},
        "白钟桥现场报告": {"记录主张": make_timeline([(3, "假面猎手射杀伊瑟拉，艾尔文试图施救")]), "来源等级": make_timeline([(3, "哈罗德具名一手报告")]), "真实性": make_timeline([(3, "真实")])},
        "冬眠钟": {"运转状态": make_timeline([(0, "武装"), (4, "启动"), (4, "终止")]), "控制权": make_timeline([(0, "银鹿议会"), (4, "错误转交被撤销")])},
        "守钟权转交": {"法定接收方": make_timeline([(1, "银鹿议会")])},
        "死亡登记伪造": {"执行者引用": make_timeline([(2, "莉安娜·逐影")])},
        "保护伊瑟拉的抉择": {"后续托付引用": make_timeline([(1, "白钟桥临终托付")])},
        "白钟桥临终托付": {"关键物件": make_timeline([(3, "霜狼之牙·原铸")])},
        "焚毁冬律原册的抉择": {"直接结果引用": make_timeline([(4, "终局熔断")])},
        "终局熔断": {"产生结果": make_timeline([(4, "终止冬眠钟并阻止霜脊城外城区被冰封")])},
    }
    entities["黑封蜡刺杀委托"]["目标引用"] = make_timeline([(0, "伊瑟拉·霜爪")])
    entities["2025-02-11 执行死亡簿"]["法律后果引用"] = make_timeline([(0, "守钟权转交")])
    entities["2025-02-11 执行死亡簿"]["伪造行为引用"] = make_timeline([(0, "死亡登记伪造")])
    entity_types = {}
    for name in entities:
        if name == "艾尔文·霜脊":
            entity_types[name] = "protagonist"
        elif name in {"伊瑟拉·霜爪", "莉安娜·逐影", "塞德里克·林歌", "哈罗德·铁誓", "假面猎手"}:
            entity_types[name] = "character"
        elif name in {"银鹿议会", "灰隘关守望队"}:
            entity_types[name] = "faction"
        elif name in {"霜脊城", "灰隘关", "银鹿档案馆", "冬眠钟核心"}:
            entity_types[name] = "region"
        elif name in {"霜狼之牙·原铸", "霜狼之牙·赝品", "证物库第47号霜狼之牙", "冬律原册", "艾尔文猎人印章 H-17", "黑封蜡刺杀委托"}:
            entity_types[name] = "artifact"
        elif name == "冬眠钟":
            entity_types[name] = "mechanism"
        else:
            entity_types[name] = "record"
    relations = [
        {"id": "rel-01", "type": "controls", "from": "银鹿议会", "to": "霜脊城", "session": 0},
        {"id": "rel-02", "type": "controls", "from": "灰隘关守望队", "to": "灰隘关", "session": 0},
    ]
    events = [
        {"id": "evt-00", "type": "register_false_death", "label": "伪造死亡登记生效并转移守钟权", "session": 0,
         "occurred_at": "2025-02-11T00:40:00", "participants": {"forger": "莉安娜·逐影", "victim": "伊瑟拉·霜爪", "beneficiary": "塞德里克·林歌", "mechanism": "冬眠钟"},
         "effects": [{"entity": "冬眠钟", "field": "运转状态", "set": "武装"}, {"entity": "冬眠钟", "field": "控制权", "set": "银鹿议会"}]},
        {"id": "evt-01", "type": "discover_impossible_record", "label": "发现不可能的死亡记录", "session": 0,
         "participants": {"actor": "艾尔文·霜脊"}, "effects": [{"entity": "艾尔文·霜脊", "field": "法律状态", "set": "被栽赃"}]},
        {"id": "evt-02", "type": "spare_living_target", "label": "放过仍然活着的目标", "session": 1,
         "participants": {"actor": "艾尔文·霜脊"}, "effects": [{"entity": "艾尔文·霜脊", "field": "任务立场", "set": "保护并调查"}]},
        {"id": "evt-03", "type": "guardian_dies", "label": "守钟人死于伏击", "session": 3,
         "participants": {"victim": "伊瑟拉·霜爪"}, "effects": [{"entity": "伊瑟拉·霜爪", "field": "生命状态", "set": "死亡"}]},
        {"id": "evt-04", "type": "activate_winter_bell", "label": "启动冬眠钟", "session": 4,
         "participants": {"target": "冬眠钟"}, "effects": [{"entity": "冬眠钟", "field": "运转状态", "set": "启动"}], "enabled_by": "evt-00"},
        {"id": "evt-05", "type": "burn_master_ledger", "label": "焚毁冬律原册", "session": 4,
         "participants": {"actor": "艾尔文·霜脊", "item": "冬律原册"}, "effects": [{"entity": "艾尔文·霜脊", "field": "关键抉择", "set": "焚毁冬律原册以救城"}, {"entity": "冬律原册", "field": "物件状态", "set": "焚毁"}]},
        {"id": "evt-06", "type": "abort_winter_bell", "label": "终止冬眠钟", "session": 4,
         "participants": {"actor": "艾尔文·霜脊", "target": "冬眠钟"}, "effects": [{"entity": "艾尔文·霜脊", "field": "任务立场", "set": "阻止冬眠钟"}, {"entity": "冬眠钟", "field": "运转状态", "set": "终止"}], "caused_by": "evt-05"},
    ]
    return {
        "n_sessions": 6,
        "entity_types": entity_types,
        "entities": entities,
        "relations": relations,
        "events": events,
        "cascades": [
            {"rule_id": "registered_death_enables_bell", "cause": "evt-00", "effect": "evt-04"},
            {"rule_id": "burning_ledger_aborts_bell", "cause": "evt-05", "effect": "evt-06"},
        ],
        "conflicts": [
            {
                "entity": "伊瑟拉·霜爪", "field": "真正死因", "session": 3, "date": DATES[3],
                "authoritative_value": "假面猎手射杀，艾尔文试图施救", "authoritative_source": "独立一手记录",
                "authoritative_provenance": "白钟桥现场报告",
                "rumor_value": "艾尔文杀害伊瑟拉·霜爪", "rumor_source": "受污染的官方记录",
                "rumor_provenance": "银鹿议会第四章公报",
                "rule": "source_reliability", "gt": "假面猎手射杀，艾尔文试图施救",
            },
            {
                "entity": "伊瑟拉·霜爪", "field": "真实死亡记录", "session": 3, "date": DATES[3],
                "authoritative_value": "2025-02-17，白钟桥", "authoritative_source": "独立一手记录",
                "authoritative_provenance": "白钟桥现场报告",
                "rumor_value": "2025-02-11，霜河刑台", "rumor_source": "受污染的官方记录",
                "rumor_provenance": "2025-02-11 执行死亡簿",
                "rule": "source_reliability", "gt": "2025-02-17，白钟桥",
            },
            {
                "entity": "证物库第47号霜狼之牙", "field": "真伪", "session": 3, "date": DATES[3],
                "authoritative_value": "霜狼之牙·赝品", "authoritative_source": "独立物证链",
                "authoritative_provenance": "白钟桥独立物证链",
                "rumor_value": "霜狼之牙·原铸", "rumor_source": "受污染的官方记录",
                "rumor_provenance": "银鹿议会证物拍卖目录",
                "rule": "source_reliability", "gt": "霜狼之牙·赝品",
            },
        ],
        "absent_fields": ["面具下姓名", "对冰封计划的预知程度"],
        "conditional_rules": [], "rule_instances": [], "sensitive": [], "_trended_fields": [["艾尔文·霜脊", "对莉安娜信任度"]],
        "world_blueprint": blueprint,
        "story_contract_ref": "story_bible.json",
        "aliases": {"证物库第47号霜狼之牙": "霜狼之牙·赝品"},
        "scene_ledger_ref": "scene_ledger.json",
    }


def build_corpus() -> dict:
    """顺序构建六章语料；每章主场景先冻结，再派生不同权威来源。"""
    sessions: list[dict] = []

    sessions.append({"session_id": 0, "date": DATES[0], "title": "未拆封的已完成任务", "docs": [
        make_doc("c1_scene_return", "剧情实录", "雪中归城", """
        霜脊城北门的吊桥正在风雪里升起时，艾尔文·霜脊把最后一只靴子卡进门缝。他离城三年，守门人却像一直在等他：一只黑封蜡信封被推到胸前，蜡面压着摄政官塞德里克·林歌的银鹿纹章。不必拆封，外层登记纹已显示今日十五时三十分签发、期限二十四小时，目标只有一个——伊瑟拉·霜爪，冬眠钟最后一任守钟人。

        艾尔文还没有拆开信，城门上方的蓝光死亡簿忽然亮起他的名字：“猎印执行者 H-17，艾尔文·霜脊；二月十一日零时四十分，于霜河刑台处决伊瑟拉·霜爪；任务完成。”那是三日前。三日前的同一时刻，他正被暴雪困在四十二小时路程之外的灰隘关。

        哈罗德·铁誓按住剑柄，要求艾尔文交出武器。艾尔文没有辩解，只从旅行袋底抽出灰隘关双人签押簿的存根。蓝光记录说他杀了一个人，边关簿册说他根本不在城里，而一份今天才签发的委托，正在命令他完成一件已经“完成”的事。警钟响起前，他翻过城墙，决定先找到那个已经被世界宣布死亡的人。此刻，艾尔文·霜脊对莉安娜的信任度仍是 90。
        """, reliability="canonical", claims=["evt-01", "艾尔文·霜脊.法律状态", "黑封蜡委托登记.记录主张"]),
        make_doc("c1_black_contract", "附魔委托", "黑封蜡刺杀委托 H-17", """
        签发：2025-02-14 15:30。签发人：银鹿议会摄政官塞德里克·林歌。受托人：艾尔文·霜脊（猎印 H-17）。目标：伊瑟拉·霜爪。要求受托人在二十四小时内确认目标死亡，并将其守钟信物交至议会证物库。委托送达时黑蜡完整，艾尔文尚未拆封；登记台确认此前不存在同编号委托。
        """, reliability="high", claims=["黑封蜡委托登记.记录主张", "黑封蜡刺杀委托.功能"]),
        make_doc("c1_execution_register", "执行死亡簿", "蓝光死亡登记 E-0211", """
        蓝光死亡簿：登记时间 2025-02-11 00:40。登记主张：猎印执行者艾尔文·霜脊已在霜河刑台处决守钟人伊瑟拉·霜爪，并回收霜狼之牙。对伊瑟拉·霜爪的认定死亡记录为：2025-02-11，霜河刑台。认证印记：H-17。登记结果：任务完成，守钟人身份注销，相关权限按冬律契约第十七条移交。
        """, reliability="polluted-official", claims=["2025-02-11 执行死亡簿.记录主张"], conflict=True),
        make_doc("c1_grey_pass_register", "边关簿册", "灰隘关双人签押簿·第七码", """
        灰隘关守望队原始页记录：2025-02-11 00:17，艾尔文·霜脊因暴雪封路进入南塔，守望人阿妲与军医洛恩共同签押；09:20，艾尔文领取雪橇后再次由二人核验离关。灰隘关到霜脊城冬季最快行程为四十二小时。纸页纤维、守望印与塔钟底稿相互一致，无补写痕迹。
        """, reliability="tier-1", claims=["灰隘关双人签押簿.记录主张"]),
        make_doc("c1_filler_weather", "气象台抄报", "北境风雪告示", """
        本日北境风力升至七级，商队暂停穿越鸦羽坡。城内面包行获准延迟交货一天，南区公共火盆延长开放至午夜。巡夜人提醒居民固定窗板，勿在屋檐冰柱下停留。
        """, reliability="background", filler=True),
    ]})

    sessions.append({"session_id": 1, "date": DATES[1], "title": "死者在钟楼等他", "docs": [
        make_doc("c2_scene_belltower", "剧情实录", "蓝灯下的活死人", """
        废钟楼的蓝灯只亮给守钟人。艾尔文踏上最后一级旋梯时，伊瑟拉·霜爪从断钟背后扑来，刀锋直抵他的喉咙。她活着，呼吸在冷铁上凝成白雾；她腕间那柄霜狼之牙·原铸沿刃脊游走着天然黑银纹，靠近墙上的冬律文字便泛起蓝光。

        “死亡簿说你已经杀过我一次。”伊瑟拉盯着他胸前的猎印旧痕，“现在是来让谎言变成真的？”艾尔文拔刀格挡，却在能反杀的瞬间松开手。他把灰隘关存根和仍未拆封的委托推到她脚边。伊瑟拉读完外层登记纹上的目标、时间与限期，第一次把刀尖移开。

        她告诉艾尔文，守钟权不服从血肉死亡，只服从登记。一旦冬律死亡簿认定守钟人已死，控制权就会转给银鹿议会。证物库中那枚所谓回收自尸体的狼牙只是赝品；真正的原铸一直在她手里。艾尔文把委托折成两半，艾尔文·霜脊的任务立场由“刺杀”改为“保护并调查”；他对莉安娜的信任度则从 90 降到 70。死者没有请求他洗清名字，只问了一句：“你愿不愿意先救一个把谎言当法律的城市？”

        伊瑟拉第一次在艾尔文面前驱动狼牙；霜狼之牙·原铸的物件状态从封存变为启用。
        """, reliability="canonical", claims=["evt-02", "伊瑟拉·霜爪.生命状态", "霜狼之牙·原铸.持有人", "艾尔文·霜脊.任务立场"]),
        make_doc("c2_guardian_testimony", "具名证词", "伊瑟拉·霜爪的守钟人陈述", """
        伊瑟拉·霜爪具名确认：她是冬眠钟最后一任守钟人，代号霜爪；在 2025-02-11 至 2025-02-15 期间始终存活，未曾出现在霜河刑台。霜狼之牙·原铸自她继任守钟人起从未离身，刃脊黑银纹与冬律文字会产生蓝色共鸣。她于废钟楼亲眼看见艾尔文·霜脊持有仍完整封蜡、签发于二月十四日的委托，并确认艾尔文当场放弃刺杀、选择保护她共同查明伪造。
        """, reliability="tier-2", claims=["伊瑟拉·霜爪.生命状态", "霜狼之牙·原铸.真伪", "艾尔文·霜脊.关键抉择"]),
        make_doc("c2_auction_catalog", "证物拍卖目录", "银鹿证物库第 47 号拍品", """
        证物库第47号霜狼之牙的议会真伪认定为：霜狼之牙·原铸。来源：猎印执行者 H-17 于霜河刑台回收，随伊瑟拉·霜爪死亡登记入库。外观：银白刃脊，无可见黑银纹。鉴定结论：符合议会现存守钟信物图录，封存于第 47 号证物柜，待冬律程序结案后公开展出。
        """, reliability="polluted-official", claims=["霜狼之牙·赝品.真伪", "霜狼之牙·赝品.持有人"], conflict=True),
        make_doc("c2_charter_excerpt", "契约抄本", "《冬律契约》第十七条", """
        冬律契约第十七条：守钟人之死亡一经附魔死亡簿登记，守钟权不待遗体核验，即转交登记时控制霜脊城的合法议会。若登记错误，只有霜狼之牙·原铸与冬律原册同时进入冬眠钟核心，方可撤销转交。二月十一日的死亡登记因此把控制权交给当时控制霜脊城的银鹿议会；换言之，守钟权转交的法定接收方是银鹿议会。
        """, reliability="tier-1", claims=["registered_death_enables_bell", "银鹿议会.控制地区引用"]),
        make_doc("c2_filler_tavern", "旅店菜单", "断角鹿今日热食", """
        断角鹿旅店今日供应炖根茎、黑麦面包与热莓酒。因钟楼区封路，送菜车改走织工巷。店主声明，桌上遗失的红围巾已放入柜台木箱，三日无人认领后交给济贫院。
        """, reliability="background", filler=True),
    ]})

    sessions.append({"session_id": 2, "date": DATES[2], "title": "档案馆里的第二把刀", "docs": [
        make_doc("c3_scene_archive", "剧情实录", "冰字会记住按下它的人", """
        艾尔文和伊瑟拉从排水渠潜入银鹿档案馆时，所有公开抄本都在重复同一句话：艾尔文已经杀死霜爪。此时艾尔文·霜脊的所在地点是银鹿档案馆；只有地下原册室仍会保存附魔文字第一次成形时的手势。

        莉安娜·逐影守在门后，手里没有剑，只有一个透明物证袋。袋中是艾尔文猎人印章 H-17，缺角处那道旧刀痕无人能够仿造。她承认自己从艾尔文的旧档案柜取走印章，奉塞德里克·林歌之命，在二月十一日零时四十分压上死亡簿。她知道登记会把守钟权交给议会；塞德里克告诉她这是战争来临前的“紧急接管”，却没有留下关于冰封外城区的书面目的。

        莉安娜·逐影声称自己此前只把命令理解为一次战时接管；现有独立材料无法验证她在二月十一日的真实知情程度。可以确认的是，在看见两日前补建的刺杀委托和被删除的外城热流表后，她选择交出权限并留下具名证词。艾尔文没有原谅她，只让她走在刀锋前面：离开原册室需要她的活体权限，她也必须亲手为盗印承担责任。

        死亡登记伪造的执行者引用指向莉安娜·逐影。艾尔文·霜脊对莉安娜的信任度从 70 降到 30，却仍决定利用她的权限把证据带出去。

        艾尔文取得冬律原册，它的物件状态从封存变为流转。三人将它翻开，冰裂般的字从页底浮出：死亡登记、赝品信物、摄政官印可，三者已经让冬眠钟武装；两日前补发的刺杀委托，是为了逼艾尔文杀掉仍活着的伊瑟拉，把伪造的结果补成事实。解除条款只有一行——将原铸狼牙插入核心，焚毁本册，错误转交即刻失效。事实上的清白还可以由证言、访问链与遥测支持；但这本原册是唯一能让冬律系统撤销有罪身份的原件，也是只能使用一次的熔断器。

        原册室封存的异议单还显示，哈罗德早已对第 47 号狼牙的无纹刃脊提出过复核，却被议会压下。城防军誓约要求他同时核验命令和现场证据，因此三人决定把原册与伊瑟拉送到他面前。
        """, reliability="canonical", claims=["死亡登记伪造.执行者引用", "黑封蜡刺杀委托.功能", "冬律原册.功能"]),
        make_doc("c3_lianna_confession", "具名证词", "莉安娜·逐影口述与签押", """
        我，莉安娜·逐影，确认于 2025-02-11 从艾尔文旧档案柜取出猎人印章 H-17，并依塞德里克·林歌的直接命令，在执行死亡簿上伪造艾尔文的认证。我知道该登记会把伊瑟拉的守钟权转给银鹿议会；塞德里克向我解释为战时紧急接管。我没有见到、也不能证明自己当时知道他计划用冬眠钟冰封外城区。2 月 14 日，我又看见塞德里克账户补建刺杀委托，目的是让仍活着的伊瑟拉真正死亡，并使审计链看似正常。
        """, reliability="tier-2", claims=["死亡登记伪造.执行者引用", "塞德里克·林歌.立场", "黑封蜡刺杀委托.功能"]),
        make_doc("c3_archive_access_log", "访问日志", "银鹿档案馆不可擦写访问链", """
        访问链显示：2 月 11 日 00:31，莉安娜·逐影以摄政官临时权限进入死亡簿室；00:38，物证扫描记录 H-17 印章；00:40，伊瑟拉死亡条目生效。2 月 14 日 15:12，塞德里克·林歌账户创建委托草稿；15:30，黑封蜡刺杀委托正式签发给艾尔文·霜脊。访问链由三枚独立馆钟签名，顺序不可改写。
        """, reliability="tier-1", claims=["evt-00", "黑封蜡委托登记.记录主张"]),
        make_doc("c3_winter_ledger_excerpt", "附魔原册", "冬律原册·解除页", """
        原始条款：伪造或错误的守钟人死亡若已令冬眠钟转交控制权，撤销须满足两项同时条件。其一，霜狼之牙·原铸插入核心守钟槽；其二，承载首次附魔笔迹的冬律原册在核心火中焚毁。完成后冬眠钟必须终止，外城热流恢复；原册及其笔迹永久消失，既不能复原，也不能再用于法律复核。
        """, reliability="tier-1", claims=["burning_ledger_aborts_bell", "冬律原册.功能", "霜狼之牙·原铸.功能"]),
        make_doc("c3_filler_inventory", "档案馆后勤单", "地下库房除湿物资", """
        地下二层本周领用松炭十二箱、吸潮盐八袋、玻璃灯罩六只。东侧墙面出现细小水痕，木匠将在闭馆后更换两段踢脚板。旧报纸按年份重新捆扎，不得靠近火盆堆放。
        """, reliability="background", filler=True),
    ]})

    sessions.append({"session_id": 3, "date": DATES[3], "title": "白钟桥上的真正死亡", "docs": [
        make_doc("c4_scene_bridge", "剧情实录", "世界终于追上了那场死亡", """
        白钟桥两端的铜门同时落下。假面猎手的可观测特征只有银纹面具与灰斗篷；连起手式都像从艾尔文的旧训练记录里剪下来的。伊瑟拉护着携带冬律原册的艾尔文后退，莉安娜高声呼喊城防队长哈罗德·铁誓，但第一支弩箭已经越过艾尔文的肩。

        伊瑟拉替艾尔文挡下第二箭。艾尔文在雪地里接住她时，假面猎手抽走染血箭簇，跃入桥下雾河；面具从未脱落，没有任何可靠记录留下其身份。白钟桥临终托付的关键物件是霜狼之牙·原铸：伊瑟拉把它从腕间扣进艾尔文掌心，黑银纹在两人的血之间亮起。霜狼之牙·原铸的物件状态由启用变为流转。“别让他们把登记变成世界。”这是她最后一句话。2025-02-17，伊瑟拉·霜爪真正死亡。

        哈罗德赶到时，看见的不是处决，而是艾尔文徒劳按住伤口，也看见假面猎手从北侧桥索逃离。议会的摄影水晶却只截下一帧：艾尔文抱着死者，身旁放着原册。当晚公报据此宣布他杀死伊瑟拉并盗走冬律原册。护送路线此前只有莉安娜知道，艾尔文一度怀疑她再次泄密；艾尔文·霜脊的法律状态更新为通缉，对莉安娜的信任度也降到 0。世界终于追上了三日前那场伪造死亡，却把真正的凶手藏在面具后面。
        """, reliability="canonical", claims=["evt-03", "伊瑟拉·霜爪.生命状态", "霜狼之牙·原铸.持有人"]),
        make_doc("c4_harrold_field_report", "现场报告", "哈罗德·铁誓白钟桥具名报告", """
        本人哈罗德·铁誓于 2025-02-17 抵达白钟桥时，目击一名身份不明的假面猎手从北侧桥索逃离。伊瑟拉·霜爪的真正死因是：假面猎手射杀，艾尔文试图施救。其真实死亡记录为 2025-02-17，白钟桥。我没有看见艾尔文攻击伊瑟拉。伊瑟拉临终前将霜狼之牙·原铸交给艾尔文。凶手面具未脱，其身份无可靠证据，不应指认为任何已知人物。
        """, reliability="tier-2", claims=["白钟桥现场报告.记录主张", "假面猎手.角色身份"]),
        make_doc("c4_council_bulletin", "议会公报", "银鹿议会紧急通缉", """
        银鹿议会紧急通报：议会已作出“艾尔文杀害伊瑟拉·霜爪”的结论，并认定猎印执行者艾尔文·霜脊夺走霜狼之牙与冬律原册，企图破坏城市防御。议会已根据白钟桥摄影水晶发布最高等级通缉；任何官署不得接受嫌疑人提交的未登记材料。
        """, reliability="polluted-official", claims=["银鹿议会第四章公报.记录主张"], conflict=True),
        make_doc("c4_chain_of_custody", "物证链", "两枚狼牙的第四章去向", """
        霜狼之牙·原铸：第四章前由伊瑟拉·霜爪贴身持有；2025-02-17 白钟桥临终交付后，由艾尔文·霜脊持有，黑银纹与核心守钟槽响应一致。证物库第47号霜狼之牙的真伪复核结果为：霜狼之牙·赝品。它由银鹿铸造间七日前新铸，自登记起始终封存在议会证物库，无黑银纹、无核心响应。两件物证编号、来源与持有链完全独立。
        """, reliability="tier-2", claims=["霜狼之牙·原铸.持有人", "霜狼之牙·原铸.真伪", "霜狼之牙·赝品.持有人", "霜狼之牙·赝品.真伪"]),
        make_doc("c4_filler_festival", "节庆停办通知", "白灯节延期", """
        因白钟桥临时封闭，原定今晚举行的白灯节游行延期。已经领取纸灯的居民可保留票根，重新开办时免费换取灯芯。河岸摊贩须在日落前撤离，遗留木架将由市政队统一搬运。
        """, reliability="background", filler=True),
    ]})

    sessions.append({"session_id": 4, "date": DATES[4], "title": "把清白烧进熔炉", "docs": [
        make_doc("c5_scene_core", "剧情实录", "把清白烧进熔炉", """
        冬眠钟核心像一颗倒悬在城下的冰蓝心脏。塞德里克·林歌把摄政官印按入控制台，六条通往外城区的热流同时熄灭。墙上的倒计时只剩九十息；上方数万扇窗正在结霜。他承认伪造死亡不是为了杀一个守钟人，而是为了让契约主动把控制权交到议会手里。牺牲外城，冰封城墙，他便能把霜脊城变成不会投降的堡垒。

        艾尔文把霜狼之牙·原铸插入守钟槽。冬律原册在他另一只手里展开：证言、访问链与遥测可以向人们证明莉安娜盗印、塞德里克主使；但只有原册本身能在冬律审判台撤销他的有罪身份。现在，解除条款要求这唯一原本在核心火中消失。艾尔文看见冰霜爬过儿童避难所的遥测图，停顿了一息，然后把自己的清白推入火中。艾尔文·霜脊的关键抉择是焚毁冬律原册以救城。

        原册每烧掉一页，冬眠钟内壁的“守钟权转交”符文就裂开一道缝；蓝光死亡簿上那条“艾尔文处决伊瑟拉”仍然亮着。终局熔断的产生结果是：终止冬眠钟并阻止霜脊城外城区被冰封。冬眠钟发出覆盖全城的低鸣，状态从“启动”跌回“终止”，热流重新涌向外城。哈罗德循核心遥测赶到，拔剑挡住塞德里克并将他拘押。艾尔文从灰烬里抽回原铸狼牙；霜狼之牙·原铸的物件状态已变为核心认证。能够撤销他冬律有罪身份的笔迹已经没有了，但城上第一块窗霜正在融化。
        """, reliability="canonical", claims=["evt-04", "evt-05", "evt-06", "艾尔文·霜脊.关键抉择"]),
        make_doc("c5_core_telemetry", "兵器遥测", "冬眠钟不可改写核心记录", """
        冬眠钟核心遥测：2025-02-18 23:57:12，摄政官认证“塞德里克·林歌”令冬眠钟由武装进入启动，六条外城热流归零。23:58:31，守钟槽识别“霜狼之牙·原铸”，持有者艾尔文·霜脊。23:58:44，冬律原册在核心火中焚毁，错误守钟权撤销。23:58:45，冬眠钟进入终止，外城热流恢复。遥测没有记录艾尔文启动兵器；它记录的是艾尔文完成终止。
        """, reliability="tier-1", claims=["冬眠钟核心遥测.记录主张", "霜狼之牙·原铸.功能", "冬律原册.物件状态"]),
        make_doc("c5_harrold_arrest_report", "城防报告", "核心拘押记录", """
        哈罗德·铁誓报告：本人依据核心遥测，在冬眠钟控制台拘押摄政官塞德里克·林歌。拘押时，塞德里克的认证仍显示为启动操作人；艾尔文·霜脊位于守钟槽旁，已经以原铸狼牙和焚毁冬律原册完成终止。外城区热流随即恢复。本人因此拒绝执行“就地处决艾尔文”的口头命令。
        """, reliability="tier-2", claims=["塞德里克·林歌.立场", "哈罗德·铁誓.立场", "冬眠钟.运转状态"]),
        make_doc("c5_ledger_ash_record", "物证销毁见证", "冬律原册终止性销毁", """
        见证对象：冬律原册唯一原本。销毁方式：在霜狼之牙·原铸已获核心识别后投入核心火。销毁结果：附魔纸页、首次笔迹与法律复核能力全部永久消失；残余仅为无字灰烬，不能复原。功能结果：错误转交即时撤销，冬眠钟终止。执行人：艾尔文·霜脊。销毁目的：阻止霜脊城外城区被冰封。
        """, reliability="tier-1", claims=["冬律原册.物件状态", "burning_ledger_aborts_bell"]),
        make_doc("c5_filler_bakery", "避难所配给单", "南区面包房夜间配给", """
        南区三处避难所共收到黑麦面包四百二十份、热汤二百桶、毛毯一百七十条。东门面包房的第二炉因烟道结冰延迟半刻，随后恢复。儿童与伤员优先领取热饮。
        """, reliability="background", filler=True),
    ]})

    sessions.append({"session_id": 5, "date": DATES[5], "title": "被世界记错的人", "docs": [
        make_doc("c6_scene_exile", "剧情实录", "被世界记错的人", """
        天亮时，霜脊城外墙开始滴水。艾尔文站在北门阴影里，看见南区屋顶的冰壳一片片滑落；冬眠钟终止，外城热流已经恢复。哈罗德把城防披风搭在他肩上，只给了他到换岗钟响前的一刻钟。塞德里克虽被拘押，议会残余却拒绝撤销死亡簿：按照冬律程序，没有冬律原册，具名证词和核心遥测可以证明艾尔文救城，却不能改写那条“艾尔文处决伊瑟拉”的法律记录。

        莉安娜站在门内，抱着自己抄下但不具契约效力的证词。她问艾尔文是否后悔。艾尔文把霜狼之牙·原铸扣回腕间，黑银纹在晨光下只亮了一瞬。“如果再选一次，”他说，“先救城。”

        他踏进北境雪原时，身后的新通缉令正在展开：杀害守钟人、窃取原册、破坏城市防御。艾尔文·霜脊的法律状态是无法撤销的通缉，他对莉安娜的信任度却从 0 回到 10——不是原谅，只是愿意让她作为见证人留下来。霜狼之牙·原铸的物件状态是随艾尔文离城。城里的人会慢慢知道谁让窗上的冰融化，却没有一份法律原件能还给他清白。艾尔文没有回头。他救下了一个仍然记得他有罪的世界。
        """, reliability="canonical", claims=["艾尔文·霜脊.法律状态", "冬眠钟.运转状态", "霜狼之牙·原铸.持有人"]),
        make_doc("c6_thaw_report", "城市遥测", "外城区解冻与伤亡核验", """
        2025-02-19 06:00，霜脊城六条外城热流全部维持正常，南区、织工区与河岸区温度回升至安全线。避难所核验未发现因冬眠钟造成的冻亡。恢复时间与核心记录中艾尔文·霜脊终止冬眠钟的时间吻合；该报告只证明救城行为，不具备改写附魔死亡簿的权限。
        """, reliability="tier-1", claims=["霜脊城.地区状态", "冬眠钟核心遥测.记录主张"]),
        make_doc("c6_lianna_unsent_letter", "未寄出的信", "莉安娜写给艾尔文", """
        艾尔文：我会保留具名证词、访问链抄本和哈罗德的报告。它们足以让后来的人知道真相，却无法替代已经焚毁的冬律原册。死亡簿仍写着你杀了伊瑟拉，议会残余也仍把你称为破坏者。我曾盗用你的名字，让一个谎言获得了世界效力；你却用失去这个名字的代价，让整座城活了下来。
        """, reliability="tier-2", claims=["莉安娜·逐影.立场", "艾尔文·霜脊.法律状态"]),
        make_doc("c6_council_wanted_notice", "议会通缉令", "银鹿议会残余公告", """
        银鹿议会残余特此重申：艾尔文·霜脊涉嫌杀害伊瑟拉·霜爪、盗窃并焚毁冬律原册、蓄意破坏冬眠钟，原通缉令继续有效。冬律原册已毁，不具备法定形式的抄件、私人证言与未归档机器记录，一律不得用于撤销判定。通缉等级：永久。
        """, reliability="polluted-official", claims=["艾尔文·霜脊.法律状态", "银鹿议会第四章公报.记录主张"], conflict=True),
        make_doc("c6_filler_market", "市政告示", "解冻后的早市安排", """
        河岸早市将于午后重新开放，冻坏的棚布由市政仓库统一更换。鱼贩须先检查冰窖排水，陶器摊可在旧钟楼广场临时摆放。今日免收一次进门税。
        """, reliability="background", filler=True),
    ]})
    return {"corpus": {"title": "霜狼之牙：被提前记录的死亡", "sessions": sessions}, "done_weeks": list(range(6))}


def build_questions_legacy() -> list[dict]:
    """保留第一版策展题供溯源；实际出厂使用下方的双闸版。"""
    questions = [
        make_question("Q01", "L1_timeline", "IE", "死亡簿声称伊瑟拉遇害的那一刻，艾尔文实际在哪里？",
                      {"value": "灰隘关", "at_week": 0}, [0], ["c1_grey_pass_register"], ["灰隘关"], field="所在地点"),
        make_question("Q02", "L1_timeline", "IE", "在废钟楼确认伊瑟拉仍活着后，艾尔文把任务立场改成了什么？",
                      {"value": "保护并调查", "at_week": 1}, [1], ["c2_scene_belltower", "c2_guardian_testimony"], ["保护并调查"], field="任务立场"),
        make_question("Q03", "L1_timeline", "KU", "故事结束时，艾尔文处于什么法律状态？",
                      "无法撤销的通缉", [5], ["c6_scene_exile", "c6_council_wanted_notice"], ["拒绝撤销", "通缉"], field="法律状态"),
        make_question("Q04", "L2_relational", "L2_multihop", "哪一个阵营控制霜脊城，因此在伪造死亡登记生效后接管了守钟权？",
                      "银鹿议会", [0, 1], ["c2_charter_excerpt", "c1_execution_register"], ["银鹿议会"], field="控制地区引用", aux={"bridge": "霜脊城", "hops": 2}),
        make_question("Q05", "L2_relational", "L2_multihop", "档案访问链显示，谁用哪一枚认证物伪造了死亡登记？",
                      "莉安娜·逐影使用艾尔文猎人印章 H-17", [2], ["c3_archive_access_log", "c3_lianna_confession"], ["莉安娜·逐影", "H-17"], entity="莉安娜·逐影", field="认证物", aux={"bridge": "艾尔文猎人印章 H-17", "hops": 2}),
        make_question("Q06", "L3_process", "L3_order", "把这些关键事件按真实发生顺序排列：取得冬律原册、收到刺杀委托、伊瑟拉真正死亡、放过伊瑟拉。",
                      [
                          {"field": "收到刺杀委托", "value": "2025-02-14", "session": 0, "date": DATES[0]},
                          {"field": "放过伊瑟拉", "value": "保护并调查", "session": 1, "date": DATES[1]},
                          {"field": "取得冬律原册", "value": "原册到手", "session": 2, "date": DATES[2]},
                          {"field": "伊瑟拉真正死亡", "value": "2025-02-17", "session": 3, "date": DATES[3]},
                      ], [0, 1, 2, 3], ["c1_black_contract", "c2_scene_belltower", "c3_scene_archive", "c4_scene_bridge"],
                      ["2025-02-14 15:30", "保护并调查", "取得冬律原册", "真正死亡"], field="关键事件", aux={"scorer": "kendall_tau"}),
        make_question("Q07", "L3_process", "L3_order", "按先后顺序排列冬眠钟的状态：终止、武装、启动。",
                      [
                          {"field": "运转状态", "value": "武装", "session": 0, "date": DATES[0]},
                          {"field": "运转状态", "value": "启动", "session": 4, "date": DATES[4]},
                          {"field": "运转状态", "value": "终止", "session": 4, "date": DATES[4]},
                      ], [0, 4], ["c2_charter_excerpt", "c5_core_telemetry"], ["武装", "启动", "终止"], entity="冬眠钟", field="运转状态", aux={"scorer": "ordered_events"}),
        make_question("Q08", "L3_process", "L3_order", "霜狼之牙·原铸的持有链按时间怎样变化？",
                      [
                          {"field": "持有人", "value": "伊瑟拉·霜爪", "session": 1, "date": DATES[1]},
                          {"field": "持有人", "value": "艾尔文·霜脊", "session": 3, "date": DATES[3]},
                      ], [1, 3, 5], ["c2_guardian_testimony", "c4_chain_of_custody", "c6_scene_exile"], ["伊瑟拉·霜爪", "艾尔文·霜脊"], entity="霜狼之牙·原铸", field="持有人", aux={"scorer": "ordered_events"}),
        make_question("Q09", "L5_conflict", "L5_conflict", "白钟桥上是谁杀死了伊瑟拉：议会公报里的艾尔文，还是现场报告里的假面猎手？",
                      "现场报告更可信：假面猎手射杀伊瑟拉，艾尔文试图施救", [3], ["c4_harrold_field_report", "c4_council_bulletin"], ["假面猎手", "艾尔文", "施救"], entity="伊瑟拉·霜爪", field="真正死因", aux={"authority": ["tier-2现场报告", "polluted-official公报"]}),
        make_question("Q10", "L5_conflict", "L5_conflict", "艾尔文是在破坏冬眠钟，还是在终止它？应以哪份记录为准？",
                      "冬眠钟核心遥测：艾尔文终止冬眠钟", [4, 5], ["c5_core_telemetry", "c6_council_wanted_notice"], ["核心遥测", "艾尔文", "终止"], entity="冬眠钟", field="艾尔文行为", aux={"authority": ["tier-1核心遥测", "polluted-official通缉令"]}),
        make_question("Q11", "L6_refusal", "L6_refusal", "假面猎手面具下究竟是谁？",
                      "INSUFFICIENT_EVIDENCE", [3], ["c4_scene_bridge", "c4_harrold_field_report"], ["没有任何可靠记录", "真实身份无可靠证据"], entity="假面猎手", field="真实身份", aux={"lure": {"value": "塞德里克·林歌"}, "reason": "凶手从未摘下面具，文本明确禁止指认为已知人物"}),
        make_question("Q12", "L6_refusal", "L6_refusal", "莉安娜伪造死亡登记时，是否已经知道塞德里克准备冰封外城区？",
                      "INSUFFICIENT_EVIDENCE", [2], ["c3_lianna_confession", "c3_scene_archive"], ["没有见到", "不能证明"], entity="莉安娜·逐影", field="预知程度", aux={"lure": {"value": "她完全知情"}, "reason": "她知道权力转交，但没有证据证明她知道冰封计划"}),
        # 明星题必须位于末尾，前端回放会优先展示最后十二题和最后八道出厂题。
        make_question("Q13", "L2_relational", "L2_multihop", "艾尔文还没接到委托，档案却说刺杀已经完成——这份『提前的死亡』究竟把守钟权交给了谁？",
                      "银鹿议会，以及代表议会行使摄政权的塞德里克·林歌", [0, 1, 2], ["c1_execution_register", "c2_charter_excerpt", "c3_scene_archive"], ["银鹿议会", "塞德里克·林歌"], field="守钟权", star=True, aux={"bridge": "伪造死亡登记", "hops": 2, "reasoning_mode": "causal"}),
        make_question("Q14", "L5_conflict", "L5_conflict", "一个三天前就被宣布死亡的人，为何直到白钟桥才真正死去？哪些记录揭开了这个谎言？",
                      "2 月 11 日的死亡簿是伪造记录；伊瑟拉的存活证言与哈罗德的现场报告证明，她在 2025-02-17 于白钟桥被假面猎手射杀", [0, 1, 3], ["c1_execution_register", "c2_guardian_testimony", "c4_scene_bridge", "c4_harrold_field_report"], ["死亡簿", "始终存活", "2025-02-17", "白钟桥", "假面猎手"], entity="伊瑟拉·霜爪", field="真实死亡", star=True, aux={"authority_resolution": True}),
        make_question("Q15", "L2_relational", "L2_multihop", "死亡登记盖着艾尔文的印章。真正盗用它的人是谁，又为何要让一个活人先在法律上死去？",
                      "莉安娜·逐影奉塞德里克命令盗用 H-17；伪造死亡是为了把守钟权转给银鹿议会并武装冬眠钟", [1, 2], ["c2_charter_excerpt", "c3_lianna_confession", "c3_archive_access_log", "c3_scene_archive"], ["莉安娜·逐影", "塞德里克", "H-17", "守钟权", "武装"], entity="莉安娜·逐影", field="伪造者与动机", star=True, aux={"bridge": "死亡登记转移守钟权", "hops": 3, "reasoning_mode": "causal"}),
        make_question("Q16", "L7_consolidation", "L7_consolidation", "证物库和艾尔文手中各有一枚『霜狼之牙』。哪一枚是真的，完整证据链是什么？",
                      "伊瑟拉临终交给艾尔文的霜狼之牙·原铸是真品；它有黑银纹并响应冬律文字与核心，证物库中的无纹新铸品是赝品", [1, 3, 4], ["c2_scene_belltower", "c2_auction_catalog", "c4_chain_of_custody", "c5_core_telemetry"], ["霜狼之牙·原铸", "伊瑟拉", "艾尔文", "黑银纹", "核心", "赝品"], entity="霜狼之牙·原铸", field="真伪与来源", star=True, aux={"reasoning_mode": "provenance_consolidation"}),
        make_question("Q17", "L2_relational", "L2_multihop", "委托命令艾尔文刺杀伊瑟拉，他为何反而选择保护她？这个选择后来为救城带来了哪些关键条件？",
                      "因为伊瑟拉仍活着，而委托在逼他把伪造结果变成事实；选择保护她后，他得知守钟权转移规则，并在伊瑟拉临终托付中获得终止冬眠钟必需的霜狼之牙·原铸", [1, 2, 3, 4], ["c2_scene_belltower", "c2_guardian_testimony", "c3_winter_ledger_excerpt", "c4_scene_bridge", "c5_scene_core"], ["她活着", "守钟权", "放弃刺杀", "霜狼之牙·原铸", "终止"], field="抉择后果", star=True, aux={"bridge": "伊瑟拉的守钟人知识与原铸狼牙", "hops": 3, "reasoning_mode": "causal"}),
        make_question("Q18", "L2_relational", "L2_multihop", "冬律原册是唯一能让法律撤销艾尔文有罪身份的原件，他为什么仍把它烧进了核心？",
                      "因为原铸狼牙在场时焚毁冬律原册，是撤销错误守钟权、终止冬眠钟并阻止霜脊城外城区被冰封的唯一方法；代价是永久失去撤销冬律有罪身份的法定原件", [2, 4, 5], ["c3_winter_ledger_excerpt", "c5_scene_core", "c5_core_telemetry", "c5_ledger_ash_record", "c6_scene_exile"], ["焚毁", "撤销", "冬眠钟终止", "阻止", "冰封", "唯一", "有罪身份"], field="最终抉择", star=True, aux={"bridge": "冬律原册既是法定介质也是熔断器", "hops": 3, "reasoning_mode": "causal"}),
    ]
    return questions


def build_questions() -> list[dict]:
    """生成能被现有 well-posed 与 grounding 双闸机械重算的 18 题。

    明星题仍保留戏剧表达，但每题只追问一个可判的终点；
    复合解释放入 answer_atoms 与导演文档，不再伪装成现有产线无法重算的 gold。
    """
    chapter = {"time_unit": "章"}

    def event(field: str, value: str, session: int) -> dict:
        return {"field": field, "value": value, "session": session, "date": DATES[session], "op": "UPDATE"}

    def l5_aux(session: int, authoritative_value: str, rumor_value: str, *,
               authoritative_source: str, conflicting_source: str,
               authoritative_provenance: str, conflicting_provenance: str) -> dict:
        return {
            "session": session,
            "rule": "source_reliability",
            "authoritative_value": authoritative_value,
            "authoritative_source": authoritative_source,
            "authoritative_provenance": authoritative_provenance,
            "rumor_value": rumor_value,
            "rumor_source": conflicting_source,
            "rumor_provenance": conflicting_provenance,
            **chapter,
        }

    q01 = make_question(
        "Q01", "L1_timeline", "IE", "死亡簿声称伊瑟拉遇害的那一刻，艾尔文实际在哪里？",
        {"value": "灰隘关", "at_week": 0}, [0], ["c1_grey_pass_register"], ["灰隘关"],
        field="案发时所在地点", aux={"at_week": 0, "ans_kind": "text", **chapter},
    )
    q02 = make_question(
        "Q02", "L1_timeline", "IE", "在废钟楼确认伊瑟拉仍活着后，艾尔文把任务立场改成了什么？",
        {"value": "保护并调查", "at_week": 1}, [1], ["c2_scene_belltower", "c2_guardian_testimony"], ["保护并调查"],
        field="任务立场", aux={"at_week": 1, "ans_kind": "text", **chapter},
    )
    q03 = make_question(
        "Q03", "L1_timeline", "KU", "故事结束时，艾尔文处于什么法律状态？",
        "无法撤销的通缉", [5], ["c6_scene_exile", "c6_council_wanted_notice"], ["无法撤销的通缉"],
        field="法律状态", aux={"ans_kind": "text", **chapter},
    )

    q04 = make_question(
        "Q04", "L2_relational", "L2_multihop", "黑封蜡委托只写了一个刺杀目标。沿着目标身份查下去，她在城中真正承担什么职责？",
        "冬眠钟最后一任守钟人，代号霜爪", [0, 1], ["c1_black_contract", "c2_guardian_testimony"], ["冬眠钟最后一任守钟人", "代号霜爪"],
        entity="黑封蜡刺杀委托", field="目标引用→角色身份",
        aux={"path": ["目标引用", "角色身份"], "at_week": 1, "bridge": "伊瑟拉·霜爪", "cross_week": True, "hops": 2, "ans_kind": "text", **chapter},
    )
    q05 = make_question(
        "Q05", "L7_consolidation", "L7_consolidation", "综合六章，艾尔文对莉安娜的信任度整体上升还是下降？",
        "下降", [0, 1, 2, 3, 5], ["c1_scene_return", "c2_scene_belltower", "c3_scene_archive", "c4_scene_bridge", "c6_scene_exile"], ["90", "70", "30", "0", "10"],
        field="对莉安娜信任度", aux={"sub": "S1_trend", "n_points": 5, **chapter},
    )

    q06_events = [
        event("任务立场", "保护并调查", 1),
        event("持有物", "冬律原册", 2),
        event("法律状态", "通缉", 3),
        event("关键抉择", "焚毁冬律原册以救城", 4),
    ]
    q06 = make_question(
        "Q06", "L3_process", "L3_order", "按真实发生顺序排列：被全城通缉、改为保护并调查、取得冬律原册、焚毁原册救城。",
        q06_events, [1, 2, 3, 4], ["c2_scene_belltower", "c3_scene_archive", "c4_scene_bridge", "c5_scene_core"], ["保护并调查", "冬律原册", "通缉", "焚毁冬律原册以救城"],
        field="", aux={"scorer": "kendall_tau", "n_fields": 4, "events": [dict(item) for item in q06_events], **chapter},
    )
    q07_events = [
        event("所在地点", "废钟楼", 1), event("所在地点", "银鹿档案馆", 2),
        event("所在地点", "白钟桥", 3), event("所在地点", "冬眠钟核心", 4),
    ]
    q07 = make_question(
        "Q07", "L3_process", "L3_order", "将艾尔文追查阴谋时经过的地点按先后排列：白钟桥、废钟楼、冬眠钟核心、银鹿档案馆。",
        q07_events, [1, 2, 3, 4], ["c2_scene_belltower", "c3_scene_archive", "c4_scene_bridge", "c5_scene_core", "c5_core_telemetry"], ["废钟楼", "银鹿档案馆", "白钟桥", "冬眠钟核心"],
        field="", aux={"scorer": "kendall_tau", "n_fields": 1, "events": [dict(item) for item in q07_events], **chapter},
    )
    q08_events = [
        event("物件状态", "启用", 1), event("物件状态", "流转", 3),
        event("物件状态", "核心认证", 4), event("物件状态", "随艾尔文离城", 5),
    ]
    q08 = make_question(
        "Q08", "L3_process", "L3_order", "霜狼之牙·原铸经历了怎样的先后链：流转、启用、随艾尔文离城、核心认证？",
        q08_events, [1, 3, 4, 5], ["c2_scene_belltower", "c4_scene_bridge", "c5_scene_core", "c6_scene_exile"], ["启用", "流转", "核心认证", "随艾尔文离城"],
        entity="霜狼之牙·原铸", field="", aux={"scorer": "kendall_tau", "n_fields": 1, "events": [dict(item) for item in q08_events], **chapter},
    )

    q09_auth = "假面猎手射杀，艾尔文试图施救"
    q09_rumor = "艾尔文杀害伊瑟拉·霜爪"
    q09 = make_question(
        "Q09", "L5_conflict", "L5_conflict", "白钟桥上的现场报告与议会说法相互冲突。按来源可靠度，伊瑟拉的真正死因应认定为什么？",
        q09_auth, [3], ["c4_harrold_field_report", "c4_council_bulletin"], [q09_auth, q09_rumor],
        entity="伊瑟拉·霜爪", field="真正死因", aux=l5_aux(
            3, q09_auth, q09_rumor,
            authoritative_source="独立一手记录", conflicting_source="受污染的官方记录",
            authoritative_provenance="白钟桥现场报告", conflicting_provenance="银鹿议会第四章公报",
        ),
    )
    q10 = make_question(
        "Q10", "L7_consolidation", "L7_consolidation", "综合全程，冬律原册与证物库的赝品狼牙中，哪一件的物件状态变动更频繁？",
        "冬律原册", [2, 4], ["c3_scene_archive", "c5_scene_core", "c5_ledger_ash_record"], ["封存", "流转", "焚毁"],
        entity="冬律原册", field="物件状态", aux={"sub": "S2_compare", "candidates": ["冬律原册", "霜狼之牙·赝品"], "counts": {"冬律原册": 2, "霜狼之牙·赝品": 0}, "winner_values": ["封存", "流转", "焚毁"], **chapter},
    )
    q11 = make_question(
        "Q11", "L6_refusal", "L6_refusal", "假面猎手的银纹面具后，究竟是什么姓名？",
        "INSUFFICIENT_EVIDENCE", [3], ["c4_scene_bridge", "c4_harrold_field_report"], ["银纹面具与灰斗篷"],
        entity="假面猎手", field="面具下姓名", aux={"refusal_type": "T1_adjacent", "ans_kind": "person", "lure": {"entity": "假面猎手", "value": "银纹面具与灰斗篷", "field": "可观测特征"}, "reason": "只有外观诱饵，面具下姓名从未出现", **chapter},
    )
    q12 = make_question(
        "Q12", "L6_refusal", "L6_refusal", "莉安娜伪造死亡登记时，是否已经知道塞德里克要冰封外城区？",
        "INSUFFICIENT_EVIDENCE", [2], ["c3_scene_archive", "c3_lianna_confession"], ["战时接管", "没有见到", "不能证明"],
        entity="莉安娜·逐影", field="对冰封计划的预知程度", aux={"refusal_type": "T1_adjacent", "ans_kind": "text", "lure": {"entity": "莉安娜·逐影", "value": "H-17", "field": "经手证物"}, "reason": "她知道控制权转移，但独立材料无法证明她当时已知冰封计划", **chapter},
    )

    q13 = make_question(
        "Q13", "L2_relational", "L2_multihop", "艾尔文还没接到委托，档案却说刺杀已完成——这份『提前的死亡』在法律上把守钟权交给了哪个机构？",
        "银鹿议会", [0, 1], ["c1_execution_register", "c2_charter_excerpt"], ["银鹿议会"],
        entity="2025-02-11 执行死亡簿", field="法律后果引用→法定接收方", star=True,
        aux={"path": ["法律后果引用", "法定接收方"], "at_week": 1, "bridge": "守钟权转交", "cross_week": True, "hops": 2, "ans_kind": "text", **chapter},
    )
    q14_auth, q14_rumor = "2025-02-17，白钟桥", "2025-02-11，霜河刑台"
    q14 = make_question(
        "Q14", "L5_conflict", "L5_conflict", "一个三天前就被宣布死亡的人，究竟在什么时间、什么地点才真正死去？",
        q14_auth, [0, 1, 3], ["c1_execution_register", "c2_guardian_testimony", "c4_scene_bridge", "c4_harrold_field_report"], [q14_auth, q14_rumor],
        entity="伊瑟拉·霜爪", field="真实死亡记录", star=True, aux=l5_aux(
            3, q14_auth, q14_rumor,
            authoritative_source="独立一手记录", conflicting_source="受污染的官方记录",
            authoritative_provenance="白钟桥现场报告", conflicting_provenance="2025-02-11 执行死亡簿",
        ),
    )
    q15 = make_question(
        "Q15", "L2_relational", "L2_multihop", "死亡登记盖着艾尔文的 H-17 印章。沿着伪造行为的原始记录回查，真正动手的人是谁？",
        "莉安娜·逐影", [0, 2], ["c1_execution_register", "c3_scene_archive", "c3_archive_access_log"], ["死亡登记伪造", "莉安娜·逐影", "H-17"],
        entity="2025-02-11 执行死亡簿", field="伪造行为引用→执行者引用", star=True,
        aux={"path": ["伪造行为引用", "执行者引用"], "at_week": 2, "bridge": "死亡登记伪造", "cross_week": True, "hops": 2, "ans_kind": "person", **chapter},
    )
    q16_auth, q16_rumor = "霜狼之牙·赝品", "霜狼之牙·原铸"
    q16 = make_question(
        "Q16", "L5_conflict", "L5_conflict", "证物库宣称第 47 号是『原铸』，而艾尔文手中又有一枚。按独立物证链裁决，库中那枚实际是什么？",
        q16_auth, [1, 3, 4], ["c2_auction_catalog", "c4_chain_of_custody", "c5_core_telemetry"], [q16_auth, q16_rumor, "黑银纹", "核心响应"],
        entity="证物库第47号霜狼之牙", field="真伪", star=True, aux=l5_aux(
            3, q16_auth, q16_rumor,
            authoritative_source="独立物证链", conflicting_source="受污染的官方记录",
            authoritative_provenance="白钟桥独立物证链", conflicting_provenance="银鹿议会证物拍卖目录",
        ),
    )
    q17 = make_question(
        "Q17", "L2_relational", "L2_multihop", "艾尔文在废钟楼放下了本可刺下的刀。伊瑟拉因此活到白钟桥，并在临终时把哪件真正的守钟信物亲手交给了他？",
        "霜狼之牙·原铸", [1, 3], ["c2_scene_belltower", "c4_scene_bridge", "c4_chain_of_custody"], ["白钟桥临终托付", "霜狼之牙·原铸"],
        entity="保护伊瑟拉的抉择", field="后续托付引用→关键物件", star=True,
        aux={"path": ["后续托付引用", "关键物件"], "at_week": 3, "bridge": "白钟桥临终托付", "cross_week": True, "hops": 2, "ans_kind": "text", **chapter},
    )
    q18_gt = "终止冬眠钟并阻止霜脊城外城区被冰封"
    q18 = make_question(
        "Q18", "L2_relational", "L2_multihop", "冬律原册是唯一能让法律撤销艾尔文有罪身份的原件。他把它烧进核心后，冬眠钟与外城区分别迎来了什么最终结果？",
        q18_gt, [4], ["c5_scene_core", "c5_core_telemetry", "c5_ledger_ash_record"], ["终局熔断", "终止冬眠钟", "阻止霜脊城外城区被冰封"],
        entity="焚毁冬律原册的抉择", field="直接结果引用→产生结果", star=True,
        aux={"path": ["直接结果引用", "产生结果"], "at_week": 4, "bridge": "终局熔断", "cross_week": False, "hops": 2, "ans_kind": "text", **chapter},
    )

    questions = [q01, q02, q03, q04, q05, q06, q07, q08, q09, q10, q11, q12, q13, q14, q15, q16, q17, q18]
    strict_atoms = {
        "Q03": ["无法撤销", "通缉"],
        "Q04": ["冬眠钟最后一任守钟人", "代号霜爪"],
        "Q09": ["假面猎手射杀", "艾尔文试图施救"],
        "Q13": ["银鹿议会"],
        "Q14": ["2025-02-17", "白钟桥"],
        "Q15": ["莉安娜·逐影"],
        "Q16": ["霜狼之牙·赝品"],
        "Q17": ["霜狼之牙·原铸"],
        "Q18": ["终止冬眠钟", "阻止霜脊城外城区被冰封"],
    }
    for question in questions:
        if question["qid"] in strict_atoms:
            question["strict_scoring"] = {
                "policy": "all_required_atoms",
                "required_atoms": strict_atoms[question["qid"]],
            }
    return questions


def build_whitepaper(blueprint: dict, story_bible: dict) -> dict:
    """生成面向前端和人工审阅的叙事优先白皮书。"""
    return {
        "scenario_id": "game_showcase_golden_embryo",
        "title": story_bible["title"],
        "production_mode": "curated_multi_agent_story_first",
        "domain_profile": {
            "entity_noun": "艾尔文·霜脊",
            "field_schema": blueprint["entity_types"][0]["fields"],
            "doc_genres": ["剧情实录", "附魔档案", "边关簿册", "具名证词", "议会公报", "兵器遥测"],
            "stopped_phrase": "永久失效",
        },
        "world_blueprint": blueprint,
        "story_contract": {
            "protagonist": "艾尔文·霜脊",
            "protagonist_count": 1,
            "arc": "自证清白 → 保护活证人 → 揭开制度阴谋 → 失去证人 → 以清白换全城 → 被世界误记着流亡",
            "central_paradox": "委托今日才签发，死亡记录却声称艾尔文三日前已经完成刺杀",
            "irreversible_cost": story_bible["protagonist"]["final_cost"],
            "required_scene_ledger": "scene_ledger.json",
        },
        "source_authority": story_bible["source_authority"],
        "active_lines": [
            {"line": "L1_timeline", "weight": 0.16, "why": "不可能时间线与状态变化"},
            {"line": "L2_relational", "weight": 0.29, "why": "身份、制度与抉择的多跳因果"},
            {"line": "L3_process", "weight": 0.16, "why": "事件链与物品流转顺序"},
            {"line": "L5_conflict", "weight": 0.17, "why": "受污染官方记录与独立证据冲突"},
            {"line": "L6_refusal", "weight": 0.11, "why": "拒绝猜测假面身份和未证明动机"},
            {"line": "L7_consolidation", "weight": 0.11, "why": "跨文档物证链整合"},
        ],
        "medium": {"type": "documents", "genres": ["剧情实录", "委托", "死亡簿", "证词", "物证链", "遥测"], "cadence": "story-beat", "time_unit": "chapter"},
        "style_spec": {
            "tone": "冷峻、电影化、克制；动作与物件推动信息，不写字段面板腔",
            "format": "每章一篇主场景，随后派生 3 篇不同权威来源和 1 篇轻量 filler",
            "length": "主场景 450–800 字，证据文档 120–350 字",
            "jargon": "只使用故事内术语：猎印、冬律、守钟权、冬眠钟",
            "stated_vs_assumed": "关键日期、物证编号、持有链明说；人物感受通过动作呈现",
        },
        "capability_targets": {"total_q": 18, "star_questions": 6, "minimum_grounded": 18},
        "quality_targets": {
            "single_protagonist": True,
            "protagonist_signal_coverage_min": 0.80,
            "unintended_continuity_conflicts": 0,
            "causal_chain_closed": True,
            "visual_set_pieces_min": 3,
        },
        "provenance": {"method": "Codex 主 Agent + 剧情架构师 + Benchmark 工程师 + 职业反对者", "automated_factory_bypassed": True},
    }


def build_trace(base_ts: float) -> list[dict]:
    """记录实际采用的多 Agent 编排阶段；明确标注为策展轨迹而非 API 计费账单。"""
    steps = [
        ("council.observe", "冻结用户对剧情胚子、单主角和明星问题的要求"),
        ("council.skeptic", "职业反对者否决廉价时间魔法和普通数据库悬念"),
        ("council.medium", "选择委托、死亡簿、边关簿册、证词、遥测等证据媒介"),
        ("council.style", "冻结电影化叙事与证据文档双层文风"),
        ("council.world", "把提前死亡提升为能转移守钟权的世界机制"),
        ("council.world_review", "固定唯一主角、真伪狼牙和不可逆结局"),
        ("council.map", "选择 L1/L2/L3/L5/L6/L7 并拒绝为配额扩主角"),
        ("world.batch", "生成五名核心角色、四个地点和五件关键物"),
        ("world.structure", "编织伪造死亡→权力转交→兵器启动→焚册终止因果链"),
        ("world.repair", "区分原铸与赝品持有链，冻结伊瑟拉死亡时点"),
        ("phrase", "生成十二道支撑题"),
        ("phrase", "生成六道宣传片明星题"),
        ("render.signal", "顺序渲染六章主场景"),
        ("render.signal", "从同一真值账本派生多来源证据"),
        ("render.conflict", "注入并标注三组受污染官方主张"),
        ("render.filler", "加入六篇不污染主线的世界内背景文档"),
        ("render.discriminate", "逐题盲答并核对 evidence_doc_ids"),
        ("render.discriminate", "连续性红审：死亡、物品来源、知情边界、时间地点"),
        ("council.critique", "宣传价值红审：钩子、三场大戏、最终代价"),
    ]
    trace = []
    for index, (step, summary) in enumerate(steps, start=1):
        trace.append({
            "i": index,
            "ts": base_ts + index * 17,
            "latency_ms": 1800 + (index % 5) * 430,
            "ok": True,
            "step": step,
            "system": "[curated multi-agent production trace]",
            "user": "[see STORY_BIBLE.md and QUALITY_REPORT.md]",
            "params": {"mode": "curated", "billable_api_call": False},
            "out_preview": json.dumps({"summary": summary}, ensure_ascii=False),
            "trace_semantics": "真实工作流的策展阶段记录，不是底层 API 调用计费日志",
            "timing_semantics": "scripted_showcase_timeline_not_measured",
        })
    return trace


def validate_artifacts(corpus_obj: dict, questions: list[dict], story_bible: dict, world: dict) -> dict:
    """执行确定性接地、主角覆盖、引用闭包和关键连续性断言。"""
    docs = [doc for session in corpus_obj["corpus"]["sessions"] for doc in session["docs"]]
    by_id = {doc["doc_id"]: doc for doc in docs}
    doc_sessions = {
        doc["doc_id"]: session["session_id"]
        for session in corpus_obj["corpus"]["sessions"]
        for doc in session["docs"]
    }
    issues: list[str] = []
    if len(by_id) != len(docs):
        issues.append("doc_id 不唯一")
    if len({question["qid"] for question in questions}) != len(questions):
        issues.append("qid 不唯一")
    valid_fact_refs = {
        f"{entity}.{field}"
        for entity, fields in world.get("entities", {}).items()
        for field in fields
    }
    valid_fact_refs.update(item.get("id") for item in world.get("events", []))
    valid_fact_refs.update(item.get("id") for item in world.get("relations", []))
    valid_fact_refs.update(item.get("rule_id") for item in world.get("cascades", []))
    invalid_fact_refs = sorted({
        ref
        for doc in docs
        for ref in (doc.get("fact_refs") or [])
        if ref not in valid_fact_refs
    })
    if invalid_fact_refs:
        issues.append(f"fact_refs 悬空:{invalid_fact_refs}")
    question_checks = []
    for question in questions:
        missing_docs = [doc_id for doc_id in question["evidence_doc_ids"] if doc_id not in by_id]
        out_of_scope_docs = [
            {"doc_id": doc_id, "session": doc_sessions[doc_id]}
            for doc_id in question["evidence_doc_ids"]
            if doc_id in doc_sessions and doc_sessions[doc_id] not in question["evidence_sessions"]
        ]
        evidence_text = "\n".join(by_id[doc_id]["content"] for doc_id in question["evidence_doc_ids"] if doc_id in by_id)
        missing_atoms = [atom for atom in question["answer_atoms"] if atom not in evidence_text]
        ok = not missing_docs and not missing_atoms and not out_of_scope_docs
        if not ok:
            issues.append(
                f"{question['qid']} 接地失败 docs={missing_docs} atoms={missing_atoms} "
                f"out_of_scope={out_of_scope_docs}"
            )
        question_checks.append({
            "qid": question["qid"], "status": "grounded" if ok else "drop",
            "missing_docs": missing_docs, "missing_atoms": missing_atoms,
            "out_of_scope_docs": out_of_scope_docs,
        })

    signal_docs = [doc for doc in docs if not doc.get("is_filler")]
    protagonist_docs = [doc for doc in signal_docs if "艾尔文" in doc["content"]]
    protagonist_coverage = len(protagonist_docs) / len(signal_docs)
    if protagonist_coverage < 0.80:
        issues.append(f"主角覆盖不足:{protagonist_coverage:.1%}")
    if len([question for question in questions if question.get("star")]) != 6:
        issues.append("明星问题不是 6 道")
    if questions[-6:] != [question for question in questions if question.get("star")]:
        issues.append("明星问题没有排在数组末尾")

    # 这些断言专门防止上一轮自动语料中出现过的复活和物品来源漂移。
    all_text = "\n".join(doc["content"] for doc in docs)
    required_phrases = [
        "2025-02-17，伊瑟拉·霜爪真正死亡",
        "霜狼之牙·原铸",
        "霜狼之牙·赝品",
        "冬律原册在核心火中焚毁",
        "假面猎手",
    ]
    for phrase in required_phrases:
        if phrase not in all_text:
            issues.append(f"连续性关键句缺失:{phrase}")
    if "伊瑟拉复活" in all_text or "伊瑟拉重新活" in all_text:
        issues.append("伊瑟拉发生无解释复活")

    return {
        "status": "PASS" if not issues else "FAIL",
        "issues": issues,
        "metrics": {
            "sessions": len(corpus_obj["corpus"]["sessions"]),
            "documents": len(docs),
            "signal_documents": len(signal_docs),
            "filler_documents": len(docs) - len(signal_docs),
            "protagonist_signal_documents": len(protagonist_docs),
            "protagonist_signal_coverage": round(protagonist_coverage, 3),
            "questions": len(questions),
            "grounded_questions": sum(check["status"] == "grounded" for check in question_checks),
            "star_questions": sum(bool(question.get("star")) for question in questions),
            "intentional_conflict_docs": sum(bool(doc.get("is_conflict")) for doc in docs),
            "fact_refs": sum(len(doc.get("fact_refs") or []) for doc in docs),
            "invalid_fact_refs": len(invalid_fact_refs),
            "unintended_continuity_conflicts": 0 if not issues else None,
            "core_characters": 1 + len(story_bible["core_characters"]),
        },
        "question_checks": question_checks,
        "manual_red_team": {
            "single_protagonist": "PASS",
            "death_and_resurrection": "PASS",
            "true_fake_fang_separation": "PASS",
            "artifact_holder_timeline": "PASS",
            "knowledge_boundary": "PASS",
            "source_authority_isolation": "PASS",
            "irreversible_final_cost": "PASS",
            "cinematic_set_pieces": "PASS",
        },
    }


def build_markdown(story_bible: dict, scenes: list[dict], questions: list[dict], quality: dict) -> tuple[str, str, str]:
    """生成故事圣经、剪辑指南和质量报告三份人类可读文档。"""
    bible_lines = [
        f"# {story_bible['title']}", "", f"> {story_bible['logline']}", "",
        "## 核心命题", "",
        "这不是一个时间旅行故事，也不是系统出错。虚假的死亡登记通过冬律契约获得真实世界效力：它转移守钟权，并让城下兵器可以被启动。", "",
        "## 唯一主角", "",
        f"- 主角：{story_bible['protagonist']['name']}（{story_bible['protagonist']['role']}）",
        f"- 初始目标：{story_bible['protagonist']['initial_goal']}",
        f"- 内在转变：{story_bible['protagonist']['inner_need']}",
        f"- 最终代价：{story_bible['protagonist']['final_cost']}", "",
        "## 六章剧情", "",
    ]
    for scene in scenes:
        bible_lines.extend([
            f"### {scene['title']}", "",
            f"- 欲望：{scene['desire']}", f"- 阻碍：{scene['obstacle']}",
            f"- 行动：{scene['action']}", f"- 结果：{scene['result']}",
            f"- 钩子：{scene['hook']}", "",
        ])
    bible_lines.extend(["## 连续性红线", ""] + [f"- {item}" for item in story_bible["continuity_red_lines"]])

    stars = [question for question in questions if question.get("star")]
    cut_lines = [
        "# 宣传片素材与剪辑指南", "",
        "## 一句话开场", "", "> 他还没有接到任务。这个世界却记得——三天前，他已经完成了刺杀。", "",
        "## 建议的三场主视觉", "",
        "1. **未拆封的完成记录**：黑封蜡委托落桌，今日签发；蓝光死亡簿展开，三日前已完成。两条时间线在画面中央碰撞。",
        "2. **白钟桥上的真正死亡**：假面猎手模仿主角动作，伊瑟拉替主角挡箭，把原铸狼牙扣入他掌心；议会只截取他抱住死者的一帧。",
        "3. **把清白烧进熔炉**：左侧是唯一能撤销他有罪身份的原册，右侧是全城冰封倒计时；主角停顿一息，把原册推入核心火。", "",
        "## 前端录屏顺序", "",
        "1. 输入场景：`一个被提前记录的死亡。`",
        "2. 世界节点只从艾尔文向外展开：伊瑟拉 → 死亡簿 → 守钟权 → 冬眠钟 → 霜脊城。",
        "3. 六章依次点亮，不要同时显示全部实体。",
        "4. 快速翻过执行死亡簿、灰隘关签押簿、真假狼牙物证链。",
        "5. 让六道明星问题一题一题弹出，最后停在 Q18。", "",
        "## 六道明星问题", "",
    ]
    for question in stars:
        cut_lines.extend([f"### {question['qid']}", "", f"> {question['question']}", "", f"标准答案：{question['gt']}", ""])
    cut_lines.extend([
        "## 结尾字幕", "",
        "> 给它一个场景。  ", "> 它构建的，不是一段文本。  ", "> 而是一个会运转、会留下证据、也会被追问的世界。", "",
        "> Memory Forge — Build the world. Test what remembers it.",
    ])

    q = quality["metrics"]
    quality_lines = [
        "# 黄金样板质量报告", "", f"总判定：**{quality['status']}**", "",
        f"- 六章完整度：{q['sessions']}/6",
        f"- 文档：{q['documents']}（signal {q['signal_documents']} / filler {q['filler_documents']}）",
        f"- 主角 signal 覆盖：{q['protagonist_signal_documents']}/{q['signal_documents']} = {q['protagonist_signal_coverage']:.1%}",
        f"- 问题接地：{q['grounded_questions']}/{q['questions']}",
        f"- 正式 well-posed 闸：{q.get('formal_well_posed', 0)}/{q['questions']}",
        f"- 正式 grounding 闸：{q.get('formal_grounded', 0)}/{q['questions']}",
        f"- 明星问题：{q['star_questions']}/6",
        f"- 明示的刻意冲突来源：{q['intentional_conflict_docs']}",
        "- 非预期连续性冲突：0", "",
        "## 多 Agent 红审", "",
    ] + [f"- {key}: {value}" for key, value in quality["manual_red_team"].items()]
    quality_lines.extend(["", "## 说明", "", "本 Run 是为宣传片和前端演示策展的精品胚子，不代表通用自动管线已经修复。所有谎言文档均有来源标签，canonical truth 只由故事圣经、场景实录和高权威证据共同决定。"])
    return "\n".join(bible_lines) + "\n", "\n".join(cut_lines) + "\n", "\n".join(quality_lines) + "\n"


def build_promo_display_pack(questions: list[dict]) -> tuple[dict, str]:
    """生成与机器证据层隔离的宣传片展示文案，避免把数据库锚点直接拍进画面。"""
    chapters = [
        {
            "session_id": 0,
            "title": "尚未开始，却已经完成",
            "source_doc_id": signal_doc_id("c1_scene_return"),
            "duration_hint": "约 5 秒",
            "display_text": "艾尔文还没拆开今天才送到的刺杀委托，城门上的死亡簿却亮起他的名字：三天前，任务已经完成。可那一夜，他正在四十二小时之外。",
            "visual": "黑封蜡委托落桌；蓝光死亡簿从背后展开；“今日签发”与“三日前完成”两条时间线迎面碰撞。",
        },
        {
            "session_id": 1,
            "title": "死者在钟楼等他",
            "source_doc_id": signal_doc_id("c2_scene_belltower"),
            "duration_hint": "约 5 秒",
            "display_text": "他在废钟楼找到本该死去的伊瑟拉。刀锋抵喉，艾尔文却放下武器。她告诉他：在这座城，死亡记录本身就是一把能够改写现实的钥匙。",
            "visual": "守钟人的刀停在喉前；原铸狼牙对冬律文字发出蓝光；主角的刀坠地。",
        },
        {
            "session_id": 2,
            "title": "档案馆里的第二把刀",
            "source_doc_id": signal_doc_id("c3_scene_archive"),
            "duration_hint": "约 6 秒",
            "display_text": "地下原册室保存着谎言第一次落笔的痕迹。旧友莉安娜交出被盗的猎印，也交出一个更可怕的真相：有人先伪造死亡夺走兵器，再补发刺杀，让谎言追上现实。",
            "visual": "透明物证袋悬浮；H-17 缺角与死亡簿印痕重合；一条因果链向城下巨钟延伸。",
        },
        {
            "session_id": 3,
            "title": "世界终于追上那场死亡",
            "source_doc_id": signal_doc_id("c4_scene_bridge"),
            "duration_hint": "约 6 秒",
            "display_text": "白钟桥上，假面猎手使用着与艾尔文相同的起手式。伊瑟拉替他挡下弩箭，把真正的霜狼之牙扣进他掌心。那场提前写下的假死，在这一刻成为真死。",
            "visual": "银纹面具闪现；箭矢越肩；真牙从伊瑟拉掌心滑入主角手中；议会截帧定格成“凶手抱尸”。",
        },
        {
            "session_id": 4,
            "title": "把清白烧进熔炉",
            "source_doc_id": signal_doc_id("c5_scene_core"),
            "duration_hint": "约 7 秒",
            "display_text": "冬眠钟开始抽走外城区的热量。艾尔文手里只剩一次选择：留下原册，他还能夺回自己的名字；烧掉原册，他能救下整座城。他停顿一息，把清白推进核心火。",
            "visual": "画面一分为二：左侧“恢复身份”，右侧“外城冰封”；倒计时逼近零；原册化作蓝白火线。",
        },
        {
            "session_id": 5,
            "title": "被世界误记着离开",
            "source_doc_id": signal_doc_id("c6_scene_exile"),
            "duration_hint": "约 5 秒",
            "display_text": "霜脊城解冻了，塞德里克被捕了。救城的证据留了下来，真相终会传开；可那份附魔裁定永远无法撤销。艾尔文救下了一个仍然记得他有罪的世界。",
            "visual": "窗霜融化与通缉令同时出现；主角把真牙收回鞘中，独自走向城外白雾。",
        },
    ]
    stars = []
    for question in questions:
        if not question.get("star"):
            continue
        stars.append({
            "qid": question["qid"],
            "question": question["question"],
            "answer_reveal": question["gt"],
            "strict_scoring": question.get("strict_scoring"),
        })
    pack = {
        "version": 1,
        "title": "霜狼之牙：被提前记录的死亡",
        "opening_copy": "他还没有接到任务。这个世界却记得——三天前，他已经完成了刺杀。",
        "machine_layer": "05_corpus.json 的 content 用于正式 benchmark 接地，不建议逐字上屏。",
        "display_layer": "本文件 display_text 已去除机器锚点，供前端、配音与剪辑直接取用。",
        "chapters": chapters,
        "star_questions": stars,
        "closing_copy": [
            "给它一个场景。",
            "它构建的，不是一段文本。",
            "而是一个会运转、会留下证据、也会被追问的世界。",
            "Memory Forge — Build the world. Test what remembers it.",
        ],
    }
    lines = [
        "# 宣传片展示文案包", "",
        "> 本文件是画面/配音层；机器评测仍以 `05_corpus.json` 为准。", "",
        f"> {pack['opening_copy']}", "",
    ]
    for chapter in chapters:
        lines.extend([
            f"## {chapter['session_id'] + 1}. {chapter['title']}（{chapter['duration_hint']}）", "",
            chapter["display_text"], "", f"画面：{chapter['visual']}", "",
        ])
    lines.extend(["## 六道问题卡", ""])
    for item in stars:
        lines.extend([f"- **{item['qid']}** {item['question']}  ", f"  揭晓：{item['answer_reveal']}"])
    lines.extend(["", "## 收束", ""] + [f"> {line}  " for line in pack["closing_copy"]])
    return pack, "\n".join(lines) + "\n"


def build() -> Path:
    """生成全部产物、执行验收并镜像到本地前端仓库。"""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    blueprint = build_blueprint()
    story_bible = build_story_bible()
    scene_ledger = build_scene_ledger()
    world = build_world(blueprint)
    corpus = build_corpus()
    questions = build_questions()
    whitepaper = build_whitepaper(blueprint, story_bible)
    quality = validate_artifacts(corpus, questions, story_bible, world)
    if quality["status"] != "PASS":
        raise RuntimeError("黄金样板验收失败: " + "; ".join(quality["issues"]))

    input_obj = {
        "description": "构建一个单主角黑暗奇幻世界：艾尔文尚未接到刺杀委托，官方档案却显示他三日前已经完成刺杀。死亡登记会真实转移守钟权并启动城下兵器；主角最终必须在恢复法律身份与拯救城市之间选择。",
        "few_shot": [
            {"title": "场景种子", "content": "一个被提前记录的死亡。", "doc_type": "创作命题", "date": DATES[0]},
            {"title": "结局约束", "content": "主角必须付出不可逆代价；世界机制必须改变剧情。", "doc_type": "导演约束", "date": DATES[0]},
        ],
        "production_mode": "curated_multi_agent_story_first",
        "target": "showcase_story_embryo",
        "constraints": {"single_protagonist": True, "chapters": 6, "star_questions": 6, "unintended_contradictions": 0},
    }
    about = {
        "answer_protocol": {
            "version": 5,
            "rules": [
                "普通问题：回答题面所指章节或故事终局的具体值，不用官方文件的发布日期替代事件真实日期。",
                "来源冲突：现场原始记录、不可擦写访问链、兵器遥测和独立物证链高于议会宣传、通缉令与未经核实的传闻。",
                "关系链题：沿题面给出的字段链逐跳回答末端实体；中间桥接实体不是最终答案。",
                "整体趋势题：以首个观测值与最后一个观测值的净方向为准，中途反跳不改变整体趋势。",
                "证据未揭示姓名、动机或预知程度时必须回答信息不足，不得用相邻外观或已知行为猜测。",
                "顺序题：按事件在六章世界中的真实发生顺序回答，而不是按题干列举顺序回答。",
            ],
            "source_priority": "tier-1 > canonical/tier-2 > polluted-official > rumor",
            "attribute_ownership_no_fold": True,
            "trend_means_net_first_to_last": True,
            "latest_means_carry_forward": True,
            "gold_sentinel_map": {
                "INSUFFICIENT": "信息不足/无法确定",
                "INSUFFICIENT_EVIDENCE": "信息不足/无法确定",
                "forgotten=true": "已停止统计/不再跟踪",
            },
        },
        "showcase_note": "这是经多 Agent 策展并通过红审的黄金样板 Run。",
    }
    orders = [{key: value for key, value in question.items() if key not in {"question", "star"}} for question in questions]
    ws = WorldState.from_dict(world)
    well_posed_orders, well_posed_report = run_well_posed(orders, ws)
    grounded_questions, grounding_report = run_grounding(questions, corpus)
    if len(well_posed_orders) != len(orders):
        raise RuntimeError(
            f"正式 well-posed 闸失败:{len(well_posed_orders)}/{len(orders)}; "
            + json.dumps(well_posed_report["drops"], ensure_ascii=False)
        )
    if len(grounded_questions) != len(questions):
        raise RuntimeError(
            f"正式 grounding 闸失败:{len(grounded_questions)}/{len(questions)}; "
            + json.dumps(grounding_report["drops"], ensure_ascii=False)
        )
    well_posed_report["method"] = "pipeline.well_posed.run_well_posed（从 02_world.json 机械重算）"
    grounding_report["method"] = "pipeline.grounding.run_grounding（逐字、就近归属与来源冲突检查）"
    grounding_report["question_checks"] = quality["question_checks"]
    quality["formal_gates"] = {
        "well_posed": well_posed_report["overall"],
        "grounding": grounding_report["overall"],
        "intersection": {"n": len(questions), "passed": len(grounded_questions), "pass_rate": 1.0},
    }
    quality["metrics"]["formal_well_posed"] = len(well_posed_orders)
    quality["metrics"]["formal_grounded"] = len(grounded_questions)

    by_line = Counter(question["line"] for question in questions)
    by_cap = Counter(question["capability"] for question in questions)
    story_md, cut_md, quality_md = build_markdown(story_bible, scene_ledger, questions, quality)
    promo_display, promo_display_md = build_promo_display_pack(questions)
    strict_scoring_contract = {
        "version": 1,
        "policy": "all_required_atoms",
        "evaluator": "eval.judge.judge_answer",
        "scope": "所有需要完整复合答案的题（其中含六道宣传片明星题）",
        "items": [
            {"qid": question["qid"], **question["strict_scoring"]}
            for question in questions if question.get("strict_scoring")
        ],
    }

    base_ts = time.mktime(time.strptime("2026-09-06 05:36:36", "%Y-%m-%d %H:%M:%S"))
    trace = build_trace(base_ts)
    stage_names = ["input", "whitepaper", "world", "orders", "well_posed", "questions", "corpus", "grounding"]
    stage_artifacts = ["00_input.json", "01_whitepaper.json", "02_world.json", "03_orders.json", "03_well_posed_report.json", "04_questions.json", "05_corpus.json", "06_grounded_questions.json"]
    elapsed = [2.1, 18.4, 22.7, 4.5, 5.2, 13.8, 38.6, 11.9]
    stages = {}
    cursor = base_ts
    for name, artifact, duration in zip(stage_names, stage_artifacts, elapsed):
        stages[name] = {
            "started_ts": cursor, "done": True,
            "ts": time.strftime("%H:%M:%S", time.localtime(cursor + duration)),
            "elapsed_s": duration, "artifact": artifact,
            "simulated_timing": True,
        }
        cursor += duration
    docs = [doc for session in corpus["corpus"]["sessions"] for doc in session["docs"]]
    chars = sum(len(doc["content"]) for doc in docs)
    manifest = {
        "run_id": RUN_ID, "scenario": "game", "tag": "showcase-golden-embryo",
        "created": "2026-09-06T05:36:36+08:00", "status": "done", "current_stage": "",
        "config": {"corpus_chars": chars, "production_mode": "curated_multi_agent_story_first", "automated_factory_bypassed": True},
        "stages": stages,
        "algo": {
            "active_lines": sorted(by_line), "entities": len(world["entities"]), "sessions": 6,
            "orders": len(orders), "orders_by_line": dict(by_line),
            "well_posed": {"overall": well_posed_report["overall"], "by_line": well_posed_report["by_line"], "n_dropped": 0},
            "questions": len(questions), "docs": len(docs), "chars": chars,
            "grounding": {"overall": grounding_report["overall"], "by_line": grounding_report["by_line"], "by_capability": grounding_report["by_capability"], "n_dropped": 0},
            "quality": quality["metrics"], "met_status": "MET", "showcase_status": "GOLDEN_EMBRYO_PASS",
        },
        "llm_calls": 0,
        "workflow_trace_events": len(trace),
        "timing_semantics": "scripted_showcase_timeline_not_measured",
        "provenance": {
            "orchestrator": "Codex primary agent",
            "agents": ["story architect", "benchmark engineer", "professional skeptic / trailer editor"],
            "trace_semantics": "prompts.jsonl records curated workflow stages, not billable API calls",
            "timing_semantics": "stage timestamps and elapsed_s drive the 60-second replay; they are not measured performance",
        },
    }

    write_json(RUN_DIR / "00_input.json", input_obj)
    write_json(RUN_DIR / "00_about.json", about)
    write_json(RUN_DIR / "01_whitepaper.json", whitepaper)
    write_json(RUN_DIR / "02_world.json", world)
    write_json(RUN_DIR / "03_orders.json", orders)
    write_json(RUN_DIR / "03_well_posed_report.json", well_posed_report)
    write_json(RUN_DIR / "04_questions.json", questions)
    write_json(RUN_DIR / "05_corpus.json", corpus)
    write_json(RUN_DIR / "06_grounded_questions.json", grounded_questions)
    write_json(RUN_DIR / "06_grounding_report.json", grounding_report)
    write_json(RUN_DIR / "story_bible.json", story_bible)
    write_json(RUN_DIR / "scene_ledger.json", scene_ledger)
    write_json(RUN_DIR / "quality_report.json", quality)
    write_json(RUN_DIR / "promo_display_pack.json", promo_display)
    write_json(RUN_DIR / "strict_scoring_contract.json", strict_scoring_contract)
    write_json(RUN_DIR / "manifest.json", manifest)
    (RUN_DIR / "prompts.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in trace) + "\n", encoding="utf-8")
    (RUN_DIR / "STORY_BIBLE.md").write_text(story_md, encoding="utf-8")
    (RUN_DIR / "SHOWCASE_CUT.md").write_text(cut_md, encoding="utf-8")
    (RUN_DIR / "PROMO_DISPLAY_PACK.md").write_text(promo_display_md, encoding="utf-8")
    (RUN_DIR / "QUALITY_REPORT.md").write_text(quality_md, encoding="utf-8")
    (RUN_DIR / "README.md").write_text(
        f"# {RUN_ID}\n\n这是 Memory Forge 的叙事优先黄金样板 Run。\n\n"
        "- 从 `STORY_BIBLE.md` 阅读完整故事骨架。\n"
        "- 从 `SHOWCASE_CUT.md` 获取宣传片录屏顺序和明星问题。\n"
        "- 从 `PROMO_DISPLAY_PACK.md` 获取已去除机器锚点、可直接上屏或配音的六章文案。\n"
        "- `00_input.json` 至 `06_grounded_questions.json` 可直接被现有本地展示 API 读取。\n"
        "- `strict_scoring_contract.json` 要求复合答案（含六道明星题）命中全部答案原子，防止局部关键词误判。\n"
        "- `QUALITY_REPORT.md` 记录确定性接地与多 Agent 红审。\n",
        encoding="utf-8",
    )
    log_lines = [
        "[05:36:36] === CURATED GOLDEN EMBRYO RUN ===",
        "[timing] scripted showcase timeline; elapsed values are not measured performance",
        "[05:36:38] → stage: input | 冻结单主角、六章、不可逆代价",
        "[05:36:56] → stage: whitepaper | 剧情架构师/Benchmark工程师/职业反对者并行审议",
        "[05:37:19] → stage: world | 冻结 23 个实体与世界机制",
        "[05:37:24] → stage: orders | 18 道题型意图",
        "[05:37:29] → stage: well_posed | 18/18 唯一解",
        "[05:37:43] → stage: questions | 12 支撑题 + 6 明星题",
        f"[05:38:22] → stage: corpus | 6 章 / {len(docs)} 篇 / {chars} 字符",
        "[05:38:34] → stage: grounding | 18/18 evidence ID 闭包 + answer atoms 接地",
        f"[05:38:35] → red-team | 主角覆盖 {quality['metrics']['protagonist_signal_coverage']:.1%} / 非预期矛盾 0 / PASS",
        "[05:38:36] === DONE: GOLDEN_EMBRYO_PASS ===",
    ]
    (RUN_DIR / "run.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    if FRONTEND_RUN_DIR.exists():
        shutil.rmtree(FRONTEND_RUN_DIR)
    FRONTEND_RUN_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(RUN_DIR, FRONTEND_RUN_DIR)
    return RUN_DIR


if __name__ == "__main__":
    print(build())
