# viz_tools.py
# Visualization + Validation tools for NAND sequence generator
# - TimelineLogger: per-target logging (die, plane, block, page) with op metadata
# - plot_gantt / plot_gantt_by_die
# - plot_block_page_sequence_3d / plot_block_page_sequence_3d_by_die
# - validate_timeline: rule-checker (duplicates, read-before-program, busy overlaps)
#
# Requirements: pandas, matplotlib

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
import pandas as pd
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
import matplotlib.patches as mpatches
import os, json, math, csv

# -------------------- Colors (can be customized) --------------------
DEFAULT_COLORS = {
    "ERASE":   "#e74c3c",  # red
    "PROGRAM": "#f1c40f",  # yellow
    "READ":    "#2ecc71",  # green
    "DOUT":    "#9b59b6",  # purple
    "SR":      "#3498db",  # blue

    # aliases -> fallback to base
    "SIN_READ": "#2ecc71",
    "MUL_READ": "#2ecc71",
    "SIN_PROGRAM": "#f1c40f",
    "MUL_PROGRAM": "#f1c40f",
    "SIN_ERASE": "#e74c3c",
    "MUL_ERASE": "#e74c3c",
}

def _color_for(kind: str) -> str:
    return DEFAULT_COLORS.get(kind, DEFAULT_COLORS.get(kind.split("_")[-1], "#7f8c8d"))

def _overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
    return not (a1 <= b0 or b1 <= a0)

# -------------------- Logger --------------------

@dataclass
class TimelineLogger:
    """
    Per-target timeline logger.
    - Scheduler에서, 예약 직후 _schedule_operation(...) 안에서 log_op(...)를 호출하세요.
    - READ는 label_for_read를 사용하면 SIN_READ/MUL_READ로 라벨링됩니다.
    - ERASE/SR도 page=0으로 기록하여 주소 스키마를 통일합니다.
    """
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def log_op(self, op, start_us: float, end_us: float, label_for_read: Optional[str] = None):
        label = op.base.name
        if label_for_read:
            label = label_for_read  # READ:MUL_/SIN_, PROGRAM/ERASE도 MUL_/SIN_ 반영
        for t in op.targets:
            page = t.page if (t.page is not None) else 0  # ERASE/SR도 page=0로 강제
            self.rows.append({
                "start_us": float(start_us),
                "end_us":   float(end_us),
                "die":      int(t.die),
                "plane":    int(t.plane),
                "block":    int(t.block),
                "page":     int(page),
                "op_name":     label,          # alias-aware (SIN_/MUL_ for multi/single)
                "op_base": op.base.name,  # ERASE/PROGRAM/READ/DOUT/SR/...
                "source":   op.meta.get("source"),
                "op_uid":   int(op.meta.get("uid", -1)),
                "arity":    int(op.meta.get("arity", 1)),
                "phase_key_used": op.meta.get("phase_key_used"),
                "state_key_at_schedule": op.meta.get("state_key_at_schedule"),
            })

    def to_dataframe(self) -> pd.DataFrame:
        df = pd.DataFrame(self.rows)
        if not df.empty:
            df = df.sort_values(["die", "block", "start_us", "end_us"]).reset_index(drop=True)
            # sequence numbers
            df["seq_per_block"] = df.groupby(["die", "block"]).cumcount() + 1
            df["seq_global_die"] = df.groupby(["die"]).cumcount() + 1
            df["dur_us"] = df["end_us"] - df["start_us"]
        return df

# -------------------- Gantt --------------------

def plot_gantt(df: pd.DataFrame,
               die: Optional[int] = None,
               blocks: Optional[Sequence[int]] = None,
               kinds: Sequence[str] = ("ERASE","PROGRAM","READ","DOUT","SR"),
               linewidth: float = 6.0,
               figsize: Tuple[float,float] = (12, 4),
               title: Optional[str] = None):
    """
    Gantt-like timeline: y=(die, block), x=time(us), color=op_base.
    """
    if df.empty:
        print("[plot_gantt] empty dataframe"); return
    d = df if die is None else df[df["die"] == die]
    if blocks is not None:
        d = d[d["block"].isin(blocks)]
    d = d[d["op_base"].isin(kinds)]
    if d.empty:
        if die is None:
            print("[plot_gantt] no rows with given filters"); return
        else:
            print(f"[plot_gantt] no rows for die={die} with given filters"); return

    # order by (die, block) numerically (ascending)
    pairs = sorted(d[["die","block"]].drop_duplicates().itertuples(index=False, name=None))
    ymap = { (di, bl): i for i, (di, bl) in enumerate(pairs) }

    plt.figure(figsize=figsize)
    for _, r in d.iterrows():
        y = ymap[(int(r["die"]), int(r["block"]))]
        c = _color_for(r["op_base"])
        plt.hlines(y, r["start_us"], r["end_us"], colors=c, linewidth=linewidth)

    plt.yticks(list(ymap.values()), [f"d{di}/blk{bl}" for (di, bl) in pairs])
    plt.xlabel("time (us)")
    plt.ylabel("die/block")
    if title:
        plt.title(title)
    else:
        if die is None:
            plt.title("All dies timeline (Gantt)")
        else:
            plt.title(f"Die {die} timeline (Gantt)")

    handles = [mpatches.Patch(color=_color_for(k), label=k) for k in kinds]
    plt.legend(handles=handles, loc="upper right", frameon=False)
    plt.grid(axis="x", linestyle="--", alpha=0.35)
    plt.tight_layout()
    plt.show()

