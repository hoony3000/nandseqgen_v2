# Problem 1-Pager — OP hooks beyond run_until

## Background
- `main.py` runs multiple scheduler iterations when `--num_run > 1`, reusing the same `ResourceManager` but dropping the scheduler instance between runs.
- Each committed operation enqueues OP_START and OP_END events; `_handle_op_end` is responsible for releasing slots and synchronizing the `AddressManager`.
- `Scheduler.run(run_until_us)` stops once `now_us > run_until_us`, leaving later events such as OP_END in the queue if the operation starts after the boundary.

## Problem
- When an operation’s `start_us` lies beyond `run_until_us`, the run loop still emits OP_START but exits before dequeuing the matching OP_END, so resource release and address synchronization never occur.
- Subsequent runs inherit stale scheduler state (e.g., suspended ops, address mappings) because the hooks were skipped, leading to inconsistent metrics and potential deadlocks when capacity appears exhausted.

## Goal
- Guarantee that any OP_START dispatched before exiting `run()` has its paired OP_END processed (including hooks and `_handle_op_end` side effects) before the scheduler instance is discarded.

## Non-Goals
- Changing the global stopping semantics of `Scheduler.run` or `EventQueue` ordering.
- Reworking bootstrap/queue refill proposal logic beyond what is required to drain OP_END events safely.
- Introducing new persistence for scheduler state across runs.

## Constraints
- Respect existing feature flags (e.g., bootstrap skip) so draining can be gated or bypassed when explicitly disabled.
- Keep new public APIs backward compatible for callers that may instantiate `Scheduler` directly in tests or scripts.
- Ensure draining is idempotent and cheap since it executes at every run boundary.
- All new functions must remain under the project’s style limits (≤50 LOC per function, cyclomatic complexity ≤10).

## Approach Options Considered
- **Option A — Scheduler-integrated drain**: + preserves encapsulation and protects all callers; − implicit behavior change requires careful gating.
- **Option B — Orchestration-level drain (main.py)**: + offers flexible feature flag wiring; − leaves other Scheduler consumers vulnerable and mandates extra API plumbing.
- **Chosen**: Option A, because the risk of missed drains in other entry points outweighs the cost of adding an opt-in flag on the scheduler.

## Implementation Plan
1. **Expose drain capability on Scheduler**
   - Add a `drain_pending_op_end_events()` method that inspects the event queue for OP_END timestamps where the corresponding OP_START has fired but OP_END has not.
   - Reuse existing `_handle_op_end` logic to process each queued OP_END in chronological order without generating duplicate timeline entries.
   - Gate invocation behind a new scheduler option/flag (e.g., `drain_on_exit`) defaulting to false to avoid surprising existing tests; the main path will opt in.
2. **Invoke drain after run completion**
   - Update `Scheduler.run` (or `close`) to call the drain helper when `drain_on_exit` is enabled and the loop exits because `now_us >= run_until_us`.
   - Ensure the drain does not advance `now_us` beyond the last processed OP_END so metrics stay consistent.
3. **Wire flag in orchestration**
   - Extend `InstrumentedScheduler` construction in `main.py` to enable `drain_on_exit` via CLI/config toggle with sensible default (likely on for multi-run scenarios).
   - Document the new option in config parsing and ensure single-run behavior remains unchanged when the flag is off.
4. **Audit bootstrap interactions**
   - Ensure draining respects bootstrap skip heuristics by checking scheduler state (`bootstrap_active`, pending resume chains) before draining; add guard rails if certain phases should be excluded.
5. **Testing**
   - Unit test the new drain helper using a synthetic scheduler/EventQueue with operations beyond run_until to verify OP_END hooks run and resources release.
   - Integration test via CLI/multi-run harness to confirm resource manager state resets between runs and instrumentation logs contain OP_END entries post-drain.
   - Regression test to ensure draining when no pending OP_END events exists is a no-op (idempotency).

## Open Questions / Follow-ups
- Should drain automatically run when the scheduler is explicitly `close()`d even if `run` wasn’t called? (default assumption: no, but document behavior.) -> (reviewed) default no.
- Determine whether bootstrap skip conditions need dedicated metrics to confirm we are draining only when safe; plan follow-up instrumentation if necessary. -> (reviewed) Not yet.
