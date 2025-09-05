from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple


def _state_names_for_base(cfg: Dict[str, Any], base: str) -> List[str]:
    bases = (cfg.get("op_bases", {}) or {})
    spec = (bases.get(base, {}) or {})
    lst = list(spec.get("states", []) or [])
    out: List[str] = []
    for st in lst:
        if isinstance(st, dict) and "name" in st:
            name = str(st.get("name"))
            out.append(name)
        elif isinstance(st, dict) and st:
            # YAML single-key mapping style: { STATE: {bus:bool, duration:float} }
            k = next(iter(st.keys()))
            out.append(str(k))
    # Append END as virtual terminal state
    if out:
        out.append("END")
    # Dedup keep order
    seen = set()
    uniq: List[str] = []
    for s in out:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
    return uniq


def _op_names_by_base(cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for name, spec in (cfg.get("op_names", {}) or {}).items():
        if not isinstance(spec, dict):
            continue
        b = str((spec or {}).get("base"))
        out.setdefault(b, []).append(str(name))
    return out


def _excluded_bases_for_key(cfg: Dict[str, Any], op_state_key: str) -> List[str]:
    by_state = (cfg.get("exclusions_by_op_state", {}) or {})
    groups = list(by_state.get(op_state_key, []) or [])
    ex_groups = (cfg.get("exclusion_groups", {}) or {})
    out: List[str] = []
    for g in groups:
        lst = ex_groups.get(str(g), []) or []
        out.extend([str(x) for x in lst])
    # dedup
    seen = set()
    uniq: List[str] = []
    for b in out:
        if b not in seen:
            uniq.append(b)
            seen.add(b)
    return uniq


def _overrides_by_name(cfg: Dict[str, Any], candidates: List[str], names_by_base: Dict[str, List[str]]) -> Dict[str, float]:
    # config maps base -> weight
    src = (cfg.get("phase_conditional_overrides", {}) or {})
    # Build base for each candidate
    base_of: Dict[str, str] = {}
    for b, names in names_by_base.items():
        for n in names:
            base_of[n] = b
    out: Dict[str, float] = {}
    for b, w in src.items():
        try:
            weight = float(w)
        except Exception:
            weight = 0.0
        if weight <= 0.0:
            continue
        # names for this base among candidates
        lst = [n for n in candidates if base_of.get(n) == str(b)]
        if not lst:
            continue
        share = weight / float(len(lst))
        for n in lst:
            out[n] = out.get(n, 0.0) + share
    return out


def build_phase_conditional(cfg: Dict[str, Any], *, seed: int = 1729) -> Dict[str, Dict[str, float]]:
    """Build phase_conditional per PRD policy using only cfg content.

    - Keys: base.state for each base's states + END
    - Candidates: all op_names minus those whose base is listed in exclusion_groups of exclusions_by_op_state[key]
    - Overrides: cfg['phase_conditional_overrides'] base->weight applied uniformly to matching candidates (excluded ones ignored)
    - Non-overridden candidates receive deterministic random weights; all positive weights normalized to sum 1
    """
    rng = random.Random(int(seed))
    names_by_base = _op_names_by_base(cfg)
    all_names: List[str] = []
    for lst in names_by_base.values():
        all_names.extend(lst)
    # Collect op_state keys
    op_bases = list((cfg.get("op_bases", {}) or {}).keys())
    state_keys: List[str] = []
    for b in op_bases:
        for s in _state_names_for_base(cfg, str(b)):
            state_keys.append(f"{str(b)}.{str(s)}")
    # Dedup keep order
    seen = set()
    op_state_keys: List[str] = []
    for k in state_keys:
        if k not in seen:
            op_state_keys.append(k)
            seen.add(k)

    pc: Dict[str, Dict[str, float]] = {}
    # Faster base lookup
    base_of: Dict[str, str] = {}
    for b, lst in names_by_base.items():
        for n in lst:
            base_of[n] = b

    for key in op_state_keys:
        excl_bases = set(_excluded_bases_for_key(cfg, key))
        candidates = [n for n in all_names if base_of.get(n) not in excl_bases]
        if not candidates:
            pc[key] = {}
            continue
        # Start with overrides
        weights = _overrides_by_name(cfg, candidates, names_by_base)
        # Fill the rest with deterministic random
        for n in candidates:
            if n not in weights:
                weights[n] = rng.random()
        # Normalize over positive entries
        pos = {k: v for k, v in weights.items() if v > 0.0}
        s = sum(pos.values())
        if s <= 0.0:
            pc[key] = {}
        else:
            pc[key] = {k: (v / s) for k, v in pos.items()}
    return pc


def ensure_phase_conditional(cfg: Dict[str, Any], *, seed: int = 1729, force: bool = False) -> Dict[str, Any]:
    """Ensure cfg['phase_conditional'] is populated.

    - If force is False: only fill when missing or empty. Existing keys are preserved.
    - If force is True: rebuild all state keys, while preserving 'DEFAULT' if present.
    """
    cur = (cfg.get("phase_conditional", {}) or {})
    keep_default = cur.get("DEFAULT")
    if cur and not force:
        return cfg
    pc = build_phase_conditional(cfg, seed=seed)
    if keep_default:
        pc["DEFAULT"] = keep_default
    out = dict(cfg)
    out["phase_conditional"] = pc
    return out
