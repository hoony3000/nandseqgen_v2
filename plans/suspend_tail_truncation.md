# SUSPEND Tail Truncation 구현 계획

## 개요

SUSPEND 시점에서 동일 base의 잔여 상태(예: `CACHE_PROGRAM_SLC.DATAIN`)를 즉시 타임라인에서 제거하고 RESUME 흐름이 남은 상태를 재예약하도록 ResourceManager 중심 로직을 조정한다. 이를 통해 `op_state_timeline` CSV 및 snapshot 상에서 의도치 않은 상태가 남지 않도록 한다.

## 현재 상태 분석

- SUSPEND 커밋 시 `_st.truncate_after`가 `CORE_BUSY` 상태만 제거 대상으로 한정되어 있어 동일 base의 다른 상태가 유지된다.
- `move_to_suspended_axis`는 남은 state 리스트를 보존하고 있으므로 타임라인을 정리해도 RESUME 로직이 정상 재예약을 수행한다.
- Scheduler 백로그 흐름은 이미 suspend 대상 작업을 재스케줄하도록 구성되어 있으나, 잔여 상태가 남아 CSV에 기록되고 있어 분석/모니터링이 어렵다.

### 핵심 발견:
- `resourcemgr.py:880`~/`934` – suspend 시 `_pred` 가 `seg.state == "CORE_BUSY"` 조건만 확인하여 `DATAIN` 등이 제거되지 않음.
- `resourcemgr.py:1600`~/`1680` – `move_to_suspended_axis` 가 meta.states 및 bus 구간을 재생성하여 RESUME 시 재예약 수행.
- `scheduler.py:1100`~/`1184` – suspend 이후 동일 base 작업을 backlog 로 이동시켜 재예약하나 타임라인 tail 미정리.
- `config.yaml:136` – `CACHE_PROGRAM_SLC` 가 ISSUE → CORE_BUSY → DATAIN 상태를 정의.
- `out/op_state_timeline_250928_0000001.csv:21` – `PROGRAM_SUSPEND` 직후에도 `CACHE_PROGRAM_SLC.DATAIN` 구간이 그대로 남아 있음 (재현 사례).

## 목표 상태

- suspend 시점 이후 동일 base의 모든 예약 state 구간이 제거된다.
- RESUME 시 재예약된 작업만 타임라인에 남아 CSV·snapshot이 실제 실행 상태를 반영한다.
- 기존 RESUME/backlog 흐름 및 다른 base(SLC 프로그램 외) suspend 로직이 회귀 없이 동작한다.

### 성공 검증 방법
- suspend 이후 동일 base state 구간 (`*.CORE_BUSY`, `*.DATAIN`, 기타 정의) 이 타임라인에서 제거되었음을 단위 테스트로 검증.
- 기존 `tests/test_suspend_resume.py` 를 확장/신규 케이스 추가하여 suspend→resume 시 잔여 state 없음 확인.
- 엔드투엔드 시뮬레이션 (`main.py -t 50000`) 결과 CSV에서 `PROGRAM_SUSPEND` 직후 동일 base state 가 남지 않는지 확인.

## 범위에서 제외되는 항목

- SUSPEND/RESUME 정책(허용 여부)이나 proposer 제안 전략 변경.
- ERASE/PROGRAM 이외 다른 베이스(READ 등) 에 대한 suspend 처리 개선.
- snapshot/export 포맷 구조 변경 (데이터 정합성 확보 외 추가 필드 조정).

## 구현 접근

`ResourceManager.commit` 의 suspend 블록을 중심으로 tail state 제거를 일반화한다. suspend 된 meta 정보를 활용해 목표 base와 plane 집합을 정확히 식별한 후, `_st.truncate_after` 호출 시 predicate 를 “동일 base & 동일 die/plane & suspend/resume가 아닌 state” 로 확장한다. 필요 시 base 목록·plane 목록을 수집하는 헬퍼 함수를 도입해 가독성을 높인다. 이후 Scheduler·테스트에서 tail 제거 후에도 재예약이 정상 동작함을 보강한다.

## 1단계: Tail state 절단 로직 확장

### 개요
동일 base의 모든 후속 state (`CORE_BUSY`, `DATAIN`, 기타 정의) 가 suspend 시점 이후 제거되도록 predicate 와 plane/base 수집 로직을 정비한다.

### 필요한 변경:

#### 1. ResourceManager suspend 처리
**File**: `resourcemgr.py`
**Changes**:
- suspend 블록에서 meta 를 활용해 `bases_to_cut` / `planes_to_cut` 목록을 구성하는 헬퍼 추가 (예: `_suspend_axes_targets`).
- `_pred` 를 `seg.op_base` 가 `bases_to_cut` 내에 존재하고, 상태 이름에 `SUSPEND`/`RESUME` 이 포함되지 않는 한 모두 제거하도록 확장.
- entry_plane_set 이 비어있는 경우 meta.targets 및 `Scope.DIE_WIDE` 를 고려해 plane 목록을 채움.
- 필요 시 suspend 처리 루프 외부로 공통 헬퍼 분리해 재사용성 확보.

