from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # optional

from scheduler import Scheduler
import proposer as _proposer
from resourcemgr import ResourceManager, Address, SIM_RES_US, quantize

try:
    from addrman import AddressManager  # type: ignore
    _HAS_NUMPY = True
except Exception:
    AddressManager = None  # type: ignore
    _HAS_NUMPY = False


# ------------------------------
# Minimal AddressManager factory (fallback-friendly)
# ------------------------------
def _mk_addrman(cfg: Dict[str, Any]):
    topo = cfg.get("topology", {}) or {}
    dies = int(topo.get("dies", 1))
    planes = int(topo.get("planes", 1))
    blocks = int(topo.get("blocks_per_die", 128))
    pages = int(topo.get("pages_per_block", 128))
    if AddressManager is not None:
        am = AddressManager(num_planes=planes, num_blocks=blocks, pagesize=pages, num_dies=dies)
        try:
            import numpy as _np  # type: ignore
            am._rng = _np.random.default_rng(1234)
        except Exception:
            pass
        return am

    # Fallback: python-only sampler
    class _SimpleAM:
        def __init__(self, dies: int, planes: int, blocks: int, pages: int):
            self.dies, self.planes, self.blocks, self.pages = dies, planes, blocks, pages

        def _one(self, sel_die=None):
            d = 0 if sel_die is None else (sel_die if isinstance(sel_die, int) else list(sel_die)[0])
            # (die, block, page)
            return [int(d), 0, 0]

        def sample_erase(self, sel_plane=None, mode: str = "SLC", size: int = 1, sel_die=None):
            if isinstance(sel_plane, list):
                return [[self._one(sel_die) for _ in sel_plane]]
            return [[self._one(sel_die)]]

        def sample_pgm(self, sel_plane=None, mode: str = "SLC", size: int = 1, sequential: bool = False, sel_die=None):
            return self.sample_erase(sel_plane=sel_plane, mode=mode, size=size, sel_die=sel_die)

        def sample_read(self, sel_plane=None, mode: str = "SLC", size: int = 1, offset: Optional[int] = None, sequential: bool = False, sel_die=None):
            return self.sample_erase(sel_plane=sel_plane, mode=mode, size=size, sel_die=sel_die)

    return _SimpleAM(dies, planes, blocks, pages)


# ------------------------------
# Instrumented scheduler for logging ops without changing core
# ------------------------------
@dataclass
class _OpRow:
    start_us: float
    end_us: float
    die: int
    plane: int
    block: int
    page: int
    op_name: str
    op_base: str
    source: Optional[str]
    op_uid: int
    phase_key: Optional[str] = None
    # proposal-time context (optional; analysis/export only)
    phase_hook_die: Optional[int] = None
    phase_hook_plane: Optional[int] = None
    phase_hook_label: Optional[str] = None
    phase_key_time: Optional[float] = None


class InstrumentedScheduler(Scheduler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rows: List[_OpRow] = []
        self._next_uid: int = 1

    def _emit_op_events(self, rec: Dict[str, Any]) -> None:  # type: ignore[override]
        # Call parent to enqueue OP_START/OP_END + PHASE_HOOKs
        super()._emit_op_events(rec)
        # Also log a normalized row per target for timeline/exports
        start = float(rec["start_us"])  # type: ignore[index]
        end = float(rec["end_us"])  # type: ignore[index]
        base = str(rec["base"])  # type: ignore[index]
        name = str(rec.get("op_name", base))
        phase_key = rec.get("phase_key")
        # proposal-time context
        pk_time = rec.get("propose_now")
        hk_die = rec.get("phase_hook_die")
        hk_plane = rec.get("phase_hook_plane")
        hk_label = rec.get("phase_hook_label")
        targets: List[Address] = list(rec.get("targets", []) or [])
        uid = self._next_uid
        self._next_uid += 1
        for t in targets:
            self._rows.append(
                _OpRow(
                    start_us=start,
                    end_us=end,
                    die=int(getattr(t, "die", 0)),
                    plane=int(getattr(t, "plane", 0)),
                    block=int(getattr(t, "block", 0)),
                    page=int(getattr(t, "page", 0) if getattr(t, "page", None) is not None else 0),
                    op_name=name,
                    op_base=base,
                    source=None,
                    op_uid=uid,
                    phase_key=(None if phase_key in (None, "None") else str(phase_key)),
                    phase_hook_die=(None if hk_die in (None, "None") else int(hk_die)),
                    phase_hook_plane=(None if hk_plane in (None, "None") else int(hk_plane)),
                    phase_hook_label=(None if hk_label in (None, "None") else str(hk_label)),
                    phase_key_time=(None if pk_time in (None, "None") else float(pk_time)),
                )
            )

    def timeline_rows(self) -> List[Dict[str, Any]]:
        return [r.__dict__.copy() for r in self._rows]


# ------------------------------
# CSV helpers (PRD §3 family)
# ------------------------------
def _date_stamp() -> str:
    # yymmdd
    return datetime.now().strftime("%y%m%d")


def _run_id_str(i: int) -> str:
    # 1-based, 7 digits like 0000001
    return f"{i:07d}"


def _ensure_dir(p: str) -> None:
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)


