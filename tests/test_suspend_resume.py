from __future__ import annotations

import unittest

import types

from resourcemgr import Address, ResourceManager, Scope
from scheduler import Scheduler


class _StubRM:
    def __init__(self) -> None:
        self.suspended: set[int] = set()
        self.completed: list[int] = []

    def release_on_dout_end(self, targets, now_us: float) -> None:  # pragma: no cover - simple stub
        return

    def release_on_exec_msb_end(self, die: int, now_us: float) -> None:  # pragma: no cover
        return

    def is_op_suspended(self, op_id: int) -> bool:
        return op_id in self.suspended

    def complete_op(self, op_id: int) -> None:
        self.completed.append(op_id)
        self.suspended.discard(op_id)

    def begin(self, now_us: float):  # pragma: no cover - not used in this test
        return object()

    def rollback(self, txn) -> None:  # pragma: no cover - not used
        return

    def commit(self, txn) -> None:  # pragma: no cover - not used
        return


class _StubAddrMan:
    def __init__(self) -> None:
        self.apply_calls = 0

    def apply_pgm(self, addrs, mode=None) -> None:
        self.apply_calls += 1

    def apply_erase(self, addrs, mode=None) -> None:  # pragma: no cover - not used here
        return


def _mk_op(base: str, dur_us: float):
    class _State:
        def __init__(self, name: str, dur: float) -> None:
            self.name = name
            self.dur_us = float(dur)
            self.bus = False

    class _Op:
        def __init__(self, op_base: str, dur: float) -> None:
            self.base = op_base
            self.states = [_State("CORE_BUSY", dur)]

    return _Op(base, dur_us)


class SuspendResumeTests(unittest.TestCase):
    def test_resource_manager_repeat_suspend_updates_remaining_us(self) -> None:
        rm = ResourceManager(cfg={}, dies=1, planes=1)
        targets = [Address(die=0, plane=0, block=0, page=0)]
        uid = 101
        op = _mk_op("PROGRAM_SLC", 40.0)

        txn = rm.begin(0.0)
        res = rm.reserve(txn, op, targets, Scope.PLANE_SET)
        self.assertTrue(res.ok)
        rm.commit(txn)
        rm.register_ongoing(
            die=0,
            op_id=uid,
            op_name="PROGRAM_SLC",
            base="PROGRAM_SLC",
            targets=targets,
            start_us=0.0,
            end_us=40.0,
        )

        rm.move_to_suspended_axis(0, op_id=uid, now_us=10.0, axis="PROGRAM")
        suspended = rm.suspended_ops_program(0)[-1]
        self.assertAlmostEqual(suspended["remaining_us"], 30.0, places=6)
        self.assertTrue(rm.is_op_suspended(uid))

        resumed_meta = rm.resume_from_suspended_axis(0, op_id=uid, axis="PROGRAM", now_us=25.0)
        self.assertIsNotNone(resumed_meta)
        assert resumed_meta is not None  # type: ignore[unreachable]
        self.assertAlmostEqual(resumed_meta.start_us, 25.0, places=6)
        self.assertAlmostEqual(resumed_meta.end_us, 55.0, places=6)
        self.assertIsNone(resumed_meta.remaining_us)
        self.assertFalse(rm.is_op_suspended(uid))

        rm.move_to_suspended_axis(0, op_id=uid, now_us=35.0, axis="PROGRAM")
        suspended2 = rm.suspended_ops_program(0)[-1]
        self.assertGreater(suspended2["remaining_us"], 0.0)
        self.assertAlmostEqual(suspended2["remaining_us"], 20.0, places=6)

    def test_scheduler_op_end_skips_when_suspended(self) -> None:
        rm = _StubRM()
        addr = _StubAddrMan()
        sched = Scheduler(cfg={}, rm=rm, addrman=addr)
        sched._am_apply_on_end = types.MethodType(lambda _self, base, op_name, targets: addr.apply_pgm(None), sched)
        uid = 404
        rm.suspended.add(uid)

        payload = {
            "base": "PROGRAM_SLC",
            "op_name": "PROGRAM_SLC",
            "targets": [Address(die=0, plane=0, block=0, page=0)],
            "op_uid": uid,
        }

        sched._handle_op_end(payload)
        self.assertEqual(addr.apply_calls, 0)
        self.assertEqual(rm.completed, [])

        rm.suspended.clear()
        sched._handle_op_end(payload)
        self.assertEqual(addr.apply_calls, 1)
        self.assertEqual(rm.completed, [uid])

    def test_scheduler_records_resumed_op_end(self) -> None:
        rm = _StubRM()
        addr = _StubAddrMan()
        sched = Scheduler(cfg={}, rm=rm, addrman=addr)
        uid = 11
        target = Address(die=0, plane=0, block=1, page=3)
        # Preload expected resume metadata
        sched._resumed_op_uids.add(uid)
        sched._resume_expected_targets[uid] = [(target.die, target.plane, target.block, target.page)]

        payload = {
            "base": "PROGRAM_SLC",
            "op_name": "PROGRAM_SLC",
            "targets": [target],
            "op_uid": uid,
        }

        sched._handle_op_end(payload)
        rows = sched.drain_op_event_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["op_uid"], uid)
        self.assertEqual(row["op_id"], uid)
        self.assertTrue(row["is_resumed"])
        self.assertEqual(row["page"], target.page)
        self.assertEqual(row["block"], target.block)
        self.assertEqual(row["triggered_us"], float(sched.now_us))
        self.assertEqual(row["event"], "OP_END")
        self.assertNotIn(uid, sched._resumed_op_uids)
        self.assertEqual(int(sched.metrics.get("program_resume_page_mismatch", 0)), 0)
        self.assertEqual(rm.completed, [uid])
        # Drain clears buffer
        self.assertEqual(sched.drain_op_event_rows(), [])

    def test_scheduler_resume_mismatch_increments_metric(self) -> None:
        rm = _StubRM()
        addr = _StubAddrMan()
        sched = Scheduler(cfg={}, rm=rm, addrman=addr)
        uid = 22
        target = Address(die=0, plane=0, block=3, page=5)
        # Store mismatched expectation (page differs)
        sched._resumed_op_uids.add(uid)
        sched._resume_expected_targets[uid] = [(target.die, target.plane, target.block, target.page - 1)]

        payload = {
            "base": "PROGRAM_SLC",
            "op_name": "PROGRAM_SLC",
            "targets": [target],
            "op_uid": uid,
        }

        sched._handle_op_end(payload)
        rows = sched.drain_op_event_rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["is_resumed"])
        self.assertEqual(int(sched.metrics.get("program_resume_page_mismatch", 0)), 1)
        self.assertEqual(rows[0]["triggered_us"], float(sched.now_us))
        self.assertEqual(rows[0]["event"], "OP_END")
        self.assertEqual(rm.completed, [uid])

    def test_scheduler_records_op_start_event(self) -> None:
        rm = _StubRM()
        addr = _StubAddrMan()
        sched = Scheduler(cfg={}, rm=rm, addrman=addr)
        uid = 33
        payload = {
            "base": "PROGRAM_SLC",
            "op_name": "PROGRAM_SLC",
            "targets": [Address(die=0, plane=0, block=7, page=2)],
            "op_uid": uid,
        }

        sched._handle_op_start(payload)
        rows = sched.drain_op_event_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["event"], "OP_START")
        self.assertEqual(row["op_uid"], uid)
        self.assertFalse(row["is_resumed"])
        self.assertEqual(row["triggered_us"], float(sched.now_us))
        self.assertEqual(sched.drain_op_event_rows(), [])


if __name__ == "__main__":
    unittest.main()
