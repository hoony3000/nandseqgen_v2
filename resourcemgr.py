from __future__ import annotations
from dataclasses import dataclass, field
import os
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Set, Callable
SIM_RES_US = 0.01
def quantize(t: float) -> float: return round(t / SIM_RES_US) * SIM_RES_US
class Scope(Enum): NONE=0; PLANE_SET=1; DIE_WIDE=2
@dataclass(frozen=True)
class Address: die:int; plane:int; block:int; page:Optional[int]=None
@dataclass
class Reservation: ok:bool; reason:Optional[str]; op:Any; targets:List[Address]; start_us:Optional[float]; end_us:Optional[float]
@dataclass
class ExclWindow: start:float; end:float; scope:str; die:Optional[int]; tokens:Set[str]
@dataclass
class _LatchEntry:
    kind: str  # e.g., 'LATCH_ON_READ', 'LATCH_ON_LSB', 'LATCH_ON_CSB', 'LATCH_ON_MSB'
    start_us: float
    end_us: Optional[float]


LatchKey = Tuple[int, int]
_LatchBucket = Dict[str, _LatchEntry]

READ_LATCH_KIND = "LATCH_ON_READ"
PROGRAM_LATCH_KINDS = {
    "LATCH_ON_LSB",
    "LATCH_ON_CSB",
    "LATCH_ON_MSB",
}
@dataclass
class _StateInterval: die:int; plane:int; op_base:str; state:str; start_us:float; end_us:float
class _StateTimeline:
    def __init__(self): self.by_plane:Dict[Tuple[int,int],List[_StateInterval]]={}; self._starts_by_plane:Dict[Tuple[int,int],List[float]]={}
    def _insert_plane(self,key:Tuple[int,int],seg:_StateInterval):
        lst=self.by_plane.setdefault(key,[]); starts=self._starts_by_plane.setdefault(key,[s.start_us for s in lst]); import bisect as b
        if len(starts)!=len(lst): starts[:]=[s.start_us for s in lst]
        i=b.bisect_left(starts,seg.start_us); lst.insert(i,seg); starts.insert(i,seg.start_us)
    def reserve_op(self,die:int,plane:int,op_base:str,states:List[Tuple[str,float]],start_us:float):
        t=start_us
        for (st,dur) in states: self._insert_plane((die,plane),_StateInterval(die,plane,op_base,st,t,t+float(dur))); t+=float(dur)
    def state_at(self,die:int,plane:int,t:float)->Optional[str]:
        key=(die,plane); lst=self.by_plane.get(key,[]); 
        if not lst: return None
        starts=self._starts_by_plane.get(key); 
        if starts is None or len(starts)!=len(lst): starts=[s.start_us for s in lst]; self._starts_by_plane[key]=starts
        import bisect as b; i=b.bisect_right(starts,t)-1
        if 0<=i<len(lst): s=lst[i]; 
        else: return None
        return f"{s.op_base}.{s.state}" if (s.start_us<=t<s.end_us) else None
    def overlaps_plane(self,die:int,plane:int,start:float,end:float,pred=None)->bool:
        key=(die,plane); lst=self.by_plane.get(key,[]); 
        if not lst: return False
        starts=self._starts_by_plane.get(key); 
        if starts is None or len(starts)!=len(lst): starts=[s.start_us for s in lst]; self._starts_by_plane[key]=starts
        import bisect as b; idx=b.bisect_left(starts,end); i=max(0,idx-1)
        while i<len(lst) and lst[i].start_us<end:
            seg=lst[i]
            if seg.start_us<end and start<seg.end_us and (pred is None or pred(seg)): return True
            i+=1
        return False
    def truncate_after(self, die:int, plane:int, at_us:float, pred=None) -> None:
        """Truncate or remove segments at/after at_us that match pred.

        - If a matching segment straddles at_us, set its end_us to at_us.
        - Remove any subsequent matching segments with start_us >= at_us.
        - Non‑matching segments are left untouched.
        """
        key=(die,plane); lst=self.by_plane.get(key,[])
        if not lst: return
        t=float(at_us)
        starts=self._starts_by_plane.get(key)
        if starts is None or len(starts)!=len(lst):
            starts=[s.start_us for s in lst]; self._starts_by_plane[key]=starts
        import bisect as b
        j=b.bisect_left(starts,t) if starts else 0
        i=j-1
        # Adjust straddling segment strictly before t
        if 0<=i<len(lst):
            seg=lst[i]
            if seg.start_us < t < seg.end_us and (pred is None or pred(seg)):
                seg.end_us = t
        # Remove following matching segments that start at or after t
        k=j
        while k < len(lst):
            seg=lst[k]
            if seg.start_us >= t and (pred is None or pred(seg)):
                k+=1
            else:
                break
        # delete lst[j:k]
        if j < k:
            del lst[j:k]
        # rebuild starts
        self._starts_by_plane[key]=[s.start_us for s in lst]
@dataclass
class _Txn:
    now_us: float
    plane_resv: Dict[Tuple[int, int], List[Tuple[float, float]]] = field(default_factory=dict)
    bus_resv: List[Tuple[float, float]] = field(default_factory=list)
    excl_global: List[ExclWindow] = field(default_factory=list)
    excl_die: Dict[int, List[ExclWindow]] = field(default_factory=dict)
    latch_locks: Dict[LatchKey, _LatchBucket] = field(default_factory=dict)
    st_ops: List[Tuple[int, int, str, List[Tuple[str, float]], float]] = field(default_factory=list)
    # EPR overlay: (die, block) -> overrides
    addr_overlay: Dict[Tuple[int, int], Dict[str, Any]] = field(default_factory=dict)

@dataclass
class _CacheEntry:
    die: int
    plane: Optional[int]
    kind: str  # 'ON_CACHE_READ' | 'ON_CACHE_PROGRAM' | 'ON_ONESHOT_CACHE_PROGRAM'
    start_us: float
    end_us: Optional[float] = None
    celltype: Optional[str] = None

@dataclass
class _SuspState:
    die: int
    state: str  # legacy single-axis snapshot entry (kept for compat)
    start_us: float
    end_us: Optional[float] = None

@dataclass
class _AxisState:
    die: int
    state: str  # 'ERASE_SUSPENDED' | 'PROGRAM_SUSPENDED'
    start_us: float
    end_us: Optional[float] = None

@dataclass
class _OpMeta:
    die: int
    op_id: Optional[int]
    op_name: Optional[str]
    base: str
    targets: List[Address]
    start_us: float
    end_us: float
    remaining_us: Optional[float] = None
    suspend_time_us: Optional[float] = None
    axis: Optional[str] = None

