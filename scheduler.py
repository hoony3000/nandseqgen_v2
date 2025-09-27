from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypedDict, Tuple

# Core collaborators
from resourcemgr import ResourceManager, Address, SIM_RES_US, quantize
import proposer as _proposer
from bootstrap import BootstrapController
from event_queue import EventQueue
from typing import Iterable


def get_allowed_program_bases(cfg: Dict[str, Any]) -> frozenset[str]:
    """Return the configured PROGRAM bases (uppercased); empty when unspecified."""
    if not isinstance(cfg, dict):
        return frozenset()
    raw = cfg.get("program_base_whitelist", [])
    return frozenset(str(x).upper() for x in (raw or []) if str(x).strip())


class SchedulerResult(TypedDict):
    success: bool
    hooks_executed: int
    ops_committed: int
    bootstrap_completed: bool
    metrics: Dict[str, Any]


class TickResult(TypedDict):
    committed: int
    rolled_back: bool
    reason: Optional[str]


@dataclass
class _Deps:
    cfg: Dict[str, Any]
    rm: ResourceManager
    addrman: Any
    validator: Optional[Any]
    rng: Any
    logger: Optional[Any]


class Scheduler:

    def __init__(
        self,
        cfg: Dict[str, Any],
        rm: ResourceManager,
        addrman: Any,
        *,
        validator: Optional[Any] = None,
        rng: Optional[Any] = None,
        logger: Optional[Any] = None,
        start_at_us: Optional[float] = None,
        drain_on_exit: bool = False,
    ) -> None:
        # Deterministic RNG (no system time)
        if rng is None:
            import random as _r
            rng = _r.Random(0)
        self._deps = _Deps(cfg=cfg, rm=rm, addrman=addrman, validator=validator, rng=rng, logger=logger)
        # Start simulation time: align with provided start_at_us if any; else 0.0
        try:
            self.now_us: float = quantize(float(start_at_us)) if start_at_us is not None else 0.0
        except Exception:
            self.now_us = 0.0
        self._hooks: int = 0
        self._ops_committed: int = 0
        # metrics (expand in milestones 3-5)
        self.metrics: Dict[str, Any] = {
            "ckpt_success_batches": 0,
            "ckpt_rollback_batches": 0,
            "ckpt_ops_committed": 0,
            "propose_calls": 0,
            "propose_success": 0,
            "last_reason": None,
            # window stats
            "window_us": float(((cfg.get("policies", {}) or {}).get("admission_window", 0.0))),
            "window_attempts": 0,
            "window_exceeds": 0,
            # latencies (logical)
            "sum_wait_us": 0.0,   # sum(start_us - now)
            "sum_exec_us": 0.0,   # sum(end_us - start_us)
            # per-base commits
            "committed_by_base": {},
            # bootstrap
            "bootstrap_active": False,
            "bootstrap_stage": 0,
            # helpful debug
            "last_commit_bases": [],
            # suspend/resume chaining diagnostics
            "chained_stubs": 0,
            "chained_stub_total_us": 0.0,
        }
        # Bootstrap controller (inactive by default unless cfg['bootstrap']['enabled'] is true)
        self._boot = BootstrapController(cfg)
        self.metrics["bootstrap_active"] = self._boot.active()
        self.metrics["bootstrap_stage"] = self._boot.stage()
        # Internal bootstrap stats for thresholds
        self._boot_read_blocks: set[Tuple[int, int]] = set()
        self._boot_erase_blocks: set[Tuple[int, int]] = set()
        self._boot_program_blocks: set[Tuple[int, int]] = set()
        # Event queue (time-ordered)
        self._eq = EventQueue()
        # seed initial queue_refill event
        self._eq.push(self.now_us, "QUEUE_REFILL", payload={})
        # round-robin cursor for QUEUE_REFILL hooks
        self._rr_die: int = 0
        self._rr_plane: int = 0
        # Drain strategy for OP_END events at run boundaries
        self._drain_on_exit: bool = bool(drain_on_exit)
        self.metrics["drain_op_end_processed"] = 0
        # Global op_uid monotonic counter (Alt C resume flow)
        self._op_uid_seq: int = 0
        # Resume diagnostics & OP event export buffers
        self._resumed_op_uids: set[int] = set()
        self._resume_expected_targets: Dict[int, List[Tuple[int, int, int, Optional[int]]]] = {}
        self._op_event_rows: List[Dict[str, Any]] = []
        self._apply_pgm_rows: List[Dict[str, Any]] = []
        self._apply_pgm_call_seq: Dict[Optional[int], int] = {}
        self.metrics["program_resume_page_mismatch"] = 0

    # -----------------
    # Public API
    # -----------------
    def run(self, run_until_us: Optional[int] = None, max_hooks: Optional[int] = None) -> SchedulerResult:
        hooks_budget = float("inf") if max_hooks is None else int(max_hooks)
        stop_reason: Optional[str] = None
        while True:
            if self._hooks >= hooks_budget:
                stop_reason = "hooks_budget"
                break
            if run_until_us is not None and self.now_us >= float(run_until_us):
                stop_reason = "run_until"
                break
            tr = self.tick()
            self._hooks += 1
        if self._drain_on_exit and stop_reason == "run_until":
            self._drain_pending_op_end_events()
        return SchedulerResult(
            success=True,
            hooks_executed=self._hooks,
            ops_committed=self._ops_committed,
            bootstrap_completed=False,  # wired in milestone 5
            metrics=dict(self.metrics),
        )

    def tick(self) -> TickResult:
        """Single scheduling window: propose -> feasible-at -> commit/rollback.
        Keeps determinism by avoiding system clock.
        """
        # Process the next time-slice worth of events with deterministic ordering
        if self._eq.is_empty():
            # Ensure at least a refill hook exists to drive proposal
            self._eq.push(self.now_us, "QUEUE_REFILL", payload={})
        t, batch = self._eq.pop_time_batch()
        self._advance_time_to(t)
        committed_total = 0
        rolled_back_any = False
        reason: Optional[str] = None
        # OP_END -> PHASE_HOOK -> QUEUE_REFILL -> OP_START
        for (_t, _prio, _seq, kind, payload) in batch:
            if kind == "OP_END":
                self._handle_op_end(payload)
        for (_t, _prio, _seq, kind, payload) in batch:
            if kind == "PHASE_HOOK":
                c, rb, rsn = self._propose_and_schedule(self.now_us, payload.get("hook", {"label": "DEFAULT"}))
                committed_total += c
                rolled_back_any = rolled_back_any or rb
                reason = reason or rsn
        for (_t, _prio, _seq, kind, payload) in batch:
            if kind == "QUEUE_REFILL":
                c, rb, rsn = self._propose_and_schedule(self.now_us, self._next_refill_hook())
                # schedule next periodic refill
                self._eq.push(self.now_us + self._queue_period(), "QUEUE_REFILL", payload={})
                committed_total += c
                rolled_back_any = rolled_back_any or rb
                reason = reason or rsn
        for (_t, _prio, _seq, kind, payload) in batch:
            if kind == "OP_START":
                self._handle_op_start(payload)
        # OP_START events are logged for diagnostics; no additional side-effects
        return TickResult(committed=committed_total, rolled_back=rolled_back_any, reason=reason)

    def close(self) -> None:
        return

    # -----------------
    # Internals
    # -----------------
    def _advance_time_to(self, t: float) -> None:
        self.now_us = float(t)

    def _drain_pending_op_end_events(self) -> int:
        """Process any queued OP_END events after the run loop exits."""
        queue = list(self._eq._q)
        if not queue:
            return 0
        drained = 0
        kept: List[Tuple[float, int, int, str, Dict[str, Any]]] = []
        last_time = self.now_us
        for (when, prio, seq, kind, payload) in queue:
            if kind != "OP_END":
                kept.append((when, prio, seq, kind, payload))
                continue
            target_time = float(when)
            if target_time < last_time:
                target_time = last_time
            self._advance_time_to(target_time)
            try:
                self._handle_op_end(payload)
            except Exception:
                # Continue draining other events even if one handler fails
                pass
            drained += 1
            last_time = self.now_us
        self._eq._q = kept
        if drained:
            self.metrics["drain_op_end_processed"] = int(self.metrics.get("drain_op_end_processed", 0)) + drained
        return drained

    # bootstrap progress helpers moved to bootstrap.py

    # -----------------
    # Event queue helpers
    # -----------------
    def _queue_period(self) -> float:
        return float(((self._deps.cfg.get("policies", {}) or {}).get("queue_refill_period_us", 50.0)))

    def _next_op_uid(self) -> int:
        self._op_uid_seq += 1
        return self._op_uid_seq

    def _tracking_axis(self, base: str) -> Optional[str]:
        bb = str(base or "").upper()
        if bb == "ERASE":
            return "ERASE"
        if ("PROGRAM" in bb) and ("SUSPEND" not in bb) and ("RESUME" not in bb) and ("CACHE" not in bb):
            return "PROGRAM"
        return None

    def _topology(self) -> tuple[int, int]:
        topo = (self._deps.cfg.get("topology", {}) or {})
        try:
            dies = int(topo.get("dies", 1))
        except Exception:
            dies = 1
        try:
            planes = int(topo.get("planes", 1))
        except Exception:
            planes = 1
        return (max(1, dies), max(1, planes))

    def _next_refill_hook(self) -> Dict[str, Any]:
        dies, planes = self._topology()
        # build hook from current cursor
        hook = {"label": "DEFAULT", "die": int(self._rr_die), "plane": int(self._rr_plane)}
        # advance plane-major, wrap across dies
        self._rr_plane += 1
        if self._rr_plane >= planes:
            self._rr_plane = 0
            self._rr_die = (self._rr_die + 1) % dies
        return hook

    # -----------------
    # Event handlers
    # -----------------
    def _handle_op_end(self, payload: Dict[str, Any]) -> None:
        base = str(payload.get("base"))
        targets = payload.get("targets") or []
        op_uid = payload.get("op_uid")
        try:
            op_uid_int = int(op_uid) if op_uid is not None else None
        except Exception:
            op_uid_int = None
        try:
            if op_uid_int is not None and self._deps.rm.is_op_suspended(op_uid_int):
                return
        except Exception:
            # Fall back to normal handling if RM lookup fails
            pass
        # Release policies
        if base in ("DOUT", "DOUT4K", "CACHE_READ_END", "PLANE_CACHE_READ_END"):
            self._deps.rm.release_on_dout_end(targets, now_us=self.now_us)
        if base in ("ONESHOT_PROGRAM_MSB_23H", "ONESHOT_PROGRAM_EXEC_MSB"):
            if targets:
                die = int(getattr(targets[0], "die", 0))
            else:
                die = int(payload.get("die", 0))
            self._deps.rm.release_on_exec_msb_end(die, now_us=self.now_us)
        # AddressManager state sync at OP_END for ERASE/PROGRAM families
        try:
            self._am_apply_on_end(
                base=str(payload.get("base")),
                op_name=str(payload.get("op_name", "")),
                targets=targets,
                op_uid=op_uid_int,
            )
        except Exception:
            # Best-effort: ignore AM sync failures to avoid breaking scheduling
            pass
        self._record_op_event_rows(
            payload=payload,
            targets=targets,
            op_uid=op_uid_int,
            event="OP_END",
            triggered_us=float(self.now_us),
            check_expected=True,
        )
        if op_uid_int is not None:
            try:
                self._deps.rm.complete_op(op_uid_int)
            except Exception:
                pass

    def _handle_op_start(self, payload: Dict[str, Any]) -> None:
        targets = payload.get("targets") or []
        op_uid = payload.get("op_uid")
        try:
            op_uid_int = int(op_uid) if op_uid is not None else None
        except Exception:
            op_uid_int = None
        self._record_op_event_rows(
            payload=payload,
            targets=targets,
            op_uid=op_uid_int,
            event="OP_START",
            triggered_us=float(self.now_us),
            check_expected=False,
        )

    def _record_op_event_rows(
        self,
        *,
        payload: Dict[str, Any],
        targets: Iterable[Any],
        op_uid: Optional[int],
        event: str,
        triggered_us: float,
        check_expected: bool,
    ) -> None:
        rows: List[Any] = list(targets or [])
        if not rows:
            return
        op_name = str(payload.get("op_name", ""))
        op_id_raw = payload.get("op_id")
        op_id_val: Optional[int]
        try:
            op_id_val = int(op_id_raw) if op_id_raw is not None else None
        except Exception:
            op_id_val = None
        if op_id_val is None and op_uid is not None:
            op_id_val = op_uid
        resumed_flag = bool(op_uid is not None and op_uid in self._resumed_op_uids)
        if resumed_flag and check_expected and op_uid is not None:
            self._resumed_op_uids.discard(op_uid)
        expected = None
        if check_expected and op_uid is not None:
            expected = self._resume_expected_targets.pop(op_uid, None)
        actual: List[Tuple[int, int, int, Optional[int]]] = []
        for tgt in rows:
            die_v = self._coerce_int(getattr(tgt, "die", payload.get("die", 0)))
            plane_v = self._coerce_int(getattr(tgt, "plane", payload.get("plane", 0)))
            block_v = self._coerce_int(getattr(tgt, "block", payload.get("block", 0)))
            page_attr = getattr(tgt, "page", None)
            page_opt: Optional[int]
            try:
                page_opt = None if page_attr is None else int(page_attr)
            except Exception:
                page_opt = None
            actual.append((die_v, plane_v, block_v, page_opt))
            page_for_csv = page_opt if page_opt is not None else -1
            row = {
                "op_name": op_name,
                "op_id": op_id_val if op_id_val is not None else 0,
                "op_uid": op_uid if op_uid is not None else 0,
                "die": die_v,
                "plane": plane_v,
                "block": block_v,
                "page": page_for_csv,
                "is_resumed": resumed_flag,
                "event": str(event),
                "triggered_us": float(triggered_us),
            }
            self._op_event_rows.append(row)
        if check_expected and expected is not None and sorted(actual) != sorted(expected):
            try:
                self.metrics["program_resume_page_mismatch"] = int(self.metrics.get("program_resume_page_mismatch", 0)) + 1
            except Exception:
                self.metrics["program_resume_page_mismatch"] = 1

    @staticmethod
    def _coerce_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    def drain_op_event_rows(self) -> List[Dict[str, Any]]:
        rows = list(self._op_event_rows)
        self._op_event_rows.clear()
        return rows

    def drain_apply_pgm_rows(self) -> List[Dict[str, Any]]:
        rows = list(self._apply_pgm_rows)
        self._apply_pgm_rows.clear()
        return rows

    def _handle_resume_commit(self, rec: Dict[str, Any]) -> None:
        b = str(rec.get("base", ""))
        bb = b.upper()
        if bb not in ("PROGRAM_RESUME", "ERASE_RESUME"):
            return
        axis = "PROGRAM" if bb == "PROGRAM_RESUME" else "ERASE"

        def _extract_die(obj: Any) -> Optional[int]:
            if obj is None:
                return None
            die_val = getattr(obj, "die", None)
            if die_val is None and isinstance(obj, dict):
                die_val = obj.get("die")
            if die_val is None:
                return None
            try:
                return int(die_val)
            except Exception:
                return None

        die0: Optional[int] = None
        for tgt in rec.get("targets") or []:
            die0 = _extract_die(tgt)
            if die0 is not None:
                break
        if die0 is None:
            hk_die = rec.get("phase_hook_die")
            try:
                die0 = None if hk_die in (None, "None") else int(hk_die)
            except Exception:
                die0 = None
        if die0 is None:
            rm = self._deps.rm
            dies_range = range(getattr(rm, "dies", 0) or 0)
            for cand in dies_range:
                if axis == "PROGRAM" and rm.suspended_ops_program(cand):
                    die0 = cand
                    break
                if axis == "ERASE" and rm.suspended_ops_erase(cand):
                    die0 = cand
                    break
        if die0 is None:
            return
        resume_at = float(rec.get("end_us", self.now_us))
        rm = self._deps.rm
        try:
            meta = rm.resume_from_suspended_axis(int(die0), op_id=None, axis=axis, now_us=resume_at)
        except TypeError:
            # Backward compat: older RM signature without now_us
            meta = rm.resume_from_suspended_axis(int(die0), op_id=None, axis=axis)  # type: ignore[call-arg]
        except Exception:
            meta = None
        if meta is None:
            err = None
            try:
                last_err = getattr(rm, "last_resume_error", None)
                if callable(last_err):
                    err = last_err()
                else:
                    err = last_err
            except Exception:
                err = None
            if isinstance(err, dict) and err:
                reason = err.get("reason")
                msg = (
                    f"[resume] reapply failed axis={axis} base={b} die={die0} op_id={err.get('op_id')} "
                    f"reason={reason} start_hint={err.get('start_hint_us')}"
                )
                _proposer._log(msg)
            return
        op_uid = getattr(meta, "op_id", None)
        if op_uid is None:
            return
        payload = {
            "base": meta.base,
            "op_name": meta.op_name,
            "targets": list(meta.targets),
            "op_uid": op_uid,
        }
        try:
            op_uid_int = int(op_uid)
        except Exception:
            op_uid_int = None
        if op_uid_int is not None:
            self._resumed_op_uids.add(op_uid_int)
            exp: List[Tuple[int, int, int, Optional[int]]] = []
            for tgt in payload["targets"]:
                die_v = int(getattr(tgt, "die", 0))
                plane_v = int(getattr(tgt, "plane", 0))
                block_v = int(getattr(tgt, "block", 0))
                page_attr = getattr(tgt, "page", None)
                page_v: Optional[int]
                try:
                    page_v = None if page_attr is None else int(page_attr)
                except Exception:
                    page_v = None
                exp.append((die_v, plane_v, block_v, page_v))
            self._resume_expected_targets[op_uid_int] = exp
        self._eq.push(float(meta.end_us), "OP_END", payload=payload)

    def _am_apply_on_end(
        self,
        base: str,
        op_name: str,
        targets: Iterable[Address],
        *,
        op_uid: Optional[int] = None,
    ) -> None:
        """Apply ERASE/PROGRAM effects to AddressManager on OP_END and log PROGRAM commits."""
        d = self._deps
        am = getattr(d, "addrman", None)
        if am is None:
            return
        # Determine op celltype from cfg when available
        def _celltype_from_cfg(cfg: Dict[str, Any], name: str) -> str:
            try:
                ct = ((cfg.get("op_names", {}) or {}).get(name or "", {}) or {}).get("celltype")
                return "TLC" if ct in (None, "None", "NONE") else str(ct)
            except Exception:
                return "TLC"

        b = str(base or "").upper()
        # Only handle ERASE and PROGRAM-like bases (final-step commit whitelist for PROGRAM)
        is_erase = (b == "ERASE")
        is_program_like = ("PROGRAM" in b) and ("SUSPEND" not in b) and ("RESUME" not in b)
        if not (is_erase or is_program_like):
            return
        t_list = list(targets or [])
        if not t_list:
            return
        try:
            import numpy as np  # type: ignore
        except Exception:
            return
        # Build ndarray (#, 1, 3): (die, block, page)
        rows = []
        mode = _celltype_from_cfg(d.cfg, op_name)
        # Whitelist of PROGRAM bases that are allowed to commit addr_state at OP_END
        allowed_program_commit = set(get_allowed_program_bases(d.cfg))
        # Optional runtime extension via cfg.features.extra_allowed_program_bases
        try:
            feats = (d.cfg.get("features", {}) or {})
            extra = feats.get("extra_allowed_program_bases", []) or []
            for x in extra:
                try:
                    allowed_program_commit.add(str(x).upper())
                except Exception:
                    continue
        except Exception:
            pass
        is_program_commit = b in allowed_program_commit
        should_log_apply = is_program_commit and hasattr(am, "apply_pgm")
        log_rows: List[Dict[str, Any]] = []
        call_seq: Optional[int] = None
        resume_flag = bool(op_uid is not None and op_uid in self._resumed_op_uids)
        for t in t_list:
            die = self._coerce_int(getattr(t, "die", 0))
            block = self._coerce_int(getattr(t, "block", 0))
            plane = self._coerce_int(getattr(t, "plane", 0))
            page_attr = getattr(t, "page", None)
            try:
                page_raw = None if page_attr is None else int(page_attr)
            except Exception:
                page_raw = None
            page = 0 if (is_erase or page_raw is None) else int(page_raw)
            rows.append([die, block, page])
            # Log PROGRAM commits per target when apply_pgm executes
            if should_log_apply:
                if call_seq is None:
                    key = op_uid
                    prev = self._apply_pgm_call_seq.get(key, 0)
                    call_seq = prev + 1
                    self._apply_pgm_call_seq[key] = call_seq
                log_rows.append(
                    {
                        "triggered_us": float(self.now_us),
                        "op_uid": op_uid if op_uid is not None else 0,
                        "op_name": str(op_name),
                        "base": b,
                        "celltype": mode,
                        "die": die,
                        "plane": plane,
                        "block": block,
                        "page": page_raw if page_raw is not None else -1,
                        "resume": resume_flag,
                        "call_seq": call_seq,
                    }
                )
        if not rows:
            return
        addrs = np.array(rows, dtype=int).reshape(-1, 1, 3)
        if log_rows and should_log_apply:
            self._apply_pgm_rows.extend(log_rows)
        if is_erase and hasattr(am, "apply_erase"):
            am.apply_erase(addrs, mode=mode)
        elif should_log_apply:
            am.apply_pgm(addrs, mode=mode)

    # -----------------
    # Propose and schedule
    # -----------------
    def _propose_and_schedule(self, now: float, hook: Dict[str, Any]) -> Tuple[int, bool, Optional[str]]:
        d = self._deps
        self.metrics["propose_calls"] += 1
        cfg_used = d.cfg
        if self._boot.active():
            cfg_used = self._boot.overlay_cfg(d.cfg)
            self.metrics["bootstrap_stage"] = self._boot.stage()
            self.metrics["bootstrap_active"] = True
        batch = _proposer.propose(now, hook=hook, cfg=cfg_used, res_view=d.rm, addr_sampler=d.addrman, rng=d.rng)
        if not batch:
            self.metrics["last_reason"] = "no_candidate"
            return (0, False, "no_candidate")

        # Feature: Skip Delay in proposal — do not reserve/commit or emit events; advance to next hook.
        def _skip_delay_enabled(cfg: Dict[str, Any]) -> bool:
            try:
                return bool(((cfg.get("features", {}) or {}).get("skip_delay_in_proposal", True)))
            except Exception:
                return True

        try:
            first = batch.ops[0] if getattr(batch, "ops", None) else None
            if first is not None and str(getattr(first, "op_name", "")) == "Delay" and _skip_delay_enabled(cfg_used):
                self.metrics["last_reason"] = "skip_delay"
                return (0, False, "skip_delay")
        except Exception:
            # Best-effort guard; fall through to normal scheduling if any error
            pass

        # Admission window and atomic reservation
        W = float((d.cfg.get("policies", {}) or {}).get("admission_window", 0.0))
        txn = d.rm.begin(now)
        ok_all = True
        reserved_any = False
        resv_records: List[Dict[str, Any]] = []
        self.metrics["window_attempts"] += 1
        # Phase key used for this proposal (Option B propagation)
        pk: Optional[str] = None
        try:
            if getattr(batch, "metrics", None):
                m = batch.metrics or {}
                v = m.get("phase_key")
                if v is not None:
                    pk = str(v)
        except Exception:
            pk = None

        # Capture propose-time context for analysis/export only
        hook_die = hook.get("die") if isinstance(hook, dict) else None
        hook_plane = hook.get("plane") if isinstance(hook, dict) else None
        hook_label = hook.get("label") if isinstance(hook, dict) else None

        # Reset last reserved records for observability/testing
        self.metrics["last_reserved_records"] = []

        for idx, p in enumerate(batch.ops):
            op = _proposer._build_op(d.cfg, p.op_name, p.targets)
            instant = _is_instant_base(d.cfg, p.base)
            # Admission window is enforced only for the first op in the batch.
            # Proposer already guarantees the first op is within window; follow that contract here.
            if idx == 0 and (not instant) and W > 0 and p.start_us >= (now + W):
                ok_all = False
                self.metrics["last_reason"] = "window_exceed"
                self.metrics["window_exceeds"] += 1
                break
            r = d.rm.reserve(txn, op, p.targets, p.scope)
            # Debug: print validity snapshot and outcome
            try:
                snap = getattr(d.rm, "last_validation", None)
                lv = snap() if callable(snap) else None
                failed = (lv or {}).get("failed_rule") if isinstance(lv, dict) else None
                # print(f"[reserve] base={p.base} instant={instant} start_hint={now:.3f} -> ok={r.ok} reason={r.reason} start={r.start_us} end={r.end_us} failed_rule={failed}")
            except Exception:
                pass
            # print(f"{p.op_name}, {p.base}, {p.targets}, {p.scope} -> {r}") # DEBUG
            if not r.ok:
                ok_all = False
                self.metrics["last_reason"] = f"reserve_fail:{r.reason}"
                break
            reserved_any = True
            rec: Dict[str, Any] = {
                "base": p.base,
                "op_name": p.op_name,
                "targets": list(p.targets),
                "scope": p.scope,
                "start_us": float(r.start_us or p.start_us),
                "end_us": float(r.end_us or (p.start_us)),
                "op": op,
                # propose-time key as reported by proposer
                "phase_key": pk,
                # proposal-time context (analysis/export only)
                "propose_now": float(now),
                "phase_hook_die": (None if hook_die is None else int(hook_die)),
                "phase_hook_plane": (None if hook_plane is None else int(hook_plane)),
                "phase_hook_label": (None if hook_label is None else str(hook_label)),
            }
            # Propagate inherit hints (e.g., celltype) for exporter
            try:
                meta = getattr(p, "meta", None)
                if isinstance(meta, dict):
                    ih = meta.get("inherit_hints")
                    if isinstance(ih, dict):
                        rec["inherit_hints"] = dict(ih)
                        ct = ih.get("celltype")
                        if ct not in (None, "None", "NONE"):
                            rec["celltype_hint"] = str(ct)
            except Exception:
                pass
            # Reserved-time phase key normalization (feature-guarded, prioritize instant bases)
            try:
                feats = (d.cfg.get("features", {}) or {})
                guard = feats.get("phase_key_used_reserved_time", True)
            except Exception:
                guard = True
            if guard and instant:
                try:
                    t0_used = float(rec["start_us"])
                    # use first target's die/plane as the reference
                    if p.targets:
                        die0 = int(p.targets[0].die)
                        plane0 = int(p.targets[0].plane)
                    else:
                        die0 = int(hook_die) if hook_die is not None else 0
                        plane0 = int(hook_plane) if hook_plane is not None else 0
                    used_key = d.rm.phase_key_at(die0, plane0, t0_used)
                    rec["phase_key_used"] = used_key
                except Exception:
                    # best-effort: skip if RM lacks API or errors
                    pass
            axis = self._tracking_axis(rec["base"])
            rec["_tracking_axis"] = axis
            rec["op_uid"] = self._next_op_uid() if axis else None
            resv_records.append(rec)
            # Public metrics: expose a thin copy for observability/tests
            try:
                pub = {
                    "base": rec["base"],
                    "op_name": rec["op_name"],
                    "start_us": rec["start_us"],
                    "end_us": rec["end_us"],
                    "phase_key": rec.get("phase_key"),
                    "phase_key_used": rec.get("phase_key_used"),
                }
                self.metrics["last_reserved_records"].append(pub)
            except Exception:
                pass
            # accumulate logical latencies
            self.metrics["sum_wait_us"] += max(0.0, float(p.start_us) - now)
            self.metrics["sum_exec_us"] += max(0.0, float((r.end_us or p.start_us)) - float(p.start_us))
            # Ensure sequential reservation within a transaction: advance txn.now_us to the
            # end of the just-reserved op so follow-ups cannot overlap the current one.
            # This keeps READ -> DOUT strictly ordered and avoids die-level multi exclusion.
            try:
                if r.end_us is not None:
                    txn.now_us = quantize(float(r.end_us))
            except Exception:
                # Best-effort; if quantize or end_us fails, keep txn.now_us unchanged
                pass

        if ok_all and reserved_any:
            d.rm.commit(txn)
            nops = len(resv_records)
            self.metrics["ckpt_success_batches"] += 1
            self.metrics["propose_success"] += 1
            self.metrics["ckpt_ops_committed"] += nops
            self._ops_committed += nops
            bases = []
            for rec in resv_records:
                b = str(rec["base"])
                bases.append(b)
                self.metrics["committed_by_base"][b] = int(self.metrics["committed_by_base"].get(b, 0)) + 1
                # emit OP_START/OP_END and state PHASE_HOOKs
                self._emit_op_events(rec)
            self.metrics["last_commit_bases"] = list(bases)
            # Register ongoing meta for freshly committed ERASE/PROGRAM operations
            try:
                for rec in resv_records:
                    axis = rec.get("_tracking_axis")
                    if not axis:
                        continue
                    tgs = rec.get("targets") or []
                    if not tgs:
                        continue
                    die0 = int(getattr(tgs[0], "die", 0))
                    d.rm.register_ongoing(
                        die=die0,
                        op_id=(None if rec.get("op_uid") is None else int(rec["op_uid"])),
                        op_name=str(rec.get("op_name", "")) if rec.get("op_name") is not None else None,
                        base=str(rec.get("base")),
                        targets=list(tgs),
                        start_us=float(rec.get("start_us")),
                        end_us=float(rec.get("end_us")),
                        scope=rec.get("scope"),
                        op=rec.get("op"),
                    )
            except Exception:
                # Best-effort; ongoing meta is observability aid and should not break scheduling
                pass
            # Bootstrap progress + possible stage advancement
            self._boot.record_committed(bases, batch)
            topo = (d.cfg.get("topology", {}) or {})
            self._boot.maybe_advance(self._boot.progress_snapshot(topo))
            self.metrics["bootstrap_active"] = self._boot.active()
            self.metrics["bootstrap_stage"] = self._boot.stage()
            # Handle resume commits by rescheduling underlying operations (Alt C)
            try:
                for rec in resv_records:
                    self._handle_resume_commit(rec)
            except Exception:
                pass
            return (nops, False, None)
        else:
            d.rm.rollback(txn)
            self.metrics["ckpt_rollback_batches"] += 1
            return (0, True, str(self.metrics.get("last_reason")))

    def _emit_op_events(self, rec: Dict[str, Any]) -> None:
        start = float(rec["start_us"]) 
        end = float(rec["end_us"]) 
        base = str(rec["base"]) 
        op = rec["op"]
        targets = rec["targets"]
        # PHASE_HOOK generation guard per PRD v2 §5.3
        # - Skip ISSUE/DATA_IN/DATA_OUT states for PHASE_HOOKs
        # - Skip PHASE_HOOKs entirely when op base affect_state == false
        #   (OP_START/OP_END events are still emitted as usual)
        SKIP_STATES = {"ISSUE", "DATA_IN", "DATA_OUT"}
        def _affects_state(cfg: Dict[str, Any], b: str) -> bool:
            try:
                return bool(((cfg.get("op_bases", {}) or {}).get(str(b), {}) or {}).get("affect_state", True))
            except Exception:
                return True
        # Policy: enrich READ-family PHASE_HOOK payload with plane_set and targets to guide non‑EPR ops (e.g., DOUT)
        def _hook_targets_enabled(cfg: Dict[str, Any]) -> bool:
            try:
                pol = (cfg.get("policies", {}) or {})
                v = pol.get("hook_targets_enabled", True)
                return bool(True if v in (None, "None") else v)
            except Exception:
                return True
        def _is_read_family(b: str) -> bool:
            bb = str(b or "").upper()
            return bb in {"READ", "READ4K", "PLANE_READ", "PLANE_READ4K", "CACHE_READ", "PLANE_CACHE_READ", "COPYBACK_READ"}
        enrich_hook = _hook_targets_enabled(self._deps.cfg) and _is_read_family(base)
        plane_set_sorted: Optional[List[int]] = None
        hook_targets_payload: Optional[List[tuple]] = None
        if enrich_hook:
            try:
                plane_set_sorted = sorted({int(t.plane) for t in targets})
                cell = _proposer._op_celltype(self._deps.cfg, rec.get("op_name"))
                hook_targets_payload = [
                    (int(t.die), int(t.plane), int(t.block), (None if t.page is None else int(t.page)), cell)
                    for t in targets
                ]
            except Exception:
                plane_set_sorted = None
                hook_targets_payload = None
        # OP_START and OP_END
        targets_payload = list(targets)
        payload_start = {"base": base, "op_name": rec["op_name"], "targets": targets_payload, "op_uid": rec.get("op_uid")}
        payload_end = {"base": base, "op_name": rec["op_name"], "targets": targets_payload, "op_uid": rec.get("op_uid")}
        self._eq.push(start, "OP_START", payload=payload_start)
        self._eq.push(end, "OP_END", payload=payload_end)
        # If this operation does not affect state, do not emit PHASE_HOOKs
        if not _affects_state(self._deps.cfg, base):
            return
        # State-driven PHASE_HOOKs (skip ISSUE)
        t = float(start)
        for seg in getattr(op, "states", []) or []:
            name = getattr(seg, "name", "")
            dur = float(getattr(seg, "dur_us", 0.0))
            if dur <= 0:
                continue
            t_end = t + dur
            # PRD v2 §5.3: skip ISSUE/DATA_IN/DATA_OUT for PHASE_HOOKs
            if str(name).upper() not in SKIP_STATES:
                pre_t = max(t, t_end - max(SIM_RES_US, 0.1 * dur))
                pre_t = quantize(pre_t)
                # one PHASE_HOOK per target plane
                for tgt in targets:
                    hook = {"die": int(tgt.die), "plane": int(tgt.plane), "label": f"{base}.{name}"}
                    if enrich_hook and plane_set_sorted is not None and hook_targets_payload is not None:
                        hook["plane_set"] = list(plane_set_sorted)
                        hook["targets"] = list(hook_targets_payload)
                    self._eq.push(pre_t, "PHASE_HOOK", payload={"hook": hook})
                # also immediately after state end to drive next-stage proposals
                post_t = quantize(t_end + 0.6)
                for tgt in targets:
                    hook = {"die": int(tgt.die), "plane": int(tgt.plane), "label": f"{base}.{name}"}
                    if enrich_hook and plane_set_sorted is not None and hook_targets_payload is not None:
                        hook["plane_set"] = list(plane_set_sorted)
                        hook["targets"] = list(hook_targets_payload)
                    self._eq.push(post_t, "PHASE_HOOK", payload={"hook": hook})
            t = t_end
        return


def _is_instant_base(cfg: Dict[str, Any], base: str) -> bool:
    try:
        return bool(((cfg.get("op_bases", {}) or {}).get(base, {}) or {}).get("instant_resv", False))
    except Exception:
        return False
