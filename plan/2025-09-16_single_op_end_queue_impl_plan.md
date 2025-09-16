# Problem 1-Pager — Single OP_END queue for suspend/resume ERASE/PROGRAM

## Background
- `Scheduler._emit_op_events` emits OP_START/OP_END for every committed operation, including resume-chain stubs created after suspend (`scheduler.py`).
- `ResourceManager.move_to_suspended_axis` preserves `remaining_us` for suspended ERASE/PROGRAM ops and exposes them through `suspended_ops_*` (`resourcemgr.py`).
- With `suspend_resume_chain_enabled`, `_propose_and_schedule` commits a resume stub and calls `_emit_op_events` again, so the same logical op ends up with multiple OP_END events (`scheduler.py`).
- `EventQueue` keeps duplicates because it only sorts tuples and has no dedupe logic, so `_handle_op_end` and AddressManager hooks run repeatedly for one op (`event_queue.py`).

## Problem
- Suspend→resume cycles enqueue a fresh OP_END each time the stub is committed, leaving several OP_END entries referencing the same targets/op in the queue and triggering duplicate state transitions when drained.

## Goal
- Guarantee that each logical ERASE/PROGRAM op, even across suspend/resume chains, has exactly one OP_END event scheduled at the final completion timestamp, while keeping existing OP_START/PHASE_HOOK semantics intact.

## Non-Goals
- Changing suspend/resume eligibility rules, ResourceManager timing math, or EventQueue ordering semantics beyond what is needed to keep a single OP_END.
- Refactoring proposer or wider scheduler admission logic unrelated to OP_END deduplication.
- Introducing global persistence for operations beyond existing scheduler/ResourceManager state.

## Constraints
- Preserve deterministic ordering in `EventQueue` (time, priority, seq) after any updates.
- Keep new helpers within project limits (≤50 LOC per function, cyclomatic complexity ≤10).
- Avoid leaking sensitive target/address data in new logs; rely only on existing payloads.
- Maintain compatibility with existing metrics and tests (e.g., drain-on-exit, resume timeline validation).
- Ensure solution works for both ERASE and PROGRAM families and across multi-plane targets.

## Approach Options Considered
- **Option A — Track `op_uid` and reschedule existing OP_END**: + single OP_END that reflects actual completion, aligns with spec; − requires EventQueue mutation helpers and op identity plumbing; Risk: incorrect bookkeeping could orphan events and stall release hooks.
- **Option B — Ignore duplicate OP_END in handler when op still suspended**: + minimal structural changes; − duplicates stay queued so spec unmet and queue churn persists; Risk: bad suspend-state check could skip the real final OP_END.
- **Option C — Periodically dedupe queue by scanning for matching payloads**: + avoids new identity field; − O(n²) scans risk regressions and still needs payload heuristics; Risk: heuristic mismatch could delete legitimate concurrent ops.
- **Chosen**: Option A because it directly enforces “only one OP_END queued” with explicit identity, avoiding heuristics and matching the research recommendation.

## Implementation Plan
1. **Introduce stable operation identity in Scheduler/ResourceManager**
   - Add a monotonic `_next_op_uid` on `Scheduler` and stamp each committed record with `op_uid`; pass it through `_emit_op_events` payloads.
   - Extend `rec` dictionaries and resume-chain `rec2` to carry `op_uid`; store it in `chain_jobs` and resume meta.
   - Update `ResourceManager.register_ongoing` / `_OpMeta` usage so `op_id` persists this `op_uid`, letting `move_to_suspended_axis` and chain jobs expose it.
2. **Enhance EventQueue for targeted updates**
   - Provide helper(s) to locate and update/remove a queued event by predicate while maintaining sorted order (e.g., `update_first(kind, predicate, new_when, new_payload=None)`).
   - Ensure helpers remain O(n) with stable tuple structure and keep seq monotonic (either reuse existing seq or bump when reinserted).
3. **Record OP_END handles for later reschedule**
   - Keep `Scheduler` map `_op_end_handles` keyed by `op_uid` that stores enough info (e.g., index or predicate payload) to update queued OP_END events.
   - When emitting the initial OP_END for a fresh op, insert via new helper and record the handle along with payload copy for validation.
4. **Reschedule instead of re-enqueue on resume stubs**
   - In `_emit_op_events`, detect `rec.get("_chain_stub")` and reuse the existing `op_uid` to reschedule the stored OP_END to the new `end_us` and refresh payload targets/base as needed.
   - Skip pushing an additional OP_END when rescheduling succeeds; still push OP_START/PHASE_HOOKS so downstream timing remains intact.
   - If no existing OP_END is found (should not happen), log via metrics (`metrics['op_end_reschedule_miss']`) and fall back to inserting one to avoid missing completion.
5. **Keep bookkeeping consistent through suspend lifecycle**
   - When suspend moves an op to `_suspended_ops_*`, ensure the meta retains `op_uid`; when resume completes, clean `_op_end_handles` so long-lived maps do not leak.
   - Update any cleanup paths (e.g., `_drain_pending_op_end_events`) to respect the handle map, removing entries once OP_END fires.
6. **Docs/Observability**
   - Document new metric or debugging counters if added (e.g., reschedule miss) either inline comments or metrics dictionary for traceability.

## Verification Plan
- **Unit — EventQueue update helper**: add tests to cover updating/removing events preserves ordering and only touches matching event (e.g., create queue with mixed kinds, reschedule OP_END, assert new ordering and contents).
- **Unit — ResourceManager suspend metadata**: extend `test_resourcemgr_resume_timeline` or add new test to confirm `move_to_suspended_axis` stores `op_id`/`remaining_us` and resumes hand back same id.
- **Integration — Scheduler suspend/resume chain**: craft a patched proposer scenario that commits ERASE (or PROGRAM), then SUSPEND/RESUME with `suspend_resume_chain_enabled=True`; assert queue never has more than one OP_END for the tracked `op_uid` and `_handle_op_end` runs exactly once per logical op (spy on handler).
- **Regression — drain_on_exit**: rerun `test_scheduler_drain_op_end` (and any new suspend/resume test) to ensure OP_END draining still succeeds when queue mutation occurs late.
- **Manual/Exploratory**: replay existing reproduction (`out/operation_timeline_250916_0000001.csv`) after implementation to verify exported timelines show a single OP_END per op and AddressManager hooks execute once.

## Impact / Dependencies
- Touches: `scheduler.py`, `event_queue.py`, `resourcemgr.py`, possibly scheduler tests and new fixtures in `tests/`.
- Downstream consumers rely on OP event payload structure; adding `op_uid` must remain backward-compatible (pure addition) so tests/exporters parsing payload dictionaries keep working.

## Open Questions
- Should OP_START also embed `op_uid` for symmetry and future debugging? (Default: yes, include to trace timeline — confirm no consumer rejects extra field.)
- Does rescheduling require updating `metrics['last_reserved_records']` or exporters? (Assume no, but validate once implementation begins.)
