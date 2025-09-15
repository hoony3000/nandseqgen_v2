---
date: 2025-09-15
author: Codex
source: research/2025-09-15_22-13-14_skip_delay_in_proposal.md
status: draft
topic: "Skip Delay in proposal: no reservation, no CSV, advance to next event_hook"
---

# Problem 1-Pager

- Background: In the current pipeline, when `Delay` is proposed (defined as `base: NOP`), it still flows through reservation/commit and InstrumentedScheduler records rows that propagate into CSVs. Requirement is to not record or schedule Delay and to advance to the next `event_hook` immediately.
- Problem: Proposer may select `Delay`. Downstream, Scheduler reserves/commits and emits OP events which later become CSV rows. This contradicts the intention of skipping Delay entirely.
- Goal: When the first op in a proposed batch is `Delay`, skip reservation/commit and event emission, produce no CSV rows, and proceed to the next hook in the same scheduling cycle. Guard with a feature flag.
- Non‑Goals: Rebalancing proposal distributions; removing `Delay` from probability tables; refactoring exporter formatting; changing AddressManager semantics.
- Constraints: Keep deterministic behavior, minimal invasive changes, feature‑flagged default‑on for easy rollback; file/function size constraints; add tests; maintain current metrics/reporting patterns.

# Scope

- In scope: Scheduler guard to skip Delay; feature flag plumbing; minimal config addition; tests for success and guard off; docs update in this plan only.
- Out of scope: Proposer probability adjustments; exporter filtering; changing event queue semantics outside the guard path.

# Alternatives Considered

1) Scheduler skip at `_propose_and_schedule` (Recommended)
   - Pros: Minimal change surface; preserves proposer stats; no extra events or CSV rows; matches “advance to next event_hook”.
   - Cons: Slight divergence between proposed vs committed stats; requires a small conditional in the hot path.
   - Risks: Edge cases if batch contains follow‑ups after Delay (assumed first op is authoritative; proposer guarantees ordering).

2) Proposer filters out `Delay`
   - Pros: Eliminates Delay earlier; simpler scheduler path.
   - Cons: Alters selection distribution; can increase `no_candidate`; breaks comparability of proposer metrics.
   - Risks: Starvation in states where Delay mass is high.

3) Exporter filters CSV rows where `op_name == 'Delay'`
   - Pros: Zero scheduler changes; very low effort.
   - Cons: Events still occur; does not actually “advance to next hook immediately”.
   - Risks: Hidden time consumption remains in timelines.

# Design (Chosen: 1)

- Add feature flag `features.skip_delay_in_proposal` (default: true) in `config.yaml` under `features` to guard the behavior.
- In `scheduler.py:_propose_and_schedule(now, hook)`, immediately after obtaining `batch` and before any reservation logic, add an early‑return guard:
  - If `batch.ops` exists and `batch.ops[0].op_name == 'Delay'` and the feature flag is enabled, set `metrics['last_reason'] = 'skip_delay'` and return `(0, False, 'skip_delay')`.
- Do not emit OP_START/OP_END/PHASE_HOOK events for this path; because the early return precedes reservation and `_emit_op_events`.
- Leave proposer behavior unchanged to preserve proposal counts and phase diagnostics.

# Changelist (Minimal)

- File: scheduler.py
  - Location: `_propose_and_schedule` after the `batch = _proposer.propose(...)` line.
  - Add `_skip_delay_enabled(cfg)` helper (local) and early return.
- File: config.yaml
  - Add `features.skip_delay_in_proposal: true` with comment.
- File: tests/ (new tests)
  - Add tests covering both flag on/off behavior and ensuring no CSV rows or events are produced when skipping.

# Affected Code References

- scheduler.py:303 — `batch = _proposer.propose(...)`
- scheduler.py:295 — `_propose_and_schedule(self, now, hook)` signature and context
- main.py: InstrumentedScheduler event/row generation relies on `_emit_op_events` which is bypassed by early return
- config.yaml: features block exists; add new flag

# Detailed Steps

1) Add feature flag in config
   - Insert under `features:`: `skip_delay_in_proposal: true  # Skip reservation/CSV when first op is Delay`

2) Implement scheduler guard
   - In `_propose_and_schedule`:
     - Extract first op safely: `first = batch.ops[0] if getattr(batch, 'ops', None) else None`.
     - Helper:
       ```python
       def _skip_delay_enabled(cfg):
           try:
               return bool(((cfg.get('features', {}) or {}).get('skip_delay_in_proposal', True)))
           except Exception:
               return True
       ```
     - Guard:
       ```python
       if first and str(getattr(first, 'op_name', '')) == 'Delay' and _skip_delay_enabled(cfg_used):
           self.metrics['last_reason'] = 'skip_delay'
           return (0, False, 'skip_delay')
       ```

3) Tests
   - Unit: fabricate a config where proposer deterministically selects `Delay` for a refill hook; assert that with flag on: `tick().committed == 0`, `reason == 'skip_delay'`, no `OP_START/OP_END` pushed, and exporter surfaces no rows for that tick.
   - Unit (flag off): same setup with `features.skip_delay_in_proposal = false`; assert reservation proceeds and appropriate events occur.
   - Regression: ensure non‑Delay ops unaffected; window exceed and rollback reasons remain unchanged.

4) Metrics/Observability
   - `metrics['last_reason']` set to `'skip_delay'` for visibility.
   - No other metrics changes needed.

5) Rollout
   - Default enabled; if issues, set `features.skip_delay_in_proposal: false` to revert at runtime.

# Risks and Mitigations

- Batch contains more than one op and relies on Delay timing
  - Mitigation: current proposer semantics use first op as the scheduled head; skipping maintains determinism by doing nothing and advancing hooks.
- Heavy `Delay` probability leads to many skips and fewer commits
  - Mitigation: acceptable by spec; consider turning off flag for distribution studies.
- Downstream analytics expecting Delay rows in CSV
  - Mitigation: communicate change; flag allows reverting.

# Validation Checklist

- With flag on and Delay proposed, no reservations, no events, no CSV rows.
- With flag off, behavior unchanged; Delay appears as before.
- Other ops unaffected; bootstrap overlays remain compatible.

# Backout Plan

- Toggle `features.skip_delay_in_proposal: false` to restore legacy behavior without reverting code.

# Notes

- Keep change surface small and explicit; avoid modifying proposer/exporter unless required by future findings.

