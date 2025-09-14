---
title: RM pending windows — logging repro and verification plan
date: 2025-09-14
author: codex
status: draft
source: research/2025-09-14_13-48-44_rm_validity_pending_bus_plane_windows.md
---

# Goal

- Produce concrete logs showing whether ResourceManager includes transaction‑pending windows in bus/plane overlap checks, and verify that same‑time reservations do not occur within a single transaction.

# What to verify

- `_bus_ok(op, start, pending=txn.bus_resv)` is called with non‑empty `pending` after an instant op in the same txn, and blocks overlap if present.
- `_planescope_ok(..., pending=txn.plane_resv)` is called and blocks plane overlap when two normal ops are attempted at the same time in the same txn.
- Instant path start time is serialized within txn by `start = max(txn.now_us, last_end(txn.bus_resv))`.

# Approach

- Unit‑level, deterministic tests that wrap RM methods to print debug lines without changing production code.
- Scenarios:
  1) ERASE (normal) → SR (instant): verify SR start ≥ ERASE end and bus check sees pending.
  2) SR (instant) → ERASE (normal): verify ERASE start ≥ SR end and bus check sees pending.
  3) READ → READ on same (die,plane) with txn.now_us not advanced: ensure `_planescope_ok` sees pending and rejects.

# Execution steps

1. Add tests/test_rm_pending_logs.py with monkey‑patched wrappers that print:
   - BUS_OK: start, #segments, committed_count, pending_count, result
   - PLANESCOPE_OK: die, plane_set, start, end, committed_count, pending_count, result
2. Run `./.venv/bin/python -m pytest -q -s tests/test_rm_pending_logs.py` to emit logs.
3. Inspect the printed lines to confirm pending>0 in the second op of each scenario and that overlap is rejected/serialized.

# Pass/Fail Criteria

- PASS if logs show pending>0 in the second op and assertions hold (no intra‑txn same‑time starts; explicit plane overlap is rejected).
- FAIL if pending=0 for second ops or same‑time reservations still occur within a single transaction.

# Notes

- CSV may still show same timestamps for different transactions or different scopes (allowed). This plan targets the intra‑transaction overlap bug specifically.