def plot_gantt_by_die(df: pd.DataFrame,
                      dies: Optional[Sequence[int]] = None,
                      **kwargs):
    """
    Convenience:
      - dies가 None이면 모든 die를 한 Figure에 함께 그립니다(die=None 전달).
      - dies가 명시되면, 각 die별로 개별 Figure를 그립니다(루프).
    kwargs는 plot_gantt에 그대로 전달됩니다.
    """
    if df.empty:
        print("[plot_gantt_by_die] empty dataframe"); return
    if dies is None:
        plot_gantt(df, die=None, **kwargs)
        return
    for d in dies:
        plot_gantt(df, die=d, **kwargs)

# -------------------- 3D plot (block–page–order) --------------------

def plot_block_page_sequence_3d(df: pd.DataFrame,
                                die: int = 0,
                                kinds: Sequence[str] = ("ERASE","PROGRAM","READ"),
                                z_mode: str = "per_block",    # "per_block" | "global_die"
                                blocks: Optional[Sequence[int]] = None,
                                figsize: Tuple[float,float] = (11, 7),
                                title: Optional[str] = None,
                                draw_lines: bool = True,
                                line_color: str = "#7f8c8d",
                                line_alpha: float = 0.6,
                                line_width: float = 1.2):
    """
    3D scatter: x=block, y=page, z=order, color=op_base.
    - z_mode:
        * "per_block": seq_per_block (블록별 순번)
        * "global_die": seq_global_die (die 전체 순번)
    - 동일 block 내 포인트들을 얇은 선으로 연결하여 '흐름'을 표시(draw_lines=True).
    """
    if df.empty:
        print("[plot_block_page_sequence_3d] empty dataframe"); return

    d = df[df["die"] == die].copy()
    if blocks is not None:
        d = d[d["block"].isin(blocks)]
    d = d[d["op_base"].isin(kinds)]
    if d.empty:
        print(f"[plot_block_page_sequence_3d] no rows for die={die} with given filters"); return

    zcol = "seq_per_block" if z_mode == "per_block" else "seq_global_die"

    fig = plt.figure(figsize=figsize)
    ax = fig.add_subplot(111, projection="3d")

    # scatter by kind
    for k in kinds:
        dk = d[d["op_base"] == k]
        if dk.empty: continue
        ax.scatter(dk["block"], dk["page"], dk[zcol],
                   s=28, depthshade=True, c=_color_for(k), label=k)

    # connect within each block to show flow
    if draw_lines:
        for b, grp in d.groupby("block"):
            # 시간 순 또는 z 순으로 정렬 (둘 다 동일 성질)
            grp = grp.sort_values(["start_us", "end_us"]).copy()
            ax.plot(grp["block"].values, grp["page"].values, grp[zcol].values,
                    color=line_color, alpha=line_alpha, linewidth=line_width)

    ax.set_xlabel("block")
    ax.set_ylabel("page")
    ax.set_zlabel("order" + (" (per block)" if z_mode=="per_block" else " (per die)"))
    if title:
        ax.set_title(title)
    else:
        ax.set_title(f"Die {die} block–page–order (3D)")

    if blocks is not None:
        ax.set_xticks(sorted(set(blocks)))
    ax.margins(x=0.01, y=0.02, z=0.02)
    ax.legend(loc="upper left")
    plt.tight_layout()
    plt.show()

def plot_block_page_sequence_3d_by_die(df: pd.DataFrame,
                                       dies: Optional[Sequence[int]] = None,
                                       **kwargs):
    """
    각 die에 대해 3D plot을 별도로 그립니다(루프).
    kwargs는 plot_block_page_sequence_3d에 그대로 전달됩니다.
    """
    if df.empty:
        print("[plot_block_page_sequence_3d_by_die] empty dataframe"); return
    all_dies = sorted(df["die"].unique()) if dies is None else dies
    for d in all_dies:
        plot_block_page_sequence_3d(df, die=d, **kwargs)

# -------------------- Validator --------------------

@dataclass
class ValidationIssue:
    kind: str
    die: Optional[int]
    block: Optional[int]
    page: Optional[int]
    t0: float
    t1: float
    detail: str
    plane: Optional[int] = None

def _spec_offsets_fixed(op_specs: Dict[str, Any]) -> Dict[str, Dict[str, Tuple[float,float]]]:
    """
    op_specs -> 각 op_base별로 { 'total': (0,total), 'core_busy': (t0,t1) or None }를 계산.
    (고정 duration만 지원)
    """
    out: Dict[str, Dict[str, Tuple[float,float]]] = {}
    for base, spec in op_specs.items():
        t = 0.0; cb = None
        for s in spec["states"]:
            dur = s["dist"]["value"] if s["dist"]["kind"] == "fixed" else None
            if dur is None:
                raise ValueError("Validator requires fixed durations in op_specs.")
            s0, s1 = t, t + float(dur)
            if s["name"] == "CORE_BUSY":
                cb = (s0, s1)
            t = s1
        out[base] = {"total": (0.0, t), "core_busy": cb}
    return out

