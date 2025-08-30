# nandsim_p6_candidate_start_fix.py
# - P6: candidate_start 기반 검사 + schedule 직전 fail-safe
# - Admission/exclusion 설계와 정합성 유지(near-future만 수용, 미래예약 폭주 억제 강화)
# - Latch/DOUT 중 SR 예약 금지 케이스를 bus/excl에서 일관되게 차단

from __future__ import annotations
import heapq, random, sys, os, time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple, Set
from collections import defaultdict
import csv
try:
    from viz_tools import TimelineLogger, plot_gantt, plot_gantt_by_die, plot_block_page_sequence_3d, plot_block_page_sequence_3d_by_die
    from viz_tools import validate_timeline, print_validation_report, violations_to_dataframe
    from viz_tools import export_patterns, pattern_preview_dataframe
    from viz_tools import plot_target_heatmap
    from viz_tools import compute_block_usage_stats, save_block_usage_stats, print_block_usage_summary
    VIZ_AVAILABLE = True
except Exception as _viz_e:
    # Minimal fallbacks for headless/no-deps environment
    VIZ_AVAILABLE = False
    class _SimpleDataFrame:
        def __init__(self, rows):
            self._rows = list(rows or [])
            self.empty = (len(self._rows) == 0)
        def to_csv(self, path: str, index: bool = False):
            import csv
            if not self._rows:
                # still create empty file with no header
                open(path, 'w', encoding='utf-8').close()
                return
            # union of keys to be safe
            keys = []
            for r in self._rows:
                for k in r.keys():
                    if k not in keys:
                        keys.append(k)
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                for r in self._rows:
                    w.writerow(r)

    class TimelineLogger:
        def __init__(self):
            self.rows = []
        def log_op(self, op, start_us: float, end_us: float, label_for_read=None):
            label = op.base.name
            if label_for_read:
                label = label_for_read
            for t in op.targets:
                page = t.page if (t.page is not None) else 0
                self.rows.append({
                    "start_us": float(start_us),
                    "end_us":   float(end_us),
                    "die":      int(t.die),
                    "plane":    int(t.plane),
                    "block":    int(t.block),
                    "page":     int(page),
                    "op_name":  label,
                    "op_base":  op.base.name,
                    "source":   op.meta.get("source"),
                    "op_uid":   int(op.meta.get("uid", -1)),
                    "arity":    int(op.meta.get("arity", 1)),
                    "phase_key_used": op.meta.get("phase_key_used"),
                    "state_key_at_schedule": op.meta.get("state_key_at_schedule"),
                })
        def to_dataframe(self):
            return _SimpleDataFrame(self.rows)

    def validate_timeline(df, cfg):
        return {"issues": [], "counts": {}}
    def print_validation_report(report, max_rows: int = 20):
        print("=== Validation Report (fallback) ===\nNo issues (validation skipped).")
        return
    def violations_to_dataframe(report):
        return _SimpleDataFrame([])
    def export_patterns(df, cfg):
        return []
    def pattern_preview_dataframe(df, cfg):
        return _SimpleDataFrame([])
    def plot_target_heatmap(*args, **kwargs):
        return
    def plot_gantt(*args, **kwargs):
        return
    def plot_gantt_by_die(*args, **kwargs):
        return
    def plot_block_page_sequence_3d(*args, **kwargs):
        return
    def plot_block_page_sequence_3d_by_die(*args, **kwargs):
        return
    def compute_block_usage_stats(*args, **kwargs):
        return {}
    def save_block_usage_stats(*args, **kwargs):
        return
    def print_block_usage_summary(*args, **kwargs):
        print("[BLOCK_USAGE] summary skipped (fallback)")

# --------------------------------------------------------------------------
# Simulation resolution
SIM_RES_US = 0.01
def quantize(t: float) -> float:
    return round(t / SIM_RES_US) * SIM_RES_US

# --------------------------------------------------------------------------
# Config
# --- Patches: RNG seed + fixed-duration enforcement for validate_timeline ---
def _seed_rng_from_cfg(cfg):
    import random
    try:
        seed = cfg.get("rng_seed", None)
        if seed is not None:
            random.seed(int(seed))
            print(f"[INIT] random.seed({seed})")
    except Exception as e:
        print(f"[INIT] random.seed skipped: {e}")

def _coerce_states_to_fixed(cfg):
    """
    Ensure all op_specs.states use fixed durations; if not, convert using representative stats.
    - normal: use mean
    - exp   : use 1/lambda as mean
    """
    converted = 0
    for op, spec in cfg.get("op_specs", {}).items():
        for st in spec.get("states", []):
            dist = st.get("dist", {})
            kind = dist.get("kind")
            if kind != "fixed":
                val = None
                if kind == "normal":
                    val = float(dist.get("mean", 0.0))
                elif kind == "exp":
                    lam = float(dist.get("lambda", 1.0))
                    val = 1.0/lam if lam > 0 else 0.0
                elif "value" in dist:
                    val = float(dist["value"])
                else:
                    try:
                        val = float(dist.get("mean", 0.0))
                    except Exception:
                        val = 0.0
                st["dist"] = {"kind":"fixed", "value": float(val)}
                converted += 1
    if converted:
        print(f"[INIT] coerced {converted} state dists to fixed for validation")

def _validate_phase_conditional_cfg(cfg):
    """
    Ensure each phase_conditional distribution has no negative weights and
    normalize to sum to 1.0 if necessary (within epsilon). Log when normalization occurs.
    """
    import logging
    logger = logging.getLogger(__name__)

    pc = cfg.get("phase_conditional", {})
    eps = 1e-6
    for key, dist in pc.items():
        if not isinstance(dist, dict):
            continue
        # OP.PHASE.POS 형태 금지 (키 2개 이상의 점 포함 금지)
        if key.count(".") >= 2 and key != "DEFAULT":
            raise ValueError(f"phase_conditional key not supported (use OP.PHASE only): {key}")
        vals = list(dist.values())
        if any((float(v) < 0.0) for v in vals):
            raise ValueError(f"phase_conditional[{key}] contains negative weight(s)")
        s = float(sum(float(v) for v in vals))
        if abs(s - 1.0) > eps:
            # 합이 0이거나 비정상적으로 작은 경우는 정규화 불가
            if s <= eps:
                raise ValueError(f"phase_conditional[{key}] sum={s} is non-positive; cannot normalize")
            factor = 1.0 / s
            for k in list(dist.keys()):
                dist[k] = float(dist[k]) * factor
            logger.warning(f"[CFG] normalized phase_conditional[%s]: sum %.6f -> 1.0", key, s)

CFG = {
    "rng_seed": 12345,
    # Progressive propose (initial scaffold)
    "propose": {
        "mode": "legacy",  # legacy | progressive | hybrid
        "sampling": {
            "enable_starvation_prevention": True,
            "starvation_threshold": 10,
            "boost_factor": 2.0,
            "rejection_penalty_decay": 0.9,
        },
        "chunking": {
            "max_ops_per_chunk": 5,
            "checkpoint_interval": 5,
            "allow_partial_success": True,
        },
        "scheduling": {
            "inject_fail_rate": 0.0  # for experiments: schedule-time failure injection (0.0~1.0)
        },
    },
    "policy": {
        "queue_refill_period_us": 50.0,
        "run_until_us": 2000.0,
        "planner_max_tries": 8,
        "hookscreen": {"startplane_scan": 2, "horizon_us": 0.30, "global_obl_iters": 1},
        # PHASE_HOOK 차단: op.kind 기준 훅 생성 비활성화(빈 리스트면 비활성화 없음)
        # 예: ["DOUT", "SR"]
        "phase_hook_disabled_kinds": ["SR"],
        # bootstrap 러닝타임 여유 = last_deadline_boot + margin_per_ob * num_bootstrap_obligations
        "run_until_bootstrap_margin_per_ob_us": 5.0,
        "enable_phase_conditional": True,
        # ---- screening policy (READ/PROGRAM vs future ERASE/PROGRAM) ----
        # READ 정책: 커밋만 허용할지, 미래 PROGRAM(커밋 전)도 허용할지
        "read_requires_committed": False,
        "read_allow_future_program": True,
        # 미래 PROGRAM 인정 경계 마진(us): program_end <= read_start - margin
        "read_future_program_guard_us": 0.2,
        # ERASE 겹침 판단 마진(us): erase_end <= read_start - margin 이면 OK, 아니면 충돌
        "read_erase_guard_margin_us": 0.2,
        # PROGRAM과 미래 ERASE 충돌 판단 마진(us)
        "program_erase_conflict_guard_us": 0.0,
    },

    # Admission gating (near-future only to prevent policy explosion)
    "admission": {
        "default_delta_us": 0.30,
        "op_overrides": {
            # op_base
            "SR": 0.30
        },
        "obligation_bypass": True  # obligations ignore admission delta by default
    },

    # Phase-conditional proposal (alias keys allowed)
    "state_timeline": {
        # affects: op_base
        "affects": {"READ": True, "PROGRAM": True, "ERASE": True, "DOUT": True, "SR": False}
    },
    "phase_conditional": {
        "READ.CORE_BUSY":       {"SIN_PROGRAM": 0.00, "MUL_PROGRAM": 0.00, "SIN_READ": 0.25, "MUL_READ": 0.00, "SIN_ERASE": 0.00, "MUL_ERASE": 0.00, "SR": 0.10},
        "READ.DATA_OUT":        {"SIN_PROGRAM": 0.00, "MUL_PROGRAM": 0.00, "SIN_READ": 0.00, "MUL_READ": 0.00, "SIN_ERASE": 0.00, "MUL_ERASE": 0.00, "SR": 0.10},
        "READ.END":             {"SIN_PROGRAM": 0.15, "MUL_PROGRAM": 0.10, "SIN_READ": 0.25, "MUL_READ": 0.15, "SIN_ERASE": 0.15, "MUL_ERASE": 0.10, "SR": 0.10},
        "PROGRAM.CORE_BUSY":    {"SIN_PROGRAM": 0.00, "MUL_PROGRAM": 0.00, "SIN_READ": 0.00, "MUL_READ": 0.00, "SIN_ERASE": 0.00, "MUL_ERASE": 0.00, "SR": 0.10},
        "PROGRAM.END":          {"SIN_PROGRAM": 0.15, "MUL_PROGRAM": 0.10, "SIN_READ": 0.25, "MUL_READ": 0.15, "SIN_ERASE": 0.15, "MUL_ERASE": 0.10, "SR": 0.10},
        "ERASE.CORE_BUSY":      {"SIN_PROGRAM": 0.00, "MUL_PROGRAM": 0.00, "SIN_READ": 0.00, "MUL_READ": 0.00, "SIN_ERASE": 0.00, "MUL_ERASE": 0.00, "SR": 0.10},
        "ERASE.END":            {"SIN_PROGRAM": 0.15, "MUL_PROGRAM": 0.10, "SIN_READ": 0.25, "MUL_READ": 0.15, "SIN_ERASE": 0.15, "MUL_ERASE": 0.10, "SR": 0.10},
        "DEFAULT":              {"SIN_PROGRAM": 0.15, "MUL_PROGRAM": 0.10, "SIN_READ": 0.25, "MUL_READ": 0.15, "SIN_ERASE": 0.15, "MUL_ERASE": 0.10, "SR": 0.10},
    },

    # Selection defaults/overrides (for fanout/interleave)
    "selection": {
        "defaults": {
            "READ":    {"fanout": 1, "interleave": True},
            "PROGRAM": {"fanout": 2, "interleave": False},
            "ERASE":   {"fanout": 2, "interleave": False},
            "SR":      {"fanout": 1, "interleave": True},
        },
        "phase_overrides": {
            "READ.CORE_BUSY": {"fanout": 4, "interleave": True},
        }
    },

    # Op specs (state sequence + bus usage; scope for CORE_BUSY occupancy)
    "op_specs": {
        "SIN_READ": {
            "base": "READ",
            "fanout": ("eq",1),
            "scope": "PLANE_SET",
            "page_equal_required": True,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "fixed", "value": 8.0}},
                {"name": "DATA_OUT",  "bus": False, "dist": {"kind": "fixed", "value": 2.0}},
            ],
        },
        "MUL_READ": {
            "base": "READ",
            "fanout": ("ge",2),
            "scope": "PLANE_SET",
            # require same page across planes
            "page_equal_required": True,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "fixed", "value": 8.0}},
                {"name": "DATA_OUT",  "bus": False, "dist": {"kind": "fixed", "value": 2.0}},
            ],
        },
        "SIN_PROGRAM": {
            "base": "PROGRAM",
            "fanout": ("eq",1),
            "scope": "DIE_WIDE",
            "page_equal_required": True,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "fixed", "value": 20.0}},
            ],
        },
        "MUL_PROGRAM": {
            "base": "PROGRAM",
            "fanout": ("ge",2),
            "scope": "DIE_WIDE",
            "page_equal_required": True,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "fixed", "value": 20.0}},
            ],
        },
        "SIN_ERASE": {
            "base": "ERASE",
            "fanout": ("eq",1),
            "scope": "DIE_WIDE",
            "page_equal_required": False,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "fixed", "value": 40.0}},
            ],
        },
        "MUL_ERASE": {
            "base": "ERASE",
            "fanout": ("ge",2),
            "scope": "DIE_WIDE",
            "page_equal_required": False,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "fixed", "value": 40.0}},
            ],
        },
        "DOUT": {
            "base": "DOUT",
            "fanout": ("eq",1),
            "scope": "NONE",
            "page_equal_required": False,
            "states": [
                {"name": "ISSUE",     "bus": True, "dist": {"kind": "fixed",  "value": 0.2}},
            ],
        },
        "SR": {
            "base": "SR",
            "fanout": ("eq",1),
            "scope": "NONE",
            "page_equal_required": False,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed",  "value": 0.2}},
            ],
            "ignore": ["admission", "reserve_planscope", "precheck"],
        },
    },

    # Runtime exclusion rules (register on schedule; checked on propose)
    "constraints": {
        "exclusions": [
            # PROGRAM/ERASE: during CORE_BUSY block READ/PROGRAM/ERASE on the die; allow SR
            # op: op_base
            {"when": {"op": "PROGRAM", "states": ["CORE_BUSY"]}, "scope": "DIE",
             "blocks": ["BASE:READ", "BASE:PROGRAM", "BASE:ERASE"]},
            {"when": {"op": "ERASE",   "states": ["CORE_BUSY"]}, "scope": "DIE",
             "blocks": ["BASE:READ", "BASE:PROGRAM", "BASE:ERASE"]},

            # MUL_READ: during CORE_BUSY block READ/PROGRAM/ERASE (on the die); allow SR
            {"when": {"op": "READ", "alias": "MUL", "states": ["ISSUE","CORE_BUSY","DATA_OUT"]}, "scope": "DIE",
             "blocks": ["BASE:READ", "BASE:PROGRAM", "BASE:ERASE"]},

            # SIN_READ: during CORE_BUSY block MUL_READ + PROGRAM + ERASE; allow SIN_READ & SR
            {"when": {"op": "READ", "alias": "SIN", "states": ["ISSUE","CORE_BUSY","DATA_OUT"]}, "scope": "DIE",
             "blocks": ["ALIAS:MUL_READ", "BASE:PROGRAM", "BASE:ERASE"]},

            # DOUT : during DATA_OUT block READ/PROGRAM/ERASE (on the die); allow SR
            {"when": {"op": "DOUT", "states": ["ISSUE"]}, "scope": "DIE",
             "blocks": ["BASE:READ", "BASE:PROGRAM", "BASE:ERASE"]},

        ]
    },

    # Obligation: READ → DOUT
    "obligations": [
        {
            # issuer: op_base / require: op_name
            "issuer": "READ",
            "require": "DOUT",
            "window_us": {"kind": "fixed", "value": 20.0},
            "priority_boost": {"start_us_before_deadline": 2.5, "boost_factor": 2.0, "hard_slot": True, "plane_stagger_us": 0.2},
        },
        {
            "issuer": "PROGRAM",
            "require": "SR",
            "window_us": {"kind": "fixed", "value": 2.0},
            "priority_boost": {"start_us_before_deadline": 2.5, "boost_factor": 2.0, "hard_slot": True, "plane_stagger_us": 0.2},
        },
        {
            "issuer": "ERASE",
            "require": "SR",
            "window_us": {"kind": "fixed", "value": 2.0},
            "priority_boost": {"start_us_before_deadline": 2.5, "boost_factor": 2.0, "hard_slot": True, "plane_stagger_us": 0.2},
        },
    ],

    # Topology (redefined): plane = block % planes
    "topology": {
        "dies": 2,
        "planes": 4,
        "blocks": 32,           # total blocks per die (not per plane)
        "pages_per_block": 40,
    },

    "export": {"tu_us": 0.01, "nop_symbol": "NOP", "wait_as_nop": True, "drift_correction": True,
                "log_to_file": False, "log_path": "run.log", "log_tee": True},

    # Address initial state: -1=ERASED, -2=initial(not erased)
    "address_init_state": -1,

    # Bootstrap (disabled by default)
    "bootstrap": {
        "enabled": False,
        "pgm_ratio": 0.2,
        "stage_gap_us": 0.5,
        "stagger_us": 0.2,
        # "hard_slot": True,
        "hard_slot": False,
        "max_ratio": 0.5,
        "dout_stagger_n": 0,
        "disable_timeline_logging": False,
        "split_timeline_logging": False,
        "bootstrap_timeline_path": "nand_timeline_bootstrap.csv",
        "policy_timeline_path": "nand_timeline_policy.csv",
        "reduce_phase_hooks": True,
        "hook_margin_us": 1,
        # READ 이후 DOUT으로 넘어가기 전 전역 간극(부트스트랩 전용)
        # "dout_global_gap_us": 5.0
    },
    # Addressing strategies (experimental)
    "addressing": {
        "program": {
            # ascending | cyclic_from_write_head
            "search_order": "cyclic_from_write_head"
        },
        "erase": {
            # ascending_non_erased | cyclic_from_write_head
            "pick_strategy": "cyclic_from_write_head"
        },
        "write_head": {
            # to_erased_block | round_robin_next | stay
            "on_erase": "round_robin_next"
        }
    },
}

