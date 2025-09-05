from __future__ import annotations

"""
Problem 1-Pager
- 배경: PRD v2 §3에서 정의한 필수 아웃풋(주소 히트 카운트, 오퍼레이션 타임라인, op_state 타임라인,
        op_state x op_name x input_time 분포)을 사람이 빠르게 점검할 수 있도록 간단한 시각화 도구가 필요함.
- 문제: out/ 디렉터리에 CSV만 있고, 즉시 확인 가능한 그림/대시보드가 없음.
- 목표: CSV를 읽어 정적 이미지(PNG)로 저장하는 간단한 CLI를 추가. 각 필수 아웃풋에 맞춘 기본 플롯 제공.
- 비목표: 대화형 서버(Bokeh/Streamlit) 제공, 복잡한 UI. (후속 과제로 가능)
- 제약: 의존성은 requirements.txt 내 패키지(pandas/matplotlib/seaborn)에 국한. 파일 ≤ 300LOC, 함수 ≤ 50LOC.
"""

import argparse
import glob
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


# -----------------------------
# Utils
# -----------------------------

DEF_OUT_DIR = "out"
DEF_VIZ_DIR = os.path.join(DEF_OUT_DIR, "viz")


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def _glob_latest(out_dir: str, prefix: str) -> Optional[str]:
    """Find latest CSV matching prefix_*.csv by lexicographic order of filename."""
    pat = os.path.join(out_dir, f"{prefix}_*.csv")
    files = sorted(glob.glob(pat))
    return files[-1] if files else None


def _save_fig(save_path: str) -> None:
    _ensure_dir(save_path)
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    print(f"[viz] saved: {save_path}")


# -----------------------------
# Operation timeline (PRD §3.3)
# -----------------------------

def plot_operation_gantt(csv_path: str, *, save_path: Optional[str] = None, max_lanes: int = 120) -> None:
    """Gantt-like chart of operation timeline.
    CSV fields: start,end,die,plane,block,page,op_name,op_base,source,op_uid,op_state
    """
    df = pd.read_csv(csv_path)
    if df.empty:
        print("[operation_gantt] empty CSV"); return
    d = df.copy()
    # lanes: die/block → use numeric y-index for robust ordering and inversion
    d["die"] = d["die"].astype("Int64")
    d["block"] = d["block"].astype("Int64")
    d["lane"] = d["die"].astype(str) + "/" + d["block"].astype(str)
    lanes = d[["die", "block", "lane"]].drop_duplicates()
    lanes = lanes.sort_values(["die", "block"]).reset_index(drop=True)
    if len(lanes) > max_lanes:
        lanes = lanes.iloc[:max_lanes].copy()
        d = d.merge(lanes[["die","block"]].assign(_keep=1), on=["die","block"], how="inner")
        print(f"[operation_gantt] truncated lanes to first {max_lanes}")
    lanes = lanes.reset_index(drop=True)
    lanes["yidx"] = lanes.index
    d = d.merge(lanes[["lane","yidx"]], on="lane", how="left")

    # PRD §3.3 점유: op_name
    kinds = sorted(d.get("op_name", d.get("op_base")).astype(str).unique())
    palette = sns.color_palette("tab20", max(len(kinds), 3))
    cmap = {k: palette[i % len(palette)] for i, k in enumerate(kinds)}

    plt.figure(figsize=(12, max(4, 0.3 * max(len(lanes), 1))))
    for _, r in d.iterrows():
        k = str(r.get("op_name", r.get("op_base", "OP")))
        plt.hlines(float(r["yidx"]), float(r["start"]), float(r["end"]), colors=[cmap.get(k, (0.5,0.5,0.5))], linewidth=6.0)
    # y as numeric with labels
    plt.yticks(lanes["yidx"], lanes["lane"].astype(str))
    plt.gca().invert_yaxis()  # top-most lane first
    plt.xlabel("time (us)")
    # PRD §3.3 y label
    plt.ylabel("die-block")
    plt.title("Operation Timeline (Gantt)")
    # legend
    from matplotlib.patches import Patch
    handles = [Patch(color=cmap[k], label=k) for k in kinds][:10]
    if handles:
        plt.legend(handles=handles, title="op_name", loc="upper right", frameon=False)
    plt.grid(axis="x", linestyle="--", alpha=0.35)
    plt.tight_layout()
    if save_path:
        _save_fig(save_path)
    else:
        plt.show()


# -----------------------------
# op_state timeline (PRD §3.4)
# -----------------------------

