# apply_pgm Diagnostics Instrumentation 구현 계획

## 개요

SUSPEND → RESUME 시 동일 PROGRAM `op_uid`에 대해 `AddressManager.apply_pgm`이 몇 번 호출되는지 추적하기 위해, Scheduler OP_END 경로를 계측하여 `apply_pgm_log.csv`를 run/site 디렉터리마다 내보내는 기능을 설계한다. 이 로그는 중복 `apply_pgm` 호출 원인을 분석하기 위한 진단 용도로만 활용한다.

## 현재 상태 분석

- Scheduler는 모든 OP_END에서 `_am_apply_on_end`를 호출하고, PROGRAM 계열이면 `addrman.apply_pgm`을 실행한다 (`scheduler.py:269-307`, `scheduler.py:500-562`).
- `_am_apply_on_end`는 PROGRAM 타깃을 ndarray로 변환 후 `am.apply_pgm(addrs, mode=mode)`를 호출하며 호출 횟수나 타이밍을 기록하지 않는다 (`scheduler.py:543-562`).
- `AddressManager.apply_pgm`은 등장 블록마다 `addrstates`를 증가시키므로 중복 호출 시 0→2→4 증가가 발생한다 (`addrman.py:607-629`).
- OP_START/END 이벤트 메타데이터는 `_op_event_rows` 버퍼로 수집되어 `op_event_resume.csv`로 출력되지만, `apply_pgm` 호출 로그는 없다 (`scheduler.py:328-380`, `main.py:1186-1264`, `main.py:182-209`).
- Suspend/Resume 단위 테스트는 `_StubAddrMan.apply_pgm` 호출 횟수를 정수 카운터로만 검증하며 로그 유무는 검사하지 않는다 (`tests/test_suspend_resume.py:42-172`).

## 목표 상태

- 각 `apply_pgm` 호출마다 OP_END 발생 시각(`Scheduler.now_us`), `op_uid`, `op_name`, 대상 좌표(die/plane/block/page), 적용 모드 등을 기록한다.
- 기록된 행을 run/site 루프마다 `apply_pgm_log.csv`로 내보낸다(`out/site_XX/...`).
- 기존 동작(예약/이벤트 처리)은 영향받지 않으며, 진단 기능은 오버헤드가 낮고 기본적으로 항상 활성화된다.

### 핵심 발견:
- `apply_pgm` 호출 시점은 `_am_apply_on_end` 내부이며, Scheduler 컨텍스트(`op_uid`, `self.now_us`)를 활용하면 OP_END 시각과 UID를 정확히 캡처할 수 있다 (`scheduler.py:269-307`, `scheduler.py:543-562`).
- 기존 `_op_event_rows` 구조를 참고하면 Scheduler에 별도 버퍼를 만들어 main 루프에서 CSV로 내보내는 패턴을 재사용할 수 있다 (`scheduler.py:328-380`, `main.py:1186-1267`).
- 테스트 스텁은 `apply_pgm` 호출 횟수를 쉽게 노출하므로, 새 로그 버퍼를 검증하도록 확장하기 용이하다 (`tests/test_suspend_resume.py:42-172`).

## 범위에서 제외되는 항목

- 중복 OP_END/`apply_pgm` 발생 자체를 해결하는 로직 변경 (큐 정리 등) — 추후 별도 계획에서 다룸.
- 기존 `op_event_resume.csv` 형식 변경이나 통합.
- 런타임 설정으로 계측을 토글하는 기능.

## 구현 접근

Scheduler에 진단 전용 버퍼와 드레인 메서드를 추가하고, `_am_apply_on_end`에서 PROGRAM 커밋 시 버퍼에 행을 누적한다. main 실행 루프는 기존 `op_event_resume.csv` 흐름을 참고해 `apply_pgm_log.csv`를 작성한다. 테스트는 스텁 주소 매니저와 suspend/resume 시나리오로 로그를 검증한다.

## 1단계: Scheduler 계측 버퍼 추가 및 OP_END 계측

### 개요
`Scheduler`에 `apply_pgm` 호출 정보를 누적하는 버퍼와 드레인 헬퍼를 추가하고, `_am_apply_on_end`에서 PROGRAM 커밋 시 행을 저장한다.

### 필요한 변경:

#### 1. Scheduler 진단 버퍼
**File**: `scheduler.py`
**Changes**:
- `__init__`에 `self._apply_pgm_rows: List[Dict[str, Any]] = []` 필드 추가.
- 버퍼를 반환/초기화하는 `drain_apply_pgm_rows()` 메서드 구현(기존 `drain_op_event_rows` 패턴 참고).

#### 2. `_am_apply_on_end` 계측
**File**: `scheduler.py`
**Changes**:
- PROGRAM 커밋 분기(`is_program_commit`)에서 `am.apply_pgm` 호출 직전에 대상 ndarray를 활용해 행을 구성.
- 각 타깃 주소에 대해 다음 정보를 저장: `op_uid`, `op_name`, `base`, `celltype`(mode), `die`, `plane`, `block`, `page`, `triggered_us=self.now_us`, `resume_flag`(필요 시 `_resumed_op_uids` 정보 재사용), `call_index`(동일 `op_uid` 누적 카운터용).
- 누적 카운터 유지를 위해 `defaultdict(int)` 혹은 버퍼 삽입 시 count 산출.