# --- Pattern export (ATE CSV) defaults ---
CFG["pattern_export"] = {
    "output_dir": "out_patterns",
    "file_prefix": "pattern",
    "columns": ["seq","time","op_id","op_name","payload"],
    "time": {"from": "start_us", "scale": 1.0, "round_decimals": 0, "out_col": "time"},
    "opcode_map": {},
    "aliasing": {"apply_to": ["READ","PROGRAM","ERASE"], "mul_threshold": 2},
    "nop": {
        "enable": True,
        "min_gap_us": 5.0,
        "quantum_us": 1.0,
        "opcode": 0,
        "op_name": "NOP",
        "rep_key": "rep"
    },
    "split": {
        "by_rows": {"enable": False, "max_rows": 50000},
        "by_time": {"enable": False, "chunk_us": 100000.0},
        "guard_whole_op": True
    },
    "payload": {
        "default": {"kind": "addresses_list"},   # 기본: 타깃 전체 리스트
        "NOP": {"kind": "nop_rep_only"},         # NOP은 {"rep": n}
        "SR": {"kind": "addresses_first"}        # SR은 첫 타깃만 (예시)
    },
    "preflight": {
        "require_opcode": True,
        "require_json_payload": True,
        "page_equal_required_from_op_specs": True,
        "time_monotonic": True
    }
}

# 예시
# CFG["pattern_export"]["payload"]["READ"] = {"kind": "addresses_list"}
# CFG["pattern_export"]["payload"]["PROGRAM"] = {"kind": "addresses_first"}
# CFG["pattern_export"]["nop"]["enable"] = False   # 미삽입
# CFG["pattern_export"]["nop"].update({"min_gap_us": 10.0, "quantum_us": 2.0})

# --------------------------------------------------------------------------
# Alias mapping for policy/external naming
OP_ALIAS = {}
for op, spec in CFG.get("op_specs", {}).items():
    OP_ALIAS[op] = {"base": spec.get("base", op), "fanout": spec.get("fanout", ("eq",1))}

MAPPING_OP_BASE = {}
CANDIDATES_OP_BASE = {}
for op_name, spec in CFG.get("op_specs", {}).items():
    base = spec.get("base", op_name)
    MAPPING_OP_BASE[op_name] = base
    if op_name not in CANDIDATES_OP_BASE:
        CANDIDATES_OP_BASE[base] = []
    CANDIDATES_OP_BASE[base].append(op_name)

# --------------------------------------------------------------------------
# Models
# Build OpKind dynamically from CFG.op_specs[*].base
def _build_opkind_from_cfg(cfg: Dict[str, Any]) -> Enum:
    bases: List[str] = []
    for op_name, spec in cfg.get("op_specs", {}).items():
        base = str(spec.get("base", op_name))
        if base not in bases:
            bases.append(base)
    # deterministic ordering
    bases = sorted(bases)
    return Enum("OpKind", bases)  # values = 1..N, names = base strings

OpKind = _build_opkind_from_cfg(CFG)

# Build opcode_map dynamically using OpKind and aliasing rules
def _build_opcode_map_from_opkind(cfg: Dict[str, Any], opkind: Enum) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    next_code = 0
    # NOP first
    nop_name = str(cfg.get("pattern_export", {}).get("nop", {}).get("op_name",
                    cfg.get("export", {}).get("nop_symbol", "NOP")))
    mapping[nop_name] = next_code; next_code += 1
    # aliasing targets
    alias_apply = set(cfg.get("pattern_export", {}).get("aliasing", {}).get("apply_to", []))
    # deterministic order by base name
    base_names = sorted([name for name in opkind.__members__.keys()])
    for base in base_names:
        if base in alias_apply:
            for prefix in ("SIN_", "MUL_"):
                key = f"{prefix}{base}"
                if key not in mapping:
                    mapping[key] = next_code; next_code += 1
        else:
            key = base
            if key not in mapping:
                mapping[key] = next_code; next_code += 1
    return mapping

# apply dynamic opcode map
CFG["pattern_export"]["opcode_map"] = _build_opcode_map_from_opkind(CFG, OpKind)

class Scope(Enum):
    NONE=0; PLANE_SET=1; DIE_WIDE=2

@dataclass(frozen=True)
class Address:
    die:int; plane:int; block:int; page:Optional[int]=None

@dataclass
class PhaseHook:
    time_us: float
    label: str
    die:int; plane:int

@dataclass
class StateSeg:
    name:str
    dur_us: float
    bus: bool = False

@dataclass
class Operation:
    name: str
    base: Enum
    targets: List[Address]
    states: List[StateSeg]
    movable: bool = True
    meta: Dict[str,Any] = field(default_factory=dict)

# --------------------------------------------------------------------------
# Rejection logging (per-attempt structured log + aggregated stats)
@dataclass
class RejectEvent:
    now_us: float
    die: int
    plane: int
    hook: str
    stage: str                 # 'obligation' | 'phase_conditional' | 'backoff'
    attempted: Optional[str]   # 'READ'/'PROGRAM'/... or None
    alias: Optional[str]       # 'SIN_READ'/'MUL_READ'/... or None
    fanout: Optional[int]
    plane_set: Optional[str]   # e.g. "[0,2]" stringified
    reason: str                # 'admission'/'plan_none'/...
    detail: str                # free-form short note
    earliest_start: Optional[float] = None
    admission_delta: Optional[float] = None
    ob_id: Optional[int] = None

class RejectionLogger:
    def __init__(self):
        self.rows: List[RejectEvent] = []
        # nested counts: stage -> reason -> count
        self.stats = defaultdict(lambda: defaultdict(int))
        # stage-wise attempts/accepts
        self.stage_attempts = defaultdict(int)
        self.stage_accepts  = defaultdict(int)

    def log_attempt(self, stage: str):
        self.stage_attempts[stage] += 1

    def log_accept(self, stage: str):
        self.stage_accepts[stage] += 1

    def log_reject(self, ev: RejectEvent):
        self.rows.append(ev)
        self.stats[ev.stage][ev.reason] += 1

    def to_csv(self, path: str = "reject_log.csv"):
        if not self.rows:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[ "now_us","die","plane","hook","stage","attempted","alias",
                             "fanout","plane_set","reason","detail",
                             "earliest_start","admission_delta","ob_id" ]
            )
            w.writeheader()
            for r in self.rows:
                w.writerow({
                    "now_us": r.now_us,
                    "die": r.die,
                    "plane": r.plane,
                    "hook": r.hook,
                    "stage": r.stage,
                    "attempted": r.attempted,
                    "alias": r.alias,
                    "fanout": r.fanout,
                    "plane_set": r.plane_set,
                    "reason": r.reason,
                    "detail": r.detail,
                    "earliest_start": r.earliest_start,
                    "admission_delta": r.admission_delta,
                    "ob_id": r.ob_id,
                })

    def to_summary_csv(self, path: str = "reject_summary.csv", top_n: int = 10):
        if not self.rows:
            return
        stage_reason = []
        for stage, d in self.stats.items():
            for reason, cnt in d.items():
                stage_reason.append((stage, reason, cnt))
        stage_reason.sort(key=lambda x: -x[2])
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["stage", "reason", "count"]) 
            for stage, reason, cnt in stage_reason[:top_n]:
                w.writerow([stage, reason, cnt])
        with open("stage_stats.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["stage", "attempts", "accepts"]) 
            stages = set(list(self.stage_attempts.keys()) + list(self.stage_accepts.keys()))
            for st in sorted(stages):
                w.writerow([st, self.stage_attempts.get(st, 0), self.stage_accepts.get(st, 0)])

    def to_obligation_skips_csv(self, path: str = "obligation_skips.csv"):
        if not self.rows:
            return
        filtered = [r for r in self.rows if (r.stage == "obligation" and isinstance(r.reason, str) and r.reason.startswith("soft_defer/"))]
        if not filtered:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["now_us","die","plane","hook","reason","attempted","alias","fanout","plane_set","earliest_start","admission_delta","detail","ob_id"])
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["now_us","die","plane","hook","reason","attempted","alias","fanout","plane_set","earliest_start","admission_delta","detail","ob_id"])
            for r in filtered:
                w.writerow([
                    r.now_us,
                    r.die,
                    r.plane,
                    r.hook,
                    r.reason,
                    r.attempted,
                    r.alias,
                    r.fanout,
                    r.plane_set,
                    r.earliest_start,
                    r.admission_delta,
                    r.detail,
                    r.ob_id,
                ])


# --------------------------------------------------------------------------
# Adaptive sampler (progressive sampling scaffold)
class AdaptiveSampler:
    def __init__(self, cfg: Dict[str, Any]):
        prop = cfg.get("propose", {})
        samp = prop.get("sampling", {})
        self.enabled = bool(samp.get("enable_starvation_prevention", True))
        self.starvation_threshold = int(samp.get("starvation_threshold", 10))
        self.boost_factor = float(samp.get("boost_factor", 2.0))
        # not used in v1, kept for compatibility with doc
        self.rejection_penalty_decay = float(samp.get("rejection_penalty_decay", 0.9))
        # counters per op_name (alias key like 'SIN_READ')
        self.since_accept: Dict[str, int] = defaultdict(int)
        self.ticks: Dict[str, int] = defaultdict(int)
        self.accepts: Dict[str, int] = defaultdict(int)
        self.boost_applied: Dict[str, int] = defaultdict(int)

    def tick(self, candidates: Dict[str, float]):
        if not self.enabled:
            return
        # increment counters for all current candidates
        for k in candidates.keys():
            self.since_accept[k] += 1
            self.ticks[k] += 1

    def on_accept(self, op_name: str):
        if not self.enabled:
            return
        self.since_accept[op_name] = 0
        self.accepts[op_name] += 1

    def reweight(self, weights: Dict[str, float]) -> Dict[str, float]:
        if not self.enabled:
            return dict(weights)
        out = {}
        for k, w in weights.items():
            c = self.since_accept.get(k, 0)
            if c >= self.starvation_threshold:
                out[k] = float(w) * self.boost_factor
                self.boost_applied[k] += 1
            else:
                out[k] = float(w)
        return out

    def to_csv(self, path: str = 'metrics_sampler.csv'):
        try:
            with open(path,'w',newline='',encoding='utf-8') as f:
                w=csv.writer(f); w.writerow(['key','ticks','accepts','since_accept','boost_applied'])
                keys = set(self.ticks.keys()) | set(self.accepts.keys()) | set(self.since_accept.keys()) | set(self.boost_applied.keys())
                for k in sorted(keys):
                    w.writerow([k, int(self.ticks.get(k,0)), int(self.accepts.get(k,0)), int(self.since_accept.get(k,0)), int(self.boost_applied.get(k,0))])
        except Exception as e:
            print(f"[SAMPLER] metrics write skipped: {e}")

    # no-op CSV helpers intentionally not present; those belong to RejectionLogger

# --------------------------------------------------------------------------
# Obligation creation logger
@dataclass
class CreateEvent:
    id: int
    require: str
    source: Optional[str]
    die: int
    planes: List[int]
    blocks: List[int]
    pages: List[int]
    deadline_us: float
    hard_slot: bool
    arity: int
    context: str
    stripe: Optional[int] = None
    page_index: Optional[int] = None
    created_at_us: Optional[float] = None

class CreationLogger:
    def __init__(self):
        self.rows: List[CreateEvent] = []

    def log(self, ob: "Obligation", context: str, stripe: Optional[int]=None, page_index: Optional[int]=None, created_at_us: Optional[float]=None):
        planes = sorted({t.plane for t in ob.targets})
        blocks = sorted({t.block for t in ob.targets})
        pages  = sorted({t.page for t in ob.targets if t.page is not None})
        die = ob.targets[0].die if ob.targets else -1
        ev = CreateEvent(
            id=ob.id,
            require=ob.require,
            source=getattr(ob, "source", None),
            die=die,
            planes=planes,
            blocks=blocks,
            pages=pages,
            deadline_us=ob.deadline_us,
            hard_slot=ob.hard_slot,
            arity=len(planes),
            context=context,
            stripe=stripe,
            page_index=page_index,
            created_at_us=created_at_us,
        )
        self.rows.append(ev)

    def to_csv(self, path: str = "obligation_creations.csv"):
        if not self.rows:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id","require","source","die","planes","blocks","pages","deadline_us","hard_slot","arity","context","stripe","page_index","created_at_us"])
            for r in self.rows:
                w.writerow([
                    r.id,
                    r.require,
                    r.source,
                    r.die,
                    ":".join(map(str,r.planes)),
                    ":".join(map(str,r.blocks)),
                    ":".join(map(str,r.pages)),
                    r.deadline_us,
                    int(r.hard_slot),
                    r.arity,
                    r.context,
                    r.stripe,
                    r.page_index,
                    r.created_at_us,
                ])
# --------------------------------------------------------------------------
# Utils
def sample_dist(d: Dict[str, Any]) -> float:
    k = d["kind"]
    if k == "fixed": return float(d["value"])
    if k == "normal":
        m, s, mn = d["mean"], d["std"], d.get("min", 0.0)
        v = random.gauss(m, s); return max(v, mn)
    if k == "exp": return random.expovariate(d["lambda"])
    raise ValueError(f"unknown dist kind: {k}")

def parse_hook_key(label: str):
    parts = label.split(".")
    if len(parts) == 2: return parts[0], parts[1], None
    if len(parts) == 3: return parts[0], parts[1], parts[2]
    return None, None, None

def get_phase_dist(cfg: Dict[str,Any], hook_label: str):
    base, state, _ = parse_hook_key(hook_label)
    pc = cfg.get("phase_conditional", {})
    keys=[]
    # POS granularity removed by plan; only OP.STATE and DEFAULT are considered
    # Expand keys to try alias/base variants to support configs written with ALIAS or BASE.
    if base and state:
        keys.append(f"{base}.{state}")
    keys.append("DEFAULT")
    for key in keys:
        dist = pc.get(key)
        if dist and sum(dist.values())>0: return dist, key
    return None, None

def roulette_pick(dist: Dict[str, float], allow: set) -> Optional[str]:
    items=[(n,p) for n,p in dist.items() if n in allow and p>0]
    if not items: return None
    tot=sum(p for _,p in items); r=random.random()*tot; acc=0.0; pick=items[-1][0]
    for n,p in items:
        acc+=p
        if r<=acc: pick=n; break
    return pick

# --- alias/base helpers for hook label resolution ---
def _op_alias_candidates(kind: str) -> List[str]:
    return CANDIDATES_OP_BASE.get(kind, [])

def _op_base_from_alias(op: str) -> Optional[str]:
    return MAPPING_OP_BASE.get(op, None)

def get_admission_delta(cfg: Dict[str,Any], hook_label: str, op_base: str) -> float:
    adm = cfg.get("admission", {})
    if op_base in adm.get("op_overrides", {}):
        return float(adm["op_overrides"][op_base])
    return float(adm.get("default_delta_us", 0.3))

# --------------------------------------------------------------------------
# Builders
def build_operation(name:str, kind: Enum, cfg_op: Dict[str, Any], targets: List[Address]) -> Operation:
    states=[]
    for s in cfg_op["states"]:
        states.append(StateSeg(name=s["name"], dur_us=sample_dist(s["dist"]), bus=bool(s.get("bus", False))))
    return Operation(name=name, base=kind, targets=targets, states=states)

def get_op_duration(op: Operation) -> float:
    return sum(seg.dur_us for seg in op.states)

def get_nominal_duration(cfg: Dict[str,Any], op_name: str) -> float:
    """Sum of nominal state durations for an op kind from cfg.op_specs (assumes fixed)."""
    spec = cfg.get("op_specs", {}).get(op_name, {})
    total = 0.0
    for st in spec.get("states", []):
        dist = st.get("dist", {})
        # _coerce_states_to_fixed ensures fixed value is present
        total += float(dist.get("value", 0.0))
    return total