def _csv_write(path: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    _ensure_dir(path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def export_operation_timeline(rows: List[Dict[str, Any]], rm: ResourceManager, *, out_dir: str, run_idx: int) -> str:
    # PRD 3.3 fields: start,end,die,plane,block,page,op_name,op_base,source,op_uid,op_state
    # Extended: phase_key_used, phase_key_virtual (proposal-time used and virtual keys)
    out_rows: List[Dict[str, Any]] = []
    for r in rows:
        die = int(r["die"])  # type: ignore[index]
        plane = int(r["plane"])  # type: ignore[index]
        start = float(r["start_us"])  # type: ignore[index]
        # Used: prefer the exact phase_key used by proposer; fallback to RM lookup
        used_fk = r.get("phase_key")
        if used_fk is None or str(used_fk).strip() == "":
            # Fallback to RM state at start
            op_state = rm.op_state(die, plane, start) or "NONE"
            phase_key_used = str(op_state)
        else:
            op_state = str(used_fk)
            phase_key_used = str(used_fk)
        # Virtual: derive proposal-time virtual key using preserved context when available
        d_ctx = r.get("phase_hook_die")
        p_ctx = r.get("phase_hook_plane")
        t_ctx = r.get("phase_key_time")
        vd = int(d_ctx) if d_ctx not in (None, "None") else die
        vp = int(p_ctx) if p_ctx not in (None, "None") else plane
        vt = float(t_ctx) if t_ctx not in (None, "None") else start
        phase_key_virtual = rm.phase_key_at(int(vd), int(vp), float(vt))
        out_rows.append(
            {
                "start": float(r["start_us"]),
                "end": float(r["end_us"]),
                "die": die,
                "plane": plane,
                "block": int(r["block"]),
                "page": int(r["page"]),
                "op_name": str(r["op_name"]),
                "op_base": str(r["op_base"]),
                "source": r.get("source"),
                "op_uid": int(r["op_uid"]),
                "op_state": str(op_state),
                "phase_key_used": str(phase_key_used),
                "phase_key_virtual": str(phase_key_virtual),
            }
        )
    # Sort primarily by op_uid for consumer friendliness
    out_rows.sort(key=lambda x: (int(x.get("op_uid", 0)), x["start"], x["die"], x["plane"], x["block"]))
    fname = f"operation_timeline_{_date_stamp()}_{_run_id_str(run_idx+1)}.csv"
    path = os.path.join(out_dir, fname)
    _csv_write(path, out_rows, [
        "start", "end", "die", "plane", "block", "page", "op_name", "op_base", "source", "op_uid", "op_state",
        "phase_key_used", "phase_key_virtual",
    ])
    return path


def export_op_state_timeline(rm: ResourceManager, rows: Optional[List[Dict[str, Any]]] = None, *, out_dir: str, run_idx: int) -> str:
    # From RM snapshot timeline: (die, plane, op_base, state, start_us, end_us)
    snap = rm.snapshot()
    segs = snap.get("timeline", []) or []
    out_rows: List[Dict[str, Any]] = []
    for (die, plane, base, state, s0, s1) in segs:
        dur = float(s1) - float(s0)
        out_rows.append(
            {
                "start": float(s0),
                "end": float(s1),
                "die": int(die),
                "plane": int(plane),
                "op_state": f"{str(base)}.{str(state)}",
                "lane": f"d{int(die)}-p{int(plane)}",
                "op_name": str(base),
                "duration": dur,
            }
        )
    # If operation rows are provided, derive a stable op_uid per segment for sorting
    if rows is not None:
        idx: Dict[Tuple[int, int, str], List[Tuple[float, float, int]]] = {}
        for r in rows:
            die = int(r["die"])  # type: ignore[index]
            plane = int(r["plane"])  # type: ignore[index]
            base = str(r["op_base"])  # type: ignore[index]
            idx.setdefault((die, plane, base), []).append((float(r["start_us"]), float(r["end_us"]), int(r["op_uid"])) )
        for k in idx:
            idx[k].sort(key=lambda t: (t[0], t[1]))

        def _uid_for(seg: Dict[str, Any]) -> int:
            die = int(seg["die"])
            plane = int(seg["plane"])
            base = str(seg.get("op_name", ""))  # op_name here is base per exporter
            s0 = float(seg["start"]) 
            cand = idx.get((die, plane, base))
            if not cand:
                return 0
            # Find the op whose window covers the segment start
            # Assumption: no overlap on same (die,plane) for same base
            lo, hi = 0, len(cand)
            while lo < hi:
                mid = (lo + hi) // 2
                if cand[mid][0] <= s0:
                    lo = mid + 1
                else:
                    hi = mid
            i = max(0, lo - 1)
            if 0 <= i < len(cand):
                st, en, uid = cand[i]
                if st <= s0 < en:
                    return uid
            # fallback: first overlapping
            for (st, en, uid) in cand:
                if st < seg["end"] and s0 < en:
                    return uid
            return 0

        out_rows.sort(key=lambda x: (_uid_for(x), x["start"], x["die"], x["plane"]))
    else:
        out_rows.sort(key=lambda x: (x["die"], x["plane"], x["start"]))
    fname = f"op_state_timeline_{_date_stamp()}_{_run_id_str(run_idx+1)}.csv"
    path = os.path.join(out_dir, fname)
    _csv_write(path, out_rows, [
        "start", "end", "die", "plane", "op_state", "lane", "op_name", "duration",
    ])
    return path


def export_address_touch_count(rows: List[Dict[str, Any]], cfg: Dict[str, Any], *, out_dir: str, run_idx: int) -> str:
    # PRD 3.2 fields: op_base,cell_type,die,block,page,count
    # Derive cell_type from cfg.op_names[op_name].celltype when available
    counts: Dict[Tuple[str, str, int, int, int], int] = {}
    def _canon(base: str) -> Optional[str]:
        b = base.upper()
        if b.startswith("PROGRAM") or b.startswith("COPYBACK_PROGRAM"):
            return "PROGRAM"
        if b.startswith("READ") or b.startswith("PLANE_READ") or b.startswith("CACHE_READ") or b.startswith("COPYBACK_READ"):
            return "READ"
        return None

    for r in rows:
        base = str(r["op_base"]).upper()
        cbase = _canon(base)
        if cbase not in ("PROGRAM", "READ"):
            continue
        name = str(r["op_name"])  # e.g., concrete op_name
        spec = (cfg.get("op_names", {}) or {}).get(name, {})
        cell = spec.get("celltype", None)
        cell_s = str(cell) if cell not in (None, "None") else "NONE"
        key = (cbase, cell_s, int(r["die"]), int(r["block"]), int(r["page"]))
        counts[key] = counts.get(key, 0) + 1
    out_rows = [
        {
            "op_base": k[0],
            "cell_type": k[1],
            "die": k[2],
            "block": k[3],
            "page": k[4],
            "count": v,
        }
        for k, v in sorted(counts.items(), key=lambda kv: (kv[0][2], kv[0][3], kv[0][4], kv[0][0], kv[0][1]))
    ]
    fname = f"address_touch_count_{_date_stamp()}_{_run_id_str(run_idx+1)}.csv"
    path = os.path.join(out_dir, fname)
    _csv_write(path, out_rows, ["op_base", "cell_type", "die", "block", "page", "count"])
    return path


def export_op_state_name_input_time_count(rows: List[Dict[str, Any]], rm: ResourceManager, *, out_dir: str, run_idx: int) -> str:
    # PRD 3.5: op_state, op_name, input_time, count
    # Extended: phase_key_used, phase_key_virtual (used in grouping for consistency)
    # input_time = (t - state_start) / (state_end - state_start) in [0,1]
    # Build an index for quick lookup of state segment covering t
    snap = rm.snapshot()
    segs = snap.get("timeline", []) or []
    # Index by (die, plane)
    idx: Dict[Tuple[int, int], List[Tuple[float, float, str, str]]] = {}
    for (die, plane, base, state, s0, s1) in segs:
        idx.setdefault((int(die), int(plane)), []).append((float(s0), float(s1), str(base), str(state)))
    for k in idx.keys():
        idx[k].sort(key=lambda x: x[0])

    def _lookup(d: int, p: int, t: float) -> Optional[Tuple[float, float, str, str]]:
        lst = idx.get((d, p))
        if not lst:
            return None
        # binary search by start
        lo, hi = 0, len(lst)
        while lo < hi:
            mid = (lo + hi) // 2
            if lst[mid][0] <= t:
                lo = mid + 1
            else:
                hi = mid
        i = max(0, lo - 1)
        seg = lst[i]
        if seg[0] <= t < seg[1]:
            return seg
        return None

    def _q(v: float, decimals: int = 2) -> float:
        return round(max(0.0, min(1.0, v)), decimals)

    # Grouping key: (phase_key_used, phase_key_virtual, op_name, input_time)
    cnt: Dict[Tuple[str, str, str, float], int] = {}
    for r in rows:
        d = int(r["die"])  # type: ignore[index]
        p = int(r["plane"])  # type: ignore[index]
        t = float(r["start_us"])  # type: ignore[index]
        seg = _lookup(d, p, t)
        if not seg:
            continue
        s0, s1, base, state = seg
        dur = max(SIM_RES_US, float(s1) - float(s0))
        it = _q((t - s0) / dur)
        # phase_key_used: prefer proposer phase_key; fallback to RM segment key
        used_fk = r.get("phase_key")
        if used_fk is None or str(used_fk).strip() == "":
            used = f"{base}.{state}"
        else:
            used = str(used_fk)
        # phase_key_virtual: derive via RM.phase_key_at using proposal-time context when available
        d_ctx = r.get("phase_hook_die")
        p_ctx = r.get("phase_hook_plane")
        t_ctx = r.get("phase_key_time")
        vd = int(d_ctx) if d_ctx not in (None, "None") else d
        vp = int(p_ctx) if p_ctx not in (None, "None") else p
        vt = float(t_ctx) if t_ctx not in (None, "None") else t
        virt = rm.phase_key_at(int(vd), int(vp), float(vt))
        key = (used, virt, str(r["op_name"]), it)
        cnt[key] = cnt.get(key, 0) + 1

    out_rows = [
        {
            "op_state": k[0],  # preserve legacy column (equals phase_key_used)
            "phase_key_used": k[0],
            "phase_key_virtual": k[1],
            "op_name": k[2],
            "input_time": k[3],
            "count": v,
        }
        for k, v in sorted(cnt.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1], kv[0][3]))
    ]
    fname = f"op_state_name_input_time_count_{_date_stamp()}_{_run_id_str(run_idx+1)}.csv"
    path = os.path.join(out_dir, fname)
    _csv_write(path, out_rows, ["op_state", "phase_key_used", "phase_key_virtual", "op_name", "input_time", "count"])
    return path


