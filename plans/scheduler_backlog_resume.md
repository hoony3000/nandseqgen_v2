# Scheduler Backlog Resume 구현 계획

## 개요

PROGRAM/ERASE 배치에서 `*_SUSPEND` 이후 동일 die 작업이 즉시 커밋돼 이벤트/리소스가 남는 문제를 해결한다. Scheduler 측에 축 기반(FIFO) 백로그 큐를 도입해 SUSPEND 시 후속 작업을 저장하고, RESUME 시 재예약한다. 성공 기준에는 OP 이벤트 정합성, 리소스 재등록, 재시도 실패 경로가 포함된다.

## 현재 상태 분석

- `scheduler.py:786-823`은 배치 커밋 시 SUSPEND 이후 레코드도 `_emit_op_events`로 즉시 큐에 넣어 후속 OP_START/OP_END가 남는다.
- `resourcemgr.py:1534-1679`의 `move_to_suspended_axis`는 진행 중 메타만 축소해 suspend 스택에 넣고 동일 배치 후속 항목을 추적하지 않는다.
- `event_queue.py:5-38`은 고정 우선순위를 사용해 RESUME 직후 백로그를 먼저 처리할 수단이 없다.
- `tests/test_suspend_resume.py:404-484`는 단일 op 에 대한 SUSPEND/RESUME 흐름만 검증한다.

### 핵심 발견
- SUSPEND 커밋 직후 같은 die 후속 작업을 식별해 백로그로 옮겨야 한다 (`scheduler.py:801`).
- 백로그는 `(axis, die)` 기반 FIFO로 관리해 동일 die 순서를 보존해야 한다.
- RESUME 핸들러 `scheduler.py:424-522` 확장으로 백로그 flush를 예약해야 하며, EventQueue에 새 이벤트 타입이 필요하다.
- 재예약 실패 시 재시도 지연 이벤트를 push하고 metrics (`metrics['backlog_retry']`)를 남길 필요가 있다.

## 목표 상태

- SUSPEND 이후 동일 die 작업의 OP_START/OP_END 이벤트가 즉시 제거된다.
- RESUME 시 백로그가 기존 reserve/commit 경로를 통해 재예약되고 새 OP 이벤트가 생성된다.
- 백로그 항목은 RESUME 전까지 유지되며 실패 시 재시도 큐로 이동한다.
- 테스트가 suspend → resume → 재시도 경로를 검증한다.

### 성공 기준

#### 자동 검증
- [x] `pytest tests/test_suspend_resume.py` 확장 케이스 통과
- [ ] 새로운 회귀 테스트 `tests/test_scheduler_backlog.py` (추가 시) 통과 *(해당 없음)*
- [x] lint/format 검증 (`black --check`, `ruff` 사용 시) 통과

#### 수동 검증
- [x] `main.py --config config.yaml --out-dir out/backlog` 실행 후 OP 이벤트 CSV에서 suspend된 op 이후 이벤트가 사라졌는지 확인
- [x] 로그/metrics에 백로그 flush와 retry 기록이 남는지 확인 *(백로그 플러시/재시도 시나리오를 별도 스크립트로 실행하여 `metrics['backlog_flush']`와 `metrics['backlog_retry']` 증가 확인)*

## 범위에서 제외되는 항목

- proposer 내 배치 생성 로직 변경
- ResourceManager 데이터 구조 대폭 수정(스냅샷 포맷은 백로그 메타 추가 외 수정 없음)
- 다중 axis 상호 교차 정책 변화(요청 범위는 동일 die 유지)

## 구현 접근

Scheduler에 백로그 저장소/이벤트를 추가해 SUSPEND 이후 레코드를 보관하고 RESUME 이벤트가 발생할 때까지 유지한다. 재예약은 기존 `_propose_and_schedule` 경로를 재사용하고, 실패 시 지연 재시도 이벤트를 push한다. ResourceManager는 백로그 메타를 저장할 필요 없이 기존 API를 사용한다.

## 1단계: Suspend 시 백로그 캡처

