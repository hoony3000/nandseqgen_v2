---
title: ResourceManager Validator Integration Guide
status: draft
updated: 2025-09-07
---

Overview
- ResourceManager integrates lightweight validation rules for state-forbid (ODT/SUSPEND/CACHE) and address-dependent EPR via a pluggable callback.
- Default behavior is unchanged until explicitly enabled by config.

Enablement (config.yaml)
- Under `constraints`:
  - `enabled_rules: ["state_forbid", "addr_dep"]` — enable state rules and address-dependent rules.
  - `enable_epr: true` — allow RM to call the EPR policy.
  - `epr: { offset_guard: <int> }` — optional, overrides AddressManager.offset for READ guard.

Basic Example
```yaml
constraints:
  enabled_rules: ["state_forbid", "addr_dep"]
  enable_epr: true
  epr:
    offset_guard: 0
```

Register Address Policy (at runtime)
- Provide an EPR callback that matches the standard interface. AddressManager exposes `check_epr` (bind as a basic example):
```python
rm.register_addr_policy(am.check_epr)
```

Rules and Reasons
- State-forbid reasons: `state_forbid_suspend`, `state_forbid_odt`, `state_forbid_cache`.
- EPR failures summarized as `epr_dep` with subcodes captured in `rm.last_validation()["epr_failures"]`.

Overlay (same-transaction awareness)
- RM tracks effects of successful `reserve()` within a transaction to evaluate later ops consistently.
- Overlay content keys: `(die, block) -> { "addr_state": int? }`.

Backward Compatibility
- If `constraints.enabled_rules` is empty or `enable_epr` is false, validators are no-ops and RM behavior is identical to previous versions.
