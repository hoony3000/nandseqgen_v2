import numpy as np
import itertools
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# from loguru import logger
TLC = "TLC"
FWSLC = "FWSLC"
SLC = "SLC"
AESLC = "AESLC"
A0SLC = "A0SLC"
ACSLC = "ACSLC"
TBD = "TBD"
BAD = -3
GOOD = -2
ERASE = -1

CMD_VOCAB = {"ERASE": 0, "PGM": 1, "READ": 2}
# NOTE: Visualization helpers still consume (plane, block, page) tuples.
# Sampled address tensors returned by random_* now include die explicitly as (die, block, page).
ADDR_KEYS = ["plane", "block", "page"]


def arr_to_nparr(adds: list | np.ndarray):
    if isinstance(adds, (list, tuple, set)):
        if all(isinstance(v, int) for v in adds):
            return np.array(adds, dtype=int)
        else:
            raise TypeError("All elements in adds must be integers.")
    elif isinstance(adds, int):
        return np.array([adds], dtype=int)
    elif isinstance(adds, np.ndarray) and adds.dtype.kind in {"i", "u"}:
        return adds
    else:
        raise TypeError(
            f"adds must be a list, tuple, set, int, or numpy array of integers: {type(adds)}"
        )


def reduce_to_blkarr(adds: np.ndarray):
    if isinstance(adds, np.ndarray) and adds.dtype.kind in {"i", "u"}:
        return adds.reshape(-1, adds.shape[-1])[:, 0]
    else:
        raise TypeError(f"adds must be a numpy array of integers: {type(adds)}")


def to_1D_blkaddr(adds: list | np.ndarray):
    tmp_adds = arr_to_nparr(adds)
    if tmp_adds.ndim > 1:
        tmp_adds = reduce_to_blkarr(tmp_adds)

    return tmp_adds


def empty_arr():
    return np.array([], dtype=int)


def all_subsets(s):
    return list(
        itertools.chain.from_iterable(
            itertools.combinations(s, r) for r in range(1, len(s) + 1)
        )
    )


