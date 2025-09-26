# DIE_WIDE op_state 타임라인 확장 구현 계획

## 개요

ResourceManager의 DIE_WIDE 스코프 상태 기록을 die 내 모든 plane으로 확장하고, suspend/resume 및 exporter 경로와 정합되도록 조정한 뒤, 산출된 `op_state_timeline*.csv`에 구간 겹침이 없음을 자동 검증하는 작업입니다.

## 현재 상태 분석

- `_Txn.st_ops`는 `(die, plane, base, states, start)` 튜플만 저장해 scope 정보를 잃고, DIE_WIDE 작업도 대상 plane마다 별도 항목으로 추가됩니다 (`resourcemgr.py:104`, `resourcemgr.py:741-744`).
- `ResourceManager.commit`은 `txn.st_ops` 항목의 plane 값만 사용해 `_st.reserve_op`을 호출하므로 DIE_WIDE라도 해당 plane에만 segment가 생성됩니다 (`resourcemgr.py:768-773`).
- suspend 경로는 최근 meta.targets에서 plane을 파생해 CORE_BUSY 구간을 잘라내므로, 타임라인을 전 plane으로 확장하면 잔여 plane에 segment가 남을 수 있습니다 (`resourcemgr.py:804-848`, `resourcemgr.py:1468-1488`).
- proposer는 `ResourceManager.op_state`를 phase key로 사용하고 `affect_state` 정책을 적용하는데, 빈 plane에서는 기본값이 반환되어 제약이 무력화됩니다 (`proposer.py:1239-1283`).
- `export_op_state_timeline`는 snapshot 타임라인을 그대로 CSV로 내보내며, 중복이나 겹침이 생기면 후속 도구 가정이 깨집니다 (`main.py:369-440`).
- cfg에서 다수의 PROGRAM/ERASE base가 `scope: "DIE_WIDE"`, `affect_state: true`로 선언돼 설계 의도가 명확합니다 (`config.yaml:92-190`).

## 목표 상태

- DIE_WIDE `affect_state=true` 작업이 die 내 모든 plane에 단일 commit 경로로 상태 segment를 생성한다.
- suspend/resume, ongoing meta, snapshot/exporter가 확장된 타임라인과 정합되며, 중복·잔존 segment 없이 작동한다.
- `op_state_timeline*.csv`에 동일 `(die, plane, op_state)` 구간이 겹치지 않음을 자동 스크립트와 테스트로 보증한다.

### 핵심 발견:
- 타임라인 확장을 위해 scope를 `txn.st_ops`까지 보존하거나 commit 시 cfg를 재조회하는 보강이 필요합니다 (`resourcemgr.py:741-773`).
- suspend 경로는 meta.targets 기반으로 plane을 절단하므로, DIE_WIDE 확장 시 전체 plane 집합을 동기화해야 합니다 (`resourcemgr.py:804-848`, `resourcemgr.py:1468-1517`).
- exporter/효율 계산은 타임라인에 겹침이 없다는 전제를 사용하므로, 겹침 검출 도구를 새로 도입해야 합니다 (`main.py:369-440`).

## 범위에서 제외되는 항목

- proposer의 phase 분포 조정이나 config 확률 테이블 변경.
- AddressManager·Validator 정책 확장.
- 기존 CSV 스키마 변경(열 추가 등) — 검증 스크립트는 별도 파일로 제공.

## 구현 접근

`_Txn.st_ops`를 scope-aware 구조로 재정의해 DIE_WIDE 정보를 보존하고, commit 시 plane 목록을 순회하며 중복 없이 segment를 추가합니다. suspend/resume와 snapshot 경로는 이 구조를 그대로 사용하도록 확장하고, CSV 산출물에 겹침이 없는지 검사하는 스크립트와 테스트를 추가합니다.

## 1단계: `_Txn.st_ops` 구조 확장 및 commit 보강

### 개요
DIE_WIDE scope가 사라지지 않도록 `_Txn.st_ops`를 구조화하고, commit 시 plane 목록을 순회하며 상태를 기록합니다.

### 필요한 변경:

#### 1. `_Txn.st_ops` 구조 개편
**File**: `resourcemgr.py`
**Changes**: `_Txn.st_ops`를 새 dataclass(예: `_StOpEntry`) 목록으로 교체해 `die`, `planes`, `scope`, `base`, `states`, `start_us`를 저장하고, 기존 튜플 사용처를 업데이트합니다.

```python
@dataclass
class _StOpEntry:
    die: int
    planes: List[int]
    scope: Scope
    base: str
    states: List[Tuple[str, float]]
    start_us: float
```

#### 2. reserve/instant 경로 업데이트
**File**: `resourcemgr.py`
**Changes**: `txn.st_ops.append(...)` 호출을 `_StOpEntry` 기반으로 변경하고, DIE_WIDE일 때 `planes=list(range(self.planes))`로 설정, 동일 die/base/start 조합 중복을 방지합니다.

#### 3. commit 타임라인 확장
**File**: `resourcemgr.py`
**Changes**: commit 루프를 `_StOpEntry`에 맞게 조정해 항목마다 plane 목록을 순회하며 `_st.reserve_op`을 호출하고, ODT/CACHE/SUSPEND 보조 로직이 plane 기준으로 올바르게 작동하도록 정리합니다.

