#!/usr/bin/env python3
"""Aggregate op_name and op_base counts from operation timeline CSV files."""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_OP_NAME_FILENAME = "op_name_counts.csv"
DEFAULT_OP_BASE_FILENAME = "op_base_counts.csv"
REQUIRED_COLUMNS = ("op_name", "op_base")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate op_name and op_base frequencies from operation timeline CSV files."
    )
    parser.add_argument(
        "directories",
        nargs="+",
        type=Path,
        help="Directories to scan for operation_timeline*.csv files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory where output files will be written (defaults to current directory).",
    )
    parser.add_argument(
        "--op-name-output",
        type=Path,
        help="Explicit path for the op_name frequency CSV file.",
    )
    parser.add_argument(
        "--op-base-output",
        type=Path,
        help="Explicit path for the op_base frequency CSV file.",
    )
    return parser.parse_args(argv)


SITE_DIR_PATTERN = re.compile(r"site_\d+$")


def _candidate_directories(root: Path) -> list[Path]:
    candidates = [root]
    for child in sorted(root.iterdir()):
        if child.is_dir() and SITE_DIR_PATTERN.fullmatch(child.name):
            candidates.append(child)
    return candidates


def discover_csv_files(directories: Sequence[Path]) -> list[Path]:
    discovered: list[Path] = []
    seen: set[Path] = set()
    for directory in directories:
        if not directory.exists():
            raise FileNotFoundError(f"directory not found: {directory}")
        if not directory.is_dir():
            raise NotADirectoryError(f"not a directory: {directory}")
        for candidate_dir in _candidate_directories(directory):
            for candidate in sorted(candidate_dir.glob("operation_timeline*.csv")):
                if candidate in seen:
                    continue
                seen.add(candidate)
                discovered.append(candidate)
    return discovered


def _require_columns(path: Path, fieldnames: Iterable[str] | None) -> None:
    available = set(fieldnames or [])
    missing = [column for column in REQUIRED_COLUMNS if column not in available]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"{path} missing required columns: {joined}")


def aggregate_counts(paths: Sequence[Path]) -> tuple[Counter[str], Counter[str]]:
    name_counts: Counter[str] = Counter()
    base_counts: Counter[str] = Counter()
    for path in paths:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            _require_columns(path, reader.fieldnames)
            for row in reader:
                name = (row.get("op_name") or "").strip()
                base = (row.get("op_base") or "").strip()
                if name:
                    name_counts[name] += 1
                if base:
                    base_counts[base] += 1
    return name_counts, base_counts


def _sorted_counts(items: Counter[str]) -> list[tuple[str, int]]:
    return sorted(items.items(), key=lambda entry: (-entry[1], entry[0]))


def write_counts(path: Path, header_label: str, items: Counter[str]) -> None:
    rows = _sorted_counts(items)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([header_label, "count"])
        for key, count in rows:
            writer.writerow([key, count])


def collect_counts_from_directories(directories: Sequence[Path]) -> tuple[Counter[str], Counter[str]]:
    files = discover_csv_files(directories)
    if not files:
        return Counter(), Counter()
    return aggregate_counts(files)


def _resolve_output_path(explicit: Path | None, base_dir: Path, default_name: str) -> Path:
    destination = explicit or base_dir / default_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        files = discover_csv_files(args.directories)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        name_counts, base_counts = aggregate_counts(files)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not files:
        print("warning: no operation timeline CSV files found", file=sys.stderr)

    base_dir = args.output_dir or Path.cwd()
    if args.output_dir:
        base_dir.mkdir(parents=True, exist_ok=True)

    name_path = _resolve_output_path(args.op_name_output, base_dir, DEFAULT_OP_NAME_FILENAME)
    base_path = _resolve_output_path(args.op_base_output, base_dir, DEFAULT_OP_BASE_FILENAME)

    write_counts(name_path, "op_name", name_counts)
    write_counts(base_path, "op_base", base_counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
