"""Validate curated seed contracts offline; optionally verify local source bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.seed_pack import SeedPackError, load_seed_pack, seed_digest


def inspect_pack(path: Path, *, verify_sources: bool = False) -> dict:
    pack = load_seed_pack(path)
    errors = []
    if verify_sources:
        for source in pack["sources"]:
            source_path = (ROOT / source["path"]).resolve()
            try:
                source_path.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(f"{source['id']}: source path must be within the repository")
                continue
            try:
                digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
            except OSError:
                errors.append(f"{source['id']}: source file unavailable")
                continue
            if digest != source["sha256"].lower():
                errors.append(f"{source['id']}: source SHA256 mismatch")
    return {"path": str(path), "seed_id": pack["seed_id"], "digest": seed_digest(pack),
            "mechanisms": len(pack["mechanisms"]), "sources": len(pack["sources"]),
            "source_bytes_verified": verify_sources and not errors,
            "passed": not errors, "issues": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Default: seeds/*.json")
    parser.add_argument("--verify-sources", action="store_true",
                        help="Also check provenance hashes against local original files")
    args = parser.parse_args()
    paths = args.paths or sorted((ROOT / "seeds").glob("*.json"))
    if not paths:
        parser.error("No seed packs found")
    reports = []
    for path in paths:
        try:
            reports.append(inspect_pack(path, verify_sources=args.verify_sources))
        except SeedPackError as error:
            reports.append({"path": str(path), "passed": False, "issues": [str(error)]})
    print(json.dumps({"passed": all(report["passed"] for report in reports),
                      "packs": reports}, ensure_ascii=False, indent=2))
    if not all(report["passed"] for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
