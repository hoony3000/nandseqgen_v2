# Scheduler state_block diagnostics 구현 계획

## 개요

`Scheduler._propose_and_schedule`에서 proposer가 `state_block`으로 후보를 탈락시킬 때 차단된 상태와 exclusion 그룹을 파악하고, `proposer_debug*.log` 및 스케줄러 진단에서 이를 확인할 수 있도록 인터페이스와 로깅을 확장한다.

## 현재 상태 분석

`scheduler.py:985-1352`의 `_propose_and_schedule`는 proposer가 `None`을 반환하면 즉시 `no_candidate`로 종료해 차단 사유를 알 수 없다. `proposer.py:1471-1487`는 `_candidate_blocked_by_states` 결과를 불리언으로만 다뤄 `attempts` 기록과 파일 로그 모두에 "state_block" 문자열만 남긴다. `_candidate_blocked_by_states` 자체도 차단 축/상태/그룹 정보를 계산하지만 외부에 전달하지 않는다(`proposer.py:370-428`). ResourceManager는 예약 단계에서 `state_forbid_suspend` 규칙으로 `CACHE_PROGRAM_SLC`를 막고 있으며, 이는 config의 `program_suspended` 그룹 정의에서 기인한다(`resourcemgr.py:2298-2415`, `config.yaml:2173-2321`).

## 목표 상태

- proposer가 `state_block` 사유를 구조화된 진단 객체로 반환해 scheduler가 실패 원인을 기록한다.
- `proposer_debug*.log`에 차단된 상태 축/상태/그룹 정보를 포함한 메시지가 출력된다.
- proposer metrics(`metrics["attempts"]`)에 사유가 구조화돼 post-mortem 분석과 로그가 일관된다.

### 핵심 발견:
- `proposer.py:370` `_candidate_blocked_by_states` 호출부는 현재 불리언만 사용한다.
- `proposer.py:1602` metrics 수집 지점은 구조화된 진단을 추가하기에 적절한 위치다.
- `scheduler.py:1003` 반환 경로에서 proposer 진단을 읽어 로그로 남겨야 한다.
- `proposer.py:80-107` `_log`는 proposer 파일 로그 출력을 담당한다.

## 범위에서 제외되는 항목

- ResourceManager 규칙 변경이나 config 수정.
- suspend 대상 조정 자체 (현재는 사유만 노출).
- proposer 후보 선정 알고리즘 변경.

## 구현 접근

1. `_candidate_blocked_by_states`가 차단 여부와 세부 사유(축, 상태, 그룹, base)를 담은 객체를 반환하도록 서명을 확장하고 관련 호출부를 업데이트한다.
2. proposer `attempts` 수집과 `_log` 호출을 새 진단 정보를 사용하도록 업데이트해 `proposer_debug*.log`에 상세 사유가 남도록 한다.
3. `Scheduler._propose_and_schedule`가 proposer 반환값에 포함된 진단 정보를 확인하고, `last_reason` 또는 별도 디버그 로그에 배치 실패 원인을 기록하도록 조정한다.
4. 필요 시 proposer와 scheduler 사이의 공용 DTO를 추가해 인터페이스 변경을 명확히 한다(예: `ProposeResult` dataclass).

## 1단계: proposer state block 진단 확장

### 개요
`_candidate_blocked_by_states`와 호출부가 차단 사유를 구조화해 반환하고 소비하도록 변경한다.

### 필요한 변경:

#### 1. proposer state 차단 헬퍼
**File**: `proposer.py`
**Changes**: `_candidate_blocked_by_states` 반환 타입을 bool → `(bool, Dict[str, Any])` 또는 dataclass로 변경하고, 차단 축(ERASE/PROGRAM/ODT/CACHE), 상태 값, exclusion 그룹, base 이름을 포함.

```python
@dataclass(frozen=True)
class StateBlockInfo:
    axis: str
    state: str
    groups: Tuple[str, ...]
    base: str

blocked, info = _candidate_blocked_by_states(...)
```

- suspend 평가 시 PROGRAM/ERASE 축과 상태 값을 info에 기록.
- ODT/CACHE 차단 시 axis를 `"ODT"`, `"CACHE"` 등으로 설정.
- 차단되지 않으면 `(False, None)`을 반환.

#### 2. proposer 후보 루프 업데이트
**File**: `proposer.py`
**Changes**: 후보 루프에서 `(blocked, info)`를 받아 `attempts.append` 시 `reason_details=info` 추가.

```python
if blocked:
    attempts.append({
        "name": name,
        "prob": prob,
        "reason": "state_block",
        "details": info.as_dict(),
    })
    _log(f"[proposer] try name={name} ... state_block axis={info.axis} state={info.state} groups={info.groups}")
    continue
```

- `_log` 문자열에 axis/state/groups/base를 포함.

### 성공 기준:

#### 자동 검증:
- [x] `pytest` 전체 통과: `.venv/bin/python -m pytest`