# --------------------------------------------------------------------------
# Address & Bus Managers (with block/plane redefinition + state rails)
class AddressManager:
    """
    v2 상태 모델 (block-scoped):
    - addr_state_committed[(die, block)] : int  # last committed program page (-1=ERASED, -2=initial)
    - addr_state_future[(die, block)]    : int  # future after reservations
    - programmed_committed[(die)] : Set[(block, page)]
    - write_head[(die, plane)] : int  # block index assigned to plane (block % planes == plane)
    """
    def __init__(self, cfg: Dict[str, Any]):
        topo=cfg["topology"]; self.cfg=cfg
        self.dies=topo["dies"]; self.planes=topo["planes"]
        self.blocks=topo["blocks"]; self.pages_per_block=topo["pages_per_block"]

        self.available={(die,p):0.0 for p in range(self.planes) for die in range(self.dies)}  # per-plane availability time
        self.resv={(die,p):[] for p in range(self.planes) for die in range(self.dies)}        # per-plane reservations: (start,end,block)
        self.bus_resv: List[Tuple[float,float]] = []            # global bus reservations

        # ---- content validity tracking (future + last-commit times) ----
        # Scheduled-but-not-committed windows
        self.future_erase_by_block: Dict[Tuple[int,int], List[Tuple[float,float]]] = {}
        self.future_program_by_page: Dict[Tuple[int,int,int], List[Tuple[float,float]]] = {}
        # Last committed times
        self.last_erase_end: Dict[Tuple[int,int], float] = {}
        self.last_program_end: Dict[Tuple[int,int,int], float] = {}

        init_state = int(cfg.get("address_init_state", -2))
        self.addr_state_committed: Dict[Tuple[int,int], int] = {}
        self.addr_state_future:    Dict[Tuple[int,int], int] = {}
        self.programmed_committed: Dict[Tuple[int], Set[Tuple[int,int]]] = {(die,): set() for die in range(self.dies)}
        self.programmed_future:    Dict[Tuple[int], Set[Tuple[int,int]]] = {(die,): set() for die in range(self.dies)}
        self.write_head: Dict[Tuple[int,int], int] = {}  # (die,plane)->block

        for die in range(self.dies):
            for b in range(self.blocks):
                self.addr_state_committed[(die,b)] = init_state
                self.addr_state_future[(die,b)]    = init_state
                self.future_erase_by_block[(die,b)] = []
                self.last_erase_end[(die,b)] = float('-inf')
            for p in range(self.planes):
                self.write_head[(die,p)] = p  # first block of that plane stripe

    # ---- helpers for plane/block mapping ----
    def plane_of(self, block:int) -> int:
        return block % self.planes

    def iter_blocks_of_plane(self, plane:int):
        for b in range(plane, self.blocks, self.planes):
            yield b

    def _blocks_of_plane_list(self, plane:int) -> List[int]:
        return list(self.iter_blocks_of_plane(plane))

    def _iter_blocks_of_plane_cyclic(self, plane:int, start_block:int):
        blocks = self._blocks_of_plane_list(plane)
        if not blocks:
            return
        # align start_block to stripe of plane
        try:
            start_idx = blocks.index(start_block)
        except ValueError:
            # if not in list, map to nearest stripe position
            try:
                start_idx = blocks.index(plane)
            except ValueError:
                start_idx = 0
        n = len(blocks)
        for i in range(n):
            yield blocks[(start_idx + i) % n]

    # ---- observations ----
    def available_at(self, die:int, plane:int)->float: return self.available[(die,plane)]

    def earliest_start_for_scope(self, die:int, scope: Scope, plane_set: Optional[List[int]]=None)->float:
        if scope==Scope.DIE_WIDE:
            planes=list(range(self.planes))
        elif scope==Scope.PLANE_SET and plane_set is not None:
            planes=plane_set
        else:
            planes=[plane_set[0]] if plane_set else [0]
        return max(self.available[(die,p)] for p in planes)

    def candidate_start_for_scope(self, now_us: float, die:int, scope: Scope, plane_set: Optional[List[int]]=None)->float:
        """실제 스케줄러가 사용할 시작 후보시각 = max(now, earliest_start_for_scope)."""
        t_planes = self.earliest_start_for_scope(die, scope, plane_set)
        return quantize(max(now_us, t_planes))

    def observe_states(self, die:int, plane:int, now_us: float):
        pgmable_blocks=0; readable_blocks=0
        cnt=0
        for b in self.iter_blocks_of_plane(plane):
            cnt += 1
            fut=self.addr_state_future[(die,b)]
            com=self.addr_state_committed[(die,b)]
            if fut < self.pages_per_block-1: pgmable_blocks += 1
            if com >= 0: readable_blocks += 1
        total=max(1, cnt)
        def bucket(x):
            r=x/total
            return "low" if r<0.34 else ("mid" if r<0.67 else "high")
        pgmable_ratio=bucket(pgmable_blocks)
        readable_ratio=bucket(readable_blocks)
        plane_busy_frac="high" if self.available_at(die,plane)>now_us else "low"
        return ({"pgmable_ratio": pgmable_ratio, "readable_ratio": readable_ratio, "cls":"host"},
                {"plane_busy_frac": plane_busy_frac})

    # ---- bus segments & gating ----
    def bus_segments_for_op(self, op: Operation)->List[Tuple[float,float]]:
        if op.name == "SR" and op.states[0].name == "ISSUE":
            print(f"op: {op.name} state: {op.states[0].name} bus: {op.states[0].bus} dur_us: {op.states[0].dur_us}")
        segs=[]; t=0.0
        for s in op.states:
            if s.bus: segs.append((t, t+s.dur_us))
            t+=s.dur_us
        return segs

    def bus_precheck(self, op: Operation, start_hint: float, segs: List[Tuple[float,float]])->bool:
        for (off0,off1) in segs:
            a0, a1 = quantize(start_hint+off0), quantize(start_hint+off1)
            for (s,e) in self.bus_resv:
                if not (a1<=s or e<=a0):
                    if op.name == "SR" and op.states[0].name == "ISSUE":
                        print(f"bus_precheck: op: {op.name} state: {op.states[0].name} bus: {op.states[0].bus} dur_us: {op.states[0].dur_us} start_hint: {start_hint} off0: {off0} off1: {off1} segs: {segs} a0: {a0} a1: {a1} s: {s} e: {e}")
                    return False
        return True

    def bus_reserve(self, start_time: float, segs: List[Tuple[float,float]]):
        for (off0,off1) in segs:
            self.bus_resv.append((quantize(start_time+off0), quantize(start_time+off1)))

    # ---- future/committed helpers ----
    def _next_page_future(self, die:int, block:int)->int:
        last = self.addr_state_future[(die,block)]
        return (last + 1) if last < self.pages_per_block-1 else self.pages_per_block

    def _find_block_for_page_future_on_plane(self, die:int, plane:int, target_page:int)->Optional[int]:
        for b in self.iter_blocks_of_plane(plane):
            if self._next_page_future(die,b) == target_page:
                return b
        return None

    def _first_erased_block_on_plane(self, die:int, plane:int)->Optional[int]:
        for b in self.iter_blocks_of_plane(plane):
            if self.addr_state_future[(die,b)] == -1:
                return b
        return None

    def _committed_pages_on_plane(self, die:int, plane:int)->Set[int]:
        return {p for (b,p) in self.programmed_committed[(die,)] if (b % self.planes)==plane}

    def _future_pages_on_plane(self, die:int, plane:int)->Set[int]:
        return {p for (b,p) in self.programmed_future[(die,)] if (b % self.planes)==plane}

    # ---- planner (non-greedy; degrade fanout; READ=committed, PROGRAM=future) ----
    def _random_plane_sets(self, fanout:int, tries:int, start_plane:int)->List[List[int]]:
        """난수 기반 plane-set 후보 생성 (재현성은 random.seed에 따름)"""
        P=list(range(self.planes)); out=[]
        for _ in range(tries):
            cand=set(random.sample(P, min(fanout,len(P))))
            if start_plane not in cand and random.random()<0.6:
                # 무작위로 하나 제거하여 start_plane 포함
                if len(cand)>0:
                    rem=random.choice(sorted(cand))
                    cand.remove(rem)
                cand.add(start_plane)
            if len(cand)==fanout:
                key=tuple(sorted(cand))
                if key not in out: out.append(key)
        return [list(ps) for ps in dict.fromkeys(out)]

    def plan_multiplane(self, kind: Enum, die:int, start_plane:int, desired_fanout:int, interleave:bool)\
            -> Optional[Tuple[List[Address], List[int], Scope]]:
        if desired_fanout<1: desired_fanout=1
        tries=self.cfg["policy"]["planner_max_tries"]
        for f in range(desired_fanout, 0, -1):
            for plane_set in self._random_plane_sets(f, tries, start_plane):
                if kind==OpKind.READ:
                    commons=None
                    for pl in plane_set:
                        pages=self._future_pages_on_plane(die, pl)
                        commons=pages if commons is None else (commons & pages)
                        if not commons: break
                    if not commons: continue
                    page=random.choice(sorted(list(commons)))
                    targets=[]
                    for pl in plane_set:
                        blks=[b for (b,p) in self.programmed_future[(die,)] if p==page and (b%self.planes)==pl]
                        if not blks: targets=[]; break
                        targets.append(Address(die,pl,blks[0],page))
                    if not targets: continue
                    return targets, plane_set, Scope.PLANE_SET

                elif kind==OpKind.PROGRAM:
                    # candidate page: mode of next pages across write heads; or 0 if fresh erased exists
                    nxts=[]
                    for pl in plane_set:
                        b_head=self.write_head[(die,pl)]
                        nx=self._next_page_future(die,b_head)
                        nxts.append(None if nx>=self.pages_per_block else nx)
                    candidates=[]
                    vals=[x for x in nxts if x is not None]
                    if vals:
                        freq={}
                        for v in vals: freq[v]=freq.get(v,0)+1
                        candidates.append(max(freq.items(), key=lambda x:x[1])[0])
                    if all(self._first_erased_block_on_plane(die,pl) is not None for pl in plane_set):
                        if 0 not in candidates: candidates.append(0)
                    if not candidates: continue

                    chosen=None
                    search_order = str(self.cfg.get("addressing", {}).get("program", {}).get("search_order", "ascending")).lower()
                    for page in candidates:
                        tlist=[]
                        ok=True
                        for pl in plane_set:
                            b_head=self.write_head[(die,pl)]
                            if self._next_page_future(die,b_head)==page:
                                b=b_head
                            else:
                                if search_order=="cyclic_from_write_head":
                                    b=None
                                    for bb in self._iter_blocks_of_plane_cyclic(pl, b_head):
                                        if self._next_page_future(die,bb)==page:
                                            b=bb; break
                                    if b is None:
                                        b=self._find_block_for_page_future_on_plane(die,pl,page)
                                else:
                                    b=self._find_block_for_page_future_on_plane(die,pl,page)
                                if b is None: ok=False; break
                            tlist.append(Address(die,pl,b,page))
                        if ok: chosen=tlist; break
                    if not chosen: continue
                    # debug log
                    return chosen, plane_set, Scope.DIE_WIDE

                elif kind==OpKind.ERASE:
                    targets=[]
                    pick_strategy = str(self.cfg.get("addressing", {}).get("erase", {}).get("pick_strategy", "ascending_non_erased")).lower()
                    for pl in plane_set:
                        chosen_b=None
                        if pick_strategy=="cyclic_from_write_head":
                            start_b = self.write_head[(die,pl)]
                            for bb in self._iter_blocks_of_plane_cyclic(pl, start_b):
                                if self.addr_state_future[(die,bb)] >= 0:
                                    chosen_b=bb; break
                        else:  # ascending_non_erased (default)
                            for bb in self.iter_blocks_of_plane(pl):
                                if self.addr_state_future[(die,bb)] >= 0:
                                    chosen_b=bb; break
                        if chosen_b is None:
                            chosen_b=self.write_head[(die,pl)]
                        targets.append(Address(die, pl, chosen_b, 0))  # page=0 for logging
                    # debug log
                    return targets, plane_set, Scope.DIE_WIDE

                elif kind==OpKind.SR:
                    # page=0 for uniform address shape
                    tgt = [Address(die, plane_set[0], plane_set[0], 0)]
                    return tgt, plane_set[:1], Scope.NONE

        return None

    # ---- precheck/reserve/future/commit ----
    def precheck_planescope(self, kind: Enum, targets: List[Address], start_hint: float, scope: Scope)->bool:
        start_hint=quantize(start_hint); end_hint=start_hint
        die=targets[0].die
        # time overlap check
        if scope==Scope.DIE_WIDE: planes={(die,p) for p in range(self.planes)}
        elif scope==Scope.PLANE_SET: planes={(t.die,t.plane) for t in targets}
        else: planes={(targets[0].die, targets[0].plane)}
        for (d,p) in planes:
            for (s,e,_) in self.resv[(d,p)]:
                if not (end_hint<=s or e<=start_hint):
                    print(f"[PRECCHK] time_overlap d={d} p={p} start={start_hint:.2f} overlaps=({s:.2f},{e:.2f}) scope={scope.name}")
                    return False
        # address/plane consistency + rules
        for t in targets:
            if t.plane != (t.block % self.planes):
                print(f"[PRECCHK] plane_consistency die={t.die} plane={t.plane} block={t.block} expected={t.block % self.planes}")
                return False
            com=self.addr_state_committed[(t.die,t.block)]
            fut=self.addr_state_future[(t.die,t.block)]
            if kind==OpKind.PROGRAM:
                if t.page is None:
                    print(f"[PRECCHK] program_page_none die={t.die} block={t.block}")
                    return False
                if t.page != fut + 1:
                    print(f"[PRECCHK] program_seq die={t.die} block={t.block} want={fut+1} got={t.page} fut={fut} com={com}")
                    return False
                if t.page >= self.pages_per_block:
                    print(f"[PRECCHK] program_oob die={t.die} block={t.block} page={t.page} pages_per_block={self.pages_per_block}")
                    return False
                # prevent programming a page that is under a future erase window overlapping start_hint
                guard = float(self.cfg.get("policy",{}).get("program_erase_conflict_guard_us", 0.0))
                wins = self.future_erase_by_block.get((t.die,t.block), [])
                for (s,e) in wins:
                    if not ((e <= (start_hint - guard)) or (end_hint <= s)):
                        print(f"[PRECCHK] program_future_erase_conflict die={t.die} block={t.block} page={t.page} win=({s:.2f},{e:.2f})")
                        return False
            elif kind==OpKind.READ:
                if t.page is None:
                    print(f"[PRECCHK] read_page_none die={t.die} block={t.block}")
                    return False
                if fut <= -1:
                    print(f"[PRECCHK] read_seq die={t.die} block={t.block} page={t.page} fut={fut} com={com}")
                    return False
                if t.page >= self.pages_per_block:
                    print(f"[PRECCHK] read_oob die={t.die} block={t.block} page={t.page} pages_per_block={self.pages_per_block}")
                    return False
                # consider future program windows: if page is not yet committed now, allow if a future PROGRAM window ends before this READ starts
                committed_now = ((t.block, t.page) in self.programmed_committed[(t.die,)])
                pol = self.cfg.get("policy", {})
                if (not committed_now):
                    if bool(pol.get("read_requires_committed", False)):
                        print(f"[PRECCHK] read_requires_committed die={t.die} block={t.block} page={t.page}")
                        return False
                    if bool(pol.get("read_allow_future_program", True)):
                        wins = self.future_program_by_page.get((t.die,t.block,int(t.page)), [])
                        guard = float(pol.get("read_future_program_guard_us", 0.01))
                        prog_ok = any(e <= (start_hint - guard) for (s,e) in wins)
                        if not prog_ok:
                            print(f"[PRECCHK] read_not_committed (no prior future program) die={t.die} block={t.block} page={t.page} at={start_hint:.2f}")
                            return False
                    else:
                        print(f"[PRECCHK] read_not_committed (future program not allowed) die={t.die} block={t.block} page={t.page}")
                        return False
                # also block READ if a future ERASE overlaps or ends at/after start (treat boundary as conflict)
                wins_er = self.future_erase_by_block.get((t.die,t.block), [])
                er_guard = float(pol.get("read_erase_guard_margin_us", 0.0))
                for (s,e) in wins_er:
                    if not ((e <= (start_hint - er_guard)) or (end_hint <= s)):
                        print(f"[PRECCHK] read_future_erase_conflict die={t.die} block={t.block} page={t.page} win=({s:.2f},{e:.2f})")
                        return False
            elif kind==OpKind.ERASE:
                pass
        return True

    def reserve_planescope(self, op: Operation, start: float, end: float):
        die=op.targets[0].die; start=quantize(start); end=quantize(end)
        if op.meta.get("scope")=="DIE_WIDE":
            planes=[(die,p) for p in range(self.planes)]
        elif op.meta.get("scope")=="PLANE_SET":
            planes=[(t.die,t.plane) for t in op.targets]
        else:
            planes=[(op.targets[0].die, op.targets[0].plane)]
        for (d,p) in planes:
            self.available[(d,p)]=max(self.available[(d,p)], end)
            self.resv[(d,p)].append((start,end,None))

    def register_future(self, op: Operation, start: float, end: float):
        for t in op.targets:
            key=(t.die,t.block)
            if op.base==OpKind.PROGRAM and t.page is not None:
                self.addr_state_future[key] = max(self.addr_state_future[key], t.page)
                self.programmed_future[(t.die,)].add((t.block, t.page))
                # advance write head if full
                nxt = self._next_page_future(t.die, t.block)
                if nxt >= self.pages_per_block:
                    er = self._first_erased_block_on_plane(t.die, t.plane)
                    if er is not None:
                        self.write_head[(t.die, t.plane)] = er
                # future program window register
                kpp=(t.die,t.block,int(t.page))
                self.future_program_by_page.setdefault(kpp, []).append((quantize(start), quantize(end)))
            elif op.base==OpKind.ERASE:
                self.addr_state_future[key] = -1
                self.programmed_future[(t.die,)].clear()
                on_erase = str(self.cfg.get("addressing", {}).get("write_head", {}).get("on_erase", "to_erased_block")).lower()
                if on_erase=="round_robin_next":
                    # move to next block in stripe after erased block
                    next_b = t.block + self.planes
                    if next_b >= self.blocks:
                        next_b = t.plane  # wrap to first stripe block for this plane
                    self.write_head[(t.die, t.plane)] = next_b
                elif on_erase=="stay":
                    # do not change write_head
                    pass
                else:  # to_erased_block (default)
                    self.write_head[(t.die, t.plane)] = t.block
                # future erase window register
                self.future_erase_by_block.setdefault(key, []).append((quantize(start), quantize(end)))

    def commit(self, op: Operation):
        for t in op.targets:
            key=(t.die,t.block)
            if op.base==OpKind.PROGRAM and t.page is not None:
                self.addr_state_committed[key] = max(self.addr_state_committed[key], t.page)
                self.programmed_committed[(t.die,)].add((t.block, t.page))
                # record last program end
                try:
                    self.last_program_end[(t.die,t.block,int(t.page))] = float(self.available[(t.die,t.plane)])
                except Exception:
                    pass
            elif op.base==OpKind.ERASE:
                self.addr_state_committed[key] = -1
                self.programmed_committed[(t.die,)] = {
                    pp for pp in self.programmed_committed[(t.die,)] if pp[0] != t.block
                }
                # record last erase end
                try:
                    self.last_erase_end[(t.die,t.block)] = float(self.available[(t.die,t.plane)])
                except Exception:
                    pass

# --------------------------------------------------------------------------
# Exclusion Manager (runtime blocking windows)
@dataclass
class ExclWindow:
    start: float; end: float
    scope: str
    die: Optional[int]
    tokens: Set[str]  # {"ANY"}, {"BASE:READ"}, {"ALIAS:MUL_READ"} ...

class ExclusionManager:
    def __init__(self, cfg: Dict[str,Any]):
        self.cfg = cfg
        self.global_windows: List[ExclWindow] = []
        self.die_windows: Dict[int, List[ExclWindow]] = {}

    def _state_windows(self, op: Operation, start: float, states: List[str]) -> List[Tuple[float,float]]:
        wins=[]; t=start
        segs=[]
        for s in op.states:
            segs.append((s.name, t, t+s.dur_us)); t+=s.dur_us
        if states==["*"]:
            return [(segs[0][1], segs[-1][2])]
        want=set(states)
        for name, s0, s1 in segs:
            if name in want: wins.append((s0,s1))
        return wins

    def _token_blocks(self, tok: str, op: Operation) -> bool:
        if tok=="ANY": return True
        if tok.startswith("BASE:"):
            base=tok.split(":")[1]
            return op.base.name==base
        if tok.startswith("ALIAS:"):
            alias=tok.split(":")[1]
            if alias=="MUL_READ": return (op.base==OpKind.READ and op.meta.get("arity",1)>1)
            if alias=="SIN_READ": return (op.base==OpKind.READ and op.meta.get("arity",1)==1)
        return False

    def allowed(self, op: Operation, start: float, end: float) -> bool:
        for w in self.global_windows:
            if not (end<=w.start or w.end<=start):
                if any(self._token_blocks(tok, op) for tok in w.tokens): return False
        die=op.targets[0].die
        for w in self.die_windows.get(die, []):
            if not (end<=w.start or w.end<=start):
                if any(self._token_blocks(tok, op) for tok in w.tokens): return False
        return True

    def register(self, op: Operation, start: float):
        rules = self.cfg.get("constraints",{}).get("exclusions",[])
        die = op.targets[0].die
        for r in rules:
            # if_op: op_base
            when=r["when"]; if_op=when.get("op")
            if if_op != op.base.name:
                if not (op.base==OpKind.READ and if_op=="READ"):
                    continue
            alias_need = when.get("alias")
            if alias_need=="MUL" and not (op.meta.get("arity",1)>1): continue
            if alias_need=="SIN" and not (op.meta.get("arity",1)==1): continue
            states = when.get("states", ["*"])
            windows = self._state_windows(op, start, states)
            scope=r.get("scope","GLOBAL"); tokens=set(r.get("blocks",[]))
            for (s0,s1) in windows:
                w=ExclWindow(start=quantize(s0), end=quantize(s1), scope=scope,
                             die=(die if scope=="DIE" else None), tokens=tokens)
                if scope=="GLOBAL":
                    self.global_windows.append(w)
                else:
                    self.die_windows.setdefault(die,[]).append(w)

