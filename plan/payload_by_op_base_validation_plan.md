Problem 1-Pager — payload_by_op_base Validation

Background
- export_operation_sequence writes operation_sequence_YYMMDD_XXXXXXX.csv with a JSON payload per row.
- The JSON schema per operation is defined by config.yaml[payload_by_op_base] keyed by op_base.
- We must verify each operation’s payload matches the configured schema.

Problem
- Ensure that for every operation in the generated CSV, its payload JSON objects contain exactly the fields defined for its op_base in config.yaml[payload_by_op_base].

Goal
- E2E validate a fresh CSV against config:
  - Parse config: payload_by_op_base and op_names[*].base.
  - For each CSV row: map op_name → base, load expected fields, and compare with payload item keys.
  - Report per-base pass/fail and detailed mismatches (missing/extra keys).

Non‑Goals
- Do not change simulator behavior or fix discovered issues.
- Do not exhaustively generate every op_base; only validate those present in the run.

Constraints
- Keep changes minimal; no external network/deps.
- Script ≤ 300 LOC; functions ≤ 50 LOC; explicit and simple.

Approach
1) Generate a CSV with main.py (seeded, short run) or accept an existing CSV path.
2) Load config.yaml and build:
   - name_to_base: op_names[*].base map
   - expected_fields_by_base: payload_by_op_base
3) For each CSV row:
   - Load payload JSON (list of dicts)
   - Resolve base from op_name
   - For each dict: compare set(keys) with expected_fields_by_base[base]
4) Summarize results and save report JSON + short markdown.

Alternatives Considered
- Unit-level validation by calling export_operation_sequence with synthetic rows
  - Pros: deterministic, no runtime variability
  - Cons: higher coupling, mocks needed for inheritance, lower E2E confidence
- Grep-based static check of code paths
  - Pros: zero runtime
  - Cons: cannot assert actual output; misses runtime conditions
→ Choose E2E CSV validation for realism and simplicity.

Test Cases (representative)
- Positive: ERASE, READ/READ4K, PROGRAM_* rows include configured fields exactly.
- Missing field detection: if config lists a field not present in payload, flag as missing.
- Extra field detection: if payload includes unexpected keys, flag as extra.
- JSON validity: payload parses as list[dict] for all rows.

Execution Plan
- Script: scripts/validate_payload_by_op_base.py
- Default run: generate CSV with `python main.py --num-runs 1 --run-until 2000 --seed 42 --out-dir out`
- Then validate latest out/operation_sequence_*.csv and write:
  - plan/payload_by_op_base_validation_results_<timestamp>.json
  - plan/payload_by_op_base_validation_results_<timestamp>.md (summary)

