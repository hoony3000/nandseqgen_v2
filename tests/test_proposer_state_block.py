import random

import proposer
from proposer import (
    AttemptRecord,
    ProposeDiagnostics,
    ProposeResult,
    StateBlockInfo,
)
from scheduler import Scheduler


class StubResourceView:
    def op_state(self, die, plane, at_us):
        return None

    def odt_state(self):
        return None

    def program_suspend_state(self, die, at_us):
        return "PROGRAM_SUSPENDED"

    def erase_suspend_state(self, die, at_us):
        return None

    def cache_state(self, die, plane, at_us):
        return None

    def feasible_at(self, op, targets, start_hint, scope):
        return float(start_hint)


class StubAddressSampler:
    def sample_erase(self, sel_plane=None, mode="TLC", size=1, sel_die=None):
        return []

    def sample_pgm(self, sel_plane=None, mode="TLC", size=1, sequential=False, sel_die=None):
        return []

    def sample_read(self, sel_plane=None, mode="TLC", size=1, offset=None, sequential=False, sel_die=None):
        return []


class RecorderLogger:
    def __init__(self):
        self.records = []

    def debug(self, msg, *args):
        if args:
            msg = msg % args
        self.records.append(("debug", msg))


class DummyResourceManager:
    pass


class DummyAddressManager:
    pass


def _minimal_cfg() -> dict:
    return {
        "topology": {"dies": 1, "planes": 1},
        "policies": {
            "admission_window": 1.0,
            "topN": 1,
            "epsilon_greedy": 0.0,
            "maxtry_candidate": 1,
        },
        "phase_conditional": {"DEFAULT": {"CACHE_PROGRAM_SLC": 1.0}},
        "op_names": {
            "CACHE_PROGRAM_SLC": {
                "base": "CACHE_PROGRAM_SLC",
                "durations": {
                    "ISSUE": 0.4,
                    "CORE_BUSY": 1.0,
                },
            }
        },
        "op_bases": {
            "CACHE_PROGRAM_SLC": {
                "scope": "DIE_WIDE",
                "affect_state": True,
                "instant_resv": False,
                "states": [
                    {"ISSUE": {"bus": True}},
                    {"CORE_BUSY": {"bus": False}},
                ],
            }
        },
        "exclusion_groups": {
            "program_suspended": ["CACHE_PROGRAM_SLC"],
        },
        "exclusions_by_suspend_state": {
            "PROGRAM_SUSPENDED": ["program_suspended"],
        },
    }


def test_propose_state_block_diagnostics():
    cfg = _minimal_cfg()
    res_view = StubResourceView()
    addr_sampler = StubAddressSampler()
    rng = random.Random(0)
    hook = {"label": "DEFAULT", "die": 0, "plane": 0}

    result = proposer.propose(0.0, hook, cfg, res_view, addr_sampler, rng)

    assert isinstance(result, ProposeResult)
    assert result.batch is None

    diagnostics = result.diagnostics
    assert diagnostics.last_state_block is not None
    assert diagnostics.last_state_block.axis == "PROGRAM"
    assert diagnostics.last_state_block.state == "PROGRAM_SUSPENDED"
    assert diagnostics.last_state_block.groups == ("program_suspended",)

    attempts = diagnostics.attempts
    assert attempts
    assert attempts[0].reason == "state_block"
    assert attempts[0].details is not None
    assert attempts[0].details["groups"] == ["program_suspended"]

    attempts_dict = diagnostics.attempts_as_dict()
    assert attempts_dict[0]["details"]["state"] == "PROGRAM_SUSPENDED"

    diag_dict = diagnostics.to_dict()
    assert diag_dict["last_state_block"]["axis"] == "PROGRAM"


def test_scheduler_records_state_block_details(monkeypatch):
    state_block = StateBlockInfo(
        axis="PROGRAM",
        state="PROGRAM_SUSPENDED",
        groups=("program_suspended",),
        base="CACHE_PROGRAM_SLC",
        die=0,
        plane=None,
    )
    attempt = AttemptRecord(
        name="CACHE_PROGRAM_SLC",
        prob=1.0,
        reason="state_block",
        details=state_block.as_dict(),
    )
    diagnostics = ProposeDiagnostics(
        attempts=(attempt,),
        last_state_block=state_block,
    )
    monkeypatch.setattr(
        proposer,
        "propose",
        lambda *args, **kwargs: ProposeResult(batch=None, diagnostics=diagnostics),
    )

    cfg = _minimal_cfg()
    logger = RecorderLogger()
    sched = Scheduler(
        cfg,
        rm=DummyResourceManager(),
        addrman=DummyAddressManager(),
        logger=logger,
    )

    committed, rolled_back, reason = sched._propose_and_schedule(
        0.0, {"label": "DEFAULT", "die": 0, "plane": 0}
    )

    assert committed == 0
    assert rolled_back is False
    assert reason == "state_block:PROGRAM:PROGRAM_SUSPENDED"
    assert sched.metrics["last_reason"] == reason

    details = sched.metrics.get("last_state_block_details")
    assert details is not None
    assert details["axis"] == "PROGRAM"
    assert details["groups"] == ["program_suspended"]

    attempts_metric = sched.metrics.get("last_propose_attempts")
    assert attempts_metric
    assert attempts_metric[0]["reason"] == "state_block"

    assert logger.records
    level, message = logger.records[-1]
    assert level == "debug"
    assert "state_block" in message
    assert "PROGRAM" in message
