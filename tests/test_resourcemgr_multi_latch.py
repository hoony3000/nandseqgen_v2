from __future__ import annotations

import json
import tempfile
import unittest

from resourcemgr import (
    Address,
    ResourceManager,
    Scope,
    READ_LATCH_KIND,
)
from main import save_snapshot


PROGRAM_LATCH_KIND = "LATCH_ON_MSB"


class _State:
    def __init__(self, name: str, dur_us: float, *, bus: bool = False) -> None:
        self.name = name
        self.dur_us = float(dur_us)
        self.bus = bool(bus)


class _Op:
    def __init__(self, base: str, dur_us: float = 5.0) -> None:
        self.base = base
        self.states = [
            _State("ISSUE", 0.2, bus=True),
            _State("CORE_BUSY", dur_us, bus=False),
        ]


class ResourceManagerMultiLatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = {
            "exclusion_groups": {
                "block_program": ["READ"],
            },
            "exclusions_by_latch_state": {
                PROGRAM_LATCH_KIND: ["block_program"],
            },
            "program_base_whitelist": ["ONESHOT_PROGRAM_MSB"],
        }
        self.rm = ResourceManager(cfg=self.cfg, dies=1, planes=2)
        self.read_targets = [Address(die=0, plane=0, block=0, page=0)]

    def _commit_read(self, start_us: float = 0.0) -> float:
        txn = self.rm.begin(start_us)
        op = _Op("READ", dur_us=3.0)
        res = self.rm.reserve(txn, op, self.read_targets, Scope.PLANE_SET)
        self.assertTrue(res.ok)
        self.rm.commit(txn)
        return float(res.end_us or 0.0)

    def _commit_program(self, start_us: float) -> float:
        txn = self.rm.begin(start_us)
        op = _Op("ONESHOT_PROGRAM_MSB", dur_us=4.0)
        res = self.rm.reserve(txn, op, self.read_targets, Scope.DIE_WIDE)
        self.assertTrue(res.ok)
        self.rm.commit(txn)
        return float(res.end_us or 0.0)

    def _plane_latch_kinds(self, die: int, plane: int) -> set[str]:
        bucket = self.rm._latch.get((die, plane), {})
        return set(bucket.keys())

    def test_multi_latch_release_per_kind(self) -> None:
        read_end = self._commit_read()
        prog_end = self._commit_program(read_end)

        kinds_before = self._plane_latch_kinds(0, 0)
        self.assertIn(READ_LATCH_KIND, kinds_before)
        self.assertIn(PROGRAM_LATCH_KIND, kinds_before)

        read_allowed = self.rm._latch_ok(_Op("READ", dur_us=1.0), self.read_targets, prog_end, Scope.PLANE_SET)
        self.assertFalse(read_allowed)

        self.rm.release_on_dout_end(self.read_targets, now_us=prog_end)
        kinds_after_read_release = self._plane_latch_kinds(0, 0)
        self.assertNotIn(READ_LATCH_KIND, kinds_after_read_release)
        self.assertIn(PROGRAM_LATCH_KIND, kinds_after_read_release)

        self.rm.release_on_exec_msb_end(0, now_us=prog_end + 1.0)
        self.assertEqual(self._plane_latch_kinds(0, 0), set())
        self.assertEqual(self._plane_latch_kinds(0, 1), set())

    def test_snapshot_round_trip_preserves_multi_latch(self) -> None:
        read_end = self._commit_read()
        prog_end = self._commit_program(read_end)

        snap = self.rm.snapshot()
        bucket = snap["latch"].get((0, 0))
        self.assertIsNotNone(bucket)
        assert bucket is not None
        self.assertIn(READ_LATCH_KIND, bucket)
        self.assertIn(PROGRAM_LATCH_KIND, bucket)

        rm2 = ResourceManager(cfg=self.cfg, dies=1, planes=2)
        rm2.restore(snap)
        restored_bucket = rm2._latch.get((0, 0), {})
        self.assertIn(READ_LATCH_KIND, restored_bucket)
        self.assertIn(PROGRAM_LATCH_KIND, restored_bucket)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_snapshot(self.rm, out_dir=tmpdir, run_idx=0)
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)

        latch_rows = data.get("latch", [])
        read_rows = [row for row in latch_rows if row["kind"] == READ_LATCH_KIND]
        prog_rows = [row for row in latch_rows if row["kind"] == PROGRAM_LATCH_KIND]
        self.assertEqual(len(read_rows), 1)
        self.assertGreaterEqual(len(prog_rows), 1)
        self.assertEqual(read_rows[0]["die"], 0)
        self.assertEqual(read_rows[0]["plane"], 0)
        self.assertTrue(all(row["die"] == 0 for row in prog_rows))
        plane_set = {row["plane"] for row in prog_rows}
        self.assertIn(0, plane_set)
        self.assertIn(1, plane_set)


if __name__ == "__main__":
    unittest.main()
