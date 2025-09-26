from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import check_op_state_overlaps as overlaps  # type: ignore


def test_check_rows_passes_when_intervals_touch() -> None:
    rows = [
        {"die": "0", "plane": "0", "op_state": "READ.CORE_BUSY", "start": "0.0", "end": "5.0"},
        {"die": "0", "plane": "0", "op_state": "READ.CORE_BUSY", "start": "5.0", "end": "10.0"},
        {"die": "0", "plane": "1", "op_state": "READ.CORE_BUSY", "start": "0.0", "end": "3.0"},
        {"die": "0", "plane": "1", "op_state": "READ.DATA_OUT", "start": "2.0", "end": "4.0"},
    ]
    errors = overlaps.check_rows(rows)
    assert errors == []


def test_check_rows_detects_overlap() -> None:
    rows = [
        {"die": "0", "plane": "0", "op_state": "PROGRAM.CORE_BUSY", "start": "0.0", "end": "10.0"},
        {"die": "0", "plane": "0", "op_state": "PROGRAM.CORE_BUSY", "start": "9.5", "end": "15.0"},
    ]
    errors = overlaps.check_rows(rows)
    assert len(errors) == 1
    err = errors[0]
    assert err.key == (0, 0, "PROGRAM.CORE_BUSY")
    assert err.prev_segment.start == 0.0
    assert err.prev_segment.end == 10.0
    assert err.curr_segment.start == 9.5


def test_check_paths_reports_no_overlap(tmp_path) -> None:
    path = tmp_path / "timeline.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("start,end,die,plane,op_state\n")
        handle.write("0.0,5.0,0,0,READ.CORE_BUSY\n")
        handle.write("5.0,10.0,0,0,READ.CORE_BUSY\n")
    errors = overlaps.check_paths([str(path)])
    assert errors == []


def test_check_paths_detects_overlap(tmp_path) -> None:
    path = tmp_path / "timeline_bad.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("start,end,die,plane,op_state\n")
        handle.write("0.0,5.0,0,0,READ.CORE_BUSY\n")
        handle.write("4.9,9.0,0,0,READ.CORE_BUSY\n")
    errors = overlaps.check_paths([str(path)])
    assert errors
    err = errors[0]
    assert err.file and err.file.endswith("timeline_bad.csv")
