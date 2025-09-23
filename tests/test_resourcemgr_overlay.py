from __future__ import annotations

import unittest

from resourcemgr import Address, ResourceManager, Scope


class _State:
    def __init__(self, name: str, dur_us: float) -> None:
        self.name = name
        self.dur_us = float(dur_us)
        self.bus = False


class _Op:
    def __init__(self, base: str, dur_us: float = 10.0) -> None:
        self.base = base
        self.states = [_State("CORE_BUSY", dur_us)]


class ResourceManagerOverlayTests(unittest.TestCase):
    def test_overlay_respects_configured_program_whitelist(self) -> None:
        cfg = {"program_base_whitelist": ["PROGRAM_SLC"]}
        rm = ResourceManager(cfg=cfg, dies=1, planes=1)

        txn1 = rm.begin(0.0)
        targets1 = [Address(die=0, plane=0, block=0, page=5)]
        res1 = rm.reserve(txn1, _Op("PROGRAM_SLC"), targets1, Scope.PLANE_SET)
        self.assertTrue(res1.ok)
        key1 = (0, 0)
        self.assertIn(key1, txn1.addr_overlay)
        self.assertEqual(txn1.addr_overlay[key1]["addr_state"], 5)
        rm.commit(txn1)

        txn2 = rm.begin(res1.end_us or 0.0)
        targets2 = [Address(die=0, plane=0, block=1, page=7)]
        res2 = rm.reserve(txn2, _Op("CACHE_PROGRAM_SLC"), targets2, Scope.PLANE_SET)
        self.assertTrue(res2.ok)
        key2 = (0, 1)
        self.assertNotIn(key2, txn2.addr_overlay)

    def test_overlay_uses_config_whitelist_entries(self) -> None:
        cfg = {"program_base_whitelist": ["ONESHOT_PROGRAM_EXEC_MSB"]}
        rm = ResourceManager(cfg=cfg, dies=1, planes=1)
        txn = rm.begin(0.0)
        targets = [Address(die=0, plane=0, block=2, page=3)]
        op = _Op("ONESHOT_PROGRAM_EXEC_MSB")
        res = rm.reserve(txn, op, targets, Scope.PLANE_SET)
        self.assertTrue(res.ok)
        key = (0, 2)
        self.assertIn(key, txn.addr_overlay)
        self.assertEqual(txn.addr_overlay[key]["addr_state"], 3)


if __name__ == "__main__":
    unittest.main()
