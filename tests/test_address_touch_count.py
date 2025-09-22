from __future__ import annotations

import csv
import tempfile
import unittest

from main import export_address_touch_count


class AddressTouchCountTests(unittest.TestCase):
    def test_program_counts_only_whitelisted_bases(self) -> None:
        rows = [
            {"op_base": "PROGRAM_SLC", "op_name": "ProgramSlc", "die": 0, "block": 1, "page": 2},
            {"op_base": "program_slc", "op_name": "ProgramSlc", "die": 0, "block": 1, "page": 2},
            {"op_base": "oneshot_program_exec_msb", "op_name": "OneShotExec", "die": 0, "block": 1, "page": 3},
            {"op_base": "PROGRAM_TLC", "op_name": "ProgramTlc", "die": 0, "block": 1, "page": 4},
            {"op_base": "READ", "op_name": "ReadOp", "die": 0, "block": 1, "page": 5},
            {"op_base": "CACHE_PROGRAM_TLC", "op_name": "CacheProgramTlc", "die": 0, "block": 1, "page": 6},
        ]
        cfg = {
            "op_names": {
                "ProgramSlc": {"celltype": "SLC"},
                "OneShotExec": {"celltype": "TLC"},
                "ReadOp": {"celltype": "SLC"},
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = export_address_touch_count(rows, cfg, out_dir=tmpdir, run_idx=0)
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                data = list(reader)

        self.assertEqual(len(data), 3)

        program_entries = [row for row in data if row["op_base"] == "PROGRAM"]
        self.assertEqual(len(program_entries), 2)

        counts_by_page = {int(row["page"]): int(row["count"]) for row in program_entries}
        self.assertEqual(counts_by_page[2], 2)
        self.assertEqual(counts_by_page[3], 1)

        read_entries = [row for row in data if row["op_base"] == "READ"]
        self.assertEqual(len(read_entries), 1)
        self.assertEqual(int(read_entries[0]["count"]), 1)

        pages_recorded = {int(row["page"]) for row in data}
        self.assertNotIn(4, pages_recorded)
        self.assertNotIn(6, pages_recorded)


if __name__ == "__main__":
    unittest.main()