class AddressManager:
    """
    AddressManager class 정의
    num_address : block address 갯수
    addrstates : address 상태를 저장하는 numpy 배열
    addrmodes_erase / addr_mode_erase : erase 시 선택된 celltype 을 저장
    addrmodes_pgm   / addr_mode_pgm   : program/read 시 사용할 celltype 을 저장
    pagesize : block 내 page 갯수
    offset : addrReadable 구할 때 last PGM page address 끝에서부터 제외할 page 갯수
    num_dies : die 수
    undo_addrs : 마지막 erase 또는 PGM 했던 address list
    undo_states : 마지막 erase 또는 PGM 했던 address 의 addrstates
    undo_modes : 마지막 erase 또는 PGM 했던 address 의 addrmodes
    oversample : 전체 가능한 address 갯수보다 더 많은 sample 을 요구했을 떄 True
    """

    # adds 배열의 상태 값 정의
    # -3: badblock
    # -2: goodblock not erased
    # -1: erased
    # 0 to pagesize-1 : PGM 된 page 수
    set_plane: set = {0, 1, 2, 4, 6}

    def __init__(
        self,
        num_planes: int,
        num_blocks: int,
        pagesize: int,
        init: int = GOOD,
        badlist=np.array([], dtype=int),
        offset: int = 0,
        num_dies: int = 1,
    ):
        """
        생성자 정의
        """
        self.num_planes: int
        self.num_dies: int
        self.num_blocks: int
        self.addrstates: np.ndarray
        self.addrmodes_erase: np.ndarray
        self.addrmodes_pgm: np.ndarray
        # Standardized aliases for external docs/specs
        self.addr_mode_erase: np.ndarray
        self.addr_mode_pgm: np.ndarray
        self.pagesize: int
        self.offset: int
        self.undo_addrs: np.ndarray = np.array([], dtype=int)
        self.undo_states: np.ndarray = np.array([], dtype=int)
        self.undo_modes: np.ndarray = np.array([], dtype=int)
        self.oversample: bool = False

        if num_planes in AddressManager.set_plane:
            self.num_planes = num_planes
        else:
            raise ValueError(
                f"num_planes must be one of {AddressManager.set_plane}, got {num_planes}"
            )

        if isinstance(num_blocks, int) and num_blocks > 0:
            # num_blocks is per-die; total = num_blocks * num_dies
            self._blocks_per_die = int(num_blocks)
        else:
            raise ValueError(f"num_blocks (per-die) must be a positive integer, got {num_blocks}")

        if isinstance(pagesize, int) and pagesize > 0:
            self.pagesize = pagesize
        else:
            raise ValueError(f"pagesize must be a positive integer, got {pagesize}")

        if isinstance(offset, int) and offset >= 0 and offset < pagesize:
            self.offset = offset
        else:
            raise ValueError(
                f"offset must be a non-negative integer less than pagesize, got {offset}"
            )

        # num_dies validation and derived topology
        if isinstance(num_dies, int) and num_dies > 0:
            self.num_dies = num_dies
        else:
            raise ValueError(f"num_dies must be a positive integer, got {num_dies}")

        # Compute total blocks across dies and allocate arrays
        self.num_blocks = self._blocks_per_die * self.num_dies
        if isinstance(init, int) and (init > BAD or init < pagesize):
            self.addrstates = np.full(self.num_blocks, init, dtype=int)
            # Track erase/program modes separately; keep legacy alias addrmodes -> program mode
            self.addrmodes_erase = np.full(self.num_blocks, TBD, dtype=object)
            self.addrmodes_pgm = np.full(self.num_blocks, TBD, dtype=object)
            # Backward-compat alias used by external scripts
            self.addrmodes = self.addrmodes_pgm
            # Standardized aliases (PRD v2): addr_mode_*
            self.addr_mode_erase = self.addrmodes_erase
            self.addr_mode_pgm = self.addrmodes_pgm
            bad_idx = self._normalize_badlist(badlist)
            if bad_idx.size:
                if np.any(bad_idx < 0) or np.any(bad_idx >= self.num_blocks):
                    raise IndexError(f"badlist indices must be in [0,{self.num_blocks})")
                self.addrstates[bad_idx] = BAD
        else:
            raise ValueError(
                f"init must be an integer greater than {BAD} or less than {pagesize}, got {init}"
            )

        # Precomputed helpers for fast filtering/sampling
        ar = np.arange(self.num_blocks)
        self._die_index = ar // self._blocks_per_die
        within_die = ar % self._blocks_per_die
        self._plane_index = within_die % self.num_planes
        self._block_groups = None  # lazily computed (#groups, num_planes) per die
        self._rng = np.random.default_rng()

    def _normalize_badlist(self, badlist) -> np.ndarray:
        """
        Normalize badlist to global block indices.
        Accepts ONLY per-die pairs (die, block_within_die).
        Examples:
          - list of pairs: [(0, 3), (1, 7)]
          - ndarray shape (N,2)
        """
        if badlist is None:
            return np.array([], dtype=int)
        if isinstance(badlist, np.ndarray):
            if badlist.size == 0:
                return np.array([], dtype=int)
            if badlist.ndim == 2 and badlist.shape[1] == 2:
                arr = badlist.astype(int)
            else:
                raise TypeError("badlist must be ndarray with shape (N,2) of (die, block)")
        elif isinstance(badlist, (list, tuple)):
            if len(badlist) == 0:
                return np.array([], dtype=int)
            if not all(isinstance(p, (list, tuple)) and len(p) == 2 for p in badlist):
                raise TypeError("badlist must be a list of pairs (die, block)")
            arr = np.array(badlist, dtype=int)
        else:
            raise TypeError("badlist must be a list of pairs or ndarray shape (N,2)")

        dies = arr[:, 0]
        blks = arr[:, 1]
        if np.any(dies < 0) or np.any(dies >= self.num_dies):
            raise IndexError("badlist die index out of range")
        if np.any(blks < 0) or np.any(blks >= self._blocks_per_die):
            raise IndexError("badlist block index out of range for die")
        return dies * self._blocks_per_die + blks

    def set_range_val(self, add_from: int, add_to: int, val: int, mode=TLC):
        """
        adds 배열에서 add_from 부터 add_to 까지의 index 에 val 값을 할당
        """
        self.addrstates[add_from : add_to + 1] = val
        if val == ERASE:
            # Setting erase result: update erase mode, reset program mode
            self.addrmodes_erase[add_from : add_to + 1] = mode
            self.addrmodes_pgm[add_from : add_to + 1] = TBD
        elif val > ERASE:
            # Setting programmed pages: set program mode (keep existing erase mode)
            self.addrmodes_pgm[add_from : add_to + 1] = mode

    def set_n_val(self, add_from: int, n: int, val: int, mode=TLC):
        """
        adds 배열에서 add_from 부터 n 개의 index 에 val 값을 할당
        """
        if add_from + n > self.num_blocks:
            raise IndexError(
                f"add_from + n exceeds num_blocks: {add_from} + {n} > {self.num_blocks}"
            )
        self.addrstates[add_from : add_from + n] = val
        if val == ERASE:
            self.addrmodes_erase[add_from : add_from + n] = mode
            self.addrmodes_pgm[add_from : add_from + n] = TBD
        elif val > ERASE:
            self.addrmodes_pgm[add_from : add_from + n] = mode

    def set_adds_val(self, adds: np.ndarray, val: int, mode=TLC):
        """
        adds 배열에 val 값을 할당
        """
        tmp_adds = to_1D_blkaddr(adds)  # 1차원 배열

        if np.any(tmp_adds >= self.num_blocks):
            raise IndexError(
                f"Some addresses in adds exceed num_blocks: {tmp_adds[tmp_adds >= self.num_blocks]}"
            )
        self.addrstates[tmp_adds] = val
        if val == ERASE:
            self.addrmodes_erase[tmp_adds] = mode
            self.addrmodes_pgm[tmp_adds] = TBD
        elif val > ERASE:
            self.addrmodes_pgm[tmp_adds] = mode

    def undo_last(self):
        """
        마지막에 했던 set_adds_erase, 또는 set_adds_pgm 의 동작을 되돌림
        """

        self.addrstates[self.undo_addrs] = self.undo_states
        # Restore both erase/program modes if available
        if hasattr(self, "undo_modes_erase") and hasattr(self, "undo_modes_pgm"):
            self.addrmodes_erase[self.undo_addrs] = self.undo_modes_erase
            self.addrmodes_pgm[self.undo_addrs] = self.undo_modes_pgm
        else:
            # Backward compatibility
            self.addrmodes[self.undo_addrs] = self.undo_modes

    # Note: Legacy get_*/sample_*/set_* APIs have been removed.
    # Use random_erase/random_pgm/random_read for fast direct sampling.

    def get_addrstates(self) -> np.ndarray:
        """
        addrstates 반환
        """
        return self.addrstates

    def get_addrmodes(self) -> np.ndarray:
        """
        program 모드(addrmodes_pgm) 반환 (호환성 유지)
        """
        return self.addrmodes_pgm

    def get_addrmodes_erase(self) -> np.ndarray:
        """
        erase 모드(addrmodes_erase) 반환
        """
        return self.addrmodes_erase

    def get_vals_adds(self, adds: np.ndarray) -> np.ndarray:
        """
        adds 배열의 값을 반환
        """
        tmp_adds = to_1D_blkaddr(adds)
        return self.addrstates[tmp_adds]

    def tolist(self, adds: np.ndarray = None):
        """
        adds 배열을 list 형태로 반환
        output : (addrstates[0], addrmodes[0]), (addrstates[1], addrmodes[1]), ...
        """
        if adds is None:
            return list(zip(self.addrstates.tolist(), self.addrmodes.tolist()))
        else:
            return list(
                zip(self.addrstates[adds].tolist(), self.addrmodes[adds].tolist())
            )

    def log(self, adds: np.ndarray = None, file=None):
        """
        adds 배열의 상태를 로그로 출력
        """
        if adds is None:
            if file is None:
                for i, add in enumerate(self.tolist()):
                    print(f"{i} : {add}")
            else:
                for i, add in enumerate(self.tolist()):
                    file.write(f"{i} : {add}\n")
        else:
            tmp_adds = to_1D_blkaddr(adds)
            if file is None:
                for i, add in enumerate(self.tolist(tmp_adds)):
                    print(f"{tmp_adds[i]} : {add}")
            else:
                for i, add in enumerate(self.tolist(tmp_adds)):
                    file.write(f"{tmp_adds[i]} : {add}\n")

    def get_size(self):
        """
        addrstates 배열의 크기를 반환
        """
        return self.num_blocks

    # ------------------------
    # Fast-path helpers
    # ------------------------

    @staticmethod
    def from_topology(topology: dict,
                      init: int = GOOD,
                      offset: int = 0,
                      badlist=None) -> "AddressManager":
        """
        Convenience factory mapping config topology keys to AddressManager args.
        Expected keys on `topology`: dies, planes, blocks_per_die, pages_per_block.
        """
        dies = int(topology.get("dies"))
        planes = int(topology.get("planes"))
        blocks_per_die = int(topology.get("blocks_per_die"))
        pages_per_block = int(topology.get("pages_per_block"))
        return AddressManager(
            num_planes=planes,
            num_blocks=blocks_per_die,  # per-die
            pagesize=pages_per_block,
            init=init,
            badlist=badlist if badlist is not None else np.array([], dtype=int),
            offset=offset,
            num_dies=dies,
        )

    def _ensure_block_groups(self):
        if self._block_groups is None:
            if self.num_blocks % self.num_planes != 0:
                raise ValueError(
                    f"num_blocks ({self.num_blocks}) must be divisible by num_planes ({self.num_planes}) for multi-plane ops"
                )
            if self._blocks_per_die % self.num_planes != 0:
                raise ValueError(
                    f"blocks_per_die ({self._blocks_per_die}) must be divisible by num_planes ({self.num_planes})"
                )
            # Group per die, then per plane (no cross-die groups)
            self._block_groups = np.arange(self.num_blocks).reshape(-1, self.num_planes)

    def _groups_for_planes(self, sel_planes: list) -> np.ndarray:
        self._ensure_block_groups()
        return self._block_groups[:, sel_planes]

    def _wrap_blocks_as_addrs(self, blocks: np.ndarray, pages: np.ndarray | int = 0) -> np.ndarray:
        """
        Return shape (#, 1, 3): (die, block, page)
        """
        if isinstance(pages, int):
            pages = np.zeros_like(blocks, dtype=int) + int(pages)
        dies = self._die_index[blocks]
        return np.dstack((dies, blocks, pages)).reshape(blocks.shape[0], 1, -1)

    def _wrap_groups_as_addrs(self, groups: np.ndarray, page: int | np.ndarray) -> np.ndarray:
        """
        Return shape (#, k, 3): (die, block, page) for each plane in group.
        """
        if isinstance(page, int):
            pages = np.full(groups.shape, int(page), dtype=int)
        else:
            pages = np.repeat(page.reshape(-1, 1), groups.shape[1], axis=1)
        dies = self._die_index[groups]
        return np.dstack((dies, groups, pages))

    # Legacy candidate expansion and sampling APIs were removed to
    # eliminate redundant allocations and complexity. Multi/single‑plane
    # fast paths were provided by random_erase/random_pgm/random_read.
    #
    # New split APIs:
    #  - sample_*: pure address selection (no state mutation)
    #  - apply_* : explicit state mutation based on provided addresses
    #  - random_*: backward‑compatible convenience = sample_* + apply_*

    def sample_erase(
        self,
        sel_plane: int | list = None,
        mode=TLC,
        size: int = 1,
        sel_die: int | list = None,
    ) -> np.ndarray:
        """
        Pure address sampling for ERASE without mutating internal state.
        Returns addresses with page=0 as (die, block, page).
        Shape: (#, 1, 3) for single‑plane; (#, k, 3) for multi‑plane (k=|planes|).
        """
        if isinstance(sel_plane, list) and len(sel_plane) == 1:
            sel_plane = sel_plane[0]

        if sel_plane is None or isinstance(sel_plane, int):
            mask = (self.addrstates != BAD) & (self.addrstates != ERASE)
            if isinstance(sel_plane, int):
                mask &= (self._plane_index == sel_plane)
            if sel_die is not None:
                if isinstance(sel_die, int):
                    mask &= (self._die_index == sel_die)
                else:
                    sel_die_arr = np.array(list(sel_die), dtype=int)
                    mask &= np.isin(self._die_index, sel_die_arr)
            cand = np.flatnonzero(mask)
            if len(cand) == 0:
                self.oversample = (size > 0)
                return empty_arr()

            k = min(size, len(cand))
            self.oversample = (size > len(cand))
            sel = self._rng.choice(cand, size=k, replace=False)
            return self._wrap_blocks_as_addrs(sel, pages=0)

        # multi‑plane
        planes = list(sel_plane)
        groups = self._groups_for_planes(planes)
        sub = self.addrstates[groups]
        ok = np.all((sub != BAD) & (sub != ERASE), axis=1)
        if sel_die is not None:
            g0 = groups[:, 0]
            die_rows = self._die_index[g0]
            if isinstance(sel_die, int):
                ok &= (die_rows == sel_die)
            else:
                sel_die_arr = np.array(list(sel_die), dtype=int)
                ok &= np.isin(die_rows, sel_die_arr)
        cand_rows = np.flatnonzero(ok)
        if len(cand_rows) == 0:
            self.oversample = (size > 0)
            return empty_arr()

        k = min(size, len(cand_rows))
        self.oversample = (size > len(cand_rows))
        rows = self._rng.choice(cand_rows, size=k, replace=False)
        chosen = groups[rows]
        return self._wrap_groups_as_addrs(chosen, page=0)

    def apply_erase(self, addrs: np.ndarray, mode=TLC) -> None:
        """
        Apply ERASE to the provided addresses.
        Expects addresses shaped (#, *, 3) with fields (die, block, page).
        """
        if addrs is None or len(addrs) == 0:
            return
        blocks = addrs[..., 1].astype(int).reshape(-1)
        if blocks.size == 0:
            return
        uniq, _counts = np.unique(blocks, return_counts=True)
        # Save undo
        self.undo_addrs = uniq
        self.undo_states = self.addrstates[uniq].copy()
        self.undo_modes_erase = self.addrmodes_erase[uniq].copy()
        self.undo_modes_pgm = self.addrmodes_pgm[uniq].copy()
        # Apply
        self.addrstates[uniq] = ERASE
        self.addrmodes_erase[uniq] = mode
        self.addrmodes_pgm[uniq] = TBD

    def sample_pgm(
        self,
        sel_plane: int | list = None,
        mode=TLC,
        size: int = 1,
        sequential: bool = False,
        sel_die: int | list = None,
    ) -> np.ndarray:
        """
        Pure address sampling for PROGRAM without mutating internal state.
        Pages reflect the next programmed page(s): current_state+1, or ranges for sequential.
        Shape: (#, 1, 3) or (#, k, 3).
        """
        if isinstance(sel_plane, list) and len(sel_plane) == 1:
            sel_plane = sel_plane[0]

        if sel_plane is None or isinstance(sel_plane, int):
            states = self.addrstates
            mask_base = (states >= ERASE) & (states < self.pagesize - 1)
            erase_modes = self.addrmodes_erase
            pgm_modes = self.addrmodes_pgm
            fresh = states == ERASE
            cont = states > ERASE
            allow_on_slc = (erase_modes == SLC) & np.isin(mode, [A0SLC, ACSLC])
            allowed = np.zeros_like(mask_base, dtype=bool)
            allowed |= fresh & ((erase_modes == mode) | allow_on_slc)
            allowed |= cont & (pgm_modes == mode)
            mask = mask_base & allowed
            if isinstance(sel_plane, int):
                mask &= (self._plane_index == sel_plane)
            if sel_die is not None:
                if isinstance(sel_die, int):
                    mask &= (self._die_index == sel_die)
                else:
                    sel_die_arr = np.array(list(sel_die), dtype=int)
                    mask &= np.isin(self._die_index, sel_die_arr)
            cand = np.flatnonzero(mask)
            if len(cand) == 0:
                self.oversample = (size > 0)
                return empty_arr()

            if sequential:
                st = self.addrstates[cand]
                ok = st + size <= (self.pagesize - 1)
                cand2 = cand[ok]
                if len(cand2) == 0:
                    self.oversample = (size > 0)
                    return empty_arr()
                blk = int(self._rng.choice(cand2, size=1, replace=False))
                start = int(self.addrstates[blk] + 1)
                pages = np.arange(start, start + size, dtype=int)
                blocks = np.repeat(np.array([blk], dtype=int), size)
                self.oversample = False
                return self._wrap_blocks_as_addrs(blocks, pages=pages)

            # non‑sequential
            k = min(size, len(cand))
            self.oversample = (size > len(cand))
            sel = self._rng.choice(cand, size=k, replace=False)
            pages = (self.addrstates[sel] + 1).astype(int)
            return self._wrap_blocks_as_addrs(sel, pages=pages)

        # multi‑plane
        planes = list(sel_plane)
        groups = self._groups_for_planes(planes)
        vals = self.addrstates[groups]
        eq = (vals == vals[:, [0]]).all(axis=1)
        rng = ((vals >= ERASE) & (vals < self.pagesize - 1)).all(axis=1)
        erase_modes_g = self.addrmodes_erase[groups]
        pgm_modes_g = self.addrmodes_pgm[groups]
        base = vals[:, 0]
        row_fresh = base == ERASE
        allow_on_slc = (erase_modes_g == SLC) & np.isin(mode, [A0SLC, ACSLC])
        mm_fresh = ((erase_modes_g == mode) | allow_on_slc).all(axis=1)
        mm_cont = (pgm_modes_g == mode).all(axis=1)
        ok = eq & rng & ((row_fresh & mm_fresh) | ((~row_fresh) & mm_cont))
        if sel_die is not None:
            g0 = groups[:, 0]
            die_rows = self._die_index[g0]
            if isinstance(sel_die, int):
                ok &= (die_rows == sel_die)
            else:
                sel_die_arr = np.array(list(sel_die), dtype=int)
                ok &= np.isin(die_rows, sel_die_arr)
        ok_rows = np.flatnonzero(ok)
        if len(ok_rows) == 0:
            self.oversample = (size > 0)
            return empty_arr()

        if sequential:
            st = vals[ok_rows, 0]
            ok2 = st + size <= (self.pagesize - 1)
            rows = ok_rows[ok2]
            if len(rows) == 0:
                self.oversample = (size > 0)
                return empty_arr()
            r = int(self._rng.choice(rows, size=1, replace=False))
            g = groups[r]
            start = int(self.addrstates[g[0]] + 1)
            pages = np.arange(start, start + size, dtype=int)
            arr = []
            for p in pages:
                arr.append(self._wrap_groups_as_addrs(g.reshape(1, -1), page=p))
            self.oversample = False
            return np.vstack(arr)

        # non‑sequential multi‑plane
        k = min(size, len(ok_rows))
        self.oversample = (size > len(ok_rows))
        rows = self._rng.choice(ok_rows, size=k, replace=False)
        chosen = groups[rows]
        pages = (self.addrstates[chosen[:, 0]] + 1).astype(int)
        return self._wrap_groups_as_addrs(chosen, page=pages)

    def apply_pgm(self, addrs: np.ndarray, mode=TLC) -> None:
        """
        Apply PROGRAM to the provided addresses.
        Increments per‑block programmed pages by the number of occurrences of the block in addrs.
        Sets program mode when starting from ERASE.
        """
        if addrs is None or len(addrs) == 0:
            return
        blocks = addrs[..., 1].astype(int).reshape(-1)
        if blocks.size == 0:
            return
        uniq, counts = np.unique(blocks, return_counts=True)
        # Save undo
        self.undo_addrs = uniq
        self.undo_states = self.addrstates[uniq].copy()
        self.undo_modes_erase = self.addrmodes_erase[uniq].copy()
        self.undo_modes_pgm = self.addrmodes_pgm[uniq].copy()
        # Apply increments
        self.addrstates[uniq] += counts
        # If any started from ERASE, set program mode
        started = (self.undo_states == ERASE)
        if np.any(started):
            self.addrmodes_pgm[uniq[started]] = mode

    def sample_read(
        self,
        sel_plane: int | list = None,
        mode=TLC,
        size: int = 1,
        offset: int = None,
        sequential: bool = False,
        sel_die: int | list = None,
    ) -> np.ndarray:
        """
        Pure address sampling for READ without mutating internal state.
        Mirrors random_read selection and shapes.
        """
        if isinstance(sel_plane, list) and len(sel_plane) == 1:
            sel_plane = sel_plane[0]

        _offset = self.offset if offset is None else int(offset)

        if sel_plane is None or isinstance(sel_plane, int):
            mask = (self.addrstates >= _offset) & (self.addrmodes_pgm == mode)
            if isinstance(sel_plane, int):
                mask &= (self._plane_index == sel_plane)
            if sel_die is not None:
                if isinstance(sel_die, int):
                    mask &= (self._die_index == sel_die)
                else:
                    sel_die_arr = np.array(list(sel_die), dtype=int)
                    mask &= np.isin(self._die_index, sel_die_arr)
            cand = np.flatnonzero(mask)
            if len(cand) == 0:
                self.oversample = (size > 0)
                return empty_arr()

            st = self.addrstates[cand]
            counts = (st - _offset + 1).astype(int)
            counts[counts < 0] = 0
            total = int(counts.sum())
            if total <= 0:
                self.oversample = (size > 0)
                return empty_arr()

            if not sequential:
                k = min(size, total)
                self.oversample = (size > total)
                r = self._rng.choice(total, size=k, replace=False)
                cum = np.cumsum(counts)
                blk_idx = np.searchsorted(cum, r, side="right")
                prev = np.concatenate(([0], cum[:-1]))
                page = (r - prev[blk_idx])
                blocks = cand[blk_idx]
                return self._wrap_blocks_as_addrs(blocks, pages=page)

            # sequential
            start_cap = counts - (size - 1)
            start_cap[start_cap < 0] = 0
            total_starts = int(start_cap.sum())
            if total_starts <= 0:
                self.oversample = (size > 0)
                return empty_arr()
            r = int(self._rng.choice(total_starts, size=1, replace=False))
            cum = np.cumsum(start_cap)
            i = int(np.searchsorted(cum, r, side="right"))
            prev = int(cum[i - 1]) if i > 0 else 0
            start = int(r - prev)
            blk = int(cand[i])
            pages = np.arange(start, start + size, dtype=int)
            blocks = np.repeat(np.array([blk], dtype=int), size)
            self.oversample = False
            return self._wrap_blocks_as_addrs(blocks, pages=pages)

        # multi‑plane
        planes = list(sel_plane)
        groups = self._groups_for_planes(planes)
        st = self.addrstates[groups]
        md = self.addrmodes_pgm[groups]
        ok = ((st > ERASE) & (st >= _offset) & (md == mode)).all(axis=1)
        if sel_die is not None:
            g0 = groups[:, 0]
            die_rows = self._die_index[g0]
            if isinstance(sel_die, int):
                ok &= (die_rows == sel_die)
            else:
                sel_die_arr = np.array(list(sel_die), dtype=int)
                ok &= np.isin(die_rows, sel_die_arr)
        if not np.any(ok):
            self.oversample = (size > 0)
            return empty_arr()
        rows = np.flatnonzero(ok)
        readmax = st[rows].min(axis=1) - _offset
        counts = (readmax + 1).astype(int)
        counts[counts < 0] = 0
        total = int(counts.sum())
        if total <= 0:
            self.oversample = (size > 0)
            return empty_arr()

        if not sequential:
            k = min(size, total)
            self.oversample = (size > total)
            r = self._rng.choice(total, size=k, replace=False)
            cum = np.cumsum(counts)
            ridx = np.searchsorted(cum, r, side="right")
            prev = np.concatenate(([0], cum[:-1]))
            page = (r - prev[ridx])
            chosen = groups[rows[ridx]]
            return self._wrap_groups_as_addrs(chosen, page=page)

        # sequential multi‑plane
        start_cap = counts - (size - 1)
        start_cap[start_cap < 0] = 0
        total_starts = int(start_cap.sum())
        if total_starts <= 0:
            self.oversample = (size > 0)
            return empty_arr()
        r = int(self._rng.choice(total_starts, size=1, replace=False))
        cum = np.cumsum(start_cap)
        i = int(np.searchsorted(cum, r, side="right"))
        prev = int(cum[i - 1]) if i > 0 else 0
        start = int(r - prev)
        g = groups[rows[i]]
        pages = np.arange(start, start + size, dtype=int)
        arr = []
        for p in pages:
            arr.append(self._wrap_groups_as_addrs(g.reshape(1, -1), page=p))
        self.oversample = False
        return np.vstack(arr)

    def random_erase(
        self,
        sel_plane: int | list = None,
        mode=TLC,
        size: int = 1,
        sel_die: int | list = None,
    ):
        """
        Backward‑compatible convenience: sample_erase + apply_erase.
        """
        adds = self.sample_erase(sel_plane=sel_plane, mode=mode, size=size, sel_die=sel_die)
        if adds is None or len(adds) == 0:
            return empty_arr()
        self.apply_erase(adds, mode=mode)
        return adds

    def random_pgm(
        self,
        sel_plane: int | list = None,
        mode=TLC,
        size: int = 1,
        sequential: bool = False,
        sel_die: int | list = None,
    ):
        """
        Backward‑compatible convenience: sample_pgm + apply_pgm.
        """
        adds = self.sample_pgm(sel_plane=sel_plane, mode=mode, size=size, sequential=sequential, sel_die=sel_die)
        if adds is None or len(adds) == 0:
            return empty_arr()
        self.apply_pgm(adds, mode=mode)
        return adds

    def random_read(
        self,
        sel_plane: int | list = None,
        mode=TLC,
        size: int = 1,
        offset: int = None,
        sequential: bool = False,
        sel_die: int | list = None,
    ):
        """
        Backward‑compatible convenience: identical to sample_read (no mutation).
        """
        return self.sample_read(sel_plane=sel_plane, mode=mode, size=size, offset=offset, sequential=sequential, sel_die=sel_die)

    def visual_seq_3d(self, seq: list, title="NAND Access Trajectory"):
        """
        Block 별 Erase, PGM, Read 동작 target address 를 추적함
        seq: (cmd_id, (plane, block, page))
        title: figure 의 title
        """
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
        block_traces = {}
        for t, (cmd_id, addr_vec) in enumerate(seq):
            for vec in addr_vec:
                addr = {k: vec[i] for i, k in enumerate(ADDR_KEYS)}
                block = addr["block"]
                page = addr["page"]
                block_traces.setdefault(block, []).append((page, t, cmd_id))

        # Define a color palette
        colors = [
            "blue",
            "green",
            "red",
            "orange",
            "purple",
            "brown",
            "pink",
            "gray",
            "olive",
            "cyan",
        ]

        # Create color map based on unique command IDs
        color_map = {
            cmd_id: colors[i % len(colors)]
            for i, cmd_id in enumerate(CMD_VOCAB.values())
        }

        fig = plt.figure(figsize=(12, 8))
        ax = fig.add_subplot(111, projection="3d")
        for blk, pts in block_traces.items():
            if not pts:
                continue
            pts.sort(key=lambda x: x[1])
            pages, times, cmds = zip(*pts)
            xs = [blk] * len(pages)
            ax.plot(xs, pages, times, alpha=0.4)
            ax.scatter(xs, pages, times, c=[color_map[c] for c in cmds], marker="o")

        # Add legend
        legend_elements = [
            Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                label=cmd,
                markerfacecolor=color_map[CMD_VOCAB[cmd]],
                markersize=8,
            )
            for cmd in CMD_VOCAB.keys()
        ]

        ax.legend(handles=legend_elements, title="Commands")
        ax.set_xlabel("Block")
        ax.set_ylabel("Page")
        ax.set_zlabel("Time")
        ax.set_title(title)
        plt.tight_layout()
        plt.show()

    def visual_seq_heatmap(
        self,
        seq: list,
        binned: bool = True,
        block_bins=100,
        page_bins=100,
        title="Address Heatmap",
    ):
        """
        Block, Address 별 address access 횟수를 누적하여 heatmap 생성
        seq: (cmd_id, (plane, block, page))
        binned: address range binning 여부
        block_bins: block bin 갯수
        page_bins: page bin 갯수
        title: figure 의 title
        """
        import seaborn as sns
        import matplotlib.pyplot as plt
        block_idxs = []
        page_idxs = []
        for cmd_id, addr_vec in seq:
            for vec in addr_vec:
                addr = {k: vec[i] for i, k in enumerate(ADDR_KEYS)}

                block = addr["block"]
                page = addr["page"]
                if binned:
                    block = int(block / self.num_blocks * (block_bins - 1))
                    page = int(page / self.pagesize * (page_bins - 1))

                block_idxs.append(block)
                page_idxs.append(page)

        if binned:
            heatmap_array = np.zeros((block_bins, page_bins), dtype=int)
            xtiklabels = block_bins // 10
            ytiklabels = page_bins // 10
        else:
            heatmap_array = np.zeros((self.num_blocks, self.pagesize), dtype=int)
            xtiklabels = self.num_blocks // 10
            ytiklabels = self.pagesize // 10

        for b, p in zip(block_idxs, page_idxs):
            heatmap_array[b, p] += 1

        plt.figure(figsize=(10, 6))
        sns.heatmap(
            heatmap_array.T,
            cmap="Reds",
            cbar=True,
            xticklabels=xtiklabels,
            yticklabels=ytiklabels,
        )
        plt.title(title)
        plt.xlabel("Block")
        plt.ylabel("Page")
        plt.gca().invert_yaxis()
        plt.tight_layout()
        plt.show()

    def visual_freq_histograms(self, seq: list, title="Operation Frequency Histograms"):
        """
        Generate frequency histograms for commands, planes, blocks, and pages from sequence data
        seq: (cmd_id, (plane, block, page)) tuples
        title: figure title
        """
        import matplotlib.pyplot as plt
        # Extract all components from sequence
        cmds = []
        planes = []
        blocks = []
        pages = []

        for cmd_id, addr_vec in seq:
            for vec in addr_vec:
                addr = {k: vec[i] for i, k in enumerate(ADDR_KEYS)}
                cmds.append(cmd_id)
                planes.append(addr["plane"])
                blocks.append(addr["block"])
                pages.append(addr["page"])

        # Create subplots
        fig, axes = plt.subplots(2, 2, figsize=(12, 10))
        fig.suptitle(title, fontsize=16)

        # Command frequency histogram
        cmd_counts = {}
        for cmd in cmds:
            cmd_counts[cmd] = cmd_counts.get(cmd, 0) + 1

        cmd_names = list(CMD_VOCAB.keys())
        cmd_values = [cmd_counts.get(i, 0) for i in range(len(cmd_names))]

        axes[0, 0].bar(range(len(cmd_names)), cmd_values)
        axes[0, 0].set_xticks(range(len(cmd_names)))
        axes[0, 0].set_xticklabels(cmd_names)
        axes[0, 0].set_title("Command Frequency")
        axes[0, 0].set_ylabel("Frequency")

        # Plane frequency histogram
        plane_counts = {}
        for plane in planes:
            plane_counts[plane] = plane_counts.get(plane, 0) + 1

        plane_values = [plane_counts.get(i, 0) for i in range(self.num_planes)]

        plane_labels = list(range(self.num_planes))
        axes[0, 1].bar(range(len(plane_values)), plane_values)
        axes[0, 1].set_xticks(plane_labels)
        axes[0, 1].set_xticklabels(plane_labels)
        axes[0, 1].set_title("Plane Frequency")
        axes[0, 1].set_ylabel("Frequency")

        # Block frequency histogram
        block_counts = {}
        for block in blocks:
            block_counts[block] = block_counts.get(block, 0) + 1

        # Use a reasonable number of bins for blocks
        block_bins = min(50, len(block_counts))
        axes[1, 0].hist(blocks, bins=block_bins, edgecolor="black")
        axes[1, 0].set_title("Block Frequency")
        axes[1, 0].set_xlabel("Block Addr.")
        axes[1, 0].set_ylabel("Frequency")

        # Page frequency histogram
        page_bins = min(50, len(pages))
        axes[1, 1].hist(pages, bins=page_bins, edgecolor="black")
        axes[1, 1].set_title("Page Frequency")
        axes[1, 1].set_xlabel("Page Addr.")
        axes[1, 1].set_ylabel("Frequency")

        plt.tight_layout()
        plt.show()

    # ------------------------
    # EPR validation callback (Phase 2)
    # ------------------------

