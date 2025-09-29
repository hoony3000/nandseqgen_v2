# Scheduler Suspend Backlog 리팩터링 구현 계획

## 개요

SUSPEND 이후 동일 die 배치 항목을 Scheduler 가 정확히 backlog 로 옮기고, RESUME 시 backlog 항목이 복구되도록 하며, 시뮬레이션 스냅샷에서도 backlog 상태가 유지되도록 개선한다. targets 가 비거나 die 추론이 실패하는 경우에도 `PROGRAM_SUSPEND` 레코드의 die 메타를 상속하고, snapshot/restore 지원과 회귀 테스트 확장을 포함한다.

## 현재 상태 분석

- `scheduler.py:1122-1179` 는 SUSPEND 이후 항목을 backlog 로 보내기 전에 `die_candidate is not None` 검사를 수행해 targets 가 없으면 커밋 경로로 남는다.
- `scheduler.py:1318-1344` 의 커밋 루프는 여전히 모든 resv 레코드에 대해 OP 이벤트를 발행하고, ResourceManager 가 반환한 단일 uid 만 취소한다.
- `resourcemgr.py:1610-1726` 는 `_ongoing_ops` 스택의 마지막 meta 한 건만 suspend 스택으로 옮겨주어 Scheduler 가 추가 정보를 얻을 수 없다.
- Scheduler 는 backlog dict/set 만 보유하며 snapshot/restore 메서드가 없어 런 재개 시 상태를 복원할 수 없다(`scheduler.py` 전반, snapshot 정의 없음).
- 테스트는 단일 backlog 엔트리 happy-path 에만 집중하고 targets 미존재나 snapshot 복구 흐름이 누락돼 있다(`tests/test_suspend_resume.py:747-807`).

## 목표 상태

- SUSPEND 이후 후속 배치가 die 추론 실패 없이 backlog 로 이동하고, 커밋 단계에서 OP 이벤트가 남지 않는다.
- RESUME 시 backlog 엔트리가 FIFO 순서로 재예약되며 retry/metrics 가 유지된다.
- Scheduler snapshot/restore 가 backlog 항목과 pending 상태를 보존해 시뮬레이션 재개 시 동일 동작을 보장한다.
- 회귀 테스트가 다중 backlog 항목, targets 상속, snapshot 복구를 검증한다.

### 핵심 발견:
- `scheduler.py:1122` 의 `die_candidate is not None` gate 가 backlog 이동 실패의 직접 원인.
- `scheduler.py:1318` 커밋 루프는 backlog 로 빠진 항목을 제외하지 못해 OP 이벤트 정리가 불완전.
- `scheduler.py:842-848` 는 backlog 큐 존재 시에만 `BACKLOG_REFILL` 을 push 해 지속 상태가 필요.
- `resourcemgr.py:1917-1924` 는 Scheduler 에 uid 리스트만 넘기므로 backlog 메타는 Scheduler 에서 완전히 구성해야 함.

## 범위에서 제외되는 항목

- ResourceManager API 확장(다중 meta 반환 등) 변경.
- proposer 내부 배치 생성 로직 및 admission window 정책 수정.
- metrics/export 포맷 대폭 변경 (새 필드 추가 외 확장 없음).

## 구현 접근

Scheduler 중심으로 die 메타 상속과 backlog 직렬화를 구현한다. SUSPEND 시 `suspend_axes` 구조에 die 정보를 함께 보존하고, backlog 엔트리 생성 시 이를 사용한다. Snapshot 은 backlog 큐/Pending 집합을 직렬화하며 restore 시 deque/set 을 재구성한다. 테스트는 `pytest` 기반으로 다양한 suspend/resume 시나리오를 추가한다.

## 1단계: Suspend Backlog 보완

### 개요
SUSPEND 이후 후속 항목을 확실히 backlog 로 옮기고, die 정보를 상속하며 커밋 단계에서 잔여 이벤트를 방지한다.

### 필요한 변경:

#### 1. SUSPEND 메타 확장
**File**: `scheduler.py`
**Changes**: `suspend_axes[(axis, key_die)]` 저장 시 die 값을 포함하고, `die_candidate` 가 없으면 fallback 으로 사용.

```python
suspend_axes[(axis_key, key_die)] = {
    "die": key_die,
    "end_us": float(rec["end_us"]),
    "phase_hook_die": rec.get("phase_hook_die"),
    # ...
}
```

#### 2. Backlog 진입 조건 완화
**File**: `scheduler.py`
**Changes**: `_propose_and_schedule` 에서 `die_candidate is not None` 검사를 제거하고, suspend_info 에서 die 를 상속.

```python
backlog_die = die_candidate if die_candidate is not None else suspend_info.get("die")
if backlog_axis and suspend_info and backlog_die is not None:
    entry = self._create_backlog_entry(..., die=backlog_die, ...)
    self._enqueue_backlog_entry(backlog_axis, backlog_die, entry)
    self._mark_backlog_pending(backlog_axis, backlog_die, suspend_info["end_us"])
    continue
```

#### 3. Backlog pending 등록 유틸
**File**: `scheduler.py`
**Changes**: backlog 엔트리 추가 시 `_backlog_pending` 및 metrics 갱신 헬퍼 도입 (RESUME 전이라도 flush 이벤트 스케줄 optional).

```python
def _mark_backlog_pending(...):
    if key not in self._backlog_pending:
        self._backlog_pending.add(key)
        self.metrics["backlog_flush_pending"] = len(self._backlog_pending)
```

### 성공 기준:

#### 자동 검증:
- [ ] `pytest tests/test_suspend_resume.py -k backlog` (신규/변경 케이스 포함).

