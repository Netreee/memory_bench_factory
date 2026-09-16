#!/usr/bin/env python3
"""按发布验收摘要冻结 Showcase 批次，并生成可校验的移交收据。

发布顺序是无环的：批审先签发不可变内容摘要；本工具只消费 PASS 摘要，
在 staging 中生成 manifest、前端镜像和归档，全部复核后才逐目标原子替换。
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.audit_showcase_batch import (  # noqa: E402
    audit_run as recompute_showcase_audit,
    distinctiveness as recompute_distinctiveness,
    validate_distinctiveness_profiles,
)
from tools.showcase_integrity import (  # noqa: E402
    CANONICAL_ACCEPTANCE_SUMMARY,
    CANONICAL_BATCH_ARCHIVE_NAME,
    CANONICAL_BATCH_ID,
    CANONICAL_RECEIPT,
    CANONICAL_RELEASE_PROFILE,
    CANONICAL_RELEASE_PROFILE_SHA256,
    CANONICAL_REVIEW_REQUIRED_RUN_IDS,
    CANONICAL_RUN_IDS,
    manifest_inventory,
    regular_files,
    release_content_digest,
    sha256_file,
    tree_digest,
    validate_manifest,
)


SAFE_ARCHIVE_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
REQUIRED_BATCH_EXTRAS = (
    Path("docs/production/game_embryos_20260906"),
    Path("tools"),
    Path("pipeline"),
    Path("eval"),
    Path("tests"),
    Path("requirements.txt"),
    Path("config.py"),
)


def read_json(path: Path) -> Any:
    """读取 UTF-8 JSON。"""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Any) -> None:
    """在同目录写临时文件后原子替换。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def ensure_under_root(path: Path, label: str) -> Path:
    """要求路径位于仓库根目录内。"""
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"{label} 不在仓库内:{resolved}") from exc
    return resolved


def paths_overlap(left: Path, right: Path) -> bool:
    """返回两个已解析路径是否相同或存在祖先/后代关系。"""
    return left == right or left in right.parents or right in left.parents


def reject_overlaps(
    labelled_paths: list[tuple[str, Path]],
    *,
    allow: set[frozenset[str]] | None = None,
) -> None:
    """在任何写入前拒绝可导致输入被覆盖或输出相撞的路径。"""
    allowed = allow or set()
    for index, (left_label, left) in enumerate(labelled_paths):
        for right_label, right in labelled_paths[index + 1:]:
            if frozenset({left_label, right_label}) in allowed:
                continue
            if paths_overlap(left, right):
                raise ValueError(
                    f"路径冲突:{left_label}={left} / {right_label}={right}"
                )


def require_canonical_release_inputs(
    summary_path: Path,
    run_dirs: list[Path],
) -> None:
    """绑定本批次唯一的摘要和正式 Run 实体，不只比较 basename。"""
    expected_summary = ROOT / CANONICAL_ACCEPTANCE_SUMMARY
    if expected_summary.is_symlink() or summary_path != expected_summary.resolve():
        raise ValueError(
            "acceptance summary 必须是固定信任根:"
            f"{CANONICAL_ACCEPTANCE_SUMMARY.as_posix()}"
        )
    expected_runs = [
        (ROOT / "output" / "runs" / run_id).resolve()
        for run_id in CANONICAL_RUN_IDS
    ]
    if run_dirs != expected_runs:
        raise ValueError(
            "输入必须是固定四个正式 Run 实体:"
            f"actual={[str(path) for path in run_dirs]}"
        )
    for run_dir in run_dirs:
        if run_dir.is_symlink():
            raise ValueError(f"正式 Run 不得为符号链接:{run_dir}")


def assert_hashes_unchanged(expected: dict[Path, str], phase: str) -> None:
    """确保验收输入与执行工具在发布过程中没有被改写。"""
    for path, expected_hash in expected.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"{phase}输入缺失或变为符号链接:{path}")
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise ValueError(
                f"{phase}输入 SHA-256 漂移:{path}:"
                f"{expected_hash}!={actual_hash}"
            )


def input_path_digest(path: Path) -> str:
    """对 extra 文件或目录生成稳定摘要。"""
    if path.is_dir():
        return tree_digest(path)
    if path.is_file() and not path.is_symlink():
        return sha256_file(path)
    raise ValueError(f"extra 不是普通文件/目录:{path}")


def assert_extra_inputs_unchanged(expected: dict[Path, str], phase: str) -> None:
    """防止批次记录或源码在打包途中漂移。"""
    for path, expected_digest in expected.items():
        actual_digest = input_path_digest(path)
        if actual_digest != expected_digest:
            raise ValueError(
                f"{phase}extra 输入摘要漂移:{path}:"
                f"{expected_digest}!={actual_digest}"
            )


