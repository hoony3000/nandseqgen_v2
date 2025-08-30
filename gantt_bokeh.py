from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple, Optional

import pandas as pd
from bokeh.io import curdoc
from bokeh.layouts import column, row
from bokeh.models import (
    Button,
    ColumnDataSource,
    HoverTool,
    TapTool,
    RangeSlider,
    MultiSelect,
    Div,
    TabPanel,
    Tabs,
    Select,
    FactorRange,
    CDSView,
    GroupFilter,
)
from bokeh.plotting import figure
from bokeh.models import Slider
from bokeh.palettes import Category20, Category10, Turbo256
from bokeh.transform import factor_cmap


def _normalize_timeline_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure columns: start, end, lane, op_name exist.
    Accepts nand_timeline.csv schema (start_us/end_us/kind).
    """
    out = df.copy()

    # start
    if "start" not in out.columns:
        if "time" in out.columns:
            out["start"] = pd.to_numeric(out["time"], errors="coerce")
        elif "start_us" in out.columns:
            out["start"] = pd.to_numeric(out["start_us"], errors="coerce")
        else:
            raise ValueError("Missing time column: one of ['start', 'time', 'start_us'] required")

    # end
    if "end" not in out.columns:
        if "end_time" in out.columns:
            out["end"] = pd.to_numeric(out["end_time"], errors="coerce")
        elif "end_us" in out.columns:
            out["end"] = pd.to_numeric(out["end_us"], errors="coerce")
        elif "duration" in out.columns:
            out["end"] = out["start"] + pd.to_numeric(out["duration"], errors="coerce").fillna(1)
        elif "latency" in out.columns:
            out["end"] = out["start"] + pd.to_numeric(out["latency"], errors="coerce").fillna(1)
        else:
            out["end"] = out["start"] + 1

    # op_name (prefer op_state for state timeline backward-compat)
    if "op_name" not in out.columns:
        if "op_state" in out.columns:
            out["op_name"] = out["op_state"].astype(str)
        elif "kind" in out.columns:
            out["op_name"] = out["kind"].astype(str)
        else:
            out["op_name"] = "OP"

    # lane
    if "lane" not in out.columns:
        if "die" in out.columns and "block" in out.columns:
            out["lane"] = (
                out["die"].astype("Int64").astype(str) + "/" + out["block"].astype("Int64").astype(str)
            )
        else:
            out["lane"] = out["op_name"].astype(str)

    # Clean
    out = out.dropna(subset=["start", "end", "lane", "op_name"])  # minimal requirements
    return out


def _build_color_map(df: pd.DataFrame) -> Dict[str, str]:
    """Auto-assign colors for each op_name using palettes; stable by sorted order."""
    ops = sorted({str(x) for x in df["op_name"].astype(str).unique()})
    n = len(ops)
    palette: List[str]
    # choose base palette
    if n <= 10:
        palette = list(Category10[10])
    elif n <= 20:
        palette = list(Category20[20])
    elif n <= 256:
        # sample evenly from Turbo256
        step = max(1, len(Turbo256) // n)
        palette = [Turbo256[i] for i in range(0, step * n, step)][:n]
    else:
        palette = [Turbo256[i % 256] for i in range(n)]
    cmap = {op: palette[i % len(palette)] for i, op in enumerate(ops)}
    return cmap


def _lane_indexing(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    lanes = df[["lane"]].drop_duplicates().reset_index(drop=True)
    lanes["yidx"] = lanes.index
    out = df.merge(lanes, on="lane", how="left")
    lane_order = lanes["lane"].tolist()
    return out, lane_order


def _compute_height(n_lanes: int) -> int:
    return int(min(1200, max(400, 28 * max(n_lanes, 3) + 120)))


def _make_doc_layout(df_in: pd.DataFrame, df_ops: Optional[pd.DataFrame] = None):
    df = _normalize_timeline_columns(df_in)
    # Use op_state for state timeline labels if available; keep op_name as auxiliary
    try:
        if "op_state" in df.columns:
            df = df.copy()
            df["op_label"] = df["op_state"].astype(str)
        else:
            df = df.copy()
            df["op_label"] = df["op_name"].astype(str)
    except Exception:
        df = df.copy()
        df["op_label"] = df.get("op_name", "OP").astype(str)
    df, lane_order = _lane_indexing(df)
    # build color map from op_label
    _df_for_colors = df.copy()
    _df_for_colors["op_name"] = df["op_label"].astype(str)
    color_map = _build_color_map(_df_for_colors)
    ops = sorted(color_map.keys())
    palette = [color_map[o] for o in ops]

    # Widgets
    die_vals = sorted(df["die"].dropna().unique().tolist()) if "die" in df.columns else []
    plane_vals = sorted(df["plane"].dropna().unique().tolist()) if "plane" in df.columns else []
    op_vals = sorted(df["op_label"].astype(str).dropna().unique().tolist())

    die_select = MultiSelect(title="Die", value=[], options=[str(v) for v in die_vals], size=6)
    plane_select = MultiSelect(title="Plane", value=[], options=[str(v) for v in plane_vals], size=8)
    op_select = MultiSelect(title="State", value=[], options=op_vals, size=8)

    tmin = float(df["start"].min()) if len(df) else 0.0
    tmax = float(df["end"].max()) if len(df) else 1.0
    time_slider = RangeSlider(title="Time Range", start=tmin, end=tmax, value=(tmin, tmax), step=max((tmax - tmin) / 1000.0, 0.001))
    reset_btn = Button(label="Reset filters")
    zoom_in_btn = Button(label="Zoom In (x)")
    zoom_out_btn = Button(label="Zoom Out (x)")
    # Size controls
    width_slider = Slider(title="Width (px)", start=600, end=2200, step=20, value=1200)
    scale_slider = Slider(title="Height Scale (x)", start=0.5, end=2.5, step=0.1, value=1.0)

    # Sources
    cols = ["lane", "yidx", "start", "end", "op_label"]
    for c in ("die", "block", "plane", "page", "op", "state", "dur_us", "op_state", "op_name"):
        if c in df.columns:
            cols.append(c)
    base_df = df[cols].copy()
    src_all = ColumnDataSource(base_df)
    src = ColumnDataSource(base_df.copy())

    # Figure
    fig = figure(
        title="State Timeline",
        x_axis_label="time",
        y_range=list(reversed(lane_order)),  # top-most first lane
        height=_compute_height(len(lane_order)),
        tools="xpan,xwheel_zoom,reset,save",
        active_scroll="xwheel_zoom",
        output_backend="canvas",
    )
    fig.x_range.start = tmin
    fig.x_range.end = tmax
    fig.width = int(width_slider.value)

    # Glyphs per state/op_label to enable per-legend toggling
    for name, color in color_map.items():
        view = CDSView(filter=GroupFilter(column_name="op_label", group=str(name)))
        fig.hbar(
            y="lane",
            left="start",
            right="end",
            height=0.8,
            fill_color=color,
            line_color=None,
            legend_label=str(name),
            source=src,
            view=view,
        )
    fig.legend.title = "State"
    fig.legend.location = "top_left"
    fig.legend.click_policy = "hide"

    # Hover
    tips = [("state", "@op_label"), ("lane", "@lane"), ("start", "@start"), ("end", "@end")]
    if "op_name" in base_df.columns:
        tips.insert(0, ("op_name", "@op_name"))
    for c in ("die", "block", "plane", "page"):
        if c in base_df.columns:
            tips.append((c, f"@{c}"))
    fig.add_tools(HoverTool(tooltips=tips))

    info = Div(text="", sizing_mode="stretch_width")

    def _apply_filters():
        nonlocal lane_order
        try:
            f = pd.DataFrame(src_all.data).copy()
            total = len(f)
            # die filter
            if "die" in f.columns and die_select.value:
                want = {int(v) for v in die_select.value}
                f = f[f["die"].isin(want)]
            # plane filter
            if "plane" in f.columns and plane_select.value:
                want = {int(v) for v in plane_select.value}
                f = f[f["plane"].isin(want)]
            # op/state label filter
            if op_select.value:
                want = set(op_select.value)
                f = f[f["op_label"].astype(str).isin(want)]
            # time filter
            t0, t1 = time_slider.value
            f = f[(f["end"] >= t0) & (f["start"] <= t1)]

            lanes_new = f[["lane"]].drop_duplicates().sort_values("lane")["lane"].astype(str).tolist()
            if not lanes_new:
                lanes_new = ["(empty)"]
            # ensure y_range is categorical
            try:
                fig.y_range.factors = list(reversed(lanes_new))
            except Exception:
                from bokeh.models import FactorRange as _FR
                fig.y_range = _FR(*list(reversed(lanes_new)))
            base_h = _compute_height(len(lanes_new))
            fig.height = int(base_h * float(scale_slider.value))
            src.data = ColumnDataSource.from_df(f)
            info.text = f"Rows: {len(f)}/{total} Lanes: {len(lanes_new)} Range: [{t0:.2f}, {t1:.2f}]"
            print(f"[GANTT] filter -> rows {len(f)}/{total} lanes {len(lanes_new)} time [{t0},{t1}]")
        except Exception as e:
            info.text = f"[GANTT] filter error: {e}"
            print(f"[GANTT] filter error: {e}")

    # wiring
    for w in (die_select, plane_select, op_select, time_slider):
        w.on_change("value", lambda attr, old, new: _apply_filters())
    width_slider.on_change("value", lambda attr, old, new: setattr(fig, "width", int(new)))
    scale_slider.on_change("value", lambda attr, old, new: _apply_filters())
    def _zoom(factor: float):
        try:
            x0 = float(fig.x_range.start)
            x1 = float(fig.x_range.end)
            c = 0.5 * (x0 + x1)
            w = max(1e-9, (x1 - x0) * float(factor))
            w = min(w, max(1e-9, tmax - tmin))
            nx0 = max(tmin, c - 0.5 * w)
            nx1 = min(tmax, c + 0.5 * w)
            if nx1 - nx0 < 1e-6:
                return
            fig.x_range.start = nx0
            fig.x_range.end = nx1
            time_slider.value = (nx0, nx1)
            _apply_filters()
        except Exception:
            pass
    zoom_in_btn.on_click(lambda: _zoom(0.5))
    zoom_out_btn.on_click(lambda: _zoom(1.5))
    reset_btn.on_click(
        lambda: (
            die_select.update(value=[]),
            plane_select.update(value=[]),
            op_select.update(value=[]),
            time_slider.update(value=(tmin, tmax)),
            _apply_filters(),
        )
    )

    _apply_filters()

    controls = column(
        die_select,
        plane_select,
        op_select,
        time_slider,
        row(zoom_in_btn, zoom_out_btn),
        row(width_slider, scale_slider),
        reset_btn,
        sizing_mode="fixed",
    )
    gantt_layout = column(row(controls, fig), info)

    # ---------------- Operation timeline (first tab) from nand_timeline.csv ----------------
    if df_ops is None or df_ops.empty:
        op_layout = column(Div(text="nand_timeline.csv not found or empty"))
    else:
        dfo = df_ops.copy()
        # required columns sanity
        need_cols = ["start_us", "end_us", "die", "block", "op_name"]
        missing = [c for c in need_cols if c not in dfo.columns]
        if missing:
            op_layout = column(Div(text=f"operation timeline missing columns: {missing}"))
        else:
            dfo["lane"] = dfo["die"].astype("Int64").astype(str) + "/" + dfo["block"].astype("Int64").astype(str)
            lanes = dfo[["lane"]].drop_duplicates().reset_index(drop=True)
            lane_order_ops = lanes["lane"].tolist()
            ops_names = sorted({str(x) for x in dfo["op_name"].astype(str).unique()})
            cmap_ops = _build_color_map(pd.DataFrame({"op_name": ops_names}))

            # widgets
            die_vals2 = sorted(dfo["die"].dropna().unique().tolist()) if "die" in dfo.columns else []
            block_vals2 = sorted(dfo["block"].dropna().unique().tolist()) if "block" in dfo.columns else []
            op_vals2 = sorted(dfo["op_name"].astype(str).dropna().unique().tolist())
            die_sel2 = MultiSelect(title="Die", value=[], options=[str(v) for v in die_vals2], size=6)
            block_sel2 = MultiSelect(title="Block", value=[], options=[str(v) for v in block_vals2], size=8)
            op_sel2 = MultiSelect(title="Operation", value=[], options=op_vals2, size=8)
            tmin2 = float(dfo["start_us"].min()) if len(dfo) else 0.0
            tmax2 = float(dfo["end_us"].max()) if len(dfo) else 1.0
            ts2 = RangeSlider(title="Time Range", start=tmin2, end=tmax2, value=(tmin2, tmax2), step=max((tmax2 - tmin2) / 1000.0, 0.001))
            reset2 = Button(label="Reset filters")
            zin2 = Button(label="Zoom In (x)")
            zout2 = Button(label="Zoom Out (x)")
            width2 = Slider(title="Width (px)", start=600, end=2200, step=20, value=1200)
            scale2 = Slider(title="Height Scale (x)", start=0.5, end=2.5, step=0.1, value=1.0)


            # source
            cols_ops = [c for c in ["lane","start_us","end_us","op_name","die","block","plane","page","op_uid"] if c in dfo.columns]
            dfo_base = dfo[cols_ops].copy()
            # ensure primitive dtypes for filters
            for cc in ("die","block","plane"):
                if cc in dfo_base.columns:
                    dfo_base[cc] = pd.to_numeric(dfo_base[cc], errors="coerce").astype("Int64")
            src_ops = ColumnDataSource(ColumnDataSource.from_df(dfo_base))

            # figure
            fig_op = figure(title="Operation Timeline", x_axis_label="time", y_range=list(reversed(lane_order_ops)), height=_compute_height(len(lane_order_ops)), tools="xpan,xwheel_zoom,reset,save", active_scroll="xwheel_zoom", output_backend="canvas")
            fig_op.x_range.start = tmin2
            fig_op.x_range.end = tmax2
            fig_op.width = int(width2.value)
            # per-op glyphs for legend toggle
            for name, color in cmap_ops.items():
                view = CDSView(filter=GroupFilter(column_name="op_name", group=str(name)))
                fig_op.hbar(y="lane", left="start_us", right="end_us", height=0.8, fill_color=color, line_color=None, legend_label=str(name), source=src_ops, view=view)
            fig_op.legend.title = "Operation"
            fig_op.legend.location = "top_left"
            fig_op.legend.click_policy = "hide"
            tips_ops = [("op", "@op_name"), ("lane", "@lane"), ("start", "@start_us"), ("end", "@end_us"), ("die","@die"), ("block","@block")]
            if "plane" in dfo.columns:
                tips_ops.append(("plane","@plane"))
            if "page" in dfo.columns:
                tips_ops.append(("page","@page"))
            if "op_uid" in dfo.columns:
                tips_ops.append(("op_uid","@op_uid"))
            fig_op.add_tools(HoverTool(tooltips=tips_ops))

            def _apply_ops():
                f = dfo_base.copy()
                if "die" in f.columns and die_sel2.value:
                    f = f[f["die"].isin({int(v) for v in die_sel2.value})]
                if "block" in f.columns and block_sel2.value:
                    f = f[f["block"].isin({int(v) for v in block_sel2.value})]
                if op_sel2.value:
                    f = f[f["op_name"].astype(str).isin(set(op_sel2.value))]
                t0, t1 = ts2.value
                f = f[(f["end_us"] >= t0) & (f["start_us"] <= t1)]
                lanes_new = f[["lane"]].drop_duplicates().sort_values("lane")["lane"].astype(str).tolist()
                if not lanes_new:
                    lanes_new = ["(empty)"]
                try:
                    fig_op.y_range.factors = list(reversed(lanes_new))
                except Exception:
                    from bokeh.models import FactorRange as _FR2
                    fig_op.y_range = _FR2(*list(reversed(lanes_new)))
                base_h = _compute_height(len(lanes_new))
                fig_op.height = int(base_h * float(scale2.value))
                # avoid index/level_* columns
                f = f.reset_index(drop=True)
                src_ops.data = ColumnDataSource.from_df(f)

            for w in (die_sel2, block_sel2, op_sel2, ts2):
                w.on_change("value", lambda attr, old, new: _apply_ops())
            width2.on_change("value", lambda attr, old, new: setattr(fig_op, "width", int(new)))
            scale2.on_change("value", lambda attr, old, new: _apply_ops())
            def _zoom2(factor: float):
                try:
                    x0 = float(fig_op.x_range.start); x1 = float(fig_op.x_range.end)
                    c = 0.5 * (x0 + x1)
                    w = max(1e-9, (x1 - x0) * float(factor))
                    w = min(w, max(1e-9, tmax2 - tmin2))
                    nx0 = max(tmin2, c - 0.5 * w)
                    nx1 = min(tmax2, c + 0.5 * w)
                    if nx1 - nx0 < 1e-6:
                        return
                    fig_op.x_range.start = nx0; fig_op.x_range.end = nx1
                    ts2.value = (nx0, nx1)
                    _apply_ops()
                except Exception:
                    pass
            zin2.on_click(lambda: _zoom2(0.5))
            zout2.on_click(lambda: _zoom2(1.5))
            reset2.on_click(lambda: (die_sel2.update(value=[]), block_sel2.update(value=[]), op_sel2.update(value=[]), ts2.update(value=(tmin2, tmax2)), _apply_ops()))
            _apply_ops()

            op_controls = column(die_sel2, block_sel2, op_sel2, ts2, row(zin2, zout2), row(width2, scale2), reset2, sizing_mode="fixed")
            op_layout = column(row(op_controls, fig_op))

    # ---------------- Schedule-time State vs Operation (second tab) ----------------
    sched_layout = column(Div(text="nand_state_timeline.csv & nand_timeline.csv 둘 다 필요합니다."))
    try:
        need_ops = False
        if (df_ops is not None) and (not df_ops.empty):
            has_core = {"die","plane","start_us"}.issubset(df_ops.columns)
            has_name = ("op_name" in df_ops.columns) or ("op_base" in df_ops.columns)
            need_ops = bool(has_core and has_name)
        # base_df는 normalize 후 컬럼명이 start/end 이므로 그 존재만 확인
        need_st   = {"die","plane","start","end","state"}.issubset(base_df.columns)
        if need_ops and need_st:
            # state: include 'op' to build op.state label
            # build state table from normalized base_df
            try:
                print(f"[SCHEDULE_TAB] base_df cols: {list(base_df.columns)}")
            except Exception:
                pass
            # prefer 'op', fallback to parse op from op_state/op_label, else op_name
            base_df2 = base_df.copy()
            if "op" not in base_df2.columns:
                import re as _re
                if "op_state" in base_df2.columns:
                    base_df2["op"] = base_df2["op_state"].astype(str).str.replace(r"\..*$", "", regex=True)
                elif "op_label" in base_df2.columns:
                    base_df2["op"] = base_df2["op_label"].astype(str).str.replace(r"\..*$", "", regex=True)
                elif "op_name" in base_df2.columns:
                    base_df2["op"] = base_df2["op_name"].astype(str)
            sel_cols = [c for c in ("die","plane","start","end","state","op") if c in base_df2.columns]
            st = base_df2[sel_cols].copy()
            # prefer start_us/end_us, but accept start/end if present
            if "start_us" not in st.columns and "start" in st.columns:
                st = st.rename(columns={"start":"start_us"})
            if "end_us" not in st.columns and "end" in st.columns:
                st = st.rename(columns={"end":"end_us"})
            try:
                print(f"[SCHEDULE_TAB] st cols after rename: {list(st.columns)}")
            except Exception:
                pass
            st = st.sort_values([c for c in ("die","plane","start_us") if c in st.columns]).reset_index(drop=True)
            # ops: include op_uid if present, for dedup per (uid,die,plane)
            ops_cols = ["die","plane","start_us"]
            for c in ("op_name","op_base","op_uid"):
                if c in df_ops.columns:
                    ops_cols.append(c)
            ops = df_ops[ops_cols].sort_values(["die","plane","start_us"]).reset_index(drop=True)
            if ("op_name" not in ops.columns) and ("op_base" in ops.columns):
                ops["op_name"] = ops["op_base"].astype(str)

            # epsilon: 작은 고정값(경계 크로싱 방지). TU가 0.01us 수준이면 1e-6us 충분
            eps = 1e-6

            # vectorized interval search per (die, plane)
            for col in ("die","plane"):
                if col in ops.columns:
                    ops[col] = pd.to_numeric(ops[col], errors="coerce")
                if col in st.columns:
                    st[col]  = pd.to_numeric(st[col], errors="coerce")
            ops = ops.dropna(subset=["die","plane","start_us"]).copy()
            st  = st.dropna(subset=["die","plane"]).copy()
            ops["query_us"] = ops["start_us"].astype(float) - float(eps)

            parts = []
            # avoid shadowing figure variable name: use pln for plane index
            for (d, pln), lop in ops.groupby(["die","plane"], sort=False):
                rst = st[(st.get("die") == d) & (st.get("plane") == pln)].copy()
                if lop.empty or rst.empty:
                    continue
                # choose start/end columns dynamically
                if "start_us" in rst.columns:
                    sc = "start_us"
                elif "start" in rst.columns:
                    sc = "start"
                else:
                    print("[SCHEDULE_TAB] missing start column in rst; skip group")
                    continue
                if "end_us" in rst.columns:
                    ec = "end_us"
                elif "end" in rst.columns:
                    ec = "end"
                else:
                    print("[SCHEDULE_TAB] missing end column in rst; skip group")
                    continue
                rst = rst.sort_values(sc).reset_index(drop=True)
                starts = rst[sc].astype(float).values
                ends   = rst[ec].astype(float).values
                states = rst["state"].astype(str).values
                ops_of = (rst["op"].astype(str).values if "op" in rst.columns else ["?"]*len(rst))

                q = lop["query_us"].values
                import numpy as _np
                idx = _np.searchsorted(starts, q, side="right") - 1
                idx[idx < 0] = -1
                valid = (idx >= 0) & (q < ends[idx])
                res = lop.copy()
                res["state"] = _np.where(valid, states[idx], _np.nan)
                res["op_prev"] = _np.where(valid, ops_of[idx], _np.nan)
                res["valid"] = valid
                res["end_us_prev"] = _np.where(valid, ends[idx], _np.nan)
                parts.append(res)
            m = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            if not m.empty:
                # keep only valid matches
                if "valid" in m.columns:
                    m = m[m["valid"]].copy()
                    m = m.drop(columns=["valid"], errors="ignore")
                # dedup per op if uid available (per die/plane)
                if "op_uid" in m.columns and m["op_uid"].notna().any():
                    m = m.drop_duplicates(subset=["op_uid","die","plane"], keep="first")
                # Prefer propose-time aggregation if available in nand_timeline.csv
                if (df_ops is not None) and ("phase_key_used" in df_ops.columns):
                    mm = df_ops.copy()
                    if "op_uid" in mm.columns and mm["op_uid"].notna().any():
                        mm = mm.drop_duplicates(subset=["op_uid","die","plane"], keep="first")
                    mm["state_at"] = mm["phase_key_used"].astype(str)
                    key_col = "op_name" if "op_name" in mm.columns else ("op_base" if "op_base" in mm.columns else None)
                    if key_col is None:
                        agg = mm.groupby(["state_at"]).size().reset_index(name="value")
                        mm["__op_key__"] = "?"
                        agg["op_name"] = "?"
                    else:
                        agg = mm.groupby(["state_at", key_col]).size().reset_index(name="value")
                        if key_col != "op_name":
                            agg = agg.rename(columns={key_col: "op_name"})
                else:
                    # build state_at as op.state using previous segment
                    m["state_at"] = (m["op_prev"].astype(str) + "." + m["state"].astype(str))
                    key_col = "op_name" if "op_name" in m.columns else ("op_base" if "op_base" in m.columns else None)
                    if key_col is None:
                        agg = m.groupby(["state_at"]).size().reset_index(name="value")
                        m["__op_key__"] = "?"
                        agg["op_name"] = "?"
                    else:
                        agg = m.groupby(["state_at", key_col]).size().reset_index(name="value")
                        if key_col != "op_name":
                            agg = agg.rename(columns={key_col: "op_name"})
                sched_src = ColumnDataSource(dict(x=[], value=[]))
                p3 = figure(title="Schedule-time: State vs Operation", x_axis_label="(state, op)", y_axis_label="count", height=420, tools="xpan,xwheel_zoom,reset,save,tap", active_scroll="xwheel_zoom", output_backend="canvas", x_range=FactorRange())
                p3.width = int(width_slider.value)
                p3.add_tools(HoverTool(tooltips=[("state,op","@x"),("value","@value")]))
                p3.add_tools(TapTool())
                details = Div(text="Click a bar to see matching ops", sizing_mode="stretch_width")
                def _populate_sched():
                    if agg.empty:
                        sched_src.data = dict(x=[], value=[])
                        p3.x_range = FactorRange()
                        return
                    factors = list(agg.apply(lambda r: (str(r["state_at"]), str(r["op_name"])) , axis=1))
                    values = agg["value"].astype(float).tolist()
                    p3.x_range = FactorRange(*factors)
                    p3.xaxis.major_label_orientation = 1.0
                    sched_src.data = dict(x=factors, value=values)
                _populate_sched()
                r3 = p3.vbar(x="x", top="value", width=0.9, source=sched_src)
                width_slider.on_change("value", lambda attr, old, new: setattr(p3, "width", int(new)))
                # selection callback: show df_ops rows that contributed to the selected bar
                def _on_select(attr, old, new):
                    try:
                        inds = list(sched_src.selected.indices)
                        if not inds:
                            details.text = "Click a bar to see matching ops"
                            return
                        i = int(inds[0])
                        fx = sched_src.data.get("x", [])
                        if not fx or i >= len(fx):
                            details.text = "(no data)"
                            return
                        fac = fx[i]
                        # factor may be tuple or list
                        try:
                            st_key, okind = fac
                        except Exception:
                            # fallback parse
                            s = str(fac)
                            if s.startswith("(") and "," in s:
                                left, right = s[1:-1].split(",", 1)
                                st_key = left.strip().strip("'\"")
                                okind = right.strip().strip("'\"")
                            else:
                                st_key, okind = s, "?"
                        if (df_ops is not None) and ("phase_key_used" in df_ops.columns):
                            if "op_name" in df_ops.columns:
                                mm = df_ops[(df_ops["phase_key_used"].astype(str) == str(st_key)) & (df_ops["op_name"].astype(str) == str(okind))].copy()
                            elif "op_base" in df_ops.columns:
                                mm = df_ops[(df_ops["phase_key_used"].astype(str) == str(st_key)) & (df_ops["op_base"].astype(str) == str(okind))].copy()
                            else:
                                mm = df_ops[df_ops["phase_key_used"].astype(str) == str(st_key)].copy()
                        else:
                            if "op_name" in m.columns:
                                mm = m[(m["state_at"].astype(str) == str(st_key)) & (m["op_name"].astype(str) == str(okind))].copy()
                            elif "op_base" in m.columns:
                                mm = m[(m["state_at"].astype(str) == str(st_key)) & (m["op_base"].astype(str) == str(okind))].copy()
                            else:
                                mm = m[m["state_at"].astype(str) == str(st_key)].copy()
                        # show top 20 rows from df_ops-like columns
                        cols = [c for c in ("die","plane","start_us","op_uid","op_name","op_base","query_us","op_prev","state","phase_key_used","state_key_at_schedule") if c in mm.columns]
                        show = mm[cols].head(20)
                        html = show.to_html(index=False)
                        details.text = f"<b>Selected:</b> {st_key} × {okind} (rows={len(mm)})" + html
                        print(f"[SCHEDULE_TAB][SELECT] {st_key} x {okind} rows={len(mm)}")
                    except Exception as e:
                        details.text = f"select error: {e}"
                        print(f"[SCHEDULE_TAB][SELECT][ERR] {e}")
                sched_src.selected.on_change("indices", _on_select)
                sched_layout = column(p3, details)
    except Exception as _e:
        try:
            import traceback as _tb
            print("[SCHEDULE_TAB][ERROR]", _e)
            print("[SCHEDULE_TAB][TRACE]\n" + _tb.format_exc())
        except Exception:
            pass
        sched_layout = column(Div(text=f"schedule-state calc error: {_e}"))

    tabs = Tabs(tabs=[
        TabPanel(child=op_layout, title="operation timeline"),
        TabPanel(child=gantt_layout, title="state timeline"),
        TabPanel(child=sched_layout, title="state x operation"),
    ])
    return tabs


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    return pd.read_csv(path)


def build():
    # Prefer state-level timeline if available, fallback to op-level timeline
    p_state = Path("nand_state_timeline.csv")
    p_ops = Path("nand_timeline.csv")
    csv_path = p_state if p_state.exists() else p_ops
    df = _load_csv(csv_path)
    df_ops = _load_csv(p_ops) if p_ops.exists() else None
    layout = _make_doc_layout(df, df_ops=df_ops)
    curdoc().add_root(layout)
    curdoc().title = "NAND Gantt (Bokeh)"


build()


