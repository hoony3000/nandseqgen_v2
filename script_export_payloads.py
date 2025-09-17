#!/usr/bin/env python3
"""Generate payload mapping for every op name declared in config.yaml."""
from __future__ import annotations

from pathlib import Path
import sys

import yaml

CONFIG_PATH = Path("config.yaml")
OUTPUT_PATH = Path("payloads.txt")


def format_payload(payload: list[str] | None) -> str:
    """Return a single-line YAML representation for the payload list."""
    payload = [] if payload is None else payload
    return yaml.safe_dump(payload, default_flow_style=True, sort_keys=False).strip()


def build_rows(config: dict) -> list[tuple[str, list[str]]]:
    rows: list[tuple[str, list[str]]] = []
    op_names = config.get("op_names") or {}
    payload_by_op_base = config.get("payload_by_op_base") or {}

    missing_base: list[str] = []
    missing_payload: list[tuple[str, str]] = []

    for op_name, attributes in op_names.items():
        base = (attributes or {}).get("base")
        if not base:
            missing_base.append(op_name)
            continue
        payload = payload_by_op_base.get(base)
        if payload is None:
            missing_payload.append((op_name, base))
            payload = []
        rows.append((op_name, payload))

    for name in missing_base:
        print(f"warning: {name} missing base", file=sys.stderr)
    for name, base in missing_payload:
        print(f"warning: {name} missing payload mapping for base {base}", file=sys.stderr)

    return rows


def write_rows(rows: list[tuple[str, list[str]]]) -> None:
    lines = [f"{op_name} {format_payload(payload)}" for op_name, payload in rows]
    if lines:
        OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        OUTPUT_PATH.write_text("", encoding="utf-8")


def main() -> int:
    if not CONFIG_PATH.exists():
        print(f"error: {CONFIG_PATH} not found", file=sys.stderr)
        return 1

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    rows = build_rows(config)
    write_rows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
