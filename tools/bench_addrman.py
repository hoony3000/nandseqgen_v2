#!/usr/bin/env python3
"""
Small benchmarking harness for AddressManager pre/post sampling paths.

Measures legacy (expand+sample) vs fast (random_*) selection on ERASE/PGM/READ.

Usage examples:
  python tools/bench_addrman.py --planes 4 --blocks 4096,16384 --pages 256,2048 --iters 100 --erase-size 64 --pgm-size 64 --read-size 128

Outputs a simple table and optional CSV.
"""
from __future__ import annotations
import argparse, time, csv, os, sys
import numpy as np
from typing import List, Tuple

# Ensure repo root (containing addrman.py) is importable when running from tools/
HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from addrman import AddressManager, TLC, GOOD, ERASE


def _make_mgr(num_planes:int, num_blocks:int, pagesize:int, seed:int, offset:int=0, dies:int=1) -> AddressManager:
    am = AddressManager(num_planes=num_planes, num_blocks=num_blocks, pagesize=pagesize, init=GOOD, offset=offset, num_dies=dies)
    am._rng = np.random.default_rng(seed)
    return am


def _prepare_initial_state(am: AddressManager, seed:int, pre_erase_ratio:float=0.35, pre_pgm_pages:int=8, mode=TLC):
    """Prepare a mixed state for reasonable candidate populations."""
    rng = np.random.default_rng(seed)
    blocks = np.arange(am.num_blocks)
    # choose erased blocks
    k = max(1, int(am.num_blocks * float(pre_erase_ratio)))
    erased = rng.choice(blocks, size=k, replace=False)
    # apply erase
    am.addrstates[erased] = ERASE
    am.addrmodes[erased] = mode
    # program some pages across erased blocks
    if pre_pgm_pages > 0:
        # allow a few steps forward per block (bounded by pagesize-1)
        step = min(pre_pgm_pages, am.pagesize - 1)
        # sample subset to program
        pgm_subset = rng.choice(erased, size=max(1, len(erased)//2), replace=False)
        am.addrstates[pgm_subset] = np.minimum(step, am.pagesize - 1)


def _clone_state(src: AddressManager, dst: AddressManager):
    dst.addrstates[:] = src.addrstates[:]
    dst.addrmodes[:]  = src.addrmodes[:]


def bench_once(num_planes:int, num_blocks:int, pagesize:int, iters:int,
               erase_size:int, pgm_size:int, read_size:int, offset:int, seed:int,
               mp_size:int|None=None, verbose: bool=False, dies:int=1) -> dict:
    # Build base manager and snapshot its initial state
    am = _make_mgr(num_planes, num_blocks, pagesize, seed=seed, offset=offset, dies=dies)
    _prepare_initial_state(am, seed=seed)

    # Snapshot baseline arrays for true resets between sections
    baseline_states = am.addrstates.copy()
    baseline_modes  = am.addrmodes.copy()

    # Helper to reset to baseline state (and RNG for fairness)
    def _reset():
        am.addrstates[:] = baseline_states
        am.addrmodes[:]  = baseline_modes
        am._rng = np.random.default_rng(seed)
        np.random.seed(seed)

    def vprint(msg: str):
        if verbose:
            print(msg, file=sys.stderr, flush=True)

    # ERASE: legacy = get+sample+set, fast = random_erase (apply)
    vprint(f"[single] ERASE (iters={iters}, size={erase_size}) …")
    t0 = time.perf_counter()
    for _ in range(iters):
        am.random_erase(size=erase_size, mode=TLC)
    t1 = time.perf_counter()
    vprint(f"[single] ERASE done: {(t1 - t0)*1000:.2f} ms")

    # reset states for program/read fairness
    _reset()

    # PGM (non-seq): legacy = get+sample (apply to match cost), fast = random_pgm
    vprint(f"[single] PGM(non-seq) (iters={iters}, size={pgm_size}) …")
    t2 = time.perf_counter()
    for _ in range(iters):
        am.random_pgm(size=pgm_size, mode=TLC, sequential=False)
    t3 = time.perf_counter()
    vprint(f"[single] PGM(non-seq) done: {(t3 - t2)*1000:.2f} ms")

    # READ (non-seq): legacy = get+sample, fast = random_read (no apply)
    vprint(f"[single] READ(non-seq) (iters={iters}, size={read_size}) …")
    t4 = time.perf_counter()
    for _ in range(iters):
        am.random_read(size=read_size, mode=TLC, offset=offset, sequential=False)
    t5 = time.perf_counter()
    vprint(f"[single] READ(non-seq) done: {(t5 - t4)*1000:.2f} ms")

    # reset for sequential comparisons
    _reset()

    # PGM (sequential)
    vprint(f"[single] PGM(seq) (iters={iters}, size={pgm_size}) …")
    t6 = time.perf_counter()
    for _ in range(iters):
        am.random_pgm(size=pgm_size, mode=TLC, sequential=True)
    t7 = time.perf_counter()
    vprint(f"[single] PGM(seq) done: {(t7 - t6)*1000:.2f} ms")

    # READ (sequential)
    _reset()
    vprint(f"[single] READ(seq) (iters={iters}, size={read_size}) …")
    t8 = time.perf_counter()
    for _ in range(iters):
        am.random_read(size=read_size, mode=TLC, offset=offset, sequential=True)
    t9 = time.perf_counter()
    vprint(f"[single] READ(seq) done: {(t9 - t8)*1000:.2f} ms")

    # Multi-plane comparisons if requested
    mp = max(0, int(mp_size or 0))
    if mp > 0:
        if mp > num_planes:
            mp = num_planes
        plane_set = list(range(mp))
        # ERASE
        _reset()
        vprint(f"[multi k={mp}] ERASE (iters={iters}, size={erase_size}) …")
        m0 = time.perf_counter()
        for _ in range(iters):
            am.random_erase(sel_plane=plane_set, size=erase_size, mode=TLC)
        m1 = time.perf_counter()
        vprint(f"[multi k={mp}] ERASE done: {(m1 - m0)*1000:.2f} ms")

        # PGM non-seq
        _reset()
        vprint(f"[multi k={mp}] PGM(non-seq) (iters={iters}, size={pgm_size}) …")
        m2 = time.perf_counter()
        for _ in range(iters):
            am.random_pgm(sel_plane=plane_set, size=pgm_size, mode=TLC, sequential=False)
        m3 = time.perf_counter()
        vprint(f"[multi k={mp}] PGM(non-seq) done: {(m3 - m2)*1000:.2f} ms")

        # PGM sequential
        _reset()
        vprint(f"[multi k={mp}] PGM(seq) (iters={iters}, size={pgm_size}) …")
        m4 = time.perf_counter()
        for _ in range(iters):
            am.random_pgm(sel_plane=plane_set, size=pgm_size, mode=TLC, sequential=True)
        m5 = time.perf_counter()
        vprint(f"[multi k={mp}] PGM(seq) done: {(m5 - m4)*1000:.2f} ms")

        # READ non-seq
        _reset()
        vprint(f"[multi k={mp}] READ(non-seq) (iters={iters}, size={read_size}) …")
        m6 = time.perf_counter()
        for _ in range(iters):
            am.random_read(sel_plane=plane_set, size=read_size, mode=TLC, offset=offset, sequential=False)
        m7 = time.perf_counter()
        vprint(f"[multi k={mp}] READ(non-seq) done: {(m7 - m6)*1000:.2f} ms")

        # READ sequential
        _reset()
        vprint(f"[multi k={mp}] READ(seq) (iters={iters}, size={read_size}) …")
        m8 = time.perf_counter()
        for _ in range(iters):
            am.random_read(sel_plane=plane_set, size=read_size, mode=TLC, offset=offset, sequential=True)
        m9 = time.perf_counter()
        vprint(f"[multi k={mp}] READ(seq) done: {(m9 - m8)*1000:.2f} ms")
    else:
        m0=m1=m2=m3=m4=m5=m6=m7=m8=m9=m10=m11=m12=m13=m14=0.0

    return {
        "planes": num_planes,
        "dies": dies,
        "blocks": num_blocks,
        "pages": pagesize,
        "iters": iters,
        "erase_size": erase_size,
        "pgm_size": pgm_size,
        "read_size": read_size,
        "mp_size": mp,
        # single-plane
        "erase_ms": (t1 - t0) * 1000.0,
        "pgm_ms":   (t3 - t2) * 1000.0,
        "read_ms":  (t5 - t4) * 1000.0,
        "pgm_seq_ms": (t7 - t6) * 1000.0,
        "read_seq_ms":(t9 - t8) * 1000.0,
        # multi-plane
        "mp_erase_ms": (m1 - m0) * 1000.0,
        "mp_pgm_ms":   (m3 - m2) * 1000.0,
        "mp_pgm_seq_ms": (m5 - m4) * 1000.0,
        "mp_read_ms":    (m7 - m6) * 1000.0,
        "mp_read_seq_ms":(m9 - m8) * 1000.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--planes", type=int, default=4)
    ap.add_argument("--blocks", type=str, default="4096,16384")
    ap.add_argument("--pages", type=str, default="256,2048")
    ap.add_argument("--dies", type=int, default=1, help="number of dies (num_blocks is per-die)")
    ap.add_argument("--iters", type=int, default=100)
    ap.add_argument("--erase-size", type=int, default=64)
    ap.add_argument("--pgm-size", type=int, default=64)
    ap.add_argument("--read-size", type=int, default=128)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--csv-out", type=str, default=None)
    ap.add_argument("--mp-size", type=int, default=0, help="multi-plane set size to benchmark (0=skip)")
    ap.add_argument("--verbose", action="store_true", help="enable verbose output")
    args = ap.parse_args()

    np.random.seed(args.seed)

    blocks_list = [int(x) for x in args.blocks.split(",") if x]
    pages_list  = [int(x) for x in args.pages.split(",") if x]

    rows: List[dict] = []
    for nb in blocks_list:
        for pg in pages_list:
            res = bench_once(
                num_planes=args.planes,
                num_blocks=nb,
                pagesize=pg,
                iters=args.iters,
                erase_size=args.erase_size,
                pgm_size=args.pgm_size,
                read_size=args.read_size,
                offset=args.offset,
                seed=args.seed,
                mp_size=args.mp_size,
                verbose=args.verbose,
                dies=args.dies,
            )
            rows.append(res)

    # Print simple table
    hdr = [
        "planes","dies","blocks","pages","iters","mp_size",
        # single-plane
        "erase_ms","pgm_ms","read_ms","pgm_seq_ms","read_seq_ms",
        # multi-plane
        "mp_erase_ms","mp_pgm_ms","mp_pgm_seq_ms","mp_read_ms","mp_read_seq_ms",
    ]
    print(",".join(hdr))
    for r in rows:
        print(",".join(str(r[k]) for k in hdr))

    # Optionally write CSV with more fields
    if args.csv_out:
        keys = sorted(rows[0].keys()) if rows else []
        with open(args.csv_out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow(r)


if __name__ == "__main__":
    main()