def export_operation_sequence(rows: List[Dict[str, Any]], cfg: Dict[str, Any], *, out_dir: str, run_idx: int) -> str:
    # PRD 3.1: seq,time,op_id,op_name,op_uid,payload (JSON)
    # Group by op_uid; time is min start_us among targets; payload schema from cfg[payload_by_op_base]
    by_uid: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        by_uid.setdefault(int(r["op_uid"]), []).append(r)
    out: List[Dict[str, Any]] = []
    pef = (cfg.get("payload_by_op_base", {}) or {})
    opcode_map: Dict[str, int] = (cfg.get("pattern_export", {}) or {}).get("opcode_map", {}) or {}
    tscale = float(((cfg.get("pattern_export", {}) or {}).get("time", {}) or {}).get("scale", 1.0))
    rdec = int(((cfg.get("pattern_export", {}) or {}).get("time", {}) or {}).get("round_decimals", 3))

    for uid, grp in sorted(by_uid.items(), key=lambda kv: (min(float(r["start_us"]) for r in kv[1]), kv[0])):
        t0 = min(float(r["start_us"]) for r in grp)
        name = str(grp[0]["op_name"]) if grp else "NOP"
        base = str(grp[0]["op_base"]) if grp else "NOP"
        # Determine payload fields for base
        fields = [str(x) for x in pef.get(base, ["die", "pl", "block", "page"])]
        # Determine celltype from op_name spec if requested
        cell = None
        try:
            cell = ((cfg.get("op_names", {}) or {}).get(name, {}) or {}).get("celltype")
        except Exception:
            cell = None
        # Compose targets sorted by plane, block, page
        grp2 = sorted(grp, key=lambda r: (int(r["plane"]), int(r["block"]), int(r["page"])) )
        payload_list: List[Dict[str, Any]] = []
        for r in grp2:
            item = {"die": int(r["die"]), "pl": int(r["plane"]), "block": int(r["block"]), "page": int(r["page"]) }
            if "celltype" in fields:
                item["celltype"] = (None if cell in (None, "None") else str(cell))
            # filter by requested fields order
            ordered = {k: item.get(k) for k in fields if k in item}
            payload_list.append(ordered)
        payload_json = json.dumps(payload_list, ensure_ascii=False, separators=(",", ":"))
        out.append(
            {
                "seq": len(out) + 1,
                "time": round(t0 * tscale, rdec),
                "op_id": int(opcode_map.get(name, 0)),
                "op_name": name,
                "op_uid": uid,
                "payload": payload_json,
            }
        )

    fname = f"operation_sequence_{_date_stamp()}_{_run_id_str(run_idx+1)}.csv"
    path = os.path.join(out_dir, fname)
    _csv_write(path, out, ["seq", "time", "op_id", "op_name", "op_uid", "payload"])
    return path


