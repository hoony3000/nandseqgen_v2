# Operation Timeline Aggregation Plan

## Goal
Generate aggregated frequency reports for `op_name` and `op_base` values by scanning `operation_timeline*.csv` files within user-specified directories, producing two CSV outputs using only Python standard library modules.

## Scope
- Read every matching CSV file at the top level of each provided directory (no recursive traversal unless explicitly requested later).
- Require the presence of `op_name` and `op_base` columns; fail fast with a clear error if either is missing.
- Emit deterministic CSVs named `op_name_counts.csv` and `op_base_counts.csv` in the working directory unless overridden via CLI flags.

## Out of Scope
- Pandas or third-party dependencies.
- Modifying existing data files.
- Visualization or summary statistics beyond simple counts.

## Implementation Outline
1. **Argument Parsing**: build a CLI with positional directory arguments plus optional `--output-dir`, `--op-name-output`, and `--op-base-output` flags.
2. **File Discovery**: collect `operation_timeline*.csv` files from each directory, ensuring duplicates are de-duplicated and missing directories raise helpful errors.
3. **Aggregation**: iterate rows with `csv.DictReader`, update `Counter` instances for `op_name` and `op_base`, and guard against absent columns.
4. **Output Writing**: write sorted results (descending count, then name) to CSV via `csv.writer` with headers.
5. **Testing**: add pytest coverage for happy path, multiple directories, and missing column failure.

## Risks & Mitigations
- **Inconsistent headers**: enforce strict column checks before aggregation.
- **Empty inputs**: gracefully produce empty output files with only headers.
- **Large datasets**: counters operate in-memory; expected scale manageable as counts only.

## Verification
- Unit tests covering aggregation and error handling.
- Manual spot-check using sample file in `out/` after implementation.