def validate_timeline(df: pd.DataFrame, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Rule checks:
      1) READ_BEFORE_PROGRAM
      2) PROGRAM_DUPLICATE (erase 없이 동일 page 재프로그램)
      3) PROGRAM_ORDER (page 순서 불일치)
      4) DOUT_OVERLAP (global freeze 위반)
      5) CORE_BUSY_DIEWIDE_OVERLAP (PROGRAM/ERASE의 CORE_BUSY 동안 같은 die의 READ/PROGRAM/ERASE 겹침)
      6) MUL_READ_CORE_BUSY_OVERLAP (MUL_READ의 CORE_BUSY 동안 같은 die의 READ/PROGRAM/ERASE 겹침)
      7) SIN_READ_CORE_BUSY_OVERLAP (SIN_READ의 CORE_BUSY 동안 같은 die의 MUL_READ/PROGRAM/ERASE 겹침)
    반환: {"issues": List[ValidationIssue], "counts": {...}}
    """
    issues: List[ValidationIssue] = []
    counts: Dict[str, int] = {}

    if df.empty:
        return {"issues": [], "counts": {}}

    # epsilon to avoid false positives from float rounding (touching edges)
    tu = float(cfg.get("export", {}).get("tu_us", 0.01))  # simulation time unit
    eps = max(1e-9, tu * 1e-3)  # 0.1% of TU or at least 1e-9 us

    # 1~3. 블록 상태를 이벤트 순회로 재구성 (commit은 end_us에 일어남)
    state = {}  # (die,block) -> {"last": int, "committed": set()}
    events = []
    for i, r in df.iterrows():
        events.append((float(r["start_us"]), 0, "start", i))  # end 우선 처리 위해 우선순위 1/0 대신 0/1
        events.append((float(r["end_us"]),   -1, "end",   i))
    # sort by time, then 'end' before 'start'
    events.sort()

    for _, _, etype, idx in events:
        r = df.iloc[idx]
        die, block, page = int(r["die"]), int(r["block"]), int(r["page"])
        base = str(r["op_base"])

        key = (die, block)
        if key not in state:
            state[key] = {"last": -1, "committed": set()}

        if etype == "start":
            if base == "READ":
                if page not in state[key]["committed"]:
                    issues.append(ValidationIssue(
                        kind="READ_BEFORE_PROGRAM", die=die, block=block, page=page,
                        t0=float(r["start_us"]), t1=float(r["end_us"]),
                        detail="READ started before page was programmed/committed",
                        plane=int(r.get("plane", 0))
                    ))
            elif base == "PROGRAM":
                if page in state[key]["committed"]:
                    issues.append(ValidationIssue(
                        kind="PROGRAM_DUPLICATE", die=die, block=block, page=page,
                        t0=float(r["start_us"]), t1=float(r["end_us"]),
                        detail="PROGRAM on a page already committed without prior erase",
                        plane=int(r.get("plane", 0))
                    ))
                # 순서 체크: 기대 = last+1
                expected = state[key]["last"] + 1
                if page != expected:
                    issues.append(ValidationIssue(
                        kind="PROGRAM_ORDER", die=die, block=block, page=page,
                        t0=float(r["start_us"]), t1=float(r["end_us"]),
                        detail=f"PROGRAM order mismatch: expected page {expected}, got {page}",
                        plane=int(r.get("plane", 0))
                    ))

        elif etype == "end":
            if base == "PROGRAM":
                state[key]["committed"].add(page)
                if page > state[key]["last"]:
                    state[key]["last"] = page
            elif base == "ERASE":
                state[key]["committed"].clear()
                state[key]["last"] = -1

    # 4~7. 윈도우 겹침 검증 (고정 duration 전제)
    specs = _spec_offsets_fixed(cfg["op_specs"])

    # CORE_BUSY windows
    def core_busy_window(row) -> Optional[Tuple[float,float]]:
        base = str(row["op_base"])
        spec = specs.get(base)
        if not spec: return None
        cb = spec.get("core_busy")
        if not cb: return None
        s0 = float(row["start_us"]) + cb[0]
        s1 = float(row["start_us"]) + cb[1]
        return (s0, s1)

    # PROGRAM/ERASE CORE_BUSY: 같은 die에서 READ/PROGRAM/ERASE 전체 금지
    diewide_rows = df[df["op_base"].isin(["PROGRAM","ERASE"])]
    all_rpe = df[df["op_base"].isin(["READ","PROGRAM","ERASE"])]
    for _, r in diewide_rows.iterrows():
        win = core_busy_window(r)
        if not win: continue
        s0, s1 = win
        d = int(r["die"])
        # 같은 die의 READ/PROGRAM/ERASE 전 구간과 겹침 검사
        ov = all_rpe[(all_rpe["die"]==d) &
                     (all_rpe.index != r.name) &
                     (all_rpe["start_us"] < (s1 - eps)) & ((s0 + eps) < all_rpe["end_us"])]
        for __, rr in ov.iterrows():
            # 동일 스케줄링으로부터 나온 행(op_uid 동일)은 겹침 무시
            if int(rr.get("op_uid", -2)) == int(r.get("op_uid", -1)):
                continue
            t0 = max(s0, float(rr["start_us"]))
            t1 = min(s1, float(rr["end_us"]))
            if (t1 - t0) <= eps:
                continue
            # 멀티/싱글 풀 라벨 표기(READ는 kind, 그 외는 op_base 기준으로 출력)
            lhs = r.get("op_name") if r["op_base"]=="READ" else r["op_base"]
            rhs = rr.get("op_name") if rr["op_base"]=="READ" else rr["op_base"]
            issues.append(ValidationIssue(
                kind="CORE_BUSY_DIEWIDE_OVERLAP", die=d, block=int(r["block"]), page=int(r.get("page", 0)),
                t0=t0, t1=t1,
                detail=f"{lhs}.CORE_BUSY overlaps with {rhs} on die {d}",
                plane=int(r.get("plane", 0))
            ))

    # MUL_READ CORE_BUSY: 같은 die에서 READ/PROGRAM/ERASE 금지
    mul_rows = df[(df["op_base"]=="READ") & (df["op_name"]=="MUL_READ")]
    for _, r in mul_rows.iterrows():
        win = core_busy_window(r)
        if not win: continue
        s0, s1 = win
        d = int(r["die"])
        ov = all_rpe[(all_rpe["die"]==d) &
                     (all_rpe.index != r.name) &
                     (all_rpe["start_us"] < (s1 - eps)) & ((s0 + eps) < all_rpe["end_us"])]
        for __, rr in ov.iterrows():
            if int(rr.get("op_uid", -2)) == int(r.get("op_uid", -1)):
                continue
            t0 = max(s0, float(rr["start_us"]))
            t1 = min(s1, float(rr["end_us"]))
            if (t1 - t0) <= eps:
                continue
            rhs = rr.get("op_name") if rr["op_base"]=="READ" else rr["op_base"]
            issues.append(ValidationIssue(
                kind="MUL_READ_CORE_BUSY_OVERLAP", die=d, block=int(r["block"]), page=int(r.get("page", 0)),
                t0=t0, t1=t1,
                detail=f"MUL_READ.CORE_BUSY overlaps with {rhs} on die {d}",
                plane=int(r.get("plane", 0))
            ))

    # SIN_READ CORE_BUSY: 같은 die에서 MUL_READ/PROGRAM/ERASE 금지 (SIN_READ는 허용)
    sin_rows = df[(df["op_base"]=="READ") & (df["op_name"]=="SIN_READ")]
    ban_for_sin = df[(df["op_base"].isin(["PROGRAM","ERASE"])) | ((df["op_base"]=="READ") & (df["op_name"]=="MUL_READ"))]
    for _, r in sin_rows.iterrows():
        win = core_busy_window(r)
        if not win: continue
        s0, s1 = win
        d = int(r["die"])
        ov = ban_for_sin[(ban_for_sin["die"]==d) &
                         (ban_for_sin.index != r.name) &
                         (ban_for_sin["start_us"] < (s1 - eps)) & ((s0 + eps) < ban_for_sin["end_us"])]
        for __, rr in ov.iterrows():
            if int(rr.get("op_uid", -2)) == int(r.get("op_uid", -1)):
                continue
            t0 = max(s0, float(rr["start_us"]))
            t1 = min(s1, float(rr["end_us"]))
            if (t1 - t0) <= eps:
                continue
            rhs = rr.get("op_name") if rr["op_base"]=="READ" else rr["op_base"]
            issues.append(ValidationIssue(
                kind="SIN_READ_CORE_BUSY_OVERLAP", die=d, block=int(r["block"]), page=int(r.get("page", 0)),
                t0=t0, t1=t1,
                detail=f"SIN_READ.CORE_BUSY overlaps with {rhs} on die {d}",
                plane=int(r.get("plane", 0))
            ))

    # counts
    for isu in issues:
        counts[isu.kind] = counts.get(isu.kind, 0) + 1

    return {"issues": issues, "counts": counts}

def print_validation_report(report: Dict[str, Any], max_rows: int = 20):
    issues = report.get("issues", [])
    counts = report.get("counts", {})
    print("=== Validation Report ===")
    if not issues:
        print("No issues detected.")
        return
    # summary
    for k, v in sorted(counts.items()):
        print(f"{k:30s}: {v}")
    # sample
    print("\n-- sample --")
    for isu in issues[:max_rows]:
        print(f"[{isu.kind}] die={isu.die} block={isu.block} page={isu.page} "
              f"time=({isu.t0:.2f},{isu.t1:.2f})  {isu.detail}")

def violations_to_dataframe(report: Dict[str, Any]) -> pd.DataFrame:
    rows = []
    for isu in report.get("issues", []):
        rows.append({
            "kind": isu.kind, "die": isu.die, "plane": isu.plane, "block": isu.block, "page": isu.page,
            "t0": isu.t0, "t1": isu.t1, "detail": isu.detail
        })
    return pd.DataFrame(rows)

# -------------------- Pattern Export (ATE CSV) --------------------
def _get_pattern_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    pe = cfg.get("pattern_export")
    if not pe:
        raise ValueError("CFG['pattern_export'] is missing. Add defaults in nandsim_demo.py.")
    return pe

def _alias_name_for_export(op_base: str,
                           kinds_in_group: Sequence[str],
                           arity: int,
                           pe: Dict[str, Any]) -> str:
    # 1) 로그에 SIN_/MUL_ 별칭이 이미 있으면 그걸 사용(동일 base일 때)
    for k in kinds_in_group:
        if isinstance(k, str) and (k.startswith("SIN_") or k.startswith("MUL_")):
            tail = k.split("_", 1)[1] if "_" in k else None
            if tail == op_base:
                return k
    # 2) 없으면 arity 기준으로 별칭 생성 (apply_to 대상에 한함)
    apply_to = set(pe.get("aliasing", {}).get("apply_to", []))
    if op_base in apply_to:
        th = int(pe.get("aliasing", {}).get("mul_threshold", 2))
        return ("MUL_" if int(arity) >= th else "SIN_") + op_base
    return op_base

def pattern_build_ops_from_timeline(df: pd.DataFrame, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    TimelineLogger DataFrame을 '오퍼레이션 1건=CSV 1행'으로 집계.
    - op_uid가 있으면 op_uid로 그룹; 없으면 (die,start_us,end_us,op_base,source)로 보수적으로 그룹
    - time은 그룹의 start_us 최소값에 time.scale/round_decimals 적용
    - payload는 CFG.pattern_export.payload 규칙 적용
    """
    pe = _get_pattern_cfg(cfg)
    if df is None or df.empty:
        return []

    df2 = df.copy()
    use_uid = ("op_uid" in df2.columns) and df2["op_uid"].ge(0).any()
    if use_uid:
        groups = df2.groupby("op_uid", sort=False)
    else:
        key_cols = ["die", "start_us", "end_us", "op_base", "source"]
        for k in key_cols:
            if k not in df2.columns:
                df2[k] = None
        groups = df2.groupby(key_cols, sort=False)

    rows: List[Dict[str, Any]] = []
    tscale = float(pe.get("time", {}).get("scale", 1.0))
    rdec   = int(pe.get("time", {}).get("round_decimals", 3))

    for key, grp in groups:
        base = str(grp["op_base"].iloc[0])
        die  = int(grp["die"].iloc[0]) if "die" in grp.columns else 0
        smin = float(grp["start_us"].min())
        emax = float(grp["end_us"].max())
        arity = int(grp["arity"].max()) if ("arity" in grp.columns and grp["arity"].notna().any()) else int(len(grp))
        kinds_in_group = [str(x) for x in (grp["kind"].unique() if "kind" in grp.columns else [base])]

        op_name = _alias_name_for_export(base, kinds_in_group, arity, pe)
        if base in ("DOUT", "SR"):  # DOUT/SR은 별칭 사용 안 함
            op_name = base

        op_id = pe.get("opcode_map", {}).get(op_name, None)
        time_val = round(smin * tscale, rdec)

        # 타겟 정렬(결정적 순서): plane, block, page
        grp_sorted = grp.sort_values(["plane","block","page"], na_position="last")
        targets: List[Dict[str,int]] = []
        for _, r in grp_sorted.iterrows():
            page = int(r["page"]) if pd.notna(r["page"]) else 0
            targets.append({
                "die": int(r["die"]), "pl": int(r["plane"]),
                "block": int(r["block"]), "page": page
            })

        # payload 스펙
        payload_spec = pe.get("payload", {}).get(op_name, pe.get("payload", {}).get("default", {"kind":"addresses_list"}))
        pkind = payload_spec.get("kind", "addresses_list") if isinstance(payload_spec, dict) else str(payload_spec)
        if pkind == "addresses_first":
            payload_obj = targets[0] if targets else {}
        elif pkind == "nop_rep_only":
            payload_obj = {}  # NOP은 삽입 단계에서 채움
        else:  # "addresses_list"
            payload_obj = targets

        # 그룹 ID
        gid = int(grp.index[0]) if use_uid else hash((die, smin, emax, base))

        rows.append({
            "op_gid": gid,
            "die": die,
            "time_us": time_val,
            "start_us_raw": smin,
            "end_us": emax,
            "op_name": op_name,
            "op_id": op_id,
            "payload_obj": payload_obj,
            "op_base": base,
            "arity": arity,
            "group_df": grp,
        })

    rows.sort(key=lambda r: (r["time_us"], r["op_gid"]))
    return rows

