from __future__ import annotations

import csv
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from script_operation_timeline_counts import (
    collect_counts_from_directories,
    write_counts,
)


HEADER = [
    "start",
    "end",
    "die",
    "plane",
    "block",
    "page",
    "op_name",
    "op_base",
    "source",
    "op_uid",
    "op_state",
    "phase_key_used",
    "phase_key_virtual",
]


def write_timeline_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)


class OperationTimelineCountsTests(unittest.TestCase):
    def test_collect_counts_merges_multiple_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()

            write_timeline_csv(
                first / "operation_timeline_one.csv",
                [
                    [0, 1, 0, 0, 0, 0, "Program_Resume", "PROGRAM_RESUME", "", 1, "STATE", "", ""],
                    [0, 1, 0, 0, 0, 0, "Program_Resume", "PROGRAM_RESUME", "", 2, "STATE", "", ""],
                    [0, 1, 0, 0, 0, 0, "Program_Suspend_Reset", "PROGRAM_SUSPEND", "", 3, "STATE", "", ""],
                ],
            )
            write_timeline_csv(
                second / "operation_timeline_two.csv",
                [
                    [0, 1, 0, 0, 0, 0, "Program_Resume", "PROGRAM_RESUME", "", 4, "STATE", "", ""],
                    [0, 1, 0, 0, 0, 0, "Block_Erase_SLC", "ERASE", "", 5, "STATE", "", ""],
                ],
            )

            name_counts, base_counts = collect_counts_from_directories([first, second])

        self.assertEqual(name_counts["Program_Resume"], 3)
        self.assertEqual(name_counts["Program_Suspend_Reset"], 1)
        self.assertEqual(name_counts["Block_Erase_SLC"], 1)
        self.assertEqual(base_counts["PROGRAM_RESUME"], 3)
        self.assertEqual(base_counts["PROGRAM_SUSPEND"], 1)
        self.assertEqual(base_counts["ERASE"], 1)

    def test_collect_counts_errors_when_column_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "missing_column"
            directory.mkdir()
            path = directory / "operation_timeline_bad.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(["op_name", "start"])
                writer.writerow(["Program_Resume", 0])

            with self.assertRaises(ValueError):
                collect_counts_from_directories([directory])

    def test_write_counts_produces_sorted_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "counts.csv"
            counts = Counter({"b": 2, "a": 2, "c": 1})
            write_counts(output_path, "label", counts)
            with output_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                rows = list(reader)

        self.assertEqual(rows[0], ["label", "count"])
        self.assertEqual(rows[1], ["a", "2"])
        self.assertEqual(rows[2], ["b", "2"])
        self.assertEqual(rows[3], ["c", "1"])


if __name__ == "__main__":
    unittest.main()
