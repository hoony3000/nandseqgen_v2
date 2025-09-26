# Suspend/Resume Resource Reinstatement 구현 계획

## 개요

Suspend/Resume 동작에서 ResourceManager가 남은 구간을 새로 예약하도록 리팩터링해, 동일 ERASE/PROGRAM이 완료되기 전 다른 ERASE/PROGRAM이 잘못 예약되는 문제를 제거하고 레이어 책임을 유지합니다.

## 현재 상태 분석

- Resume 커밋은 `ResourceManager.resume_from_suspended_axis`를 호출해 meta의 `start_us/end_us`만 조정하고 `_ongoing_ops`로 돌려놓을 뿐, plane/bus/latch 타임라인을 재예약하지 않습니다 (`resourcemgr.py:1227`).
- Suspend 시 `_Txn.st_ops`에 저장된 state/bus 정보가 존재하지만 `_OpMeta`에는 보존되지 않아 잔여 구간을 재구성할 수 없습니다 (`resourcemgr.py:543`, `resourcemgr.py:1113`).
- `move_to_suspended_axis`는 remaining_us를 계산만 하고 state 슬라이스나 scope 복구 정보를 유지하지 않습니다 (`resourcemgr.py:1188`).
- Scheduler의 resume 경로는 ResourceManager에 재예약을 위임하지 않고 OP_END만 다시 큐잉합니다 (`scheduler.py:400`).
- 반복 suspend/resume 케이스는 plane 예약이 유지되지 않아 새 PROGRAM이 통과하는 회귀가 발생하며, 현재 테스트는 remaining_us만 검증합니다 (`tests/test_suspend_resume.py:68`).

## 목표 상태

- `_OpMeta`가 scope/state/bus 정보를 보존해 suspend 시점의 잔여 계획을 재구성할 수 있다.
- Resume 시 ResourceManager가 내부 트랜잭션을 생성해 잔여 state를 `reserve/commit`으로 재적용하고, 실패하면 안전하게 복원·로그를 남긴다.
- Scheduler는 재예약 성공 시 기존처럼 OP_END를 큐잉하고, 재예약 실패는 proposer 디버그 로그에 기록한다.
- 반복 suspend/resume 시 plane/bus/latch 예약이 유지되어 새 ERASE/PROGRAM 예약이 거절된다.

### 핵심 발견:
- `_Txn.st_ops`는 `(state, dur)` 목록을 이미 보존하므로 meta에 복사하면 잔여 state 슬라이싱에 활용 가능 (`resourcemgr.py:639`).
- `_st.truncate_after`가 CORE_BUSY 구간을 잘라내므로 suspend 타이밍 기준 잔여 시간을 정확히 측정할 수 있다 (`resourcemgr.py:741`).
- Snapshot/restore 경로가 `_ongoing_ops`와 `_suspended_ops_*` 정보를 직렬화하므로 새 meta 필드도 함께 저장해야 한다 (`resourcemgr.py:1324`, `resourcemgr.py:1477`).
- Scheduler는 tracking axis 보유한 op만 `register_ongoing`에 전달하므로 scope/state 정보를 이때 함께 넘기면 meta 확장이 가능하다 (`scheduler.py:734`).

## 범위에서 제외되는 항목

- Suspend/Resume 확률(proposer phase distribution) 조정이나 config.yaml 정책 변경.
- AddressManager/Validator 로직 확장 (필요 시 follow-up).
- 외부 CLI/CSV 출력 포맷 변경.

## 구현 접근

ResourceManager 중심으로 `_OpMeta`를 확장해 suspend 시 잔여 state/bus 정보를 저장하고, resume 시 내부 `_Txn`을 재구성해 기존 `reserve/commit` 경로를 그대로 사용합니다. 실패 시 meta를 suspended 스택에 되돌리고 proposer 디버그 로그에 기록해 관측 가능성을 유지합니다.

## 1단계: `_OpMeta` 메타데이터 확장

### 개요
Suspend 시점에 잔여 state를 재구성할 수 있도록 `_OpMeta`에 scope/state/bus 정보를 추가하고 snapshot 경로를 업데이트합니다.

### 필요한 변경:

#### 1. ResourceManager meta 구조
**File**: `resourcemgr.py`
**Changes**: `_OpMeta`에 `scope: Scope`, `states: List[Tuple[str, float]]`, `bus_segments: List[Tuple[float, float]]`, `consumed_us: float` 필드 추가. `register_ongoing` 시 scope/state/bus 정보를 채우고 snapshot/restore 경로에서 직렬화/역직렬화하도록 수정합니다.

```python
@dataclass
class _OpMeta:
    ...
    scope: Scope
    states: List[Tuple[str, float]] = field(default_factory=list)
    bus_segments: List[Tuple[float, float]] = field(default_factory=list)
    consumed_us: float = 0.0
```

### 성공 기준:

#### 자동 검증:
- [x] `tests/test_suspend_resume.py`의 기존 케이스가 통과한다. (`.venv/bin/python -m pytest tests/test_suspend_resume.py`)
- [x] 새로운 snapshot round-trip 검증이 meta 확장 후에도 통과한다 (`tests/test_resourcemgr_multi_latch.py`). (`.venv/bin/python -m pytest tests/test_resourcemgr_multi_latch.py`)

#### 수동 검증:
- [x] `ResourceManager.snapshot()` 출력에 새 필드가 포함되는지 확인.

---

## 2단계: suspend 시 잔여 state 슬라이스 생성