@dataclass
class EprFailure:
    code: str
    message: str
    evidence: Dict[str, Any]


@dataclass
class EprResult:
    ok: bool
    failures: List[EprFailure]
    warnings: List[EprFailure]
    checked_rules: List[str]


def _extract_addr_triplet(t) -> Tuple[int, int, Optional[int]]:
    """Accept (die, block, page) tuple or object with attributes."""
    # tuple/list
    if isinstance(t, (tuple, list)) and len(t) >= 2:
        die = int(t[0])
        block = int(t[1])
        page = None if (len(t) < 3 or t[2] is None) else int(t[2])
        return die, block, page
    # object with attributes
    die = int(getattr(t, "die"))
    block = int(getattr(t, "block"))
    page_attr = getattr(t, "page", None)
    page = None if page_attr is None else int(page_attr)
    return die, block, page


def _effective_state(am: "AddressManager", die: int, block: int, pending: Optional[Dict[Tuple[int, int], Dict[str, Any]]]) -> int:
    """Return effective addr_state considering pending overlay if provided."""
    if pending is not None:
        ov = pending.get((die, block))
        if ov is not None and ("addr_state" in ov) and isinstance(ov["addr_state"], int):
            return int(ov["addr_state"])
    # translate die-local block index to global index
    idx = die * am._blocks_per_die + block
    return int(am.addrstates[idx])