def validate_acceptance(summary_path: Path, run_dirs: list[Path]) -> tuple[dict, list[str]]:
    """验证唯一批次结论与当前 Run 内容完全绑定。"""
    failures = []
    try:
        summary = read_json(summary_path)
    except Exception as exc:  # noqa: BLE001
        return {}, [f"验收摘要无法读取:{exc!r}"]

    run_ids = [path.name for path in run_dirs]
    result_by_id = {
        item.get("run_id"): item
        for item in summary.get("runs", [])
        if isinstance(item, dict)
    }
    if summary.get("schema_version") != 2:
        failures.append(f"验收摘要 schema_version 不是 2:{summary.get('schema_version')!r}")
    if summary.get("batch_id") != CANONICAL_BATCH_ID:
        failures.append("验收摘要不是本批次的固定 batch_id")
    if run_ids != list(CANONICAL_RUN_IDS):
        failures.append("输入 Run 不是本批次的固定集合/顺序")
    if summary.get("status") != "PASS" or summary.get("release_mode") is not True:
        failures.append("验收摘要不是 release_mode 的 PASS")
    if summary.get("release_failures") != []:
        failures.append(f"验收摘要仍有 release_failures:{summary.get('release_failures')!r}")
    if list(result_by_id) != run_ids:
        failures.append(
            f"验收 Run 集合/顺序不符:summary={list(result_by_id)},input={run_ids}"
        )
    if len(summary.get("runs", [])) != len(result_by_id):
        failures.append("验收摘要含重复或无 run_id 的 Run")

    profile: dict = {}
    profile_raw = summary.get("release_profile")
    if not isinstance(profile_raw, str) or not profile_raw:
        failures.append("验收摘要缺少 release profile 路径")
    else:
        profile_path = Path(profile_raw)
        if not profile_path.is_absolute():
            profile_path = ROOT / profile_path
        try:
            profile_path = ensure_under_root(profile_path, "release profile")
            canonical_profile_path = (ROOT / CANONICAL_RELEASE_PROFILE).resolve()
            if profile_path != canonical_profile_path:
                failures.append("release profile 路径不是固定信任根")
            current_profile_hash = sha256_file(profile_path)
            if current_profile_hash != CANONICAL_RELEASE_PROFILE_SHA256:
                failures.append("release profile 内容与工具固定哈希不符")
            if current_profile_hash != summary.get("release_profile_sha256"):
                failures.append("release profile 在验收后发生变化")
            profile = read_json(profile_path)
            if profile.get("profile_version") != 1:
                failures.append("release profile_version 不是 1")
            if profile.get("expected_run_ids") != run_ids:
                failures.append("release profile 的 expected_run_ids 与输入不符")
            if profile.get("batch_id") != summary.get("batch_id"):
                failures.append("release profile 与摘要 batch_id 不符")
            if profile.get("review_required_run_ids") != list(
                CANONICAL_REVIEW_REQUIRED_RUN_IDS
            ):
                failures.append("release profile 未精确要求三个新 Run 审查链")
        except Exception as exc:  # noqa: BLE001
            failures.append(str(exc))

    tool_hashes = summary.get("tool_hashes") or {}
    required_tool_hashes = {
        "tools/audit_showcase_batch.py",
        "tools/showcase_integrity.py",
        "tools/audit_run.py",
        "eval/judge.py",
    }
    if set(tool_hashes) != required_tool_hashes:
        failures.append(
            f"验收工具哈希集合不完整:{sorted(tool_hashes)}"
        )
    for relative, expected_hash in tool_hashes.items():
        tool_path = ROOT / relative
        try:
            tool_path = ensure_under_root(tool_path, "验收工具")
            if sha256_file(tool_path) != expected_hash:
                failures.append(f"验收工具在签发后变化:{relative}")
        except Exception as exc:  # noqa: BLE001
            failures.append(str(exc))

    for run_dir in run_dirs:
        result = result_by_id.get(run_dir.name)
        if not result:
            continue
        if result.get("status") != "PASS" or result.get("failures") or result.get("warnings"):
            failures.append(f"{run_dir.name} 单 Run 验收非全绿")
        if (result.get("repository_audit") or {}).get("status") != "PASS":
            failures.append(f"{run_dir.name} 仓库原生审计非 PASS")
        strict_checks = result.get("strict_scoring_checks") or []
        expected_strict_count = len(
            read_json(run_dir / "strict_scoring_contract.json").get("items", [])
        )
        if (
            len(strict_checks) != expected_strict_count
            or not strict_checks
            or len({item.get("qid") for item in strict_checks}) != len(strict_checks)
            or not all(item.get("ok") for item in strict_checks)
        ):
            failures.append(f"{run_dir.name} 严格评分检查非全绿")
        metrics = result.get("metrics") or {}
        if (
            metrics.get("strict_scoring_contracts") != expected_strict_count
            or metrics.get("strict_scoring_passed") != expected_strict_count
        ):
            failures.append(f"{run_dir.name} 严格评分计数不一致")
        question_checks = result.get("question_checks") or []
        if (
            len(question_checks) != 18
            or len({item.get("qid") for item in question_checks}) != 18
            or not all(item.get("ok") for item in question_checks)
        ):
            failures.append(f"{run_dir.name} 题目证据闭包非全绿")
        well_posed = (result.get("formal_well_posed") or {}).get("overall") or {}
        grounding = (result.get("formal_grounding") or {}).get("overall") or {}
        if well_posed.get("n") != 18 or well_posed.get("well_posed") != 18:
            failures.append(f"{run_dir.name} 正式 well-posed 非 18/18")
        if grounding.get("n") != 18 or grounding.get("grounded") != 18:
            failures.append(f"{run_dir.name} 正式 grounding 非 18/18")
        batch = result.get("batch") or {}
        if (
            batch.get("batch_status") != "PASS"
            or batch.get("batch_id") != summary.get("batch_id")
            or batch.get("release_profile_sha256") != summary.get("release_profile_sha256")
        ):
            failures.append(f"{run_dir.name} per-run batch 绑定不符")
        required_reviews = set(profile.get("review_required_run_ids") or [])
        if run_dir.name in required_reviews and (result.get("review_chain") or {}).get("status") != "PASS":
            failures.append(f"{run_dir.name} 强制 review chain 非 PASS")
        current_digest = release_content_digest(run_dir)
        if result.get("content_tree_sha256") != current_digest:
            failures.append(
                f"{run_dir.name} 内容已在验收后变化:"
                f"{result.get('content_tree_sha256')}!={current_digest}"
            )
        per_run_acceptance = run_dir / "production/FINAL_ACCEPTANCE.json"
        if not per_run_acceptance.is_file() or read_json(per_run_acceptance) != result:
            failures.append(f"{run_dir.name} 的 FINAL_ACCEPTANCE 与批次摘要不一致")

    # 验收 JSON 不是不可伪造的签名。Finalizer 因此使用已被摘要哈希
    # 绑定的当前 auditor 重算全部结果，并要求与摘要逐字段一致。
    try:
        recomputed_pairs: list[tuple[Path, dict]] = []
        reviewed_ids = set(profile.get("review_required_run_ids") or [])
        for run_dir in run_dirs:
            recomputed = recompute_showcase_audit(
                run_dir,
                require_review_chain=run_dir.name in reviewed_ids,
                expected_roles=(profile.get("roles") or {}).get(run_dir.name),
                release_mode=True,
            )
            recomputed_pairs.append((run_dir, recomputed))
        recomputed_comparisons = recompute_distinctiveness(recomputed_pairs)
        recomputed_structured, structured_failures = validate_distinctiveness_profiles(
            list(CANONICAL_RUN_IDS),
            profile.get("distinctiveness_profiles") or {},
        )
        recomputed_batch_pass = (
            not structured_failures
            and all(item[1].get("status") == "PASS" for item in recomputed_pairs)
            and all(item.get("status") == "PASS" for item in recomputed_comparisons)
        )
        for _, recomputed in recomputed_pairs:
            recomputed["batch"] = {
                "batch_id": CANONICAL_BATCH_ID,
                "batch_status": "PASS" if recomputed_batch_pass else "FAIL",
                "release_mode": True,
                "release_profile_sha256": CANONICAL_RELEASE_PROFILE_SHA256,
            }
            if recomputed != result_by_id.get(recomputed.get("run_id")):
                failures.append(
                    f"{recomputed.get('run_id')} 验收摘要与 finalizer 现场重算不一致"
                )
        if recomputed_comparisons != (summary.get("distinctiveness") or []):
            failures.append("验收摘要的字符差异比较与现场重算不一致")
        if recomputed_structured != summary.get("structured_distinctiveness"):
            failures.append("验收摘要的七维差异档案与现场重算不一致")
        if not recomputed_batch_pass:
            failures.append("finalizer 现场重算的整批结论非 PASS")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"finalizer 现场重算验收失败:{exc!r}")

    comparisons = summary.get("distinctiveness") or []
    expected_pairs = len(run_ids) * (len(run_ids) - 1) // 2
    if len(comparisons) != expected_pairs or not all(
        item.get("status") == "PASS" for item in comparisons
    ):
        failures.append("字符相似度比较未完整通过")
    if (summary.get("structured_distinctiveness") or {}).get("status") != "PASS":
        failures.append("七维结构化差异档案未通过")

    return summary, failures


