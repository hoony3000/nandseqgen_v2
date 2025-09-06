from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple
import os

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


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
    # Exclude ISSUE state from phase_conditional runtime keys
    out = [s for s in out if str(s).upper() != "ISSUE"]
    # Append END as virtual terminal state even if only ISSUE existed originally
    if lst:
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


def _overrides_by_name(
    cfg: Dict[str, Any],
    key: str,
    candidates: List[str],
    names_by_base: Dict[str, List[str]],
) -> Dict[str, float]:
    """Compute override weights per candidate op_name for a given op_state key.

    Supports three forms for cfg['phase_conditional_overrides']:
      - Backcompat flat mapping: { base|op_name: weight } applied globally.
      - Global block: { 'global': { base|op_name: weight } }
      - Per-key block: { '<BASE.STATE>': { base|op_name: weight } }

    For base-scoped entries, the weight is shared uniformly among candidate
    op_names that belong to that base (excluded candidates are ignored).
    For op_name-scoped entries, the weight is applied directly.
    """
    src = (cfg.get("phase_conditional_overrides", {}) or {})
    # Precompute base_of(op_name)
    base_of: Dict[str, str] = {}
    for b, names in names_by_base.items():
        for n in names:
            base_of[n] = b

    def _apply(entries: Dict[str, float], acc: Dict[str, float], *, replace: bool = False) -> None:
        for k, w in (entries or {}).items():
            try:
                # Clamp negatives to 0.0; allow explicit zero to force removal
                weight = max(0.0, float(w))
            except Exception:
                weight = 0.0
            k = str(k)
            # If k matches a base, distribute across its candidate names
            if k in names_by_base:
                lst = [n for n in candidates if base_of.get(n) == k]
                if not lst:
                    continue
                share = weight / float(len(lst)) if len(lst) > 0 else 0.0
                for n in lst:
                    if replace:
                        acc[n] = share
                    else:
                        acc[n] = acc.get(n, 0.0) + share
            # Else if k matches a candidate op_name, apply directly
            elif k in candidates:
                if replace:
                    acc[k] = weight
                else:
                    acc[k] = acc.get(k, 0.0) + weight
            # Otherwise ignore unknown keys

    out: Dict[str, float] = {}
    # 1) Backcompat: flat mapping at top-level (no 'global' and no dotted keys)
    flat: Dict[str, float] = {}
    for k, v in src.items():
        if isinstance(v, (int, float)):
            flat[str(k)] = float(v)
    if flat:
        _apply(flat, out, replace=False)
    # 2) Global block
    if isinstance(src.get("global"), dict):
        _apply({str(k): float(v) for k, v in (src.get("global") or {}).items()}, out, replace=False)
    # 3) Per-op_state block
    if isinstance(src.get(key), dict):
        # Per-key entries replace prior contributions (global/backcompat)
        _apply({str(k): float(v) for k, v in (src.get(key) or {}).items()}, out, replace=True)
    return out


