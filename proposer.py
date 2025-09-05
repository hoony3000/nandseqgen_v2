from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Ports: use narrow types from ResourceManager for Address/Scope
from resourcemgr import Address, Scope  # runtime types shared across modules


# ------------------------------
# DTOs for Scheduler integration
# ------------------------------
@dataclass(frozen=True)
class ProposedOp:
    op_name: str
    base: str
    targets: List[Address]
    scope: Scope
    start_us: float


@dataclass(frozen=True)
class ProposedBatch:
    ops: List[ProposedOp]
    source: str
    hook: Dict[str, Any]
    metrics: Optional[Dict[str, Any]] = None


# ------------------------------
# Internal op stub for feasibility
# ------------------------------
@dataclass(frozen=True)
class _StateSeg:
    name: str
    dur_us: float
    bus: bool = False


@dataclass(frozen=True)
class _OpStub:
    name: str
    base: str  # e.g., ERASE, PROGRAM_SLC, READ, READ4K, ...
    states: List[_StateSeg]


# ------------------------------
# Ports (Protocols by duck typing)
# ------------------------------
class ResourceView:
    def op_state(self, die: int, plane: int, at_us: float) -> Optional[str]:
        raise NotImplementedError

    def feasible_at(self, op: Any, targets: List[Address], start_hint: float, scope: Scope = Scope.PLANE_SET) -> Optional[float]:
        raise NotImplementedError


class AddressSampler:
    # Each should return numpy ndarray with shape (#, k, 3) of (die, block, page)
    def sample_erase(self, sel_plane=None, mode: str = "TLC", size: int = 1, sel_die=None):
        raise NotImplementedError

    def sample_pgm(self, sel_plane=None, mode: str = "TLC", size: int = 1, sequential: bool = False, sel_die=None):
        raise NotImplementedError

    def sample_read(self, sel_plane=None, mode: str = "TLC", size: int = 1, offset: Optional[int] = None, sequential: bool = False, sel_die=None):
        raise NotImplementedError


# ------------------------------
# Helpers
# --- lightweight file logger for proposer debug ---
_LOG_PATH: Optional[str] = None


def enable_file_log(path: str) -> None:
    """Enable proposer debug logs to be written to a file.

    If not enabled, logs are printed to stdout.
    """
    global _LOG_PATH
    _LOG_PATH = str(path)
    try:
        # Touch file and write a header
        with open(_LOG_PATH, "w", encoding="utf-8") as f:
            f.write("# proposer debug log\n")
    except Exception:
        # Fallback to disabled on failure
        _LOG_PATH = None


def _log(msg: str) -> None:
    try:
        if _LOG_PATH:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(str(msg) + "\n")
        else:
            print(str(msg))
    except Exception:
        # Best-effort; ignore logging failures
        pass
# ------------------------------
def _cfg_topology(cfg: Dict[str, Any]) -> Tuple[int, int]:
    topo = cfg.get("topology", {}) or {}
    return int(topo.get("dies", 1)), int(topo.get("planes", 1))