def refresh_manifest(
    run_dir: Path,
    summary: dict,
    result: dict,
    acceptance_summary_sha256: str,
) -> dict:
    """在 staged Run 内生成覆盖全部文件的最终 manifest。"""
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    files = manifest_inventory(run_dir)
    manifest["files"] = files
    manifest["finalization"] = {
        "status": "frozen_after_release_acceptance",
        "batch_id": summary.get("batch_id"),
        "acceptance_summary_sha256": acceptance_summary_sha256,
        "release_profile_sha256": summary.get("release_profile_sha256"),
        "accepted_content_tree_sha256": result.get("content_tree_sha256"),
        "file_count_excluding_manifest": len(files),
        "hash_algorithm": "sha256",
        "note": "manifest 不自哈希；验收/冻结证明不进入其所证明的内容摘要",
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def normalized_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """归一化 tar 元数据，使同一内容重复归档得到稳定字节。"""
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def add_path_deterministic(
    handle: tarfile.TarFile,
    source: Path,
    arcname: PurePosixPath,
    seen: set[str],
) -> None:
    """以稳定顺序加入普通文件树，并拒绝重复归档路径。"""
    paths = [source]
    if source.is_dir():
        regular_files(source)
        paths.extend(sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()))
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"归档不允许符号链接:{path}")
        relative = Path() if path == source else path.relative_to(source)
        member_name = (arcname / PurePosixPath(relative.as_posix())).as_posix()
        if member_name in seen:
            raise ValueError(f"归档路径重复:{member_name}")
        seen.add(member_name)
        handle.add(path, arcname=member_name, recursive=False, filter=normalized_tar_info)


