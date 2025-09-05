from __future__ import annotations

import random
import os
import sys

# Ensure repo root is on sys.path when running from tools/
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # optional; we will fallback to defaults

from scheduler import Scheduler
from resourcemgr import ResourceManager
try:
    from addrman import AddressManager  # requires numpy
    _HAS_NUMPY = True
except Exception:
    AddressManager = None  # type: ignore
    _HAS_NUMPY = False


def _minimal_cfg(base_cfg: dict) -> dict:
    cfg = dict(base_cfg)
    # default distribution to a safe op
    cfg.setdefault("phase_conditional", {})
    if not cfg["phase_conditional"]:
        cfg["phase_conditional"] = {"DEFAULT": {"Block_Erase_SLC": 1.0}}
    # small window and refill period for demo
    pol = dict(cfg.get("policies", {}) or {})
    pol.setdefault("admission_window", 1.0)
    pol.setdefault("queue_refill_period_us", 50.0)
    cfg["policies"] = pol
    # minimal op_bases/op_names if missing (to avoid YAML dependency)
    op_bases = dict(cfg.get("op_bases", {}) or {})
    if "ERASE" not in op_bases:
        op_bases["ERASE"] = {
            "scope": "DIE_WIDE",
            "affect_state": True,
            "instant_resv": False,
            "states": [
                {"ISSUE": {"bus": True, "duration": 0.4}},
                {"CORE_BUSY": {"bus": False, "duration": 8.0}},
            ],
        }
    cfg["op_bases"] = op_bases
    op_names = dict(cfg.get("op_names", {}) or {})
    if "Block_Erase_SLC" not in op_names:
        op_names["Block_Erase_SLC"] = {
            "base": "ERASE",
            "multi": False,
            "celltype": "SLC",
            "durations": {"ISSUE": 0.4, "CORE_BUSY": 8.0},
        }
    cfg["op_names"] = op_names
    return cfg


def _mk_addrman(cfg: dict):
    topo = cfg.get("topology", {})
    dies = int(topo.get("dies", 1))
    planes = int(topo.get("planes", 2))
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
    # Fallback: minimal sampler that returns Python lists ((#=1,k,3))
    class _SimpleAM:
        def __init__(self, dies: int, planes: int, blocks: int, pages: int):
            self.dies, self.planes, self.blocks, self.pages = dies, planes, blocks, pages
        def _one_block(self, sel_die=None):
            d = 0 if sel_die is None else (sel_die if isinstance(sel_die, int) else list(sel_die)[0])
            return [d, 0, 0]
        def sample_erase(self, sel_plane=None, mode: str = "SLC", size: int = 1, sel_die=None):
            if isinstance(sel_plane, list):
                return [[self._one_block(sel_die) for _ in sel_plane]]
            return [[self._one_block(sel_die)]]
        def sample_pgm(self, sel_plane=None, mode: str = "SLC", size: int = 1, sequential: bool = False, sel_die=None):
            return self.sample_erase(sel_plane=sel_plane, mode=mode, size=size, sel_die=sel_die)
        def sample_read(self, sel_plane=None, mode: str = "SLC", size: int = 1, offset: int | None = None, sequential: bool = False, sel_die=None):
            return self.sample_erase(sel_plane=sel_plane, mode=mode, size=size, sel_die=sel_die)
    return _SimpleAM(dies, planes, blocks, pages)


def main() -> None:
    if yaml is not None:
        try:
            with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
                base_cfg = yaml.safe_load(f)
        except Exception:
            base_cfg = {}
    else:
        base_cfg = {}
    cfg = _minimal_cfg(base_cfg)

    topo = cfg.get("topology", {})
    dies = int(topo.get("dies", 1))
    planes = int(topo.get("planes", 2))
    rm = ResourceManager(cfg=cfg, dies=dies, planes=planes)
    am = _mk_addrman(cfg)

    rng = random.Random(42)
    sched = Scheduler(cfg=cfg, rm=rm, addrman=am, rng=rng)

    res = sched.run(run_until_us=500, max_hooks=50)
    print("Scheduler result:", res)


if __name__ == "__main__":
    main()