#### 수동 검증:
- [x] proposer debug 로그에 `state_block` 상세 항목이 출력되는지 확인.
- [x] metrics dump에서 `attempts` 항목의 `details`가 채워졌는지 확인.
- [x] 기존 `state_block` 처리 외 케이스 회귀 없음.

---

## 2단계: proposer ↔ scheduler 인터페이스 확장

### 개요
proposer가 진단을 함께 반환하고, scheduler가 이를 소비해 마지막 실패 사유에 남긴다.

### 필요한 변경:

#### 1. proposer 반환 구조 조정
**File**: `proposer.py`
**Changes**: `ProposedBatch` 대신 `ProposeResult`(예시) 반환. 배치와 함께 `diagnostics` 필드를 포함해 `attempts` 리스트를 scheduler가 직접 사용 가능.

```python
@dataclass(frozen=True)
class ProposeResult:
    batch: Optional[ProposedBatch]
    diagnostics: ProposeDiagnostics

@dataclass(frozen=True)
class ProposeDiagnostics:
    attempts: Tuple[AttemptRecord, ...]
    last_state_block: Optional[StateBlockInfo]
```

- 기존 호출부(`scheduler.py:995-1002`)가 새 타입을 수용하도록 변경.
- 후방호환이 필요하다면 proposer 내부에서 `ProposedBatch`에 `diagnostics` 속성을 추가하는 대안 검토.

#### 2. scheduler 소비 로직
**File**: `scheduler.py`
**Changes**: proposer 호출 후 `result.batch` 사용, `result.diagnostics`를 확인해 배치가 없을 때 마지막 `state_block` 이유를 `last_reason`과 logger에 기록.

```python
result = _proposer.propose(...)
if not result.batch:
    reason = result.diagnostics.last_reason or "no_candidate"
    self.metrics["last_reason"] = reason
    self._log_state_block(reason, result.diagnostics)
    return (0, False, reason)
```

- `self._log_state_block`(신규)에서 `details`를 `logger.debug`나 `metrics["last_state_block"]`에 저장.

### 성공 기준:

#### 자동 검증:
- [x] 관련 unit test 추가 (프로포저 result 변환) → `tests/test_proposer_state_block.py`(신규) 등

#### 수동 검증:
- [x] 인터페이스 변경에 따른 호출부 에러 없음 (`main.py` 실행 점검).
- [x] proposer_debug 로그와 scheduler metrics가 동일한 사유를 보고함.

---

## 3단계: 로깅 및 메트릭 정비

### 개요
새 진단정보가 `proposer_debug*.log`와 scheduler 관측 지표에서 쉽게 확인되도록 보완한다.

### 필요한 변경:

#### 1. proposer 로그 포맷 정리
**File**: `proposer.py`
**Changes**: `_log` 호출 메시지에 `state_block axis=... state=... groups=... base=...` 형식 반영.

#### 2. scheduler metrics 확장
**File**: `scheduler.py`
**Changes**: `self.metrics`에 `last_state_block_details` 추가, proposer 진단을 그대로 저장. `logger`가 있다면 `logger.info`/`logger.debug`로 동일 메시지를 출력.

```python
self.metrics["last_state_block_details"] = result.diagnostics.last_attempt_details
```

### 성공 기준:

#### 자동 검증:
- [x] 기존 pytest 스위트 통과 (1단계 테스트와 동일).

#### 수동 검증:
- [x] 시뮬레이션 실행 후 `proposer_debug*.log`에서 상세 메시지 확인.
- [x] scheduler metrics dump에서 `last_state_block_details` 확인.

---

## 테스트 전략

### 단위 테스트:
- proposer 진단 구조 테스트: state_block 시 `details.axis == "PROGRAM"` 등을 검증.
- scheduler가 proposer 진단을 받아 `last_reason`에 반영하는 테스트 추가.

### 통합 테스트:
- 시뮬레이션 run을 통해 suspend 상태에서 `CACHE_PROGRAM_SLC`가 차단되는 시나리오 재현, 로그/metrics 출력 확인.

### 수동 테스트 단계:
1. `.venv/bin/python main.py --config config.yaml --out-dir out` 실행.
2. 생성된 `proposer_debug*.log`에서 state_block 메시지 확인.
3. scheduler metrics 출력(예: 디버그 dump)에서 `last_state_block_details` 검사.

## 성능 고려사항

- 진단 객체 생성에 따른 오버헤드는 후보 평가 루프 내에서만 발생하며, topN 크기가 제한돼 영향 미미.
- 파일 로그 출력은 기존 `_log` 경로를 재사용하므로 추가 비용은 문자열 포매팅 수준.

## 마이그레이션 노트

- proposer 반환 타입 변경 시 호출자(주로 scheduler) 외 다른 사용자가 있는지 확인하고 동시 수정 필요.
- 기존 로그 파서가 `state_block` 문자열만 가정한다면 새로운 필드 대응 업데이트 필요.

## 참고 자료

- 관련 연구: `research/2025-09-28_15-52-08_cache_program_slc_suspend.md`
- 유사 구현: `scheduler.py:985`, `proposer.py:370`, `proposer.py:1471`, `resourcemgr.py:2298`
