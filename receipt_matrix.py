#!/usr/bin/env python3
"""Import immutable experiment receipts and describe comparison limits.

Standalone public extraction of an HMIC receipt-first experiment matrix.
Stdlib only. It never calls a model, reads credentials, or submits data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "receipt-matrix.v1"
STATUSES = {"COMPLETED", "FAILED", "TRUNCATED", "NO_ANSWER", "MODEL_DOES_NOT_FIT", "UNAVAILABLE", "CANCELLED"}
FIELDS = (
    "schema_version", "record_id", "run_key", "imported_at", "receipt_path", "receipt_sha256",
    "source_sha256", "prompt_sha256", "output_sha256", "model_id", "model_revision", "quant",
    "runtime", "hardware", "stage", "condition", "status", "seed", "requested_settings",
    "effective_settings", "elapsed_seconds", "elapsed_time_basis", "input_tokens", "output_tokens",
    "quality_score", "quality_score_basis", "comparison_class",
)
HASH_FIELDS = {"record_id", "run_key", "receipt_sha256", "source_sha256", "prompt_sha256", "output_sha256"}
NUMBER_FIELDS = {"seed", "elapsed_seconds", "input_tokens", "output_tokens", "quality_score"}
CONTROL_FIELDS = (
    "source_sha256", "prompt_sha256", "model_revision", "runtime", "hardware", "quant", "seed",
    "stage", "condition", "requested_settings", "effective_settings", "elapsed_time_basis",
)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(data: bytes) -> dict:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"nonfinite JSON: {x}")))
    if not isinstance(value, dict):
        raise ValueError("receipt must be a JSON object")
    return value


def _hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def make_row(receipt_path: Path, source_path: Path, prompt_path: Path, store: Path) -> dict:
    """Bind a receipt to exact source and prompt bytes; preserve unknowns as null."""
    raw = receipt_path.read_bytes()
    receipt = _json_bytes(raw)
    if receipt.get("status") not in STATUSES:
        raise ValueError("status must be an explicit known result")
    if not isinstance(receipt.get("model_id"), str) or not receipt["model_id"]:
        raise ValueError("model_id is required")
    source_hash, prompt_hash = sha(source_path.read_bytes()), sha(prompt_path.read_bytes())
    for field, actual in (("source_sha256", source_hash), ("prompt_sha256", prompt_hash)):
        if receipt.get(field) is not None and receipt[field] != actual:
            raise ValueError(f"{field} disagrees with supplied file")
    row = dict.fromkeys(FIELDS)
    for field in ("output_sha256", "model_id", "model_revision", "quant", "runtime", "hardware",
                  "stage", "condition", "seed", "requested_settings", "effective_settings",
                  "elapsed_seconds", "elapsed_time_basis", "input_tokens", "output_tokens",
                  "quality_score", "quality_score_basis"):
        row[field] = receipt.get(field)
    row.update(schema_version=VERSION, imported_at=datetime.now(timezone.utc).isoformat(),
               receipt_path=os.path.relpath(receipt_path.resolve(), store.resolve()).replace("\\", "/"),
               receipt_sha256=sha(raw), source_sha256=source_hash, prompt_sha256=prompt_hash,
               status=receipt["status"], comparison_class="DESCRIPTIVE_ONLY")
    if row["status"] == "MODEL_DOES_NOT_FIT" and any(row[key] is not None for key in ("output_tokens", "quality_score", "output_sha256")):
        raise ValueError("fit failure cannot have a model output or semantic score")
    row["run_key"] = sha(canonical({key: row[key] for key in CONTROL_FIELDS + ("model_id",)}).encode("utf-8"))
    row["record_id"] = sha(canonical({"path": row["receipt_path"], "sha": row["receipt_sha256"],
                                      "run_key": row["run_key"]}).encode("utf-8"))
    validate_row(row)
    return row


def validate_row(row: dict) -> None:
    if set(row) != set(FIELDS) or row["schema_version"] != VERSION:
        raise ValueError("row schema mismatch")
    if row["status"] not in STATUSES or row["comparison_class"] != "DESCRIPTIVE_ONLY":
        raise ValueError("status or comparison class invalid")
    if not row["model_id"] or not isinstance(row["model_id"], str):
        raise ValueError("model_id invalid")
    for field in HASH_FIELDS:
        if row[field] is not None and not _hex64(row[field]):
            raise ValueError(f"invalid digest: {field}")
    for field in NUMBER_FIELDS:
        value = row[field]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError(f"invalid number: {field}")
    for field in ("requested_settings", "effective_settings"):
        if row[field] is not None and not isinstance(row[field], dict):
            raise ValueError(f"invalid object: {field}")
    if row["quality_score"] is not None and not row["quality_score_basis"]:
        raise ValueError("quality score requires a stated scoring basis")
    if row["status"] == "MODEL_DOES_NOT_FIT" and any(row[key] is not None for key in ("output_tokens", "quality_score", "output_sha256")):
        raise ValueError("fit failure cannot have a model output or semantic score")
    expected_run_key = sha(canonical({key: row[key] for key in CONTROL_FIELDS + ("model_id",)}).encode("utf-8"))
    if row["run_key"] != expected_run_key:
        raise ValueError("run key does not match controls")
    expected_record_id = sha(canonical({"path": row["receipt_path"], "sha": row["receipt_sha256"],
                                        "run_key": row["run_key"]}).encode("utf-8"))
    if row["record_id"] != expected_record_id:
        raise ValueError("record id does not match receipt binding")


def load_rows(store: Path, verify_receipts: bool = True) -> list[dict]:
    ledger = store / "runs.jsonl"
    if not ledger.exists():
        return []
    rows = []
    seen = set()
    for line_number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = _json_bytes(line.encode("utf-8"))
            validate_row(row)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"invalid ledger line {line_number}: {exc}") from exc
        if row["record_id"] in seen:
            raise ValueError(f"duplicate record at line {line_number}")
        seen.add(row["record_id"])
        if verify_receipts:
            path = (store / row["receipt_path"]).resolve()
            if not path.is_file() or sha(path.read_bytes()) != row["receipt_sha256"]:
                raise ValueError(f"original receipt missing or changed at line {line_number}")
        rows.append(row)
    return rows


def _write_csv_projection(store: Path, rows: list[dict]) -> None:
    csv_path = store / "runs.csv"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=store,
                                     prefix=".runs-", suffix=".csv", delete=False) as handle:
        temp_path = Path(handle.name)
        try:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: canonical(value) if isinstance(value, dict) else value for key, value in row.items()})
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    os.replace(temp_path, csv_path)


def ingest(store: Path, receipt: Path, source: Path, prompt: Path) -> int:
    store.mkdir(parents=True, exist_ok=True)
    row = make_row(receipt, source, prompt, store)
    lock = store / ".ingest.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.close(fd)
        rows = load_rows(store)
        new = row["record_id"] not in {old["record_id"] for old in rows}
        if new:
            with (store / "runs.jsonl").open("a", encoding="utf-8", newline="") as handle:
                handle.write(canonical(row) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            rows.append(row)
        _write_csv_projection(store, rows)
        return int(new)
    finally:
        lock.unlink()


def validate_store(store: Path) -> dict:
    rows = load_rows(store)
    csv_path = store / "runs.csv"
    if rows and not csv_path.is_file():
        raise ValueError("CSV projection missing")
    if csv_path.exists():
        with csv_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != FIELDS:
                raise ValueError("CSV columns changed")
            flat_rows = list(reader)
        if len(flat_rows) != len(rows):
            raise ValueError("CSV row count mismatch")
        for row, flat in zip(rows, flat_rows):
            expected = {key: canonical(value) if isinstance(value, dict) else "" if value is None else str(value)
                        for key, value in row.items()}
            if flat != expected:
                raise ValueError(f"CSV projection mismatch: {row['record_id']}")
    return {"status": "PASS", "records": len(rows), "completed": sum(r["status"] == "COMPLETED" for r in rows)}


def compare(left: dict, right: dict) -> dict:
    return {"left": left["record_id"], "right": right["record_id"],
            "classification": "DESCRIPTIVE_ONLY", "causal_claim_allowed": False,
            "unknown_controls": [key for key in CONTROL_FIELDS if left[key] is None or right[key] is None],
            "differing_controls": [key for key in CONTROL_FIELDS if left[key] is not None and right[key] is not None and left[key] != right[key]],
            "reason": "These saved receipts do not establish randomization, equivalent execution, or a causal model ranking."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("ingest")
    add.add_argument("--store", type=Path, required=True)
    add.add_argument("--receipt", type=Path, required=True)
    add.add_argument("--source", type=Path, required=True)
    add.add_argument("--prompt", type=Path, required=True)
    check = sub.add_parser("validate")
    check.add_argument("--store", type=Path, required=True)
    pairs = sub.add_parser("compare")
    pairs.add_argument("--store", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "ingest":
        print(canonical({"new_records": ingest(args.store, args.receipt, args.source, args.prompt)}))
    elif args.command == "validate":
        print(canonical(validate_store(args.store)))
    else:
        rows = load_rows(args.store)
        print(json.dumps([compare(a, b) for a, b in itertools.combinations(rows, 2) if a["stage"] == b["stage"]], indent=2))


if __name__ == "__main__":
    main()
