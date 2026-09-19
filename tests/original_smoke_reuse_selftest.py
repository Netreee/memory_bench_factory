"""Offline identity checks for comparisons through the original factory stages."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.seed_pack import load_seed_pack, seed_contract, seed_digest, seed_input
from tools.run_original_bc_smoke import reuse_original_stages


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.target = Path(self.temp.name) / "target"
        self.source.mkdir()
        pack = load_seed_pack(ROOT / "seeds/insurance.json")
        description, examples = seed_input(pack)
        digest = seed_digest(pack)
        self.write("00_seed_pack.json", pack)
        self.write("00_input.json", {"description": description, "few_shot": examples,
            "seed": {"seed_id": pack["seed_id"], "family": pack["family"], "digest": digest,
                     "artifact": "00_seed_pack.json", "schema_version": pack["schema_version"]}})
        self.write("01_whitepaper.json", {"seed_contract": seed_contract(pack)})
        for name in ("00_about.json", "01_seed_audit.json", "02_seed_audit.json", "02_world.json"):
            self.write(name, {})
        self.manifest = {"scenario": "seed_" + pack["seed_id"], "status": "done",
            "config": {"seed_id": pack["seed_id"], "seed_pack_digest": digest,
                       "seed_pack_path": "missing mutable source.json", "augment": True},
            "algo": {"seed": {"digest": digest}, "entities": 8, "quality": "stale"},
            "stages": {name: {"done": True, "artifact": artifact} for name, artifact in
                       [("input", "00_input.json"), ("whitepaper", "01_whitepaper.json"), ("world", "02_world.json")]}}
        self.write("manifest.json", self.manifest)

    def write(self, name, value):
        (self.source / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_whitepaper_reuse_keeps_frozen_seed_and_excludes_old_world(self):
        result = reuse_original_stages(self.source, self.target, "whitepaper")
        for name in ("00_input.json", "00_seed_pack.json", "01_whitepaper.json", "01_seed_audit.json"):
            self.assertEqual((self.source / name).read_bytes(), (self.target / name).read_bytes())
        self.assertFalse((self.target / "02_world.json").exists())
        self.assertNotIn("entities", result["algo"])
        self.assertNotIn("quality", result["algo"])
        self.assertNotIn("augment", result["config"])
        self.assertEqual(set(result["stages"]), {"input", "whitepaper"})

    def test_world_reuse_keeps_seed_world_audit(self):
        result = reuse_original_stages(self.source, self.target, "world")
        self.assertTrue((self.target / "02_seed_audit.json").is_file())
        self.assertEqual(result["algo"]["entities"], 8)

    def test_changed_seed_identity_rejected_before_destination_created(self):
        pack = json.loads((self.source / "00_seed_pack.json").read_text(encoding="utf-8"))
        pack["title"] += " changed"
        self.write("00_seed_pack.json", pack)
        with self.assertRaises(ValueError):
            reuse_original_stages(self.source, self.target, "whitepaper")
        self.assertFalse(self.target.exists())

    def test_incomplete_or_active_source_rejected(self):
        self.manifest["status"] = "running"
        self.write("manifest.json", self.manifest)
        with self.assertRaises(ValueError):
            reuse_original_stages(self.source, self.target, "world")
        self.assertFalse(self.target.exists())

    def test_office_reuse_remains_unseeded(self):
        self.manifest.update(scenario="bc_small_office", config={})
        self.write("manifest.json", self.manifest)
        self.write("01_whitepaper.json", {})
        (self.source / "00_seed_pack.json").unlink()
        result = reuse_original_stages(self.source, self.target, "world")
        self.assertEqual(result["scenario"], "bc_small_office")
        self.assertFalse((self.target / "00_seed_pack.json").exists())

    def question_source(self):
        for name, artifact in [("orders", "03_orders.json"),
                               ("well_posed", "03_well_posed_report.json"),
                               ("questions", "04_questions.json")]:
            self.write(artifact, {"frozen": name})
            self.manifest["stages"][name] = {"done": True, "artifact": artifact}
        self.write("04_wording_report.json", {"status": "passed"})
        self.manifest["stages"]["corpus"] = {"done": False, "status": "failed"}
        self.manifest["status"] = "failed"
        self.manifest["algo"].update(questions=4, docs=3, render_strategy="stale")
        self.write("manifest.json", self.manifest)

    def test_question_resume_preserves_checkpoint_bytes_and_provenance(self):
        self.question_source()
        self.write("05_corpus.ckpt.json", {"identity": "factory-validates-this",
            "done_weeks": [2, 3], "corpus": {"sessions": []}})
        result = reuse_original_stages(self.source, self.target, "questions")
        for name in ("04_questions.json", "04_wording_report.json", "05_corpus.ckpt.json"):
            self.assertEqual((self.source / name).read_bytes(), (self.target / name).read_bytes())
            self.assertIn(name, result["derived_from"]["input_files"])
        self.assertNotIn("corpus", result["stages"])
        self.assertEqual(result["algo"]["questions"], 4)
        self.assertNotIn("docs", result["algo"])
        self.assertNotIn("render_strategy", result["algo"])

    def test_question_resume_also_supports_no_checkpoint(self):
        self.question_source()
        reuse_original_stages(self.source, self.target, "questions")
        self.assertFalse((self.target / "05_corpus.ckpt.json").exists())

    def test_incomplete_questions_cannot_be_reused(self):
        self.question_source()
        self.manifest["stages"]["questions"]["done"] = False
        self.write("manifest.json", self.manifest)
        with self.assertRaises(ValueError):
            reuse_original_stages(self.source, self.target, "questions")
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
