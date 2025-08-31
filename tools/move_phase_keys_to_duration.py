"""
Move phase duration keys under `duration` within each op in `op_names`,
while preserving YAML anchors and aliases.

Rules:
- For each op under `op_names`, move any key whose value is numeric
  (int/float) or an alias to a numeric anchored scalar into the op's
  `duration` map.
- Keep `base`, `multi`, `celltype`, and `duration` at the top level.
- Preserve anchors/aliases by using ruamel.yaml round‑trip mode and moving
  node objects without copying.

Idempotent: running multiple times yields the same structure.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap
except ModuleNotFoundError as exc:
    # Provide a clear, actionable message if dependency is missing.
    print(
        "Missing dependency: ruamel.yaml.\n"
        "Install it with one of the following commands:\n"
        "  python3 -m pip install --user ruamel.yaml\n"
        "  # or inside your venv\n"
        "  pip install ruamel.yaml\n",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


KEEP_TOP_LEVEL = {"base", "multi", "celltype", "duration"}


def is_number_like(v: Any) -> bool:
    """Return True if value behaves like a number for our purposes.

    ruamel's ScalarFloat/ScalarInt subclass float/int, so isinstance checks work.
    """
    return isinstance(v, (int, float))


def transform_rt(data: CommentedMap) -> CommentedMap:
    if not isinstance(data, CommentedMap):
        raise ValueError("Root YAML must be a mapping")

    op_names = data.get("op_names")
    if not isinstance(op_names, CommentedMap):
        raise ValueError("Expected 'op_names' to be a mapping")

    for op_key, op_val in op_names.items():
        if not isinstance(op_val, CommentedMap):
            continue

        duration = op_val.get("duration")
        if duration is None:
            duration = CommentedMap()
            op_val["duration"] = duration
        elif not isinstance(duration, CommentedMap):
            raise ValueError(f"op_names['{op_key}'].duration must be a mapping")

        # Identify movable keys first to avoid modifying while iterating over map
        keys_to_move = []
        for k, v in op_val.items():
            if k in KEEP_TOP_LEVEL:
                continue
            if is_number_like(v):
                keys_to_move.append(k)

        for k in keys_to_move:
            # Move the node object to preserve alias/anchor identity
            node = op_val.pop(k)
            duration[k] = node

    return data


def main(path: str) -> int:
    p = Path(path)
    if not p.exists():
        print(f"Error: file not found: {p}")
        return 1

    yaml_rt = YAML(typ="rt")
    yaml_rt.preserve_quotes = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)

    with p.open("r", encoding="utf-8") as f:
        data = yaml_rt.load(f)

    data = transform_rt(data)

    with p.open("w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)

    print(f"Updated (aliases preserved): {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python tools/move_phase_keys_to_duration.py <path-to-op_specs.yaml>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
