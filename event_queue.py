from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional


_PRIO = {"OP_END": 0, "PHASE_HOOK": 1, "QUEUE_REFILL": 2, "OP_START": 3}


class EventQueue:
    def __init__(self) -> None:
        self._q: List[Tuple[float, int, int, str, Dict[str, Any]]] = []
        self._seq: int = 0

    def is_empty(self) -> bool:
        return not self._q

    def push(self, when: float, kind: str, payload: Dict[str, Any]) -> int:
        pri = _PRIO.get(kind, 3)
        self._seq += 1
        seq = int(self._seq)
        payload_copy = dict(payload or {})
        payload_copy.setdefault("event_seq", seq)
        self._q.append((float(when), int(pri), seq, str(kind), payload_copy))
        self._q.sort(key=lambda x: (x[0], x[1], x[2]))
        return seq

    def pop_time_batch(self) -> Tuple[float, List[Tuple[float, int, int, str, Dict[str, Any]]]]:
        if not self._q:
            return (0.0, [])
        t0 = self._q[0][0]
        batch: List[Tuple[float, int, int, str, Dict[str, Any]]] = []
        i = 0
        while i < len(self._q) and self._q[i][0] == t0:
            batch.append(self._q[i])
            i += 1
        del self._q[:i]
        return (t0, batch)

    def remove(self, seq_id: int, *, kind: Optional[str] = None) -> bool:
        """Remove an event by its sequence identifier.

        When *kind* is provided, only remove a matching event type. Returns
        True when an entry was removed, False otherwise.
        """

        for idx, (_, _, seq, entry_kind, _) in enumerate(self._q):
            if seq == seq_id and (kind is None or entry_kind == kind):
                del self._q[idx]
                return True
        return False
