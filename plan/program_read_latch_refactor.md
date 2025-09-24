# READ/PROGRAM 다중 래치 리팩터 구현 계획

## 개요

ResourceManager가 (die, plane) 당 단일 `_Latch`만 유지하는 제약을 제거하여 READ와 PROGRAM 계열 래치가 동시에 유지되도록 개선한다. 구조를 일반화해 향후 래치 종류 확장에 대응하고, release·snapshot·export 경로를 모두 일관된 다중 래치 표현으로 통합한다.

## 현재 상태 분석

- `_Txn.latch_locks`와 `ResourceManager._latch`는 (die, plane) → 단일 `_Latch`에 고정되어 중복 래치가 덮어쓰기 된다. (`resourcemgr.py:87`, `resourcemgr.py:143`)
- `_is_locked_at` 및 `_latch_ok`는 단일 래치 전제를 두고 있어 READ/PROGRAM을 동시에 판별할 수 없다. (`resourcemgr.py:399`, `resourcemgr.py:409`)
- 예약/커밋 경로는 READ/PROGRAM 계열을 동일 키에 기록하고 release 훅이 전체 키를 pop 해 조기 해제가 발생한다. (`resourcemgr.py:569`, `resourcemgr.py:695`, `scheduler.py:269`)
- Snapshot/restore/save_snapshot은 단일 `_Latch` 포맷에 결합되어 있어 구조 변경 시 모두 동기 수정이 필요하다. (`resourcemgr.py:1217`, `resourcemgr.py:1315`, `main.py:861`)
- Config는 latch kind별 exclusion 그룹을 이미 보유하고 있어(kind 기반 판별 유지 필요) 향후 추가 kind에도 재사용 가능하다. (`config.yaml:2311`)

### 핵심 발견:
- 단일 맵 구조 때문에 READ 예약이 PROGRAM 래치를 덮어쓰며, release 경로도 kind 구분 없이 pop 한다. (`resourcemgr.py:574`, `resourcemgr.py:699`)
- Scheduler OP_END 훅이 READ/PROGRAM 종료마다 동일 release 함수를 호출해 다중 래치 전환 시 분기 처리가 필요하다. (`scheduler.py:284`)
- Snapshot/restore/save_snapshot 모두 기존 포맷을 가정해 포맷 개편 후 일관된 round-trip 검증이 없다. (`resourcemgr.py:1217`, `main.py:905`)

## 목표 상태

다중 래치 컨테이너 도입 후, READ/PROGRAM 래치가 동시에 유지·평가·해제되며 snapshot/restore/export가 새 포맷을 안정적으로 사용한다. 래치 관련 규칙 및 release 훅이 kind 단위로 동작하고, 신규 구조를 검증하는 테스트가 제공된다.

## 범위에서 제외되는 항목

- 기존 snapshot JSON 포맷 역호환 처리 (사용자 요청으로 필요 없음).
- latch exclusion 그룹(`config.yaml`)의 정책 수정 또는 확장.
- 래치 상태를 소비하는 외부 도구/리포터 변경 (save_snapshot 결과 반영 외 추가 확장 없음).

## 구현 접근

1. `_Txn`과 ResourceManager 내부 상태를 (die, plane)→{kind→Latch} 구조로 재정의하고 접근 보조 메서드를 추가한다.
2. 예약/커밋/검증/해제 경로를 새 컨테이너에 맞게 업데이트하고, Scheduler hook이 kind 단위 release를 호출하도록 보완한다.
3. Snapshot/restore/save_snapshot 및 테스트를 새 포맷으로 갱신하고, 회귀 테스트로 다중 래치 시나리오를 검증한다.

## 1단계: 래치 데이터 구조 재설계

### 개요
단일 `_Latch` 저장 방식을 다중 래치 컨테이너로 대체하고, 접근/변경 로직을 추상화해 나머지 코드가 일관된 API를 사용하도록 만든다.

### 필요한 변경:

#### 1. 트랜잭션 및 내부 상태 구조
**File**: `resourcemgr.py:87`
**Changes**: `_Txn.latch_locks`를 (die, plane)→dict[kind→_Latch] 또는 `_LatchSet` 타입으로 변경하고 helper 메서드를 추가.

#### 2. 런타임 래치 컨테이너
**File**: `resourcemgr.py:134`
**Changes**: `self._latch`를 새 컨테이너로 초기화하고 공용 helper (`_get_latches`, `_set_latch`, `_remove_latch`) 제공.

#### 3. 래치 판단 로직
**File**: `resourcemgr.py:399`
**Changes**: `_is_locked_at`·`_latch_ok`를 다중 래치 컬렉션을 순회하도록 업데이트하고 kind 기반 exclusion을 유지.

```python
@dataclass
class _LatchEntry:
    kind: str
    start_us: float
    end_us: Optional[float]
```

### 성공 기준:

#### 자동 검증:
- [x] `pytest`로 새/기존 단위 테스트 통과 (`python -m pytest`). (via .venv)