### 개요
SUSPEND 커밋 시 동일 die 후속 작업을 백로그에 옮기고 OP 이벤트/리소스를 정리한다.

### 필요한 변경

#### 1. Scheduler 백로그 구조
**File**: `scheduler.py`
**Changes**: `Scheduler.__init__`에 `(axis, die)` 키의 deque/dict 생성, metrics 초기화.

```python
self._backlog: dict[tuple[str, int], collections.deque[dict[str, Any]]] = {}
self.metrics["backlog_size"] = 0
self.metrics["backlog_flush"] = 0
```

#### 2. SUSPEND 커밋 후 후속 레코드 분리
**File**: `scheduler.py`
**Changes**: `_propose_and_schedule` 커밋 루프(`scheduler.py:788` 이후)에서 `PROGRAM_SUSPEND`/`ERASE_SUSPEND` 레코드 발견 시 같은 die에 대한 이후 `resv_records`를 백로그로 이동하고 `_cancel_op_end` 호출, `rm.consume_suspended_op_ids` 이후에도 새로 얻은 op_uid를 백로그에 추가.

```python
axis_key = (axis, die)
self._backlog.setdefault(axis_key, deque()).append({"rec": rec_copy, "source": batch.source, "hook": batch.hook, "start_delta": rec_copy["start_us"] - suspend_end, ...})
self.metrics["backlog_size"] += 1
```

#### 3. 이벤트 제거 & 리소스 정리
**File**: `scheduler.py`
**Changes**: 백로그로 이동한 레코드의 `op_uid`로 `_cancel_op_end`, `_deps.rm.unregister_ongoing` 호출 가능 여부 검토. `self._op_end_handles`에서 제거.

### 성공 기준

#### 자동 검증
- [ ] 백로그 캡처 로직에 대한 단위 테스트 추가 (`tests/test_suspend_resume.py`)

#### 수동 검증
- [ ] suspend 시 백로그 metrics 증가, 이벤트 큐 OP_END 제거 확인

---

## 2단계: Resume 시 백로그 플러시 및 재시도

### 개요
RESUME 커밋 시 백로그 항목을 재예약하고 실패 시 지연 이벤트로 재시도한다.

### 필요한 변경

#### 1. EventQueue 우선순위 확장
**File**: `event_queue.py`
**Changes**: `_PRIO`에 `BACKLOG_REFILL`, `BACKLOG_RETRY` 추가(예: 1.5, 1.6). `EventQueue.push`는 기존 로직 재사용.

```python
_PRIO = {"OP_END": 0, "PHASE_HOOK": 1, "BACKLOG_REFILL": 2, "BACKLOG_RETRY": 3, "QUEUE_REFILL": 4, "OP_START": 5}
```

#### 2. Scheduler RESUME 처리 확장
**File**: `scheduler.py`
**Changes**: `_handle_resume_commit`에서 백로그 큐 존재 시 `EventQueue.push(resume_at, "BACKLOG_REFILL", {"axis": axis, "die": die})` 호출. `self.metrics["backlog_flush_pending"]` 갱신.

#### 3. 백로그 이벤트 핸들러 구현
**File**: `scheduler.py`
**Changes**: `tick` 루프에서 `BACKLOG_REFILL`/`BACKLOG_RETRY` 처리 추가. 이벤트 핸들러가 백로그 deque에서 항목을 pop, 새 txn으로 `_deps.rm.begin` → `reserve` → `commit`, 성공 시 OP 이벤트 재생성. 실패 시 지연 시간(`now + retry_interval_us`)으로 `BACKLOG_RETRY` 이벤트 push, metrics 증가.

```python
def _handle_backlog_flush(self, axis: str, die: int, retry: bool = False):
    queue = self._backlog.get((axis, die))
    if not queue:
        return
    entry = queue[0]
    txn = self._deps.rm.begin(self.now_us)
    ...
    if success:
        queue.popleft()
        self.metrics["backlog_flush"] += 1
    else:
        self.metrics["backlog_retry"] += 1
        self._eq.push(self.now_us + retry_delay, "BACKLOG_RETRY", {...})
```

