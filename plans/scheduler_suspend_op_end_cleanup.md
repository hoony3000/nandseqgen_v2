# Scheduler suspend OP_END cleanup 구현 계획

## 개요

SUSPEND 커밋이 발생한 프로그램/소거 작업의 예정된 `OP_END` 이벤트를 즉시 취소하고, RESUME 후 새 이벤트만 실행하도록 큐/리소스 매니저/스케줄러 경로를 리팩터링한다. 이를 통해 동일 (die, block) 대상의 PROGRAM 페이지 증가가 1씩만 진행되도록 보장한다.

## 현재 상태 분석

- `Scheduler._emit_op_events` 는 모든 예약 레코드에 대해 OP_START/OP_END 를 무조건 enqueue 한다. SUSPEND 에 대해서도 예외가 없어, 이후 RESUME 시 새 OP_END 가 추가되기 전까지 기존 이벤트가 큐에 남는다. (`scheduler.py:829`–`scheduler.py:875`)
- `_handle_op_end` 는 실행 시점에 `ResourceManager.is_op_suspended` 로만 필터링한다. RESUME 이후에는 해당 op 이 suspended set 에 없으므로 과거 이벤트가 그대로 실행되어 `AddressManager.apply_pgm` 이 중복 호출된다. (`scheduler.py:271`–`scheduler.py:317`)
- `ResourceManager.move_to_suspended_axis` 는 `_ongoing_ops` 에서 meta 를 꺼내 axis 별 스택에 push 하지만, 어느 `op_id` 가 이동했는지 외부로 노출하지 않는다. (`resourcemgr.py:891`, `resourcemgr.py:1531`–`resourcemgr.py:1698`)
- `EventQueue` 는 push/pop 만 제공하며, 특정 이벤트를 가리키는 핸들을 반환하거나 제거할 수 없다. (`event_queue.py:15`–`event_queue.py:33`)
- AddressManager 는 `apply_pgm` 호출 시 블록 단위로 프로그램 페이지를 누적 증가시키므로 동일 (die, block) 에 대한 중복 호출이 곧 페이지 주소 0→2→4 증가 문제로 이어진다. (`addrman.py:607`–`addrman.py:646`)

### 핵심 발견:
- 큐에서 stale `OP_END` 를 제거하려면 push 시점의 식별자가 필요하다.
- SUSPEND 커밋 직후 바로 취소해야 하므로 ResourceManager 가 suspend 된 `op_id` 리스트를 Scheduler 에게 전달할 경량 버퍼가 필요하다.
- RESUME 시 새로 enqueue 되는 `OP_END` 는 교체된 핸들로 추적되어야 하며, 실제 실행된 이벤트만 `apply_pgm` 을 호출해야 한다.

## 목표 상태

- `EventQueue` 가 각 이벤트의 시퀀스 ID 를 반환하고, 해당 ID 로 항목을 삭제할 수 있다.
- `ResourceManager` 는 SUSPEND 시 이동한 op_id 를 축적하고, Scheduler 가 소진하면 비운다. snapshot/restore 시에도 버퍼 일관성을 유지한다.
- Scheduler 는 `op_uid` 별 `OP_END` 이벤트 핸들을 추적한다. SUSPEND 커밋 즉시 큐에서 제거하고, RESUME 시 새 핸들로 갱신한다.
- 단위/통합 테스트에서 SUSPEND→RESUME 반복 후에도 동일 블록의 PROGRAM 페이지가 1씩 증가함을 검증한다.

### 핵심 발견:
- `scheduler.py:772`–`scheduler.py:823`: 커밋 후 `_emit_op_events` 호출, 이후 `register_ongoing` 및 resume 처리.
- `resourcemgr.py:891`–`resourcemgr.py:909`: SUSPEND 처리가 commit 루프 안에서 수행됨.
- `tests/test_suspend_resume.py` 기존 케이스는 `_StubRM` 기반이라 신규 이벤트 취소 경로 검증을 위해 실제 RM/AddrMan 조합 테스트가 필요하다.

## 범위에서 제외되는 항목

- Resume 경로 외의 OP_END (예: READ/DOUT) 이벤트 취소 로직 변경.
- EventQueue 의 우선순위 정책 변화.
- GUI/CSV exporter 등 OP 이벤트 소비자 로직 수정 (필요 시 payload 확장 정도만 허용).

## 구현 접근

EventQueue 에 취소 API 를 도입해 핸들을 반환하고, ResourceManager 가 SUSPEND 된 op_id 를 버퍼링하여 Scheduler 가 즉시 취소한다. Scheduler 는 `op_uid` 별 handle 맵을 유지하고, SUSPEND/RESUME 흐름마다 갱신한다. 테스트는 suspend/resume 루프와 AddressManager 페이지 증가를 검증한다.

## 1단계: EventQueue 취소 API 추가