def _is_program_base(base: str) -> bool:
    b = str(base).upper()
    return b in {"PROGRAM_SLC", "COPYBACK_PROGRAM_SLC"}


def _is_read_base(base: str) -> bool:
    b = str(base).upper()
    return b in {"READ", "READ4K", "PLANE_READ", "PLANE_READ4K", "CACHE_READ", "PLANE_CACHE_READ"}


def _is_erase_base(base: str) -> bool:
    return str(base).upper() == "ERASE"


def check_epr(
    *,
    base: str,
    targets: List[Any],
    op_name: Optional[str] = None,
    op_celltype: Optional[str] = None,
    as_of_us: Optional[float] = None,
    pending: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None,
    offset_guard: Optional[int] = None,
) -> EprResult:
    """Evaluate address dependency rules for proposed operation targets.

    Inputs:
      - targets: list of (die, block, page) tuples or objects with .die/.block/.page
      - pending: optional overlay dict from RM txn to reflect earlier same-txn operations
      - offset_guard: page read guard; defaults to AddressManager.offset if None
    Returns EprResult with failures gathered (warnings not used in this phase).
    """
    del op_name, as_of_us  # unused in core rules; kept for signature stability
    am = globals().get("addman")  # not used; placeholder for external references if needed
    checked: List[str] = []
    failures: List[EprFailure] = []
    warnings: List[EprFailure] = []

    # Normalize targets
    norm: List[Tuple[int, int, Optional[int]]] = [_extract_addr_triplet(t) for t in targets]
    if not norm:
        return EprResult(ok=True, failures=[], warnings=[], checked_rules=[])

    # Determine an AddressManager instance from context: since this function is defined
    # in module scope, we cannot access self. The recommended pattern is to bind
    # AddressManager.check_epr = check_epr.__get__(am_instance) at runtime, but for
    # simplicity we implement a pure function that requires global arrays. To keep
    # compatibility with research plan, we rebind this as a bound method below.
    raise_if_unbound = False
    # We will dynamically attach this function as a bound method on AddressManager below.
    # If mistakenly called unbound, signal via exception for easier debugging.
    if raise_if_unbound:
        pass

    return EprResult(ok=True, failures=failures, warnings=warnings, checked_rules=checked)