def export_phase_proposal_counts(rows: List[Dict[str, Any]], rm: ResourceManager, *, out_dir: str, run_idx: int) -> str:
    """Aggregate proposal-time (used vs. virtual) phase keys per (die,plane,op_name).

    - used: row.phase_key if present else "DEFAULT"
    - virtual: rm.phase_key_at(d, p, t) using proposal-time context when available
    - choose (d,p,t) from row: prefer phase_hook_(die,plane) and phase_key_time; fallback to row's (die,plane,start_us)
    - output columns: phase_key_used, phase_key_virtual, die, plane, propose_time, op_name, count
    - sort by: (die, plane, op_name, phase_key_used, phase_key_virtual)
    """
    from typing import Tuple as _T

    agg: Dict[_T[str, str, int, int, str], Dict[str, Any]] = {}

    for r in rows:
        used = str(r.get("phase_key") or "DEFAULT")
        # proposal-time context
        d = r.get("phase_hook_die")
        p = r.get("phase_hook_plane")
        t = r.get("phase_key_time")
        if d in (None, "None") or p in (None, "None"):
            d = int(r["die"])  # type: ignore[index]
            p = int(r["plane"])  # type: ignore[index]
        else:
            d = int(d)
            p = int(p)
        if t in (None, "None"):
            t = float(r["start_us"])  # type: ignore[index]
        else:
            t = float(t)
        t = quantize(t)
        virt = rm.phase_key_at(int(d), int(p), float(t))
        key = (str(used), str(virt), int(d), int(p), str(r["op_name"]))
        cur = agg.get(key)
        if cur is None:
            agg[key] = {"count": 1, "propose_time": float(t)}
        else:
            cur["count"] = int(cur.get("count", 0)) + 1
            # keep earliest proposal time as representative
            cur["propose_time"] = min(float(cur.get("propose_time", float(t))), float(t))

    out_rows: List[Dict[str, Any]] = []
    for (used, virt, d, p, name), info in sorted(agg.items(), key=lambda kv: (kv[0][2], kv[0][3], kv[0][4], kv[0][0], kv[0][1])):
        out_rows.append(
            {
                "phase_key_used": used,
                "phase_key_virtual": virt,
                "die": int(d),
                "plane": int(p),
                "propose_time": float(info.get("propose_time", 0.0)),
                "op_name": name,
                "count": int(info.get("count", 0)),
            }
        )

    fname = f"phase_proposal_counts_{_date_stamp()}_{_run_id_str(run_idx+1)}.csv"
    path = os.path.join(out_dir, fname)
    _csv_write(path, out_rows, [
        "phase_key_used", "phase_key_virtual", "die", "plane", "propose_time", "op_name", "count",
    ])
    return path