### 개요
`EventQueue.push` 가 시퀀스 ID 를 반환하도록 하고, 해당 ID 로 큐에서 이벤트를 제거할 수 있는 `remove(seq_id)` API 를 추가한다.

### 필요한 변경:

#### 1. push 반환값 및 remove 구현
**File**: `event_queue.py`
**Changes**: `push` 가 `int`(시퀀스) 를 반환하며, 내부 `_q` 를 선형 탐색해 특정 `_seq` 항목을 제거하는 `remove(self, seq_id: int, *, kind: str | None = None) -> bool` 구현. `_seq` 는 32-bit wrap 대비 python int 유지.

```python
    def push(...):
        ...
        self._seq += 1
        entry = (..., self._seq, ...)
        self._q.append(entry)
        self._q.sort(...)
        return self._seq

    def remove(self, seq_id: int, kind: str | None = None) -> bool:
        for i, (_, _, seq, k, _) in enumerate(self._q):
            if seq == seq_id and (kind is None or k == kind):
                del self._q[i]
                return True
        return False
```

### 성공 기준:

#### 자동 검증:
- [x] `python -m pytest tests/test_event_queue.py` (신규/기존) 추가 예정 — 최소 push/remove 동작 검증.

#### 수동 검증:
- [ ] REPL 에서 간단히 push/remove 호출 시 예상 bool 반환 확인.

---

## 2단계: ResourceManager suspend 전송 버퍼

### 개요
SUSPEND 시 이동한 `op_id` 를 축적하고 Scheduler 가 소비할 수 있도록 axis/die 별 버퍼와 consume API 를 도입한다. snapshot/restore 에도 반영한다.

### 필요한 변경:

#### 1. 버퍼 필드 및 초기화
**File**: `resourcemgr.py:173` 부근
**Changes**: `self._suspend_transfers = {"PROGRAM": {d: []}, "ERASE": {d: []}}` 초기화 및 타입 주석 추가.

#### 2. move_to_suspended_axis 확장
**File**: `resourcemgr.py:1531`
**Changes**: meta 이동 후 `meta.op_id` 가 있을 때 `self._suspend_transfers[fam][die].append(meta.op_id)` 수행.

#### 3. consume API 제공
**File**: `resourcemgr.py` (public section, e.g., around `suspended_ops_program`)
**Changes**: `def consume_suspended_op_ids(self, axis: str, die: Optional[int] = None) -> Dict[int, List[int]] | List[int]` 구현. `die` 지정 시 리스트 반환 후 버퍼 비움, 미지정 시 `{die: ids}` 딕셔너리.

#### 4. snapshot/restore 반영
**File**: `resourcemgr.py:1928`, `resourcemgr.py:2056`
**Changes**: snapshot dict 에 `_suspend_transfers` 추가, restore 시 존재하면 로드. 기본값은 빈 리스트로 재설정.

### 성공 기준:

#### 자동 검증:
- [x] 신규 단위 테스트에서 `move_to_suspended_axis` 호출 후 `consume_suspended_op_ids` 가 올바른 리스트 반환 및 버퍼 초기화 확인.
- [x] 기존 suspend/resume 테스트 (`python -m pytest tests/test_suspend_resume.py -k suspend`) 통과.

#### 수동 검증:
- [ ] suspend → consume → consume 재호출 시 빈 리스트 반환 확인 (디버그 REPL).

---

## 3단계: Scheduler 통합 및 이벤트 취소

### 개요
Scheduler 가 `OP_END` 이벤트 핸들을 추적하고, SUSPEND 커밋 시 `EventQueue.remove` 를 호출하여 중복 이벤트를 제거한다. RESUME 재등록 시 새 핸들로 갱신하며, 실제 실행된 이벤트만 기록한다.

### 필요한 변경:

#### 1. 내부 상태 추가
**File**: `scheduler.py:102`
**Changes**: `self._op_end_handles: Dict[int, int] = {}` 필드 추가 (op_uid → event seq). 필요 시 메트릭 `metrics['suspended_op_end_cancelled']` 추가.

#### 2. `_emit_op_events` handle 추적
**File**: `scheduler.py:829`
**Changes**: `op_uid` 존재 시 `handle = self._eq.push(...)` 결과를 저장하고 payload 에 `event_seq` 삽입. `OP_START` 는 기존대로 처리.

#### 3. `_handle_op_end` cleanup
**File**: `scheduler.py:271`
**Changes**: 진입 시 `op_uid_int` 가 있으면 `self._op_end_handles.pop(op_uid_int, None)` 호출. (payload 의 `event_seq` 도 사용 가능하면 교차 검증.)

#### 4. SUSPEND 커밋 취소 루틴
**File**: `scheduler.py:781` 이후 커밋 루프
**Changes**: `resv_records` 반복 시 `PROGRAM_SUSPEND` / `ERASE_SUSPEND` 인 레코드에 대해 die 식별 (targets→die, fallback `phase_hook_die`). `rm.consume_suspended_op_ids(axis, die)` 결과를 순회하며 `_cancel_op_end(op_uid)` 호출.

