---
title: ResourceManager resume ISSUE state timeline fix plan
date: 2025-09-16
author: codex
status: draft
---

# Problem 1-Pager

- Background: ResourceManager.commit builds the op_state timeline used by downstream CSV exporters and diagnostics. For RESUME operations (ERASE_RESUME, PROGRAM_RESUME) it currently filters out ISSUE segments before handing entries to `_StateTimeline.reserve_op`.
- Problem: When a RESUME is scheduled, its ISSUE phase never appears in `op_state_timeline_*.csv`, so consumers cannot correlate the resume issuance time with other events. This diverges from PRD expectations and breaks timeline analysis.
- Goal: Ensure RESUME operations record their ISSUE phase in the op_state timeline while keeping the existing filtering for non-RESUME states intact.
- Non-Goals: Do not change suspend bookkeeping, exclusion logic, or CSV schemas. Avoid altering how ISSUE is handled for other operation families.
- Constraints: Keep changes localized (≤300 LOC touched, functions ≤50 LOC). Maintain quantization semantics and backward compatibility for configs without toggles.

# Options (pros/cons/risks)

1. Adjust `_affects_state`/`commit` to keep ISSUE for RESUME bases only.
   - Advantage: Minimal surface area; aligns behavior with PRD at the data source.
   - Drawback: Needs careful guard to avoid reintroducing ISSUE for other ops unintentionally.
   - Risk: If filtering logic is bypassed incorrectly, we could duplicate segments or violate historical expectations.
2. Post-process timelines (e.g., exporter layer) to re-insert ISSUE for RESUME.
   - Advantage: Zero change to ResourceManager internals.
   - Drawback: Duplicates logic across exporters and leaves in-memory timeline inconsistent.
   - Risk: Consumers that read the in-memory timeline (tests, analytics) would still miss ISSUE, causing divergence.

# Chosen Approach

- Proceed with Option 1: make `commit` retain ISSUE for RESUME operations via precise filtering, ensuring only the targeted bases change behavior.

# Work Plan

1. Characterize current behavior with a focused unit test (new `tests/test_resourcemgr_resume_timeline.py`) that schedules ERASE/PROGRAM suspend/resume and asserts missing ISSUE state today.
2. Update `ResourceManager.commit` filtering so RESUME bases keep ISSUE in `st_for_tl`, preferably via a helper that strips ISSUE only when explicitly configured.
3. Extend/adjust the new test to expect ISSUE presence and run the suite (`./.venv/bin/python -m pytest tests/test_resourcemgr_resume_timeline.py`).
4. Document the change briefly in `docs/PRD_v2.md` or changelog if needed and sanity-check CSV generation via existing scripts if applicable.

# Validation

- Unit test covering suspend→resume path and verifying timeline segment names.
- Optional manual run of the generator to confirm CSV now includes `*_RESUME.ISSUE` rows with correct timestamps.

# Open Questions

- Does any consumer rely on RESUME lacking ISSUE? Need to confirm via doc/spec review before implementing. -> (reviewed) No.

# Notes

- Keep guard clauses explicit (`if base_upper in {"ERASE_RESUME", "PROGRAM_RESUME"}`) to avoid silent behavior changes for future resume-like ops.