# --------------------------------------------------------------------------
# Latch Manager: READ.end_us ~ DOUT.end_us 동안 (die,plane) 래치 보존 락
@dataclass
class _Latch:
    start_us: float
    end_us: Optional[float]  # None -> open until DOUT end

class LatchManager:
    def __init__(self):
        # (die, plane) -> _Latch
        self._locks: Dict[Tuple[int,int], _Latch] = {}

    def plan_lock_after_read(self, targets: List[Address], read_end_us: float):
        for t in targets:
            self._locks[(t.die, t.plane)] = _Latch(start_us=read_end_us, end_us=None)

    def release_on_dout_end(self, targets: List[Address], now_us: float):
        for t in targets:
            key=(t.die, t.plane)
            if key in self._locks:
                del self._locks[key]

    def _is_locked_at(self, die:int, plane:int, t0:float) -> bool:
        lock = self._locks.get((die,plane))
        if not lock: return False
        if t0 < lock.start_us: return False
        if lock.end_us is None: return True
        return t0 < lock.end_us

    def allowed(self, op: Operation, start_hint: float) -> bool:
        # DOUT/SR allowed (does not corrupt latch)
        if op.base in (OpKind.DOUT, OpKind.SR):
            return True
        if op.base not in (OpKind.READ, OpKind.PROGRAM, OpKind.ERASE):
            return True

        die = op.targets[0].die
        scope = op.meta.get("scope", "PLANE_SET")

        if scope == "DIE_WIDE":
            for (d,p), _ in self._locks.items():
                if d == die and self._is_locked_at(d, p, start_hint):
                    return False
            return True
        else:
            for t in op.targets:
                if self._is_locked_at(t.die, t.plane, start_hint):
                    return False
            return True

# --------------------------------------------------------------------------
# State Timeline (OP.STATE + END with inf tail)
@dataclass
class StateInterval:
    die: int
    plane: int
    op_name: str
    op_base: str
    state: str
    start_us: float
    end_us: float

class StateTimeline:
    def __init__(self):
        self.by_plane: Dict[Tuple[int,int], List[StateInterval]] = {}
        self.last_end_idx: Dict[Tuple[int,int], int] = {}
        # 1) per-plane starts cache for binary search
        self._starts_by_plane: Dict[Tuple[int,int], List[float]] = {}
        # 2) DIE-level merged index (exclude END)
        self._die_index: Dict[int, List[StateInterval]] = {}
        self._die_starts: Dict[int, List[float]] = {}
        # 3) GLOBAL merged index (exclude END)
        self._global_index: List[StateInterval] = []
        self._global_starts: List[float] = []

    def _insert_plane(self, key: Tuple[int,int], seg: StateInterval):
        lst = self.by_plane.setdefault(key, [])
        starts = self._starts_by_plane.setdefault(key, [s.start_us for s in lst])
        import bisect as _bisect
        # refresh starts list if out-of-sync in size
        if len(starts) != len(lst):
            starts[:] = [s.start_us for s in lst]
        i = _bisect.bisect_left(starts, seg.start_us)
        lst.insert(i, seg)
        starts.insert(i, seg.start_us)

    def _insert_die(self, die: int, seg: StateInterval):
        if seg.state == "END":
            return
        lst = self._die_index.setdefault(die, [])
        starts = self._die_starts.setdefault(die, [s.start_us for s in lst])
        import bisect as _bisect
        if len(starts) != len(lst):
            starts[:] = [s.start_us for s in lst]
        i = _bisect.bisect_left(starts, seg.start_us)
        lst.insert(i, seg)
        starts.insert(i, seg.start_us)

    def _insert_global(self, seg: StateInterval):
        if seg.state == "END":
            return
        lst = self._global_index
        starts = self._global_starts
        import bisect as _bisect
        if len(starts) != len(lst):
            starts[:] = [s.start_us for s in lst]
        i = _bisect.bisect_left(starts, seg.start_us)
        lst.insert(i, seg)
        starts.insert(i, seg.start_us)

    def reserve_op(self, die: int, plane: int, op_name: str, op_base: str, states: List[Tuple[str, float]], start_us: float, affect: bool):
        if not affect:
            return
        import math as _math
        key=(die, plane)
        lst = self.by_plane.setdefault(key, [])
        # truncate previous END
        end_idx = self.last_end_idx.get(key)
        if end_idx is not None and 0 <= end_idx < len(lst) and lst[end_idx].state == "END":
            lst[end_idx].end_us = start_us
        # insert concrete states
        t = start_us
        for st_name, dur in states:
            seg = StateInterval(die, plane, op_name, op_base, st_name, t, t + float(dur))
            self._insert_plane(key, seg)
            self._insert_die(die, seg)
            self._insert_global(seg)
            t += float(dur)
        # insert new END
        end_seg = StateInterval(die, plane, op_name, op_base, "END", t, _math.inf)
        self._insert_plane(key, end_seg)
        self.last_end_idx[key] = lst.index(end_seg)

    def state_at(self, die: int, plane: int, t: float) -> Optional[str]:
        key=(die, plane)
        lst = self.by_plane.get(key, [])
        if not lst:
            return None
        starts = self._starts_by_plane.get(key)
        if starts is None or len(starts) != len(lst):
            starts = [s.start_us for s in lst]
            self._starts_by_plane[key] = starts
        import bisect as _bisect
        i = _bisect.bisect_right(starts, t) - 1
        if 0 <= i < len(lst):
            s = lst[i]
            if s.start_us <= t < s.end_us:
                return f"{s.op_base}.{s.state}"
        return None

    def overlaps(self, die: int, plane: int, start: float, end: float, pred=None) -> bool:
        key=(die, plane)
        lst = self.by_plane.get(key, [])
        if not lst:
            return False
        starts = self._starts_by_plane.get(key)
        if starts is None or len(starts) != len(lst):
            starts = [s.start_us for s in lst]
            self._starts_by_plane[key] = starts
        import bisect as _bisect
        # candidates have seg.start_us < end
        idx = _bisect.bisect_left(starts, end)
        # scan a small window backward to include possibly overlapping segs before idx
        i = max(0, idx - 1)
        while i < len(lst) and lst[i].start_us < end:
            seg = lst[i]
            if seg.start_us < end and start < seg.end_us:
                if pred is None or pred(seg):
                    return True
            i += 1
        return False

    def to_dataframe(self):
        """
        Materialize state timeline to a pandas DataFrame for visualization/export.
        - Includes END segments if they have a finite end (infinite tail segments are skipped)
        - Columns: start_us, end_us, die, plane, op_state(OP.STATE), lane("die/plane"), op_name(OP), state, dur_us
        """
        rows = []
        for (die, plane), lst in self.by_plane.items():
            for seg in lst:
                # keep END only if it has a finite end time (tail with +inf is skipped)
                if seg.state == "END":
                    try:
                        import math as _math
                        if _math.isinf(seg.end_us):
                            continue
                    except Exception:
                        # best-effort: if end_us is a very large number, still include
                        pass
                rows.append({
                    "start_us": float(seg.start_us),
                    "end_us": float(seg.end_us),
                    "die": int(die),
                    "plane": int(plane),
                    "op_state": f"{seg.op_base}.{seg.state}",
                    "lane": f"{die}/{plane}",
                    "op_name": seg.op_name,
                    "state": seg.state,
                })
        try:
            import pandas as _pd
            df = _pd.DataFrame(rows)
            if not df.empty:
                df = df.sort_values(["die", "plane", "start_us", "end_us"]).reset_index(drop=True)
                df["dur_us"] = df["end_us"] - df["start_us"]
            return df
        except Exception:
            # pandas not available or other issue; return minimal fallback-like structure
            return rows  # type: ignore[return-value]

    def to_csv(self, path: str):
        """Export state timeline to CSV at given path."""
        try:
            import pandas as _pd
            df = self.to_dataframe()
            if isinstance(df, list):
                # fallback without pandas
                import csv as _csv
                with open(path, "w", encoding="utf-8", newline="") as f:
                    if not df:
                        return
                    writer = _csv.DictWriter(f, fieldnames=list(df[0].keys()))
                    writer.writeheader()
                    writer.writerows(df)
            else:
                df.to_csv(path, index=False)
        except Exception as _e:
            print(f"[STATE_TIMELINE] export failed: {_e}")

    def overlaps_die(self, die: int, start: float, end: float, pred=None) -> bool:
        lst = self._die_index.get(die, [])
        if not lst:
            return False
        starts = self._die_starts.get(die)
        if starts is None or len(starts) != len(lst):
            starts = [s.start_us for s in lst]
            self._die_starts[die] = starts
        import bisect as _bisect
        idx = _bisect.bisect_left(starts, end)
        i = max(0, idx - 1)
        while i < len(lst) and lst[i].start_us < end:
            seg = lst[i]
            if seg.start_us < end and start < seg.end_us:
                if pred is None or pred(seg):
                    return True
            i += 1
        return False

    def overlaps_global(self, start: float, end: float, pred=None) -> bool:
        lst = self._global_index
        if not lst:
            return False
        starts = self._global_starts
        if len(starts) != len(lst):
            starts[:] = [s.start_us for s in lst]
        import bisect as _bisect
        idx = _bisect.bisect_left(starts, end)
        i = max(0, idx - 1)
        while i < len(lst) and lst[i].start_us < end:
            seg = lst[i]
            if seg.start_us < end and start < seg.end_us:
                if pred is None or pred(seg):
                    return True
            i += 1
        return False

# --------------------------------------------------------------------------
# Obligation Manager (with stats)
@dataclass(order=True)
class _ObHeapItem:
    deadline_us: float
    seq: int
    ob: "Obligation" = field(compare=False)

@dataclass
class Obligation:
    id: int
    # require: OpKind
    require: str
    targets: List[Address]
    deadline_us: float
    hard_slot: bool
    # bootstrap/flags
    source: Optional[str] = None
    skip_dout_creation: bool = False

class ObligationManager:
    def __init__(self, cfg_list: List[Dict[str,Any]], cfg_root: Optional[Dict[str,Any]] = None):
        self.specs = cfg_list
        self.cfg_root = cfg_root
        self.heap: List[_ObHeapItem] = []
        self._seq = 0
        self.assigned: Dict[int, Obligation] = {}
        self.stats = {
            "created":0,
            "assigned":0,
            "fulfilled":0,
            "fulfilled_in_time":0,
            "expired":0,
            # pop_urgent diagnostics
            "pop_calls":0,
            "pop_examined":0,
            "pop_kept":0,
            "pop_chosen":0,
            "pop_returned":0,
            # chosen but rejected in propose (consumed)
            "dropped":0,
            # extensions/requeues
            "extended_cycles":0,
            "extended_total":0,
            "requeued":0,
        }
        self.debug = False
        self._last_audit_lost = 0

    def _page_index_of_ob(self, ob: Obligation) -> int:
        try:
            if ob.require in (OpKind.PROGRAM, OpKind.READ):
                # multi-plane, same page index
                pages = [t.page for t in ob.targets if t.page is not None]
                return int(pages[0]) if pages else -1
            elif ob.require in (OpKind.ERASE, OpKind.DOUT):
                return int(ob.targets[0].page or 0)
        except Exception:
            return -1
        return -1

    def audit_order_all(self, where: str):
        # group by (require, die, source)
        groups: Dict[Tuple[str,int,Optional[str]], List[Tuple[int,int,float]]] = {}
        for it in self.heap:
            o = it.ob
            key = (o.require.name, o.targets[0].die, getattr(o, "source", None))
            groups.setdefault(key, []).append((o.id, self._page_index_of_ob(o), o.deadline_us))
        any_inv = False
        for (req_name, die, src), items in groups.items():
            items_sorted = sorted(items, key=lambda x: (x[1], x[2]))
            inv = []
            for i in range(len(items_sorted)):
                for j in range(i+1, len(items_sorted)):
                    id_i, p_i, dl_i = items_sorted[i]
                    id_j, p_j, dl_j = items_sorted[j]
                    if p_i < p_j and dl_i > dl_j:
                        inv.append(((id_i, p_i, dl_i), (id_j, p_j, dl_j)))
            if self.debug:
                print(f"[OBLIGAUD] order_check where={where} require={req_name} die={die} src={src} pages_deadlines={[(p, round(d,2)) for _,p,d in items_sorted]}")
                if inv:
                    print(f"[OBLIGAUD] INVERSION DETECTED where={where} require={req_name} die={die} src={src} inv={inv}")
            if inv:
                any_inv = True
        if any_inv and self.assert_on_inversion:
            raise AssertionError(f"Obligation deadline inversion detected at {where}")

    def _rebuild_heap(self):
        items = self.heap
        self.heap = []
        for it in items:
            heapq.heappush(self.heap, _ObHeapItem(deadline_us=it.ob.deadline_us, seq=it.ob.id, ob=it.ob))

    def requeue(self, ob: Obligation, delta_us: float = 0.2):
        prev = ob.deadline_us
        ob.deadline_us = quantize(ob.deadline_us + max(0.0, delta_us))
        heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=ob.id, ob=ob))
        self.stats["requeued"] += 1
        if self.debug:
            print(f"[OBLIGDBG] requeue: id={ob.id} prev_deadline={prev:.2f} new_deadline={ob.deadline_us:.2f}")

    def has_pending(self, source: Optional[str] = None) -> bool:
        if not self.heap:
            return False
        if source is None:
            return True
        for item in self.heap:
            if getattr(item.ob, "source", None) == source:
                return True
        return False

    def on_commit(self, op: Operation, now_us: float):
        # READ completion -> create DOUT obligation(s)
        for spec in self.specs:
            if spec["issuer"] == op.base.name:
                # Bootstrap 체인으로 미리 생성된 DOUT과 중복 생성 방지 가드
                if op.meta.get("source") == "bootstrap" or op.meta.get("skip_dout_creation"):
                    continue

                dt = sample_dist(spec["window_us"])
                base_deadline = quantize(now_us + dt)
                hard_slot = spec.get("priority_boost", {}).get("hard_slot", False)
                plane_stagger = spec.get("priority_boost", {}).get("plane_stagger_us", 0.2)
                # optional: amplify stagger by DOUT duration multiplier N
                n_mult = float(spec.get("priority_boost", {}).get("plane_stagger_by_dout_n", 0.0))
                if n_mult and n_mult > 0.0:
                    dout_dur = get_nominal_duration(self.cfg_root, "DOUT") if hasattr(self, "cfg_root") else 0.0
                    plane_stagger = max(plane_stagger, n_mult * dout_dur)

                # 멀티플레인 READ의 경우 plane 순서대로 DOUT을 분산 생성
                if op.base == OpKind.READ and len(op.targets) > 1:
                    sorted_targets = sorted(op.targets, key=lambda a: a.plane)
                    for idx, t in enumerate(sorted_targets):
                        self._seq += 1
                        ob = Obligation(
                            id=self._seq,
                            # require=OpKind[spec["require"]],
                            require=spec["require"],
                            targets=[t],
                            source='obligation.dout',
                            deadline_us=quantize(base_deadline + idx * plane_stagger),
                            hard_slot=hard_slot,
                        )
                        heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=ob.id, ob=ob))
                        self.stats["created"] += 1
                        print(f"[{now_us:7.2f} us] OBLIG  created: READ -> {ob.require} by {ob.deadline_us:7.2f} us, target(d{t.die},p{t.plane})")
                else:
                    # 단일 plane 또는 비-READ 발행자의 경우 기존 방식 유지
                    self._seq += 1
                    ob = Obligation(
                        id=self._seq,
                        # require=OpKind[spec["require"]],
                        require=spec["require"],
                        targets=op.targets,
                        deadline_us=base_deadline,
                        hard_slot=hard_slot,
                    )
                    heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=ob.id, ob=ob))
                    self.stats["created"] += 1
                    first = op.targets[0]
                    print(f"[{now_us:7.2f} us] OBLIG  created: {op.base.name} -> {ob.require} by {ob.deadline_us:7.2f} us, target(d{first.die},p{first.plane})")

    def pop_urgent(self, now_us: float, die:int, plane:int, horizon_us: float, earliest_start: float) -> Optional[Obligation]:
        if not self.heap:
            return None
        kept: List[_ObHeapItem] = []
        chosen: Optional[Obligation] = None
        now_us=quantize(now_us); earliest_start=quantize(earliest_start)
        self.stats["pop_calls"] += 1
        if self.debug:
            print(f"[OBLIGDBG] pop_urgent: heap_size={len(self.heap)} now={now_us:.2f} die={die} plane={plane} horizon={horizon_us:.2f} earliest_start={earliest_start:.2f}")
        while self.heap and not chosen:
            item = heapq.heappop(self.heap)
            ob=item.ob
            self.stats["pop_examined"] += 1
            same_die=(ob.targets[0].die==die)
            in_horizon=((ob.deadline_us - now_us) <= max(horizon_us, 0.0)) or ob.hard_slot
            feasible=(earliest_start <= ob.deadline_us)
            cond = (same_die and in_horizon and feasible)
            if cond:
                if self.debug:
                    print(f"[OBLIGDBG] pop_urgent: CHOOSE id={ob.id} req={ob.require} src={getattr(ob,'source',None)} deadline={ob.deadline_us:.2f} conds sd={same_die} hz={in_horizon} fs={feasible}")
                self.stats["pop_chosen"] += 1
                chosen=ob; break
            kept.append(item)
            self.stats["pop_kept"] += 1
            if self.debug:
                print(f"[OBLIGDBG] pop_urgent: SKIP  id={ob.id} req={ob.require} src={getattr(ob,'source',None)} deadline={ob.deadline_us:.2f} conds sd={same_die} hz={in_horizon} fs={feasible}")
        for it in kept: heapq.heappush(self.heap, it)
        self.stats["pop_returned"] += len(kept)
        if self.debug:
            succ = self.stats["pop_chosen"]
            calls = self.stats["pop_calls"]
            rate = (succ / calls) if calls else 0.0
            print(f"[OBLIGDBG] pop_urgent: heap_size_after={len(self.heap)} chosen={(chosen.id if chosen else None)} kept={len(kept)} returned={len(kept)} pop_succ_rate={rate:.3f} (chosen={succ}/calls={calls})")
        return chosen

    def mark_assigned(self, ob: Obligation):
        self.assigned[ob.id] = ob
        self.stats["assigned"] += 1
        if self.debug:
            print(f"[OBLIGDBG] mark_assigned: id={ob.id} require={ob.require} deadline={ob.deadline_us:.2f} src={getattr(ob,'source',None)} heap_size={len(self.heap)} assigned={len(self.assigned)}")

    def mark_fulfilled(self, ob: Obligation, now: float):
        self.assigned.pop(ob.id, None)
        self.stats["fulfilled"] += 1
        if now <= ob.deadline_us:
            self.stats["fulfilled_in_time"] += 1
        else:
            print(f"not_fulfilled: {ob.require} by {now:7.2f} us, deadline={ob.deadline_us:7.2f} us, target(d{ob.targets[0].die},p{ob.targets[0].plane})")
        if self.debug:
            print(f"[OBLIGDBG] mark_fulfilled: id={ob.id} at={now:.2f} heap_size={len(self.heap)} assigned={len(self.assigned)}")

    def expire_due(self, now: float):
        if not self.heap:
            return
        earliest = self.heap[0].deadline_us
        if earliest <= now:
            # extend all deadlines uniformly to preserve relative order
            delta = quantize(now - earliest + 0.2)
            for it in self.heap:
                it.ob.deadline_us = quantize(it.ob.deadline_us + delta)
            self._rebuild_heap()
            self.stats["extended_cycles"] += 1
            self.stats["extended_total"] += len(self.heap)
            if self.debug:
                print(f"[OBLIGDBG] extend_all: delta={delta:.2f} heap_size={len(self.heap)} new_earliest={self.heap[0].deadline_us:.2f}")
                # choose one representative ob to check ordering per kind
                rep = self.heap[0].ob