def _cfg_policies(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("policies", {}) or {}


def _pol_topN(cfg: Dict[str, Any]) -> int:
    return int(_cfg_policies(cfg).get("topN", 4))


def _pol_epsilon(cfg: Dict[str, Any]) -> float:
    try:
        return float(_cfg_policies(cfg).get("epsilon_greedy", 0.0))
    except Exception:
        return 0.0


def _pol_window(cfg: Dict[str, Any]) -> float:
    return float(_cfg_policies(cfg).get("admission_window", 0.5))


def _pol_maxtry(cfg: Dict[str, Any]) -> int:
    return int(_cfg_policies(cfg).get("maxtry_candidate", 4))


def _pol_maxplanes(cfg: Dict[str, Any]) -> int:
    return int(_cfg_policies(cfg).get("maxplanes", 4))


def _base_scope(cfg: Dict[str, Any], base: str) -> Scope:
    # Map cfg string scope -> Scope enum
    sc = ((cfg.get("op_bases", {}) or {}).get(base, {}) or {}).get("scope", "PLANE_SET")
    sc = str(sc).upper()
    if sc == "DIE_WIDE":
        return Scope.DIE_WIDE
    if sc == "NONE":
        return Scope.NONE
    return Scope.PLANE_SET


def _base_instant(cfg: Dict[str, Any], base: str) -> bool:
    return bool(((cfg.get("op_bases", {}) or {}).get(base, {}) or {}).get("instant_resv", False))


def _exclusion_groups(cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    eg = cfg.get("exclusion_groups", {}) or {}
    out: Dict[str, List[str]] = {}
    try:
        for g, lst in eg.items():
            out[str(g)] = [str(b) for b in (lst or [])]
    except Exception:
        pass
    return out


def _blocked_by_groups(cfg: Dict[str, Any], base: str, groups: List[str]) -> bool:
    eg = _exclusion_groups(cfg)
    for g in groups or []:
        if str(base) in set(eg.get(str(g), []) or []):
            return True
    return False


def _candidate_blocked_by_states(
    now: float,
    cfg: Dict[str, Any],
    res_view: Any,
    op_name: str,
    hook: Dict[str, Any],
) -> bool:
    # derive base
    base = str(((cfg.get("op_names", {}) or {}).get(op_name, {}) or {}).get("base"))
    die = int(hook.get("die", 0))
    plane = int(hook.get("plane", 0))

    # ODT
    try:
        odt = res_view.odt_state()
    except Exception:
        odt = None
    if odt:
        groups = (cfg.get("exclusions_by_odt_state", {}) or {}).get(str(odt), [])
        if _blocked_by_groups(cfg, base, groups):
            return True

    # Suspend (die-level)
    try:
        susp = res_view.suspend_states(die, at_us=float(now))
    except Exception:
        susp = None
    if susp:
        groups = (cfg.get("exclusions_by_suspend_state", {}) or {}).get(str(susp), [])
        if _blocked_by_groups(cfg, base, groups):
            return True

    # Cache (die-level or plane-level)
    try:
        cst = res_view.cache_state(die, plane=plane, at_us=float(now))
    except Exception:
        cst = None
    if cst:
        groups = (cfg.get("exclusions_by_cache_state", {}) or {}).get(str(cst), [])
        if _blocked_by_groups(cfg, base, groups):
            return True

    return False


def _build_states_from_cfg(cfg: Dict[str, Any], op_name: str, base: str) -> List[_StateSeg]:
    # Use op_bases[base].states for order+bus only.
    # Duration must come exclusively from op_names[op_name].durations[state_name].
    bases = (cfg.get("op_bases", {}) or {})
    names = (cfg.get("op_names", {}) or {})
    base_spec = (bases.get(base, {}) or {})
    states_meta = list(base_spec.get("states", []) or [])
    durations = ((names.get(op_name, {}) or {}).get("durations", {}) or {})

    # Map base state names to possible duration keys used in op_names.
    # Prefer exact match; otherwise try these alternatives in order.
    alias: Dict[str, List[str]] = {
        "DATA_OUT": ["DOUT", "DOUT4K"],
        "DATA_IN": ["DATAIN"],
    }

    out: List[_StateSeg] = []
    for st in states_meta:
        # st may be mapping like { 'ISSUE': {bus: true, duration: 0.4} } or { 'name': 'ISSUE', 'bus': true, 'duration': 0.4 }
        if isinstance(st, dict) and "name" in st:
            name = str(st.get("name"))
            bus = bool(st.get("bus", False))
        else:
            # YAML as key -> nested mapping form
            items = list(st.items()) if isinstance(st, dict) else []
            if items:
                name = str(items[0][0])
                v = items[0][1] or {}
                bus = bool(v.get("bus", False))
            else:
                continue
        # Duration strictly from op_names[op_name].durations
        if name in durations:
            dur = float(durations[name])
        else:
            keys = alias.get(name, [])
            val = None
            for k in keys:
                if k in durations:
                    val = durations[k]
                    break
            dur = float(val) if val is not None else 0.0
        out.append(_StateSeg(name=name, dur_us=dur, bus=bus))
    return out


def _build_op(cfg: Dict[str, Any], op_name: str, targets: List[Address]) -> _OpStub:
    op_def = (cfg.get("op_names", {}) or {}).get(op_name)
    if not op_def:
        raise KeyError(f"op_name not found in cfg.op_names: {op_name}")
    base = str(op_def.get("base"))
    states = _build_states_from_cfg(cfg, op_name, base)
    return _OpStub(name=op_name, base=base, states=states)


def _op_celltype(cfg: Dict[str, Any], op_name: str) -> Optional[str]:
    try:
        ct = (cfg.get("op_names", {}) or {}).get(op_name, {}).get("celltype")
        return None if ct in (None, "NONE") else str(ct)
    except Exception:
        return None


def _to_targets(addrs_nd: Any) -> List[Address]:
    """
    Accepts nested sequences or numpy arrays with shape (#, k, 3) or (#, 1, 3).
    If numpy is unavailable, falls back to parsing Python sequences.
    """
    if addrs_nd is None:
        return []
    # Try numpy path first for performance
    try:
        import numpy as np  # optional dependency
        a = np.array(addrs_nd)
        if a.size == 0:
            return []
        if a.ndim == 2 and a.shape[1] == 3:
            a = a.reshape(-1, 1, 3)
        vec = a[0]
        out: List[Address] = []
        for v in vec:
            d, b, p = int(v[0]), int(v[1]), int(v[2])
            out.append(Address(die=d, plane=0, block=b, page=p))
        return out
    except Exception:
        # Pure-Python fallback: expect list[list[tuple|list[int,int,int]]]
        try:
            # Normalize to first group
            g0 = None
            if isinstance(addrs_nd, (list, tuple)) and addrs_nd and isinstance(addrs_nd[0], (list, tuple)) and addrs_nd and len(addrs_nd[0]) and isinstance(addrs_nd[0][0], (list, tuple)):
                # shape like (#, k, 3)
                g0 = addrs_nd[0]
            elif isinstance(addrs_nd, (list, tuple)) and addrs_nd and isinstance(addrs_nd[0], (int,)):
                # shape like (3,) -> wrap
                g0 = [addrs_nd]
            elif isinstance(addrs_nd, (list, tuple)) and addrs_nd and isinstance(addrs_nd[0], (list, tuple)) and len(addrs_nd[0]) == 3:
                # shape like (#, 3) -> treat first as single
                g0 = [addrs_nd[0]]
            else:
                return []
            out: List[Address] = []
            for v in g0:
                if not isinstance(v, (list, tuple)) or len(v) < 3:
                    continue
                d, b, p = int(v[0]), int(v[1]), int(v[2])
                out.append(Address(die=d, plane=0, block=b, page=p))
            return out
        except Exception:
            return []


def _guess_plane_from_block(block: int, planes: int) -> int:
    try:
        return int(block % planes)
    except Exception:
        return 0


def _sample_targets_for_op(cfg: Dict[str, Any], addr: AddressSampler, op_name: str, sel_die: Optional[int], planes_hint: Optional[List[int]] = None) -> List[Address]:
    op_def = (cfg.get("op_names", {}) or {}).get(op_name) or {}
    base = str(op_def.get("base"))
    multi = bool(op_def.get("multi", False))
    cell = _op_celltype(cfg, op_name) or "TLC"

    dies, planes = _cfg_topology(cfg)

    if "ERASE" in base:
        if multi:
            # choose plane set up to maxplanes; try shrinking until >=2
            maxk = min(_pol_maxplanes(cfg), planes)
            for k in range(maxk, 1, -1):
                sel_planes = list(range(k)) if planes_hint is None else list(planes_hint[:k])
                adds = addr.sample_erase(sel_plane=sel_planes, mode=cell, size=1, sel_die=sel_die)
                t = _to_targets(adds)
                if t:
                    # fill plane index using block modulo topology planes
                    t2 = [Address(die=ti.die, plane=_guess_plane_from_block(ti.block, planes), block=ti.block, page=0) for ti in t]
                    return t2
            return []
        else:
            adds = addr.sample_erase(sel_plane=None, mode=cell, size=1, sel_die=sel_die)
            t = _to_targets(adds)
            if t:
                ti = t[0]
                return [Address(die=ti.die, plane=_guess_plane_from_block(ti.block, planes), block=ti.block, page=0)]
            return []

    # Treat any PROGRAM variants (e.g., PROGRAM_SLC) as PROGRAM
    if "PROGRAM" in base:
        if multi:
            maxk = min(_pol_maxplanes(cfg), planes)
            for k in range(maxk, 1, -1):
                sel_planes = list(range(k)) if planes_hint is None else list(planes_hint[:k])
                adds = addr.sample_pgm(sel_plane=sel_planes, mode=cell, size=1, sequential=False, sel_die=sel_die)
                t = _to_targets(adds)
                if t:
                    t2 = [Address(die=ti.die, plane=_guess_plane_from_block(ti.block, planes), block=ti.block, page=ti.page) for ti in t]
                    return t2
            return []
        else:
            adds = addr.sample_pgm(sel_plane=None, mode=cell, size=1, sequential=False, sel_die=sel_die)
            t = _to_targets(adds)
            if t:
                ti = t[0]
                return [Address(die=ti.die, plane=_guess_plane_from_block(ti.block, planes), block=ti.block, page=ti.page)]
            return []

    # READ-like families
    if "READ" in base:
        if multi:
            maxk = min(_pol_maxplanes(cfg), planes)
            for k in range(maxk, 1, -1):
                sel_planes = list(range(k)) if planes_hint is None else list(planes_hint[:k])
                adds = addr.sample_read(sel_plane=sel_planes, mode=cell, size=1, sequential=False, sel_die=sel_die)
                t = _to_targets(adds)
                if t:
                    t2 = [Address(die=ti.die, plane=_guess_plane_from_block(ti.block, planes), block=ti.block, page=ti.page) for ti in t]
                    return t2
            return []
        else:
            adds = addr.sample_read(sel_plane=None, mode=cell, size=1, sequential=False, sel_die=sel_die)
            t = _to_targets(adds)
            if t:
                ti = t[0]
                return [Address(die=ti.die, plane=_guess_plane_from_block(ti.block, planes), block=ti.block, page=ti.page)]
            return []

    # Unsupported bases: skip (no target addresses)
    return []


def _op_names_by_base(cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for name, spec in (cfg.get("op_names", {}) or {}).items():
        base = str((spec or {}).get("base"))
        out.setdefault(base, []).append(name)
    return out


def _choose_op_name_for_base(
    cfg: Dict[str, Any], base: str, multi: Optional[bool] = None, celltype: Optional[str] = None
) -> Optional[str]:
    names = _op_names_by_base(cfg).get(str(base), [])
    if not names:
        return None
    def ok(n: str) -> bool:
        spec = (cfg.get("op_names", {}) or {}).get(n, {}) or {}
        if celltype is not None and str(spec.get("celltype")) not in (celltype, None, "None", "NONE"):
            return False
        if multi is not None and bool(spec.get("multi", False)) != bool(multi):
            return False
        return True
    # First pass: strict match
    for n in names:
        if ok(n):
            return n
    # Fallbacks
    if celltype is not None:
        for n in names:
            if bool((cfg.get("op_names", {}) or {}).get(n, {}).get("multi", False)) == bool(multi):
                return n
    return names[0]


def _seq_spec(cfg: Dict[str, Any], base: str) -> Optional[Dict[str, Any]]:
    return ((cfg.get("op_bases", {}) or {}).get(str(base), {}) or {}).get("sequence")


def _seq_probs(seq: Dict[str, Any]) -> Dict[str, float]:
    d = seq.get("probs", {}) if isinstance(seq, dict) else {}
    out: Dict[str, float] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out[str(k)] = float(v)
    elif isinstance(d, list):
        for item in d:
            if isinstance(item, dict) and item:
                k, v = next(iter(item.items()))
                out[str(k)] = float(v)
    return out


def _seq_inherit(seq: Dict[str, Any]) -> Dict[str, List[str]]:
    inh = seq.get("inherit", {}) if isinstance(seq, dict) else {}
    out: Dict[str, List[str]] = {}
    if isinstance(inh, dict):
        for k, v in inh.items():
            if isinstance(v, list):
                out[str(k)] = [str(x) for x in v]
    elif isinstance(inh, list):
        for item in inh:
            if isinstance(item, dict) and item:
                k, v = next(iter(item.items()))
                if isinstance(v, list):
                    out[str(k)] = [str(x) for x in v]
    return out


def _targets_with_inherit(rules: List[str], targets: List[Address]) -> List[Address]:
    if not rules:
        return list(targets)
    # Apply page-related inheritance
    same_page = any(r in rules for r in ("same_page", "pgm_same_page", "same_page_from_program_suspend"))
    inc_page = ("inc_page" in rules)
    if not same_page and not inc_page:
        return list(targets)
    out: List[Address] = []
    for t in targets:
        page = t.page
        if page is None:
            out.append(Address(die=t.die, plane=t.plane, block=t.block, page=0 if inc_page else None))
            continue
        new_page = page + 1 if inc_page else page
        out.append(Address(die=t.die, plane=t.plane, block=t.block, page=new_page))
    return out


def _op_total_duration(op: _OpStub) -> float:
    return sum(s.dur_us for s in (op.states or []))


def _expand_sequence_once(
    cfg: Dict[str, Any], first_name: str, first_targets: List[Address], rng: Any
) -> Optional[Tuple[str, List[Address]]]:
    base1 = str(((cfg.get("op_names", {}) or {}).get(first_name, {}) or {}).get("base"))
    seq = _seq_spec(cfg, base1)
    if not seq:
        return None
    probs = _seq_probs(seq)
    if not probs:
        return None
    inherit = _seq_inherit(seq)
    # Weighted pick
    keys = list(probs.keys())
    weights = [float(probs[k]) for k in keys]
    tot = sum(weights)
    if tot <= 0:
        return None
    acc = 0.0
    r = (rng.random() if hasattr(rng, "random") else __import__("random").random()) * tot
    choice = keys[-1]
    for k, w in zip(keys, weights):
        acc += w
        if r <= acc:
            choice = k
            break
    base2 = choice.split(".")[0]
    rules = inherit.get(choice, [])
    # Inherit celltype when requested
    cell = _op_celltype(cfg, first_name) if ("same_celltype" in rules) else None
    multi = (len(first_targets) > 1) if ("multi" in rules) else None
    name2 = _choose_op_name_for_base(cfg, base2, multi=multi, celltype=cell)
    if not name2:
        return None
    t2 = _targets_with_inherit(rules, first_targets)
    return name2, t2


def _preflight_schedule(
    now: float,
    cfg: Dict[str, Any],
    res_view: ResourceView,
    ops: List[Tuple[str, List[Address]]],
) -> Optional[List[ProposedOp]]:
    if not ops:
        return None
    planned: List[ProposedOp] = []
    # First op determines initial start; use feasible_at with hint=now
    name0, targets0 = ops[0]
    op0 = _build_op(cfg, name0, targets0)
    scope0 = _base_scope(cfg, op0.base)
    t0 = res_view.feasible_at(op0, targets0, start_hint=float(now), scope=scope0)
    if t0 is None:
        return None
    planned.append(ProposedOp(op_name=name0, base=op0.base, targets=list(targets0), scope=scope0, start_us=float(t0)))
    # Chain others back-to-back with optional sequence_gap
    gap = float(_cfg_policies(cfg).get("sequence_gap", 0.0))
    tcur = float(t0) + _op_total_duration(op0)
    for (name_i, targets_i) in ops[1:]:
        if gap > 0:
            tcur += gap
        opi = _build_op(cfg, name_i, targets_i)
        scopei = _base_scope(cfg, opi.base)
        ti = res_view.feasible_at(opi, targets_i, start_hint=tcur, scope=scopei)
        if ti is None:
            return None
        planned.append(ProposedOp(op_name=name_i, base=opi.base, targets=list(targets_i), scope=scopei, start_us=float(ti)))
        tcur = float(ti) + _op_total_duration(opi)
    return planned


def _phase_key(hook: Dict[str, Any], res: ResourceView, now_us: float) -> str:
    # Hook may include die/plane and a label; prefer RM state at now
    die = int(hook.get("die", 0))
    plane = int(hook.get("plane", 0))
    st = res.op_state(die, plane, now_us)
    key: str
    if st:
        key = str(st)
    else:
        # Fallback to hook label if formatted as BASE.STATE
        lbl = str(hook.get("label", "")).strip()
        if "." in lbl:
            parts = lbl.split(".")
            if len(parts) >= 2:
                key = f"{parts[0]}.{parts[1]}"
            else:
                key = "DEFAULT"
        else:
            key = "DEFAULT"
    try:
        # Debug: trace how phase key is derived
        _log(
            f"[proposer] phase_key: now={float(now_us):.3f} die={die} plane={plane} rm_state={str(st)} hook_label={str(hook.get('label'))} -> key={key}"
        )
        if key.endswith(".ISSUE"):
            _log("[proposer][warn] phase_key ends with .ISSUE — check PHASE_HOOK timing and state mapping")
    except Exception:
        pass
    return key


def _phase_dist(cfg: Dict[str, Any], key: str) -> Dict[str, float]:
    pc = cfg.get("phase_conditional", {}) or {}
    dist = pc.get(key)
    if not dist:
        dist = pc.get("DEFAULT", {}) or {}
    # coerce to name->float
    out: Dict[str, float] = {}
    try:
        for k, v in (dist or {}).items():
            out[str(k)] = float(v)
    except Exception:
        return {}
    return out


def _sorted_candidates(dist: Dict[str, float], eps: float) -> List[Tuple[str, float]]:
    items = [(n, p) for n, p in dist.items() if float(p) > 0.0]
    items.sort(key=lambda x: -x[1])
    # Simple epsilon-greedy: with prob eps, shuffle the tail slightly
    return items


def _weighted_sample_candidates(dist: Dict[str, float], k: int, rng: Any) -> List[Tuple[str, float]]:
    """Sample up to k unique candidates from dist by weight.

    - Interprets values in `dist` as non‑negative weights (no need to be normalized)
    - Samples without replacement; each selected key's weight is zeroed before next draw
    - Uses `rng.random()` if available; otherwise falls back to `random.random()`
    Returns list of (name, original_weight) preserving the original weights for logging/tie‑breaks.
    """
    # Filter positive weights and keep original mapping
    keys: List[str] = []
    weights: List[float] = []
    for n, p in dist.items():
        try:
            w = float(p)
        except Exception:
            w = 0.0
        if w > 0.0:
            keys.append(str(n))
            weights.append(w)
    if not keys:
        return []
    k = max(0, min(int(k), len(keys)))
    if k == 0:
        return []
    # Iterative weighted draws without replacement
    out: List[Tuple[str, float]] = []
    for _ in range(k):
        tot = sum(weights)
        if tot <= 0.0:
            break
        try:
            r = (rng.random() if hasattr(rng, "random") else __import__("random").random()) * tot
        except Exception:
            from random import random as _r
            r = _r() * tot
        acc = 0.0
        idx = len(keys) - 1
        for i, w in enumerate(weights):
            acc += w
            if r <= acc:
                idx = i
                break
        name = keys[idx]
        # Record original weight from dist
        out.append((name, float(dist.get(name, 0.0))))
        # Zero out to avoid reselection
        weights[idx] = 0.0
    return out


def validate_phase_conditional(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Validate cfg['phase_conditional'] contents.

    Checks per-key distribution shape, name validity, positive mass and normalization.
    Returns an aggregate summary and logs concise per-key lines to the proposer log.
    """
    pc = (cfg.get("phase_conditional", {}) or {})
    op_names = (cfg.get("op_names", {}) or {})
    out: Dict[str, Any] = {
        "keys": 0,
        "empty": 0,
        "invalid_names": 0,
        "nonpos": 0,
        "not_normalized": 0,
    }

    def _summ(dist: Dict[str, Any]) -> Tuple[int, int, float, List[Tuple[str, float]], int, int]:
        vals: Dict[str, float] = {}
        bad_names = 0
        nonpos = 0
        for k, v in (dist or {}).items():
            name = str(k)
            try:
                p = float(v)
            except Exception:
                p = 0.0
            if name not in op_names:
                bad_names += 1
            if p <= 0.0:
                nonpos += 1
            vals[name] = p
        s = sum(vals.values()) if vals else 0.0
        pos = len([1 for v in vals.values() if v > 0.0])
        top = sorted(vals.items(), key=lambda x: -x[1])[:5]
        return len(vals), pos, s, top, bad_names, nonpos

    if not pc:
        _log("[proposer][pc] EMPTY (no keys)")
        return out

    _log(f"[proposer][pc] keys={len(pc)}")
    for k in sorted(pc.keys()):
        v = pc.get(k)
        if not isinstance(v, dict):
            out["keys"] += 1
            out["empty"] += 1
            _log(f"[proposer][pc] {k}: invalid type={type(v).__name__}")
            continue
        cnt, pos, s, top, bad, nonpos = _summ(v)
        out["keys"] += 1
        if cnt == 0 or pos == 0:
            out["empty"] += 1
        out["invalid_names"] += bad
        out["nonpos"] += nonpos
        if s > 0.0 and abs(s - 1.0) > 1e-6:
            out["not_normalized"] += 1
        top_s = ", ".join([f"{n}:{p:.4f}" for (n, p) in top])
        _log(
            f"[proposer][pc] {k}: cnt={cnt} pos={pos} sum={s:.6f}"
            + (f" top=[{top_s}]" if top else "")
        )

    _log(
        f"[proposer][pc] summary: keys={out['keys']} empty={out['empty']} invalid_names={out['invalid_names']} nonpos={out['nonpos']} not_normalized={out['not_normalized']}"
    )
    return out


def propose(now: float, hook: Dict[str, Any], cfg: Dict[str, Any], res_view: ResourceView, addr_sampler: AddressSampler, rng: Any) -> Optional[ProposedBatch]:
    """
    Top‑N greedy proposer (single-op batch, pure):
      - Reads phase-conditional distribution
      - Filters/samples targets and checks earliest feasible start within window
      - Picks earliest start among evaluated candidates with tie-break on prob

    Returns None if no candidate fits the admission window.
    """
    # Phase distribution
    key = _phase_key(hook, res_view, now)
    dist = _phase_dist(cfg, key)
    if not dist:
        return None

    topN = max(1, _pol_topN(cfg))
    eps = _pol_epsilon(cfg)
    W = float(_pol_window(cfg))
    maxtry = max(1, _pol_maxtry(cfg))

    try:
        # Debug: show distribution used for this phase key
        _log(
            f"[proposer] dist for key={key}: { {k: float(v) for k, v in dist.items()} }"
        )
    except Exception:
        pass

    # Candidate selection: weighted sampling by phase_conditional values
    cands = _weighted_sample_candidates(dist, topN, rng)
    # Fallback to deterministic topN if sampling yields none
    if not cands:
        cands = _sorted_candidates(dist, eps)[:topN]
    if not cands:
        return None

    best: Optional[Tuple[float, ProposedOp, float, List[ProposedOp]]] = None  # (t0, first_op, prob, full_plan)
    attempts: List[Dict[str, Any]] = []
    tried = 0
    try:
        _log(f"[proposer] candidates (topN={topN}): {[(n, round(p, 6)) for (n,p) in cands]}")
        _log(f"[proposer] window_us={W} maxtry={maxtry} epsilon_greedy={eps}")
    except Exception:
        pass
    for name, prob in cands:
        tried += 1
        if tried > maxtry:
            break
        # Light prefilter based on ODT/SUSPEND/CACHE states
        if _candidate_blocked_by_states(now, cfg, res_view, name, hook):
            attempts.append({"name": name, "prob": prob, "reason": "state_block"})
            try:
                _log(f"[proposer] try name={name} p={prob:.6f} -> state_block")
            except Exception:
                pass
            continue
        # Sample targets (address-bearing ops only)
        sel_die = hook.get("die")
        targets = _sample_targets_for_op(cfg, addr_sampler, name, sel_die=(int(sel_die) if sel_die is not None else None))
        if not targets:
            attempts.append({"name": name, "prob": prob, "reason": "sample_none"})
            try:
                _log(f"[proposer] try name={name} p={prob:.6f} -> sample_none")
            except Exception:
                pass
            continue
        # Build operation and evaluate earliest feasible start
        op_stub = _build_op(cfg, name, targets)
        scope = _base_scope(cfg, op_stub.base)
        t0 = res_view.feasible_at(op_stub, targets, start_hint=float(now), scope=scope)
        if t0 is None:
            attempts.append({"name": name, "prob": prob, "reason": "feasible_none"})
            try:
                _log(f"[proposer] try name={name} p={prob:.6f} -> feasible_none")
            except Exception:
                pass
            continue
        # Admission window check unless instant reservation
        instant = _base_instant(cfg, op_stub.base)
        if (not instant) and (t0 >= (now + W)):
            attempts.append({"name": name, "prob": prob, "reason": "window_exceed", "t0": float(t0)})
            try:
                _log(f"[proposer] try name={name} p={prob:.6f} -> window_exceed(t0={float(t0):.3f}, now={float(now):.3f}, W={W})")
            except Exception:
                pass
            continue
        # Sequence expansion (one step) + preflight plan
        seq_next = _expand_sequence_once(cfg, name, targets, rng)
        ops_chain: List[Tuple[str, List[Address]]] = [(name, targets)]
        if seq_next:
            ops_chain.append(seq_next)
        planned = _preflight_schedule(now, cfg, res_view, ops_chain)
        if not planned:
            attempts.append({"name": name, "prob": prob, "reason": "preflight_fail", "t0": float(t0)})
            try:
                _log(f"[proposer] try name={name} p={prob:.6f} -> preflight_fail")
            except Exception:
                pass
            continue
        p_first = planned[0]
        if best is None or (p_first.start_us < best[0]):
            best = (float(p_first.start_us), p_first, float(prob), planned)
        elif best and (abs(p_first.start_us - best[0]) < 1e-9):
            # tie-break: prob-weighted
            try:
                import random as _r
                r = rng.random() if hasattr(rng, "random") else _r.random()
                total = float(prob) + float(best[2])
                if total <= 0:
                    total = 1.0
                if r < (float(prob) / total):
                    best = (float(p_first.start_us), p_first, float(prob), planned)
            except Exception:
                pass

        attempts.append({"name": name, "prob": prob, "reason": "ok", "t0": float(p_first.start_us)})
        try:
            _log(f"[proposer] try name={name} p={prob:.6f} -> ok(t0={float(p_first.start_us):.3f})")
        except Exception:
            pass

    if best is None:
        return None
    # Whole-batch return (first op inside admission window already enforced)
    metrics = {
        "phase_key": key,
        "window_us": W,
        "topN": topN,
        "epsilon_greedy": eps,
        "maxtry_candidate": maxtry,
        "attempts": attempts,
        "selected": {
            "op_name": best[1].op_name,
            "base": best[1].base,
            "start_us": best[1].start_us,
            "len_batch": len(best[3]),
        },
    }
    try:
        sel = metrics["selected"]
        _log(
            f"[proposer] selected op={sel['op_name']} base={sel['base']} start_us={float(sel['start_us']):.3f} len_batch={int(sel['len_batch'])}"
        )
    except Exception:
        pass
    return ProposedBatch(ops=list(best[3]), source="proposer.topN_greedy", hook=dict(hook or {}), metrics=metrics)
