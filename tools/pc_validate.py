from __future__ import annotations

import sys
from typing import Any, Dict

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


def _load_yaml(path: str) -> Dict[str, Any]:
    if yaml is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data or {}
    except Exception:
        return {}


def _summary(dist: Dict[str, Any], op_names: Dict[str, Any]) -> Dict[str, Any]:
    ok = {}
    invalid_names = []
    nonpos = []
    try:
        for k, v in (dist or {}).items():
            name = str(k)
            try:
                p = float(v)
            except Exception:
                p = 0.0
            if name not in op_names:
                invalid_names.append(name)
            if p <= 0.0:
                nonpos.append(name)
            ok[name] = p
    except Exception:
        ok = {}
    s = sum([float(v) for v in ok.values()]) if ok else 0.0
    pos_cnt = len([1 for v in ok.values() if v > 0.0])
    return {
        "count": len(ok),
        "pos_count": pos_cnt,
        "sum": s,
        "invalid_names": invalid_names,
        "nonpos": nonpos,
        "top": sorted(ok.items(), key=lambda x: -x[1])[:5],
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python tools/pc_validate.py <config.yaml>")
        return 2
    cfg_path = argv[1]
    cfg = _load_yaml(cfg_path)
    pc = (cfg.get("phase_conditional", {}) or {})
    op_names = (cfg.get("op_names", {}) or {})
    if not pc:
        print("phase_conditional: EMPTY (no keys)")
        return 0
    print(f"phase_conditional: {len(pc)} keys")
    total_keys = 0
    empty_keys = 0
    bad_names_total = 0
    nonpos_total = 0
    not_norm = 0
    for k in sorted(pc.keys()):
        v = pc.get(k) or {}
        if not isinstance(v, dict):
            print(f" - {k}: invalid type={type(v).__name__} (expected dict)")
            empty_keys += 1
            total_keys += 1
            continue
        sm = _summary(v, op_names)
        total_keys += 1
        if sm["count"] == 0 or sm["pos_count"] == 0:
            empty_keys += 1
        if sm["invalid_names"]:
            bad_names_total += len(sm["invalid_names"])
        if sm["nonpos"]:
            nonpos_total += len(sm["nonpos"])
        if sm["sum"] > 0 and abs(sm["sum"] - 1.0) > 1e-6:
            not_norm += 1
        # concise per-key line
        top_s = ", ".join([f"{name}:{p:.4f}" for (name, p) in sm["top"]])
        print(
            f" - {k}: cnt={sm['count']} pos={sm['pos_count']} sum={sm['sum']:.6f}"
            + (f" top=[{top_s}]" if sm["top"] else "")
        )
    print(
        f"summary: keys={total_keys} empty={empty_keys} invalid_names={bad_names_total} nonpos={nonpos_total} not_normalized={not_norm}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