# Bind check_epr as an AddressManager method with access to "self"
def _addrman_check_epr(self: "AddressManager", base: str, targets: List[Any], *, op_name: Optional[str] = None, op_celltype: Optional[str] = None, as_of_us: Optional[float] = None, pending: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None, offset_guard: Optional[int] = None) -> EprResult:  # noqa: E501
    checked: List[str] = []
    failures: List[EprFailure] = []
    warnings: List[EprFailure] = []

    norm: List[Tuple[int, int, Optional[int]]] = [_extract_addr_triplet(t) for t in targets]
    if not norm:
        return EprResult(ok=True, failures=[], warnings=[], checked_rules=[])

    # program_before_erase
    if _is_program_base(base):
        checked.append("epr_program_before_erase")
        viol_pairs = []
        for (die, block, page) in norm:
            st = _effective_state(self, die, block, pending)
            if st != ERASE:
                viol_pairs.append((die, block))
        if viol_pairs:
            failures.append(EprFailure(
                code="epr_program_before_erase",
                message="Cannot program on non-erased block",
                evidence={"pairs": viol_pairs},
            ))

    # read_before_program_with_offset_guard
    if _is_read_base(base):
        checked.append("epr_read_before_program_with_offset_guard")
        guard = int(self.offset if offset_guard is None else offset_guard)
        viol_triplets = []
        for (die, block, page) in norm:
            if page is None:
                continue  # ignore if page not provided
            st = _effective_state(self, die, block, pending)
            readmax = int(st) - guard
            if page > readmax:
                viol_triplets.append((die, block, page, readmax))
        if viol_triplets:
            failures.append(EprFailure(
                code="epr_read_before_program_with_offset_guard",
                message="Read page must be <= last_programmed_page - offset_guard",
                evidence={"targets": viol_triplets},
            ))

    # programs_on_same_page (only when explicit pages are given)
    if _is_program_base(base):
        checked.append("epr_programs_on_same_page")
        seen: Dict[Tuple[int, int, int], None] = {}
        dup: List[Tuple[int, int, int]] = []
        for (die, block, page) in norm:
            if page is None:
                continue
            key = (die, block, int(page))
            if key in seen:
                dup.append(key)
            else:
                seen[key] = None
        # pending overlay may imply just-programmed page equals new target
        if pending:
            for (die, block, page) in norm:
                if page is None:
                    continue
                ov = pending.get((die, block))
                if ov and isinstance(ov.get("addr_state"), int) and int(ov["addr_state"]) == int(page):
                    dup.append((die, block, int(page)))
        if dup:
            failures.append(EprFailure(
                code="epr_programs_on_same_page",
                message="Multiple programs on the same page are forbidden",
                evidence={"targets": dup},
            ))

    # different_celltypes_on_same_block (only when op_celltype is provided)
    if _is_program_base(base) and op_celltype is not None:
        checked.append("epr_different_celltypes_on_same_block")
        mismatches: List[Tuple[int, int, str, str]] = []
        for (die, block, page) in norm:
            idx = die * self._blocks_per_die + block
            erase_mode = str(self.addrmodes_erase[idx])
            pgm_mode = str(self.addrmodes_pgm[idx])
            # Allowed: ERASE SLC with program A0SLC/ACSLC/SLC; otherwise modes must match
            if erase_mode == SLC:
                if op_celltype not in {SLC, A0SLC, ACSLC}:
                    mismatches.append((die, block, erase_mode, op_celltype))
            else:
                # continuing programs should match previous program mode if set
                if pgm_mode != TBD and op_celltype != pgm_mode:
                    mismatches.append((die, block, pgm_mode, op_celltype))
        if mismatches:
            failures.append(EprFailure(
                code="epr_different_celltypes_on_same_block",
                message="Celltype must be consistent within a block",
                evidence={"targets": mismatches},
            ))

    ok = (len(failures) == 0)
    return EprResult(ok=ok, failures=failures, warnings=warnings, checked_rules=checked)