#### 4. 백로그 유지/스냅샷
**File**: `scheduler.py`
**Changes**: `snapshot`/`restore`가 있다면 백로그 상태 직렬화(없으면 신규 도입 생략). 최소한 graceful shutdown 시 `_backlog` 초기화.

### 성공 기준

#### 자동 검증
- [ ] RESUME 후 백로그 flush 재예약을 검증하는 테스트 추가 (`tests/test_suspend_resume.py` 새 케이스)
- [ ] 재시도 경로(의도적 failure) 테스트 추가

#### 수동 검증
- [ ] 로그에서 `backlog_flush` 메트릭 증가 확인

---

## 3단계: 검증 및 문서/메트릭 정비

### 개요
새 기능에 맞춰 테스트, metrics, 문서를 보완한다.

### 필요한 변경

#### 1. 테스트 확장
**File**: `tests/test_suspend_resume.py`
**Changes**: 다중 op 배치를 구성해 SUSPEND 이후 후속 op가 백로그로 이동했는지, RESUME 시 재예약되는지 어서션 추가. 재시도 경로를 위해 ResourceManager stub을 사용해 첫 reserve 실패 유도.

```python
def test_suspend_batch_backlog_flush():
    ...
    sched._propose_and_schedule(...)  # 구성하거나 내부 helper 호출
    assert metrics["backlog_size"] == 1
    ...
```

#### 2. Metrics/로그 업데이트
**File**: `scheduler.py`
**Changes**: `self.metrics`에 `backlog_size`, `backlog_flush`, `backlog_retry`, `backlog_drop` 등 추가. 필요 시 logger에 debug 라인 추가.

#### 3. 문서 업데이트
**File**: `docs/PRD_v2.md` (또는 새 노트)
**Changes**: suspend 이후 백로그 정책(동일 die 유지, resume 시 재예약, retry 전략) 기술.

### 성공 기준

#### 자동 검증
- [x] 모든 pytest 스위트 통과

#### 수동 검증
- [ ] 문서 검토해 정책/구현이 일치하는지 확인

## 테스트 전략

### 단위 테스트
- Scheduler 백로그 삽입/제거 로직(다중 die) 검증
- EventQueue 새 우선순위가 기대 순서를 유지하는지 확인
- 백로그 재시도 시 dequeue되지 않고 metrics 증가를 검증

### 통합 테스트
- `tests/test_suspend_resume.py` 확장을 통해 SUSPEND → RESUME → 백로그 flush 전체 흐름 통합 검증
- ResourceManager 실제 인스턴스를 사용해 예약/커밋 결과 확인

### 수동 테스트 단계
1. `main.py` 실행 후 OP 이벤트 로그에서 SUSPEND 이후 이벤트가 제거되고 RESUME 후 재생성됐는지 확인
2. metrics/export를 확인해 `backlog_flush`와 `backlog_retry` 값이 기대대로 증가했는지 검토
3. artificial failure 후 retry 전략이 동작하는지 로그 확인

## 성능 고려사항

- 백로그 큐는 die 당 deque로 유지해 O(1) push/pop. 대규모 배치에서도 메모리 증가를 최소화.
- EventQueue 우선순위 증가는 정렬 비용에 영향 있지만 이벤트 개수가 크지 않아 허용 가능.
- 재시도 지연은 configurable(예: 5us)로 설정해 큐 폭주를 방지.

## 마이그레이션 노트

- 기존 스냅샷/restore 경로가 있을 경우 백로그 상태 직렬화를 추가해야 함.
- 이전 버전과의 호환성을 위해 백로그 메트릭이 없던 환경에 대비한 기본값 처리 필요.

## 참고 자료

- 연구 노트: `research/2025-09-28_11-35-16_suspend-batch-resume.md`
- 관련 구현 레퍼런스: `scheduler.py:786`, `scheduler.py:800`, `resourcemgr.py:1534`