### 성공 기준:

#### 자동 검증:
- [x] 기존 스위트 `python -m pytest tests/test_resourcemgr_multi_latch.py` 통과.
- [x] 새 단위 테스트에서 DIE_WIDE 예약 후 모든 plane에 segment가 생성됨을 확인.

#### 수동 검증:
- [x] 리그 실행 후 특정 DIE_WIDE 작업의 타임라인을 spot-check하여 전 plane에 동일 기간이 반영되는지 확인.

---

## 2단계: suspend/resume 및 snapshot 경로 정합성 확보

### 개요
확장된 plane 정보를 suspend/resume, ongoing meta, exporter와 동기화해 잔여 segment나 잘못된 복원이 발생하지 않도록 합니다.

### 필요한 변경:

#### 1. suspend plane 집합 보강
**File**: `resourcemgr.py`
**Changes**: `move_to_suspended_axis`와 commit의 SUSPEND 분기를 `_StOpEntry`의 plane 목록이나 scope를 사용하도록 수정해, DIE_WIDE일 때 전체 plane을 잘라내고 overlay/avail이 일관되게 조정되도록 합니다.

#### 2. ongoing/snapshot 직렬화 업데이트
**File**: `resourcemgr.py`
**Changes**: `_OpMeta`와 snapshot/restore 경로가 확장된 plane 정보를 유지하도록 검토하고 필요한 경우 plane 집합을 포함시킵니다.

#### 3. exporter 정합성 확인
**File**: `main.py`
**Changes**: 타임라인 확장으로 행 수가 증가해도 `_uid_for` 등 가정이 여전히 유효한지 확인하고, 필요 시 경계 조건(정렬/검색) 보강 테스트를 추가합니다.

### 성공 기준:

#### 자동 검증:
- [x] `python -m pytest tests/test_suspend_resume.py` 통과.
- [x] 새 회귀 테스트에서 suspend → resume 후 모든 plane segment가 끊김 없이 복원됨을 확인.

#### 수동 검증:
- [x] snapshot JSON에서 해당 DIE_WIDE 작업이 plane 목록 전체를 기록하는지 확인.

---

## 3단계: CSV 겹침 검증 도구 및 테스트 추가

### 개요
`op_state_timeline*.csv`에서 동일 `(die, plane, op_state)` 조합으로 시간이 겹치지 않는지 검사하는 유틸리티와 테스트를 추가합니다.

### 필요한 변경:

#### 1. 겹침 검사 스크립트 작성
**File**: `scripts/check_op_state_overlaps.py` (신규)
**Changes**: CSV를 읽어 (die, plane, op_state)별로 구간을 정렬 후 겹침 여부를 검사하고, 발견 시 비제로 종료코드와 상세를 출력합니다.

#### 2. 테스트 추가
**File**: `tests/test_op_state_timeline_overlaps.py` (신규)
**Changes**: ResourceManager mock 타임라인을 생성해 스크립트 함수를 직접 호출하고, 겹침/비겹침 시 동작을 검증합니다.

#### 3. 통합 실행 훅(선택)
**File**: `Makefile` 또는 `scripts/README.md`
**Changes**: 시뮬레이션 산출물 검증 단계에 스크립트 실행 예시를 문서화합니다.

### 성공 기준:

#### 자동 검증:
- [x] `python -m pytest tests/test_op_state_timeline_overlaps.py` 통과.
- [x] 샘플 산출물에 대해 `python scripts/check_op_state_overlaps.py out/op_state_timeline*.csv` 실행 시 겹침이 없으면 0으로 종료.

#### 수동 검증:
- [x] 겹침 사례를 인위적으로 만들어 스크립트가 오류를 리포트하는지 확인.

---

## 테스트 전략

- 단위 테스트: `_StateTimeline`에 plane 확장이 제대로 반영되는지, suspend/resume 회귀 시나리오, 겹침 검사 유틸 검증.
- 통합 테스트: 선택된 config로 `python main.py --config config.yaml --out-dir out` 실행 후 새 검증 스크립트를 적용.
- 회귀 테스트: 기존 스위트(`python -m pytest`) 전체 실행으로 구조 변경 영향 확인.

## 성능 고려사항

- `_StOpEntry` 구조로 전환하면서 중복 삽입을 줄여 commit 루프 반복을 최소화합니다.
- DIE_WIDE 확장으로 타임라인 행 수가 plane 수 배로 증가하므로, exporter 및 겹침 검사에서 정렬 성능을 고려해 O(n log n) 알고리즘을 사용합니다.

## 마이그레이션 노트

- snapshot 포맷 변경 시 과거 스냅샷과의 호환성을 유지하기 위해 restore에서 기본값 경로를 제공해야 합니다.
- 새 스크립트는 CI/로컬 파이프라인에서 선택적으로 실행할 수 있도록 문서화합니다.

## 참고 자료

- 연구: `research/2025-09-26_15-17-55_die_wide_op_state_timeline.md`
- 연구: `research/2025-09-26_14-11-29_suspend_resume_plane_scope.md`
- 관련 계획: `plans/suspend_resume_reservation_reinstatement.md`
