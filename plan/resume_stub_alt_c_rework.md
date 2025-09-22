# Alt C Resume Stub Rework Plan

## Problem 1-Pager
- **배경**: 기존 RESUME 체인은 Scheduler 가 `_build_core_busy_stub`으로 잔여 CORE_BUSY 작업을 재예약하여 EventQueue 에 중복 `OP_END` 이벤트가 발생했고, AddressManager 가 동일 PROGRAM 작업을 두 번 커밋하며 page address 가 비정상적으로 증가했다.
- **문제**: Suspend 후 Resume 시 `apply_pgm` 및 `OP_END` 가 중복 실행되어 programmed page 수가 실제보다 크게 증가하고, remaining_us 메타가 반복 suspend 시 0/음수로 붕괴한다.
- **목표**: SUSPEND_RESUME_RULES 에 따라 suspend 시 종료 이벤트를 연장하고 resume 시 남은 시간만큼만 재스케줄하여 OP_END 가 단 한 번만 실행되도록 Alt C 전략을 구현한다.
- **비목표**: proposer, AddressManager 내부 데이터 모델 전면 교체, EventQueue 의 구조적 교체.
- **제약**: UID 안정성 보장, remaining_us 재설정, 기존 metric/로그 호환 유지, 함수 ≤ 50 LOC 준수.

## 대안 비교
1. **체인 스텁 유지 + 가드 추가**
   - 장점: 구조 변경 최소화
   - 단점: Resume 체인이 계속 OP_END 를 재등록해 중복 가능성 남음
   - 위험: 가드 조건 실수 시 정상 OP_END 까지 드롭
2. **이벤트 재스케줄 (Alt C)** *(선택)*
   - 장점: Suspend 중 기존 OP_END 를 연장하고 Resume 시 동일 UID 로 재스케줄 → 중복 제거
   - 단점: UID 관리, Resumable 메타 갱신, RM API 확장 필요
   - 위험: UID 미전파/remaining_us 갱신 실패 시 재현 가능

## 구현 개요
- **UID 부여**: Scheduler 가 `_next_op_uid` 로 모노토닉 UID 생성, `_tracking_axis` 로 ERASE/PROGRAM 선별 후 ResourceManager.register_ongoing 에 UID 전달. Event payload 에 `op_uid` 추가.
- **Suspend 처리**: `move_to_suspended_axis` 가 `remaining_us` 와 `suspend_time_us` 양자화, meta.axis 기록.
- **Resume**: `resume_from_suspended_axis` 가 now+remaining 으로 start/end 재설정, remaining_us 초기화, meta 반환. Scheduler 는 `_handle_resume_commit` 으로 이 메타의 `end_us` 시각에 OP_END 재push.
- **OP_END 처리**: `_handle_op_end` 가 UID 기준으로 suspend 상태면 early return. 정상 종료 시 RM.complete_op 호출.
- **RM 헬퍼**: `is_op_suspended`, `complete_op`, `_axis_for_base` 등 보조 API 추가.

## 구현 단계
1. Scheduler
   - `_op_uid_seq`, `_next_op_uid`, `_tracking_axis` 추가
   - `_emit_op_events` payload 확장, `_handle_op_end`에서 suspend 체크, `_handle_resume_commit` 신설
   - 체인 스텁 관련 코드 및 metric 제거, resume 후 `_handle_resume_commit` 호출로 교체
2. ResourceManager
   - `_OpMeta.axis` 추가
   - `register_ongoing`, `move_to_suspended_axis`, `resume_from_suspended_axis` 의 remaining_us/start/end 로직 갱신
   - `is_op_suspended`, `complete_op` 노출
3. Tests
   - `tests/test_suspend_resume.py` 작성: RM 반복 suspend/resume, Scheduler OP_END guard 단위 테스트
4. Docs
   - `docs/SUSPEND_RESUME_RULES.md` 준수 확인 (수정 없음)

## 검증
- 단위: `python -m unittest tests.test_suspend_resume`
- 수동: AddressManager 에 중복 apply 없는지 리뷰, remaining_us 양수 유지 확인
- 향후: 통합 시뮬레이션 시나리오로 확장 검증 권장
