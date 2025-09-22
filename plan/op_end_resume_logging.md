# OP_END Resume Logging & Page Verification Plan

## Problem 1-Pager
- **배경**: Alt C 재개 전략 적용 이후 Scheduler 는 `_handle_op_end`(scheduler.py:253)에서 `ResourceManager.is_op_suspended`를 조회하여 중복 종료 이벤트를 건너뛰고, `PROGRAM_RESUME` 커밋 시 `_handle_resume_commit`(scheduler.py:284)이 원본 OP_END 를 재스케줄한다. 실행 로그가 부족해 SUSPEND→RESUME 흐름의 실제 종료 대상과 재사용한 page 주소를 추적하기 어렵다.
- **문제**: 반복적인 SUSPEND→RESUME 시나리오에서 최종 PROGRAM OP_END 시점의 page 주소가 기대(연속 증가 & 재개 시 동일 page)와 다를 가능성이 보고됐으나, 이를 확인하거나 원인을 좁힐 수 있는 데이터가 없다. 또한 현재 실행 파이프라인(main.py:1032 이후)은 해당 정보를 CSV로 내보내지 않는다.
- **목표**: Scheduler tick 과정에서 OP_END 가 처리될 때마다 (1) 재개 여부(`is_resumed`)를 정확히 식별하고, (2) 대상 주소(die, plane, block, page)를 포함한 로그를 수집/CSV로 출력하여 비정상 샘플링을 진단할 수 있도록 한다. 재개된 OP_END 가 초기 예약 주소와 불일치하면 감지할 수 있는 진단 정보도 수집한다.
- **비목표**: proposer 로직 전면 개편, AddressManager 의 샘플링 정책 변경, Resume 전략(Alt C) 자체 재설계.
- **제약**: 함수 ≤ 50 LOC, 파일 ≤ 300 LOC 유지. 기존 metrics/CSV 포맷 유지(신규 파일만 추가). 비 ASCII 금지. `.venv` 내 파이썬 실행. 테스트 결정적 유지.

## 현재 상태 분석
- `_handle_op_end`는 `op_uid`가 RM에 아직 suspend로 남아 있으면 조기 return 하여 AM 동기화를 건너뛴다(scheduler.py:253~281).
- `_handle_resume_commit`은 `rm.resume_from_suspended_axis`가 돌려준 `meta`로 OP_END 이벤트를 재push하지만, 재개 여부를 추적하거나 로그화하지 않는다(scheduler.py:329~345).
- InstrumentedScheduler(main.py:80~170)는 예약 시점 정보를 별도 row 형태로 수집하지만 OP_END 로그는 없다.
- RM은 `resume_from_suspended_axis`에서 meta.targets 를 그대로 돌려주므로 원본 주소 비교가 가능(resourcemgr.py:1153~1186). 그러나 Scheduler 측에서 해당 정보를 저장하지 않는다.
- CLI는 run 완료 후 여러 CSV 를 `out_dir`에 작성하지만 OP_END / resume 특화 로그는 없음(main.py:1105~1180).

## 대안 비교
1. **Scheduler 내부 컬렉션 + post-run flush** *(선택안)*
   - 장점: 기존 Scheduler/RM 인터페이스 변경 없이 `op_uid` 기준으로 재개 여부와 주소 기록 가능. 재개 시 `_handle_resume_commit`에서 즉시 `op_uid`를 표기해 후속 비교 수행.
   - 단점: Scheduler 가 로그 컬렉션을 보관해야 하므로 메모리 사용 증가 가능.
   - 위험: 다중 run에서 flush 순서를 잘못 처리하면 CSV 덮어쓰기 요구를 어길 수 있음.
2. **ResourceManager 에 재개 플래그/조회 API 추가**
   - 장점: 재개 여부를 RM이 단일 출처로 관리해 일관성 유지.
   - 단점: RM 공개 API 확장 필요, suspend/resume 경로 모두 수정. Scheduler 외 다른 호출자 영향 범위 커짐.
   - 위험: RM 상태머신 복잡도 증가, 기존 테스트에 영향 가능.

