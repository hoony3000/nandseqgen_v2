---
date: 2025-09-17T02:45:00+00:00
prepare_for: suspend-resume-op-end-verification
status: active
owner: Codex
---

# Baseline Inputs — Suspend→Resume OP_END Requeue Reproduction

## Scenario Summary
- **Purpose**: Reproduce the PROGRAM suspend→resume flow that generates duplicate `OP_END` events and `source=RESUME_CHAIN` segments used throughout the OP_END requeue investigation.
- **Workload Config**: Use the repository default `config.yaml`, which keeps suspend/resume chaining enabled and the effective timeline export active (`config.yaml:30`).
- **Phase Distribution**: Load `op_state_probs.yaml` (autofilled per `main.py`) so the DEFAULT state prefers PROGRAM, with ERASE and `Program_Resume` as secondary candidates (`op_state_probs.yaml:1`).

## Command Line
```bash
python main.py \
  --config config.yaml \
  --run-until 100000 \
  --num-runs 2 \
  --seed 42 \
  --out-dir out
```
- `--run-until` keeps the default 100 ms simulation window that surfaces multiple suspend→resume cycles.
- `--num-runs 2` matches the captured artefacts under `out/` for cross-run comparisons; a single run (`--num-runs 1`) still exhibits the duplicated OP_END behaviour.

## Expected Outputs
- `out/operation_timeline_250917_0000001.csv` – contains paired segments for a single logical PROGRAM (e.g., `op_uid=3` and `op_uid=6` with `source=RESUME_CHAIN`).
- `out/operation_timeline_250917_0000002.csv` – second-run confirmation with the same pattern.
- `out/proposer_debug_250917_0000001.log` – shows proposer repeatedly selecting `Program_Resume` from the DEFAULT distribution.
- `out/snapshots/state_snapshot_20250917_014241_0000001.json` – captured RM state at the end of the first run for regression diffing.

## Validation Instrumentation Toggle
- The scheduler now reads validation switches from `config.yaml:49`. To enable logging for Strategy 1 and Strategy 2:
  ```yaml
  validation:
    suspend_resume_op_end:
      enabled: true
      log_dir: out/validation
      strategy1:
        enabled: true
      strategy2:
        enabled: true
  ```
- When enabled, JSONL artefacts are written to `out/validation/` (the directory is created automatically).
- Strategy 1 records OP_START/OP_END scheduling metadata in `strategy1_events.jsonl`; Strategy 2 captures `apply_pgm` deltas per (die, block) in `strategy2_apply_pgm.jsonl`.

## Notes
- The baseline run assumes `suspend_resume_chain_enabled: true` and `operation_timeline_effective: true` (see `config.yaml:30-41`), which ensures the RESUME stubs are materialised and visible.
- Keep `op_state_probs.yaml` in sync with the config before running; regenerate via `python main.py --config config.yaml --refresh-op-state-probs` if edits change the workload mix.
- All outputs listed above are ignored by git (`.gitignore`) and should be regenerated locally when needed.

## Validation Evidence (2025-09-17)
- `config.yaml` updated with `validation.suspend_resume_op_end.enabled=true` and both strategies enabled.
- Replayed the baseline command (seed `42`, two runs) producing `out/validation/strategy1_events.jsonl` and `out/validation/strategy2_apply_pgm.jsonl`.
- Strategy 1 summary: 176 `OP_END` records captured, with 73 distinct `op_uid` values receiving more than one `OP_END` (some up to 3). Example: `op_uid=3` logged 3 `OP_END` events (`strategy1_events.jsonl`).
- Strategy 2 summary: 88 patched `apply_pgm` calls recorded, 30 `op_uid` values observed twice (initial and resume segments). All entries reported matching `expected_delta` vs `actual_delta` for (die, block) increments (`strategy2_apply_pgm.jsonl`).
- Logged files reside under `out/validation/` and are ready for downstream analysis (e.g., metric aggregation scripts or notebook exploration).
- Aggregated metrics are exported to `out/validation/analysis_summary.json`; after two consecutive runs the file reports 352 `OP_END` entries (all 76 `op_uid`s exhibiting duplicates) and 176 patched `apply_pgm` calls with matching deltas for each block.
- Strategy 3 snapshots recorded in `out/validation/strategy3_queue_snapshot.jsonl` show both `PROGRAM_RESUME` commits and subsequent `RESUME_CHAIN` stubs, each snapshot including the queued OP_END entries (with `op_uid` and targets) at the capture point.
- Snapshot analytics:
  - 86 snapshots captured (57 `resume_op`, 29 `chain_stub`).
  - `resume_op` snapshots always contain a single OP_END entry for the freshly queued resume, while `chain_stub` snapshots average 2 OP_END entries—the resume plus the underlying PROGRAM stub (e.g., first chain snapshot lists `Program_Resume` `op_uid=5` alongside `Page_Program_SLC` `op_uid=3`).
  - No snapshot shows duplicate OP_END entries for the same `op_uid`; duplicates observed in Strategy 1 logs therefore arise from successive queue insertions over time rather than simultaneous duplicates.