#### 수동 검증:
- [ ] suspend 대상 배치에 targets 누락된 op 포함 시 이벤트 큐에 잔여 OP_END 가 없는지 로그로 확인.

---

## 2단계: Backlog 상태 지속성

### 개요
Scheduler 가 backlog 및 pending 상태를 snapshot/restore 할 수 있도록 직렬화 로직을 추가한다.

### 필요한 변경:

#### 1. Snapshot/Restore 메서드 도입
**File**: `scheduler.py`
**Changes**: `snapshot(self) -> Dict[str, Any]` 와 `restore(self, snap: Dict[str, Any]) -> None` 추가. `_BacklogEntry` 를 직렬화 가능한 dict 로 변환.

```python
def snapshot(self) -> Dict[str, Any]:
    return {
        "now_us": self.now_us,
        "backlog": {
            f"{axis}:{die}": [entry.__dict__ for entry in queue]
            for (axis, die), queue in self._backlog.items()
        },
        "pending": [list(key) for key in self._backlog_pending],
    }
```

#### 2. Restore 시 구조 복원
**File**: `scheduler.py`
**Changes**: snapshot 에서 복원 시 deque/set 재구성, `_recompute_backlog_size()` 호출, pending metrics 갱신.

```python
def restore(...):
    self._backlog.clear()
    for key, items in snap.get("backlog", {}).items():
        axis, die = key.split(":", 1)
        queue = self._backlog_queue(axis, int(die))
        for payload in items:
            queue.append(_BacklogEntry(**payload))
    self._backlog_pending = {tuple(item) for item in snap.get("pending", [])}
    self.metrics["backlog_flush_pending"] = len(self._backlog_pending)
    self._recompute_backlog_size()
```

#### 3. InstrumentedScheduler 호환
**File**: `main.py`
**Changes**: InstrumentedScheduler 가 부모 snapshot/restore 호출 후 자체 상태(예: `_rows`) 유지하도록 가드 (필요 시 pass-through).

### 성공 기준:

#### 자동 검증:
- [ ] 신규 테스트에서 snapshot → restore 후 backlog flush 가 동일하게 동작.

#### 수동 검증:
- [ ] 단일 백로그가 남은 상태에서 snapshot 저장 후 새 Scheduler 에 restore 했을 때 metrics/backlog 값 동일.

---

## 3단계: 테스트 확장

### 개요
다중 backlog, targets 상속, snapshot 복구와 retry 흐름을 포괄하는 회귀 테스트를 추가한다.

### 필요한 변경:

#### 1. Suspend backfill 테스트 추가
**File**: `tests/test_suspend_resume.py`
**Changes**: targets 가 비어 die 추론이 실패하는 배치를 구성하고 backlog 크기/커밋 이벤트 확인.

```python
def test_suspend_backlog_inherits_die_when_targets_missing(...):
    # arrange suspend batch without targets
    # assert backlog key exists and events cancelled
```

#### 2. Snapshot 복구 케이스
**File**: `tests/test_suspend_resume.py`
**Changes**: Scheduler snapshot 저장 후 새 인스턴스 restore → RESUME → backlog flush 검증.

```python
def test_scheduler_backlog_snapshot_restore(monkeypatch):
    sched, rm, key, targets = _setup_scheduler_with_backlog(...)
    snap = sched.snapshot()
    sched2 = Scheduler(...)
    sched2.restore(snap)
    # resume and ensure backlog flushes
```

#### 3. 보조 헬퍼 조정
**File**: `tests/test_suspend_resume.py`
**Changes**: `_setup_scheduler_with_backlog` 가 suspend 정보에 die 를 기록하도록 업데이트.

### 성공 기준:

#### 자동 검증:
- [ ] `pytest tests/test_suspend_resume.py` 전체 통과.
- [ ] `pytest` 전체 스위트 (회귀 체크) 통과.

#### 수동 검증:
- [ ] 테스트 로그에서 `backlog_flush`/`backlog_retry` metrics 값이 기대대로 증가하는지 확인.

---

## 테스트 전략

### 단위 테스트:
- Suspend 후 backlog 엔트리 die 상속 유효성 (`test_suspend_backlog_inherits_die_when_targets_missing`).
- Snapshot/restore 후 backlog pending 복원 확인.

### 통합 테스트:
- 기존 suspend/resume 흐름(`test_scheduler_backlog_flush_on_resume`, `test_scheduler_backlog_retry_flow`)이 변경 후에도 통과하는지 확인.

### 수동 테스트 단계:
1. `.venv/bin/python -m pytest tests/test_suspend_resume.py` 실행 후 로그 확인.
2. 필요 시 `main.py` 시뮬레이션 실행, suspend → snapshot → restore → resume 플로우 수동 검증.
3. metrics 덤프에서 `backlog_size`, `backlog_flush_pending` 값 확인.

## 성능 고려사항

- `snapshot/restore` 직렬화는 backlog 큐 크기에 비례하며, `_BacklogEntry` 수는 die 당 deque 길이로 제한돼 메모리 영향이 작다.
- 추가 메트릭 업데이트는 정수 연산 수준이라 성능 영향이 미미하다.

## 마이그레이션 노트

- 신규 snapshot 필드를 사용하는 소비자는 `backlog`/`pending` 키를 처리해야 한다.
- Restore 시 구버전 스냅샷과의 호환성을 위해 키 존재 여부 검사와 기본값 처리 필요.

## 참고 자료

- 연구 노트: `research/2025-09-29_15-33-39_scheduler_backlog_resume_refactor.md`
- 기존 계획: `plans/scheduler_backlog_resume.md`
- 구현 참고: `scheduler.py:1122`, `scheduler.py:842`, `resourcemgr.py:1610`