# --------------------------------------------------------------------------
# Policy Engine (phase-conditional + admission gating + backoff + latch check)
class PolicyEngine:
    def _reject(self, now_us: float, hook: PhaseHook, stage: str, reason: str,
                attempted: Optional[str], alias: Optional[str],
                fanout: Optional[int], plane_set: Optional[List[int]],
                earliest_start: Optional[float], admission_delta: Optional[float],
                detail: str = ""):
        if self.rejlog is None:
            return
        ob_id = None
        if stage == "obligation":
            # attach obligation id when available
            try:
                ob = getattr(self, 'current_obligation', None)
                if ob is not None:
                    ob_id = getattr(ob, 'id', None)
            except Exception:
                ob_id = None
        self.rejlog.log_reject(RejectEvent(
            now_us=now_us, die=hook.die, plane=hook.plane, hook=hook.label,
            stage=stage, attempted=attempted, alias=alias, fanout=fanout,
            plane_set=(str(sorted(plane_set)) if plane_set is not None else None),
            reason=reason, detail=detail,
            earliest_start=earliest_start, admission_delta=admission_delta,
            ob_id=ob_id
        ))

    def __init__(self, cfg, addr: AddressManager, obl: ObligationManager, excl: ExclusionManager,
                 rejlog: Optional[RejectionLogger] = None,
                 latch: Optional[LatchManager] = None,
                 state_timeline: Optional["StateTimeline"] = None):
        self.cfg=cfg; self.addr=addr; self.obl=obl; self.excl=excl
        self.stats={"alias_degrade":0}
        self.rejlog = rejlog or RejectionLogger()
        self.latch = latch or LatchManager()
        self.state_timeline = state_timeline
        # compile exclusion rules for data-driven predicates
        self._excl_rules = self._compile_exclusion_rules()
        # progressive sampling mode & adaptive sampler
        self._propose_mode = str(self.cfg.get("propose", {}).get("mode", "legacy")).lower()
        self._sampler = AdaptiveSampler(self.cfg)

    def _fanout_from_alias(self, op_base: str, alias_constraint: Optional[Tuple[str,int]], hook_label: str)->Tuple[int,bool]:
        fanout, interleave = get_phase_selection_override(self.cfg, hook_label, op_base)
        if alias_constraint:
            mode,val=alias_constraint
            if mode=="eq": fanout=val
            elif mode=="ge": fanout=max(fanout,val)
        return max(1,fanout), interleave

    def _exclusion_ok(self, op: Operation, start_hint: float) -> bool:
        dur = get_op_duration(op)
        end = quantize(start_hint + dur)
        return self.excl.allowed(op, start_hint, end)

    def _admission_ok(self, now_us: float, hook_label: str, op_base: str, start_hint: float, deadline: Optional[float]=None) -> bool:
        adm = self.cfg.get("admission", {})
        delta = get_admission_delta(self.cfg, hook_label, op_base)
        if deadline is not None:  # for obligations if bypass disabled
            delta = min(delta, max(0.0, deadline - now_us))
        return start_hint <= now_us + delta

    # --- exclusion rules compilation & predicates (data-driven) ---
    def _compile_exclusion_rules(self):
        rules = []
        try:
            for r in self.cfg.get("constraints", {}).get("exclusions", []):
                when = r.get("when", {})
                wop = str(when.get("op", "")).upper()
                states = when.get("states", ["*"])
                tokens = set(r.get("blocks", []))
                scope = str(r.get("scope", "GLOBAL")).upper()
                rules.append((wop, set(states), tokens, scope))
        except Exception:
            pass
        return rules

    def _token_blocks_labels(self, token: str, base_name: str, alias_label: Optional[str]) -> bool:
        if token == "ANY":
            return True
        if token.startswith("BASE:"):
            return base_name == token.split(":",1)[1]
        if token.startswith("ALIAS:"):
            return (alias_label or "") == token.split(":",1)[1]
        return False

    def _resolve_op_name(self, op_name: str) -> str:
        # return self.cfg.get("op_specs", {}).get(op, {}).get("base", op)
        if op_name in OP_ALIAS: return OP_ALIAS[op_name]["base"], OP_ALIAS[op_name]["fanout"]
        return op_name, None

    def _alias_label_for(self, base: str, arity: int) -> Optional[str]:
        # derive alias label from base and arity using helper candidates
        try:
            cands = _op_alias_candidates(base)
            if not cands:
                return None
            # pick MUL_* when arity>1 else SIN_*
            prefer = "MUL_" if arity > 1 else "SIN_"
            for c in cands:
                if c.startswith(prefer):
                    return c
            return cands[0]
        except Exception:
            return None

    def _excl_pred(self, seg: "StateInterval", op: Operation) -> bool:
        # END state never blocks
        if getattr(seg, "state", None) == "END":
            return False
        base_name = op.base.name
        alias_label = self._alias_label_for(base_name, int(op.meta.get("arity", 1)) if hasattr(op, "meta") else 1)
        for wop, states, tokens, _scope in self._excl_rules:
            if wop and str(seg.op_base).upper() != wop:
                continue
            if ("*" not in states) and (str(seg.state) not in states):
                continue
            # if any token blocks this candidate
            for tok in tokens:
                if self._token_blocks_labels(tok, base_name, alias_label):
                    return True
        return False

    def _bus_pred(self, seg: "StateInterval") -> bool:
        # END state has no bus usage
        if getattr(seg, "state", None) == "END":
            return False
        try:
            spec = self.cfg.get("op_specs", {}).get(str(seg.op_base), {})
            for st in spec.get("states", []):
                if str(st.get("name")) == str(seg.state):
                    return bool(st.get("bus", False))
        except Exception:
            return False
        return False

    # --- scope-aware overlaps helpers ---
    def _normalize_scope(self, s: Optional[str]) -> str:
        try:
            v = str(s or "PLANE").upper()
            return v if v in ("PLANE","DIE","GLOBAL") else "PLANE"
        except Exception:
            return "PLANE"

    def _overlaps_scope(self, die: int, plane_set: List[int], t0: float, t1: float, pred, scope: str) -> bool:
        if self.state_timeline is None:
            return False
        sc = self._normalize_scope(scope)
        if sc == "GLOBAL":
            return self.state_timeline.overlaps_global(t0, t1, pred=pred)
        elif sc == "DIE":
            return self.state_timeline.overlaps_die(die, t0, t1, pred=pred)
        else:  # PLANE
            for p in plane_set:
                if self.state_timeline.overlaps(die, p, t0, t1, pred=pred):
                    return True
            return False

    def _excl_blocks_candidate(self, die: int, plane_set: List[int], t0: float, t1: float, base_name: str, op_name: Optional[str]) -> bool:
        # iterate compiled rules and test overlaps in their scopes
        if not self._excl_rules:
            return False
        for wop, states, tokens, scope in self._excl_rules:
            # build seg predicate
            def _pred(seg: "StateInterval"):
                if getattr(seg, "state", None) == "END":
                    return False
                if wop and str(seg.op_base).upper() != wop:
                    return False
                if ("*" not in states) and (str(seg.state) not in states):
                    return False
                # check if rule tokens would block the candidate
                for tok in tokens:
                    if self._token_blocks_labels(tok, base_name, op_name):
                        return True
                return False
            if self._overlaps_scope(die, plane_set, t0, t1, pred=_pred, scope=scope):
                return True
        return False

    def _validate_candidate(self, now_us: float, hook: PhaseHook, stage: str, op: Operation,
                            plane_set: List[int], start_hint: float,
                            deadline: Optional[float] = None, ob: Optional["Obligation"] = None) -> bool:
        ignore = set(self.cfg.get("op_specs", {}).get(op.name, {}).get("ignore", []))
        attempted = op.base.name
        alias_used = op.name if op.name != attempted else None
        if deadline is not None and start_hint > deadline:
            self._reject(now_us, hook, stage, "deadline", attempted, alias_used,
                         len(plane_set), plane_set, start_hint, None, "deadline_miss")
            if ob:
                self.obl.requeue(ob)
            return False
        bypass = (stage == "obligation" and self.cfg.get("admission", {}).get("obligation_bypass", True))
        if "admission" not in ignore and not bypass:
            adm_delta = get_admission_delta(self.cfg, hook.label, attempted)
            if not self._admission_ok(now_us, hook.label, attempted, start_hint, deadline):
                self._reject(now_us, hook, stage, "admission", attempted, alias_used,
                             len(plane_set), plane_set, start_hint, adm_delta, "near_future_gate")
                if ob:
                    self.obl.requeue(ob)
                return False
        else:
            adm_delta = None
        if "precheck" not in ignore:
            scope = Scope[op.meta.get("scope", "PLANE_SET")]
            if not self.addr.precheck_planescope(op.base, op.targets, start_hint, scope):
                self._reject(now_us, hook, stage, "precheck", attempted, alias_used,
                             len(plane_set), plane_set, start_hint, adm_delta, "addr/precheck")
                if ob:
                    self.obl.requeue(ob)
                return False
        if "bus_precheck" not in ignore:
            if not self.addr.bus_precheck(op, start_hint, self.addr.bus_segments_for_op(op)):
                self._reject(now_us, hook, stage, "bus", attempted, alias_used,
                             len(plane_set), plane_set, start_hint, adm_delta, "bus_conflict")
                if ob:
                    self.obl.requeue(ob)
                return False
        if "latch" not in ignore:
            if not self.latch.allowed(op, start_hint):
                self._reject(now_us, hook, stage, "latch", attempted, alias_used,
                             len(plane_set), plane_set, start_hint, adm_delta, "read->dout plane latched")
                if ob:
                    self.obl.requeue(ob)
                return False
        if "excl" not in ignore:
            if not self._exclusion_ok(op, start_hint):
                self._reject(now_us, hook, stage, "excl", attempted, alias_used,
                             len(plane_set), plane_set, start_hint, adm_delta, "exclusion_window")
                if ob:
                    self.obl.requeue(ob)
                return False
        self.rejlog.log_accept(stage)
        return True

    def propose(self, now_us: float, hook: PhaseHook, g: Dict[str,str], l: Dict[str,str], earliest_start: float) -> Optional[Operation]:
        die, hook_plane = hook.die, hook.plane

        # 0) obligations first
        stage = "obligation"
        self.rejlog.log_attempt(stage)
        hookscreen_cfg = self.cfg.get("policy", {}).get("hookscreen", {})
        horizon = float(hookscreen_cfg.get("horizon_us", 10.0))
        pop_earliest = self.addr.candidate_start_for_scope(now_us, die, Scope.DIE_WIDE, list(range(self.addr.planes)))
        ob=self.obl.pop_urgent(now_us, die, hook_plane, horizon_us=horizon, earliest_start=pop_earliest)
        if ob:
            cfg_op=self.cfg["op_specs"][ob.require]
            op=build_operation(ob.require, OpKind[_op_base_from_alias(ob.require)], cfg_op, ob.targets)
            op.meta["scope"]=cfg_op["scope"]; op.meta["plane_list"]=sorted({a.plane for a in ob.targets}); op.meta["arity"]=len(op.meta["plane_list"])
            op.meta["obligation"]=ob
            if getattr(ob, "source", None):
                op.meta["source"] = ob.source
            if getattr(ob, "skip_dout_creation", False):
                op.meta["skip_dout_creation"] = True
            plane_set=op.meta["plane_list"]
            scope=Scope[op.meta["scope"]]
            affects = self.cfg.get("state_timeline", {}).get("affects", {})
            if op.base.name in affects:
                start_hint=quantize(earliest_start)
            else:
                start_hint=self.addr.candidate_start_for_scope(now_us, die, scope, plane_set)
            self.current_obligation = ob
            if self._validate_candidate(now_us, hook, stage, op, plane_set, start_hint, deadline=ob.deadline_us, ob=ob):
                op.meta["phase_key_used"]="(obligation)"
                return op, start_hint

        # 1) phase-conditional (optional; can be disabled via CFG)
        stage = "phase_conditional"
        self.rejlog.log_attempt(stage)
        # disable knob
        if not self.cfg.get("policy", {}).get("enable_phase_conditional", True):
            self._reject(now_us, hook, stage, "disabled", None, None, None, None, earliest_start, None, "disabled_by_cfg")
            return None, None
        # guard: while bootstrap obligations exist anywhere, skip policy proposals
        if self.obl.has_pending(source="bootstrap"):
            self._reject(now_us, hook, stage, "guard_bootstrap_pending", None, None, None, None, earliest_start, None, "bootstrap_pending_skip")
            return None, None

        # # Step 1: admission screen (target-level) - available_at <= now + default_delta
        # adm_delta = float(self.cfg.get("admission", {}).get("default_delta_us", 0.0))
        # if self.addr.available_at(die, hook_plane) > now_us + adm_delta:
        #     self._reject(now_us, hook, stage, "admission_target", None, None, None, None, earliest_start, adm_delta, "target_busy")
        #     return None
        allow=set(list(OP_ALIAS.keys()))
        # derive state key from state_timeline at reference time (earliest_start)
        st_key = None
        if self.state_timeline is not None:
            st_key = self.state_timeline.state_at(hook.die, hook.plane, earliest_start)  # e.g., "READ.CORE_BUSY" or "READ.END"
        used_label = st_key if st_key else hook.label
        dist, used_key = get_phase_dist(self.cfg, used_label)
        if not dist:
            self._reject(now_us, hook, stage, "none_available", None, None, None, None, earliest_start, None, "no_dist_for_hook")
        else:
            # Step 2-3: exclusion/bus screens at operation-level using state_timeline
            # Step 4: phase_conditional screen → allow = positive weight items
            cand = {op_name: w for op_name, w in dist.items() if op_name in allow and float(w) > 0.0}
            if not cand:
                self._reject(now_us, hook, stage, "none_available", None, None, None, None, earliest_start, None, "all_zero_weight")
                return None, None
            # DIE_WIDE op screen with state_timeline (operation screen)
            filtered = {}
            for op_name, w in cand.items():
                base, _cons = self._resolve_op_name(op_name)
                # exclude/bus op-level quick screen on hook plane interval
                try:
                    dur_nom = get_nominal_duration(self.cfg, op_name)
                except Exception:
                    dur_nom = 0.0
                t0 = earliest_start; t1 = quantize(earliest_start + dur_nom)
                # exclusion op-level filter (scope-aware)
                if self._excl_blocks_candidate(die, [hook_plane], t0, t1, base, op_name):
                    continue
                # bus op-level prescreen on hook plane
                if self.state_timeline is not None and dur_nom > 0.0:
                    if self.state_timeline.overlaps(die, hook_plane, t0, t1, pred=lambda seg: self._bus_pred(seg)):
                        continue
                # DIE_WIDE scope screen: die-wide busy check (coarse)
                scope_name = str(self.cfg.get("op_specs", {}).get(op_name, {}).get("scope", "PLANE_SET"))
                if scope_name == "DIE_WIDE" and self.state_timeline is not None and dur_nom > 0.0:
                    if self._overlaps_scope(die, [hook_plane], t0, t1, pred=lambda seg: seg.state != "END", scope="DIE"):
                        continue
                filtered[op_name] = w
            if not filtered:
                self._reject(now_us, hook, stage, "none_available", None, None, None, None, earliest_start, None, "allow_filtered_empty")
                return None, None
            # progressive sampling: reweight for starvation prevention
            pick = None
            if self._propose_mode in ("progressive", "hybrid"):
                self._sampler.tick(filtered)
                weighted = self._sampler.reweight(filtered)
                pick = roulette_pick(weighted, set(weighted.keys()))
            else:
                pick = roulette_pick(filtered, set(filtered.keys()))
            if not pick:
                self._reject(now_us, hook, stage, "none_available", None, None, None, None, earliest_start, None, "roulette_zero_weight")
            else:
                base, alias_const = self._resolve_op_name(pick)
                kind=OpKind[base]
                fanout, interleave=self._fanout_from_alias(base, alias_const, hook.label)
                plan=self.addr.plan_multiplane(kind, die, hook_plane, fanout, interleave)
                alias_used=pick
                if not plan and fanout>1:
                    self.stats["alias_degrade"]+=1
                    plan=self.addr.plan_multiplane(kind, die, hook_plane, 1, interleave)
                    if plan: fanout=1
                # start_plane round-robin scan when easing enabled and no plan
                if not plan:
                    scan = max(1, int(hookscreen_cfg.get("startplane_scan", 1)))
                    tried = 0
                    p = (hook_plane + 1) % self.addr.planes
                    while tried < self.addr.planes and tried < scan and not plan:
                        plan=self.addr.plan_multiplane(kind, die, p, fanout, interleave)
                        tried += 1
                        p = (p + 1) % self.addr.planes
                if not plan:
                    self._reject(now_us, hook, stage, "plan_none", base, alias_used, fanout, None, earliest_start, None, "no_targets")
                else:
                    targets, plane_set, scope=plan
                    cfg_op=self.cfg["op_specs"][pick]
                    op=build_operation(alias_used, kind, cfg_op, targets)
                    op.meta["scope"]=cfg_op["scope"]; op.meta["plane_list"]=plane_set; op.meta["arity"]=len(plane_set); op.meta["alias_used"]=alias_used
                    try:
                        op.meta["phase_key_used"] = str(used_key if used_key else used_label)
                        op.meta["state_key_at_schedule"] = str(st_key) if st_key else None
                    except Exception:
                        op.meta["phase_key_used"] = str(used_label)
                    affects = self.cfg.get("state_timeline", {}).get("affects", {})
                    if op.base.name in affects:
                        start_hint=quantize(earliest_start)
                    else:
                        start_hint=self.addr.candidate_start_for_scope(now_us, die, scope, plane_set)
                    if self._validate_candidate(now_us, hook, stage, op, plane_set, start_hint):
                        op.meta["source"]="policy.phase_conditional"
                        # mark accept for sampler (progressive mode)
                        if self._propose_mode in ("progressive", "hybrid"):
                            try:
                                self._sampler.on_accept(alias_used)
                            except Exception:
                                pass
                        return op, start_hint

        # backoff 단계 제거됨 (충돌 정합성 해소 계획에 따라 비활성화)
        return None, None

