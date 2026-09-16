#!/usr/bin/env python3
"""Showcase Run 的无环内容摘要与闭世界 manifest 校验。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath


# 这组三件套只为本次正式 Showcase 批次签发 release 结论。把发布身份固定在
# 一个不可由命令行替换的信任根上，避免攻击者自造“更宽松的 profile”后仍得到
# 看似合法的 PASS。若未来发布新批次，应显式更新这些常量并重新接受工具审查。
CANONICAL_BATCH_ID = "game_embryos__20260906-065200"
CANONICAL_RUN_IDS = (
    "game_showcase__20260906-053636",
    "game_abyss__20260906-065200",
    "game_orbit__20260906-065200",
    "game_desert__20260906-065200",
)
CANONICAL_REVIEW_REQUIRED_RUN_IDS = CANONICAL_RUN_IDS[1:]
CANONICAL_RELEASE_PROFILE = Path(
    "docs/production/game_embryos_20260906/RELEASE_PROFILE.json"
)
CANONICAL_RELEASE_PROFILE_SHA256 = (
    "1bbe43d06284817af327d98ed439935bf2f2e7d70a0cbf331147f5d75c4ec27f"
)
CANONICAL_ACCEPTANCE_SUMMARY = Path(
    "docs/production/game_embryos_20260906/FINAL_MACHINE_ACCEPTANCE.json"
)
CANONICAL_BATCH_ARCHIVE_NAME = "game_story_embryos__20260906-065200"
CANONICAL_RECEIPT = Path(
    "artifacts/game_story_embryos__20260906-065200.receipt.json"
)


# 这些文件是验收或冻结阶段生成的“证明”，不能再进入它们证明的内容摘要。
CONTENT_DIGEST_EXCLUDES = {
    "manifest.json",
    "production/FINAL_ACCEPTANCE.json",
    "production/RUN_AUDIT.json",
}

# 终审者签字前的目标摘要还必须排除终审报告与其机器索引，避免自哈希循环。
REVIEW_TARGET_EXCLUDES = CONTENT_DIGEST_EXCLUDES | {
    "production/FINAL_REVIEW.md",
    "production/review_chain.json",
}


def sha256_file(path: Path) -> str:
    """流式计算普通文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular_files(root: Path, excludes: set[str] | None = None) -> list[Path]:
    """返回稳定排序的普通文件；发现符号链接或特殊文件时立即失败。"""
    excluded = excludes or set()
    files = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        if path.is_symlink():
            raise ValueError(f"内容树不允许符号链接:{relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"内容树出现特殊文件:{relative}")
        files.append(path)
    return files


def tree_digest(root: Path, excludes: set[str] | None = None) -> str:
    """按相对路径、文件长度和文件哈希计算稳定的闭世界摘要。"""
    digest = hashlib.sha256()
    for path in regular_files(root, excludes):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        size = path.stat().st_size
        file_hash = bytes.fromhex(sha256_file(path))
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(size.to_bytes(8, "big"))
        digest.update(file_hash)
    return digest.hexdigest()


def review_target_digest(run_dir: Path) -> str:
    """计算作者修订内容摘要，供非作者终审绑定。"""
    return tree_digest(run_dir, REVIEW_TARGET_EXCLUDES)


def release_content_digest(run_dir: Path) -> str:
    """计算包含终审记录、但排除派生证明文件的发布内容摘要。"""
    return tree_digest(run_dir, CONTENT_DIGEST_EXCLUDES)


def manifest_inventory(run_dir: Path) -> list[dict]:
    """生成除 manifest 自身外的闭世界文件清单。"""
    entries = []
    for path in regular_files(run_dir, {"manifest.json"}):
        entries.append(
            {
                "path": path.relative_to(run_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return entries


def validate_manifest(run_dir: Path) -> list[str]:
    """验证 manifest 路径安全、唯一，且与磁盘文件形成完全闭包。"""
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return ["缺少普通文件 manifest.json"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [f"manifest 无法解析:{exc!r}"]

    declared = manifest.get("files")
    if not isinstance(declared, list):
        return ["manifest.files 不是数组"]

    failures = []
    declared_by_path: dict[str, dict] = {}
    for item in declared:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            failures.append(f"非法清单项:{item!r}")
            continue
        raw_path = item["path"]
        pure = PurePosixPath(raw_path)
        if pure.is_absolute() or ".." in pure.parts or raw_path != pure.as_posix():
            failures.append(f"不安全或非规范路径:{raw_path}")
            continue
        if raw_path in declared_by_path:
            failures.append(f"重复路径:{raw_path}")
            continue
        declared_by_path[raw_path] = item

    try:
        actual_entries = manifest_inventory(run_dir)
    except ValueError as exc:
        failures.append(str(exc))
        actual_entries = []
    actual_by_path = {item["path"]: item for item in actual_entries}
    missing = sorted(set(declared_by_path) - set(actual_by_path))
    undeclared = sorted(set(actual_by_path) - set(declared_by_path))
    if missing:
        failures.append(f"清单声明但磁盘缺失:{missing}")
    if undeclared:
        failures.append(f"磁盘存在但清单未声明:{undeclared}")

    for relative in sorted(set(declared_by_path) & set(actual_by_path)):
        declared_item = declared_by_path[relative]
        actual_item = actual_by_path[relative]
        if declared_item.get("bytes") != actual_item["bytes"]:
            failures.append(f"大小不符:{relative}")
        if declared_item.get("sha256") != actual_item["sha256"]:
            failures.append(f"SHA-256 不符:{relative}")

    finalization = manifest.get("finalization") or {}
    declared_count = finalization.get("file_count_excluding_manifest")
    if declared_count != len(actual_entries):
        failures.append(
            f"file_count_excluding_manifest 不符:{declared_count}!={len(actual_entries)}"
        )
    return failures