def make_deterministic_archive(
    archive: Path,
    items: list[tuple[Path, PurePosixPath]],
) -> None:
    """生成 gzip mtime 与 tar 元数据均固定的归档。"""
    seen: set[str] = set()
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as handle:
                for source, arcname in items:
                    add_path_deterministic(handle, source, arcname, seen)


def verify_archive(archive: Path, expected_runs: dict[str, str]) -> None:
    """安全解包临时归档，并复核其中 Run 的 manifest 与内容摘要。"""
    with tempfile.TemporaryDirectory(prefix="showcase-verify-") as raw_temp:
        temp = Path(raw_temp)
        with tarfile.open(archive, "r:gz") as handle:
            for member in handle.getmembers():
                pure = PurePosixPath(member.name)
                if pure.is_absolute() or ".." in pure.parts or member.issym() or member.islnk():
                    raise ValueError(f"归档成员不安全:{member.name}")
            handle.extractall(temp, filter="data")
        for relative, expected_digest in expected_runs.items():
            extracted = temp / relative
            manifest_failures = validate_manifest(extracted)
            if manifest_failures:
                raise ValueError(f"解包 manifest 失败:{relative}:{manifest_failures}")
            if release_content_digest(extracted) != expected_digest:
                raise ValueError(f"解包内容摘要不符:{relative}")


def remove_path(path: Path) -> None:
    """删除一个交易内新目标；只用于失败回滚。"""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def transactional_replace(
    staged: Path,
    destination: Path,
    backup_root: Path,
    ledger: list[tuple[Path, Path | None, str]],
    label: str,
) -> None:
    """替换一个发布目标，并把旧值保存在唯一 staging 树供批次回滚。"""
    if not staged.exists() or staged.is_symlink():
        raise ValueError(f"交易 staged 目标缺失或为符号链接:{label}:{staged}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if destination.exists() or destination.is_symlink():
        backup = backup_root / f"{len(ledger):02d}-{uuid.uuid4().hex}"
        os.replace(destination, backup)
    try:
        os.replace(staged, destination)
    except BaseException:
        if backup is not None and backup.exists():
            os.replace(backup, destination)
        raise
    ledger.append((destination, backup, label))


def rollback_transaction(
    ledger: list[tuple[Path, Path | None, str]],
) -> list[str]:
    """逆序撤销已提交目标，并返回任何回滚异常。"""
    failures = []
    for destination, backup, label in reversed(ledger):
        try:
            remove_path(destination)
            if backup is not None and backup.exists():
                os.replace(backup, destination)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label}:{exc!r}")
    return failures


