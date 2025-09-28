from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
    # Optional metadata for exporter/analysis
    meta: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ProposedBatch:
    ops: List[ProposedOp]
    source: str
    hook: Dict[str, Any]
    metrics: Optional[Dict[str, Any]] = None


# ------------------------------
# Diagnostics DTOs
# ------------------------------
@dataclass(frozen=True)
class StateBlockInfo:
    axis: str
    state: str
    groups: Tuple[str, ...]
    base: str
    die: Optional[int] = None
    plane: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "axis": self.axis,
            "state": self.state,
            "groups": list(self.groups),
            "base": self.base,
        }
        if self.die is not None:
            data["die"] = self.die
        if self.plane is not None:
            data["plane"] = self.plane
        return data


@dataclass(frozen=True)
class AttemptRecord:
    name: str
    prob: float
    reason: str
    details: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "name": self.name,
            "prob": float(self.prob),
            "reason": self.reason,
        }
        if self.details:
            data["details"] = dict(self.details)
        return data


@dataclass(frozen=True)
class ProposeDiagnostics:
    attempts: Tuple[AttemptRecord, ...]
    last_state_block: Optional[StateBlockInfo] = None

    def attempts_as_dict(self) -> List[Dict[str, Any]]:
        return [rec.as_dict() for rec in self.attempts]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempts": self.attempts_as_dict(),
            "last_state_block": None
            if self.last_state_block is None
            else self.last_state_block.as_dict(),
        }

    @property
    def last_state_block_details(self) -> Optional[Dict[str, Any]]:
        if self.last_state_block is None:
            return None
        return self.last_state_block.as_dict()


@dataclass(frozen=True)
class ProposeResult:
    batch: Optional[ProposedBatch]
    diagnostics: ProposeDiagnostics

    def has_batch(self) -> bool:
        return self.batch is not None


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
    # Optional method provided by ResourceManager; used opportunistically when present
    # def phase_key_at(self, die: int, plane: int, t: float, default: str = "DEFAULT", derive_end: bool = True, prefer_end_on_boundary: bool = True, exclude_issue: bool = True) -> str: ...
    # Optional: suspended ops snapshot for inherit rules (RECOVERY_RD.SEQ)
    # def suspended_ops(self, die: Optional[int] = None) -> List[Dict[str, Any]]: ...


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


def _pol_split_dout_per_plane(cfg: Dict[str, Any]) -> bool:
    try:
        return bool(_cfg_policies(cfg).get("split_dout_per_plane", True))
    except Exception:
        return True


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


def _pol_hook_targets_enabled(cfg: Dict[str, Any]) -> bool:
    try:
        pol = _cfg_policies(cfg)
        v = pol.get("hook_targets_enabled", True)
        return bool(True if v in (None, "None") else v)
    except Exception:
        return True


