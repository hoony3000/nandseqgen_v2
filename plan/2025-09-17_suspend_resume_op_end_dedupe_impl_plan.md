# Problem 1-Pager — Suspend-resume OP_END dedupe (Approach 1)

## Background
- `Scheduler._emit_op_events` currently queues OP_END when an ERASE/PROGRAM op is first committed and again when a resume-chain stub is committed, so suspend→resume creates duplicate completions (`research/2025-09-17_15-57-35_suspend_resume_op_end_dedupe.md`).
- `ResourceManager.move_to_suspended_axis` keeps `remaining_us` but does not retarget or retire the original OP_END, leaving the stale event in place while resume emits a fresh one.
- `EventQueue` maintains a sorted list without dedupe, so downstream handlers like `AddressManager.apply_pgm` execute once per queued OP_END.

## Problem
- Duplicate OP_END events survive suspend→resume cycles, causing repeated completion handling, inflated page state transitions, and noisy validation logs.

## Goal
- Ensure each logical ERASE/PROGRAM op emits exactly one OP_END that reflects the final completion timestamp, even across suspend/resume chains, while keeping CORE_BUSY stub semantics intact.

## Non-Goals
- Changing suspend/resume eligibility, CORE_BUSY reservation policy, or removing resume stubs.
- Altering AddressManager logic beyond observing a single OP_END.
- Replacing the scheduler’s event sequencing or priority rules.

## Constraints
- Keep new helpers ≤50 LOC and cyclomatic complexity ≤10; stay within 300 LOC per file.
- Preserve EventQueue determinism `(time, priority, seq)` and avoid breaking existing payload consumers.
- Remain compatible with Python 3.11 runtime and current validation hooks.
- No secrets or sensitive payload data in logs or metrics.

## Approach Options Considered
- **Approach 1 — Stable `op_uid` + queue replacement**: + Single canonical OP_END enforced at scheduling boundary; scoped to Scheduler/EventQueue. − Requires queue mutation helper and reliable `op_uid` propagation. Risk: missing `op_uid` leaves stale OP_END.
- **Approach 2 — Skip OP_END at handler when still suspended**: + Minimal queue changes. − Leaves duplicates queued and relies on runtime checks; Risk: incorrect suspend state could drop real completions.
- **Decision**: Adopt Approach 1 so dedupe happens before dispatch, satisfying spec and research guidance while keeping downstream handlers unchanged.

## Implementation Plan
1. **Guarantee stable `op_uid` assignment**
   - Add monotonic `op_uid` generation on Scheduler and stamp every ERASE/PROGRAM record (initial commits and resume stubs) regardless of validation mode.
   - Persist `op_uid` through ResourceManager suspend metadata and chain job records so resume path can reference the same identity.
   - Surface `op_uid` on both OP_START and OP_END payloads to keep trace parity and satisfy validation tooling expectations.
2. **Extend EventQueue with targeted removal/reschedule**
   - Implement a helper (e.g., `remove_where` or `reschedule_first`) to filter `_q` for entries matching `op_uid` and kind `OP_END` while keeping ordering guarantees.
   - Cover helper with focused unit tests to ensure ordering and tuple structure stay intact.
3. **Update `_emit_op_events` to reuse OP_END**
   - Before enqueuing OP_END, attempt to remove an existing event for the same `op_uid`; if found, push a single replacement with the new `end_us` and payload.
   - Maintain existing OP_START/PHASE events and ensure fallback logs/metrics fire when no prior OP_END is seen (unexpected path).
4. **Clean up bookkeeping after completion**
   - When OP_END drains, drop any scheduler-side handle/state for that `op_uid` to avoid leaks; ensure suspend retry paths refresh metadata correctly.
5. **Instrument reschedule latency behind a flag**
   - Add a lightweight metric or tracer for queue reschedule cost that is gated by a config/feature flag so the default path has no additional overhead.
   - Document the flag and default-off behavior so operators can enable it when measuring queue churn.
6. **Documentation and observability**
   - Update inline comments/metrics to describe the new dedupe behavior and the optional reschedule metrics flag.

## Verification Plan
- **Unit — EventQueue helper**: New tests covering removal/reschedule preserves ordering and does not affect unrelated events.
- **Unit — Scheduler suspend metadata**: Test that suspend→resume keeps `op_uid` stable and `_emit_op_events` results in exactly one OP_END in the queue, with OP_START/OP_END payloads both carrying the id.
- **Integration — Suspend/resume chain**: Scenario test exercising PROGRAM suspend/resume to assert only one OP_END fires and AddressManager hooks execute once.
- **Regression — Existing scheduler drains**: Re-run drain/exit and resume window tests to ensure queue mutation doesn’t break shutdown or metrics.
- **Manual — Validation replay**: Replay prior reproduction logs to confirm OP_END dedupe removes duplicate `apply_pgm` calls without altering CORE_BUSY stubs.
- **Instrumentation — Metrics guard**: Verify that the reschedule latency metric is emitted only when the flag is enabled and remains silent otherwise.

## Assumptions / Notes
- `op_uid` can be safely added to event payloads without breaking consumers because existing parsers ignore extra keys.
- Resume stubs continue to emit CORE_BUSY segments; dedupe only touches OP_END scheduling.
- Reschedule latency instrumentation defaults to disabled so baseline runs avoid extra logging overhead.

## Decisions Incorporated
- OP_START will include `op_uid` for trace parity alongside OP_END.
- Queue reschedule latency metrics will be controlled by an opt-in flag to limit overhead.