def main() -> int:
    """执行全批次预检、staging、复核、提交和外部收据写入。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--acceptance-summary", required=True, type=Path)
    parser.add_argument(
        "--frontend-root", type=Path, default=ROOT / "frontend" / "output" / "runs"
    )
    parser.add_argument("--artifacts-dir", type=Path, default=ROOT / "artifacts")
    parser.add_argument("--batch-archive", required=True, help="批次压缩包文件名（不含 .tar.gz）")
    parser.add_argument("--receipt-output", required=True, type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="完成全量 staging 与解包复核，但不修改正式 Run、镜像、归档或收据",
    )
    parser.add_argument(
        "--extra", action="append", default=[], type=Path,
        help="额外纳入批次包的仓库内文件或目录，可重复",
    )
    args = parser.parse_args()

    if (
        not SAFE_ARCHIVE_BASENAME.fullmatch(args.batch_archive)
        or args.batch_archive in {".", ".."}
        or args.batch_archive.endswith(".tar.gz")
    ):
        raise SystemExit(
            "batch archive 必须是不含路径分隔符且不含 .tar.gz 后缀的安全 basename"
        )
    if args.batch_archive != CANONICAL_BATCH_ARCHIVE_NAME:
        raise SystemExit(
            f"本批次总包名必须固定为:{CANONICAL_BATCH_ARCHIVE_NAME}"
        )

    raw_run_dirs = list(args.run_dirs)
    for raw_run in raw_run_dirs:
        if raw_run.is_symlink():
            raise SystemExit(f"输入 Run 不得为符号链接:{raw_run}")
    run_dirs = [ensure_under_root(path, "Run") for path in args.run_dirs]
    if len({path.name for path in run_dirs}) != len(run_dirs):
        raise SystemExit("输入 Run basename 必须唯一")
    for run_dir in run_dirs:
        if not run_dir.is_dir() or not (run_dir / "manifest.json").is_file():
            raise SystemExit(f"不是完整 Run:{run_dir}")
        regular_files(run_dir)

    summary_path = ensure_under_root(args.acceptance_summary, "acceptance summary")
    try:
        require_canonical_release_inputs(summary_path, run_dirs)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    summary, acceptance_failures = validate_acceptance(summary_path, run_dirs)
    if acceptance_failures:
        raise SystemExit("发布验收前置失败:\n- " + "\n- ".join(acceptance_failures))

    extras = [ensure_under_root(path, "extra") for path in args.extra]
    if len(set(extras)) != len(extras) or any(not path.exists() for path in extras):
        raise SystemExit("extra 必须存在且不得重复")
    for left_index, left in enumerate(extras):
        if left.is_dir():
            regular_files(left)
        elif left.is_symlink() or not left.is_file():
            raise SystemExit(f"extra 不是普通文件/目录:{left}")
        for right in extras[left_index + 1:]:
            if left in right.parents or right in left.parents:
                raise SystemExit(f"extra 互相包含，会造成重复归档:{left} / {right}")
    required_extras = [(ROOT / path).resolve() for path in REQUIRED_BATCH_EXTRAS]
    if extras != required_extras:
        raise SystemExit(
            "正式批次 extra 必须按固定顺序完整包含:"
            f"{[path.as_posix() for path in REQUIRED_BATCH_EXTRAS]}"
        )

    for label, raw_path in [
        ("frontend root", args.frontend_root),
        ("artifacts dir", args.artifacts_dir),
        ("receipt output", args.receipt_output),
    ]:
        if raw_path.is_symlink():
            raise SystemExit(f"{label} 不得为符号链接:{raw_path}")
    frontend_root = ensure_under_root(args.frontend_root, "frontend root")
    artifacts_dir = ensure_under_root(args.artifacts_dir, "artifacts dir")
    receipt_output = ensure_under_root(args.receipt_output, "receipt output")
    if receipt_output.parent != artifacts_dir or receipt_output.suffix != ".json":
        raise SystemExit(
            "receipt output 必须是 artifacts dir 直接下的 .json 文件"
        )
    if receipt_output.name != CANONICAL_RECEIPT.name:
        raise SystemExit(f"本批次 receipt 文件名必须固定为:{CANONICAL_RECEIPT.name}")

    if args.batch_archive in {path.name for path in run_dirs}:
        raise SystemExit("batch archive basename 不得与任一 Run id 相同")

    profile_path = (ROOT / CANONICAL_RELEASE_PROFILE).resolve()
    audit_tool_path = (ROOT / "tools/audit_showcase_batch.py").resolve()
    integrity_tool_path = (ROOT / "tools/showcase_integrity.py").resolve()
    repository_audit_path = (ROOT / "tools/audit_run.py").resolve()
    judge_path = (ROOT / "eval/judge.py").resolve()
    finalizer_path = Path(__file__).resolve()
    protected_files = [
        ("acceptance summary", summary_path),
        ("release profile", profile_path),
        ("batch auditor", audit_tool_path),
        ("integrity tool", integrity_tool_path),
        ("repository auditor", repository_audit_path),
        ("strict judge", judge_path),
        ("finalizer", finalizer_path),
    ]
    input_trees = (
        [(f"source Run {path.name}", path) for path in run_dirs]
        + [(f"extra {path.relative_to(ROOT)}", path) for path in extras]
    )
    if paths_overlap(frontend_root, artifacts_dir):
        raise SystemExit(
            f"frontend root 与 artifacts dir 不得重叠:{frontend_root} / {artifacts_dir}"
        )
    for output_label, output_root in [
        ("frontend root", frontend_root),
        ("artifacts dir", artifacts_dir),
    ]:
        for input_label, input_path in input_trees + protected_files:
            if paths_overlap(output_root, input_path):
                raise SystemExit(
                    f"输出根不得与输入重叠:"
                    f"{output_label}={output_root} / {input_label}={input_path}"
                )

    final_run_archives = {
        path.name: artifacts_dir / f"{path.name}.tar.gz" for path in run_dirs
    }
    final_batch_archive = artifacts_dir / f"{args.batch_archive}.tar.gz"
    output_files = (
        [(f"run archive {run_id}", path) for run_id, path in final_run_archives.items()]
        + [("batch archive", final_batch_archive), ("receipt", receipt_output)]
    )
    try:
        reject_overlaps(output_files)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    for output_label, output_path in output_files:
        if output_path.is_symlink() or output_path.is_dir():
            raise SystemExit(f"{output_label} 目标不得为符号链接/目录:{output_path}")
        for input_label, input_path in input_trees + protected_files:
            if paths_overlap(output_path, input_path):
                raise SystemExit(
                    f"输出文件不得覆盖或落入输入树:"
                    f"{output_label}={output_path} / {input_label}={input_path}"
                )
        if paths_overlap(output_path, frontend_root):
            raise SystemExit(
                f"输出文件不得落入 frontend root:"
                f"{output_label}={output_path} / {frontend_root}"
            )

    for label, output_root in [
        ("frontend root", frontend_root),
        ("artifacts dir", artifacts_dir),
    ]:
        if output_root.exists() and not output_root.is_dir():
            raise SystemExit(f"{label} 已存在且不是目录:{output_root}")

    result_by_id = {item["run_id"]: item for item in summary["runs"]}
    acceptance_summary_sha256 = sha256_file(summary_path)
    protected_input_hashes = {
        path: sha256_file(path) for _, path in protected_files
    }
    extra_input_digests = {path: input_path_digest(path) for path in extras}
    # dry-run 与正式执行的一切准备写入都限定在同一个唯一临时树。
    # 该树位于仓库文件系统，使正式提交可使用原子 rename。
    staging_root = Path(tempfile.mkdtemp(prefix=".showcase-finalize-", dir=ROOT))
    staged_runs: dict[str, Path] = {}
    staged_frontend: dict[str, Path] = {}
    staged_archives: dict[str, Path] = {}
    run_receipts = []
    transaction_ledger: list[tuple[Path, Path | None, str]] = []
    created_output_roots: list[Path] = []
    preserve_staging_on_rollback_failure = False
    try:
        # 第一阶段：所有输出只进入 staging；任一失败都不会碰正式镜像或归档。
        for run_dir in run_dirs:
            run_id = run_dir.name
            staged_run = staging_root / "runs" / run_id
            staged_run.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(run_dir, staged_run)
            refresh_manifest(
                staged_run,
                summary,
                result_by_id[run_id],
                acceptance_summary_sha256,
            )
            manifest_failures = validate_manifest(staged_run)
            if manifest_failures:
                raise ValueError(f"staged manifest 失败:{run_id}:{manifest_failures}")
            if release_content_digest(staged_run) != result_by_id[run_id]["content_tree_sha256"]:
                raise ValueError(f"staged 内容摘要漂移:{run_id}")
            staged_runs[run_id] = staged_run

            frontend_stage = staging_root / "frontend" / run_id
            frontend_stage.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staged_run, frontend_stage)
            if validate_manifest(frontend_stage):
                raise ValueError(f"前端 staged manifest 失败:{run_id}")
            if tree_digest(frontend_stage) != tree_digest(staged_run):
                raise ValueError(f"前端 staged 完整树不一致:{run_id}")
            staged_frontend[run_id] = frontend_stage

            archive_stage = staging_root / f"{run_id}.tar.gz"
            make_deterministic_archive(
                archive_stage,
                [(staged_run, PurePosixPath(run_id))],
            )
            verify_archive(
                archive_stage,
                {run_id: result_by_id[run_id]["content_tree_sha256"]},
            )
            staged_archives[run_id] = archive_stage

        batch_stage = staging_root / f"{args.batch_archive}.tar.gz"
        batch_items = [
            (staged_runs[run_dir.name], PurePosixPath("output/runs") / run_dir.name)
            for run_dir in run_dirs
        ]
        batch_items.extend(
            (extra, PurePosixPath(extra.relative_to(ROOT).as_posix())) for extra in extras
        )
        make_deterministic_archive(batch_stage, batch_items)
        verify_archive(
            batch_stage,
            {
                f"output/runs/{run_dir.name}": result_by_id[run_dir.name]["content_tree_sha256"]
                for run_dir in run_dirs
            },
        )
        assert_hashes_unchanged(protected_input_hashes, "staging 完成后")
        assert_extra_inputs_unchanged(extra_input_digests, "staging 完成后")
        for run_dir in run_dirs:
            if release_content_digest(run_dir) != result_by_id[run_dir.name][
                "content_tree_sha256"
            ]:
                raise ValueError(f"staging 完成后源 Run 内容漂移:{run_dir.name}")
            if read_json(
                run_dir / "production/FINAL_ACCEPTANCE.json"
            ) != result_by_id[run_dir.name]:
                raise ValueError(f"staging 完成后单 Run 验收证明漂移:{run_dir.name}")

        if args.dry_run:
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "dry_run": True,
                        "batch_id": summary.get("batch_id"),
                        "verified_runs": [path.name for path in run_dirs],
                        "acceptance_summary_sha256": acceptance_summary_sha256,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        # 第二阶段：所有 staged 输出已验证，开始提交。
        for output_root in (frontend_root, artifacts_dir):
            if not output_root.exists():
                output_root.mkdir(parents=True)
                created_output_roots.append(output_root)
        backup_root = staging_root / "transaction-backups"
        for run_dir in run_dirs:
            run_id = run_dir.name
            source_manifest = staged_runs[run_id] / "manifest.json"
            transactional_replace(
                source_manifest,
                run_dir / "manifest.json",
                backup_root,
                transaction_ledger,
                f"source manifest {run_id}",
            )
            manifest_failures = validate_manifest(run_dir)
            if manifest_failures:
                raise ValueError(f"源 Run manifest 提交后失败:{run_id}:{manifest_failures}")

        for run_dir in run_dirs:
            run_id = run_dir.name
            transactional_replace(
                staged_frontend[run_id],
                frontend_root / run_id,
                backup_root,
                transaction_ledger,
                f"frontend mirror {run_id}",
            )

        for run_dir in run_dirs:
            run_id = run_dir.name
            final_archive = final_run_archives[run_id]
            transactional_replace(
                staged_archives[run_id],
                final_archive,
                backup_root,
                transaction_ledger,
                f"run archive {run_id}",
            )
            full_run_digest = tree_digest(run_dir)
            frontend_digest = tree_digest(frontend_root / run_id)
            if full_run_digest != frontend_digest:
                raise ValueError(f"发布后源 Run 与前端镜像不一致:{run_id}")
            run_receipts.append(
                {
                    "run_id": run_id,
                    "accepted_content_tree_sha256": result_by_id[run_id]["content_tree_sha256"],
                    "manifest_sha256": sha256_file(run_dir / "manifest.json"),
                    "full_run_tree_sha256": full_run_digest,
                    "frontend_full_tree_sha256": frontend_digest,
                    "archive": str(final_archive.relative_to(ROOT)),
                    "archive_bytes": final_archive.stat().st_size,
                    "archive_sha256": sha256_file(final_archive),
                }
            )

        transactional_replace(
            batch_stage,
            final_batch_archive,
            backup_root,
            transaction_ledger,
            "batch archive",
        )
        assert_hashes_unchanged(protected_input_hashes, "发布提交后")

        # 不仅信任 staging；对最终路径上的每个归档再做一次解包校验。
        for run_dir in run_dirs:
            run_id = run_dir.name
            verify_archive(
                final_run_archives[run_id],
                {run_id: result_by_id[run_id]["content_tree_sha256"]},
            )
        verify_archive(
            final_batch_archive,
            {
                f"output/runs/{run_dir.name}": result_by_id[run_dir.name]["content_tree_sha256"]
                for run_dir in run_dirs
            },
        )
        receipt = {
            "schema_version": 1,
            "batch_id": summary.get("batch_id"),
            "finalized_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "receipt_path": str(receipt_output.relative_to(ROOT)),
            "acceptance_summary": str(summary_path.relative_to(ROOT)),
            "acceptance_summary_sha256": acceptance_summary_sha256,
            "release_profile": CANONICAL_RELEASE_PROFILE.as_posix(),
            "release_profile_sha256": summary.get("release_profile_sha256"),
            "batch_auditor_sha256": protected_input_hashes[audit_tool_path],
            "repository_auditor_sha256": protected_input_hashes[repository_audit_path],
            "strict_judge_sha256": protected_input_hashes[judge_path],
            "finalizer_sha256": protected_input_hashes[finalizer_path],
            "integrity_tool_sha256": protected_input_hashes[integrity_tool_path],
            "runs": run_receipts,
            "batch_archive": {
                "path": str(final_batch_archive.relative_to(ROOT)),
                "bytes": final_batch_archive.stat().st_size,
                "sha256": sha256_file(final_batch_archive),
            },
        }
        receipt_stage = staging_root / "receipt.json"
        write_json_atomic(receipt_stage, receipt)
        transactional_replace(
            receipt_stage,
            receipt_output,
            backup_root,
            transaction_ledger,
            "finalization receipt",
        )
        if read_json(receipt_output) != receipt:
            raise ValueError("回执写入后重读不一致")
        assert_hashes_unchanged(protected_input_hashes, "回执写入后")

        # 用回执中对外声明的路径/大小/哈希反向重验，防止回执与实体分叉。
        for item in receipt["runs"]:
            archive = ensure_under_root(ROOT / item["archive"], "receipt run archive")
            if archive != final_run_archives[item["run_id"]]:
                raise ValueError(f"回执 Run 归档路径不符:{item['run_id']}")
            if archive.stat().st_size != item["archive_bytes"]:
                raise ValueError(f"回执 Run 归档大小不符:{item['run_id']}")
            if sha256_file(archive) != item["archive_sha256"]:
                raise ValueError(f"回执 Run 归档哈希不符:{item['run_id']}")
        declared_batch = receipt["batch_archive"]
        declared_batch_path = ensure_under_root(
            ROOT / declared_batch["path"], "receipt batch archive"
        )
        if declared_batch_path != final_batch_archive:
            raise ValueError("回执批次归档路径不符")
        if declared_batch_path.stat().st_size != declared_batch["bytes"]:
            raise ValueError("回执批次归档大小不符")
        if sha256_file(declared_batch_path) != declared_batch["sha256"]:
            raise ValueError("回执批次归档哈希不符")

        # 最后一道整批栅栏：不依赖各 Run 早先检查过的时点，而在成功
        # 返回前同时重算四个源 Run、四个前端镜像与回执声明。
        receipt_by_id = {item["run_id"]: item for item in receipt["runs"]}
        if list(receipt_by_id) != [path.name for path in run_dirs]:
            raise ValueError("回执 Run 集合/顺序不符")
        for run_dir in run_dirs:
            run_id = run_dir.name
            frontend_dir = frontend_root / run_id
            source_manifest_failures = validate_manifest(run_dir)
            frontend_manifest_failures = validate_manifest(frontend_dir)
            if source_manifest_failures:
                raise ValueError(
                    f"最终源 Run manifest 失败:{run_id}:{source_manifest_failures}"
                )
            if frontend_manifest_failures:
                raise ValueError(
                    f"最终前端 manifest 失败:{run_id}:{frontend_manifest_failures}"
                )
            expected_content = result_by_id[run_id]["content_tree_sha256"]
            if release_content_digest(run_dir) != expected_content:
                raise ValueError(f"最终源 Run 内容摘要漂移:{run_id}")
            if release_content_digest(frontend_dir) != expected_content:
                raise ValueError(f"最终前端内容摘要漂移:{run_id}")
            if read_json(
                run_dir / "production/FINAL_ACCEPTANCE.json"
            ) != result_by_id[run_id]:
                raise ValueError(f"最终单 Run 验收证明与批次摘要不一致:{run_id}")
            if read_json(
                frontend_dir / "production/FINAL_ACCEPTANCE.json"
            ) != result_by_id[run_id]:
                raise ValueError(f"最终前端验收证明与批次摘要不一致:{run_id}")
            source_tree = tree_digest(run_dir)
            frontend_tree = tree_digest(frontend_dir)
            item = receipt_by_id[run_id]
            if source_tree != frontend_tree:
                raise ValueError(f"最终源 Run/前端完整树不一致:{run_id}")
            if (
                item.get("accepted_content_tree_sha256") != expected_content
                or item.get("manifest_sha256") != sha256_file(run_dir / "manifest.json")
                or item.get("full_run_tree_sha256") != source_tree
                or item.get("frontend_full_tree_sha256") != frontend_tree
            ):
                raise ValueError(f"最终 Run/前端/回执三方不一致:{run_id}")
        assert_hashes_unchanged(protected_input_hashes, "最终整批复核后")
        assert_extra_inputs_unchanged(extra_input_digests, "最终整批复核后")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    except BaseException as exc:
        rollback_failures = rollback_transaction(transaction_ledger)
        for output_root in reversed(created_output_roots):
            try:
                output_root.rmdir()
            except OSError:
                # 只删除交易自己创建且回滚后为空的根；其他内容不动。
                pass
        if rollback_failures:
            preserve_staging_on_rollback_failure = True
            raise RuntimeError(
                "发布失败且部分目标未能自动回滚；"
                f"备份保留于 {staging_root}:{rollback_failures}"
            ) from exc
        raise
    finally:
        if not preserve_staging_on_rollback_failure:
            shutil.rmtree(staging_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
