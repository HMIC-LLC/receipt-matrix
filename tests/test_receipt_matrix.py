import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import receipt_matrix as matrix


class MatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.examples = Path(__file__).resolve().parents[1] / "examples"
        for source in self.examples.iterdir():
            shutil.copy2(source, self.root / source.name)
        self.store = self.root / "store"

    def add(self, name="model-a.receipt.json"):
        return matrix.ingest(self.store, self.root / name, self.root / "source.txt", self.root / "prompt.txt")

    def test_reimport_is_idempotent_and_csv_matches(self):
        self.assertEqual(self.add(), 1)
        self.assertEqual(self.add(), 0)
        self.assertEqual(matrix.validate_store(self.store)["records"], 1)

    def test_comparison_exposes_unknown_and_different_controls(self):
        self.add()
        self.add("model-b.receipt.json")
        left, right = matrix.load_rows(self.store)
        result = matrix.compare(left, right)
        self.assertFalse(result["causal_claim_allowed"])
        self.assertIn("model_revision", result["unknown_controls"])
        self.assertIn("hardware", result["differing_controls"])

    def test_changed_receipt_requires_preserving_old_bytes(self):
        self.add()
        receipt = self.root / "model-a.receipt.json"
        data = json.loads(receipt.read_text(encoding="utf-8"))
        data["output_tokens"] = 2
        receipt.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            matrix.validate_store(self.store)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.add()

    def test_fit_failure_cannot_claim_output(self):
        receipt = self.root / "model-a.receipt.json"
        data = json.loads(receipt.read_text(encoding="utf-8"))
        data["status"] = "MODEL_DOES_NOT_FIT"
        receipt.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fit failure"):
            self.add()

    def test_hash_binding_and_duplicate_keys(self):
        receipt = self.root / "model-a.receipt.json"
        data = json.loads(receipt.read_text(encoding="utf-8"))
        data["source_sha256"] = "0" * 64
        receipt.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            self.add()
        receipt.write_text('{"status":"COMPLETED","status":"FAILED","model_id":"x"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            self.add()

    def test_csv_projection_recovers_after_interrupted_write(self):
        self.add()
        (self.store / "runs.csv").write_text("partial header\n", encoding="utf-8")
        self.assertEqual(self.add(), 0)
        self.assertEqual(matrix.validate_store(self.store)["records"], 1)

    def test_ledger_control_tamper_breaks_run_key(self):
        self.add()
        ledger = self.store / "runs.jsonl"
        row = json.loads(ledger.read_text(encoding="utf-8"))
        row["quant"] = "different"
        ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "run key"):
            matrix.validate_store(self.store)


if __name__ == "__main__":
    unittest.main()