def pattern_maybe_insert_nops(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """인접 오퍼레이션 사이의 간극을 NOP(rep)로 채움."""
    pe = _get_pattern_cfg(cfg)
    nop_cfg = pe.get("nop", {})
    if not nop_cfg or not nop_cfg.get("enable", False) or not rows:
        return list(rows)

    quantum = float(nop_cfg.get("quantum_us", 1.0))
    min_gap = float(nop_cfg.get("min_gap_us", 0.0))
    rdec    = int(pe.get("time", {}).get("round_decimals", 3))
    tscale  = float(pe.get("time", {}).get("scale", 1.0))

    out: List[Dict[str, Any]] = []

    # 시작부 gap
    prev_end = 0.0
    first_start = float(rows[0].get("start_us_raw", rows[0]["time_us"]))
    gap0 = first_start - prev_end
    if gap0 >= min_gap:
        rep = int(math.floor(gap0 / max(quantum, 1e-12)))
        if rep > 0:
            out.append({
                "op_gid": -1, "die": int(rows[0]["die"]),
                "time_us": round(prev_end * tscale, rdec),
                "start_us_raw": prev_end, "end_us": prev_end,
                "op_name": nop_cfg.get("op_name", "NOP"),
                "op_id": int(nop_cfg.get("opcode", 0)),
                "payload_obj": {str(nop_cfg.get("rep_key", "rep")): rep},
                "op_base": "NOP", "arity": 1, "group_df": None
            })

    for i, row in enumerate(rows):
        if i > 0:
            p = rows[i-1]
            prev_end = float(p.get("end_us", p["time_us"]))
            start = float(row.get("start_us_raw", row["time_us"]))
            gap = start - prev_end
            if gap >= min_gap:
                rep = int(math.floor(gap / max(quantum, 1e-12)))
                if rep > 0:
                    out.append({
                        "op_gid": -1, "die": int(row["die"]),
                        "time_us": round(prev_end * tscale, rdec),
                        "start_us_raw": prev_end, "end_us": prev_end,
                        "op_name": nop_cfg.get("op_name", "NOP"),
                        "op_id": int(nop_cfg.get("opcode", 0)),
                        "payload_obj": {str(nop_cfg.get("rep_key", "rep")): rep},
                        "op_base": "NOP", "arity": 1, "group_df": None
                    })
        out.append(row)

    return out

def pattern_preflight(rows: List[Dict[str, Any]], df: pd.DataFrame, cfg: Dict[str, Any]) -> List[str]:
    """출력 전 사전점검: opcode, payload JSON, page_equal_required, 시간 단조 증가."""
    pe = _get_pattern_cfg(cfg)
    errs: List[str] = []
    if not rows:
        return errs

    opt = pe.get("preflight", {})
    req_opcode   = bool(opt.get("require_opcode", True))
    req_json     = bool(opt.get("require_json_payload", True))
    chk_time     = bool(opt.get("time_monotonic", True))
    chk_page_eq  = bool(opt.get("page_equal_required_from_op_specs", True))

    prev_t = None
    for idx, r in enumerate(rows):
        if req_opcode and (r.get("op_id") is None):
            errs.append(f"[row#{idx}] missing opcode for op_name={r.get('op_name')}")
        if req_json:
            try:
                json.dumps(r.get("payload_obj", {}), ensure_ascii=False)
            except Exception as e:
                errs.append(f"[row#{idx}] payload not JSON-serializable: {e}")

        if chk_page_eq and r.get("group_df") is not None:
            base = str(r.get("op_base"))
            spec = cfg.get("op_specs", {}).get(base, {})
            if spec and bool(spec.get("page_equal_required", False)):
                grp = r["group_df"]
                pages = set(int(p) for p in grp["page"].dropna().astype(int).tolist())
                if len(pages) > 1:
                    errs.append(f"[row#{idx}] page_equal_required violation ({r.get('op_name')} pages={sorted(pages)})")

        if chk_time:
            t = float(r.get("time_us", 0.0))
            if prev_t is not None and t < prev_t:
                errs.append(f"[row#{idx}] non-monotonic time: {t} < {prev_t}")
            prev_t = t

    return errs

def pattern_split_rows(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    """행수/시간 기준 분할."""
    pe = _get_pattern_cfg(cfg)
    if not rows:
        return []

    parts: List[List[Dict[str, Any]]] = [rows]
    sp = pe.get("split", {})

    # by_time
    bt = sp.get("by_time", {})
    if bt.get("enable", False):
        chunk = float(bt.get("chunk_us", 0.0))
        if chunk > 0:
            new_parts: List[List[Dict[str, Any]]] = []
            cur: List[Dict[str, Any]] = []
            window_end = rows[0]["time_us"] + chunk
            for r in rows:
                if not cur or r["time_us"] < window_end:
                    cur.append(r)
                else:
                    new_parts.append(cur); cur = [r]
                    window_end = r["time_us"] + chunk
            if cur: new_parts.append(cur)
            parts = new_parts

    # by_rows
    br = sp.get("by_rows", {})
    if br.get("enable", False):
        n = int(br.get("max_rows", 0))
        if n > 0:
            chopped: List[List[Dict[str, Any]]] = []
            for prt in parts:
                for i in range(0, len(prt), n):
                    chopped.append(prt[i:i+n])
            parts = chopped

    return parts

def pattern_rows_to_dataframe(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> pd.DataFrame:
    pe = _get_pattern_cfg(cfg)
    cols = list(pe.get("columns", ["seq","time","op_id","op_name","payload"]))
    time_col = pe.get("time", {}).get("out_col", "time")

    recs = []
    for r in rows:
        recs.append({
            time_col: r["time_us"],
            "op_id": int(r["op_id"] if r.get("op_id") is not None else 0),
            "op_name": str(r["op_name"]),
            "payload": json.dumps(r.get("payload_obj", {}), ensure_ascii=False, separators=(",",":")),
        })
    df_out = pd.DataFrame(recs)
    df_out.insert(0, "seq", range(len(df_out)))
    # 요청된 컬럼 순으로 정렬(없는 컬럼은 무시)
    df_out = df_out[[c for c in cols if c in df_out.columns]]
    return df_out

def pattern_export_csv_parts(dfs: List[pd.DataFrame], out_dir: str, prefix: str) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    paths: List[str] = []
    for i, d in enumerate(dfs):
        path = os.path.join(out_dir, f"{prefix}_{i:03d}.csv")
        d.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)
        paths.append(path)
    return paths

def export_patterns(df: pd.DataFrame, cfg: Dict[str, Any]) -> List[str]:
    """end-to-end: build → NOP → preflight → split → to_csv, 반환은 파일 경로 리스트"""
    pe = _get_pattern_cfg(cfg)
    rows = pattern_build_ops_from_timeline(df, cfg)
    if pe.get("nop", {}).get("enable", False):
        rows = pattern_maybe_insert_nops(rows, cfg)

    errs = pattern_preflight(rows, df, cfg)
    if errs:
        print(f"[pattern_export][preflight] {len(errs)} issue(s) found.")
        for e in errs[:50]:
            print("  -", e)

    parts = pattern_split_rows(rows, cfg)
    dfs   = [pattern_rows_to_dataframe(p, cfg) for p in parts]
    out_dir = pe.get("output_dir", "out_patterns")
    prefix  = pe.get("file_prefix", "pattern")
    return pattern_export_csv_parts(dfs, out_dir, prefix)

def pattern_preview_dataframe(df: pd.DataFrame, cfg: Dict[str, Any], insert_nops: bool = True) -> pd.DataFrame:
    """CSV 쓰지 않고 미리보기 DataFrame만 생성"""
    pe = _get_pattern_cfg(cfg)
    rows = pattern_build_ops_from_timeline(df, cfg)
    if insert_nops and pe.get("nop", {}).get("enable", False):
        rows = pattern_maybe_insert_nops(rows, cfg)
    return pattern_rows_to_dataframe(rows, cfg)

# -------------------- Target Heatmap --------------------
def plot_target_heatmap(df: pd.DataFrame,
                        dies: Optional[Sequence[int]] = None,
                        kinds: Optional[Sequence[str]] = None,
                        title: Optional[str] = None,
                        figsize: Tuple[float,float] = (14, 6),
                        cmap: str = "Reds",
                        vmin: Optional[float] = None,
                        vmax: Optional[float] = None,
                        save_path: Optional[str] = None):
    """
    Heatmap of target address hits.
    - x-axis: die-block (flattened; labeled as "d{die}/blk{block}")
    - y-axis: page
    kinds: 필터할 op_base 목록 (예: ("PROGRAM","READ")); None이면 전체 사용
    dies: None이면 모든 die 포함, 아니면 지정한 die만 포함
    """
    if df is None or df.empty:
        print("[plot_target_heatmap] empty dataframe"); return None, None
    d = df.copy()
    # filter by dies
    if dies is not None:
        d = d[d["die"].isin(list(dies))]
    # filter by base kinds
    if kinds is not None:
        d = d[d["op_base"].isin(list(kinds))]
    if d.empty:
        print("[plot_target_heatmap] no rows after filters"); return None, None
    d = d.dropna(subset=["die","block","page"]).copy()
    d["die"] = d["die"].astype(int)
    d["block"] = d["block"].astype(int)
    d["page"] = d["page"].astype(int)

    # counts per (die, block, page)
    cnt = d.groupby(["die","block","page"], dropna=False).size().reset_index(name="count")
    # pivot: index=page, columns=(die,block)
    cnt = cnt.sort_values(["die","block","page"])  
    pivot = cnt.pivot_table(index="page", columns=["die","block"], values="count", aggfunc="sum", fill_value=0)
    # sort columns numerically
    pivot = pivot.sort_index(axis=1, level=[0,1])

    # labels for x-axis: combined die/block
    x_labels = [f"d{di}/blk{bl}" for (di, bl) in pivot.columns.tolist()]
    y_labels = pivot.index.tolist()

    data = pivot.values.astype(float)

    plt.figure(figsize=figsize)
    im = plt.imshow(data, aspect="auto", origin="lower", cmap=cmap, vmin=vmin, vmax=vmax)
    plt.colorbar(im, fraction=0.046, pad=0.04, label="hits")
    plt.ylabel("page")
    plt.xlabel("die/block")
    if title:
        plt.title(title)
    else:
        plt.title("Target address heatmap")

    # tick density control
    max_xticks = 60
    step = max(1, len(x_labels) // max_xticks if max_xticks >0 else 1)
    xticks = range(0, len(x_labels), step)
    plt.xticks(xticks, [x_labels[i] for i in xticks], rotation=90)
    # y ticks: show reasonable density
    max_yticks = 40
    ystep = max(1, len(y_labels) // max_yticks if max_yticks > 0 else 1)
    yticks = range(0, len(y_labels), ystep)
    plt.yticks(yticks, [y_labels[i] for i in yticks])

    plt.tight_layout()
    if save_path:
        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True) if os.path.dirname(save_path) else None
            plt.savefig(save_path, dpi=140)
            print(f"[plot_target_heatmap] saved to {save_path}")
        except Exception as e:
            print(f"[plot_target_heatmap] save failed: {e}")
    plt.show()
    return plt.gcf(), plt.gca()

# -------------------- Target Address Statistics --------------------
def _weighted_quantile(values: pd.Series, weights: pd.Series, q: float) -> float:
    if values.empty:
        return float("nan")
    d = pd.DataFrame({"v": values.astype(float), "w": weights.astype(float)})
    d = d.sort_values("v")
    cw = d["w"].cumsum()
    cutoff = q * d["w"].sum()
    idx = cw.searchsorted(cutoff, side="right")
    idx = min(max(int(idx), 0), len(d) - 1)
    return float(d.iloc[idx]["v"])

def _gini_from_counts(counts: pd.Series) -> float:
    # Gini for non-negative counts; 0 if uniform/empty
    x = counts.astype(float).values
    if x.size == 0:
        return 0.0
    if x.sum() <= 0:
        return 0.0
    x_sorted = sorted(x)
    n = len(x_sorted)
    cum = 0.0
    for i, xi in enumerate(x_sorted, start=1):
        cum += i * xi
    g = (2.0 * cum) / (n * x.sum()) - (n + 1.0) / n
    return float(max(0.0, min(1.0, g)))

def compute_block_usage_stats(df: pd.DataFrame,
                              kinds: Sequence[str] = ("PROGRAM", "READ")) -> Dict[str, pd.DataFrame]:
    """
    블록/주소 쏠림을 계측하는 통계 집계.
    반환:
      - detail: die, plane, block, op_base, count
      - summary_die: die, op_base, metrics...
      - summary_die_plane: die, plane, op_base, metrics...
    """
    out: Dict[str, pd.DataFrame] = {}
    if df is None or df.empty:
        out["detail"] = pd.DataFrame()
        out["summary_die"] = pd.DataFrame()
        out["summary_die_plane"] = pd.DataFrame()
        return out

    d0 = df.copy()
    d0 = d0[d0["op_base"].isin(list(kinds))]
    if d0.empty:
        out["detail"] = pd.DataFrame()
        out["summary_die"] = pd.DataFrame()
        out["summary_die_plane"] = pd.DataFrame()
        return out

    # detail counts by die/plane/block/op_base
    grp_cols = ["die", "plane", "block", "op_base"]
    detail = d0.groupby(grp_cols, dropna=False).size().reset_index(name="count")
    out["detail"] = detail

    # helper to compute summary metrics on block index distribution
    def _summarize(group: pd.DataFrame, level_cols: List[str]) -> pd.DataFrame:
        rows = []
        if group.empty:
            return pd.DataFrame()
        # limits per die for normalization (block index 0..max)
        # If block max unknown in subset, infer from data
        blk_min = int(group["block"].min()) if not group["block"].empty else 0
        blk_max = int(group["block"].max()) if not group["block"].empty else 0
        blk_span = max(1, blk_max - blk_min)
        for keys, gk in group.groupby(level_cols + ["op_base"], dropna=False):
            # keys could be tuple; normalize
            if not isinstance(keys, tuple):
                keys = (keys,)
            key_dict = {col: val for col, val in zip(level_cols + ["op_base"], keys)}
            cnts = gk.groupby("block").size().rename("count")
            total = int(cnts.sum())
            if total <= 0:
                continue
            # weighted stats
            blocks = cnts.index.to_series()
            weights = cnts.values
            mean_b = float((blocks * cnts.values).sum() / total)
            med_b  = _weighted_quantile(blocks, pd.Series(weights), 0.5)
            p90_b  = _weighted_quantile(blocks, pd.Series(weights), 0.9)
            # normalization to [0,1] w.r.t observed span
            mean_norm = (mean_b - blk_min) / blk_span
            med_norm  = (med_b - blk_min) / blk_span
            p90_norm  = (p90_b - blk_min) / blk_span
            gini      = _gini_from_counts(cnts)
            # head/tail ratio by block index (bottom/top 10%)
            # define thresholds by index percentiles
            thr_low  = blk_min + 0.10 * blk_span
            thr_high = blk_min + 0.90 * blk_span
            head_sum = int(cnts[blocks <= math.floor(thr_low)].sum()) if not cnts.empty else 0
            tail_sum = int(cnts[blocks >= math.ceil(thr_high)].sum()) if not cnts.empty else 0
            head_tail_ratio = (tail_sum / max(head_sum, 1)) if head_sum > 0 else float("inf") if tail_sum > 0 else 0.0
            rows.append({
                **key_dict,
                "total_ops": total,
                "num_blocks": int(cnts.index.nunique()),
                "block_min": blk_min,
                "block_max": blk_max,
                "mean_block": mean_b,
                "mean_norm": mean_norm,
                "median_block": med_b,
                "median_norm": med_norm,
                "p90_block": p90_b,
                "p90_norm": p90_norm,
                "gini": gini,
                "head_tail_ratio": float(head_tail_ratio),
            })
        return pd.DataFrame(rows)

    # per-die summary (aggregate planes)
    die_group = detail.groupby(["die", "block", "op_base"], dropna=False).size().reset_index(name="count")
    summary_die = _summarize(die_group, ["die"]).sort_values(["die", "op_base"]).reset_index(drop=True)
    out["summary_die"] = summary_die

    # per-die-plane summary
    dpl_group = detail.groupby(["die", "plane", "block", "op_base"], dropna=False).size().reset_index(name="count")
    summary_dpl = _summarize(dpl_group, ["die", "plane"]).sort_values(["die", "plane", "op_base"]).reset_index(drop=True)
    out["summary_die_plane"] = summary_dpl

    return out

def save_block_usage_stats(stats: Dict[str, pd.DataFrame], prefix: str = "block_usage") -> List[str]:
    paths: List[str] = []
    detail = stats.get("detail")
    if isinstance(detail, pd.DataFrame) and not detail.empty:
        p = f"{prefix}_detail.csv"; detail.to_csv(p, index=False); paths.append(p)
    s_die = stats.get("summary_die")
    if isinstance(s_die, pd.DataFrame) and not s_die.empty:
        p = f"{prefix}_die_summary.csv"; s_die.to_csv(p, index=False); paths.append(p)
    s_dpl = stats.get("summary_die_plane")
    if isinstance(s_dpl, pd.DataFrame) and not s_dpl.empty:
        p = f"{prefix}_die_plane_summary.csv"; s_dpl.to_csv(p, index=False); paths.append(p)
    return paths

def print_block_usage_summary(stats: Dict[str, pd.DataFrame], max_rows: int = 20):
    s = stats.get("summary_die")
    if s is None or s.empty:
        print("[block_usage] no data")
        return
    print("\n=== Block Usage Summary (per die, by op_base) ===")
    cols = ["die", "op_base", "total_ops", "num_blocks", "mean_norm", "median_norm", "p90_norm", "gini", "head_tail_ratio"]
    s2 = s[cols].copy()
    # pretty rounding
    for c in ("mean_norm", "median_norm", "p90_norm", "gini", "head_tail_ratio"):
        s2[c] = s2[c].astype(float).round(4)
    print(s2.head(max_rows).to_string(index=False))