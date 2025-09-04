from __future__ import annotations
from dataclasses import dataclass, field
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
class _Latch:
    start_us: float
    end_us: Optional[float]
    kind: str  # e.g., 'LATCH_ON_READ', 'LATCH_ON_LSB', 'LATCH_ON_CSB', 'LATCH_ON_MSB'
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
@dataclass
class _Txn: now_us:float; plane_resv:Dict[Tuple[int,int],List[Tuple[float,float]]]=field(default_factory=dict); bus_resv:List[Tuple[float,float]]=field(default_factory=list); excl_global:List[ExclWindow]=field(default_factory=list); excl_die:Dict[int,List[ExclWindow]]=field(default_factory=dict); latch_locks:Dict[Tuple[int,int],_Latch]=field(default_factory=dict); st_ops:List[Tuple[int,int,str,List[Tuple[str,float]],float]]=field(default_factory=list)
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
        self._latch: Dict[Tuple[int, int], _Latch] = {}
        self._st = _StateTimeline()
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

    def _planescope_ok(self, die: int, scope: Scope, plane_set: List[int], start: float, end: float) -> bool:
        planes = list(range(self.planes)) if scope == Scope.DIE_WIDE else plane_set
        for p in planes:
            for (s, e) in self._plane_resv[(die, p)]:
                if not (end <= s or e <= start):
                    return False
        return True

    def _bus_ok(self, op: Any, start: float) -> bool:
        for (off0, off1) in self._bus_segments(op):
            a0, a1 = quantize(start + off0), quantize(start + off1)
            for (s, e) in self._bus_resv:
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
        lock = self._latch.get((die, plane))
        if not lock:
            return False
        if t0 < lock.start_us:
            return False
        if lock.end_us is None:
            return True
        return t0 < lock.end_us

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
            lock = self._latch.get((die, plane))
            if not lock or not self._is_locked_at(die, plane, start):
                return False
            kinds = [lock.kind]
            for k in kinds:
                groups = groups_by_latch.get(k, [])
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
        t0 = quantize(max(start_hint, self._earliest_planescope(die, scope, plane_set)))
        base = self._op_base(op)
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
        return t0

    def reserve(self, txn: _Txn, op: Any, targets: List[Address], scope: Scope, duration_us: Optional[float] = None) -> Reservation:
        die = targets[0].die
        plane_set = [t.plane for t in targets]
        dur = float(duration_us) if duration_us is not None else self._total_duration(op)
        start = quantize(max(txn.now_us, self._earliest_planescope(die, scope, plane_set)))
        end = quantize(start + dur)
        base = self._op_base(op)
        if not self._planescope_ok(die, scope, plane_set, start, end):
            return Reservation(False, "planescope", op, targets, None, None)
        if not self._bus_ok(op, start):
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
                    txn.latch_locks[(t.die, t.plane)] = _Latch(start_us=end, end_us=None, kind=latch_kind)
            elif base in ("ONESHOT_PROGRAM_LSB", "ONESHOT_PROGRAM_CSB", "ONESHOT_PROGRAM_MSB"):
                # die-wide program latch applied to all planes in die as plane-scoped entries
                for p in range(self.planes):
                    txn.latch_locks[(die, p)] = _Latch(start_us=end, end_us=None, kind=latch_kind)
        st_list = [(getattr(s, "name", "STATE"), float(getattr(s, "dur_us", 0.0))) for s in getattr(op, "states", [])]
        for t in targets:
            txn.st_ops.append((t.die, t.plane, base, st_list, start))
        return Reservation(True, None, op, targets, start, end)

    def commit(self, txn: _Txn) -> None:
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
        for k, v in txn.latch_locks.items():
            self._latch[k] = v
        for (die, plane, base, st_list, start) in txn.st_ops:
            self._st.reserve_op(die, plane, base, st_list, start)

    def rollback(self, txn: _Txn) -> None:
        return

    def release_on_dout_end(self, targets: List[Address], now_us: float) -> None:
        for t in targets:
            self._latch.pop((t.die, t.plane), None)

    def release_on_exec_msb_end(self, die: int, now_us: float) -> None:
        """Release program-related latches after ONESHOT_PROGRAM_MSB_23h or ONESHOT_PROGRAM_EXEC_MSB completion for a die."""
        for p in range(self.planes):
            self._latch.pop((die, p), None)

    def op_state(self, die: int, plane: int, at_us: float) -> Optional[str]:
        return self._st.state_at(die, plane, quantize(at_us))

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

    def snapshot(self) -> Dict[str, Any]:
        return {
            "avail": dict(self._avail),
            "plane_resv": {k: list(v) for k, v in self._plane_resv.items()},
            "bus_resv": list(self._bus_resv),
            "excl_global": [ExclWindow(w.start, w.end, w.scope, w.die, set(w.tokens)) for w in self._excl_global],
            "excl_die": {d: [ExclWindow(w.start, w.end, w.scope, w.die, set(w.tokens)) for w in lst] for d, lst in self._excl_die.items()},
            "latch": {k: _Latch(v.start_us, v.end_us, v.kind) for k, v in self._latch.items()},
            "timeline": [(seg.die, seg.plane, seg.op_base, seg.state, seg.start_us, seg.end_us) for lst in self._st.by_plane.values() for seg in lst],
        }

    def restore(self, snap: Dict[str, Any]) -> None:
        self._avail = dict(snap.get("avail", {}))
        self._plane_resv = {tuple(k) if not isinstance(k, tuple) else k: list(v) for k, v in snap.get("plane_resv", {}).items()}
        self._bus_resv = list(snap.get("bus_resv", []))
        self._excl_global = [ExclWindow(w.start, w.end, w.scope, w.die, set(w.tokens)) for w in snap.get("excl_global", [])]
        self._excl_die = {int(d): [ExclWindow(w.start, w.end, w.scope, w.die, set(w.tokens)) for w in lst] for d, lst in snap.get("excl_die", {}).items()}
        self._latch = {tuple(k) if not isinstance(k, tuple) else k: _Latch(v.start_us, v.end_us, getattr(v, "kind", "LATCH_ON_READ")) for k, v in snap.get("latch", {}).items()}
        self._st = _StateTimeline()
        for (die, plane, op_base, state, s0, s1) in snap.get("timeline", []):
            self._st._insert_plane((die, plane), _StateInterval(die, plane, op_base, state, s0, s1))
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
        if base in ("READ", "READ4K", "PLANE_READ", "PLANE_READ4K", "CACHE_READ", "PLANE_CACHE_READ", "COPYBACK_READ"):
            return "LATCH_ON_READ"
        if base == "ONESHOT_PROGRAM_LSB":
            return "LATCH_ON_LSB"
        if base == "ONESHOT_PROGRAM_CSB":
            return "LATCH_ON_CSB"
        if base == "ONESHOT_PROGRAM_MSB":
            return "LATCH_ON_MSB"
        return None