#### 수동 검증:
- [x] README/문서 없이도 코드 설명으로 새 구조가 이해된다고 확인.

---

## 2단계: 예약·해제·검증 경로 업데이트

### 개요
예약/커밋, release 훅, Scheduler 통합을 다중 래치 구조에 맞게 조정하여 READ와 PROGRAM 래치가 독립적으로 유지·해제되도록 한다.

### 필요한 변경:

#### 1. 예약/커밋 경로
**File**: `resourcemgr.py:525`
**Changes**: `_Txn.latch_locks`에 래치 추가 시 kind별 append, commit 시 `_latch` 컨테이너 병합 로직 구현.

#### 2. 래치 해제 함수
**File**: `resourcemgr.py:695`
**Changes**: `release_on_dout_end`가 READ 계열 kind만 제거하도록 수정, PROGRAM 계열은 유지. `release_on_exec_msb_end`는 PROGRAM 계열만 제거.

#### 3. Scheduler 통합
**File**: `scheduler.py:269`
**Changes**: OP_END 훅에서 새로운 release API 호출 (필요 시 kind 파라미터 전달) 및 대상 plane/die 계산 검증.

#### 4. 기타 소비자 업데이트
**File**: `resourcemgr.py:569`
**Changes**: `_latch_ok` 호출 전 새 컨테이너 사용, 필요 시 proposer가 참조하는 래치 관련 API 점검.

### 성공 기준:

#### 자동 검증:
- [x] READ→PROGRAM 연속 시나리오 단위 테스트가 래치 유지/해제를 검증. (tests/test_resourcemgr_multi_latch.py)
- [x] 기존 회귀 테스트 전부 통과 (`python -m pytest`). (via .venv)

#### 수동 검증:
- [x] 샘플 실행 `python main.py --config config.yaml --out-dir out` 결과에서 READ와 PROGRAM이 동일 plane에서 순차 실행 시 충돌 로그가 발생하지 않음. (Run: hooks=2728, ops_committed=404)

---

## 3단계: Snapshot/Export 및 테스트 보강

### 개요
새 래치 구조를 snapshot/restore/save_snapshot에 반영하고, 관련 exporter 및 테스트를 업데이트하여 포맷 변경과 다중 래치를 검증한다.

### 필요한 변경:

#### 1. Snapshot/Restore 업데이트
**File**: `resourcemgr.py:1217`
**Changes**: snapshot에 래치 리스트를 kind 포함 구조로 기록하고, restore가 새 포맷을 읽도록 변경 (역호환 불필요).

#### 2. Save Snapshot JSON
**File**: `main.py:861`
**Changes**: `save_snapshot`이 새 래치 리스트를 직렬화하도록 수정하고, JSON 스키마 문서화.

#### 3. 테스트 추가
**File**: `tests/`
**Changes**: 다중 래치 유지/해제 시나리오, snapshot round-trip, exporter 실행 smoke 테스트 추가.

### 성공 기준:

#### 자동 검증:
- [x] 신규 테스트가 래치 snapshot round-trip을 검증. (tests/test_resourcemgr_multi_latch.py)
- [x] `python -m pytest` 전체 통과. (via .venv)

#### 수동 검증:
- [x] `save_snapshot` 실행 후 JSON에서 READ/PROGRAM 래치가 모두 기록되는지 확인.

---

## 테스트 전략

### 단위 테스트:
- `_latch_ok`와 release 함수에 대한 직접 테스트로 READ/PROGRAM 동시 유지 및 해제 동작 확인.
- Snapshot/restore round-trip 시 래치 구조가 동일하게 복원되는지 검증.

### 통합 테스트:
- Scheduler를 통한 READ→DOUT→PROGRAM 시나리오 실행으로 래치 충돌이 해결됐는지 확인.
- `save_snapshot` 및 핵심 exporter 실행 smoke 테스트 (실패 없이 완료 여부 확인).

### 수동 테스트 단계:
1. `python main.py --config config.yaml --out-dir out` 실행.
2. 실행 로그에서 READ/PROGRAM 순서가 래치 충돌 없이 진행되는지 확인.
3. 생성된 snapshot JSON에서 READ/PROGRAM 래치 엔트리가 모두 존재하는지 검토.

## 성능 고려사항

- 래치 조회가 빈번하므로 새 컨테이너 접근이 O(1)에 가깝도록 구현하고 불필요한 복사를 피한다.
- Snapshot 직렬화 시 데이터량 증가에 대비해 필요 이상으로 중복 데이터를 기록하지 않는다.

## 마이그레이션 노트

- 기존 snapshot 포맷 역호환은 제공하지 않는다. 새 버전과 혼용 시 snapshot을 재생성해야 함을 문서화한다.

## 참고 자료

- 연구 기록: `research/2025-09-23_18-08-59_program_read_latch.md`
- Snapshot 영향 분석: `research/2025-09-23_18-25-43_multi_latch_snapshot_export_verification.md`