```python
    def _cancel_op_end(self, op_uid: int) -> bool:
        seq = self._op_end_handles.pop(op_uid, None)
        if seq is None:
            return False
        return self._eq.remove(seq, kind="OP_END")
```

- 취소 성공 시 metrics 증가. 실패 시 (이미 실행 등) 경고 로그 수준.

#### 5. RESUME 재등록 처리
**File**: `scheduler.py:485`
**Changes**: `_handle_resume_commit` 내 `self._eq.push` 반환값으로 새 seq 저장 (`self._op_end_handles[op_uid_int] = seq`). payload 에도 `event_seq` 업데이트.

#### 6. 종료 시 드레인 유지
**File**: `scheduler.py:192`
**Changes**: `_drain_pending_op_end_events` 에서 핸들 맵 정리 (`self._op_end_handles.pop(op_uid, None)`).

### 성공 기준:

#### 자동 검증:
- [x] 기존 suspend/resume 관련 테스트 전부 통과 (`python -m pytest tests/test_suspend_resume.py`).
- [x] 신규 테스트가 SUSPEND 후 큐에서 OP_END 가 제거되었음을 검증 (`EventQueue.remove` 호출 여부 및 핸들 맵 상태).

#### 수동 검증:
- [ ] 로깅 또는 metrics 를 통해 취소 건수 증가 확인 (예: `metrics['suspended_op_end_cancelled']`).

---

## 4단계: 테스트 및 회귀 검증

### 개요
큐 취소, ResourceManager 버퍼, Scheduler 전체 흐름을 검증하는 단위/통합 테스트를 추가하고, AddressManager 페이지 증가 검증을 포함한다.

### 필요한 변경:

#### 1. EventQueue 단위 테스트
**File**: `tests/test_event_queue.py` (신규)
**Changes**: push→remove→pop 흐름 검증 케이스 추가.

#### 2. Scheduler suspend/resume 회귀 테스트
**File**: `tests/test_suspend_resume.py`
**Changes**: 실제 `ResourceManager` + `AddressManager` 를 사용하는 시나리오 추가
- PROGRAM 예약 → `_emit_op_events` → SUSPEND → RESUME → OP_END 실행.
- `addrman.addrstates` 또는 `sched.drain_apply_pgm_rows()` 로 동일 (die, block) 의 페이지 증가가 1임을 단언.
- 큐 내 `OP_END` 항목 수/핸들 맵 상태 확인.

#### 3. Smoke: metrics/handle 맵 비움 확인
**File**: `tests/test_suspend_resume.py` 기존 케이스 보완
- `_handle_op_end` 호출 후 핸들 맵이 정리되는지 확인.

### 성공 기준:

#### 자동 검증:
- [x] `python -m pytest` 전체 통과.

#### 수동 검증:
- [ ] 필요 시 `.venv/bin/python main.py --config config.yaml --out-dir out` 실행 후 로그/metrics 에서 `suspended_op_end_cancelled` 증가 확인.

---

## 테스트 전략

### 단위 테스트:
- EventQueue push/remove 동작 검증.
- ResourceManager `consume_suspended_op_ids` 버퍼 동작.

### 통합 테스트:
- Scheduler + ResourceManager + AddressManager 로 suspend→resume 루프 실행, OP_END 취소 여부와 페이지 증가량 1 검증.

### 수동 테스트 단계:
1. 가상 환경 활성화 후 `python -m pytest` 실행.
2. 필요 시 샘플 실행 (`.venv/bin/python main.py --config config.yaml --out-dir out`) 후 로그에서 취소 메트릭/이벤트 확인.
3. 생성된 `out/*apply_pgm*.csv` 또는 metrics 로 페이지 증가 추이 점검 (옵션).

## 성능 고려사항

- EventQueue.remove 는 O(n) 이나, suspend 빈도가 상대적으로 낮고 큐 크기가 관리 가능한 수준이라 허용. 필요 시 추후 힙 + 인덱스 구조로 확장 가능.
- Scheduler 가 유지하는 핸들 딕셔너리는 활성 TRACKING op 수에 비례해 선형이며, suspend 시 즉시 제거되므로 상한이 낮다.

## 마이그레이션 노트

- Snapshot 포맷에 `_suspend_transfers` 필드가 추가된다. 과거 스냅샷을 로드할 때는 해당 필드가 비어있는 것으로 처리되므로 역호환성 유지.
- EventQueue API 변경으로 caller 가 반환값을 사용하도록 업데이트되어야 한다 (Scheduler 외 커스텀 호출자는 수정 필요).

## 참고 자료

- 관련 연구: `research/2025-09-27_18-48-25_scheduler_suspend_op_end_cleanup.md`
- 대안 비교: `research/2025-09-27_20-24-45_scheduler_suspend_op_end_alternatives.md`
- 기존 suspend/resume 테스트: `tests/test_suspend_resume.py`