class ResourceManager:
    def __init__(self, cfg: Optional[Dict[str, Any]] = None, dies: int = 1, planes: int = 1):
        self.cfg = cfg or {}
        self.dies = int(dies)
        self.planes = int(planes)
        self._avail: Dict[Tuple[int, int], float] = {(d, p): 0.0 for d in range(self.dies) for p in range(self.planes)}
        self._plane_resv: Dict[Tuple[int, int], List[Tuple[float, float]]] = {(d, p): [] for d in range(self.dies) for p in range(self.planes)}
        self._bus_resv: List[Tuple[float, float]] = []
        self._excl_global: List[ExclWindow] = []
        self._excl_die: Dict[int, List[ExclWindow]] = {}
        self._latch: Dict[LatchKey, _LatchBucket] = {}
        self._st = _StateTimeline()
        # runtime states for Proposer/Validator (PRD §5.4/5.5)
        self._odt_disabled: bool = False
        self._cache_read: Dict[Tuple[int, int], _CacheEntry] = {}
        self._cache_program: Dict[int, _CacheEntry] = {}
        # Legacy single-axis field kept for backward-compatible API only
        self._suspend_states: Dict[int, Optional[_SuspState]] = {d: None for d in range(self.dies)}
        # Split suspend axes: ERASE and PROGRAM
        self._erase_susp: Dict[int, Optional[_AxisState]] = {d: None for d in range(self.dies)}
        self._pgm_susp: Dict[int, Optional[_AxisState]] = {d: None for d in range(self.dies)}
        self._ongoing_ops: Dict[int, List[_OpMeta]] = {d: [] for d in range(self.dies)}
        # Axis-specific suspended op stacks
        self._suspended_ops_erase: Dict[int, List[_OpMeta]] = {d: [] for d in range(self.dies)}
        self._suspended_ops_program: Dict[int, List[_OpMeta]] = {d: [] for d in range(self.dies)}
        # Backward-compat container (legacy snapshots); not used for new writes
        self._suspended_ops: Dict[int, List[_OpMeta]] = {d: [] for d in range(self.dies)}
        # --- Validator integration (skeleton, gated by config) ---
        # External address-dependent policy callback (e.g., AddressManager.check_epr)
        self.addr_policy: Optional[Callable[..., Any]] = None
        # Last validation snapshot for debugging/observability
        self._last_validation: Optional[Dict[str, Any]] = None
        # exclusion window token semantics (die-level overlap control)
        self._EXCL_TOKEN_SINGLE = "SINGLE"
        self._EXCL_TOKEN_MULTI = "MULTI"
        # allowed bases for single×single overlap (PRD_v2.md:307-309)
        # overrideable via cfg['constraints']['single_single_allowed_bases']
        default_allowed = {"PLANE_READ", "PLANE_READ4K", "PLANE_CACHE_READ"}
        try:
            self._ALLOWED_SINGLE_SINGLE_BASES: Set[str] = set(
                (self.cfg.get("constraints", {}) or {}).get("single_single_allowed_bases", default_allowed)
            ) or set(default_allowed)
        except Exception:
            self._ALLOWED_SINGLE_SINGLE_BASES = set(default_allowed)
        self._program_base_whitelist: Set[str] = self._load_program_base_whitelist()

    def _txn_record_latch(self, txn: _Txn, die: int, plane: int, entry: _LatchEntry) -> None:
        key = (int(die), int(plane))
        bucket = txn.latch_locks.setdefault(key, {})
        bucket[entry.kind] = entry

    def _set_latch_entry(self, die: int, plane: int, entry: _LatchEntry) -> None:
        key = (int(die), int(plane))
        bucket = self._latch.setdefault(key, {})
        bucket[entry.kind] = entry

    def _remove_latch_entry(self, die: int, plane: int, kind: str) -> None:
        key = (int(die), int(plane))
        bucket = self._latch.get(key)
        if not bucket:
            return
        bucket.pop(kind, None)
        if not bucket:
            self._latch.pop(key, None)

    def _active_latches(self, die: int, plane: int, at_us: float) -> List[_LatchEntry]:
        bucket = self._latch.get((int(die), int(plane)))
        if not bucket:
            return []
        active: List[_LatchEntry] = []
        for entry in bucket.values():
            if self._latch_entry_active(entry, at_us):
                active.append(entry)
        return active

    @staticmethod
    def _latch_entry_active(entry: _LatchEntry, t0: float) -> bool:
        if t0 < entry.start_us:
            return False
        if entry.end_us is None:
            return True
        return t0 < entry.end_us

    def _affects_state(self, base: str) -> bool:
        """Return True when cfg marks this base as affecting op_state timeline.

        Safe lookup with conservative default True when unspecified/malformed.
        """
        try:
            cfg = self.cfg or {}
            op_bases = (cfg.get("op_bases", {}) or {})
            b = str(base)
            ent = (op_bases.get(b, {}) or {})
            return bool(ent.get("affect_state", True))
        except Exception:
            return True

    def _load_program_base_whitelist(self) -> Set[str]:
        cfg = self.cfg or {}
        try:
            raw = cfg.get("program_base_whitelist", [])
        except Exception:
            raw = []
        return {str(item).upper() for item in (raw or []) if str(item).strip()}

    # --- instant reservation helpers (bus-only immediate scheduling) ---
    def _base_instant(self, base: str) -> bool:
        """Return True when cfg marks this base as instant-reservable.

        Semantics: proposer may bypass admission window; RM additionally allows
        bus-only immediate reservation at now (or start_hint for feasibility),
        skipping plane/die exclusivity and plane reservation windows.
        """
        try:
            return bool(((self.cfg.get("op_bases", {}) or {}).get(base, {}) or {}).get("instant_resv", False))
        except Exception:
            return False

    def _instant_scope_ok(self, scope: Scope, base: str) -> bool:
        """Optional policy guard to restrict instant path by scope.

        If cfg['policies']['instant_bus_only_scope_none'] is true, allow only
        scope == Scope.NONE; otherwise allow all scopes declared instant.
        """
        try:
            pol = (self.cfg.get("policies", {}) or {})
            guard = bool(pol.get("instant_bus_only_scope_none", False))
        except Exception:
            guard = False
        return (scope == Scope.NONE) if guard else True

    def _op_base(self, op: Any) -> str:
        try:
            return str(op.base.name)
        except Exception:
            try:
                return str(op.base)
            except Exception:
                return str(getattr(op, "name", "OP"))

    def _bus_segments(self, op: Any) -> List[Tuple[float, float]]:
        segs: List[Tuple[float, float]] = []
        t = 0.0
        for s in getattr(op, "states", []) or []:
            dur = float(getattr(s, "dur_us", 0.0))
            if bool(getattr(s, "bus", False)):
                segs.append((t, t + dur))
            t += dur
        return segs

    def _total_duration(self, op: Any) -> float:
        return sum(float(getattr(s, "dur_us", 0.0)) for s in getattr(op, "states", []) or [])

    def _op_name(self, op: Any) -> Optional[str]:
        try:
            n = getattr(op, "name", None)
            return str(n) if n is not None else None
        except Exception:
            return None

    def _cfg_multi_of_op(self, op: Any) -> Optional[bool]:
        """Derive multiplicity from configuration.

        Priority:
        1) cfg['op_specs'][op_name]['multi'] if available
        2) cfg['op_names'][op_name]['multi'] as fallback
        Returns None if not found so caller can fallback to structural logic.
        """
        name = self._op_name(op)
        if not name:
            return None
        cfg = self.cfg or {}
        try:
            spec = (cfg.get("op_specs", {}) or {}).get(name)
            if spec is not None and "multi" in spec:
                return bool(spec["multi"])  # type: ignore[index]
        except Exception:
            pass
        try:
            spec = (cfg.get("op_names", {}) or {}).get(name)
            if spec is not None and "multi" in spec:
                return bool(spec["multi"])  # type: ignore[index]
        except Exception:
            pass
        return None

    def _earliest_planescope(self, die: int, scope: Scope, plane_set: Optional[List[int]]) -> float:
        if scope == Scope.DIE_WIDE:
            planes = list(range(self.planes))
        elif scope == Scope.PLANE_SET and plane_set:
            planes = list(plane_set)
        else:
            planes = [plane_set[0]] if plane_set else [0]
        return max(self._avail[(die, p)] for p in planes)

    def _planescope_ok(
        self,
        die: int,
        scope: Scope,
        plane_set: List[int],
        start: float,
        end: float,
        pending: Optional[Dict[Tuple[int, int], List[Tuple[float, float]]]] = None,
    ) -> bool:
        """Check plane reservation overlap including committed and pending windows.

        pending: current-transaction plane windows (keyed by (die, plane)).
        """
        planes = list(range(self.planes)) if scope == Scope.DIE_WIDE else plane_set
        for p in planes:
            # committed windows
            for (s, e) in self._plane_resv[(die, p)]:
                if not (end <= s or e <= start):
                    return False
            # pending windows in this txn
            if pending:
                for (s, e) in pending.get((die, p), []) or []:
                    if not (end <= s or e <= start):
                        return False
        return True

    def _bus_ok(self, op: Any, start: float, pending: Optional[List[Tuple[float, float]]] = None) -> bool:
        """Check bus segment overlap including committed and pending windows."""
        for (off0, off1) in self._bus_segments(op):
            a0, a1 = quantize(start + off0), quantize(start + off1)
            # committed bus windows
            for (s, e) in self._bus_resv:
                if not (a1 <= s or e <= a0):
                    return False
            # pending bus windows in this txn
            if pending:
                for (s, e) in pending:
                    if not (a1 <= s or e <= a0):
                        return False
        return True

    # --- single/multi exclusion and single×single allowance (die-level) ---
    def _multiplicity_kind(self, op: Any, scope: Scope, plane_set: List[int]) -> str:
        # Prefer config multiplicity per PRD (CFG[op_specs][op_name][multi])
        cfg_multi = self._cfg_multi_of_op(op)
        if cfg_multi is True:
            return self._EXCL_TOKEN_MULTI
        if cfg_multi is False:
            return self._EXCL_TOKEN_SINGLE
        # Fallback to structural inference by scope/plane_set
        if scope == Scope.DIE_WIDE:
            return self._EXCL_TOKEN_MULTI
        return self._EXCL_TOKEN_MULTI if len(set(plane_set)) > 1 else self._EXCL_TOKEN_SINGLE

    def _single_multi_violation(
        self,
        die: int,
        start: float,
        end: float,
        kind: str,
        base: str,
        pending: Optional[List[ExclWindow]] = None,
    ) -> bool:
        def overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
            return not (a1 <= b0 or b1 <= a0)

        def _extract_base_from_tokens(tokens: Set[str]) -> Optional[str]:
            # We deliberately use OPBASE: prefix to avoid interfering with legacy BASE: tokens
            for tok in tokens:
                if tok.startswith("OPBASE:"):
                    return tok.split(":", 1)[1]
            return None

        def _conflict_by_kind(self_kind: str, other_kind: str, self_base: str, other_base: Optional[str]) -> bool:
            # Enforce PRD rules:
            # - single×multi -> conflict
            # - multi×multi -> conflict
            # - single×single -> allowed only if both bases are in allowed set
            if self_kind == self._EXCL_TOKEN_MULTI and other_kind == self._EXCL_TOKEN_MULTI:
                return True
            if (self_kind == self._EXCL_TOKEN_MULTI and other_kind == self._EXCL_TOKEN_SINGLE) or (
                self_kind == self._EXCL_TOKEN_SINGLE and other_kind == self._EXCL_TOKEN_MULTI
            ):
                return True
            if self_kind == self._EXCL_TOKEN_SINGLE and other_kind == self._EXCL_TOKEN_SINGLE:
                # both must be allowed read-plane bases
                return not (
                    (self_base in self._ALLOWED_SINGLE_SINGLE_BASES)
                    and (other_base in self._ALLOWED_SINGLE_SINGLE_BASES)
                )
            return False
        # committed windows
        for w in self._excl_die.get(die, []):
            if overlap(start, end, w.start, w.end):
                other_kind = self._EXCL_TOKEN_MULTI if (self._EXCL_TOKEN_MULTI in w.tokens) else self._EXCL_TOKEN_SINGLE
                if _conflict_by_kind(kind, other_kind, base, _extract_base_from_tokens(w.tokens)):
                    return True
        # include pending windows in current txn, if any
        if pending:
            for w in pending:
                if w.die != die:
                    continue
                if overlap(start, end, w.start, w.end):
                    other_kind = self._EXCL_TOKEN_MULTI if (self._EXCL_TOKEN_MULTI in w.tokens) else self._EXCL_TOKEN_SINGLE
                    if _conflict_by_kind(kind, other_kind, base, _extract_base_from_tokens(w.tokens)):
                        return True
        return False

    def _is_locked_at(self, die: int, plane: int, t0: float) -> bool:
        return bool(self._active_latches(die, plane, float(t0)))

    def _latch_ok(self, op: Any, targets: List[Address], start: float, scope: Scope) -> bool:
        """Validate operation against active latch states using config exclusions.

        Behavior:
        - Look up active latch kinds at t=start for relevant (die,plane) scopes.
        - Map latch kinds -> exclusion group names via CFG['exclusions_by_latch_state'].
        - Block if op.base belongs to any base listed in CFG['exclusion_groups'][group].
        """
        base = self._op_base(op)
        cfg = self.cfg or {}
        groups_by_latch: Dict[str, List[str]] = (cfg.get("exclusions_by_latch_state") or {})
        group_defs: Dict[str, List[str]] = (cfg.get("exclusion_groups") or {})

        def blocked_by_latch(die: int, plane: int) -> bool:
            active = self._active_latches(die, plane, start)
            if not active:
                return False
            for entry in active:
                groups = groups_by_latch.get(entry.kind, [])
                for g in groups:
                    bases = group_defs.get(g, [])
                    if base in bases:
                        return True
            return False

        die = targets[0].die
        if scope == Scope.DIE_WIDE:
            for p in range(self.planes):
                if blocked_by_latch(die, p):
                    return False
            return True
        for t in targets:
            if blocked_by_latch(t.die, t.plane):
                return False
        return True

    def _excl_ok(self, die: int, start: float, end: float, base: str) -> bool:
        def blocks(w: ExclWindow) -> bool:
            for tok in w.tokens:
                if tok == "ANY":
                    return True
                if tok.startswith("BASE:") and base == tok.split(":", 1)[1]:
                    return True
            return False
        for w in self._excl_global:
            if not (end <= w.start or w.end <= start) and blocks(w):
                return False
        for w in self._excl_die.get(die, []):
            if not (end <= w.start or w.end <= start) and blocks(w):
                return False
        return True

    def begin(self, now_us: float) -> _Txn:
        return _Txn(now_us=quantize(now_us))

    def feasible_at(self, op: Any, targets: List[Address], start_hint: float, scope: Scope = Scope.PLANE_SET) -> Optional[float]:
        die = targets[0].die
        plane_set = [t.plane for t in targets]
        base = self._op_base(op)
        # instant path: bus-only validation, plane/die exclusivity bypass
        if self._base_instant(base) and self._instant_scope_ok(scope, base):
            t0 = quantize(start_hint)
            end = quantize(t0 + self._total_duration(op))
            if not self._bus_ok(op, t0):
                return None
            if not self._latch_ok(op, targets, t0, scope):
                return None
            ok, _reason = self._eval_rules(stage="feasible", op=op, targets=targets, scope=scope, start=t0, end=end, txn=None)
            if not ok:
                return None
            return t0
        # normal path
        t0 = quantize(max(start_hint, self._earliest_planescope(die, scope, plane_set)))
        end = quantize(t0 + self._total_duration(op))
        if not self._planescope_ok(die, scope, plane_set, t0, end):
            return None
        if not self._bus_ok(op, t0):
            return None
        # die-level overlap exclusion per PRD: block single×multi and multi×multi; allow single×single only for specific bases
        kind = self._multiplicity_kind(op, scope, plane_set)
        if self._single_multi_violation(die, t0, end, kind, base):
            return None
        # legacy/base-token exclusions (kept for compatibility; no-op by default)
        if not self._excl_ok(die, t0, end, base):
            return None
        if not self._latch_ok(op, targets, t0, scope):
            return None
        # Optional rule evaluation (no-op by default; feature-flagged)
        ok, _reason = self._eval_rules(stage="feasible", op=op, targets=targets, scope=scope, start=t0, end=end, txn=None)
        if not ok:
            return None
        return t0

    def reserve(self, txn: _Txn, op: Any, targets: List[Address], scope: Scope, duration_us: Optional[float] = None) -> Reservation:
        die = targets[0].die
        plane_set = [t.plane for t in targets]
        dur = float(duration_us) if duration_us is not None else self._total_duration(op)
        base = self._op_base(op)
        # instant path: schedule at txn.now_us with bus/latch/rules only; skip plane/die windows
        if self._base_instant(base) and self._instant_scope_ok(scope, base):
            # minimal serialization guard within txn: avoid overlapping pending bus windows
            last_bus_end = max((e for (_s, e) in (txn.bus_resv or [])), default=0.0)
            start = quantize(max(txn.now_us, last_bus_end))
            end = quantize(start + dur)
            if not self._bus_ok(op, start, pending=txn.bus_resv):
                return Reservation(False, "bus", op, targets, None, None)
            if not self._latch_ok(op, targets, start, scope):
                return Reservation(False, "latch", op, targets, None, None)
            ok, reason = self._eval_rules(stage="reserve", op=op, targets=targets, scope=scope, start=start, end=end, txn=txn)
            if not ok:
                return Reservation(False, (reason or "rules"), op, targets, None, None)
            # Only reserve bus segments and state timeline; do NOT create plane or die exclusivity windows
            for (off0, off1) in self._bus_segments(op):
                txn.bus_resv.append((quantize(start + off0), quantize(start + off1)))
            # Latch transitions (same semantics as normal path)
            latch_kind = self._latch_kind_for_base(base)
            if latch_kind:
                if base in ("READ", "READ4K", "PLANE_READ", "PLANE_READ4K", "CACHE_READ", "PLANE_CACHE_READ", "COPYBACK_READ"):
                    for t in targets:
                        self._txn_record_latch(
                            txn,
                            t.die,
                            t.plane,
                            _LatchEntry(kind=latch_kind, start_us=end, end_us=None),
                        )
                elif base in ("ONESHOT_PROGRAM_LSB", "ONESHOT_PROGRAM_CSB", "ONESHOT_PROGRAM_MSB"):
                    for p in range(self.planes):
                        self._txn_record_latch(
                            txn,
                            die,
                            p,
                            _LatchEntry(kind=latch_kind, start_us=end, end_us=None),
                        )
            st_list = [(getattr(s, "name", "STATE"), float(getattr(s, "dur_us", 0.0))) for s in getattr(op, "states", [])]
            for t in targets:
                txn.st_ops.append((t.die, t.plane, base, st_list, start))
            self._update_overlay_for_reserved(txn, base, targets)
            return Reservation(True, None, op, targets, start, end)
        # normal path
        start = quantize(max(txn.now_us, self._earliest_planescope(die, scope, plane_set)))
        end = quantize(start + dur)
        if not self._planescope_ok(die, scope, plane_set, start, end, pending=txn.plane_resv):
            return Reservation(False, "planescope", op, targets, None, None)
        if not self._bus_ok(op, start, pending=txn.bus_resv):
            return Reservation(False, "bus", op, targets, None, None)
        # die-level single×multi, multi×multi exclusion including pending windows in txn
        kind = self._multiplicity_kind(op, scope, plane_set)
        pending_wins = txn.excl_die.get(die, [])
        if self._single_multi_violation(die, start, end, kind, base, pending=pending_wins):
            return Reservation(False, "exclusion_multi", op, targets, None, None)
        # legacy/base-token exclusions (kept for compatibility; currently unused)
        if not self._excl_ok(die, start, end, base):
            return Reservation(False, "exclusion", op, targets, None, None)
        if not self._latch_ok(op, targets, start, scope):
            return Reservation(False, "latch", op, targets, None, None)
        # Optional rule evaluation (no-op by default; feature-flagged)
        ok, reason = self._eval_rules(stage="reserve", op=op, targets=targets, scope=scope, start=start, end=end, txn=txn)
        if not ok:
            return Reservation(False, (reason or "rules"), op, targets, None, None)
        planes = list(range(self.planes)) if scope == Scope.DIE_WIDE else plane_set
        for p in planes:
            txn.plane_resv.setdefault((die, p), []).append((start, end))
        for (off0, off1) in self._bus_segments(op):
            txn.bus_resv.append((quantize(start + off0), quantize(start + off1)))
        # record die-level multiplicity window with op base for later conflict checks
        # We use OPBASE: prefix to avoid interfering with legacy _excl_ok BASE: tokens
        win = ExclWindow(start=start, end=end, scope="DIE", die=die, tokens={kind, f"OPBASE:{base}"})
        txn.excl_die.setdefault(die, []).append(win)
        # Latch transitions: apply per research + PRD
        latch_kind = self._latch_kind_for_base(base)
        if latch_kind:
            if base in ("READ", "READ4K", "PLANE_READ", "PLANE_READ4K", "CACHE_READ", "PLANE_CACHE_READ", "COPYBACK_READ"):
                # plane-scoped read latch on target planes
                for t in targets:
                    self._txn_record_latch(
                        txn,
                        t.die,
                        t.plane,
                        _LatchEntry(kind=latch_kind, start_us=end, end_us=None),
                    )
            elif base in ("ONESHOT_PROGRAM_LSB", "ONESHOT_PROGRAM_CSB", "ONESHOT_PROGRAM_MSB"):
                # die-wide program latch applied to all planes in die as plane-scoped entries
                for p in range(self.planes):
                    self._txn_record_latch(
                        txn,
                        die,
                        p,
                        _LatchEntry(kind=latch_kind, start_us=end, end_us=None),
                    )
        st_list = [(getattr(s, "name", "STATE"), float(getattr(s, "dur_us", 0.0))) for s in getattr(op, "states", [])]
        for t in targets:
            txn.st_ops.append((t.die, t.plane, base, st_list, start))
        # Update EPR overlay with effects of this reservation for subsequent ops in the same txn
        self._update_overlay_for_reserved(txn, base, targets)
        return Reservation(True, None, op, targets, start, end)

    def commit(self, txn: _Txn) -> None:
        # Track processed SUSPEND handling per die to avoid duplicate work when txn includes
        # multiple st_ops entries referencing the same die/operation.
        _susp_processed: Set[Tuple[str, int]] = set()
        for (key, lst) in txn.plane_resv.items():
            self._plane_resv[key].extend(lst)
            if lst:
                last_end = max(e for (_, e) in lst)
                if self._avail[key] < last_end:
                    self._avail[key] = last_end
        self._bus_resv.extend(txn.bus_resv)
        self._excl_global.extend(txn.excl_global)
        for d, lst in txn.excl_die.items():
            self._excl_die.setdefault(d, []).extend(lst)
        for key, bucket in txn.latch_locks.items():
            if not bucket:
                continue
            dest = self._latch.setdefault(key, {})
            for kind, entry in bucket.items():
                dest[kind] = entry
        for (die, plane, base, st_list, start) in txn.st_ops:
            # PRD v2 §5.4: skip op_state timeline segments for affect_state == false
            if self._affects_state(base):
                if st_list:
                    self._st.reserve_op(die, plane, base, st_list, start)
            # Opportunistic minimal state hooks for ODT/CACHE/SUSPEND bookkeeping
            end = quantize(start + sum(float(d) for (_, d) in st_list))
            b = str(base).upper()
            # ODT
            if b == "ODTDISABLE":
                self._odt_disabled = True
            elif b == "ODTENABLE":
                self._odt_disabled = False
            # CACHE_READ (plane-scoped) and *_END cleanups
            if b in ("CACHE_READ", "PLANE_CACHE_READ"):
                self._cache_read[(die, plane)] = _CacheEntry(die=die, plane=plane, kind="ON_CACHE_READ", start_us=start)
            elif b in ("CACHE_READ_END", "PLANE_CACHE_READ_END"):
                ent = self._cache_read.get((die, plane))
                if ent and ent.end_us is None:
                    ent.end_us = end
            # CACHE_PROGRAM (die-scoped)
            if b == "CACHE_PROGRAM_SLC":
                self._cache_program[die] = _CacheEntry(die=die, plane=None, kind="ON_CACHE_PROGRAM", start_us=start)
            elif b == "ONESHOT_CACHE_PROGRAM":
                self._cache_program[die] = _CacheEntry(die=die, plane=None, kind="ON_ONESHOT_CACHE_PROGRAM", start_us=start)
            elif b in ("PROGRAM_SLC",):
                # PROGRAM can end an active cache_program on this die
                ent = self._cache_program.get(die)
                if ent and ent.end_us is None:
                    ent.end_us = end
            elif b in ("ONESHOT_PROGRAM_MSB_23H", "ONESHOT_PROGRAM_EXEC_MSB"):
                # One‑shot cache program should conclude by MSB EXEC completion as well
                ent = self._cache_program.get(die)
                if ent and ent.end_us is None:
                    ent.end_us = end
            # SUSPEND bookkeeping (split axes) + timeline truncation + meta move
            if b in ("ERASE_SUSPEND", "PROGRAM_SUSPEND"):
                fam = "ERASE" if b == "ERASE_SUSPEND" else "PROGRAM"
                # Open axis state at suspend time
                if b == "ERASE_SUSPEND":
                    self._erase_susp[die] = _AxisState(die=die, state="ERASE_SUSPENDED", start_us=start)
                else:
                    self._pgm_susp[die] = _AxisState(die=die, state="PROGRAM_SUSPENDED", start_us=start)
                # Guard: handle once per die per suspend base within this commit
                key_s = (b, int(die))
                if key_s not in _susp_processed:
                    _susp_processed.add(key_s)
                    # Move latest ongoing op of this die to the matching-axis suspended and compute remaining
                    try:
                        self.move_to_suspended_axis(int(die), op_id=None, now_us=float(start), axis=str(fam))
                    except Exception:
                        # best-effort; continue even if no ongoing meta
                        pass
                    # Determine planes to truncate based on just-moved meta when available
                    planes_to_cut: List[int]
                    try:
                        if fam == "ERASE":
                            meta_list = self._suspended_ops_erase.get(int(die), [])
                        else:
                            meta_list = self._suspended_ops_program.get(int(die), [])
                        meta = meta_list[-1] if meta_list else None
                        if meta and meta.targets:
                            planes_to_cut = sorted({int(t.plane) for t in meta.targets})
                        else:
                            planes_to_cut = list(range(self.planes))
                    except Exception:
                        planes_to_cut = list(range(self.planes))
                    # Predicate: cut only CORE_BUSY segments of the target family
                    def _pred(seg: _StateInterval) -> bool:
                        try:
                            return (seg.state == "CORE_BUSY") and (fam in str(seg.op_base)) and ("SUSPEND" not in str(seg.op_base)) and ("RESUME" not in str(seg.op_base))
                        except Exception:
                            return False
                    for p in planes_to_cut:
                        try:
                            self._st.truncate_after(int(die), int(p), float(start), pred=_pred)
                        except Exception:
                            # continue per-plane to avoid partial failure
                            continue
            elif b == "ERASE_RESUME":
                st_e = self._erase_susp.get(die)
                if st_e and st_e.end_us is None:
                    st_e.end_us = end
                self._erase_susp[die] = None
            elif b == "PROGRAM_RESUME":
                st_p = self._pgm_susp.get(die)
                if st_p and st_p.end_us is None:
                    st_p.end_us = end
                self._pgm_susp[die] = None

    def rollback(self, txn: _Txn) -> None:
        return

    def release_on_dout_end(self, targets: List[Address], now_us: float) -> None:
        for t in targets:
            self._remove_latch_entry(t.die, t.plane, READ_LATCH_KIND)

    def release_on_exec_msb_end(self, die: int, now_us: float) -> None:
        """Release program-related latches after ONESHOT_PROGRAM_MSB_23H or ONESHOT_PROGRAM_EXEC_MSB completion for a die."""
        for p in range(self.planes):
            for kind in PROGRAM_LATCH_KINDS:
                self._remove_latch_entry(die, p, kind)

    def op_state(self, die: int, plane: int, at_us: float) -> Optional[str]:
        return self._st.state_at(die, plane, quantize(at_us))

    def phase_key_at(
        self,
        die: int,
        plane: int,
        t: float,
        default: str = "DEFAULT",
        derive_end: bool = True,
        prefer_end_on_boundary: bool = True,
        exclude_issue: bool = True,
    ) -> str:
        """Return phase key at time t for (die,plane).

        Behavior:
        - Inside a state segment: return "BASE.STATE" (same as op_state).
        - If no segment covers t and derive_end=True: return "<LAST_BASE>.END" when a prior
          segment exists with end_us <= t; otherwise return default.
        - If a segment starts exactly at t and prefer_end_on_boundary=True: prefer previous
          segment's "<BASE>.END" when it exists; otherwise fall back to the covering segment.
        """
        tq = quantize(float(t))
        key = (int(die), int(plane))
        lst = self._st.by_plane.get(key, [])
        starts = self._st._starts_by_plane.get(key)
        if starts is None or len(starts or []) != len(lst):
            starts = [s.start_us for s in lst]
            self._st._starts_by_plane[key] = starts

        # Fast path: current covering state
        st = self._st.state_at(die, plane, tq)
        if st is not None and str(st).strip() != "":
            # Optionally remap ISSUE -> previous.END for analysis-only consumers
            if exclude_issue and str(st).endswith(".ISSUE") and lst:
                import bisect as _b
                j = _b.bisect_right(starts, tq) - 1 if starts else -1
                if 0 <= j < len(lst):
                    cur = lst[j]
                    if str(cur.state).upper() == "ISSUE":
                        if j - 1 >= 0:
                            prev = lst[j - 1]
                            if float(prev.end_us) <= tq:
                                return f"{prev.op_base}.END"
                        # No previous segment; fall through to default/derive_end path below
                        if not derive_end:
                            return str(default)
                        # try virtual end based on prior segment even if inside first ISSUE
                        return str(default)
            if prefer_end_on_boundary and lst:
                # Detect exact boundary: a segment whose start == t exists
                import bisect as _b
                j = _b.bisect_right(starts, tq) - 1 if starts else -1
                if 0 <= j < len(lst) and abs(float(lst[j].start_us) - tq) <= 0.0:
                    # If this is exactly the start of segment j, prefer previous END when present
                    if j - 1 >= 0:
                        prev = lst[j - 1]
                        # Only prefer END if previous segment truly ended at or before t
                        if float(prev.end_us) <= tq:
                            return f"{prev.op_base}.END"
                    # No previous segment; fall back to current state
            return str(st)

        if not derive_end:
            return str(default)
        if not lst:
            return str(default)
        import bisect as _b
        i = _b.bisect_right(starts, tq) - 1 if starts else -1
        if 0 <= i < len(lst):
            seg = lst[i]
            # If t is at or after this segment's end and no segment covers t, treat as virtual END
            if tq >= float(seg.end_us):
                return f"{seg.op_base}.END"
        return str(default)

    def has_overlap(self, scope: Scope, die: int, plane_set: Optional[List[int]], start_us: float, end_us: float, pred: Optional[Callable[[_StateInterval], bool]] = None) -> bool:
        t0, t1 = quantize(start_us), quantize(end_us)
        if scope == Scope.DIE_WIDE:
            for p in range(self.planes):
                if self._st.overlaps_plane(die, p, t0, t1, pred=pred):
                    return True
            return False
        if scope == Scope.PLANE_SET and plane_set:
            for p in plane_set:
                if self._st.overlaps_plane(die, p, t0, t1, pred=pred):
                    return True
            return False
        p = (plane_set[0] if plane_set else 0)
        return self._st.overlaps_plane(die, p, t0, t1, pred=pred)

    def latch_state(self, die: int, plane: int, at_us: float) -> bool:
        return self._is_locked_at(die, plane, quantize(at_us))

    def exclusions(self, scope: str = "GLOBAL", die: Optional[int] = None) -> List[ExclWindow]:
        if str(scope).upper() == "GLOBAL":
            return list(self._excl_global)
        if die is None:
            return []
        return list(self._excl_die.get(int(die), []))

    # --- Proposer-facing state queries (PRD §5.4) ---
    def odt_state(self) -> Optional[str]:
        return "ODT_DISABLE" if self._odt_disabled else None

    def set_odt_disable(self) -> None:
        self._odt_disabled = True

    def set_odt_enable(self) -> None:
        self._odt_disabled = False

    def cache_state(self, die: int, plane: int, at_us: Optional[float] = None) -> Optional[str]:
        """Return active cache state per PRD semantics.

        Rules:
        - Die-level program cache has priority over plane-level read cache.
        - "End" takes effect when the END operation completes, i.e., at END's end time.
        - If at_us is None, treat only entries with end_us is None as active.
        - If at_us is provided, active when start_us <= at_us < end_us (or end_us is None).
        """
        # die-level program cache first
        ent_d = self._cache_program.get(die)
        if ent_d:
            if at_us is None:
                if ent_d.end_us is None:
                    return ent_d.kind
            else:
                t = quantize(at_us)
                if ent_d.start_us <= t and (ent_d.end_us is None or t < ent_d.end_us):
                    return ent_d.kind
        # plane-level read cache
        ent_p = self._cache_read.get((die, plane))
        if ent_p:
            if at_us is None:
                if ent_p.end_us is None:
                    return ent_p.kind
            else:
                t = quantize(at_us)
                if ent_p.start_us <= t and (ent_p.end_us is None or t < ent_p.end_us):
                    return ent_p.kind
        return None

    def begin_cache_read(self, die: int, plane: int, start_us: float, celltype: Optional[str] = None) -> None:
        self._cache_read[(die, plane)] = _CacheEntry(die=die, plane=plane, kind="ON_CACHE_READ", start_us=quantize(start_us), celltype=celltype)

    def end_cache_read(self, die: int, plane: int, end_us: float) -> None:
        ent = self._cache_read.get((die, plane))
        if ent and ent.end_us is None:
            ent.end_us = quantize(end_us)

    def begin_cache_program(self, die: int, start_us: float, kind: str = "ON_CACHE_PROGRAM", celltype: Optional[str] = None) -> None:
        if kind not in ("ON_CACHE_PROGRAM", "ON_ONESHOT_CACHE_PROGRAM"):
            kind = "ON_CACHE_PROGRAM"
        self._cache_program[die] = _CacheEntry(die=die, plane=None, kind=kind, start_us=quantize(start_us), celltype=celltype)

    def end_cache_program(self, die: int, end_us: float) -> None:
        ent = self._cache_program.get(die)
        if ent and ent.end_us is None:
            ent.end_us = quantize(end_us)

    def suspend_states(self, die: int, at_us: Optional[float] = None) -> Optional[str]:
        """Legacy single-axis view of suspend state.

        Returns one active state if any, preferring PROGRAM over ERASE when both
        are active. Returns None when neither is active. Kept for backward
        compatibility; new code should use axis-specific APIs below.
        """
        # program axis preferred if active
        ps = self.program_suspend_state(die, at_us)
        if ps == "PROGRAM_SUSPENDED":
            return "PROGRAM_SUSPENDED"
        es = self.erase_suspend_state(die, at_us)
        if es == "ERASE_SUSPENDED":
            return "ERASE_SUSPENDED"
        return None

    def erase_suspend_state(self, die: int, at_us: Optional[float] = None) -> str:
        """Return ERASE suspend axis state as symbolic string.

        - Active: 'ERASE_SUSPENDED'
        - Inactive/default: 'NOT_ERASE_SUSPENDED'
        """
        st = self._erase_susp.get(die)
        if not st:
            return "NOT_ERASE_SUSPENDED"
        if at_us is None:
            return "ERASE_SUSPENDED" if st.end_us is None else "NOT_ERASE_SUSPENDED"
        t = quantize(at_us)
        return "ERASE_SUSPENDED" if (st.start_us <= t and (st.end_us is None or t < st.end_us)) else "NOT_ERASE_SUSPENDED"

    def program_suspend_state(self, die: int, at_us: Optional[float] = None) -> str:
        """Return PROGRAM suspend axis state as symbolic string.

        - Active: 'PROGRAM_SUSPENDED'
        - Inactive/default: 'NOT_PROGRAM_SUSPENDED'
        """
        st = self._pgm_susp.get(die)
        if not st:
            return "NOT_PROGRAM_SUSPENDED"
        if at_us is None:
            return "PROGRAM_SUSPENDED" if st.end_us is None else "NOT_PROGRAM_SUSPENDED"
        t = quantize(at_us)
        return "PROGRAM_SUSPENDED" if (st.start_us <= t and (st.end_us is None or t < st.end_us)) else "NOT_PROGRAM_SUSPENDED"

    def set_suspend_state(self, die: int, state: Optional[str], now_us: float) -> None:
        """Legacy helper to toggle suspend state; maps to split axes.

        Accepts 'ERASE_SUSPENDED'/'PROGRAM_SUSPENDED' to activate a given axis,
        None to clear both axes. Intended for tests/tools; normal flow uses ops.
        """
        t = quantize(now_us)
        if state is None:
            # Clear both axes
            st = self._erase_susp.get(die)
            if st and st.end_us is None:
                st.end_us = t
            self._erase_susp[die] = None
            stp = self._pgm_susp.get(die)
            if stp and stp.end_us is None:
                stp.end_us = t
            self._pgm_susp[die] = None
            self._suspend_states[die] = None
            return
        s = str(state).upper()
        if s == "ERASE_SUSPENDED":
            self._erase_susp[die] = _AxisState(die=die, state="ERASE_SUSPENDED", start_us=t)
            # legacy mirror
            self._suspend_states[die] = _SuspState(die=die, state="ERASE_SUSPENDED", start_us=t)
        elif s == "PROGRAM_SUSPENDED":
            self._pgm_susp[die] = _AxisState(die=die, state="PROGRAM_SUSPENDED", start_us=t)
            # legacy mirror
            self._suspend_states[die] = _SuspState(die=die, state="PROGRAM_SUSPENDED", start_us=t)

    def ongoing_ops(self, die: Optional[int] = None) -> List[Dict[str, Any]]:
        if die is None:
            lst: List[Dict[str, Any]] = []
            for d, ops in self._ongoing_ops.items():
                for o in ops:
                    lst.append({
                        "die": d,
                        "op_id": o.op_id,
                        "op_name": o.op_name,
                        "base": o.base,
                        "targets": [Address(t.die, t.plane, t.block, t.page) for t in o.targets],
                        "start_us": o.start_us,
                        "end_us": o.end_us,
                        "remaining_us": o.remaining_us,
                        "suspend_time_us": o.suspend_time_us,
                    })
            return lst
        return [
            {
                "die": die,
                "op_id": o.op_id,
                "op_name": o.op_name,
                "base": o.base,
                "targets": [Address(t.die, t.plane, t.block, t.page) for t in o.targets],
                "start_us": o.start_us,
                "end_us": o.end_us,
                "remaining_us": o.remaining_us,
                "suspend_time_us": o.suspend_time_us,
            }
            for o in self._ongoing_ops.get(die, [])
        ]

    def suspended_ops(self, die: Optional[int] = None) -> List[Dict[str, Any]]:
        def _pub(d: int, o: _OpMeta) -> Dict[str, Any]:
            return {
                "die": d,
                "op_id": o.op_id,
                "op_name": o.op_name,
                "base": o.base,
                "targets": [Address(t.die, t.plane, t.block, t.page) for t in o.targets],
                "start_us": o.start_us,
                "end_us": o.end_us,
                "remaining_us": o.remaining_us,
                "suspend_time_us": o.suspend_time_us,
            }
        # Merge ERASE/PROGRAM axes into a legacy single list (sorted by start_us)
        if die is None:
            acc: List[Dict[str, Any]] = []
            for d in range(self.dies):
                for o in self._suspended_ops_erase.get(d, []):
                    acc.append(_pub(d, o))
                for o in self._suspended_ops_program.get(d, []):
                    acc.append(_pub(d, o))
            try:
                acc.sort(key=lambda m: float(m.get("start_us", 0.0)))
            except Exception:
                pass
            return acc
        acc: List[Dict[str, Any]] = []
        for o in self._suspended_ops_erase.get(int(die), []):
            acc.append(_pub(int(die), o))
        for o in self._suspended_ops_program.get(int(die), []):
            acc.append(_pub(int(die), o))
        try:
            acc.sort(key=lambda m: float(m.get("start_us", 0.0)))
        except Exception:
            pass
        return acc

    def suspended_ops_erase(self, die: Optional[int] = None) -> List[Dict[str, Any]]:
        def _pub(d: int, o: _OpMeta) -> Dict[str, Any]:
            return {
                "die": d,
                "op_id": o.op_id,
                "op_name": o.op_name,
                "base": o.base,
                "targets": [Address(t.die, t.plane, t.block, t.page) for t in o.targets],
                "start_us": o.start_us,
                "end_us": o.end_us,
                "remaining_us": o.remaining_us,
                "suspend_time_us": o.suspend_time_us,
            }
        if die is None:
            lst: List[Dict[str, Any]] = []
            for d, ops in self._suspended_ops_erase.items():
                for o in ops:
                    lst.append(_pub(d, o))
            return lst
        return [_pub(int(die), o) for o in self._suspended_ops_erase.get(int(die), [])]

    def suspended_ops_program(self, die: Optional[int] = None) -> List[Dict[str, Any]]:
        def _pub(d: int, o: _OpMeta) -> Dict[str, Any]:
            return {
                "die": d,
                "op_id": o.op_id,
                "op_name": o.op_name,
                "base": o.base,
                "targets": [Address(t.die, t.plane, t.block, t.page) for t in o.targets],
                "start_us": o.start_us,
                "end_us": o.end_us,
                "remaining_us": o.remaining_us,
                "suspend_time_us": o.suspend_time_us,
            }
        if die is None:
            lst: List[Dict[str, Any]] = []
            for d, ops in self._suspended_ops_program.items():
                for o in ops:
                    lst.append(_pub(d, o))
            return lst
        return [_pub(int(die), o) for o in self._suspended_ops_program.get(int(die), [])]

    def register_ongoing(self, die: int, op_id: Optional[int], op_name: Optional[str], base: str, targets: List[Address], start_us: float, end_us: float) -> None:
        axis = self._axis_for_base(base)
        meta = _OpMeta(
            die=die,
            op_id=op_id,
            op_name=op_name,
            base=str(base),
            targets=list(targets),
            start_us=quantize(start_us),
            end_us=quantize(end_us),
            axis=axis,
        )
        self._ongoing_ops.setdefault(die, []).append(meta)

    def _axis_for_base(self, base: str) -> Optional[str]:
        bb = str(base or "").upper()
        if "ERASE" in bb and "SUSPEND" not in bb and "RESUME" not in bb:
            return "ERASE"
        if "PROGRAM" in bb and "SUSPEND" not in bb and "RESUME" not in bb and "CACHE" not in bb:
            return "PROGRAM"
        return None

    def move_to_suspended(self, die: int, op_id: Optional[int], now_us: float) -> None:
        """Backward-compatible wrapper. Infers axis from op base and delegates.

        New code should call move_to_suspended_axis(..., axis).
        """
        ops = self._ongoing_ops.get(die, [])
        if not ops:
            return
        idx = None
        if op_id is not None:
            for i in range(len(ops) - 1, -1, -1):
                if ops[i].op_id == op_id:
                    idx = i
                    break
        if idx is None:
            idx = len(ops) - 1
        if idx < 0:
            return
        # Peek to choose axis; do not mutate ongoing here
        meta = ops[idx]
        b = str(meta.base).upper()
        axis = "ERASE" if "ERASE" in b else ("PROGRAM" if "PROGRAM" in b else None)
        if axis is None:
            return
        self.move_to_suspended_axis(die, op_id=meta.op_id, now_us=now_us, axis=axis)

    def move_to_suspended_axis(self, die: int, op_id: Optional[int], now_us: float, axis: str) -> None:
        """Move the latest ongoing op to axis-specific suspended list when family matches.

        axis: 'ERASE' | 'PROGRAM'
        """
        ops = self._ongoing_ops.get(die, [])
        if not ops:
            return
        fam = str(axis).upper()
        idx = None
        if op_id is not None:
            for i in range(len(ops) - 1, -1, -1):
                if ops[i].op_id == op_id:
                    idx = i
                    break
        if idx is None:
            idx = len(ops) - 1
        if idx < 0:
            return
        meta = ops[idx]
        b = str(meta.base).upper()
        # Only move when the meta family matches the given axis
        is_match = ((fam == "ERASE" and "ERASE" in b) or (fam == "PROGRAM" and ("PROGRAM" in b) and ("SUSPEND" not in b) and ("RESUME" not in b)))
        if not is_match:
            return
        # pop and move
        meta = ops.pop(idx)
        now_q = quantize(now_us)
        meta.suspend_time_us = now_q
        rem = max(0.0, meta.end_us - now_q)
        rem = quantize(rem)
        meta.remaining_us = rem
        meta.axis = fam
        if fam == "ERASE":
            stack = self._suspended_ops_erase.setdefault(die, [])
            stack.append(meta)
        else:
            stack = self._suspended_ops_program.setdefault(die, [])
            stack.append(meta)

    def resume_from_suspended(self, die: int, op_id: Optional[int], now_us: Optional[float] = None) -> None:
        """Backward-compatible wrapper. Picks the most recent across axes when op_id is None.

        New code should call resume_from_suspended_axis(..., axis).
        """
        # Determine candidate from both axes
        lst_e = self._suspended_ops_erase.get(die, [])
        lst_p = self._suspended_ops_program.get(die, [])
        if not lst_e and not lst_p:
            return
        # If op_id specified, prefer matching in program, then erase
        if op_id is not None:
            if any(meta.op_id == op_id for meta in lst_p):
                self.resume_from_suspended_axis(die, op_id=op_id, axis="PROGRAM", now_us=now_us)
                return
            if any(meta.op_id == op_id for meta in lst_e):
                self.resume_from_suspended_axis(die, op_id=op_id, axis="ERASE", now_us=now_us)
                return
        # No op_id or not found: choose the one with latest start_us
        cand_p = lst_p[-1] if lst_p else None
        cand_e = lst_e[-1] if lst_e else None
        if cand_p and (not cand_e or cand_p.start_us >= cand_e.start_us):
            self.resume_from_suspended_axis(die, op_id=getattr(cand_p, "op_id", None), axis="PROGRAM", now_us=now_us)
        elif cand_e:
            self.resume_from_suspended_axis(die, op_id=getattr(cand_e, "op_id", None), axis="ERASE", now_us=now_us)

    def resume_from_suspended_axis(self, die: int, op_id: Optional[int], axis: str, now_us: Optional[float] = None) -> Optional[_OpMeta]:
        fam = str(axis).upper()
        lst = self._suspended_ops_program.get(die, []) if fam == "PROGRAM" else self._suspended_ops_erase.get(die, [])
        if not lst:
            return None
        idx = None
        if op_id is not None:
            for i in range(len(lst) - 1, -1, -1):
                if lst[i].op_id == op_id:
                    idx = i
                    break
        if idx is None:
            idx = len(lst) - 1
        if idx < 0:
            return None
        meta = lst.pop(idx)
        start_ref = now_us if now_us is not None else meta.suspend_time_us
        if start_ref is None:
            start_ref = meta.start_us
        if meta.suspend_time_us is not None:
            start_ref = max(start_ref, meta.suspend_time_us)
        start_q = quantize(start_ref)
        rem = meta.remaining_us
        if rem is None:
            rem = max(0.0, meta.end_us - start_q)
        rem = quantize(rem)
        end_q = quantize(start_q + rem)
        meta.start_us = start_q
        meta.end_us = end_q
        meta.remaining_us = None
        meta.suspend_time_us = None
        meta.axis = fam
        self._ongoing_ops.setdefault(die, []).append(meta)
        return meta

    def is_op_suspended(self, op_id: int) -> bool:
        if op_id is None:
            return False
        for lst in self._suspended_ops_program.values():
            if any(meta.op_id == op_id for meta in lst):
                return True
        for lst in self._suspended_ops_erase.values():
            if any(meta.op_id == op_id for meta in lst):
                return True
        return False

    def complete_op(self, op_id: int) -> None:
        if op_id is None:
            return
        for ops in self._ongoing_ops.values():
            for i in range(len(ops) - 1, -1, -1):
                if ops[i].op_id == op_id:
                    ops.pop(i)
                    return

    def snapshot(self) -> Dict[str, Any]:
        return {
            "avail": dict(self._avail),
            "plane_resv": {k: list(v) for k, v in self._plane_resv.items()},
            "bus_resv": list(self._bus_resv),
            "excl_global": [ExclWindow(w.start, w.end, w.scope, w.die, set(w.tokens)) for w in self._excl_global],
            "excl_die": {d: [ExclWindow(w.start, w.end, w.scope, w.die, set(w.tokens)) for w in lst] for d, lst in self._excl_die.items()},
            "latch": {
                k: {
                    kind: _LatchEntry(kind=kind, start_us=entry.start_us, end_us=entry.end_us)
                    for kind, entry in bucket.items()
                }
                for k, bucket in self._latch.items()
            },
            "timeline": [(seg.die, seg.plane, seg.op_base, seg.state, seg.start_us, seg.end_us) for lst in self._st.by_plane.values() for seg in lst],
            # proposer-facing runtime states
            "odt_disabled": bool(self._odt_disabled),
            "cache_read": [
                (d, p, e.kind, e.start_us, e.end_us, e.celltype)
                for ((d, p), e) in self._cache_read.items()
            ],
            "cache_program": [
                (d, e.kind, e.start_us, e.end_us, e.celltype)
                for (d, e) in self._cache_program.items()
            ],
            # split suspend axes
            "suspend_states_erase": {
                d: (s.state, s.start_us, s.end_us) if s else None for d, s in self._erase_susp.items()
            },
            "suspend_states_program": {
                d: (s.state, s.start_us, s.end_us) if s else None for d, s in self._pgm_susp.items()
            },
            # legacy single-axis snapshot (kept for compat)
            "suspend_states": {
                d: (s.state, s.start_us, s.end_us) if s else None for d, s in self._suspend_states.items()
            },
            "ongoing_ops": {
                d: [
                    {
                        "op_id": m.op_id,
                        "op_name": m.op_name,
                        "base": m.base,
                        "targets": [(t.die, t.plane, t.block, t.page) for t in m.targets],
                        "start_us": m.start_us,
                        "end_us": m.end_us,
                        "remaining_us": m.remaining_us,
                        "suspend_time_us": m.suspend_time_us,
                    }
                    for m in lst
                ]
                for d, lst in self._ongoing_ops.items()
            },
            # axis-specific suspended ops
            "suspended_ops_erase": {
                d: [
                    {
                        "op_id": m.op_id,
                        "op_name": m.op_name,
                        "base": m.base,
                        "targets": [(t.die, t.plane, t.block, t.page) for t in m.targets],
                        "start_us": m.start_us,
                        "end_us": m.end_us,
                        "remaining_us": m.remaining_us,
                        "suspend_time_us": m.suspend_time_us,
                    }
                    for m in lst
                ]
                for d, lst in self._suspended_ops_erase.items()
            },
            "suspended_ops_program": {
                d: [
                    {
                        "op_id": m.op_id,
                        "op_name": m.op_name,
                        "base": m.base,
                        "targets": [(t.die, t.plane, t.block, t.page) for t in m.targets],
                        "start_us": m.start_us,
                        "end_us": m.end_us,
                        "remaining_us": m.remaining_us,
                        "suspend_time_us": m.suspend_time_us,
                    }
                    for m in lst
                ]
                for d, lst in self._suspended_ops_program.items()
            },
            # legacy single-axis union view (for backward-compat consumers)
            "suspended_ops": {
                d: [
                    {
                        "op_id": m.op_id,
                        "op_name": m.op_name,
                        "base": m.base,
                        "targets": [(t.die, t.plane, t.block, t.page) for t in m.targets],
                        "start_us": m.start_us,
                        "end_us": m.end_us,
                        "remaining_us": m.remaining_us,
                        "suspend_time_us": m.suspend_time_us,
                    }
                    for m in (self._suspended_ops_erase.get(d, []) + self._suspended_ops_program.get(d, []))
                ]
                for d in range(self.dies)
            },
        }

    def restore(self, snap: Dict[str, Any]) -> None:
        self._avail = dict(snap.get("avail", {}))
        self._plane_resv = {tuple(k) if not isinstance(k, tuple) else k: list(v) for k, v in snap.get("plane_resv", {}).items()}
        self._bus_resv = list(snap.get("bus_resv", []))
        self._excl_global = [ExclWindow(w.start, w.end, w.scope, w.die, set(w.tokens)) for w in snap.get("excl_global", [])]
        self._excl_die = {int(d): [ExclWindow(w.start, w.end, w.scope, w.die, set(w.tokens)) for w in lst] for d, lst in snap.get("excl_die", {}).items()}
        self._latch = {}
        for k, raw_bucket in (snap.get("latch", {}) or {}).items():
            key_tuple = tuple(k) if not isinstance(k, tuple) else k
            key = tuple(int(x) for x in key_tuple)
            bucket: _LatchBucket = {}
            if isinstance(raw_bucket, dict):
                items = raw_bucket.items()
            else:
                # Legacy snapshot storing a single latch entry per key
                items = [(getattr(raw_bucket, "kind", READ_LATCH_KIND), raw_bucket)]
            for kind, value in items:
                if value is None:
                    continue
                if isinstance(value, _LatchEntry):
                    entry = _LatchEntry(kind=value.kind, start_us=value.start_us, end_us=value.end_us)
                elif isinstance(value, dict):
                    entry = _LatchEntry(
                        kind=str(value.get("kind", kind)),
                        start_us=float(value.get("start_us", 0.0)),
                        end_us=(None if value.get("end_us") is None else float(value.get("end_us", 0.0))),
                    )
                else:
                    entry = _LatchEntry(
                        kind=str(getattr(value, "kind", kind)),
                        start_us=float(getattr(value, "start_us", 0.0)),
                        end_us=(None if getattr(value, "end_us", None) is None else float(getattr(value, "end_us", 0.0))),
                    )
                bucket[str(kind)] = entry
            if bucket:
                self._latch[key] = bucket
        self._st = _StateTimeline()
        for (die, plane, op_base, state, s0, s1) in snap.get("timeline", []):
            self._st._insert_plane((die, plane), _StateInterval(die, plane, op_base, state, s0, s1))
        # proposer-facing runtime states
        self._odt_disabled = bool(snap.get("odt_disabled", False))
        self._cache_read = {}
        for (d, p, kind, s0, s1, cell) in snap.get("cache_read", []):
            self._cache_read[(int(d), int(p))] = _CacheEntry(die=int(d), plane=int(p), kind=str(kind), start_us=float(s0), end_us=(None if s1 is None else float(s1)), celltype=(None if cell in (None, "None") else cell))
        self._cache_program = {}
        for (d, kind, s0, s1, cell) in snap.get("cache_program", []):
            self._cache_program[int(d)] = _CacheEntry(die=int(d), plane=None, kind=str(kind), start_us=float(s0), end_us=(None if s1 is None else float(s1)), celltype=(None if cell in (None, "None") else cell))
        # Restore split suspend axes (prefer new keys; fallback to legacy)
        self._erase_susp = {d: None for d in range(self.dies)}
        self._pgm_susp = {d: None for d in range(self.dies)}
        for k, v in (snap.get("suspend_states_erase", {}) or {}).items():
            d = int(k)
            if v is None:
                self._erase_susp[d] = None
            else:
                st, s0, s1 = v
                self._erase_susp[d] = _AxisState(die=d, state=str(st), start_us=float(s0), end_us=(None if s1 is None else float(s1)))
        for k, v in (snap.get("suspend_states_program", {}) or {}).items():
            d = int(k)
            if v is None:
                self._pgm_susp[d] = None
            else:
                st, s0, s1 = v
                self._pgm_susp[d] = _AxisState(die=d, state=str(st), start_us=float(s0), end_us=(None if s1 is None else float(s1)))
        # Legacy single-axis snapshot (kept for compat)
        self._suspend_states = {d: None for d in range(self.dies)}
        for k, v in (snap.get("suspend_states", {}) or {}).items():
            d = int(k)
            if v is None:
                self._suspend_states[d] = None
            else:
                st, s0, s1 = v
                self._suspend_states[d] = _SuspState(die=d, state=str(st), start_us=float(s0), end_us=(None if s1 is None else float(s1)))
        self._ongoing_ops = {d: [] for d in range(self.dies)}
        for k, lst in (snap.get("ongoing_ops", {}) or {}).items():
            d = int(k)
            acc: List[_OpMeta] = []
            for m in lst:
                acc.append(_OpMeta(
                    die=d,
                    op_id=m.get("op_id"),
                    op_name=m.get("op_name"),
                    base=str(m.get("base")),
                    targets=[Address(int(t[0]), int(t[1]), int(t[2]), (None if t[3] in (None, "None") else int(t[3]))) for t in m.get("targets", [])],
                    start_us=float(m.get("start_us", 0.0)),
                    end_us=float(m.get("end_us", 0.0)),
                    remaining_us=(None if m.get("remaining_us") in (None, "None") else float(m.get("remaining_us"))),
                    suspend_time_us=(None if m.get("suspend_time_us") in (None, "None") else float(m.get("suspend_time_us"))),
                ))
            self._ongoing_ops[d] = acc
        # Restore suspended ops (axis-specific preferred, legacy fallback)
        self._suspended_ops_erase = {d: [] for d in range(self.dies)}
        self._suspended_ops_program = {d: [] for d in range(self.dies)}
        # New fields
        for k, lst in (snap.get("suspended_ops_erase", {}) or {}).items():
            d = int(k)
            acc: List[_OpMeta] = []
            for m in lst:
                acc.append(_OpMeta(
                    die=d,
                    op_id=m.get("op_id"),
                    op_name=m.get("op_name"),
                    base=str(m.get("base")),
                    targets=[Address(int(t[0]), int(t[1]), int(t[2]), (None if t[3] in (None, "None") else int(t[3]))) for t in m.get("targets", [])],
                    start_us=float(m.get("start_us", 0.0)),
                    end_us=float(m.get("end_us", 0.0)),
                    remaining_us=(None if m.get("remaining_us") in (None, "None") else float(m.get("remaining_us"))),
                    suspend_time_us=(None if m.get("suspend_time_us") in (None, "None") else float(m.get("suspend_time_us"))),
                ))
            self._suspended_ops_erase[d] = acc
        for k, lst in (snap.get("suspended_ops_program", {}) or {}).items():
            d = int(k)
            acc: List[_OpMeta] = []
            for m in lst:
                acc.append(_OpMeta(
                    die=d,
                    op_id=m.get("op_id"),
                    op_name=m.get("op_name"),
                    base=str(m.get("base")),
                    targets=[Address(int(t[0]), int(t[1]), int(t[2]), (None if t[3] in (None, "None") else int(t[3]))) for t in m.get("targets", [])],
                    start_us=float(m.get("start_us", 0.0)),
                    end_us=float(m.get("end_us", 0.0)),
                    remaining_us=(None if m.get("remaining_us") in (None, "None") else float(m.get("remaining_us"))),
                    suspend_time_us=(None if m.get("suspend_time_us") in (None, "None") else float(m.get("suspend_time_us"))),
                ))
            self._suspended_ops_program[d] = acc
        # Legacy field fallback
        if not any(self._suspended_ops_erase.values()) and not any(self._suspended_ops_program.values()):
            for k, lst in (snap.get("suspended_ops", {}) or {}).items():
                d = int(k)
                for m in lst:
                    meta = _OpMeta(
                        die=d,
                        op_id=m.get("op_id"),
                        op_name=m.get("op_name"),
                        base=str(m.get("base")),
                        targets=[Address(int(t[0]), int(t[1]), int(t[2]), (None if t[3] in (None, "None") else int(t[3]))) for t in m.get("targets", [])],
                        start_us=float(m.get("start_us", 0.0)),
                        end_us=float(m.get("end_us", 0.0)),
                        remaining_us=(None if m.get("remaining_us") in (None, "None") else float(m.get("remaining_us"))),
                        suspend_time_us=(None if m.get("suspend_time_us") in (None, "None") else float(m.get("suspend_time_us"))),
                    )
                    b = str(meta.base).upper()
                    if "ERASE" in b:
                        self._suspended_ops_erase.setdefault(d, []).append(meta)
                    elif "PROGRAM" in b and ("SUSPEND" not in b) and ("RESUME" not in b):
                        self._suspended_ops_program.setdefault(d, []).append(meta)
                    else:
                        # Unknown base; skip to avoid misclassification
                        continue
    # minimal exclusion window derivation from cfg
    def _derive_excl(self, op: Any, start: float, die: int) -> List[ExclWindow]:
        rules=(self.cfg or {}).get("constraints",{}).get("exclusions",[])
        if not rules: return []
        base=self._op_base(op); t=start
        segs=[(getattr(s,"name","STATE"), t:=t+float(getattr(s,"dur_us",0.0))) for s in getattr(op,"states",[])]
        # segs now hold (state, end_time); derive (s0,s1) from cumulative
        wins:List[ExclWindow]=[]; s_prev=start
        for (st_name,s_end) in segs:
            for r in rules:
                when=r.get("when",{}); wop=str(when.get("op",""))
                if wop and wop.upper()!=base.upper(): continue
                states=when.get("states",["*"]); tokens=set(r.get("blocks",[])); scope=str(r.get("scope","GLOBAL")).upper()
                if "*" in states or st_name in states:
                    wins.append(ExclWindow(start=quantize(s_prev), end=quantize(s_end), scope=scope, die=(die if scope=="DIE" else None), tokens=tokens))
            s_prev=s_end
        return wins

    # helpers
    def _latch_kind_for_base(self, base: str) -> Optional[str]:
        b = str(base).upper()
        if b in ("READ", "READ4K", "PLANE_READ", "PLANE_READ4K", "CACHE_READ", "PLANE_CACHE_READ", "COPYBACK_READ"):
            return READ_LATCH_KIND
        if b == "ONESHOT_PROGRAM_LSB":
            return "LATCH_ON_LSB"
        if b == "ONESHOT_PROGRAM_CSB":
            return "LATCH_ON_CSB"
        if b == "ONESHOT_PROGRAM_MSB":
            return "LATCH_ON_MSB"
        return None

    # Overlay updates to reflect address effects of reserved ops in current txn
    def _update_overlay_for_reserved(self, txn: _Txn, base: str, targets: List[Address]) -> None:
        b = str(base).upper()
        # ERASE resets addr_state to -1
        if b == "ERASE":
            for t in targets:
                key = (int(t.die), int(t.block))
                ov = txn.addr_overlay.setdefault(key, {})
                ov["addr_state"] = -1
        # PROGRAM-like ops set addr_state to the programmed page
        if b in self._program_base_whitelist:
            for t in targets:
                if t.page is None:
                    continue
                key = (int(t.die), int(t.block))
                ov = txn.addr_overlay.setdefault(key, {})
                prev = ov.get("addr_state", None)
                pg = int(t.page)
                ov["addr_state"] = max(prev, pg) if isinstance(prev, int) else pg

    # -------------------------------
    # Validator integration — skeleton
    # -------------------------------
    def register_addr_policy(self, fn: Optional[Callable[..., Any]]) -> None:
        """Register address-dependent policy callback (e.g., AddressManager.check_epr).

        Phase 0 skeleton: stored for later EPR evaluation; unused unless enabled by config.
        """
        self.addr_policy = fn

    def last_validation(self) -> Optional[Dict[str, Any]]:
        """Return last validation snapshot for observability/debugging."""
        return self._last_validation

    def _rules_cfg(self) -> Dict[str, Any]:
        cfg = (self.cfg or {}).get("constraints", {}) or {}
        enabled_rules = set()
        try:
            for r in (cfg.get("enabled_rules") or []):
                if isinstance(r, str):
                    enabled_rules.add(r.strip())
        except Exception:
            pass
        return {
            "enabled_rules": enabled_rules,
            "enable_epr": bool(cfg.get("enable_epr", False)),
            "epr": dict(cfg.get("epr", {}) or {}),
        }

    def _eval_rules(
        self,
        stage: str,
        op: Any,
        targets: List[Address],
        scope: Scope,
        start: float,
        end: float,
        txn: Optional[_Txn] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Evaluate enabled rules and return (ok, reason_code).

        Phase 0: skeleton — feature-flagged no-op to ensure zero behavior change
        when not explicitly enabled. Always returns (True, None).
        """
        rcfg = self._rules_cfg()
        self._last_validation = {
            "stage": stage,
            "op_name": self._op_name(op),
            "base": self._op_base(op),
            "targets": [(t.die, t.plane, t.block, t.page) for t in targets],
            "scope": str(scope.name),
            "start": start,
            "end": end,
            "enabled_rules": sorted(list(rcfg.get("enabled_rules", set()))),
            "enable_epr": bool(rcfg.get("enable_epr", False)),
        }
        # Evaluate state_forbid rules if enabled
        enabled = rcfg.get("enabled_rules", set())
        base = self._op_base(op)
        die = targets[0].die
        plane_set = [t.plane for t in targets]

        def _enabled(name: str, category: Optional[str] = None) -> bool:
            if name in enabled:
                return True
            if category and category in enabled:
                return True
            return False

        # Suspend
        if _enabled("state_forbid_suspend", category="state_forbid"):
            reason = self._rule_forbid_on_suspend(base, die, start)
            if reason is not None:
                self._last_validation.update({"failed_rule": reason})  # type: ignore[union-attr]
                return False, reason

        # ODT
        if _enabled("state_forbid_odt", category="state_forbid"):
            reason = self._rule_forbid_on_odt(base, start)
            if reason is not None:
                self._last_validation.update({"failed_rule": reason})  # type: ignore[union-attr]
                return False, reason

        # Cache (die-level and plane-level)
        if _enabled("state_forbid_cache", category="state_forbid"):
            reason = self._rule_forbid_on_cache(base, die, plane_set, start)
            if reason is not None:
                self._last_validation.update({"failed_rule": reason})  # type: ignore[union-attr]
                return False, reason

        # Address-dependent EPR via injected policy (if enabled)
        if rcfg.get("enable_epr", False) and _enabled("addr_dep"):
            if self.addr_policy is not None:
                try:
                    pending = None
                    if txn is not None and txn.addr_overlay:
                        pending = dict(txn.addr_overlay)
                    simple_targets = [
                        (int(t.die), int(t.block), (None if t.page is None else int(t.page)))
                        for t in targets
                    ]
                    epr_cfg = rcfg.get("epr", {}) or {}
                    offset_guard = epr_cfg.get("offset_guard")
                    disable_pbe = bool(epr_cfg.get("disable_program_before_erase", False))
                    # Derive celltype for this op (if any) from cfg.op_names
                    cell = None
                    try:
                        on = self._op_name(op)
                        cell = ((self.cfg.get("op_names", {}) or {}).get(on or "", {}) or {}).get("celltype")
                        cell = None if cell in (None, "NONE", "None") else str(cell)
                    except Exception:
                        cell = None
                    res = self.addr_policy(
                        base=self._op_base(op),
                        targets=simple_targets,
                        op_name=self._op_name(op),
                        op_celltype=cell,
                        as_of_us=start,
                        pending=pending,
                        offset_guard=offset_guard,
                        disable_program_before_erase=disable_pbe,
                    )
                    ok = getattr(res, "ok", None)
                    if ok is None and isinstance(res, dict):
                        ok = bool(res.get("ok", False))
                    if not ok:
                        failures = getattr(res, "failures", None)
                        if failures is None and isinstance(res, dict):
                            failures = res.get("failures", [])
                        self._last_validation.update({  # type: ignore[union-attr]
                            "failed_rule": "epr_dep",
                            "epr_failures": [
                                getattr(f, "code", None) if not isinstance(f, dict) else f.get("code")
                                for f in (failures or [])
                            ],
                        })
                        # Optional detailed debug for EPR
                        try:
                            dbg_env = os.getenv("EPR_DEBUG", "").lower() not in ("", "0", "false")
                            dbg_cfg = bool((rcfg.get("epr", {}) or {}).get("debug", False))
                            if dbg_env or dbg_cfg:
                                tg_str = ",".join([f"({d},{b},{p})" for (d,b,p) in simple_targets])
                                keys = list((pending or {}).keys()) if isinstance(pending, dict) else []
                                print(f"[epr-debug] epr_dep base={self._op_base(op)} op={self._op_name(op)} start={start} targets=[{tg_str}] epr_failures={[getattr(f, 'code', None) if not isinstance(f, dict) else f.get('code') for f in (failures or [])]} pending_keys={keys}")
                                # Show per-target pending overlay values when available
                                if pending:
                                    for (d,b,p) in simple_targets:
                                        ov = pending.get((int(d), int(b)))
                                        if isinstance(ov, dict) and ("addr_state" in ov):
                                            print(f"[epr-debug] pending_overlay die={d} block={b} addr_state={ov.get('addr_state')}")
                        except Exception:
                            pass
                        return False, "epr_dep"
                except Exception as e:
                    # Defensive: treat as allow but record
                    self._last_validation.update({  # type: ignore[union-attr]
                        "epr_error": str(e)[:200],
                    })
        return True, None

    # --- Rule helpers: state_forbid family ---
    def _blocked_by_groups(self, base: str, groups: List[str]) -> bool:
        group_defs: Dict[str, List[str]] = (self.cfg.get("exclusion_groups") or {})
        for g in groups:
            bases = group_defs.get(g, [])
            if base in bases:
                return True
        return False

    def _rule_forbid_on_suspend(self, base: str, die: int, at_us: float) -> Optional[str]:
        """Block operations based on split suspend axes at `at_us`.

        Uses exclusions_by_suspend_state mapping with four keys and applies
        union of groups from ERASE and PROGRAM axes (including NOT_* defaults).
        """
        t = float(at_us)
        es = self.erase_suspend_state(die, at_us=t)
        ps = self.program_suspend_state(die, at_us=t)
        groups_by_state: Dict[str, List[str]] = (self.cfg.get("exclusions_by_suspend_state") or {})
        groups: List[str] = []
        groups.extend(groups_by_state.get(str(es), []) or [])
        groups.extend(groups_by_state.get(str(ps), []) or [])
        if not groups:
            return None
        if self._blocked_by_groups(base, groups):
            return "state_forbid_suspend"
        return None

    def _rule_forbid_on_odt(self, base: str, at_us: float) -> Optional[str]:
        """Block operations when ODT_DISABLE is active per config mapping.

        Config keys: exclusions_by_odt_state -> [group]
        """
        # odt_state does not need time to evaluate; it's global boolean. Keep at_us for symmetry.
        odt = self.odt_state()
        if not odt:
            return None
        groups_by_state: Dict[str, List[str]] = (self.cfg.get("exclusions_by_odt_state") or {})
        groups = groups_by_state.get(odt, [])
        if self._blocked_by_groups(base, groups):
            return "state_forbid_odt"
        return None

    def _rule_forbid_on_cache(self, base: str, die: int, plane_set: List[int], at_us: float) -> Optional[str]:
        """Block operations when cache state is active per config mapping.

        Checks die-level program cache first, then per-target plane read cache.
        Config keys: exclusions_by_cache_state -> [group]
        """
        groups_by_state: Dict[str, List[str]] = (self.cfg.get("exclusions_by_cache_state") or {})
        # die-level cache program has priority
        st_die = self.cache_state(die, plane=0, at_us=at_us)
        if st_die in ("ON_CACHE_PROGRAM", "ON_ONESHOT_CACHE_PROGRAM"):
            groups = groups_by_state.get(str(st_die), [])
            if self._blocked_by_groups(base, groups):
                return "state_forbid_cache"
        # plane-level cache read on any target plane
        for p in plane_set:
            st_plane = self.cache_state(die, plane=p, at_us=at_us)
            if not st_plane:
                continue
            groups = groups_by_state.get(str(st_plane), [])
            if self._blocked_by_groups(base, groups):
                return "state_forbid_cache"
        return None
