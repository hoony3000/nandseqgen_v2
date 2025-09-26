from __future__ import annotations

from resourcemgr import Address, ResourceManager, Scope


class _State:
    def __init__(self, name: str, dur_us: float, *, bus: bool = False) -> None:
        self.name = name
        self.dur_us = float(dur_us)
        self.bus = bool(bus)


class _Op:
    def __init__(self, base: str, states: list[_State]) -> None:
        self.base = base
        self.states = states


def test_die_wide_operation_populates_all_planes() -> None:
    rm = ResourceManager(cfg={}, dies=1, planes=4)
    op = _Op("ERASE", [_State("ISSUE", 0.2, bus=True), _State("CORE_BUSY", 1.2)])
    txn = rm.begin(0.0)
    targets = [Address(die=0, plane=0, block=0, page=0)]

    res = rm.reserve(txn, op, targets, Scope.DIE_WIDE)
    assert res.ok
    rm.commit(txn)

    for plane in range(rm.planes):
        key = (0, plane)
        assert key in rm._st.by_plane
        segments = rm._st.by_plane[key]
        assert len(segments) == 2
        assert [seg.state for seg in segments] == ["ISSUE", "CORE_BUSY"]
        assert all(seg.op_base == "ERASE" for seg in segments)