### 개요
`move_to_suspended_axis`에서 경과 시간을 기반으로 state/bus를 잘라 잔여 정보를 meta에 저장합니다.

### 필요한 변경:

#### 1. 잔여 state 계산 헬퍼 도입
**File**: `resourcemgr.py`
**Changes**: `_slice_states(states, consumed_us)` 헬퍼 추가. 누적 dur을 따라가며 소비된 시간을 제외하고 잔여 state 리스트를 quantize해 반환. Bus 세그먼트도 동일 원리로 슬라이스.

#### 2. suspend 경로 갱신
**File**: `resourcemgr.py`
**Changes**: `move_to_suspended_axis`에서 meta의 `consumed_us` 기록, `_slice_states` 호출로 `states`/`bus_segments` 업데이트, `remaining_us`는 잔여 state 합을 기준으로 재계산.

### 성공 기준:

#### 자동 검증:
- [x] 신규 회귀 테스트에서 suspend 후 잔여 state 수가 기대와 일치하는지 어서션 포함.

#### 수동 검증:
- [ ] Debug 로그 또는 REPL에서 meta.states가 소비 구간을 제외하는지 확인.

---

## 3단계: resume 시 내부 트랜잭션으로 재예약

### 개요
`resume_from_suspended_axis`가 meta의 잔여 정보를 바탕으로 `_Txn`을 생성해 `reserve/commit`을 호출하고, 실패 시 안전하게 복원합니다.

### 필요한 변경:

#### 1. 내부 트랜잭션 생성
**File**: `resourcemgr.py`
**Changes**: meta에서 `states`/`bus_segments`로 `_make_resume_op(meta)` 헬퍼를 통해 lightweight op 객체 생성. `_Txn`에 잔여 state 삽입 후 `reserve`/`commit` 호출. 성공 시 meta.start/end를 new window로 갱신.

#### 2. 실패 경로 처리
**File**: `resourcemgr.py`
**Changes**: 예약 실패하면 meta를 다시 suspended 스택에 push, `_last_resume_error` 필드에 reason 저장.

#### 3. Scheduler 연동
**File**: `scheduler.py`
**Changes**: `_handle_resume_commit`에서 ResourceManager가 None 또는 실패 상태를 반환하면 proposer 디버그 로그(`proposer._log`)에 기록하고 큐잉을 건너뛴다.

### 성공 기준:

#### 자동 검증:
- [x] 반복 suspend/resume 회귀 테스트가 xfail에서 pass로 전환된다.
- [x] 새 단위 테스트로 resume 실패 시 meta가 suspended로 복원되는지 검증.

#### 수동 검증:
- [ ] `proposer_debug_*.log`에 실패 이유가 기록되는지 확인.

---

## 4단계: 테스트 보강 및 문서 업데이트

### 개요
새 동작을 검증하고 스냅샷/문서가 최신 상태임을 보장합니다.

### 필요한 변경:

#### 1. 회귀 테스트 확장
**File**: `tests/test_suspend_resume.py`
**Changes**: 현재 xfail 케이스를 성공 경로로 전환해 잔여 plane 예약을 검증. multi-plane/다중 suspend 케이스 추가.

#### 2. snapshot 테스트
**File**: `tests/test_resourcemgr_multi_latch.py`
**Changes**: snapshot round-trip이 새 meta 필드를 포함하는지 assert 추가.

#### 3. 문서화
**File**: `docs/SUSPEND_RESUME_RULES.md`
**Changes**: Resume가 남은 구간을 다시 예약한다는 규칙을 명시하고, 실패 시 로깅 동작을 추가.

### 성공 기준:

#### 자동 검증:
- [x] `python -m pytest` 전체 스위트가 통과한다. (`.venv/bin/python -m pytest`)

#### 수동 검증:
- [x] `docs/SUSPEND_RESUME_RULES.md` 업데이트 사항이 팀 합의된 규칙과 일치하는지 리뷰.

---

## 테스트 전략

### 단위 테스트:
- 반복 suspend/resume 후 plane/bus/latch 예약 유지 검증 (`tests/test_suspend_resume.py`).
- Resume 실패 경로 및 snapshot 직렬화 검증.

### 통합 테스트:
- 시뮬레이터 실행 후 OP_START/OP_END CSV에서 동일 `op_uid`가 단 한 번씩만 등장하는지 확인.

### 수동 테스트 단계:
1. `python main.py --config config.yaml --out-dir out` 실행 후 `op_event_resume.csv`의 OP_END 타이밍 점검.
2. 생성된 `proposer_debug_*.log`에서 resume 실패 로그 유무 확인.

## 성능 고려사항

- 잔여 state 슬라이싱은 suspend 당 한 번 수행되며 리스트 길이가 작아 오버헤드는 미미할 것으로 예상됩니다.
- Resume 시 내부 트랜잭션을 생성하지만 기존 reserve/commit 로직을 재사용하므로 별도 최적화는 필요 없습니다.

## 마이그레이션 노트

- Snapshot 포맷이 확장되므로 구버전 스냅샷에 대한 역호환 로직을 추가하거나 버전 필드를 명시해야 합니다.
- 기존 suspended 메타를 로드할 때 새 필드를 기본값으로 보완합니다.

## 참고 자료

- 관련 연구: `research/2025-09-24_07-56-26_suspend_resume_reservation_alignment.md`
- 유사 구현: `resourcemgr.py:543`, `resourcemgr.py:1227`, `scheduler.py:400`
