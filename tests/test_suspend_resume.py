from __future__ import annotations

import unittest

import types
from typing import Optional

import pytest

from addrman import AddressManager
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
            scope=Scope.PLANE_SET,
            op=op,
        )

        rm.move_to_suspended_axis(0, op_id=uid, now_us=10.0, axis="PROGRAM")
        suspended = rm.suspended_ops_program(0)[-1]
        self.assertAlmostEqual(suspended["remaining_us"], 30.0, places=6)
        self.assertEqual(suspended["states"], [("CORE_BUSY", 30.0)])
        self.assertAlmostEqual(suspended["consumed_us"], 10.0, places=6)
        self.assertEqual(rm._plane_resv[(0, 0)], [(0.0, 10.0)])
        self.assertTrue(rm.is_op_suspended(uid))

        resumed_meta = rm.resume_from_suspended_axis(0, op_id=uid, axis="PROGRAM", now_us=25.0)
        self.assertIsNotNone(resumed_meta)
        assert resumed_meta is not None  # type: ignore[unreachable]
        self.assertAlmostEqual(resumed_meta.start_us, 25.0, places=6)
        self.assertAlmostEqual(resumed_meta.end_us, 55.0, places=6)
        self.assertIsNone(resumed_meta.remaining_us)
        self.assertFalse(rm.is_op_suspended(uid))
        self.assertEqual(rm._plane_resv[(0, 0)], [(0.0, 10.0), (25.0, 55.0)])

        rm.move_to_suspended_axis(0, op_id=uid, now_us=35.0, axis="PROGRAM")
        suspended2 = rm.suspended_ops_program(0)[-1]
        self.assertGreater(suspended2["remaining_us"], 0.0)
        self.assertAlmostEqual(suspended2["remaining_us"], 20.0, places=6)
        self.assertEqual(suspended2["states"], [("CORE_BUSY", 20.0)])
        self.assertAlmostEqual(suspended2["consumed_us"], 20.0, places=6)
        self.assertEqual(rm._plane_resv[(0, 0)], [(0.0, 10.0), (25.0, 35.0)])

    def test_consume_suspended_op_ids_returns_buffered_handles(self) -> None:
        rm = ResourceManager(cfg={}, dies=1, planes=1)
        targets = [Address(die=0, plane=0, block=0, page=0)]
        uid = 333
        op = _mk_op("PROGRAM_SLC", 20.0)

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
            end_us=20.0,
            scope=Scope.PLANE_SET,
            op=op,
        )

        rm.move_to_suspended_axis(0, op_id=uid, now_us=5.0, axis="PROGRAM")
        self.assertTrue(rm.is_op_suspended(uid))

        # Incorrect axis returns nothing and leaves the entry queued
        self.assertEqual(rm.consume_suspended_op_ids("ERASE", 0), [])

        ids = rm.consume_suspended_op_ids("PROGRAM", 0)
        self.assertEqual(ids, [uid])
        # Subsequent calls drain the buffer
        self.assertEqual(rm.consume_suspended_op_ids("PROGRAM", 0), [])

    def test_die_wide_suspend_covers_all_planes(self) -> None:
        rm = ResourceManager(cfg={}, dies=1, planes=3)
        targets = [Address(die=0, plane=0, block=0, page=0)]
        uid = 515
        op = _mk_op("PROGRAM_SLC", 30.0)

        txn = rm.begin(0.0)
        res = rm.reserve(txn, op, targets, Scope.DIE_WIDE)
        self.assertTrue(res.ok)
        rm.commit(txn)
        rm.register_ongoing(
            die=0,
            op_id=uid,
            op_name="PROGRAM_SLC",
            base="PROGRAM_SLC",
            targets=targets,
            start_us=float(res.start_us or 0.0),
            end_us=float(res.end_us or 0.0),
            scope=Scope.DIE_WIDE,
            op=op,
        )

        rm.move_to_suspended_axis(0, op_id=uid, now_us=12.0, axis="PROGRAM")
        for plane in range(rm.planes):
            self.assertEqual(rm._plane_resv[(0, plane)], [(0.0, 12.0)])

        suspended = rm.suspended_ops_program(0)[-1]
        self.assertEqual(sorted(suspended["planes"]), [0, 1, 2])

        resumed_meta = rm.resume_from_suspended_axis(0, op_id=uid, axis="PROGRAM", now_us=20.0)
        self.assertIsNotNone(resumed_meta)
        for plane in range(rm.planes):
            self.assertEqual(rm._plane_resv[(0, plane)], [(0.0, 12.0), (20.0, 38.0)])

    def test_scheduler_op_end_skips_when_suspended(self) -> None:
        rm = _StubRM()
        addr = _StubAddrMan()
        sched = Scheduler(cfg={}, rm=rm, addrman=addr)
        sched._am_apply_on_end = types.MethodType(
            lambda _self, base, op_name, targets, op_uid=None: addr.apply_pgm(None),
            sched,
        )
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

    def test_resource_manager_repeated_suspend_blocks_overlap(self) -> None:
        rm = ResourceManager(cfg={}, dies=1, planes=1)
        targets = [Address(die=0, plane=0, block=0, page=0)]
        uid = 202

        def _build_program(duration: float = 40.0):
            return _mk_op("PROGRAM_SLC", duration)

        txn0 = rm.begin(0.0)
        op_initial = _build_program()
        res0 = rm.reserve(txn0, op_initial, targets, Scope.PLANE_SET)
        self.assertTrue(res0.ok)
        rm.commit(txn0)
        rm.register_ongoing(
            die=0,
            op_id=uid,
            op_name="PROGRAM_SLC",
            base="PROGRAM_SLC",
            targets=targets,
            start_us=float(res0.start_us or 0.0),
            end_us=float(res0.end_us or 0.0),
            scope=Scope.PLANE_SET,
            op=op_initial,
        )

        rm.move_to_suspended_axis(0, op_id=uid, now_us=10.0, axis="PROGRAM")
        resumed_meta = rm.resume_from_suspended_axis(0, op_id=uid, axis="PROGRAM", now_us=25.0)
        self.assertIsNotNone(resumed_meta)
        self.assertEqual(rm._plane_resv[(0, 0)], [(0.0, 10.0), (25.0, 55.0)])

        def _reserve_start_at(now_us: float) -> Optional[float]:
            txn = rm.begin(now_us)
            candidate = _build_program(duration=5.0)
            res = rm.reserve(txn, candidate, targets, Scope.PLANE_SET)
            start_val: Optional[float]
            if res.ok and res.start_us is not None:
                start_val = float(res.start_us)
            else:
                start_val = None
            rm.rollback(txn)
            return start_val if res.ok else None

        start_after_resume = _reserve_start_at(41.0)
        self.assertIsNotNone(start_after_resume)
        assert start_after_resume is not None
        self.assertGreaterEqual(
            start_after_resume,
            55.0,
            msg="resume should retain plane reservation until remaining CORE_BUSY completes",
        )

        rm.move_to_suspended_axis(0, op_id=uid, now_us=35.0, axis="PROGRAM")
        resumed_meta2 = rm.resume_from_suspended_axis(0, op_id=uid, axis="PROGRAM", now_us=60.0)
        self.assertIsNotNone(resumed_meta2)
        self.assertEqual(rm._plane_resv[(0, 0)], [(0.0, 10.0), (25.0, 35.0), (60.0, 80.0)])

        second_start = _reserve_start_at(70.0)
        self.assertIsNotNone(second_start)
        assert second_start is not None
        self.assertGreaterEqual(
            second_start,
            80.0,
            msg="repeated resume should still block overlapping PROGRAM reservations",
        )

        rm.complete_op(uid)
        final_start = _reserve_start_at(90.0)
        self.assertIsNotNone(final_start)
        assert final_start is not None
        self.assertGreaterEqual(final_start, 90.0)

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

    def test_scheduler_logs_apply_pgm_calls(self) -> None:
        cfg = {"program_base_whitelist": ["PROGRAM_SLC"]}
        rm = _StubRM()
        addr = _StubAddrMan()
        sched = Scheduler(cfg=cfg, rm=rm, addrman=addr)
        uid = 77
        payload = {
            "base": "PROGRAM_SLC",
            "op_name": "PROGRAM_SLC",
            "targets": [Address(die=0, plane=1, block=2, page=4)],
            "op_uid": uid,
        }

        sched._handle_op_end(payload)
        first_rows = sched.drain_apply_pgm_rows()
        self.assertEqual(addr.apply_calls, 1)
        self.assertEqual(len(first_rows), 1)
        first = first_rows[0]
        self.assertEqual(first["op_uid"], uid)
        self.assertEqual(first["call_seq"], 1)
        self.assertFalse(first["resume"])
        self.assertEqual(first["plane"], 1)
        self.assertEqual(first["block"], 2)
        self.assertEqual(first["page"], 4)
        self.assertEqual(first["base"], "PROGRAM_SLC")

        sched._resumed_op_uids.add(uid)
        sched._handle_op_end(payload)
        second_rows = sched.drain_apply_pgm_rows()
        self.assertEqual(addr.apply_calls, 2)
        self.assertEqual(len(second_rows), 1)
        second = second_rows[0]
        self.assertEqual(second["call_seq"], 2)
        self.assertTrue(second["resume"])
        self.assertEqual(second["op_uid"], uid)
        self.assertEqual(second["plane"], 1)
        self.assertEqual(second["block"], 2)
        self.assertEqual(second["page"], 4)
        self.assertEqual(sched.drain_apply_pgm_rows(), [])

    def test_scheduler_cancels_op_end_on_suspend_then_applies_once_on_resume(self) -> None:
        cfg = {
            "program_base_whitelist": ["PROGRAM_SLC"],
            "topology": {"dies": 1, "planes": 1, "blocks_per_die": 4, "pages_per_block": 8},
            "op_bases": {"PROGRAM_SLC": {"affect_state": True}},
        }
        addr = AddressManager.from_topology(cfg["topology"])
        rm = ResourceManager(cfg=cfg, dies=1, planes=1)
        sched = Scheduler(cfg=cfg, rm=rm, addrman=addr)

        targets = [Address(die=0, plane=0, block=0, page=0)]
        op = _mk_op("PROGRAM_SLC", 30.0)
        uid = 5150
        rec_program = {
            "base": "PROGRAM_SLC",
            "op_name": "PROGRAM_SLC",
            "targets": targets,
            "op": op,
            "start_us": 0.0,
            "end_us": 30.0,
            "scope": Scope.PLANE_SET,
            "op_uid": uid,
        }

        baseline_state = int(addr.addrstates[0])
        sched._emit_op_events(rec_program)
        rm.register_ongoing(
            die=0,
            op_id=uid,
            op_name="PROGRAM_SLC",
            base="PROGRAM_SLC",
            targets=targets,
            start_us=0.0,
            end_us=30.0,
            scope=Scope.PLANE_SET,
            op=op,
        )

        def _queue_contains_op_end(op_uid: int) -> bool:
            return any(
                kind == "OP_END" and payload.get("op_uid") == op_uid
                for (_, _, _, kind, payload) in sched._eq._q
            )

        self.assertTrue(_queue_contains_op_end(uid))

        rm.move_to_suspended_axis(0, op_id=uid, now_us=10.0, axis="PROGRAM")
        suspended_ids = rm.consume_suspended_op_ids("PROGRAM", 0)
        self.assertEqual(suspended_ids, [uid])
        self.assertTrue(sched._cancel_op_end(uid))
        self.assertEqual(int(sched.metrics["suspended_op_end_cancelled"]), 1)
        self.assertFalse(_queue_contains_op_end(uid))
        self.assertNotIn(uid, sched._op_end_handles)

        resume_rec = {
            "base": "PROGRAM_RESUME",
            "op_name": "PROGRAM_RESUME",
            "targets": targets,
            "end_us": 40.0,
        }
        sched._handle_resume_commit(resume_rec)
        self.assertIn(uid, sched._op_end_handles)

        pending_entry = None
        for entry in list(sched._eq._q):
            if entry[3] == "OP_END" and entry[4].get("op_uid") == uid:
                pending_entry = entry
                break
        self.assertIsNotNone(pending_entry)
        assert pending_entry is not None
        when, _prio, seq, _kind, payload = pending_entry

        self.assertTrue(sched._eq.remove(seq, kind="OP_END"))
        sched._advance_time_to(float(when))
        before_resume_state = int(addr.addrstates[0])
        sched._handle_op_end(payload)
        after_state = int(addr.addrstates[0])
        self.assertEqual(after_state - before_resume_state, 1)
        self.assertNotIn(uid, sched._op_end_handles)
        self.assertEqual(int(addr.addrstates[0]) - baseline_state, 1)

    def test_suspend_slices_states_and_bus_segments(self) -> None:
        rm = ResourceManager(cfg={}, dies=1, planes=1)
        targets = [Address(die=0, plane=0, block=1, page=0)]
        uid = 303

        class _State:
            def __init__(self, name: str, dur: float, bus: bool) -> None:
                self.name = name
                self.dur_us = float(dur)
                self.bus = bus

        class _Op:
            def __init__(self) -> None:
                self.base = "PROGRAM_SLC"
                self.states = [
                    _State("ISSUE", 1.0, True),
                    _State("CORE_BUSY", 9.0, False),
                    _State("DATA_OUT", 2.0, True),
                ]

        op = _Op()
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
            start_us=float(res.start_us or 0.0),
            end_us=float(res.end_us or 0.0),
            scope=Scope.PLANE_SET,
            op=op,
        )

        rm.move_to_suspended_axis(0, op_id=uid, now_us=5.0, axis="PROGRAM")
        suspended = rm.suspended_ops_program(0)[-1]
        self.assertEqual(suspended["states"], [("CORE_BUSY", 5.0), ("DATA_OUT", 2.0)])
        self.assertEqual(suspended["bus_segments"], [(5.0, 7.0)])
        self.assertEqual(rm._plane_resv[(0, 0)], [(0.0, 5.0)])

    def test_resume_failure_restores_meta_and_sets_error(self) -> None:
        rm = ResourceManager(cfg={}, dies=1, planes=1)
        targets = [Address(die=0, plane=0, block=2, page=1)]
        uid = 909
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
            start_us=float(res.start_us or 0.0),
            end_us=float(res.end_us or 0.0),
            scope=Scope.PLANE_SET,
            op=op,
        )

        rm.move_to_suspended_axis(0, op_id=uid, now_us=10.0, axis="PROGRAM")

        blocker = _mk_op("PROGRAM_SLC", 30.0)
        txn_block = rm.begin(25.0)
        res_block = rm.reserve(txn_block, blocker, targets, Scope.PLANE_SET)
        self.assertTrue(res_block.ok)
        rm.commit(txn_block)

        resumed = rm.resume_from_suspended_axis(0, op_id=uid, axis="PROGRAM", now_us=25.0)
        self.assertIsNone(resumed)
        err = rm.last_resume_error()
        self.assertIsInstance(err, dict)
        assert err is not None
        self.assertEqual(err.get("reason"), "planescope")
        self.assertEqual(err.get("axis"), "PROGRAM")
        self.assertEqual(len(rm.suspended_ops_program(0)), 1)
        self.assertEqual(rm._plane_resv[(0, 0)], [(0.0, 10.0), (25.0, 55.0)])


if __name__ == "__main__":
    unittest.main()