def save_snapshot(rm: ResourceManager, *, out_dir: str, run_idx: int) -> str:
    snap = rm.snapshot()

    # Make JSON-friendly (no tuple keys, no dataclasses, no sets)
    def _excl_to_dict(w: Any) -> Dict[str, Any]:
        return {
            "start": float(getattr(w, "start", 0.0)),
            "end": float(getattr(w, "end", 0.0)),
            "scope": str(getattr(w, "scope", "")),
            "die": (None if getattr(w, "die", None) is None else int(getattr(w, "die", 0))),
            "tokens": sorted(list(getattr(w, "tokens", []) or [])),
        }

    def _latch_to_dict(l: Any) -> Dict[str, Any]:
        return {
            "start_us": float(getattr(l, "start_us", 0.0)),
            "end_us": (None if getattr(l, "end_us", None) is None else float(getattr(l, "end_us", 0.0))),
            "kind": str(getattr(l, "kind", "")),
        }

    snap2: Dict[str, Any] = {}
    # avail: {(d,p): t}
    if isinstance(snap.get("avail"), dict):
        snap2["avail"] = [
            {"die": int(k[0]), "plane": int(k[1]), "avail_us": float(v)}
            for k, v in snap["avail"].items()
        ]
    # plane_resv: {(d,p): [(s,e), ...]}
    if isinstance(snap.get("plane_resv"), dict):
        snap2["plane_resv"] = [
            {"die": int(k[0]), "plane": int(k[1]), "intervals": [[float(a), float(b)] for (a, b) in snap["plane_resv"][k]]}
            for k in snap["plane_resv"].keys()
        ]
    # bus_resv: list of (s,e)
    snap2["bus_resv"] = [[float(a), float(b)] for (a, b) in (snap.get("bus_resv") or [])]
    # excl_global: list of ExclWindow
    snap2["excl_global"] = [_excl_to_dict(w) for w in (snap.get("excl_global") or [])]
    # excl_die: {die: [ExclWindow,...]}
    if isinstance(snap.get("excl_die"), dict):
        snap2["excl_die"] = [
            {"die": int(d), "windows": [_excl_to_dict(w) for w in lst]}
            for d, lst in snap["excl_die"].items()
        ]
    # latch: {(d,p): _Latch}
    if isinstance(snap.get("latch"), dict):
        snap2["latch"] = [
            {"die": int(k[0]), "plane": int(k[1]), **_latch_to_dict(v)}
            for k, v in snap["latch"].items()
        ]
    # timeline
    snap2["timeline"] = [
        [int(d), int(p), str(b), str(st), float(s0), float(s1)]
        for (d, p, b, st, s0, s1) in (snap.get("timeline") or [])
    ]
    # runtime flags
    snap2["odt_disabled"] = bool(snap.get("odt_disabled", False))
    snap2["cache_read"] = [
        [int(d), int(p), str(k), float(s0), (None if s1 is None else float(s1)), (None if c in (None, "None") else str(c))]
        for (d, p, k, s0, s1, c) in (snap.get("cache_read") or [])
    ]
    snap2["cache_program"] = [
        [int(d), str(k), float(s0), (None if s1 is None else float(s1)), (None if c in (None, "None") else str(c))]
        for (d, k, s0, s1, c) in (snap.get("cache_program") or [])
    ]
    # suspend/ongoing/suspended ops can be large; include directly (JSON-friendly already)
    snap2["suspend_states"] = snap.get("suspend_states")
    snap2["ongoing_ops"] = snap.get("ongoing_ops")
    snap2["suspended_ops"] = snap.get("suspended_ops")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"snapshots/state_snapshot_{ts}_{_run_id_str(run_idx+1)}.json"
    path_tmp = os.path.join(out_dir, fname + ".tmp")
    path = os.path.join(out_dir, fname)
    _ensure_dir(path)
    with open(path_tmp, "w", encoding="utf-8") as f:
        json.dump(snap2, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(path_tmp, path)
    return path


# ------------------------------
# Runner
# ------------------------------
def _load_cfg(path: str) -> Dict[str, Any]:
    if yaml is None:
        # Fallback: empty config; will be minimally completed later
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        # If loading fails, keep going with minimal defaults
        return {}


def _ensure_min_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    c = dict(cfg or {})
    topo = dict(c.get("topology", {}) or {})
    topo.setdefault("dies", 1)
    topo.setdefault("planes", 2)
    topo.setdefault("blocks_per_die", 128)
    topo.setdefault("pages_per_block", 128)
    c["topology"] = topo

    pol = dict(c.get("policies", {}) or {})
    pol.setdefault("admission_window", 1.0)
    pol.setdefault("queue_refill_period_us", 50.0)
    pol.setdefault("topN", 4)
    pol.setdefault("epsilon_greedy", 0.0)
    pol.setdefault("maxplanes", 4)
    pol.setdefault("maxtry_candidate", 4)
    pol.setdefault("sequence_gap", 1.0)
    c["policies"] = pol

    # phase_conditional: ensure DEFAULT has at least one candidate
    pc = dict(c.get("phase_conditional", {}) or {})
    c["phase_conditional"] = pc

    # payload mapping minimal default for ERASE
    pb = dict(c.get("payload_by_op_base", {}) or {})
    pb.setdefault("ERASE", ["die", "plane", "block", "page", "celltype"])
    c["payload_by_op_base"] = pb

    # pattern export minimal defaults
    pe = dict(c.get("pattern_export", {}) or {})
    pe.setdefault("time", {"scale": 1.0, "round_decimals": 3, "out_col": "time"})
    pe.setdefault("columns", ["seq", "time", "op_id", "op_name", "op_uid", "payload"])
    pe.setdefault("opcode_map", {})
    c["pattern_export"] = pe
    return c


def _apply_overrides(
    cfg: Dict[str, Any], *, admission_window: Optional[float], bootstrap_enabled: bool, validate_pc: Optional[bool] = None
) -> Dict[str, Any]:
    c = dict(cfg)
    pol = dict(c.get("policies", {}) or {})
    if admission_window is not None:
        pol["admission_window"] = float(admission_window)
    if validate_pc is not None:
        pol["validate_pc"] = bool(validate_pc)
    c["policies"] = pol
    b = dict(c.get("bootstrap", {}) or {})
    b["enabled"] = bool(bootstrap_enabled)
    c["bootstrap"] = b
    # Provide minimal pattern_export defaults (non-failing)
    pe = dict(c.get("pattern_export", {}) or {})
    pe.setdefault("time", {"scale": 1.0, "round_decimals": 3, "out_col": "time"})
    pe.setdefault("columns", ["seq", "time", "op_id", "op_name", "op_uid", "payload"])
    pe.setdefault("opcode_map", {})
    c["pattern_export"] = pe
    return c


def run_once(cfg: Dict[str, Any], rm: ResourceManager, am: Any, *, run_until_us: float, rng_seed: Optional[int]) -> Tuple[InstrumentedScheduler, Dict[str, Any]]:
    import random
    rng = random.Random(int(rng_seed) if rng_seed is not None else 42)
    sched = InstrumentedScheduler(cfg=cfg, rm=rm, addrman=am, rng=rng)
    res = sched.run(run_until_us=run_until_us)
    return sched, res


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run NAND scheduler and export PRD outputs")
    p.add_argument("--config", default="config.yaml", help="Path to YAML config")
    p.add_argument("--run-until", "-t", type=float, default=100000.0, help="Simulation time per run (us)")
    p.add_argument("--num-runs", "-n", type=int, default=1, help="Number of runs")
    p.add_argument("--bootstrap", dest="bootstrap", action="store_true", help="Enable bootstrap (first run only if num_runs>1)")
    p.add_argument("--no-bootstrap", dest="bootstrap", action="store_false", help="Disable bootstrap")
    p.set_defaults(bootstrap=False)
    p.add_argument("--admission-window", type=float, default=None, help="Override policies.admission_window (us)")
    p.add_argument("--seed", type=int, default=42, help="Base RNG seed")
    p.add_argument("--out-dir", default="out", help="Output directory root")
    p.add_argument("--pc-demo", choices=["erase-only","mix","pgm-read"], default=None,
                   help="Override phase_conditional DEFAULT to a preset: erase-only | mix | pgm-read")
    p.add_argument("--autofill-pc", action="store_true", help="Autofill phase_conditional from CFG (PRD policy)")
    p.add_argument(
        "--refresh-op-state-probs",
        action="store_true",
        help="Force rebuild and overwrite op_state_probs.yaml from CFG (ignores existing file)",
    )
    p.add_argument("--op-state-probs", dest="op_state_probs", default=None,
                   help="Path to op_state_probs.yaml (load if present; write when auto-filled)")
    p.add_argument("--validate-pc", action="store_true", help="Validate CFG.phase_conditional and log summary")
    args = p.parse_args(argv)

    cfg = _load_cfg(args.config)
    cfg = _ensure_min_cfg(cfg)
    # Default: load op_state_probs.yaml if present; otherwise autofill when empty or only DEFAULT
    try:
        import cfg_autofill as _pc
        probs_path = args.op_state_probs
        if not probs_path:
            # default next to the config file
            import os as _os
            cfg_dir = _os.path.dirname(args.config)
            if not cfg_dir:
                cfg_dir = "."
            probs_path = _os.path.join(cfg_dir, "op_state_probs.yaml")
        force_rebuild = bool(getattr(args, "autofill_pc", False)) or bool(getattr(args, "refresh_op_state_probs", False))
        cfg = _pc.ensure_from_file_or_build(cfg, path=probs_path, seed=int(args.seed), force=force_rebuild)
    except Exception:
        pass
    topo = cfg.get("topology", {}) or {}
    dies = int(topo.get("dies", 1))
    planes = int(topo.get("planes", 1))

    # Shared state across runs for continuity
    rm = ResourceManager(cfg=cfg, dies=dies, planes=planes)
    am = _mk_addrman(cfg)
    # Register address-dependent policy (EPR) when available
    try:
        if hasattr(rm, "register_addr_policy") and hasattr(am, "check_epr"):
            rm.register_addr_policy(am.check_epr)  # type: ignore[attr-defined]
    except Exception:
        pass

    # Per run
    for i in range(args.num_runs):
        enable_boot = bool(args.bootstrap) and (i == 0) and (args.num_runs > 1)
        cfg_run = _apply_overrides(
            cfg,
            admission_window=args.admission_window,
            bootstrap_enabled=enable_boot,
            validate_pc=bool(args.validate_pc),
        )
        # Optional phase_conditional demo override
        if args.pc_demo is not None:
            pc = dict(cfg_run.get("phase_conditional", {}) or {})
            if args.pc_demo == "erase-only":
                pc["DEFAULT"] = {"Block_Erase_SLC": 1.0}
            elif args.pc_demo == "mix":
                pc["DEFAULT"] = {
                    "Block_Erase_SLC": 0.5,
                    "All_WL_Dummy_Program": 0.25,
                    "4KB_Page_Read_confirm_LSB": 0.25,
                }
            elif args.pc_demo == "pgm-read":
                pc["DEFAULT"] = {
                    "All_WL_Dummy_Program": 0.6,
                    "4KB_Page_Read_confirm_LSB": 0.4,
                }
            cfg_run["phase_conditional"] = pc
        # Enable proposer file logging per run
        try:
            os.makedirs(args.out_dir, exist_ok=True)
            log_path = os.path.join(args.out_dir, f"proposer_debug_{_date_stamp()}_{_run_id_str(i+1)}.log")
            _proposer.enable_file_log(log_path)
        except Exception:
            pass

        # Optional: validate phase_conditional keys and distributions
        if args.validate_pc:
            try:
                _proposer.validate_phase_conditional(cfg_run)
            except Exception:
                pass

        seed_i = (int(args.seed) + i) if args.seed is not None else None
        sched, res = run_once(cfg_run, rm, am, run_until_us=float(args.run_until), rng_seed=seed_i)

        # Collect timeline rows
        rows = sched.timeline_rows()

        # Exports (PRD §3)
        os.makedirs(args.out_dir, exist_ok=True)
        op_timeline = export_operation_timeline(rows, rm, out_dir=args.out_dir, run_idx=i)
        opstate_timeline = export_op_state_timeline(rm, rows=rows, out_dir=args.out_dir, run_idx=i)
        touch_cnt = export_address_touch_count(rows, cfg, out_dir=args.out_dir, run_idx=i)
        opstate_name_input_time = export_op_state_name_input_time_count(rows, rm, out_dir=args.out_dir, run_idx=i)
        op_sequence = export_operation_sequence(rows, cfg, out_dir=args.out_dir, run_idx=i)
        phase_counts = export_phase_proposal_counts(rows, rm, out_dir=args.out_dir, run_idx=i)
        snap_path = save_snapshot(rm, out_dir=args.out_dir, run_idx=i)

        # Brief run summary
        print("Run", i + 1, "results:")
        print("  hooks=", res.get("hooks_executed"), "ops_committed=", res.get("ops_committed"))
        cmb = (res.get("metrics", {}) or {}).get("committed_by_base", {})
        if cmb:
            print("  committed_by_base:")
            for k in sorted(cmb.keys()):
                print(f"    - {k}: {cmb[k]}")
        print("  files:")
        for pth in (op_sequence, touch_cnt, op_timeline, opstate_timeline, opstate_name_input_time, phase_counts, snap_path):
            print("   -", pth)

    return 0


if __name__ == "__main__":
    sys.exit(main())
