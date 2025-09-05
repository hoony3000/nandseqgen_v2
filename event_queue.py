from __future__ import annotations

from typing import Any, Dict, List, Tuple


_PRIO = {"OP_END": 0, "PHASE_HOOK": 1, "QUEUE_REFILL": 2, "OP_START": 3}


class EventQueue:
    def __init__(self) -> None:
        self._q: List[Tuple[float, int, int, str, Dict[str, Any]]] = []
        self._seq: int = 0

    def is_empty(self) -> bool:
        return not self._q

    def push(self, when: float, kind: str, payload: Dict[str, Any]) -> None:
        pri = _PRIO.get(kind, 3)
        self._seq += 1
        self._q.append((float(when), int(pri), int(self._seq), str(kind), dict(payload or {})))
        self._q.sort(key=lambda x: (x[0], x[1], x[2]))

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