# --------------------------------------------------------------------------
# Selection overrides
def get_phase_selection_override(cfg: Dict[str,Any], hook_label: str, kind_name: str):
    op, state, _ = parse_hook_key(hook_label)
    po=cfg.get("selection",{}).get("phase_overrides",{})
    keys=[]
    # Try alias/base expanded keys for robustness
    if op and state:
        keys.append(f"{op}.{state}")
        for _ali in _op_alias_candidates(op):
            k=f"{_ali}.{state}"
            if k not in keys:
                keys.append(k)
        _base=_op_base_from_alias(op)
        if _base:
            k=f"{_base}.{state}"
            if k not in keys:
                keys.append(k)
    for k in keys:
        val=po.get(k)
        if val:
            return int(val.get("fanout",1)), bool(val.get("interleave",True))
    dflt=cfg.get("selection",{}).get("defaults",{}).get(kind_name,{"fanout":1,"interleave":True})
    return int(dflt.get("fanout",1)), bool(dflt.get("interleave",True))

# --------------------------------------------------------------------------
def _addr_str(a: Address)->str: return f"(d{a.die},p{a.plane},b{a.block},pg{a.page})"

def populate_bootstrap_obligations(cfg: Dict[str,Any], addr: AddressManager, obl: ObligationManager):
    bs = cfg.get("bootstrap", {})
    if not bs or not bs.get("enabled", False):
        return
    ratio = float(bs.get("pgm_ratio", 0.0))
    ratio = max(0.0, min(ratio, float(bs.get("max_ratio", 0.5))))
    dies = addr.dies
    pages_per_block = addr.pages_per_block
    k = int(min(max(int(pages_per_block * ratio), 0), max(pages_per_block - 1, 0)))
    if k <= 0:
        return
    die = 0
    planes = addr.planes
    gap = float(bs.get("stage_gap_us", 0.2))
    # base stagger; can be overridden by DOUT N-multiple rule below when generating DOUT obligations
    stagger = float(bs.get("stagger_us", 0.5))
    hard_slot = bool(bs.get("hard_slot", True))
    # nominal durations for dependency-aware deadlines
    erase_nom = get_nominal_duration(cfg, "MUL_ERASE")
    prog_nom  = get_nominal_duration(cfg, "MUL_PROGRAM")
    read_nom  = get_nominal_duration(cfg, "MUL_READ")
    dout_nom  = get_nominal_duration(cfg, "DOUT")
    dout_mult = float(bs.get("dout_stagger_n", 0.0))
    stagger_dout = max(stagger, dout_nom * dout_mult) if dout_mult > 0.0 else stagger

    # gap_global = float(bs.get("dout_global_gap_us", 50.0))

    # eps 상수화(슬림 스키마): 필요 시 코드 변경으로 조정
    # per-page step so that p increases in order and READ(p) < PROGRAM(p+1)
    seq_id = obl._seq
    # iterate all block stripes across planes so that every block is covered
    stripes = (addr.blocks + planes - 1) // planes
    # next stripe starts after the last deadline of previous stripe (strictly increasing)
    stripe_base = quantize(addr.available_at(die, 0) + gap)
    stripe_last_deadline = stripe_base
    # init creation logger
    if not hasattr(obl, "creation_logger"):
        obl.creation_logger = CreationLogger()
    for die in range(dies):
        # ERASE
        for s in range(stripes):
            for p in range(k):
                plane_targets = []
                for pl in range(planes):
                    b_idx = pl + s * planes
                    if b_idx >= addr.blocks:
                        continue
                    plane_targets.append(Address(die, pl, b_idx, p))
                if not plane_targets:
                    continue
                erase_base = stripe_last_deadline + gap
                erase_deadline = quantize(erase_base + erase_nom)
                if p == 0:
                    # ERASE: multi-plane (all planes in this stripe)
                    seq_id += 1
                    # ob_erase = Obligation(id=seq_id, require=OpKind.ERASE, targets=list(plane_targets),
                    ob_erase = Obligation(id=seq_id, require="MUL_ERASE", targets=list(plane_targets),
                                        deadline_us=quantize(erase_deadline), hard_slot=hard_slot, source="bootstrap", skip_dout_creation=False)
                    heapq.heappush(obl.heap, _ObHeapItem(deadline_us=ob_erase.deadline_us, seq=ob_erase.id, ob=ob_erase))
                    obl.stats["created"] += 1
                    obl.creation_logger.log(ob_erase, context="bootstrap", stripe=s, page_index=p)
                    # debug logging removed
                    stripe_last_deadline = ob_erase.deadline_us

        # PROGRAM
        for s in range(stripes):
            for p in range(k):
                plane_targets = []
                for pl in range(planes):
                    b_idx = pl + s * planes
                    if b_idx >= addr.blocks:
                        continue
                    plane_targets.append(Address(die, pl, b_idx, p))
                if not plane_targets:
                    continue
                # PROGRAM deadline: ensure page order
                pgm_base = stripe_last_deadline + gap
                prog_deadline = quantize(pgm_base + prog_nom)
                seq_id += 1
                # ob_pgm = Obligation(id=seq_id, require=OpKind.PROGRAM, targets=plane_targets,
                ob_pgm = Obligation(id=seq_id, require="MUL_PROGRAM", targets=plane_targets,
                                    deadline_us=prog_deadline, hard_slot=hard_slot, source="bootstrap", skip_dout_creation=False)
                heapq.heappush(obl.heap, _ObHeapItem(deadline_us=ob_pgm.deadline_us, seq=ob_pgm.id, ob=ob_pgm))
                obl.stats["created"] += 1
                obl.creation_logger.log(ob_pgm, context="bootstrap", stripe=s, page_index=p)
                # debug logging removed
                stripe_last_deadline = ob_pgm.deadline_us

        # READ
        for s in range(stripes):
            for p in range(k):
                plane_targets = []
                for pl in range(planes):
                    b_idx = pl + s * planes
                    if b_idx >= addr.blocks:
                        continue
                    plane_targets.append(Address(die, pl, b_idx, p))
                if not plane_targets:
                    continue
                # READ deadline: after PROGRAM(p) completes nominally
                read_base = stripe_last_deadline + gap
                read_deadline = quantize(read_base + read_nom)
                seq_id += 1
                ob_read = Obligation(id=seq_id, require="MUL_READ", targets=plane_targets,
                                    deadline_us=read_deadline, hard_slot=hard_slot, source="bootstrap", skip_dout_creation=True)
                heapq.heappush(obl.heap, _ObHeapItem(deadline_us=ob_read.deadline_us, seq=ob_read.id, ob=ob_read))
                obl.stats["created"] += 1
                obl.creation_logger.log(ob_read, context="bootstrap", stripe=s, page_index=p)
                # debug logging removed
                stripe_last_deadline = ob_read.deadline_us + gap
                # Pair (READ->DOUT) per page: DOUT after READ(p)
                for idx, t in enumerate(sorted(plane_targets, key=lambda a: a.plane)):
                    dout_base = stripe_last_deadline + gap
                    dout_deadline = quantize(dout_base + stagger_dout)
                    seq_id += 1
                    ob_dout = Obligation(id=seq_id, require="DOUT", targets=[Address(t.die, t.plane, t.block, p)],
                                        deadline_us=quantize(dout_deadline), hard_slot=hard_slot, source="bootstrap", skip_dout_creation=False)
                    heapq.heappush(obl.heap, _ObHeapItem(deadline_us=ob_dout.deadline_us, seq=ob_dout.id, ob=ob_dout))
                    obl.stats["created"] += 1
                    obl.creation_logger.log(ob_dout, context="bootstrap", stripe=s, page_index=p)
                    stripe_last_deadline = ob_dout.deadline_us
    obl._seq = seq_id

