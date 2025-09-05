from __future__ import annotations

from typing import Any, Dict, List


class BootstrapController:
    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        b = (cfg.get("bootstrap", {}) or {})
        self._active: bool = bool(b.get("enabled", False))
        self._stage: int = 0
        thr = (b.get("thresholds", {}) or {})
        mins = (b.get("minimums", {}) or {})
        self._thr_erase = int(thr.get("erase_volume", 100))
        self._thr_program = int(thr.get("program_volume", 100))
        self._thr_read_cov = float(thr.get("read_coverage", 0.10))
        self._min_read_vol = int(mins.get("read_volume", 100))
        # progress trackers
        self._read_blocks: set[tuple[int, int]] = set()
        self._erase_blocks: set[tuple[int, int]] = set()
        self._program_blocks: set[tuple[int, int]] = set()

    def active(self) -> bool:
        return self._active

    def stage(self) -> int:
        return self._stage

    def maybe_advance(self, progress: Dict[str, Any]) -> None:
        if not self._active:
            return
        if self._stage == 0:
            if int(progress.get("erase_volume", 0)) >= self._thr_erase:
                self._stage = 1
        elif self._stage == 1:
            if int(progress.get("program_volume", 0)) >= self._thr_program:
                self._stage = 2
        elif self._stage == 2:
            if int(progress.get("read_volume", 0)) >= self._min_read_vol and float(progress.get("read_coverage", 0.0)) >= self._thr_read_cov:
                self._stage = 3
                self._active = False

    # Progress accounting
    def record_committed(self, bases: list[str], batch: Any) -> None:
        from resourcemgr import Address  # local import for type hints
        def key(t: Address) -> tuple[int, int]:
            return (int(t.die), int(t.block))
        for p, base in zip(batch.ops, bases):
            targets = getattr(p, "targets", []) or []
            if base.startswith("ERASE") or base == "ERASE":
                self._erase_blocks.update(key(t) for t in targets)
            elif base.startswith("PROGRAM") or base.startswith("CACHE_PROGRAM") or base.startswith("ONESHOT_PROGRAM") or base == "PROGRAM_SLC":
                self._program_blocks.update(key(t) for t in targets)
            elif base.startswith("READ") or base.startswith("PLANE_READ") or base.startswith("COPYBACK_READ"):
                self._read_blocks.update(key(t) for t in targets)

    def progress_snapshot(self, topology: Dict[str, Any]) -> Dict[str, Any]:
        dies = int((topology or {}).get("dies", 1))
        blocks_per_die = int((topology or {}).get("blocks_per_die", 1))
        total_blocks = dies * blocks_per_die if dies > 0 and blocks_per_die > 0 else 1
        coverage = (len(self._read_blocks) / float(total_blocks)) if total_blocks > 0 else 0.0
        return {
            "erase_volume": len(self._erase_blocks),
            "program_volume": len(self._program_blocks),
            "read_volume": len(self._read_blocks),
            "read_coverage": coverage,
        }

    def overlay_cfg(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        if not self._active:
            return cfg
        stage = self._stage
        allowed_names = self._allowed_op_names_for_stage(cfg, stage)
        pc = self._build_overlay_phase_conditional(cfg, allowed_names)
        out = dict(cfg)
        out["phase_conditional"] = pc
        return out

    def _allowed_op_names_for_stage(self, cfg: Dict[str, Any], stage: int) -> List[str]:
        names_by_base: Dict[str, List[str]] = {}
        for n, spec in (cfg.get("op_names", {}) or {}).items():
            b = str((spec or {}).get("base"))
            names_by_base.setdefault(b, []).append(str(n))
        if stage <= 0:
            bases = {"ERASE"}
        elif stage == 1:
            bases = {"PROGRAM_SLC", "CACHE_PROGRAM_SLC", "ONESHOT_PROGRAM_LSB", "ONESHOT_PROGRAM_CSB", "ONESHOT_PROGRAM_MSB", "ONESHOT_PROGRAM_MSB_23h", "ONESHOT_PROGRAM_EXEC_MSB", "ONESHOT_CACHE_PROGRAM"}
        else:
            bases = {"READ", "READ4K", "PLANE_READ", "PLANE_READ4K", "CACHE_READ", "CACHE_READ_END", "PLANE_CACHE_READ", "PLANE_CACHE_READ_END", "COPYBACK_READ", "DOUT", "DOUT4K"}
        allowed: List[str] = []
        for b in bases:
            allowed.extend(names_by_base.get(b, []) or [])
        for b in ("SR", "SR_ADD"):
            allowed.extend(names_by_base.get(b, []) or [])
        return list(dict.fromkeys(allowed))

    def _build_overlay_phase_conditional(self, cfg: Dict[str, Any], allowed_names: List[str]) -> Dict[str, Dict[str, float]]:
        orig = (cfg.get("phase_conditional", {}) or {})
        pc: Dict[str, Dict[str, float]] = {}
        weights = self._celltype_weights(cfg)

        def _op_celltype(op_name: str) -> str:
            try:
                ct = ((cfg.get("op_names", {}) or {}).get(op_name, {}) or {}).get("celltype")
                return str(ct) if ct not in (None, "None") else "NONE"
            except Exception:
                return "NONE"

        def _weight_for_name(op_name: str) -> float:
            ct = _op_celltype(op_name)
            try:
                return float(weights.get(ct, 1.0))
            except Exception:
                return 1.0

        def _weighted_uniform(names: List[str]) -> Dict[str, float]:
            if not names:
                return {}
            pairs = [(n, _weight_for_name(n)) for n in names]
            total = sum(w for (_, w) in pairs)
            if total <= 0.0:
                p = 1.0 / float(len(names))
                return {n: p for n in names}
            return {n: (w / total) for (n, w) in pairs}

        def filter_and_norm(m: Dict[str, Any]) -> Dict[str, float]:
            # Start with filtered entries
            base = {k: float(v) for k, v in (m or {}).items() if k in allowed_names and float(v) > 0.0}
            if not base:
                # No prior distribution: use weighted-uniform across allowed
                return _weighted_uniform(allowed_names)
            # Apply celltype weights
            weighted = {k: float(v) * _weight_for_name(k) for (k, v) in base.items()}
            s = sum(weighted.values())
            if s <= 0.0:
                # Fall back to weighted-uniform if all dropped to zero
                return _weighted_uniform(list(base.keys()))
            return {k: (val / s) for (k, val) in weighted.items()}
        base_default = orig.get("DEFAULT", {}) or {}
        pc["DEFAULT"] = filter_and_norm(base_default)
        for k, v in orig.items():
            if k == "DEFAULT":
                continue
            if isinstance(v, dict):
                pc[k] = filter_and_norm(v)
        return pc

    def _celltype_weights(self, cfg: Dict[str, Any]) -> Dict[str, float]:
        b = (cfg.get("bootstrap", {}) or {})
        src = (b.get("celltype_weights", {}) or {})
        out: Dict[str, float] = {}
        try:
            for k, v in src.items():
                out[str(k)] = float(v)
        except Exception:
            pass
        return out
