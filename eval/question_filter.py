"""根据已有逐题判分剔除全员答对题；纯离线、仅使用标准库。

python -m eval.question_filter --bench 06_grounded_questions.json \
    --results results.json --out-dir filtered --keep-easy-ratio 0.2
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil


DISPOSITIONS = ("removed_easy", "kept_easy_sample", "kept_not_all_correct", "kept_incomplete")
IDENTITY_FIELDS = ("line", "capability", "question", "gt", "aux", "strict_scoring")


def question_key(item: dict) -> str:
    """按题面和评分合同匹配，避免题号重排或旧答案造成错配。"""
    payload = {field: item.get(field) for field in IDENTITY_FIELDS}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_options(systems: list[str], keep_easy_ratio: float, seed: int) -> None:
    """提前检查筛选参数，供离线命令和在线评测共用。"""
    if (len(systems) < 2 or any(not isinstance(s, str) or not s.strip() for s in systems)
            or len(systems) != len(set(systems))):
        raise ValueError("至少需要两个名称不同的系统，才能判断全员答对")
    if not math.isfinite(keep_easy_ratio) or not 0 <= keep_easy_ratio <= 1:
        raise ValueError("keep_easy_ratio 必须在 0 到 1 之间")
    if type(seed) is not int:
        raise ValueError("seed 必须为整数")


def _rows(value, label: str) -> list[dict]:
    """校验逐题列表；损坏输入直接报错，避免静默漏读。"""
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{label} 必须是题目对象列表")
    for row in value:
        if not isinstance(row.get("question"), str) or not row["question"].strip():
            raise ValueError(f"{label} 中存在空题面或缺失 question 的记录")
        if "gt" not in row or not row.get("capability"):
            raise ValueError(f"{label} 中存在缺失 gt 或 capability 的记录")
    return value


def _invalid_reason(record: dict) -> str | None:
    """只有成功、可判分且带布尔判分的回答才参与难度判断。"""
    if record.get("error") or record.get("judge_error"):
        return "evaluation_error"
    if record.get("judgeable", True) is not True:
        return "not_judgeable"
    if type(record.get("correct")) is not bool:
        return "invalid_judgement"
    pred = record.get("pred")
    if not isinstance(pred, str) or not pred.strip():
        return "missing_answer"
    if pred.lstrip().startswith("[") and "ERROR" in pred:
        return "evaluation_error"
    return None


def filter_questions(questions: list[dict], results: dict[str, list[dict]], *,
                     keep_easy_ratio: float = 0.0, seed: int = 0,
                     preserve_capabilities: tuple[str, ...] | list[str] = ()) -> tuple[list[dict], dict]:
    """筛选全员答对题并返回审计报告；输入不变，缺测或异常题一律保留。"""
    systems = sorted(results)
    validate_options(systems, keep_easy_ratio, seed)
    preserved = set(preserve_capabilities)
    _rows(questions, "bench")
    keys = [question_key(q) for q in questions]
    key_counts = Counter(keys)
    indexes = {}
    unmatched = {}
    for system in systems:
        index = defaultdict(list)
        for record in _rows(results[system], system):
            index[question_key(record)].append(record)
        indexes[system] = index
        unmatched[system] = sum(len(rows) for key, rows in index.items() if key not in key_counts)

    items = []
    easy = []
    for position, (q, key) in enumerate(zip(questions, keys)):
        issues = {}
        if key_counts[key] > 1:
            issues["bench"] = "duplicate_question"
        if q.get("judgeable", True) is not True:
            issues["bench"] = "not_judgeable"
        if q.get("capability") in preserved:
            issues["review"] = "capability_pending_review"
        grades = {}
        for system in systems:
            records = indexes[system].get(key, [])
            if len(records) != 1:
                issues[system] = "missing_result" if not records else "duplicate_result"
                grades[system] = None
                continue
            record = records[0]
            reason = _invalid_reason(record)
            if q.get("qid") is not None and record.get("qid") is not None and q["qid"] != record["qid"]:
                reason = "qid_mismatch"
            grades[system] = None if reason else record["correct"]
            if reason:
                issues[system] = reason
        if issues:
            disposition = "kept_incomplete"
        elif all(grades.values()):
            disposition = "removed_easy"
            easy.append(key)
        else:
            disposition = "kept_not_all_correct"
        items.append({"index": position, "key": key, "qid": q.get("qid"),
                      "line": q.get("line"), "capability": q.get("capability"),
                      "question": q["question"], "correct": grades,
                      "issues": issues, "disposition": disposition})

    # 哈希排序抽样使保留集合不受输入顺序或系统排列影响，并支持逐步提高保留率。
    keep_n = int(len(easy) * Fraction(str(keep_easy_ratio)))
    ranked = sorted(easy, key=lambda key: (hashlib.sha256(f"{seed}:{key}".encode()).hexdigest(), key))
    sampled = set(ranked[:keep_n])
    kept = []
    for q, item in zip(questions, items):
        if item["key"] in sampled:
            item["disposition"] = "kept_easy_sample"
        if item["disposition"] != "removed_easy":
            kept.append(q)

    def counts(rows):
        counter = Counter(item["disposition"] for item in rows)
        return {"input": len(rows), "kept": len(rows) - counter["removed_easy"],
                **{name: counter[name] for name in DISPOSITIONS}}

    by_line = defaultdict(list)
    by_capability = defaultdict(list)
    for item in items:
        by_line[str(item["line"])].append(item)
        by_capability[str(item["capability"])].append(item)
    report = {
        "schema_version": 1, "rule": "all_selected_systems_correct",
        "systems": systems, "keep_easy_ratio": keep_easy_ratio, "seed": seed,
        "preserve_capabilities": sorted(preserved),
        "sampling": "floor(n_easy * keep_easy_ratio); seeded SHA-256 order",
        "identity_fields": list(IDENTITY_FIELDS),
        "counts": {**counts(items), "all_correct": len(easy)},
        "by_line": {key: counts(rows) for key, rows in sorted(by_line.items())},
        "by_capability": {key: counts(rows) for key, rows in sorted(by_capability.items())},
        "unmatched_result_rows": unmatched,
        "judgement_basis": "supplied_correct_labels_without_rejudging",
        "items": items,
    }
    return kept, report


def _read_json(path: Path):
    """兼容 UTF-8 BOM 的 JSON 文件。"""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_questions(path: Path) -> list[dict]:
    """兼容题目列表及 {questions: [...]} 包装。"""
    data = _read_json(path)
    return _rows(data.get("questions") if isinstance(data, dict) else data, str(path))


def load_results(*, aggregate: Path | None = None, system_files: dict[str, Path] | None = None,
                 systems: list[str] | None = None) -> dict[str, list[dict]]:
    """读本项目汇总 JSON 或每个系统独立的 JSON/JSONL，禁止静默省略所选系统。"""
    if (aggregate is None) == (not system_files):
        raise ValueError("必须且只能提供汇总 results 或逐系统结果文件")
    if aggregate is not None:
        data = _read_json(aggregate)
        raw = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            raise ValueError("汇总文件缺少 results 对象")
        selected = systems if systems is not None else data.get("systems", list(raw))
        if not isinstance(selected, list):
            raise ValueError("systems 必须为系统名称列表")
        validate_options(selected, 0.0, 0)
        result = {}
        for system in selected:
            if system not in raw or not isinstance(raw[system], dict):
                raise ValueError(f"缺少所选系统的结果: {system}")
            result[system] = _rows(raw[system].get("records"), system)
        return result
    selected = list(system_files) if systems is None else systems
    validate_options(selected, 0.0, 0)
    result = {}
    for system in selected:
        if system not in system_files:
            raise ValueError(f"缺少所选系统的结果文件: {system}")
        path = system_files[system]
        if path.suffix.lower() == ".jsonl":
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        else:
            data = _read_json(path)
            rows = data.get("records") if isinstance(data, dict) else data
        result[system] = _rows(rows, system)
    return result


def _markdown_report(report: dict) -> str:
    """生成人可读的筛选摘要；逐题去留依据保存在配套 JSON。"""
    c = report["counts"]
    lines = ["# 全员答对题筛选", "", f"系统：{', '.join(report['systems'])}", "",
             f"输入 {c['input']} 题；全员答对 {c['all_correct']} 题；剔除 {c['removed_easy']} 题；最终保留 {c['kept']} 题。", "",
             f"简单题保留比例：{report['keep_easy_ratio']}；种子：{report['seed']}；向下取整保留 {c['kept_easy_sample']} 道简单题。", "",
             f"缺测、异常、重复记录或人工暂缓筛选的 {c['kept_incomplete']} 题保留待核查。", "",
             f"暂缓筛选的能力：{', '.join(report['preserve_capabilities']) or '无'}。", "",
             "| 能力 | 原题数 | 剔除 | 保留 | 待核查 |", "| --- | ---: | ---: | ---: | ---: |"]
    for capability, row in report["by_capability"].items():
        lines.append(f"| {capability} | {row['input']} | {row['removed_easy']} | {row['kept']} | {row['kept_incomplete']} |")
    lines += ["", "筛选依据是所选系统已有的 correct 布尔标记，本步骤不重新作答或判分。",
              "全员答对仅指本次所选系统；保留结果未重新证明题目有效性，判分错误需先修正再重跑筛选。",
              "逐题判分、去留原因、输入文件 SHA-256 见 filter_report.json。", ""]
    return "\n".join(lines)


def export_filtered_benchmark(bench: Path, results: dict[str, list[dict]], out_dir: Path, *,
                              keep_easy_ratio: float = 0.0, seed: int = 0,
                              preserve_capabilities: tuple[str, ...] | list[str] = (),
                              corpus: Path | None = None, about: Path | None = None,
                              result_paths: list[Path] = ()) -> dict:
    """在新目录导出筛选题库、报告和原语料/协议，不覆盖任何已有目录。"""
    bench, out_dir = Path(bench), Path(out_dir)
    questions = load_questions(bench)
    filtered, report = filter_questions(questions, results, keep_easy_ratio=keep_easy_ratio, seed=seed,
                                        preserve_capabilities=preserve_capabilities)
    copies = {}
    for name, supplied in (("05_corpus.json", corpus), ("00_about.json", about)):
        path = Path(supplied) if supplied is not None else bench.parent / name
        if supplied is not None or path.exists():
            if not path.is_file():
                raise ValueError(f"配套文件不存在: {path}")
            copies[name] = path
    inputs = [bench, *map(Path, result_paths), *copies.values()]
    report["input_files"] = [{"path": str(path.resolve()),
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in inputs]
    report["outputs"] = ["06_grounded_questions.json", "filter_report.json", "filter_report.md", *copies]
    # exist_ok=False 阻止覆盖源目录和旧导出；所有解析、参数校验在创建目录前完成。
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "06_grounded_questions.json").write_text(
        json.dumps(filtered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, source in copies.items():
        shutil.copyfile(source, out_dir / name)
    (out_dir / "filter_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "filter_report.md").write_text(_markdown_report(report), encoding="utf-8")
    return report


def main(argv=None) -> int:
    """离线筛题入口；可直接使用四种 harness 的原始 results.jsonl。"""
    parser = argparse.ArgumentParser(description="剔除全员答对题，或固定种子按比例保留；不调用模型")
    parser.add_argument("--bench", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--results", type=Path, help="eval.multi_system 生成的 results.json")
    source.add_argument("--system-result", action="append", metavar="NAME=PATH", help="逐系统 JSON/JSONL；每个系统指定一次")
    parser.add_argument("--systems", help="逗号分隔的系统名单；默认使用输入声明的全部系统")
    parser.add_argument("--keep-easy-ratio", type=float, default=0.0, help="全员答对题保留比例，0 全剔除，1 全保留")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--preserve-capability", action="append", default=[], help="暂缓筛选的能力名，可重复指定；适用于判分待复核的题")
    parser.add_argument("--out-dir", required=True, type=Path, help="尚不存在的输出目录")
    parser.add_argument("--corpus", type=Path, help="默认复制 bench 同目录的 05_corpus.json")
    parser.add_argument("--about", type=Path, help="默认复制 bench 同目录的 00_about.json")
    args = parser.parse_args(argv)
    try:
        system_files = {}
        for spec in args.system_result or []:
            name, separator, path = spec.partition("=")
            name = name.strip()
            if not separator or not name or not path or name in system_files:
                raise ValueError(f"无效或重复的 --system-result: {spec}")
            system_files[name] = Path(path)
        systems = None if args.systems is None else args.systems.split(",")
        systems = [s.strip() for s in systems] if systems is not None else None
        results = load_results(aggregate=args.results, system_files=system_files, systems=systems)
        paths = [args.results] if args.results else [system_files[s] for s in results]
        report = export_filtered_benchmark(args.bench, results, args.out_dir,
            keep_easy_ratio=args.keep_easy_ratio, seed=args.seed, corpus=args.corpus,
            about=args.about, result_paths=paths, preserve_capabilities=args.preserve_capability)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"out_dir": str(args.out_dir), **report["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