class Scheduler:
    def __init__(self, cfg, addr:AddressManager, spe:PolicyEngine, obl:ObligationManager,
                 excl:ExclusionManager, logger: Optional[TimelineLogger]=None,
                 latch: Optional[LatchManager]=None, state_timeline: Optional[StateTimeline]=None):
        self.cfg=cfg; self.addr=addr; self.SPE=spe; self.obl=obl; self.excl=excl
        self.now=0.0; self.ev=[]; self._seq=0
        self.stat_propose_calls=0; self.stat_scheduled=0
        self._propose_time_total=0.0
        self.logger = logger or TimelineLogger()
        self.latch = latch or LatchManager()
        # state timeline
        self.state_timeline = state_timeline
        # bootstrap markers
        self._bootstrap_started=False
        self._bootstrap_start_time=None
        self._bootstrap_end_time=None
        self._last_now=0.0
        self._push(1, "QUEUE_REFILL", None)
        for plane in range(self.addr.planes):
            self._push(2, "PHASE_HOOK", PhaseHook(2, "BOOT.START", 0, plane))
        # ---- observability (preflight/ckpt/global overlay) ----
        self._obs = {
            'pf_local_calls': 0,
            'pf_global_calls': 0,
            'pf_local_len_hist': defaultdict(int),
            'pf_global_len_hist': defaultdict(int),
            'pf_local_stops': defaultdict(int),
            'pf_global_stops': defaultdict(int),
            'ckpt_success_batches': 0,
            'ckpt_rollback_batches': 0,
            'ckpt_ops_committed': 0,
            'overlay_fail_global': defaultdict(lambda: defaultdict(int)),  # (die,plane)->reason->count
        }

    # ---------------- Progressive helpers (preflight + commit) ----------------
    @dataclass
    class _ChunkOverlay:
        plane_resv: Dict[Tuple[int,int], List[Tuple[float,float]]] = field(default_factory=lambda: defaultdict(list))
        bus_resv: List[Tuple[float,float]] = field(default_factory=list)
        excl_global: List[ExclWindow] = field(default_factory=list)
        excl_die: Dict[int, List[ExclWindow]] = field(default_factory=lambda: defaultdict(list))
        latch_locks: Dict[Tuple[int,int], float] = field(default_factory=dict)  # (die,plane) -> start_us

    @dataclass
    class _TxnSnapshot:
        # AddressManager
        addr_available: Dict[Tuple[int,int], float]
        addr_resv: Dict[Tuple[int,int], List[Tuple[float,float,int]]]
        addr_bus_resv: List[Tuple[float,float]]
        addr_future_erase: Dict[Tuple[int,int], List[Tuple[float,float]]]
        addr_future_program: Dict[Tuple[int,int,int], List[Tuple[float,float]]]
        addr_state_future: Dict[Tuple[int,int], int]
        addr_programmed_future: Dict[Tuple[int], Set[Tuple[int,int]]]
        addr_write_head: Dict[Tuple[int,int], int]
        # ExclusionManager
        excl_global: List[ExclWindow]
        excl_die: Dict[int, List[ExclWindow]]
        # LatchManager
        latch_locks: Dict[Tuple[int,int], _Latch]
        # StateTimeline (struct fields)
        st_by_plane: Dict[Tuple[int,int], List['StateInterval']]
        st_last_end_idx: Dict[Tuple[int,int], int]
        st_starts_by_plane: Dict[Tuple[int,int], List[float]]
        st_die_index: Dict[int, List['StateInterval']]
        st_die_starts: Dict[int, List[float]]
        st_global_index: List['StateInterval']
        st_global_starts: List[float]
        # Logger sizes
        log_len: Optional[int] = None
        boot_rows_len: Optional[int] = None
        pol_rows_len: Optional[int] = None
        stat_scheduled: int = 0

    def _begin_txn(self) -> _TxnSnapshot:
        import copy as _copy
        # AddressManager
        addr = self.addr
        snap = Scheduler._TxnSnapshot(
            addr_available=dict(addr.available),
            addr_resv={k: list(v) for k, v in addr.resv.items()},
            addr_bus_resv=list(addr.bus_resv),
            addr_future_erase={k: list(v) for k, v in addr.future_erase_by_block.items()},
            addr_future_program={k: list(v) for k, v in addr.future_program_by_page.items()},
            addr_state_future=dict(addr.addr_state_future),
            addr_programmed_future={k: set(v) for k, v in addr.programmed_future.items()},
            addr_write_head=dict(addr.write_head),
            excl_global=list(self.excl.global_windows),
            excl_die={k: list(v) for k, v in self.excl.die_windows.items()},
            latch_locks=dict(self.latch._locks),
            st_by_plane=_copy.deepcopy(self.state_timeline.by_plane) if self.state_timeline is not None else {},
            st_last_end_idx=dict(self.state_timeline.last_end_idx) if self.state_timeline is not None else {},
            st_starts_by_plane=_copy.deepcopy(self.state_timeline._starts_by_plane) if self.state_timeline is not None else {},
            st_die_index=_copy.deepcopy(self.state_timeline._die_index) if self.state_timeline is not None else {},
            st_die_starts=_copy.deepcopy(self.state_timeline._die_starts) if self.state_timeline is not None else {},
            st_global_index=_copy.deepcopy(self.state_timeline._global_index) if self.state_timeline is not None else [],
            st_global_starts=list(self.state_timeline._global_starts) if self.state_timeline is not None else [],
            stat_scheduled=int(self.stat_scheduled),
        )
        # logger rows length (best-effort)
        try:
            if hasattr(self.logger, 'rows') and isinstance(self.logger.rows, list):
                snap.log_len = len(self.logger.rows)
        except Exception:
            pass
        # split buffers
        try:
            snap.boot_rows_len = len(getattr(self, '_timeline_rows_bootstrap', []))
            snap.pol_rows_len = len(getattr(self, '_timeline_rows_policy', []))
        except Exception:
            pass
        return snap

    def _rollback_txn(self, snap: _TxnSnapshot):
        # AddressManager
        self.addr.available = dict(snap.addr_available)
        self.addr.resv = {k: list(v) for k, v in snap.addr_resv.items()}
        self.addr.bus_resv = list(snap.addr_bus_resv)
        self.addr.future_erase_by_block = {k: list(v) for k, v in snap.addr_future_erase.items()}
        self.addr.future_program_by_page = {k: list(v) for k, v in snap.addr_future_program.items()}
        self.addr.addr_state_future = dict(snap.addr_state_future)
        self.addr.programmed_future = {k: set(v) for k, v in snap.addr_programmed_future.items()}
        self.addr.write_head = dict(snap.addr_write_head)
        # ExclusionManager
        self.excl.global_windows = list(snap.excl_global)
        self.excl.die_windows = {k: list(v) for k, v in snap.excl_die.items()}
        # LatchManager
        self.latch._locks = dict(snap.latch_locks)
        # StateTimeline (restore contents in-place)
        if self.state_timeline is not None:
            self.state_timeline.by_plane = snap.st_by_plane
            self.state_timeline.last_end_idx = snap.st_last_end_idx
            self.state_timeline._starts_by_plane = snap.st_starts_by_plane
            self.state_timeline._die_index = snap.st_die_index
            self.state_timeline._die_starts = snap.st_die_starts
            self.state_timeline._global_index = snap.st_global_index
            self.state_timeline._global_starts = snap.st_global_starts
        # Logger
        try:
            if snap.log_len is not None and hasattr(self.logger, 'rows') and isinstance(self.logger.rows, list):
                self.logger.rows[:] = self.logger.rows[:snap.log_len]
        except Exception:
            pass
        try:
            if snap.boot_rows_len is not None and hasattr(self, '_timeline_rows_bootstrap'):
                self._timeline_rows_bootstrap = self._timeline_rows_bootstrap[:snap.boot_rows_len]
            if snap.pol_rows_len is not None and hasattr(self, '_timeline_rows_policy'):
                self._timeline_rows_policy = self._timeline_rows_policy[:snap.pol_rows_len]
        except Exception:
            pass
        # Stats
        self.stat_scheduled = snap.stat_scheduled

    def _overlay_planes_for(self, op: Operation) -> List[Tuple[int,int]]:
        die = op.targets[0].die
        scope = str(op.meta.get("scope", "PLANE_SET"))
        if scope == "DIE_WIDE":
            return [(die, p) for p in range(self.addr.planes)]
        elif scope == "PLANE_SET":
            return [(t.die, t.plane) for t in op.targets]
        else:
            t = op.targets[0]
            return [(t.die, t.plane)]

    def _overlay_planescope_ok(self, ov: _ChunkOverlay, op: Operation, start: float, end: float) -> bool:
        # check against overlay plane reservations
        for (d,p) in self._overlay_planes_for(op):
            for (s,e) in ov.plane_resv[(d,p)]:
                if not (end <= s or e <= start):
                    return False
        return True

    def _overlay_bus_ok(self, ov: _ChunkOverlay, op: Operation, start: float) -> bool:
        segs = self.addr.bus_segments_for_op(op)
        for (off0,off1) in segs:
            a0, a1 = quantize(start+off0), quantize(start+off1)
            for (s,e) in ov.bus_resv:
                if not (a1 <= s or e <= a0):
                    return False
        return True

    def _overlay_excl_ok(self, ov: _ChunkOverlay, op: Operation, start: float, end: float) -> bool:
        # replicate ExclusionManager.allowed for overlay windows
        def _tok_blocks(tok: str) -> bool:
            if tok == "ANY":
                return True
            if tok.startswith("BASE:"):
                return op.base.name == tok.split(":",1)[1]
            if tok.startswith("ALIAS:"):
                alias = tok.split(":",1)[1]
                if alias == "MUL_READ": return (op.base == OpKind.READ and op.meta.get("arity",1) > 1)
                if alias == "SIN_READ": return (op.base == OpKind.READ and op.meta.get("arity",1) == 1)
            return False
        # global
        for w in ov.excl_global:
            if not (end <= w.start or w.end <= start):
                if any(_tok_blocks(tok) for tok in w.tokens):
                    return False
        d = op.targets[0].die
        for w in ov.excl_die.get(d, []):
            if not (end <= w.start or w.end <= start):
                if any(_tok_blocks(tok) for tok in w.tokens):
                    return False
        return True

    def _overlay_latch_ok(self, ov: _ChunkOverlay, op: Operation, start: float) -> bool:
        # DOUT/SR allowed
        if op.base in (OpKind.DOUT, OpKind.SR):
            return True
        # check DIE_WIDE or PLANE_SET locks
        die = op.targets[0].die
        scope = str(op.meta.get("scope", "PLANE_SET"))
        if scope == "DIE_WIDE":
            for (d,p), st in ov.latch_locks.items():
                if d == die and start >= st:
                    return False
            return True
        else:
            for t in op.targets:
                st = ov.latch_locks.get((t.die, t.plane))
                if st is not None and start >= st:
                    return False
            return True

    def _overlay_register(self, ov: _ChunkOverlay, op: Operation, start: float, end: float):
        # planescope
        for key in self._overlay_planes_for(op):
            ov.plane_resv[key].append((start, end))
        # bus
        for (off0,off1) in self.addr.bus_segments_for_op(op):
            ov.bus_resv.append((quantize(start+off0), quantize(start+off1)))
        # exclusion windows from rules
        rules = self.excl.cfg.get("constraints",{}).get("exclusions",[])
        die = op.targets[0].die
        for r in rules:
            when=r.get("when", {})
            if_op=when.get("op")
            if if_op != op.base.name and not (op.base==OpKind.READ and if_op=="READ"):
                continue
            alias_need = when.get("alias")
            if alias_need=="MUL" and not (op.meta.get("arity",1)>1): continue
            if alias_need=="SIN" and not (op.meta.get("arity",1)==1): continue
            states = when.get("states", ["*"])
            wins = self.excl._state_windows(op, start, states)
            scope=r.get("scope","GLOBAL"); tokens=set(r.get("blocks",[]))
            for (s0,s1) in wins:
                w=ExclWindow(start=quantize(s0), end=quantize(s1), scope=scope,
                             die=(die if scope=="DIE" else None), tokens=tokens)
                if scope=="GLOBAL":
                    ov.excl_global.append(w)
                else:
                    ov.excl_die.setdefault(die, []).append(w)
        # latch plan after READ
        if op.base == OpKind.READ:
            for t in op.targets:
                ov.latch_locks[(t.die, t.plane)] = end

    def _preflight_chunk(self, hook: PhaseHook, g: Dict[str,str], l: Dict[str,str], earliest_start: float,
                          max_ops: int, allow_partial: bool) -> List[Tuple[Operation,float]]:
        self._obs['pf_local_calls'] += 1
        planned: List[Tuple[Operation,float]] = []
        overlay = Scheduler._ChunkOverlay()
        for _ in range(max_ops):
            self.stat_propose_calls += 1
            _ts = time.perf_counter()
            op, start_hint = self.SPE.propose(self.now, hook, g, l, earliest_start)
            self._propose_time_total += time.perf_counter() - _ts
            if not op:
                self._obs['pf_local_stops']['no_candidate'] += 1
                break
            start = quantize(start_hint); end = quantize(start + get_op_duration(op))
            # existing system prechecks
            segs = self.addr.bus_segments_for_op(op)
            if not self.addr.bus_precheck(op, start, segs):
                self._obs['pf_local_stops']['bus_precheck'] += 1
                if allow_partial:
                    break
                else:
                    return []
            if not self.latch.allowed(op, start):
                self._obs['pf_local_stops']['latch_precheck'] += 1
                if allow_partial:
                    break
                else:
                    return []
            if not self.excl.allowed(op, start, end):
                self._obs['pf_local_stops']['excl_precheck'] += 1
                if allow_partial:
                    break
                else:
                    return []
            scope = Scope[op.meta.get("scope", "PLANE_SET")]
            if not self.addr.precheck_planescope(op.base, op.targets, start, scope):
                self._obs['pf_local_stops']['planescope_precheck'] += 1
                if allow_partial:
                    break
                else:
                    return []
            # overlay checks (intra-chunk conflicts)
            if not self._overlay_planescope_ok(overlay, op, start, end):
                self._obs['pf_local_stops']['overlay_planescope'] += 1
                if allow_partial:
                    break
                else:
                    return []
            if not self._overlay_bus_ok(overlay, op, start):
                self._obs['pf_local_stops']['overlay_bus'] += 1
                if allow_partial:
                    break
                else:
                    return []
            if not self._overlay_excl_ok(overlay, op, start, end):
                self._obs['pf_local_stops']['overlay_excl'] += 1
                if allow_partial:
                    break
                else:
                    return []
            if not self._overlay_latch_ok(overlay, op, start):
                self._obs['pf_local_stops']['overlay_latch'] += 1
                if allow_partial:
                    break
                else:
                    return []
            # accept into plan and update overlays
            planned.append((op, start))
            self._overlay_register(overlay, op, start, end)
        self._obs['pf_local_len_hist'][len(planned)] += 1
        return planned

    def _preflight_global_batch(self, max_ops: int, allow_partial: bool) -> List[Tuple[Operation,float]]:
        """Collect up to max_ops across all dies/planes using overlay checks.
        Does not mutate system state; uses same checks as _preflight_chunk.
        """
        self._obs['pf_global_calls'] += 1
        planned: List[Tuple[Operation,float]] = []
        overlay = Scheduler._ChunkOverlay()
        # sweep until no progress or filled
        while len(planned) < max_ops:
            added = 0
            for die in range(self.addr.dies):
                for plane in range(self.addr.planes):
                    if len(planned) >= max_ops:
                        break
                    hook = PhaseHook(self.now, "GLOBAL.REFILL", die, plane)
                    earliest_start = self.now
                    g, l = self.addr.observe_states(die, plane, self.now)
                    self.stat_propose_calls += 1
                    _ts = time.perf_counter()
                    op, start_hint = self.SPE.propose(self.now, hook, g, l, earliest_start)
                    self._propose_time_total += time.perf_counter() - _ts
                    if not op:
                        continue
                    start = quantize(start_hint); end = quantize(start + get_op_duration(op))
                    # existing guards
                    segs = self.addr.bus_segments_for_op(op)
                    if not self.addr.bus_precheck(op, start, segs):
                        self._obs['pf_global_stops']['bus_precheck'] += 1
                        if not allow_partial:
                            return []
                        continue
                    if not self.latch.allowed(op, start):
                        self._obs['pf_global_stops']['latch_precheck'] += 1
                        if not allow_partial:
                            return []
                        continue
                    if not self.excl.allowed(op, start, end):
                        self._obs['pf_global_stops']['excl_precheck'] += 1
                        if not allow_partial:
                            return []
                        continue
                    scope = Scope[op.meta.get("scope", "PLANE_SET")]
                    if not self.addr.precheck_planescope(op.base, op.targets, start, scope):
                        self._obs['pf_global_stops']['planescope_precheck'] += 1
                        if not allow_partial:
                            return []
                        continue
                    # overlay (intra-batch) checks
                    if not self._overlay_planescope_ok(overlay, op, start, end):
                        self._obs['pf_global_stops']['overlay_planescope'] += 1
                        self._obs['overlay_fail_global'][(die,plane)]['planescope'] += 1
                        if not allow_partial:
                            return []
                        continue
                    if not self._overlay_bus_ok(overlay, op, start):
                        self._obs['pf_global_stops']['overlay_bus'] += 1
                        self._obs['overlay_fail_global'][(die,plane)]['bus'] += 1
                        if not allow_partial:
                            return []
                        continue
                    if not self._overlay_excl_ok(overlay, op, start, end):
                        self._obs['pf_global_stops']['overlay_excl'] += 1
                        self._obs['overlay_fail_global'][(die,plane)]['excl'] += 1
                        if not allow_partial:
                            return []
                        continue
                    if not self._overlay_latch_ok(overlay, op, start):
                        self._obs['pf_global_stops']['overlay_latch'] += 1
                        self._obs['overlay_fail_global'][(die,plane)]['latch'] += 1
                        if not allow_partial:
                            return []
                        continue
                    planned.append((op, start))
                    self._overlay_register(overlay, op, start, end)
                    added += 1
            if added == 0:
                break
        self._obs['pf_global_len_hist'][len(planned)] += 1
        return planned

    def _push(self, t: float, typ: str, payload: Any):
        heapq.heappush(self.ev, (quantize(t), self._seq, typ, payload)); self._seq+=1

    def _start_time_for_op(self, op: Operation) -> float:
        die=op.targets[0].die
        scope=Scope[op.meta.get("scope","PLANE_SET")]
        plane_set=[a.plane for a in op.targets]
        t_planes=self.addr.earliest_start_for_scope(die, scope, plane_set)
        return quantize(max(self.now, t_planes))

    def _label_for_read(self, op: Operation)->str:
        arity = op.meta.get("arity", 1)
        if op.base == OpKind.READ:
            return "MUL_READ" if arity>1 else "SIN_READ"
        if op.base == OpKind.PROGRAM:
            return "MUL_PROGRAM" if arity>1 else "SIN_PROGRAM"
        if op.base == OpKind.ERASE:
            return "MUL_ERASE" if arity>1 else "SIN_ERASE"
        return op.base.name

    def _schedule_operation(self, op: Operation, start_hint: float) -> bool:
        # start=self._start_time_for_op(op); dur=get_op_duration(op); end=quantize(start+dur)
        start=quantize(start_hint); dur=get_op_duration(op); end=quantize(start+dur)

        # optional failure injection to exercise checkpoint behavior
        try:
            sched_cfg = self.cfg.get("propose", {}).get("scheduling", {})
            rate = float(sched_cfg.get("inject_fail_rate", 0.0))
            mode = str(self.cfg.get("propose", {}).get("mode", "legacy")).lower()
            if rate > 0.0 and mode in ("progressive", "hybrid") and ("obligation" not in op.meta):
                if random.random() < rate:
                    print(f"[INJECT] schedule-time failure: {op.base.name} start={start:.2f} end={end:.2f}")
                    return False
        except Exception:
            pass

        # ---- fail-safe: schedule 직전 마지막 충돌 점검 ----
        segs = self.addr.bus_segments_for_op(op)
        if not self.addr.bus_precheck(op, start, segs):
            print(f"[WARN] BUS conflict at schedule-time: {op.base.name} start={start:.2f} end={end:.2f}")
            return False
        if not self.latch.allowed(op, start):
            print(f"[WARN] LATCH conflict at schedule-time: {op.base.name} start={start:.2f} end={end:.2f}")
            return False
        if not self.excl.allowed(op, start, end):
            print(f"[WARN] EXCLUSION conflict at schedule-time: {op.base.name} start={start:.2f} end={end:.2f}")
            return False
        # -----------------------------------------------

        # reserve plane scope + bus + register exclusions + future update
        ignore = set(self.cfg.get("op_specs", {}).get(op.name, {}).get("ignore", []))
        if "reserve_planscope" not in ignore:
            self.addr.reserve_planescope(op, start, end)
        if "bus_reserve" not in ignore:
            self.addr.bus_reserve(start, self.addr.bus_segments_for_op(op))
        if "register_exclusion" not in ignore:
            self.excl.register(op, start)
        if "register_future" not in ignore:
            self.addr.register_future(op, start, end)
        # state timeline register per target
        affects = self.cfg.get("state_timeline", {}).get("affects", {})
        affect_op = bool(affects.get(op.base.name, True)) and ("state_timeline" not in ignore)
        if affect_op:
            st_list = [(s.name, float(s.dur_us)) for s in op.states]
            for t in op.targets:
                self.state_timeline.reserve_op(t.die, t.plane, op.name, op.base.name, st_list, start, True)
        # latch: if READ, plan lock from READ.end_us until DOUT ends
        if op.base == OpKind.READ and "latch_plan_lock" not in ignore:
            self.latch.plan_lock_after_read(op.targets, end)

        # assign deterministic op uid (per scheduled op)
        if "uid" not in op.meta:
            op.meta["uid"] = self.stat_scheduled + 1
        if self.logger is not None:
            bs_cfg = self.cfg.get("bootstrap", {})
            disable_bootlog = bool(bs_cfg.get("disable_timeline_logging", False))
            split_logging = bool(bs_cfg.get("split_timeline_logging", False))
            in_bootstrap = (self._bootstrap_started and (self._bootstrap_end_time is None))
            is_bootstrap_op = (op.meta.get("source") == "bootstrap")

            if split_logging:
                # 분리 로깅: 부트스트랩/정책 각각 별도 버퍼에 기록
                if not hasattr(self, "_timeline_rows_bootstrap"):
                    self._timeline_rows_bootstrap = []
                if not hasattr(self, "_timeline_rows_policy"):
                    self._timeline_rows_policy = []
                # 실제 한 번만 구성하여 viz_tools 포맷과 동일 dict로 저장
                def _row_dict():
                    label = self._label_for_read(op)
                    rows = []
                    for t in op.targets:
                        page = t.page if (t.page is not None) else 0
                        rows.append({
                            "start_us": float(start),
                            "end_us":   float(end),
                            "die":      int(t.die),
                            "plane":    int(t.plane),
                            "block":    int(t.block),
                            "page":     int(page),
                            "op_name":     label,
                            "op_base": op.base.name,
                            "source":   op.meta.get("source"),
                            "op_uid":   int(op.meta.get("uid", -1)),
                            "arity":    int(op.meta.get("arity", 1)),
                        })
                    return rows
                if in_bootstrap or is_bootstrap_op:
                    self._timeline_rows_bootstrap.extend(_row_dict())
                else:
                    self._timeline_rows_policy.extend(_row_dict())
            else:
                # 단일 로깅: 부트스트랩 구간 로깅 비활성화 옵션 적용
                if disable_bootlog and (in_bootstrap or is_bootstrap_op):
                    pass
                else:
                    self.logger.log_op(op, start, end, label_for_read=self._label_for_read(op))

        # obligation assignment stats
        if "obligation" in op.meta:
            self.obl.mark_assigned(op.meta["obligation"])

        # push events
        self._push(start, "OP_START", op); self._push(end, "OP_END", op)

        # hooks: bootstrap에서는 훅을 1회로 축소(END만). 비부트스트랩은 기존 2~3회 유지
        bs_cfg = self.cfg.get("bootstrap", {})
        pol_cfg = self.cfg.get("policy", {})
        # bootstrap 전용: 오퍼레이션이 bootstrap 소스일 때만 훅 축소 적용
        is_bootstrap_op = (op.meta.get("source") == "bootstrap")
        reduce_hooks = bool(bs_cfg.get("reduce_phase_hooks", False)) and is_bootstrap_op
        hook_margin = float(bs_cfg.get("hook_margin_us", 0.1))
        # kind별 훅 차단
        disabled_kinds = set(str(k).upper() for k in pol_cfg.get("phase_hook_disabled_kinds", []))
        hooks_blocked = (op.base.name in disabled_kinds)
        if not hooks_blocked:
            for t in op.targets:
                if reduce_hooks:
                    # op 종료 시각 + margin 기준 한 번만 훅 발생
                    self._push(end + hook_margin, "PHASE_HOOK", PhaseHook(end + hook_margin, f"{op.name}.END", t.die, t.plane))
                else:
                    cur=start
                    for s in op.states:
                        if s.name !="ISSUE":
                            eps_s = random.random()*s.dur_us*0.2
                            eps_e = random.random()*s.dur_us*0.2
                            self._push(cur + s.dur_us - eps_s,    "PHASE_HOOK", PhaseHook(cur + s.dur_us,    f"{op.name}.{s.name}.MID",   t.die, t.plane))
                            self._push(cur + s.dur_us + eps_e,    "PHASE_HOOK", PhaseHook(cur + s.dur_us,    f"{op.name}.{s.name}.END",   t.die, t.plane))
                        cur += s.dur_us

        first=op.targets[0]
        print(f"[{self.now:7.2f} us] SCHED  {op.base.name:7s} arity={op.meta.get('arity')} scope={op.meta.get('scope')} start={start:7.2f} end={end:7.2f} 1st={_addr_str(first)} src={op.meta.get('source')} alias={op.meta.get('alias_used')}")

        self.stat_scheduled+=1
        return True

    def run_until(self, t_end: float):
        t_end=quantize(t_end)
        while self.ev and self.ev[0][0] <= t_end:
            self.now, _, typ, payload = heapq.heappop(self.ev)

            # expire obligations due
            self.obl.expire_due(self.now)

            if typ=="QUEUE_REFILL":
                # 일원화된 훅 트리거: 글로벌/로컬 모두 여기에서 처리
                mode = str(self.cfg.get("propose", {}).get("mode", "legacy")).lower()
                if mode == "global":
                    # global-batch: preflight+checkpointed commit across dies/planes
                    chunk_cfg = self.cfg.get("propose", {}).get("chunking", {})
                    max_ops = int(chunk_cfg.get("max_ops_per_chunk", 5))
                    allow_partial = bool(chunk_cfg.get("allow_partial_success", True))
                    planned = self._preflight_global_batch(max_ops, allow_partial)
                    if planned:
                        ckpt = int(self.cfg.get("propose", {}).get("chunking", {}).get("checkpoint_interval", max_ops))
                        ckpt = max(1, ckpt)
                        i = 0
                        while i < len(planned):
                            batch = planned[i:i+ckpt]
                            snap = self._begin_txn()
                            ok = True
                            for (op, start) in batch:
                                if not self._schedule_operation(op, start):
                                    ok = False
                                    break
                            if not ok:
                                self._rollback_txn(snap)
                                self._obs['ckpt_rollback_batches'] += 1
                                if not allow_partial:
                                    break
                                break
                            else:
                                self._obs['ckpt_success_batches'] += 1
                                self._obs['ckpt_ops_committed'] += len(batch)
                                i += ckpt
                else:
                    hookscreen_cfg = self.cfg.get("policy", {}).get("hookscreen", {})
                    iters = max(1, int(hookscreen_cfg.get("global_obl_iters", 1)))
                    for _ in range(iters):
                        for die in range(self.addr.dies):
                            for plane in range(self.addr.planes):
                                self._push(self.now, "PHASE_HOOK", PhaseHook(self.now, "REFILL.NUDGE", die, plane))
                self._push(self.now + self.cfg["policy"]["queue_refill_period_us"], "QUEUE_REFILL", None)

            elif typ=="PHASE_HOOK":
                hook: PhaseHook = payload
                earliest_start = self.now
                g,l=self.addr.observe_states(hook.die, hook.plane, self.now)
                # bootstrap watchdog: first time pending detected / drain completion
                if self.obl.has_pending("bootstrap"):
                    if not self._bootstrap_started:
                        self._bootstrap_started=True
                        self._bootstrap_start_time=self.now
                else:
                    if self._bootstrap_started and self._bootstrap_end_time is None:
                        self._bootstrap_end_time=self.now

                # progressive chunked propose scheduling
                mode = str(self.cfg.get("propose", {}).get("mode", "legacy")).lower()
                if mode in ("progressive", "hybrid"):
                    chunk_cfg = self.cfg.get("propose", {}).get("chunking", {})
                    max_ops = int(chunk_cfg.get("max_ops_per_chunk", 5))
                    allow_partial = bool(chunk_cfg.get("allow_partial_success", True))
                    # Preflight: simulate conflicts within chunk first
                    planned = self._preflight_chunk(hook, g, l, earliest_start, max_ops, allow_partial)
                    # Commit with checkpoints + rollback scaffolding
                    if planned:
                        ckpt = int(self.cfg.get("propose", {}).get("chunking", {}).get("checkpoint_interval", max_ops))
                        ckpt = max(1, ckpt)
                        i = 0
                        committed_batches = 0
                        while i < len(planned):
                            batch = planned[i:i+ckpt]
                            snap = self._begin_txn()
                            ok = True
                            for (op, start) in batch:
                                if not self._schedule_operation(op, start):
                                    ok = False
                                    break
                            if not ok:
                                # rollback this batch
                                self._rollback_txn(snap)
                                self._obs['ckpt_rollback_batches'] += 1
                                # if partial not allowed, stop processing remaining batches
                                if not allow_partial:
                                    break
                                # else stop after previous committed batches
                                break
                            else:
                                committed_batches += 1
                                self._obs['ckpt_success_batches'] += 1
                                self._obs['ckpt_ops_committed'] += len(batch)
                                i += ckpt
                else:
                    # legacy: single propose per hook
                    self.stat_propose_calls+=1
                    _ts = time.perf_counter()
                    op, start_hint=self.SPE.propose(self.now, hook, g, l, earliest_start)
                    self._propose_time_total += time.perf_counter() - _ts
                    if op:
                        self._schedule_operation(op, start_hint)

            elif typ=="OP_START":
                op: Operation=payload; first=op.targets[0]
                print(f"[{self.now:7.2f} us] START  {op.base.name:7s} arity={op.meta.get('arity')} target={_addr_str(first)}")

            elif typ=="OP_END":
                op: Operation=payload; first=op.targets[0]
                print(f"[{self.now:7.2f} us] END    {op.base.name:7s} arity={op.meta.get('arity')} target={_addr_str(first)}")
                self.addr.commit(op)
                # DOUT 종료 시 래치 해제
                if op.base == OpKind.DOUT:
                    self.latch.release_on_dout_end(op.targets, self.now)
                # obligation fulfillment stats
                if "obligation" in op.meta:
                    self.obl.mark_fulfilled(op.meta["obligation"], self.now)
                self.obl.on_commit(op, self.now)

        # final stats
        print(f"\n=== Stats ===")
        print(f"run_until     : {t_end:.2f}")
        print(f"propose calls : {self.stat_propose_calls}")
        print(f"scheduled ops : {self.stat_scheduled}")
        if self.stat_propose_calls:
            print(f"accept ratio  : {100.0*self.stat_scheduled/self.stat_propose_calls:.1f}%")
            print(f"reject ratio  : {100.0*(self.stat_propose_calls - self.stat_scheduled)/self.stat_propose_calls:.1f}%")
            avg = (self._propose_time_total/self.stat_propose_calls)*1000.0
            print(f"avg propose ms: {avg:.3f}")
        s=self.obl.stats
        rate=(100.0*s["fulfilled_in_time"]/s["created"]) if s["created"] else 0.0
        print(f"obligations   : created={s['created']} assigned={s['assigned']} fulfilled={s['fulfilled']} in_time={s['fulfilled_in_time']} expired={s['expired']} success={rate:.1f}%")
        if self._bootstrap_started:
            dur = (self._bootstrap_end_time - self._bootstrap_start_time) if (self._bootstrap_end_time is not None and self._bootstrap_start_time is not None) else None
            print(f"bootstrap     : started_at={self._bootstrap_start_time} ended_at={self._bootstrap_end_time} duration={dur}")
        # Observability CSV dumps (best-effort)
        try:
            # preflight histograms
            with open('metrics_preflight_hist.csv','w',newline='',encoding='utf-8') as f:
                w=csv.writer(f); w.writerow(['mode','planned_len','count'])
                for k,v in sorted(self._obs['pf_local_len_hist'].items()): w.writerow(['local',k,v])
                for k,v in sorted(self._obs['pf_global_len_hist'].items()): w.writerow(['global',k,v])
            with open('metrics_preflight_stops.csv','w',newline='',encoding='utf-8') as f:
                w=csv.writer(f); w.writerow(['mode','reason','count'])
                for r,c in sorted(self._obs['pf_local_stops'].items()): w.writerow(['local',r,c])
                for r,c in sorted(self._obs['pf_global_stops'].items()): w.writerow(['global',r,c])
            # checkpoint summary
            with open('metrics_checkpoint.csv','w',newline='',encoding='utf-8') as f:
                w=csv.writer(f); w.writerow(['success_batches','rollback_batches','ops_committed'])
                w.writerow([self._obs['ckpt_success_batches'], self._obs['ckpt_rollback_batches'], self._obs['ckpt_ops_committed']])
            # global overlay matrix
            with open('metrics_overlay_global.csv','w',newline='',encoding='utf-8') as f:
                w=csv.writer(f); w.writerow(['die','plane','planescope_fail','bus_fail','excl_fail','latch_fail'])
                for (die,pl), d in sorted(self._obs['overlay_fail_global'].items()):
                    w.writerow([die,pl,int(d.get('planescope',0)),int(d.get('bus',0)),int(d.get('excl',0)),int(d.get('latch',0))])
        except Exception as e:
            print(f"[OBS] metrics write skipped: {e}")