def apply_overrides_to_pc(cfg: Dict[str, Any], pc: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """Apply cfg['phase_conditional_overrides'] onto an existing pc map.

    - For each key in `pc`, compute overrides among current candidates only.
    - Overridden weights replace existing ones (including explicit zeros which remove the entry).
    - Non-overridden candidates keep their existing weights.
    - Positive entries are renormalized to sum to 1.0 per key.
    """
    names_by_base = _op_names_by_base(cfg)
    out: Dict[str, Dict[str, float]] = {}
    for key, dist in (pc or {}).items():
        # Candidates are the current names in the distribution
        cand = [str(n) for n in (dist or {}).keys()]
        if not cand:
            out[key] = {}
            continue
        fixed = _absolute_overrides(cfg, str(key), cand, names_by_base)
        sum_fixed = sum(v for v in fixed.values() if v > 0.0)
        if sum_fixed >= 1.0:
            # Fixed overrides dominate; renormalize fixed to 1.0
            out[key] = {n: (v / sum_fixed) for n, v in fixed.items() if v > 0.0}
            continue
        # Distribute leftover proportionally to existing non-fixed entries
        rem = 1.0 - sum_fixed
        # Existing positives among non-fixed
        # Only names not mentioned in fixed are eligible to receive leftover
        non_fixed = {n: float(p) for n, p in (dist or {}).items() if (n not in fixed) and float(p) > 0.0}
        s_nf = sum(non_fixed.values())
        out_key: Dict[str, float] = {}
        if s_nf > 0.0:
            for n, v in non_fixed.items():
                out_key[n] = (v / s_nf) * rem
        # Add fixed entries
        for n, v in fixed.items():
            if v > 0.0:
                out_key[n] = float(v)
        out[key] = out_key
    return out


def _absolute_overrides(
    cfg: Dict[str, Any],
    key: str,
    candidates: List[str],
    names_by_base: Dict[str, List[str]],
) -> Dict[str, float]:
    """Resolve overrides to absolute final probabilities for given candidates.

    Semantics:
      - Values in phase_conditional_overrides are treated as FINAL probability mass,
        not pre-normalization weights.
      - Precedence: flat/global → then per-key, where per-key replaces any global
        contribution for the same symbol.
      - Base-scoped entries assign their mass uniformly to candidate op_names of that base,
        excluding op_names with an explicit per-op override already set.
      - Unknown symbols and excluded candidates are ignored.
    Returns name->final_mass (can sum to any value; caller handles normalization with others).
    """
    src = (cfg.get("phase_conditional_overrides", {}) or {})
    candidates = [str(n) for n in (candidates or [])]
    cand_set = set(candidates)
    # Build base_of mapping
    base_of: Dict[str, str] = {}
    for b, lst in (names_by_base or {}).items():
        for n in (lst or []):
            base_of[str(n)] = str(b)

    def _collect_numeric(d: Dict[str, Any]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for k, v in (d or {}).items():
            try:
                out[str(k)] = float(v)
            except Exception:
                continue
        return out

    # Global layer: flat + global
    flat = {k: v for k, v in src.items() if isinstance(v, (int, float))}
    glob = _collect_numeric(src.get("global", {}) if isinstance(src.get("global"), dict) else {})
    g_all: Dict[str, float] = {}
    g_all.update(_collect_numeric(flat))
    g_all.update(glob)
    # Per-key layer
    pk = _collect_numeric(src.get(key, {}) if isinstance(src.get(key), dict) else {})

    # Resolve op-name specific masses with precedence (global → per-key override)
    op_masses: Dict[str, float] = {}
    # From global op-name entries
    for sym, mass in g_all.items():
        if sym in cand_set and sym in base_of:
            # sym is op_name
            op_masses[sym] = max(0.0, float(mass))
    # From per-key op-name entries (replace)
    for sym, mass in pk.items():
        if sym in cand_set and sym in base_of:
            op_masses[sym] = max(0.0, float(mass))

    # Resolve base-level masses: start with global, then per-key overrides
    base_masses: Dict[str, float] = {}
    for sym, mass in g_all.items():
        if sym in names_by_base:
            base_masses[sym] = max(0.0, float(mass))
    for sym, mass in pk.items():
        if sym in names_by_base:
            base_masses[sym] = max(0.0, float(mass))

    # Distribute base-level masses uniformly across remaining candidates of that base
    for b, mass in base_masses.items():
        lst = [n for n in candidates if base_of.get(n) == b]
        # Exclude names already assigned by op-level overrides
        lst = [n for n in lst if n not in op_masses]
        if not lst:
            continue
        share = (float(mass) / float(len(lst))) if len(lst) > 0 else 0.0
        for n in lst:
            op_masses[n] = max(0.0, share)

    return op_masses


def build_phase_conditional(cfg: Dict[str, Any], *, seed: int = 1729) -> Dict[str, Dict[str, float]]:
    """Build phase_conditional per PRD policy using only cfg content.

    - Keys: base.state for each base's states + END (ISSUE excluded)
    - Candidates: all op_names minus those whose base is listed in exclusion_groups of exclusions_by_op_state[key]
    - Overrides: cfg['phase_conditional_overrides'] supports
        * global: { base|op_name: weight } applied to all keys
        * per-key: { '<BASE.STATE>': { base|op_name: weight } } applied to that key
        * flat(backcompat): { base|op_name: weight } equivalent to global
      Base-scoped weights share uniformly among candidate op_names of that base. Excluded candidates are ignored.
    - Non-overridden candidates receive deterministic random weights; all positive weights normalized to sum 1
    """
    rng = random.Random(int(seed))
    names_by_base = _op_names_by_base(cfg)
    all_names: List[str] = []
    for lst in names_by_base.values():
        all_names.extend(lst)
    # Collect op_state keys (exclude DEFAULT base; use fallback DEFAULT instead)
    op_bases = [b for b in (cfg.get("op_bases", {}) or {}).keys() if str(b).upper() != "DEFAULT"]
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
        # Absolute overrides: treat configured values as final masses
        fixed = _absolute_overrides(cfg, key, candidates, names_by_base)
        sum_fixed = sum(v for v in fixed.values() if v > 0.0)
        if sum_fixed >= 1.0:
            # Normalize fixed to 1.0 and drop the rest
            pc[key] = {k: (v / sum_fixed) for k, v in fixed.items() if v > 0.0}
            continue
        # Fill remaining with deterministic random among non-fixed candidates
        rem = 1.0 - sum_fixed
        # Only names not mentioned in fixed are eligible for random fill
        others = [n for n in candidates if n not in fixed]
        if not others:
            # Only fixed entries
            pc[key] = {k: v for k, v in fixed.items() if v > 0.0}
            continue
        rnd: Dict[str, float] = {n: rng.random() for n in others}
        s_rnd = sum(rnd.values())
        out: Dict[str, float] = {}
        if s_rnd > 0.0:
            for n, v in rnd.items():
                out[n] = (v / s_rnd) * rem
        # Merge fixed
        for n, v in fixed.items():
            if v > 0.0:
                out[n] = float(v)
        pc[key] = out
    # Also build a DEFAULT fallback distribution if not explicitly present in cfg
    if "DEFAULT" not in ((cfg.get("phase_conditional", {}) or {})):
        candidates = list(all_names)
        if candidates:
            fixed = _absolute_overrides(cfg, "DEFAULT", candidates, names_by_base)
            sum_fixed = sum(v for v in fixed.values() if v > 0.0)
            if sum_fixed >= 1.0:
                pc["DEFAULT"] = {k: (v / sum_fixed) for k, v in fixed.items() if v > 0.0}
            else:
                rem = 1.0 - sum_fixed
                others = [n for n in candidates if n not in fixed]
                rnd = {n: rng.random() for n in others}
                s_rnd = sum(rnd.values())
                out: Dict[str, float] = {}
                if s_rnd > 0.0:
                    for n, v in rnd.items():
                        out[n] = (v / s_rnd) * rem
                for n, v in fixed.items():
                    if v > 0.0:
                        out[n] = float(v)
                pc["DEFAULT"] = out
    return pc


def ensure_phase_conditional(cfg: Dict[str, Any], *, seed: int = 1729, force: bool = False) -> Dict[str, Any]:
    """Ensure cfg['phase_conditional'] is populated.

    - If force is False: only fill when missing or empty. Existing keys are preserved.
    - If force is True: rebuild all state keys, while preserving 'DEFAULT' if present.
    """
    cur = (cfg.get("phase_conditional", {}) or {})
    keep_default = cur.get("DEFAULT")
    has_non_default = any(k != "DEFAULT" for k in cur.keys())
    # Rebuild when:
    #  - force is True, or
    #  - no keys, or only DEFAULT exists (treated as effectively empty)
    if (not force) and has_non_default:
        return cfg
    pc = build_phase_conditional(cfg, seed=seed)
    if keep_default:
        pc["DEFAULT"] = keep_default
    out = dict(cfg)
    out["phase_conditional"] = pc
    return out


def _load_yaml(path: str) -> Dict[str, Any]:
    if not path:
        return {}
    if yaml is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _dump_yaml(data: Dict[str, Any], path: str) -> None:
    if not path or yaml is None:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_op_state_probs(path: str) -> Dict[str, Dict[str, float]]:
    """Load phase_conditional map from a YAML file.

    Accepts either a top-level mapping or a mapping under 'phase_conditional'.
    Returns an empty dict on failure.
    """
    data = _load_yaml(path)
    if not data:
        return {}
    if isinstance(data.get("phase_conditional"), dict):
        pc = data.get("phase_conditional") or {}
    elif isinstance(data, dict):
        pc = data
    else:
        return {}
    # coerce to name->prob floats
    out: Dict[str, Dict[str, float]] = {}
    try:
        for k, v in pc.items():
            if isinstance(v, dict):
                out[str(k)] = {str(n): float(p) for n, p in v.items()}
    except Exception:
        return {}
    return out


def save_op_state_probs(pc: Dict[str, Dict[str, float]], path: str) -> None:
    """Save phase_conditional map to YAML file under 'phase_conditional' key."""
    if not path:
        return
    _dump_yaml({"phase_conditional": pc}, path)


def ensure_from_file_or_build(
    cfg: Dict[str, Any], *, path: str, seed: int = 1729, force: bool = False
) -> Dict[str, Any]:
    """If file exists and not forcing, load op_state_probs; else build and save.

    - When loading: if the file lacks DEFAULT but cfg has one, keep cfg.DEFAULT.
    - When building: preserve cfg.DEFAULT and write out the result.
    """
    cur_default = (cfg.get("phase_conditional", {}) or {}).get("DEFAULT")
    if (not force) and path and os.path.exists(path):
        pc = load_op_state_probs(path)
        if pc:
            if (cur_default is not None) and ("DEFAULT" not in pc):
                pc["DEFAULT"] = cur_default
            # Apply overrides on top of loaded map (PRD override policy)
            try:
                pc = apply_overrides_to_pc(cfg, pc)
            except Exception:
                # Best-effort: if override application fails, keep loaded map
                pass
            out = dict(cfg)
            out["phase_conditional"] = pc
            return out
    # Build and save
    out = ensure_phase_conditional(cfg, seed=seed, force=True)
    pc2 = out.get("phase_conditional", {}) or {}
    if path:
        try:
            save_op_state_probs(pc2, path)
        except Exception:
            pass
    return out