#### 3. 재사용 헬퍼 추가(선택)
**File**: `scheduler.py`
**Changes**:
- 중복 코드를 줄이기 위해 `_log_apply_pgm_rows(...)` 내부 헬퍼를 도입해 행 생성/카운터 갱신 담당.

### 성공 기준:

#### 자동 검증:
- [x] `pytest tests/test_suspend_resume.py` 통과.
- [ ] 새로운 type hints/mypy(?) 필요 시 linters 영향 없음.

#### 수동 검증:
- [ ] Suspend→Resume 시나리오에서 `apply_pgm` 호출마다 동일 UID로 행이 누적되는지 로그 확인.

---

## 2단계: CSV 작성 및 main 파이프라인 통합

### 개요
새 버퍼를 run/site 루프에서 비우고 `apply_pgm_log.csv` 파일을 생성한다.

### 필요한 변경:

#### 1. CSV 작성 유틸리티
**File**: `main.py`
**Changes**:
- `_write_apply_pgm_log_csv(rows, out_dir)` 함수 추가 (기존 `_write_op_event_resume_csv` 참고).
- 필드 구성: `triggered_us`, `op_uid`, `op_name`, `base`, `celltype`, `die`, `plane`, `block`, `page`, `resume`, `call_seq`.

#### 2. run/site 루프 통합
**File**: `main.py`
**Changes**:
- `run_once` 호출 후 `sched.drain_apply_pgm_rows()` 실행하여 누적 행을 정렬 후 CSV 작성.
- 멀티-run/site에서도 누적 방식이 맞도록 `site_apply_pgm_rows` 리스트 유지 (OP 이벤트와 동일 패턴).

#### 3. InstrumentedScheduler 호환성 검증
**File**: `main.py`
**Changes**:
- `InstrumentedScheduler`가 새 메서드를 상속받아 문제없이 사용되는지 확인(필요시 override 여부 확인).

### 성공 기준:

#### 자동 검증:
- [x] `pytest` 통과.
- [x] 스모크 실행 `.venv/bin/python main.py --config config.yaml --run-until 100 --out-dir out/tmp` 후 `apply_pgm_log.csv` 생성 확인 (수동 명령 안내).

#### 수동 검증:
- [x] 생성된 CSV 열 순서와 값이 사양과 일치.
- [x] OP 이벤트 CSV와 동일 디렉터리에 저장.

---

## 3단계: 테스트 및 문서 업데이트

### 개요
단위 테스트를 확장해 새로운 로그 버퍼와 CSV 유틸 검증을 포함하고, 관련 연구 문서나 README에 진단 기능을 기록한다.

### 필요한 변경:

#### 1. Suspend/Resume 테스트 확장
**File**: `tests/test_suspend_resume.py`
**Changes**:
- `_StubAddrMan` 또는 새 테스트에서 Scheduler의 `drain_apply_pgm_rows()` 사용.
- Suspend → Resume → OP_END 반복 시 동일 `op_uid`에 대해 행 수와 `call_seq` 증가를 검증.

#### 2. CSV 유틸 단위 테스트(선택)
**File**: `tests/test_suspend_resume.py` 또는 새 테스트 파일
**Changes**:
- `_write_apply_pgm_log_csv`에 대한 간단한 파일 생성 테스트(Temp dir 이용) 추가 가능.

#### 3. 문서 메모
**File**: `research/2025-09-27_15-48-50_scheduler-suspend-resume-op-end.md` 또는 새 노트
**Changes**:
- 새 로그 도입 경로와 사용법 요약 추가(선택, 필요시).

### 성공 기준:

#### 자동 검증:
- [x] 확장된 테스트가 CI에서 안정적으로 통과.

#### 수동 검증:
- [x] 연구 문서/README에 새 로그 파일 설명이 반영되어 팀이 활용법을 이해.

---

## 테스트 전략

### 단위 테스트:
- Suspend/Resume 경로에서 `apply_pgm` 로그 행 수, `call_seq`, `resume` 플래그 점검.
- CSV 작성 함수가 필드와 정렬 규칙을 지키는지 확인.

### 통합 테스트:
- 짧은 메인 실행으로 실제 CSV 출력 확인 (수동/도구).

### 수동 테스트 단계:
1. `.venv/bin/python main.py --config config.yaml --run-until 200 --out-dir out/debug` 실행.
2. `out/debug/apply_pgm_log.csv` 내용을 확인해 중복 UID가 누적되는지 확인.
3. Suspend 없는 시나리오에서 `call_seq`가 1로 유지되는지 비교.

## 성능 고려사항

- 계측은 PROGRAM OP_END마다 O(targets) 행 추가이며, 기존 `_op_event_rows` 수준의 오버헤드로 허용 가능.
- CSV 정렬은 행 수가 많을 경우 비용이 있으나, 진단 목적상 허용(필요시 후속 최적화 고려).

## 마이그레이션 노트

- 신규 파일 출력이므로 기존 워크플로에 영향 없음. 로그 파일은 아카이브/분석 도구가 인식하도록 추가 안내 필요.

## 참고 자료

- 관련 연구: `research/2025-09-27_15-48-50_scheduler-suspend-resume-op-end.md`
- 유사 구현: `scheduler.py:328-380` (`_record_op_event_rows`), `main.py:182-209` (OP 이벤트 CSV 출력)