# --------------------------------------------------------------------------
# Main
def main():
    # 1) 구성
    # 1.0) 로깅 옵션
    CFG["export"]["log_to_file"] = True
    CFG["export"]["log_tee"] = False
    CFG["bootstrap"]["disable_timeline_logging"] = False
    CFG["bootstrap"]["split_timeline_logging"] = False
    # 1.1) bootstrap, phase conditional 활성화 여부 설정
    CFG["bootstrap"]["enabled"] = True
    CFG["policy"]["enable_phase_conditional"] = True
    CFG["bootstrap"]["pgm_ratio"] = 0.2
    # progressive propose 모드 기본값 (env override 지원)
    CFG["propose"]["mode"] = "progressive"
    # ---- Environment overrides ----
    try:
        _mode = os.environ.get("PROPOSE_MODE")
        if _mode:
            CFG["propose"]["mode"] = _mode
        _log_path = os.environ.get("RUN_LOG_PATH")
        if _log_path:
            CFG["export"]["log_path"] = _log_path
        _chunk_max = os.environ.get("CHUNK_MAX")
        if _chunk_max:
            CFG["propose"].setdefault("chunking", {})["max_ops_per_chunk"] = int(_chunk_max)
        _chunk_partial = os.environ.get("CHUNK_PARTIAL")
        if _chunk_partial is not None:
            # accept values like '1','true','True'
            val = str(_chunk_partial).strip().lower() in ("1","true","yes","y")
            CFG["propose"].setdefault("chunking", {})["allow_partial_success"] = val
        _chunk_ckpt = os.environ.get("CHUNK_CHECKPOINT")
        if _chunk_ckpt:
            CFG["propose"].setdefault("chunking", {})["checkpoint_interval"] = int(_chunk_ckpt)
        _inj = os.environ.get("INJECT_FAIL_RATE")
        if _inj:
            CFG["propose"].setdefault("scheduling", {})["inject_fail_rate"] = float(_inj)
        _sth = os.environ.get("SAMPLER_THRESH")
        if _sth:
            CFG["propose"].setdefault("sampling", {})["starvation_threshold"] = int(_sth)
        _sboost = os.environ.get("SAMPLER_BOOST")
        if _sboost:
            CFG["propose"].setdefault("sampling", {})["boost_factor"] = float(_sboost)
    except Exception as e:
        print(f"[ENV] overrides skipped: {e}")

    # Optional guard/sampler tuning for experiments
    try:
        if str(os.environ.get("RELAX_LATCH", "")).strip().lower() in ("1","true","yes","y"):
            for name, spec in CFG.get("op_specs", {}).items():
                ig = list(spec.get("ignore", []))
                if "latch" not in ig:
                    ig.append("latch")
                spec["ignore"] = ig
            print("[TUNE] latch checks relaxed via ignore list")
        if str(os.environ.get("RELAX_EXCL", "")).strip().lower() in ("1","true","yes","y"):
            CFG.setdefault("constraints", {})["exclusions"] = []
            print("[TUNE] exclusion windows disabled")
        if str(os.environ.get("RELAX_SCOPE", "")).strip().lower() in ("1","true","yes","y"):
            for name, spec in CFG.get("op_specs", {}).items():
                base = str(spec.get("base", name))
                if base in ("PROGRAM", "ERASE"):
                    spec["scope"] = "PLANE_SET"
            print("[TUNE] scope for PROGRAM/ERASE relaxed to PLANE_SET")
        if str(os.environ.get("INCREASE_READ_WEIGHT", "")).strip().lower() in ("1","true","yes","y"):
            pc = CFG.get("phase_conditional", {})
            for key, dist in pc.items():
                if not isinstance(dist, dict):
                    continue
                # favor READ aliases
                base = {"SIN_READ": 0.6, "MUL_READ": 0.3}
                for k in list(dist.keys()):
                    if k.startswith("SIN_") or k.startswith("MUL_"):
                        if k.endswith("READ"):
                            dist[k] = base.get(k, 0.05)
                        else:
                            dist[k] = 0.05
            print("[TUNE] sampler weights biased towards READ")
    except Exception as e:
        print(f"[TUNE] tuning skipped: {e}")
    # 1.2) topology 설정
    CFG["topology"]["dies"] = 1
    CFG["topology"]["planes"] = 4
    CFG["topology"]["blocks"] = 8
    CFG["topology"]["pages_per_block"] = 100
    print(f"topology: {CFG['topology']}")
    # 시각화 on/off 토글
    enable_visualization = False
    try:
        exp = CFG.get("export", {})
        if bool(exp.get("log_to_file", False)):
            path = str(exp.get("log_path", "run.log"))
            f = open(path, "w", encoding="utf-8", newline="")
            if bool(exp.get("log_tee", True)):
                class _Tee:
                    def __init__(self, a, b):
                        self.a=a; self.b=b
                    def write(self, s):
                        self.a.write(s); self.b.write(s)
                    def flush(self):
                        self.a.flush(); self.b.flush()
                sys.stdout = _Tee(sys.stdout, f)
            else:
                sys.stdout = f
    except Exception as e:
        print(f"[LOG] redirect skipped: {e}")

    _seed_rng_from_cfg(CFG)
    _coerce_states_to_fixed(CFG)
    _validate_phase_conditional_cfg(CFG)
    logger = TimelineLogger()
    rejlog = RejectionLogger()
    addr = AddressManager(CFG); excl = ExclusionManager(CFG)
    obl  = ObligationManager(CFG["obligations"], cfg_root=CFG)
    latch = LatchManager()
    # shared state timeline
    state_timeline = StateTimeline()
    spe  = PolicyEngine(CFG, addr, obl, excl, rejlog=rejlog, latch=latch, state_timeline=state_timeline)
    sch  = Scheduler(CFG, addr, spe, obl, excl, logger=logger, latch=latch, state_timeline=state_timeline)

    # 1.3) Bootstrap obligations (optional)
    try:
        populate_bootstrap_obligations(CFG, addr, obl)
    except Exception as e:
        print(f"[BOOTSTRAP] skipped: {e}")

    # 2) 실행: bootstrap 전용 여유와 총 러닝타임 분리 계산
    CFG["policy"]["run_until_us"] = 50000.0

    run_until_base = CFG["policy"]["run_until_us"]
    run_until_boot = 0.0
    try:
        if CFG.get("bootstrap", {}).get("enabled", False) and hasattr(obl, "heap") and obl.heap:
            # 부트스트랩 의무만 필터링
            boot_items = [it for it in obl.heap if getattr(it.ob, "source", None) == "bootstrap"]
            if boot_items:
                last_deadline_boot = max((it.ob.deadline_us for it in boot_items), default=run_until_base)
                num_bootstrap_ob = len(boot_items)
                margin_per_ob = float(CFG.get("policy", {}).get("run_until_bootstrap_margin_per_ob_us", 3.0))
                # 제안식: run_until_boot = last_deadline_boot + num_bootstrap_ob * margin_per_ob
                run_until_boot = quantize(last_deadline_boot + num_bootstrap_ob * margin_per_ob)
                print(f"bootstrap: {num_bootstrap_ob} obligations, run_until_boot={run_until_boot}")
    except Exception:
        run_until_boot = 0.0
    # 총 런: run_until_tot = run_until_boot + run_until_base (사용자 제안식)
    run_until_tot = quantize(run_until_base + run_until_boot)
    sch.run_until(run_until_tot)

    # 3) DataFrame (timeline)
    bs_cfg = CFG.get("bootstrap", {})
    split_logging = bool(bs_cfg.get("split_timeline_logging", False))
    df_boot = None
    df_pol = None
    if split_logging and (hasattr(sch, "_timeline_rows_bootstrap") or hasattr(sch, "_timeline_rows_policy")):
        # 분리 저장
        try:
            import pandas as _pd
            df_boot = _pd.DataFrame(getattr(sch, "_timeline_rows_bootstrap", []))
            df_pol  = _pd.DataFrame(getattr(sch, "_timeline_rows_policy", []))
            # enrich function: add derived cols like TimelineLogger.to_dataframe
            def _enrich(df_):
                if df_.empty:
                    return df_
                df_ = df_.sort_values(["die", "block", "start_us", "end_us"]).reset_index(drop=True)
                df_["seq_per_block"] = df_.groupby(["die", "block"]).cumcount() + 1
                df_["seq_global_die"] = df_.groupby(["die"]).cumcount() + 1
                df_["dur_us"] = df_["end_us"] - df_["start_us"]
                return df_
            df_boot = _enrich(df_boot)
            df_pol  = _enrich(df_pol)
            if not df_boot.empty:
                df_boot.to_csv(bs_cfg.get("bootstrap_timeline_path", "nand_timeline_bootstrap.csv"), index=False)
            if not df_pol.empty:
                df_pol.to_csv(bs_cfg.get("policy_timeline_path", "nand_timeline_policy.csv"), index=False)
            # 병합본도 기본 경로로 저장(있을 때)
            df_all = _pd.concat([df_boot, df_pol], ignore_index=True) if ((df_boot is not None and not df_boot.empty) or (df_pol is not None and not df_pol.empty)) else _pd.DataFrame()
            if not df_all.empty:
                df_all = _enrich(df_all)
            df = df_all
            df.to_csv("nand_timeline.csv", index=False)
        except Exception as e:
            print(f"[TIMELINE] split save failed: {e}")
            df = logger.to_dataframe()
            df.to_csv("nand_timeline.csv", index=False)
    else:
        df = logger.to_dataframe()
        df.to_csv("nand_timeline.csv", index=False)

    # 3.5) Export state timeline for Bokeh Gantt
    try:
        state_timeline.to_csv("nand_state_timeline.csv")
        print("[STATE_TIMELINE] saved to nand_state_timeline.csv")
    except Exception as e:
        print(f"[STATE_TIMELINE] save skipped: {e}")


    # 4) 규칙 자동검증
    report = validate_timeline(df, CFG)
    print_validation_report(report, max_rows=30)
    viol_df = violations_to_dataframe(report)
    viol_df.to_csv("nand_violations.csv", index=False)

    # 4.2) Target address usage stats (PROGRAM/READ 중심)
    try:
        stats = compute_block_usage_stats(df, kinds=("PROGRAM","READ"))
        save_block_usage_stats(stats, prefix="block_usage")
        print_block_usage_summary(stats, max_rows=50)
    except Exception as e:
        print(f"[BLOCK_USAGE] skipped: {e}")

    # 4.5) Rejection log
    try:
        rejlog.to_csv("reject_log.csv")
        rejlog.to_obligation_skips_csv("obligation_skips.csv")
        print("\n=== Rejection Summary (by stage/reason) ===")
        for stage, d in rejlog.stats.items():
            total = sum(d.values())
            acc   = rejlog.stage_accepts.get(stage, 0)
            att   = rejlog.stage_attempts.get(stage, 0)
            print(f"- {stage:18s}: attempts={att} accepts={acc} rejects={total}")
            for reason, cnt in sorted(d.items(), key=lambda x: -x[1])[:8]:
                print(f"    {reason:14s}: {cnt}")
        # Sampler metrics (if progressive/hybrid)
        try:
            mode = str(CFG.get("propose", {}).get("mode", "legacy")).lower()
            if mode in ("progressive","hybrid") and hasattr(spe, '_propose_mode'):
                # PolicyEngine holds sampler at spe._sampler
                spe._sampler.to_csv('metrics_sampler.csv')
        except Exception as e:
            print(f"[SAMPLER] export skipped: {e}")
    except Exception as e:
        print(f"[REJLOG] skipped: {e}")

    # 4.6) Obligation creations CSV (if available)
    try:
        if hasattr(obl, "creation_logger"):
            obl.creation_logger.to_csv("obligation_creations.csv")
    except Exception as e:
        print(f"[CREATIONS] skipped: {e}")

    # 4.7) 성능 프로파일 저장 제거됨

    # 5) 시각화
    if enable_visualization:
        if split_logging and (df_boot is not None or df_pol is not None):
            if df_boot is not None and not df_boot.empty:
                plot_gantt_by_die(df_boot, title="Bootstrap Timeline")
                plot_block_page_sequence_3d_by_die(df_boot, kinds=("ERASE","PROGRAM","READ"),
                                                   z_mode="global_die", draw_lines=True)
                # heatmap (bootstrap)
                try:
                    plot_target_heatmap(df_boot, kinds=("PROGRAM","READ"),
                                        title="Target heatmap (bootstrap)",
                                        save_path="figs/heatmap_bootstrap.png")
                except Exception as e:
                    print(f"[HEATMAP] bootstrap skipped: {e}")
            if df_pol is not None and not df_pol.empty:
                plot_gantt_by_die(df_pol, title="Policy Timeline")
                plot_block_page_sequence_3d_by_die(df_pol, kinds=("ERASE","PROGRAM","READ"),
                                                   z_mode="global_die", draw_lines=True)
                try:
                    plot_target_heatmap(df_pol, kinds=("PROGRAM","READ"),
                                        title="Target heatmap (policy)",
                                        save_path="figs/heatmap_policy.png")
                except Exception as e:
                    print(f"[HEATMAP] policy skipped: {e}")
        else:
            plot_gantt_by_die(df)  # 모든 die별로 개별 그림
            plot_block_page_sequence_3d_by_die(df, kinds=("ERASE","PROGRAM","READ"),
                                               z_mode="global_die", draw_lines=True)
            try:
                plot_target_heatmap(df, kinds=("PROGRAM","READ"),
                                    title="Target heatmap (all)",
                                    save_path="figs/heatmap_all.png")
            except Exception as e:
                print(f"[HEATMAP] all skipped: {e}")
    else:
        print("[VIZ] disabled")

    # (선택) 미리보기
    # df_preview = pattern_preview_dataframe(df, CFG)
    # print(df_preview.head())

    # CSV 내보내기
    paths = export_patterns(df, CFG)
    print("written:", paths)

if __name__=="__main__":
    main()