def plot_op_state_gantt(csv_path: str, *, save_path: Optional[str] = None, max_lanes: int = 120) -> None:
    """Gantt-like chart of op_state per (die,plane) lane.
    CSV fields: start,end,die,plane,op_state,lane,op_name,duration
    """
    df = pd.read_csv(csv_path)
    if df.empty:
        print("[op_state_gantt] empty CSV"); return
    d = df.copy()
    # Per-(die,plane) lanes → numeric y-index; consistent inverted ordering
    d["die"] = d["die"].astype("Int64")
    d["plane"] = d["plane"].astype("Int64")
    lanes = d[["die", "plane"]].drop_duplicates()
    lanes = lanes.sort_values(["die", "plane"]).reset_index(drop=True)
    # Tick label format: die/plane (e.g., "0/3")
    lanes["lane_label"] = lanes["die"].astype(str) + "/" + lanes["plane"].astype(str)
    if len(lanes) > max_lanes:
        lanes = lanes.iloc[:max_lanes].copy()
        d = d.merge(lanes[["die","plane"]].assign(_keep=1), on=["die","plane"], how="inner")
        print(f"[op_state_gantt] truncated lanes to first {max_lanes}")

    # color by op_state label
    labels = sorted(d["op_state"].astype(str).unique())
    palette = sns.color_palette("tab20", max(len(labels), 3))
    cmap = {k: palette[i % len(palette)] for i, k in enumerate(labels)}

    plt.figure(figsize=(12, max(4, 0.3 * max(len(lanes), 1))))
    # attach y indices
    lanes = lanes.reset_index(drop=True)
    lanes["yidx"] = lanes.index
    d = d.merge(lanes[["die","plane","yidx","lane_label"]], on=["die","plane"], how="left")
    for _, r in d.iterrows():
        k = str(r.get("op_state", "STATE"))
        plt.hlines(float(r["yidx"]), float(r["start"]), float(r["end"]), colors=[cmap.get(k, (0.5,0.5,0.5))], linewidth=6.0)
    plt.yticks(lanes["yidx"], lanes["lane_label"].astype(str))
    plt.gca().invert_yaxis()
    plt.xlabel("time (us)")
    # y label per request
    plt.ylabel("die-plane")
    plt.title("op_state Timeline (Gantt)")
    from matplotlib.patches import Patch
    handles = [Patch(color=cmap[k], label=k) for k in labels][:12]
    if handles:
        plt.legend(handles=handles, title="op_state", loc="upper right", frameon=False)
    plt.grid(axis="x", linestyle="--", alpha=0.35)
    plt.tight_layout()
    if save_path:
        _save_fig(save_path)
    else:
        plt.show()


# -----------------------------
# Address touch heatmap (PRD §3.2)
# -----------------------------

def plot_address_touch_heatmap(csv_path: str, *, save_path: Optional[str] = None,
                               kinds: Optional[List[str]] = None) -> None:
    """Heatmap for address touch count.
    CSV fields: op_base,cell_type,die,block,page,count
    kinds: filter op_base (e.g., ["PROGRAM","READ"]) if provided
    """
    df = pd.read_csv(csv_path)
    if df.empty:
        print("[address_heatmap] empty CSV"); return
    d = df.copy()
    if kinds is not None:
        d = d[d["op_base"].isin(kinds)]
    if d.empty:
        print("[address_heatmap] no rows after filter"); return
    d["lane"] = d["die"].astype("Int64").astype(str) + "/" + d["block"].astype("Int64").astype(str)
    # PRD §3.2 heatmap axes: x=lane, y=page
    # pivot: index=page (rows), columns=lane (cols), values=sum(count)
    pvt = d.groupby(["page","lane"], dropna=False)["count"].sum().unstack(fill_value=0)
    # order lanes numerically by die/block
    cols = sorted(pvt.columns, key=lambda s: tuple(map(int, str(s).split("/"))))
    pvt = pvt[cols]
    plt.figure(figsize=(14, 6))
    sns.heatmap(pvt, cmap="Reds", cbar_kws={"label": "hits"})
    plt.xlabel("die-block")
    plt.ylabel("page")
    plt.title("Address Touch Heatmap" + (" (" + ",".join(kinds) + ")" if kinds else ""))
    plt.tight_layout()
    if save_path:
        _save_fig(save_path)
    else:
        plt.show()


# -----------------------------
# op_state x op_name x input_time (PRD §3.5)
# -----------------------------