→ Scheduler 쪽에서 필요한 최소 상태만 추적하여 post-run 에서 CSV 를 생성하는 1안을 채택한다.

## 구현 접근
1. Scheduler 에 OP_END 로깅 전용 구조 추가
   - `_resumed_op_uids: set[int]`로 재스케줄 여부 추적.
   - `_resume_expected_targets: Dict[int, List[Tuple[int,int,int,int]]]` 형태로 `_handle_resume_commit` 시점의 주소 스냅샷 저장 (die, plane, block, page).
   - `_op_end_rows: List[Dict[str, Any]]`를 두고 `_handle_op_end`에서 per-target 로우를 축적.
   - `is_resumed` 여부는 `_resumed_op_uids` membership 으로 판별 후 처리 후 discard.
   - PROGRAM family 종료 시 `AddressManager` 적용 전에 기대 주소와 실제 payload.targets 를 비교해 mismatch 카운터를 메트릭에 기록 (`metrics['program_resume_page_mismatch']`).
2. CSV 플러시 지원
   - `Scheduler`에 `consume_op_end_rows()` 같은 공개 메서드 추가 (리스트 반환 & 내부 리스트 초기화).
   - InstrumentedScheduler 는 상속 그대로 사용 가능하도록 base 구현을 활용.
3. CLI(main.py)에서 run 루프 뒤 `op_event_resume.csv` 작성 (실행마다 덮어쓰므로 루프 밖에서 한 번만 실행). 필드 순서는 요구사항 대로 고정.
4. 테스트 추가
   - `_handle_op_end`가 suspend 중에는 기록하지 않고, resume 이후 기록/플래그 업데이트 되는지 검증.
   - 재개 시 저장된 기대 주소와 실제 주소 비교가 false mismatch 로 계산되지 않는지 확인.
   - main 경량 실행 없이 `Scheduler` 단위 테스트로 컬렉션 behaviour 확인.

## 범위에서 제외되는 항목
- proposer 의 주소 샘플링 로직 변경.
- AddressManager.apply_pgm 내부 구현 변경.
- CSV 이외의 새로운 출력 채널 추가 (JSON 등).

## 구현 단계
1. **Scheduler 확장**
   - 속성 초기화 (`_resumed_op_uids`, `_resume_expected_targets`, `_op_end_rows`).
   - `_handle_resume_commit`에서 meta.targets 를 tuple 리스트로 보관하고 `_resumed_op_uids` 갱신.
   - `_handle_op_end`에서 per-target dict 생성 (`op_name`, `op_id`, `op_uid`, `die`, `plane`, `block`, `page`, `is_resumed`) 및 mismatch 메트릭 계산.
   - 새 공개 메서드 `drain_op_event_rows()` 추가.
2. **CLI 연동**
   - `run_once` 호출 이후 InstrumentedScheduler 로부터 rows 추출.
   - 모든 run 데이터 합쳐 `out_dir/op_event_resume.csv`에 `w` 모드로 저장 (헤더 포함).
3. **테스트 작성/갱신**
   - `tests/test_suspend_resume.py`에 재개 플로우 단위 테스트 추가 (stub RM/addrman 활용) → 로우 생성 및 `is_resumed` 판별 검증.
   - mismatch 메트릭이 기대대로 동작하는지 간단한 시나리오 포함.

## 테스트 전략
- 단위 테스트: `tests/test_suspend_resume.py`에 신규 케이스 추가.
- 수동: (선택) 시뮬레이터 실행 후 `op_event_resume.csv` 눈으로 확인.

## 성공 기준
### 자동 검증
- [ ] `python -m unittest tests.test_suspend_resume` 통과.

### 수동 검증
- [ ] `out_dir/op_event_resume.csv`가 실행마다 재생성되고 요구 필드를 모두 포함함을 확인.
- [ ] 재개된 OP_END 의 페이지가 초기 예약과 다르면 mismatch 메트릭이 증가함을 확인 (필요 시 로그 출력).

## 참고
- Suspend/Resume 로직 요약: `plan/resume_stub_alt_c_rework.md`.
- RM 재개 메타 구조: `resourcemgr.py:1153` 부근.