def _exclusion_groups(cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    eg = cfg.get("exclusion_groups", {}) or {}
    out: Dict[str, List[str]] = {}
    try:
        for g, lst in eg.items():
            out[str(g)] = [str(b) for b in (lst or [])]
    except Exception:
        pass
    return out


def _blocking_groups(cfg: Dict[str, Any], base: str, groups: Iterable[str]) -> Tuple[str, ...]:
    eg = _exclusion_groups(cfg)
    matched: List[str] = []
    base_str = str(base)
    for g in groups or []:
        g_str = str(g)
        members = set(eg.get(g_str, []) or [])
        if base_str in members:
            matched.append(g_str)
    return tuple(matched)


def _blocked_by_groups(cfg: Dict[str, Any], base: str, groups: List[str]) -> bool:
    return bool(_blocking_groups(cfg, base, groups))


def _pol_validate_pc(cfg: Dict[str, Any]) -> bool:
    try:
        pol = _cfg_policies(cfg)
        return bool(pol.get("validate_pc", False))
    except Exception:
        return False


def _excluded_bases_for_op_state_key(cfg: Dict[str, Any], key: str) -> List[str]:
    try:
        by_state = (cfg.get("exclusions_by_op_state", {}) or {})
        groups = list(by_state.get(str(key), []) or [])
        eg = _exclusion_groups(cfg)
        out: List[str] = []
        for g in groups:
            out.extend([str(x) for x in (eg.get(str(g), []) or [])])
        # dedup keep order
        seen = set()
        uniq: List[str] = []
        for b in out:
            if b not in seen:
                uniq.append(b)
                seen.add(b)
        return uniq
    except Exception:
        return []


def _apply_phase_overrides(cfg: Dict[str, Any], key: str, dist: Dict[str, float]) -> Dict[str, float]:
    """Apply runtime phase_conditional_overrides to a given key's distribution.

    Semantics:
    - Global then per-key precedence; per-key replaces same symbol from global.
    - Symbols can be op_name or base; base mass distributes to its candidate op_names.
      Distribution among a base's names is proportional to current dist for those
      names; if all zero/missing, distribute uniformly.
    - Candidate set starts from current dist keys; extend with override-referenced
      names/bases when allowed by exclusions and present in cfg.
    - If sum(overrides) >= 1: keep only overrides and normalize to 1.
      Else: scale remaining non-overridden positives proportionally to fill 1.
    - Zero masses act as explicit removals (they block receiving leftover).
    """
    names_by_base = _op_names_by_base(cfg)
    # base_of map for quick lookup
    base_of: Dict[str, str] = {}
    for b, lst in names_by_base.items():
        for n in lst:
            base_of[str(n)] = str(b)

    # Allowed check via exclusions_by_op_state
    excl = set(_excluded_bases_for_op_state_key(cfg, str(key)))
    def _allowed_name(n: str) -> bool:
        b = base_of.get(str(n))
        if b is None:
            return False
        return str(b) not in excl

    # Start candidates from existing dist keys
    cand: Dict[str, float] = {str(n): float(v) for n, v in (dist or {}).items()}

    # Collect override symbols and values
    src = (cfg.get("phase_conditional_overrides", {}) or {})

    def _collect_numeric(d: Any) -> Dict[str, float]:
        out: Dict[str, float] = {}
        if isinstance(d, dict):
            for k, v in d.items():
                try:
                    val = float(v)
                except Exception:
                    continue
                # Ignore negatives; treat zero as explicit removal if applied
                if val < 0.0:
                    continue
                out[str(k)] = val
        return out

    flat_numeric: Dict[str, float] = {}
    for k, v in src.items():
        if isinstance(v, (int, float)):
            try:
                vv = float(v)
                if vv >= 0.0:
                    flat_numeric[str(k)] = vv
            except Exception:
                continue
    glob = _collect_numeric(src.get("global"))
    g_all: Dict[str, float] = {}
    g_all.update(flat_numeric)
    g_all.update(glob)
    pk = _collect_numeric(src.get(str(key)))

    # Extend candidates from override symbols (op_name or base)
    def _extend_from(sym: str) -> None:
        s = str(sym)
        if s in base_of:
            if _allowed_name(s):
                cand.setdefault(s, cand.get(s, 0.0))
        elif s in names_by_base:
            for n in (names_by_base.get(s) or []):
                if _allowed_name(n):
                    cand.setdefault(str(n), cand.get(str(n), 0.0))
        # else: unknown symbol ignored

    for sym in list(g_all.keys() | pk.keys()):
        _extend_from(sym)

    # Resolve absolute masses per op_name with precedence
    fixed: Dict[str, float] = {}
    # 1) global op_name entries
    for sym, mass in g_all.items():
        if sym in base_of and sym in cand and _allowed_name(sym):
            fixed[sym] = float(mass)
    # 2) per-key op_name entries (replace)
    for sym, mass in pk.items():
        if sym in base_of and sym in cand and _allowed_name(sym):
            fixed[sym] = float(mass)

    # Base-level masses (global then per-key override)
    base_masses: Dict[str, float] = {b: m for b, m in g_all.items() if b in names_by_base}
    for b, m in pk.items():
        if b in names_by_base:
            base_masses[b] = float(m)

    for b, mass in base_masses.items():
        # Candidate names of this base that are allowed and not already fixed
        lst = [n for n in cand.keys() if (base_of.get(n) == b) and _allowed_name(n) and (n not in fixed)]
        if not lst:
            continue
        # Proportional to existing dist among these names; fallback to uniform
        weights = [max(0.0, float(dist.get(n, 0.0))) for n in lst]
        sw = sum(weights)
        if sw > 0.0:
            for n, w in zip(lst, weights):
                fixed[n] = float(mass) * (float(w) / float(sw))
        else:
            share = (float(mass) / float(len(lst))) if len(lst) > 0 else 0.0
            for n in lst:
                fixed[n] = share

    # Normalize with others according to rules
    sum_fixed = sum(v for v in fixed.values() if v > 0.0)
    if sum_fixed >= 1.0 - 1e-12:
        out = {n: (v / sum_fixed) for n, v in fixed.items() if v > 0.0}
    else:
        rem = 1.0 - sum_fixed
        others = {n: max(0.0, float(dist.get(n, 0.0))) for n in cand.keys() if n not in fixed}
        s = sum(others.values())
        out: Dict[str, float] = {}
        if s > 0.0:
            for n, v in others.items():
                out[n] = (float(v) / float(s)) * rem
            for n, v in fixed.items():
                if v > 0.0:
                    out[n] = float(v)
        else:
            # No positive others; renormalize fixed if any, else empty
            if sum_fixed > 0.0:
                out = {n: (v / sum_fixed) for n, v in fixed.items() if v > 0.0}
            else:
                out = {}

    # Optional one-line validation log
    if _pol_validate_pc(cfg):
        try:
            def _top3(d: Dict[str, float]) -> str:
                items = sorted(((n, float(p)) for n, p in d.items() if float(p) > 0.0), key=lambda x: -x[1])[:3]
                return ", ".join([f"{n}:{p:.4f}" for n, p in items])
            s_before = sum(max(0.0, float(v)) for v in (dist or {}).values())
            s_after = sum(out.values()) if out else 0.0
            _log(
                f"[proposer][pc-rt] {str(key)}: before_sum={s_before:.6f} top=[{_top3(dist)}]; "
                f"ovr_sum={sum_fixed:.6f} top=[{_top3({k:v for k,v in fixed.items() if v>0})}]; "
                f"after_sum={s_after:.6f} top=[{_top3(out)}]"
            )
        except Exception:
            pass
    return out


def _candidate_blocked_by_states(
    now: float,
    cfg: Dict[str, Any],
    res_view: Any,
    op_name: str,
    hook: Dict[str, Any],
) -> Tuple[bool, Optional[StateBlockInfo]]:
    # derive base context
    base = str(((cfg.get("op_names", {}) or {}).get(op_name, {}) or {}).get("base"))
    die = int(hook.get("die", 0))
    plane = int(hook.get("plane", 0))

    def _make_info(
        axis: str,
        state: Optional[str],
        groups: Iterable[str],
        *,
        die_hint: Optional[int] = None,
        plane_hint: Optional[int] = None,
    ) -> Optional[StateBlockInfo]:
        if not state:
            return None
        matched = _blocking_groups(cfg, base, groups)
        if not matched:
            return None
        return StateBlockInfo(
            axis=str(axis),
            state=str(state),
            groups=matched,
            base=base,
            die=die_hint,
            plane=plane_hint,
        )

    # ODT (global)
    try:
        odt = res_view.odt_state()
    except Exception:
        odt = None
    if odt:
        groups = list((cfg.get("exclusions_by_odt_state", {}) or {}).get(str(odt), []) or [])
        info = _make_info("ODT", str(odt), groups, die_hint=die)
        if info:
            return True, info

    # Suspend (die-level) — evaluate both axes when available
    axis_states: List[Tuple[str, str]] = []
    by_state_suspend: Dict[str, List[str]] = (cfg.get("exclusions_by_suspend_state", {}) or {})
    suspend_axes_supported = False
    try:
        es = getattr(res_view, "erase_suspend_state")(die, at_us=float(now))  # type: ignore[attr-defined]
        suspend_axes_supported = True
        if isinstance(es, str):
            axis_states.append(("ERASE", str(es)))
    except Exception:
        pass
    try:
        ps = getattr(res_view, "program_suspend_state")(die, at_us=float(now))  # type: ignore[attr-defined]
        suspend_axes_supported = True
        if isinstance(ps, str):
            axis_states.append(("PROGRAM", str(ps)))
    except Exception:
        pass
    for axis, state_val in axis_states:
        groups = list(by_state_suspend.get(str(state_val), []) or [])
        info = _make_info(axis, state_val, groups, die_hint=die)
        if info:
            return True, info
    if not suspend_axes_supported:
        # Fallback to legacy single-axis API (no NOT_* defaults available)
        try:
            susp = res_view.suspend_states(die, at_us=float(now))
        except Exception:
            susp = None
        if susp:
            groups = list(by_state_suspend.get(str(susp), []) or [])
            info = _make_info("SUSPEND", susp, groups, die_hint=die)
            if info:
                return True, info

    # Cache (die-level or plane-level)
    try:
        cst = res_view.cache_state(die, plane=plane, at_us=float(now))
    except Exception:
        cst = None
    if cst:
        groups = list((cfg.get("exclusions_by_cache_state", {}) or {}).get(str(cst), []) or [])
        plane_hint = plane if "READ" in str(cst).upper() else None
        info = _make_info("CACHE", str(cst), groups, die_hint=die, plane_hint=plane_hint)
        if info:
            return True, info

    return False, None


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
        # Support multiple YAML shapes:
        # 1) { 'name': 'ISSUE', 'bus': true }
        # 2) { 'ISSUE': { 'bus': true } }
        # 3) { 'ISSUE': None, 'bus': true }  (flattened form seen in config)
        if isinstance(st, dict) and "name" in st:
            name = str(st.get("name"))
            bus = bool(st.get("bus", False))
        else:
            items = list(st.items()) if isinstance(st, dict) else []
            if not items:
                continue
            key0, val0 = items[0][0], items[0][1]
            name = str(key0)
            if isinstance(val0, dict) and ("bus" in val0):
                bus = bool(val0.get("bus", False))
            else:
                # flattened form: bus sits at same level as key
                try:
                    bus = bool(st.get("bus", False))  # type: ignore[attr-defined]
                except Exception:
                    bus = False
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


def _is_addr_sampling_base(base: str) -> bool:
    """Return True if this op base should use AddressManager sampling.

    Allowed families per PRD and plan:
    - ERASE
    - PROGRAM family (excluding *_SUSPEND/*_RESUME)
    - READ family: READ/RECOVERY_RD/READ4K/PLANE_READ/PLANE_READ4K/CACHE_READ/PLANE_CACHE_READ/COPYBACK_READ
    """
    b = str(base or "").upper()
    if b == "ERASE":
        return True
    # Explicit READ-like bases
    read_bases = {
        "READ",
        "READ4K",
        "PLANE_READ",
        "PLANE_READ4K",
        "CACHE_READ",
        "PLANE_CACHE_READ",
        "COPYBACK_READ",
    }
    if b in read_bases:
        return True
    # PROGRAM families (exclude suspend/resume)
    if ("PROGRAM" in b) and ("SUSPEND" not in b) and ("RESUME" not in b):
        return True
    return False


def _targets_from_hook(hook: Dict[str, Any]) -> List[Address]:
    """Parse enriched PHASE_HOOK targets: [(die,plane,block,page,celltype?) ...]."""
    vals = hook.get("targets") if isinstance(hook, dict) else None
    out: List[Address] = []
    if not isinstance(vals, list):
        return out
    for item in vals:
        try:
            if isinstance(item, (list, tuple)) and len(item) >= 4:
                d = int(item[0]); p = int(item[1]); b = int(item[2])
                pg = None if item[3] in (None, "None") else int(item[3])
                out.append(Address(die=d, plane=p, block=b, page=pg))
        except Exception:
            continue
    return out


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


def _choose_op_name_for_base_uniform(
    cfg: Dict[str, Any],
    base: str,
    multi: Optional[bool] = None,
    celltype: Optional[str] = None,
    rng: Any = None,
    weights: Optional[Dict[str, float]] = None,
) -> Optional[str]:
    """Choose an op_name for the given base by uniform (or weighted) sampling.

    - Filters candidates by optional `multi` and `celltype` hints using cfg.op_names.
    - If `weights` is provided and contains positive weights for any of the filtered
      candidates, samples proportionally to those weights.
    - Otherwise, samples uniformly among filtered candidates.
    - Uses provided `rng.random()` when available; falls back to `random.random()`.
    """
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

    cand = [n for n in names if ok(n)]
    if not cand:
        return None

    # Weighted sampling if weights provided for any candidate (positive)
    keys: List[str] = []
    wts: List[float] = []
    if isinstance(weights, dict):
        for n in cand:
            try:
                w = float(weights.get(n, 0.0))
            except Exception:
                w = 0.0
            if w > 0.0:
                keys.append(n)
                wts.append(w)
    if keys and wts and sum(wts) > 0.0:
        tot = float(sum(wts))
        try:
            import random as _r
            r = (rng.random() if hasattr(rng, "random") else _r.random()) * tot
        except Exception:
            from random import random as _rr
            r = _rr() * tot
        acc = 0.0
        for n, w in zip(keys, wts):
            acc += float(w)
            if r <= acc:
                return n
        return keys[-1]

    # Uniform among candidates
    try:
        import random as _r
        r = (rng.random() if hasattr(rng, "random") else _r.random())
    except Exception:
        from random import random as _rr
        r = _rr()
    idx = int(r * len(cand))
    if idx >= len(cand):
        idx = len(cand) - 1
    return cand[idx]


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

@dataclass(frozen=True)
class SeqCtx:
    first_name: str
    first_targets: List[Address]
    last_step_targets: List[Address]
    last_program_targets: Optional[List[Address]]
    suspended_program_targets: Optional[List[Address]]
    die: Optional[int] = None


def _apply_inherit_rules(rules: List[str], prev_targets: List[Address], ctx: SeqCtx) -> List[Address]:
    """Apply extended inherit rules to produce new targets.

    Supported rules:
    - same_plane: implicit (keep prev die/plane/block)
    - same_page: keep prev page
    - inc_page: page + 1; None -> 0
    - prev_page: page - 1; None -> 0; lower bound 0
    - pgm_same_page: inherit page from ctx.last_program_targets; fallback to prev_targets page
    - same_page_from_program_suspend: page from ctx.suspended_program_targets; fallback to prev_targets
    - same_celltype, multi, none: ignored here (handled by name selection/scheduling)
    """
    if not rules:
        return list(prev_targets)

    # Determine base page source
    def _page_from(lst: Optional[List[Address]]) -> Optional[int]:
        if not lst:
            return None
        try:
            p = lst[0].page
            return int(p) if p is not None else None
        except Exception:
            return None

    use_same_page = ("same_page" in rules)
    use_inc = ("inc_page" in rules)
    use_prev = ("prev_page" in rules)
    use_pgm_same = ("pgm_same_page" in rules)
    use_from_suspend = ("same_page_from_program_suspend" in rules)

    # Determine target page according to precedence: suspend > pgm_same > same/inc/prev
    base_page: Optional[int] = None
    if use_from_suspend:
        base_page = _page_from(ctx.suspended_program_targets)
    if base_page is None and use_pgm_same:
        base_page = _page_from(ctx.last_program_targets)

    out: List[Address] = []
    for t in prev_targets:
        page = t.page
        # External page source overrides
        new_page: Optional[int]
        if base_page is not None:
            new_page = int(base_page)
        else:
            if use_inc:
                new_page = 0 if page is None else int(page) + 1
            elif use_prev:
                new_page = 0 if page is None else max(0, int(page) - 1)
            elif use_same_page:
                new_page = int(page) if page is not None else None
            else:
                new_page = page
        out.append(Address(die=t.die, plane=t.plane, block=t.block, page=new_page))
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
    if _feature_enabled(cfg, "uniform_subseq_sampling", True):
        name2 = _choose_op_name_for_base_uniform(cfg, base2, multi=multi, celltype=cell, rng=rng)
    else:
        name2 = _choose_op_name_for_base(cfg, base2, multi=multi, celltype=cell)
    if not name2:
        return None
    t2 = _targets_with_inherit(rules, first_targets)
    return name2, t2


def _expand_sequence_seq(
    cfg: Dict[str, Any],
    choice_key: str,
    first_name: str,
    first_targets: List[Address],
    hook: Dict[str, Any],
    res_view: ResourceView,
    rng: Any,
) -> List[Tuple[str, List[Address], Optional[Dict[str, Any]]]]:
    """Expand a .SEQ choice key into a full op chain using generate_seq_rules.

    Returns list of (op_name, targets) for each step defined in the sequence rules.
    The returned list does not include (first_name, first_targets); caller prepends that.
    """
    rules_root = (cfg.get("generate_seq_rules", {}) or {}).get(str(choice_key), {}) or {}
    seqs = list(rules_root.get("sequences", []) or [])
    if not seqs:
        return []

    # Build context
    try:
        die_hint = first_targets[0].die if first_targets else None
    except Exception:
        die_hint = None
    susp_prog_targets: Optional[List[Address]] = None
    try:
        fn = getattr(res_view, "suspended_ops", None)
        if callable(fn) and die_hint is not None:
            lst = fn(int(die_hint))
            # pick last PROGRAM-like entry
            for rec in (list(lst) if isinstance(lst, list) else []):
                pass
            # iterate from end
            if isinstance(lst, list):
                for i in range(len(lst) - 1, -1, -1):
                    rec = lst[i] or {}
                    b = str(rec.get("base", ""))
                    if "PROGRAM" in b and ("SUSPEND" not in b) and ("RESUME" not in b):
                        t = rec.get("targets") or []
                        if t:
                            # normalize Address objects
                            susp_prog_targets = [Address(tt.die, tt.plane, tt.block, tt.page) for tt in t]
                            break
    except Exception:
        susp_prog_targets = None

    ctx = SeqCtx(
        first_name=str(first_name),
        first_targets=list(first_targets),
        last_step_targets=list(first_targets),
        last_program_targets=(list(first_targets) if ("PROGRAM" in str(((cfg.get("op_names", {}) or {}).get(first_name, {}) or {}).get("base", ""))) else None),
        suspended_program_targets=susp_prog_targets,
        die=die_hint,
    )

    chain: List[Tuple[str, List[Address], Optional[Dict[str, Any]]]] = []

    def _is_program_base(b: str) -> bool:
        bb = str(b)
        return ("PROGRAM" in bb) and ("SUSPEND" not in bb) and ("RESUME" not in bb)

    # Celltype hint from first op when rule requests same_celltype
    first_cell = _op_celltype(cfg, first_name)

    # Optional per-step name weights by base for this sequence key
    name_weights_root = (rules_root.get("name_weights", {}) or {})

    for ent in seqs:
        if not isinstance(ent, dict) or not ent:
            continue
        base_i, rules_i = next(iter(ent.items()))
        base_i = str(base_i)
        rules_i = [str(x) for x in (rules_i or [])]
        # Name selection hints
        multi_hint: Optional[bool] = (len(first_targets) > 1) if ("multi" in rules_i) else None
        cell_hint: Optional[str] = first_cell if ("same_celltype" in rules_i) else None
        # Optional weights for this base at this choice key
        weights_i = None
        if _feature_enabled(cfg, "uniform_subseq_sampling", True):
            try:
                m = name_weights_root.get(base_i)
                if isinstance(m, dict):
                    weights_i = {str(k): float(v) for k, v in m.items()}
            except Exception:
                weights_i = None
            name_i = _choose_op_name_for_base_uniform(cfg, base_i, multi=multi_hint, celltype=cell_hint, rng=rng, weights=weights_i)
        else:
            name_i = _choose_op_name_for_base(cfg, base_i, multi=multi_hint, celltype=cell_hint)
        if not name_i:
            continue
        # Targets via inherit rules using ctx
        prev_t = ctx.last_step_targets
        t_i = _apply_inherit_rules(rules_i, prev_t, ctx)
        # Build meta with inherit hints when applicable
        meta_i: Optional[Dict[str, Any]] = None
        try:
            inherit_hints: Dict[str, Any] = {}
            if "same_celltype" in rules_i and first_cell not in (None, "None", "NONE"):
                inherit_hints["celltype"] = str(first_cell)
            if inherit_hints:
                meta_i = {"inherit_hints": inherit_hints, "inherit_from": str(first_name), "inherit_rules": list(rules_i)}
        except Exception:
            meta_i = None
        chain.append((name_i, t_i, meta_i))
        # Update context
        ctx = SeqCtx(
            first_name=ctx.first_name,
            first_targets=ctx.first_targets,
            last_step_targets=list(t_i),
            last_program_targets=(list(t_i) if _is_program_base(base_i) else ctx.last_program_targets),
            suspended_program_targets=ctx.suspended_program_targets,
            die=ctx.die,
        )
    return chain


def _expand_sequence_chain(
    cfg: Dict[str, Any],
    first_name: str,
    first_targets: List[Address],
    hook: Dict[str, Any],
    res_view: ResourceView,
    rng: Any,
) -> List[Tuple[str, List[Address], Optional[Dict[str, Any]]]]:
    """Expand sequence from the first op to full chain when applicable.

    - If no sequence: returns [(first_name, first_targets)]
    - If sequence picks a non-.SEQ symbol: append one step using op_bases[base].sequence.inherit
    - If sequence picks *.SEQ: expand using generate_seq_rules[choice].sequences
    """
    base1 = str(((cfg.get("op_names", {}) or {}).get(first_name, {}) or {}).get("base"))
    seq = _seq_spec(cfg, base1)
    if not seq:
        return [(first_name, first_targets, None)]
    probs = _seq_probs(seq)
    if not probs:
        return [(first_name, first_targets, None)]
    inherit = _seq_inherit(seq)
    # Weighted pick
    keys = list(probs.keys())
    weights = [float(probs[k]) for k in keys]
    tot = sum(weights)
    if tot <= 0:
        return [(first_name, first_targets)]
    acc = 0.0
    try:
        r = (rng.random() if hasattr(rng, "random") else __import__("random").random()) * tot
    except Exception:
        from random import random as _r
        r = _r() * tot
    choice = keys[-1]
    for k, w in zip(keys, weights):
        acc += w
        if r <= acc:
            choice = k
            break

    chain: List[Tuple[str, List[Address], Optional[Dict[str, Any]]]] = [(first_name, first_targets, None)]
    if str(choice).endswith(".SEQ"):
        chain.extend(_expand_sequence_seq(cfg, str(choice), first_name, first_targets, hook, res_view, rng))
        return chain

    # Non-SEQ single step
    base2 = str(choice).split(".")[0]
    rules = inherit.get(choice, [])
    cell = _op_celltype(cfg, first_name) if ("same_celltype" in rules) else None
    multi = (len(first_targets) > 1) if ("multi" in rules) else None
    if _feature_enabled(cfg, "uniform_subseq_sampling", True):
        name2 = _choose_op_name_for_base_uniform(cfg, base2, multi=multi, celltype=cell, rng=rng)
    else:
        name2 = _choose_op_name_for_base(cfg, base2, multi=multi, celltype=cell)
    if not name2:
        return chain
    t2 = _targets_with_inherit(rules, first_targets)
    meta2: Optional[Dict[str, Any]] = None
    try:
        inherit_hints: Dict[str, Any] = {}
        if "same_celltype" in rules and cell not in (None, "None", "NONE"):
            inherit_hints["celltype"] = str(cell)
        if inherit_hints:
            meta2 = {"inherit_hints": inherit_hints, "inherit_from": str(first_name), "inherit_rules": list(rules)}
    except Exception:
        meta2 = None
    chain.append((name2, t2, meta2))
    return chain


def _preflight_schedule(
    now: float,
    cfg: Dict[str, Any],
    res_view: ResourceView,
    ops: List[Tuple[str, List[Address], Optional[Dict[str, Any]]]],
) -> Optional[List[ProposedOp]]:
    if not ops:
        return None
    planned: List[ProposedOp] = []
    # Optional DOUT/CACHE_READ_END plane-wise split for multi-plane READ families
    try:
        if _pol_split_dout_per_plane(cfg) and len(ops) >= 2:
            name0, targets0 = ops[0]
            planes_order: List[int] = []
            seenp = set()
            for t in (targets0 or []):
                if t.plane not in seenp:
                    planes_order.append(t.plane)
                    seenp.add(t.plane)
            if len(planes_order) > 1:
                split_bases = {"DOUT", "DOUT4K", "CACHE_READ_END", "PLANE_CACHE_READ_END"}
                ops2: List[Tuple[str, List[Address]]] = [ops[0]]
                for (name_i, targets_i) in ops[1:]:
                    base_i = str(((cfg.get("op_names", {}) or {}).get(name_i, {}) or {}).get("base", ""))
                    if base_i in split_bases:
                        # split by original first plane order
                        for pl in planes_order:
                            # pick all targets for this plane (usually 1)
                            t_pl = [Address(t.die, t.plane, t.block, t.page) for t in (targets_i or []) if t.plane == pl]
                            if not t_pl and targets_i:
                                # fallback: project from first_targets item for plane with same block/page as available
                                for tt in targets_i:
                                    if tt.plane == pl:
                                        t_pl = [Address(tt.die, tt.plane, tt.block, tt.page)]
                                        break
                            if t_pl:
                                ops2.append((name_i, t_pl))
                    else:
                        ops2.append((name_i, list(targets_i or [])))
                if len(ops2) != len(ops):
                    ops = ops2
                    try:
                        _log(f"[proposer] split_dout_per_plane applied planes={planes_order} total_ops={len(ops)}")
                    except Exception:
                        pass
    except Exception:
        # Fallback to original ops on any failure
        pass
    # First op determines initial start; use feasible_at with hint=now
    name0, targets0, _meta0 = ops[0]
    op0 = _build_op(cfg, name0, targets0)
    scope0 = _base_scope(cfg, op0.base)
    t0 = res_view.feasible_at(op0, targets0, start_hint=float(now), scope=scope0)
    if t0 is None:
        return None
    planned.append(ProposedOp(op_name=name0, base=op0.base, targets=list(targets0), scope=scope0, start_us=float(t0), meta=None))
    # Chain others back-to-back with optional sequence_gap
    gap = float(_cfg_policies(cfg).get("sequence_gap", 0.0))
    tcur = float(t0) + _op_total_duration(op0)
    for (_name_i, _targets_i, _meta_i) in ops[1:]:
        name_i = _name_i
        targets_i = _targets_i
        if gap > 0:
            tcur += gap
        opi = _build_op(cfg, name_i, targets_i)
        scopei = _base_scope(cfg, opi.base)
        ti = res_view.feasible_at(opi, targets_i, start_hint=tcur, scope=scopei)
        if ti is None:
            return None
        meta_i = None
        # Pass through inherit hints to scheduler/exporter when enabled
        try:
            if _feature_enabled(cfg, "inherit_hint_propagation", True) and isinstance(_meta_i, dict):
                meta_i = dict(_meta_i)
        except Exception:
            meta_i = None
        planned.append(ProposedOp(op_name=name_i, base=opi.base, targets=list(targets_i), scope=scopei, start_us=float(ti), meta=meta_i))
        tcur = float(ti) + _op_total_duration(opi)
    return planned


def _feature_enabled(cfg: Optional[Dict[str, Any]], name: str, default: bool = True) -> bool:
    try:
        if not cfg:
            return bool(default)
        feats = (cfg.get("features", {}) or {})
        v = feats.get(name)
        if v in (None, "None"):
            return bool(default)
        return bool(v)
    except Exception:
        return bool(default)


def _parse_label_to_key(lbl: str) -> Optional[str]:
    s = str(lbl or "").strip()
    if "." in s:
        parts = s.split(".")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"{parts[0]}.{parts[1]}"
    return None


def _phase_key(cfg: Optional[Dict[str, Any]], hook: Dict[str, Any], res: ResourceView, now_us: float) -> str:
    # Hook may include die/plane and a label; prefer RM state at now
    die = int(hook.get("die", 0))
    plane = int(hook.get("plane", 0))
    st = res.op_state(die, plane, now_us)
    key: str
    if st:
        key = str(st)
    else:
        # 1) Fallback to hook label if formatted as BASE.STATE
        key = _parse_label_to_key(hook.get("label", "")) or "DEFAULT"
        # 2) If still DEFAULT and feature enabled, use RM virtual phase key
        if key == "DEFAULT" and _feature_enabled(cfg, "phase_key_rm_fallback", True):
            # Use RM's virtual END derivation when op_state is None
            rm_key: Optional[str] = None
            try:
                # mypy/runtime: ResourceManager provides this method; duck-typed here
                rm_key = getattr(res, "phase_key_at")(die, plane, float(now_us), default="DEFAULT", derive_end=True, prefer_end_on_boundary=True, exclude_issue=True)  # type: ignore[attr-defined]
            except Exception:
                rm_key = None
            if isinstance(rm_key, str) and rm_key.strip():
                key = rm_key
                try:
                    _log(
                        f"[proposer] phase_key_fallback die={die} plane={plane} now={float(now_us):.3f} chosen_key={key} reason=op_state_none"
                    )
                except Exception:
                    pass
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
    # Semantics (Option A):
    # - If key is missing: fall back to DEFAULT
    # - If key exists but is an empty mapping: DO NOT fall back (no candidates)
    sentry = object()
    dist_any = pc.get(key, sentry)
    if dist_any is sentry:
        dist = pc.get("DEFAULT", {}) or {}
    else:
        dist = dist_any if isinstance(dist_any, dict) else {}
    # coerce to name->float
    out: Dict[str, float] = {}
    try:
        for k, v in (dist or {}).items():
            out[str(k)] = float(v)
    except Exception:
        return {}
    # Apply runtime overrides as a final, idempotent correction layer
    try:
        out = _apply_phase_overrides(cfg, key, out)
    except Exception:
        # Best-effort: fall back to raw distribution on failure
        pass
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


def propose(now: float, hook: Dict[str, Any], cfg: Dict[str, Any], res_view: ResourceView, addr_sampler: AddressSampler, rng: Any) -> ProposeResult:
    """
    Top‑N greedy proposer (single-op batch, pure):
      - Reads phase-conditional distribution
      - Filters/samples targets and checks earliest feasible start within window
      - Picks earliest start among evaluated candidates with tie-break on prob

    Returns a ProposeResult that includes the selected batch (if any) and
    structured diagnostics for evaluated candidates.
    """
    attempt_records: List[AttemptRecord] = []
    last_state_block: Optional[StateBlockInfo] = None

    def _build_result(batch: Optional[ProposedBatch]) -> ProposeResult:
        diagnostics = ProposeDiagnostics(
            attempts=tuple(attempt_records),
            last_state_block=last_state_block,
        )
        return ProposeResult(batch=batch, diagnostics=diagnostics)

    # Phase distribution
    key = _phase_key(cfg, hook, res_view, now)
    dist = _phase_dist(cfg, key)
    if not dist:
        return _build_result(None)

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
        return _build_result(None)

    best: Optional[Tuple[float, ProposedOp, float, List[ProposedOp]]] = None  # (t0, first_op, prob, full_plan)
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
        try:
            prob_val = float(prob)
        except Exception:
            prob_val = float(prob or 0.0) if prob is not None else 0.0
        # Light prefilter based on ODT/SUSPEND/CACHE states
        blocked, blocked_info = _candidate_blocked_by_states(now, cfg, res_view, name, hook)
        if blocked:
            details = None
            if blocked_info:
                details = blocked_info.as_dict()
                last_state_block = blocked_info
            attempt_records.append(
                AttemptRecord(name=name, prob=prob_val, reason="state_block", details=details)
            )
            try:
                if blocked_info:
                    groups_str = ",".join(blocked_info.groups)
                    extra = []
                    if blocked_info.die is not None:
                        extra.append(f"die={blocked_info.die}")
                    if blocked_info.plane is not None:
                        extra.append(f"plane={blocked_info.plane}")
                    extra_payload = f" {' '.join(extra)}" if extra else ""
                    _log(
                        f"[proposer] try name={name} p={prob:.6f} -> state_block axis={blocked_info.axis} state={blocked_info.state} base={blocked_info.base} groups={groups_str}{extra_payload}"
                    )
                else:
                    _log(f"[proposer] try name={name} p={prob:.6f} -> state_block")
            except Exception:
                pass
            continue
        # Decide target source: (E/P/R) AddressManager sampling vs hook-provided targets for non-E/P/R
        sel_die = hook.get("die")
        base = str(((cfg.get("op_names", {}) or {}).get(name, {}) or {}).get("base"))
        targets: List[Address] = []
        used_hook_ctx = False
        if _is_addr_sampling_base(base):
            planes_hint = None
            try:
                ph = hook.get("plane_set")
                if isinstance(ph, list):
                    planes_hint = [int(x) for x in ph]
            except Exception:
                planes_hint = None
            targets = _sample_targets_for_op(
                cfg, addr_sampler, name, sel_die=(int(sel_die) if sel_die is not None else None), planes_hint=planes_hint
            )
            if not targets:
                attempt_records.append(
                    AttemptRecord(name=name, prob=prob_val, reason="sample_none")
                )
                try:
                    _log(f"[proposer] try name={name} p={prob:.6f} -> sample_none")
                except Exception:
                    pass
                continue
        else:
            # Non‑E/P/R ops: prefer hook‑provided targets; otherwise fall back to phase_key die/plane
            if _pol_hook_targets_enabled(cfg):
                targets = _targets_from_hook(hook)
            if not targets:
                # Fallback per research/2025-09-06_22-56-15_non_epr_target_selection.md:
                # use the die/plane that were used to derive phase_key
                try:
                    dies, planes = _cfg_topology(cfg)
                except Exception:
                    dies, planes = (1, 1)
                try:
                    die_val = hook.get("die")
                    die = int(die_val) if die_val is not None else 0
                except Exception:
                    die = 0
                try:
                    plane_val = hook.get("plane")
                    plane = int(plane_val) if plane_val is not None else 0
                except Exception:
                    plane = 0
                # Clamp to topology just in case
                if dies > 0:
                    die = max(0, min(die, dies - 1))
                if planes > 0:
                    plane = max(0, min(plane, planes - 1))
                targets = [Address(die=die, plane=plane, block=0, page=None)]
                try:
                    _log(f"[proposer] non‑EPR fallback targets -> [(die={die}, plane={plane}, block=0, page=None)] for base={base}")
                except Exception:
                    pass
            used_hook_ctx = True
        # Build operation and evaluate earliest feasible start
        op_stub = _build_op(cfg, name, targets)
        scope = _base_scope(cfg, op_stub.base)
        t0 = res_view.feasible_at(op_stub, targets, start_hint=float(now), scope=scope)
        if t0 is None:
            attempt_records.append(
                AttemptRecord(name=name, prob=prob_val, reason="feasible_none")
            )
            try:
                _log(f"[proposer] try name={name} p={prob:.6f} -> feasible_none")
            except Exception:
                pass
            continue
        # Admission window check unless instant reservation
        instant = _base_instant(cfg, op_stub.base)
        if (not instant) and (t0 >= (now + W)):
            attempt_records.append(
                AttemptRecord(
                    name=name,
                    prob=prob_val,
                    reason="window_exceed",
                    details={"t0": float(t0)},
                )
            )
            try:
                _log(f"[proposer] try name={name} p={prob:.6f} -> window_exceed(t0={float(t0):.3f}, now={float(now):.3f}, W={W})")
            except Exception:
                pass
            continue
        # Sequence expansion (multi-step for *.SEQ) + preflight plan (with inherit hints)
        ops_chain = _expand_sequence_chain(cfg, name, targets, hook, res_view, rng)
        planned = _preflight_schedule(now, cfg, res_view, ops_chain)
        if not planned:
            attempt_records.append(
                AttemptRecord(
                    name=name,
                    prob=prob_val,
                    reason="preflight_fail",
                    details={"t0": float(t0)},
                )
            )
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

        rec_details: Dict[str, Any] = {"t0": float(p_first.start_us)}
        if used_hook_ctx:
            rec_details["note"] = "skip_non_epr_sample"
        attempt_records.append(
            AttemptRecord(name=name, prob=prob_val, reason="ok", details=rec_details)
        )
        try:
            _log(f"[proposer] try name={name} p={prob:.6f} -> ok(t0={float(p_first.start_us):.3f})")
        except Exception:
            pass

    if best is None:
        return _build_result(None)
    # Whole-batch return (first op inside admission window already enforced)
    metrics = {
        "phase_key": key,
        "window_us": W,
        "topN": topN,
        "epsilon_greedy": eps,
        "maxtry_candidate": maxtry,
        "attempts": [rec.as_dict() for rec in attempt_records],
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
    batch = ProposedBatch(
        ops=list(best[3]),
        source="proposer.topN_greedy",
        hook=dict(hook or {}),
        metrics=metrics,
    )
    return _build_result(batch)
