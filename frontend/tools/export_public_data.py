"""生成可公开的历史 Run、评测和盲审材料副本。

输入默认来自本仓库的上一级 ``memory_bench_factory``。脚本保留目录结构与
研究记录，但会移除环境秘密、第三方端点、本机绝对路径，并替换看起来像
身份证、银行卡、手机号、邮箱或口令的合成值。输出只用于研究追溯和演示，
不能当作未经泄漏的正式 benchmark。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any


TEXT_SUFFIXES = {".json", ".jsonl", ".log", ".md", ".txt", ".py", ".yaml", ".yml"}
SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "passwd",
    "base_url",
    "endpoint",
}
SENSITIVE_KEY_FRAGMENTS = ("银行卡", "身份证", "登录口令", "bankcard", "id_card", "password", "passwd")
SHOWCASE_ORDER = [
    "office__20260717-064826",
    "game__20260625-112210",
    "agent__20260624-214306",
    "cs__20260625-134047",
    "companion__20260624-234524",
    "assistant__20260625-143946",
    "kb__20260625-032549",
]
SHOWCASE_RUNS = set(SHOWCASE_ORDER)
RUN_NOTES = {
    "office__20260717-064826": "盲审 C−；阻断正式 benchmark 发布",
    "game__20260625-112210": "完整流程；配额 UNMET；盲审约 B−/C+",
    "agent__20260624-214306": "完整流程；配额 UNMET；盲审约 B−/B",
    "cs__20260625-134047": "完整流程；配额 UNMET；尚无独立盲审",
    "companion__20260624-234524": "完整流程；配额 UNMET；盲审约 C+/B−",
    "assistant__20260625-143946": "完整流程；配额 UNMET；盲审约 C+/B−",
    "kb__20260625-032549": "完整流程；配额 UNMET；盲审约 B−/B",
    "office__20260608-160053": "目录曾被多轮重跑覆盖；现有盲审与当前主文件不完全一一对应",
}


class Sanitizer:
    """对结构化对象和自由文本做确定性的公开脱敏，并统计替换次数。"""

    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root.resolve()
        self.counts: Counter[str] = Counter()
        self.env_values = self._load_env_values(source_root / ".env")
        self.sensitive_values = self._collect_sensitive_values(source_root / "output" / "runs")
        self.aliases: dict[tuple[str, str], str] = {}

    @staticmethod
    def _load_env_values(path: Path) -> list[str]:
        values: list[str] = []
        if not path.exists():
            return values
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            _, value = line.split("=", 1)
            value = value.strip().strip("'\"")
            if len(value) >= 6:
                values.append(value)
        return sorted(set(values), key=len, reverse=True)

    @staticmethod
    def _collect_sensitive_values(runs_root: Path) -> list[str]:
        """从结构化 Run 中预收集敏感合成值，供日志和预测结果做精确替换。"""
        values: set[str] = set()
        sensitive_terms = ("secret", "pii", "bankcard", "password", "银行卡", "身份证", "口令")
        value_keys = {"value", "gold", "gold_set", "pred", "forbidden", "lure"}

        def add_scalars(value: Any) -> None:
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    add_scalars(item)
            elif isinstance(value, (str, int)):
                token = str(value)
                if len(token) >= 6:
                    values.add(token)

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                context = " ".join(
                    str(value.get(name, ""))
                    for name in ("stype", "field", "pairs_field", "attribute", "sensitive_field")
                ).lower()
                if any(term in context for term in sensitive_terms):
                    for item_key, item_value in value.items():
                        if str(item_key).lower() in value_keys:
                            add_scalars(item_value)
                for item_key, item_value in value.items():
                    normalized = str(item_key).lower().replace("-", "_")
                    if normalized in SECRET_KEYS or any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS):
                        add_scalars(item_value)
                    walk(item_value)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        if not runs_root.exists():
            return []
        for path in sorted(runs_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
                continue
            raw = path.read_text(encoding="utf-8", errors="ignore")
            if path.suffix.lower() == ".jsonl":
                payloads: list[Any] = []
                for line in raw.splitlines():
                    try:
                        payloads.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            else:
                try:
                    payloads = [json.loads(raw)]
                except json.JSONDecodeError:
                    continue
            for payload in payloads:
                walk(payload)
        return sorted(values, key=len, reverse=True)

    def _alias(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self.aliases:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8].upper()
            self.aliases[key] = f"[REDACTED_{kind}_{digest}]"
        return self.aliases[key]

    @staticmethod
    def _luhn(value: str) -> bool:
        total = 0
        parity = len(value) % 2
        for index, char in enumerate(value):
            digit = int(char)
            if index % 2 == parity:
                digit *= 2
                if digit > 9:
                    digit -= 9
            total += digit
        return total % 10 == 0

    @staticmethod
    def _prc_id(value: str) -> bool:
        if not re.fullmatch(r"\d{17}[0-9Xx]", value):
            return False
        weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
        checks = "10X98765432"
        return checks[sum(int(a) * b for a, b in zip(value[:17], weights)) % 11] == value[-1].upper()

    def _replace(self, pattern: str, text: str, label: str, replacement: str | None = None, flags: int = 0) -> str:
        regex = re.compile(pattern, flags)

        def repl(match: re.Match[str]) -> str:
            self.counts[label] += 1
            return replacement if replacement is not None else self._alias(label.upper(), match.group(0))

        return regex.sub(repl, text)

    def text(self, value: str) -> str:
        text = value
        for secret in self.env_values:
            if secret in text:
                self.counts["environment_value"] += text.count(secret)
                text = text.replace(secret, "[REDACTED_ENV_VALUE]")
        for sensitive in self.sensitive_values:
            if sensitive in text:
                occurrences = text.count(sensitive)
                self.counts["known_sensitive_value"] += occurrences
                text = text.replace(sensitive, self._alias("SYNTHETIC_SENSITIVE", sensitive))

        source = str(self.source_root)
        if source in text:
            self.counts["local_path"] += text.count(source)
            text = text.replace(source, "/workspace/memory_bench_factory")
        text = self._replace(
            r"/(?:home|Users)/[^\s\]\[<>()\"']+",
            text,
            "local_path",
            "/workspace/[REDACTED_PATH]",
        )
        text = self._replace(
            r"(?i)\b[A-Z]:\\Users\\[^\s\]\[<>()\"']+",
            text,
            "local_path",
            "C:/workspace/[REDACTED_PATH]",
        )
        text = self._replace(r"\bsk-[A-Za-z0-9_-]{8,}\b", text, "api_key", "[REDACTED_API_KEY]")
        text = self._replace(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]{8,}", text, "authorization", "Bearer [REDACTED]")
        text = self._replace(r"https?://[^\s\]\[<>()\"']+", text, "endpoint", "[REDACTED_ENDPOINT]")
        text = self._replace(r"(?i)(?:密码|口令|password|passwd)\s*(?:是|为|[:：=])\s*[^\s,，。；;]{6,}", text, "credential", "口令：[REDACTED_CREDENTIAL]")
        text = self._replace(
            r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![A-Za-z])",
            text,
            "email",
        )
        text = self._replace(r"(?<!\d)1[3-9]\d{9}(?!\d)", text, "phone")

        def replace_long_number(match: re.Match[str]) -> str:
            token = match.group(0)
            if self._prc_id(token):
                self.counts["prc_id"] += 1
                return self._alias("SYNTHETIC_ID", token)
            if 15 <= len(token) <= 19 and self._luhn(token):
                self.counts["bankcard"] += 1
                return self._alias("SYNTHETIC_BANKCARD", token)
            return token

        return re.sub(r"(?<!\d)\d{15,19}(?!\d)", replace_long_number, text)

    def object(self, value: Any, key: str = "") -> Any:
        normalized = key.lower().replace("-", "_")
        if (normalized in SECRET_KEYS or any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)) and value not in (None, "", False):
            self.counts[f"secret_key:{normalized}"] += 1
            return f"[REDACTED_{normalized.upper()}]"
        if isinstance(value, dict):
            context = " ".join(str(value.get(name, "")) for name in ("stype", "field", "pairs_field", "attribute", "sensitive_field")).lower()
            sensitive_context = any(term in context for term in ("secret", "pii", "bankcard", "password", "银行卡", "身份证", "口令"))
            public: dict[str, Any] = {}
            for item_key, item_value in value.items():
                item_name = str(item_key)
                if sensitive_context and item_name.lower() in {"value", "gold", "gold_set", "pred", "forbidden", "lure"}:
                    self.counts["sensitive_context_value"] += 1
                    public[item_name] = "[REDACTED_SYNTHETIC_SENSITIVE_VALUE]"
                else:
                    public[item_name] = self.object(item_value, item_name)
            return public
        if isinstance(value, list):
            return [self.object(item, key) for item in value]
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, int):
            token = str(value)
            if self._prc_id(token):
                self.counts["prc_id"] += 1
                return self._alias("SYNTHETIC_ID", token)
            if 15 <= len(token) <= 19 and self._luhn(token):
                self.counts["bankcard"] += 1
                return self._alias("SYNTHETIC_BANKCARD", token)
        return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_text_file(source: Path, target: Path, sanitizer: Sanitizer) -> dict[str, Any]:
    """按格式脱敏一个文本文件，返回公开清单所需的来源信息。"""
    raw = source.read_text(encoding="utf-8", errors="replace")
    before = dict(sanitizer.counts)
    if source.suffix == ".json":
        try:
            payload = json.loads(raw)
            public = json.dumps(sanitizer.object(payload), ensure_ascii=False, indent=2) + "\n"
        except json.JSONDecodeError:
            public = sanitizer.text(raw)
    elif source.suffix == ".jsonl":
        rows: list[str] = []
        for line in raw.splitlines():
            try:
                rows.append(json.dumps(sanitizer.object(json.loads(line)), ensure_ascii=False))
            except json.JSONDecodeError:
                rows.append(sanitizer.text(line))
        public = "\n".join(rows) + ("\n" if rows else "")
    else:
        public = sanitizer.text(raw)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(public, encoding="utf-8")
    delta = {key: sanitizer.counts[key] - before.get(key, 0) for key in sanitizer.counts if sanitizer.counts[key] != before.get(key, 0)}
    return {
        "path": target.as_posix(),
        "source_bytes": source.stat().st_size,
        "public_bytes": target.stat().st_size,
        "source_sha256": sha256(source),
        "public_sha256": sha256(target),
        "redactions": delta,
    }


def iter_run_files(source_root: Path) -> list[Path]:
    base = source_root / "output" / "runs"
    return sorted(path for path in base.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES)


def iter_eval_files(source_root: Path) -> list[Path]:
    base = source_root / "output" / "eval"
    files: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(base)
        if any(part.startswith("_") for part in relative.parts):
            continue
        files.append(path)
    return sorted(files)


def iter_review_files(source_root: Path) -> list[Path]:
    base = source_root / "docs" / "anchors"
    files: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        first = path.relative_to(base).parts[0]
        if first.startswith("blind_review") or first.startswith("audit_blind"):
            files.append(path)
    return sorted(files)


def build_run_index(destination_root: Path) -> list[dict[str, Any]]:
    """从公开副本生成面向读者的全量 Run 索引。"""
    rows: list[dict[str, Any]] = []
    runs_root = destination_root / "output" / "runs"
    expected = {
        "00_input.json",
        "01_whitepaper.json",
        "02_world.json",
        "03_orders.json",
        "03_well_posed_report.json",
        "04_questions.json",
        "05_corpus.json",
        "06_grounded_questions.json",
        "06_grounding_report.json",
    }
    for run_dir in sorted(path for path in runs_root.iterdir() if path.is_dir()):
        manifest_path = run_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        except json.JSONDecodeError:
            manifest = {}
        files = {path.name for path in run_dir.iterdir() if path.is_file()}
        stages = manifest.get("stages") or {}
        status = str(manifest.get("status") or ("archive" if run_dir.name == "v10_archive" else "legacy"))
        rows.append({
            "run_id": run_dir.name,
            "status": status,
            "stage_count": sum(1 for name in expected if name in files),
            "has_06": "06_grounded_questions.json" in files,
            "complete": expected.issubset(files),
            "llm_calls": int(manifest.get("llm_calls") or 0),
            "bytes": sum(path.stat().st_size for path in run_dir.rglob("*") if path.is_file()),
            "showcase": run_dir.name in SHOWCASE_RUNS,
            "note": RUN_NOTES.get(run_dir.name, ""),
            "manifest_stages": len(stages),
        })

    by_name = {row["run_id"]: row for row in rows}
    featured = [by_name[name] for name in SHOWCASE_ORDER if name in by_name]
    archive = [row for row in rows if not row["showcase"]]
    lines = [
        "# Historical Run Index",
        "",
        "> 这些是经过公开脱敏的历史研究/调试记录。`done` 仅表示流程结束，不表示质量验收通过。",
        "> 题目、GT 与评测预测已作为 public development artifacts 公开，不能再作为 unseen benchmark 使用。",
        "",
        "## Showcase runs",
        "",
        "| Run | 状态 | 阶段文件 | 06 | LLM calls | 公开说明 |",
        "|---|---:|---:|:---:|---:|---|",
    ]
    for row in featured:
        lines.append(f"| [`{row['run_id']}`](runs/{row['run_id']}/) | {row['status']} | {row['stage_count']}/9 | {'✓' if row['has_06'] else '—'} | {row['llm_calls']} | {row['note']} |")
    lines.extend([
        "",
        "## Complete archive",
        "",
        "| Run | 状态 | 阶段文件 | 06 | 大小 | 备注 |",
        "|---|---:|---:|:---:|---:|---|",
    ])
    for row in archive:
        size = f"{row['bytes'] / 1024 / 1024:.1f} MiB"
        lines.append(f"| [`{row['run_id']}`](runs/{row['run_id']}/) | {row['status']} | {row['stage_count']}/9 | {'✓' if row['has_06'] else '—'} | {size} | {row['note'] or '历史记录'} |")
    lines.extend([
        "",
        "## Reading the archive",
        "",
        "- `manifest.json`：阶段状态、计时、运行配置；端点与环境值已脱敏。",
        "- `prompts.jsonl`：历史调用轨迹的公开脱敏副本；不含真实凭据。",
        "- `00_*`–`06_*`：各阶段结构化产物；合成 PII/口令已替换为稳定占位符。",
        "- `run.log`：公开脱敏日志；本机绝对路径与端点已替换。",
        "- [`PUBLIC_EXPORT_MANIFEST.json`](PUBLIC_EXPORT_MANIFEST.json)：逐文件来源哈希、公开哈希及脱敏统计。",
        "",
    ])
    (destination_root / "output" / "RUN_INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    return rows


def main() -> None:
    """导出全部历史文本记录，并写入可复核的哈希与脱敏清单。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--destination-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    destination_root = args.destination_root.resolve()
    sanitizer = Sanitizer(source_root)

    targets = [destination_root / "output" / "runs", destination_root / "output" / "eval", destination_root / "docs" / "blind-reviews"]
    for target in targets:
        if target.exists():
            shutil.rmtree(target)

    records: list[dict[str, Any]] = []
    groups = [
        (iter_run_files(source_root), source_root / "output" / "runs", destination_root / "output" / "runs", "run"),
        (iter_eval_files(source_root), source_root / "output" / "eval", destination_root / "output" / "eval", "evaluation"),
        (iter_review_files(source_root), source_root / "docs" / "anchors", destination_root / "docs" / "blind-reviews", "review"),
    ]
    for files, source_base, target_base, kind in groups:
        for source in files:
            target = target_base / source.relative_to(source_base)
            record = export_text_file(source, target, sanitizer)
            record["kind"] = kind
            record["path"] = target.relative_to(destination_root).as_posix()
            records.append(record)

    run_rows = build_run_index(destination_root)
    run_names = [row["run_id"] for row in run_rows]
    eval_names = sorted(path.name for path in (destination_root / "output" / "eval").iterdir() if path.is_dir())
    manifest = {
        "format": "memory-forge-public-export/v1",
        "notice": "Sanitized historical research records. Not an unreleased benchmark or a leaderboard.",
        "source_root": "/workspace/memory_bench_factory",
        "showcase_runs": [name for name in SHOWCASE_ORDER if name in set(run_names)],
        "all_runs": run_names,
        "counts": {
            "runs": len(run_names),
            "complete_stage_runs": sum(1 for row in run_rows if row["complete"]),
            "evaluations": len(eval_names),
            "files": len(records),
            "redactions": dict(sorted(sanitizer.counts.items())),
        },
        "excluded": [
            ".env and credentials",
            "binary embedding/QA caches",
            "virtual environments and node_modules",
            "non-run private workspace files",
        ],
        "files": records,
    }
    manifest_path = destination_root / "output" / "PUBLIC_EXPORT_MANIFEST.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