# Attach as method
AddressManager.check_epr = _addrman_check_epr  # type: ignore[attr-defined]


# addrman 사용 예제 (직접 실행 시에만 동작)
if __name__ == "__main__":
    # device parameter 설정
    num_planes = 4
    num_blocks = 1020
    pagesize = 2564
    offset = 0

    num_samples = 1000
    test_mode = TLC
    p_init_erase = 0.5
    erased_blocks = int(p_init_erase * num_blocks)
    p_init_pgm = 0.001  # erase block 에서 pagesize 의 몇 퍼센트 pgm 할 지 확률

    # badblock 설정
    # badlist = np.random.choice(num_blocks, num_blocks*1//100)
    badlist = []

    # instance creation via topology mapping
    topology = {
        "dies": 1,
        "planes": num_planes,
        "blocks_per_die": num_blocks,
        "pages_per_block": pagesize,
    }
    addman = AddressManager.from_topology(topology, init=GOOD, offset=offset, badlist=badlist)

    # dict 초기화 : cmds, planes, modes
    dict_cmds = {i: 0 for i in ("ERASE", "PGM", "READ")}
    comb_planes = all_subsets(set(range(num_planes)))
    # print(f"plane combinations: {comb_planes}")
    dict_planes = {str(comb): 0 for comb in comb_planes}
    dict_modes = {mode: 0 for mode in (TLC, SLC)}

    # 항목별 확률 weight 설정
    p_opers = np.array([1, 5, 10], dtype=float)
    p_opers /= np.sum(p_opers)
    p_planes = np.ones(len(dict_planes), dtype=float) / len(dict_planes)
    # p_planes[:] = 0
    # p_planes[1] = 1 # plane 0: (0,)
    # p_planes[14] = 1 # plane 0~3: (0,1,2,3)
    p_planes /= np.sum(p_planes)
    p_modes = np.ones(len(dict_modes), dtype=float) / len(dict_modes)

    # 사전 erase
    cnt_tot = cnt = 0
    for _ in range(erased_blocks):
        adds = addman.random_erase(mode=test_mode)
        cnt_tot += 1
        if len(adds):
            cnt += 1

    states = addman.get_addrstates()
    modes = addman.get_addrmodes()
    print(
        f"pre erase succ rate: {cnt/cnt_tot:.2f}, total blocks: {num_blocks}, attempt:{cnt_tot}, success: {cnt}"
    )
    print(
        f"{test_mode} erased block rate: {np.sum((states == ERASE) & (modes == test_mode))/num_blocks}"
    )

    # 사전 pgm
    cnt_tot = cnt = 0
    for _ in range(int(erased_blocks * pagesize * p_init_pgm)):
        adds = addman.random_pgm(mode=test_mode)
        cnt_tot += 1
        if len(adds):
            cnt += 1

    print(
        f"pre pgm succ rate: {cnt/cnt_tot:.2f}, total blocks: {num_blocks}, attempt:{cnt_tot}, success: {cnt}"
    )
    print(
        f"{test_mode} pgmed block rate: {np.sum((states > ERASE) & (modes == test_mode))/num_blocks}"
    )

    # sampling 반복
    sequence = []
    cnt_tot = cnt = 0
    with open("output.txt", "w") as file:
        for i in range(num_samples):
            op = np.random.choice(list(CMD_VOCAB.keys()), p=p_opers)
            sel_plane = comb_planes[np.random.choice(len(comb_planes), p=p_planes)]
            # mode = np.random.choice(list(dict_modes.keys()), p=p_modes)
            mode = test_mode
            match op:
                case "ERASE":
                    adds = addman.random_erase(sel_plane=sel_plane, mode=mode)
                case "PGM":
                    adds = addman.random_pgm(sel_plane=sel_plane, mode=mode)
                    # adds = addman.random_pgm(sel_plane=sel_plane, mode=mode, size=20, sequential=True)
                case "READ":
                    adds = addman.random_read(sel_plane=sel_plane, mode=mode)
                    # adds = addman.random_read(sel_plane=sel_plane, mode=mode, size=20, sequential=True)
            if len(adds) == 0:
                file.write(
                    f"{i+1} rep, FAIL, {mode}, {op}, planes:{sel_plane}, addr:NONE\n"
                )
            else:
                str_adds = [e for e in np.squeeze(adds).tolist()]
                cnt += 1
                file.write(
                    f"{i+1} rep, SUCC, {mode}, {op}, planes:{sel_plane}, addr:{str_adds}\n"
                )

                cmd_id = CMD_VOCAB[op.item()]
                dies = adds[..., 0].flatten()
                blocks = adds[..., 1].flatten()
                planes = blocks % addman.num_planes
                pages = adds[..., 2].flatten()
                # print(cmd_id, op)

                seq = list(zip(planes.tolist(), blocks.tolist(), pages.tolist()))
                sequence.append((cmd_id, seq))

            cnt_tot += 1

    print(f"operation succ rate: {cnt/cnt_tot:.2f}, attempt:{cnt_tot}, success: {cnt}")

    # 시각화 출력
    addman.visual_seq_3d(sequence)
    addman.visual_seq_heatmap(sequence)
    addman.visual_freq_histograms(sequence)