```python
# 예시 스케치
bases_to_cut = _collect_bases_for_suspend(meta)

def _pred(seg: _StateInterval) -> bool:
    base = str(seg.op_base)
    state = str(seg.state).upper()
    return (
        base in bases_to_cut
        and "SUSPEND" not in base.upper()
        and "RESUME" not in base.upper()
    )
```

### 성공 기준:

#### 자동 검증:
- [x] `pytest tests/test_suspend_resume.py` 에 신규/확장 케이스 추가하여 통과
- [x] `pytest tests/test_proposer_state_block.py` (회귀 확인)

#### 수동 검증:
- [x] `main.py --config config.yaml -t 50000 --out-dir out` 실행 후 `op_state_timeline*.csv` 에서 `PROGRAM_SUSPEND` 직후 동일 base state 누락 확인
- [x] `out/snapshots/*` 에 동일 base timeline segment 잔존 여부 수동 점검

---

## 2단계: Scheduler 백로그·재예약 경로 점검

### 개요
tail 제거 이후에도 backlog/resume 로직이 정상 동작하도록 필요시 경로를 보완하고, 테스트를 통해 검증한다.

### 필요한 변경:

#### 1. Scheduler resume/backlog 테스트 확장
**File**: `tests/test_suspend_resume.py`
**Changes**:
- suspend 후 backlog 로 옮겨진 작업이 재예약될 때 RESOURCE timeline 에 잔여 state 가 없어야 함을 검증하는 케이스 추가.
- 기존 `_setup_scheduler_with_backlog` 유틸을 사용해 suspend→resume 플로우를 재현하고, ResourceManager snapshot/timeline 확인을 위한 assertion 추가.

```python
def test_tail_states_removed_on_suspend(resource_manager_fixture):
    # suspend 전후 snapshot 비교 후 동일 base 상태 제거 확인
```

### 성공 기준:

#### 자동 검증:
- [x] `pytest tests/test_suspend_resume.py -k tail`

#### 수동 검증:
- [x] Scheduler backlog metrics (`backlog_size`, `backlog_flush`) 가 기존과 동일하게 집계되는지 로그 확인

---

## 3단계: 회귀 테스트 및 산출물 검증

### 개요
전체 테스트와 시뮬레이션을 통해 회귀가 없는지 확인하고, 계획한 수동 검증 절차를 문서화한다.

### 필요한 변경:

#### 1. 테스트 실행 문서화
**File**: `docs/` (필요시)
**Changes**: (선택) suspend tail 처리 관련 테스트 방법 간단히 언급.

### 성공 기준:

#### 자동 검증:
- [x] `pytest`

#### 수동 검증:
- [x] `main.py` 실행 결과에서 `CACHE_PROGRAM_SLC.DATAIN` 이 suspend 직후 존재하지 않음
- [x] `out/op_event_resume.csv` 등 샘플 파일 검사해 RESUME 재등록이 기대대로 진행되었는지 확인

---

## 테스트 전략

### 단위 테스트:
- ResourceManager suspend → timeline trimming을 확인하는 전용 테스트 (기존 `test_suspend_resume` 확장).
- resume 실패/성공 케이스 재확인하여 meta/state 보존이 조정 이후에도 일관적인지 검증.

### 통합 테스트:
- Scheduler 백로그 플로우 (`test_scheduler_backlog_*`) 가 회귀 없이 통과하는지 확인.
- 필요 시 간단한 통합 시뮬레이션을 통해 op_state_timeline 산출물을 비교.

### 수동 테스트 단계:
1. `python main.py --config config.yaml -t 50000 --out-dir out` 실행
2. `grep "CACHE_PROGRAM_SLC" out/op_state_timeline_*.csv` 로 suspend 직후 state 존재 여부 확인
3. `jq` / 뷰어로 `out/snapshots/state_snapshot*.json` 내 timeline 항목 점검

## 성능 고려사항

- suspend tail 제거는 기존 `_st.truncate_after` 호출을 유지하므로 오버헤드는 미미하다.
- plane/base 식별을 위해 meta 조회가 추가되지만 die당 suspend 빈도가 낮아 성능 영향이 없다고 판단.

## 마이그레이션 노트

- 추가 마이그레이션 없음. 기존 snapshot 포맷도 변경하지 않는다.

## 참고 자료

- 연구 노트: `research/2025-09-28_18-23-53_cache_program_slc_datain_suspend.md`
- 유사 구현: `tests/test_suspend_resume.py`