def plot_state_name_input_time_hist(csv_path: str, *, save_path: Optional[str] = None,
                                    topk_states: int = 6, topk_ops: int = 4) -> None:
    """Histogram-like bar charts per state keyed by input_time.
    CSV fields: op_state,op_name,input_time,count
    Draw top-k states by total count; within each, top-k ops by total.
    """
    df = pd.read_csv(csv_path)
    if df.empty:
        print("[state_input_hist] empty CSV"); return
    d = df.copy()
    d["input_time"] = d["input_time"].astype(float)
    # choose top-k states and ops
    st_order = d.groupby("op_state")["count"].sum().sort_values(ascending=False).head(topk_states).index.tolist()
    d = d[d["op_state"].isin(st_order)]
    # for each state, choose top-k ops
    keep_rows = []
    for st in st_order:
        dd = d[d["op_state"] == st]
        top_ops = dd.groupby("op_name")["count"].sum().sort_values(ascending=False).head(topk_ops).index.tolist()
        keep_rows.append(dd[dd["op_name"].isin(top_ops)])
    d2 = pd.concat(keep_rows, ignore_index=True) if keep_rows else pd.DataFrame()
    if d2.empty:
        print("[state_input_hist] nothing to plot after filtering"); return

    # PRD §3.5: x = op_state-op_name-input_time, y = count
    # Build combined categorical x; keep top-k filtering applied above
    d2 = d2.copy()
    d2["input_time"] = d2["input_time"].astype(float)
    d2["xcat"] = (
        d2["op_state"].astype(str)
        + " | "
        + d2["op_name"].astype(str)
        + " | t="
        + d2["input_time"].map(lambda v: f"{v:.2f}")
    )
    d2 = d2.sort_values(["op_state", "op_name", "input_time"])  # stable order
    plt.figure(figsize=(max(8, 0.5 * len(d2)), 4))
    plt.bar(d2["xcat"], d2["count"].astype(float))
    plt.xticks(rotation=80, ha="right", fontsize=8)
    plt.xlabel("op_state | op_name | input_time")
    plt.ylabel("count")
    plt.title("State × Operation × Input-time Count")
    plt.tight_layout()
    if save_path:
        _save_fig(save_path)
    else:
        plt.show()


# -----------------------------
# CLI
# -----------------------------

def _build_paths(out_dir: str) -> Dict[str, Optional[str]]:
    return {
        "operation_timeline": _glob_latest(out_dir, "operation_timeline"),
        "op_state_timeline": _glob_latest(out_dir, "op_state_timeline"),
        "address_touch_count": _glob_latest(out_dir, "address_touch_count"),
        "op_state_name_input_time_count": _glob_latest(out_dir, "op_state_name_input_time_count"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Visualize PRD v2 required outputs from out/*.csv")
    ap.add_argument("which", choices=["all","op","state","heatmap","hist"], help="Which plot to generate")
    ap.add_argument("--out-dir", default=DEF_OUT_DIR, help="CSV output directory root (default: out)")
    ap.add_argument("--save-dir", default=DEF_VIZ_DIR, help="Directory to save figures (default: out/viz)")
    ap.add_argument("--no-save", action="store_true", help="Do not save; show instead")
    ap.add_argument("--kinds", nargs="*", default=None, help="For heatmap: filter op_base kinds (e.g., PROGRAM READ)")
    ap.add_argument("--topk-states", type=int, default=6, help="For hist: number of states to show")
    ap.add_argument("--topk-ops", type=int, default=4, help="For hist: number of ops per state")
    args = ap.parse_args(argv)

    paths = _build_paths(args.out_dir)
    missing: List[str] = []
    def _need(key: str) -> str:
        p = paths.get(key)
        if not p:
            missing.append(key)
            return ""
        return p

    os.makedirs(args.save_dir, exist_ok=True)

    if args.which in ("all", "op"):
        p = _need("operation_timeline")
        if p:
            sp = None if args.no_save else os.path.join(args.save_dir, "operation_timeline_gantt.png")
            plot_operation_gantt(p, save_path=sp)
    if args.which in ("all", "state"):
        p = _need("op_state_timeline")
        if p:
            sp = None if args.no_save else os.path.join(args.save_dir, "op_state_timeline_gantt.png")
            plot_op_state_gantt(p, save_path=sp)
    if args.which in ("all", "heatmap"):
        p = _need("address_touch_count")
        if p:
            sp = None if args.no_save else os.path.join(args.save_dir, "address_touch_heatmap.png")
            kinds = args.kinds if args.kinds else None
            plot_address_touch_heatmap(p, save_path=sp, kinds=kinds)
    if args.which in ("all", "hist"):
        p = _need("op_state_name_input_time_count")
        if p:
            sp = None if args.no_save else os.path.join(args.save_dir, "op_state_name_input_time_hist.png")
            plot_state_name_input_time_hist(p, save_path=sp, topk_states=int(args.topk_states), topk_ops=int(args.topk_ops))

    if missing and args.which != "all":
        print(f"[viz] missing required CSV for '{args.which}': {missing}")
    elif missing and args.which == "all":
        print(f"[viz] some CSVs are missing: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
