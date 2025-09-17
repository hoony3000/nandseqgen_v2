#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, TextIO


@dataclass(frozen=True)
class IntRange:
    start: int
    stop: int


@dataclass(frozen=True)
class IndexedChoiceSpec:
    choices: Sequence[Sequence[str]]

    def random_value(self, rng: random.Random) -> List[str]:
        return [rng.choice(options) for options in self.choices]


DEFAULT_EXP_VAL_CHOICES: Sequence[Sequence[str]] = (
    ("busy", "extrdy", "ready"),
    ("no_erasus", "erasus"),
    ("no_pgmsus", "pgmsus"),
)

TIME_STEP = 100.0


def create_field_specs(exp_val_override: Sequence[Sequence[str]] | None = None) -> Dict[str, object]:
    exp_val_choices = exp_val_override or DEFAULT_EXP_VAL_CHOICES
    if len(exp_val_choices) != 3:
        raise ValueError("exp_val choices must contain exactly three index lists")
    normalized: List[Sequence[str]] = []
    for idx, options in enumerate(exp_val_choices):
        if not options:
            raise ValueError(f"exp_val index {idx} must have at least one choice")
        normalized.append(tuple(options))
    return {
        "die": IntRange(0, 1),
        "plane": IntRange(0, 4),
        "block": IntRange(0, 1000),
        "page": IntRange(0, 2564),
        "celltype": (
            "NONE",
            "FWSLC",
            "SLC",
            "AESLC",
            "A0SLC",
            "ACSLC",
            "TLC",
        ),
        "exp_val": IndexedChoiceSpec(tuple(normalized)),
    }


def parse_field_tokens(raw: str) -> List[str]:
    cleaned = raw.strip()[1:-1].strip()
    if not cleaned:
        return []
    return [token.strip() for token in cleaned.split(",")]


def load_payload_definitions(path: Path) -> List[Dict[str, object]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    payloads = []
    for line in lines:
        if not line.strip():
            continue
        op_name, base, field_part = line.split("\t", 2)
        fields = parse_field_tokens(field_part)
        payloads.append({"op_name": op_name, "base": base, "fields": fields})
    return payloads


def generate_field_value(field: str, rng: random.Random, field_specs: Mapping[str, object]) -> object:
    spec = field_specs.get(field)
    if spec is None:
        raise KeyError(f"No field spec registered for '{field}'")
    if isinstance(spec, IntRange):
        if spec.stop <= spec.start:
            raise ValueError(f"Invalid range for '{field}': {spec}")
        return rng.randrange(spec.start, spec.stop)
    if isinstance(spec, IndexedChoiceSpec):
        return spec.random_value(rng)
    if isinstance(spec, Sequence) and not isinstance(spec, (str, bytes)):
        if not spec:
            raise ValueError(f"Choice list for '{field}' is empty")
        return rng.choice(spec)
    raise TypeError(f"Unsupported spec type for '{field}': {type(spec)!r}")


def randomize_payload_fields(
    fields: Sequence[str], rng: random.Random, field_specs: Mapping[str, object]
) -> Dict[str, object]:
    return {field: generate_field_value(field, rng, field_specs) for field in fields}


def build_payload_instances(
    defs: Iterable[Dict[str, object]], rng: random.Random, field_specs: Mapping[str, object]
) -> List[Dict[str, object]]:
    results = []
    for entry in defs:
        payload = randomize_payload_fields(entry["fields"], rng, field_specs)
        results.append({"op_name": entry["op_name"], "base": entry["base"], "payload": payload})
    return results


def write_csv(instances: Sequence[Dict[str, object]], stream: TextIO, *, time_step: float = TIME_STEP) -> None:
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["seq", "time", "op_id", "op_name", "op_uid", "payload"])
    for seq, instance in enumerate(instances, start=1):
        payload_json = json.dumps(
            [instance["payload"]], ensure_ascii=True, separators=(",", ":")
        )
        time_value = (seq - 1) * time_step
        writer.writerow([seq, time_value, 0, instance["op_name"], seq, payload_json])


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Randomize payload field values for NAND sequences and emit CSV rows."
    )
    parser.add_argument(
        "--payloads",
        type=Path,
        default=Path("payloads.txt"),
        help="Input payload definition file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination file for CSV output. Defaults to stdout.",
    )
    parser.add_argument("--seed", type=int, help="Seed for deterministic outputs.")
    parser.add_argument(
        "--exp-val-choices",
        type=str,
        help=(
            "JSON array of three string arrays defining exp_val choices per index. "
            "Example: [[\"OK\",\"FAIL\"],[\"BUSY\"],[\"WP\"]]"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    rng = random.Random(args.seed)
    exp_val_override = None
    if args.exp_val_choices:
        try:
            loaded = json.loads(args.exp_val_choices)
        except json.JSONDecodeError as exc:
            raise ValueError("--exp-val-choices must be valid JSON") from exc
        if not isinstance(loaded, list):
            raise ValueError("--exp-val-choices must be a JSON array of arrays")
        exp_val_override = []
        for idx, element in enumerate(loaded):
            if not isinstance(element, list):
                raise ValueError(f"exp_val index {idx} must be a JSON array of strings")
            for choice in element:
                if not isinstance(choice, str):
                    raise ValueError(
                        f"exp_val index {idx} contains non-string value {choice!r}"
                    )
            exp_val_override.append(element)

    field_specs = create_field_specs(exp_val_override)
    payload_defs = load_payload_definitions(args.payloads)
    instances = build_payload_instances(payload_defs, rng, field_specs)
    output = args.output.open("w", encoding="utf-8", newline="") if args.output else None
    try:
        stream = output or sys.stdout
        write_csv(instances, stream)
    finally:
        if output:
            output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
