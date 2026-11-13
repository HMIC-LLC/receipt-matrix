# Receipt Matrix

A small, offline harness for importing experiment receipts without turning missing settings into measured facts. It records the exact receipt, source, and prompt hashes; writes an append-only JSONL ledger with a chartable CSV projection; and explains why a pair of saved runs is only a descriptive comparison.

This is a standalone public extraction of an HMIC experiment intake matrix. The bundled garden-pump example is fictional. **It does not run a model or certify an experiment as controlled.**

## Try it

Python 3.10+; standard library only. These PowerShell commands work from this folder:

```powershell
$py = 'python'
& $py .\receipt_matrix.py ingest --store .\demo-store --receipt .\examples\model-a.receipt.json --source .\examples\source.txt --prompt .\examples\prompt.txt
& $py .\receipt_matrix.py ingest --store .\demo-store --receipt .\examples\model-b.receipt.json --source .\examples\source.txt --prompt .\examples\prompt.txt
& $py .\receipt_matrix.py validate --store .\demo-store
& $py .\receipt_matrix.py compare --store .\demo-store
& $py -m unittest discover -s .\tests -v
```

`ingest` is idempotent for the same receipt bytes and bindings. An amended receipt must be saved under a new filename while preserving the old file; validation refuses missing or changed originals. The tool uses an exclusive `.ingest.lock` and never silently removes a pre-existing lock. Investigate an abandoned lock before removing it.

## Receipt fields

Required: `status` and `model_id`. Optional fields include `model_revision`, `quant`, `runtime`, `hardware`, `stage`, `condition`, `seed`, `requested_settings`, `effective_settings`, `elapsed_seconds`, `elapsed_time_basis`, `input_tokens`, `output_tokens`, `quality_score`, and `quality_score_basis`. Set unknowns to `null` or omit them. If you provide `source_sha256` or `prompt_sha256`, it must match the bytes supplied at import. The harness never infers a quality score from completion, latency, or token count.

`MODEL_DOES_NOT_FIT` is a first-class result and cannot carry output tokens, output hash, or a quality score. A model weight ID is not a revision hash; record the revision separately when known.

`compare` reports missing and differing controls for each same-stage pair. Every pair remains `DESCRIPTIVE_ONLY`, even if these lists are empty: a causal claim needs a separately designed experiment. In particular, equal seeds across runtimes do not guarantee equivalent sampling, and elapsed wall time cannot be treated as a throughput measurement without a shared timing basis.

## Limits

- The receipt format is deliberately small; it does not contact a model endpoint, validate an answer, prove physical GPU placement, or detect a dishonest receipt.
- JSONL is the authority; CSV is a recoverable projection. If an import is interrupted after the JSONL append, reimport the same receipt to fill the missing CSV row.
- Source and prompt bytes are hashed at import, but the ledger stores their hashes, not the files. Keep your inputs and fixture versions separately.
- The public example has no private research data and demonstrates method only. It is not a benchmark result.

MIT license. Copyright (c) 2026 HMIC LLC.
