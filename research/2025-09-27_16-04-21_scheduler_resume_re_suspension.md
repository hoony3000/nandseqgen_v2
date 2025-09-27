---
date: 2025-09-27T16:04:21.296109+09:00
git_commit: c63331cd5dc037b7077df671c9066d54510d8e68
branch: main
repository: nandseqgen_v2
topic: "Scheduler resume re-suspension loops"
tags: [research, codebase, scheduler, resourcemgr]
status: complete
last_updated: 2025-09-27
---

# 연구: Scheduler resume re-suspension loops

**Date**: 2025-09-27T16:04:21.296109+09:00
**Git Commit**: c63331cd5dc037b7077df671c9066d54510d8e68
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
Scheduler._propose_and_schedule 에서 SUSPEND→RESUME 으로 재예약된 PROGRAM 이 반복적으로 다시 SUSPEND→RESUME 대상이 되는가?

## 요약
- Scheduler 는 `PROGRAM_RESUME` 커밋 직후 `_handle_resume_commit` 을 호출하여 ResourceManager 의 `resume_from_suspended_axis` 로 원본 PROGRAM 메타를 다시 진행 상태로 되돌린다. 별도의 차단 로직이 없어 같은 메타가 이후 제약에 따라 재차 `PROGRAM_SUSPEND` 대상으로 선택될 수 있다.
- ResourceManager 는 `move_to_suspended_axis` 와 `resume_from_suspended_axis` 에서 남은 실행 시간(`remaining_us`)과 소비 시간을 누적 관리하며, 재개된 메타를 `_ongoing_ops` 에 재삽입한다. 이 구조는 동일 PROGRAM 이 여러 번 suspend→resume 되는 것을 허용한다.
- 단위 테스트(`test_resource_manager_repeat_suspend_updates_remaining_us`) 는 동일 `op_id` 가 suspend→resume→suspend 흐름을 거치며 `remaining_us` 가 줄어드는 것을 검증해 반복 suspend/resume 시나리오가 정상 경로임을 보여준다. 반복을 막는 상위 정책은 현재 없다.

## 상세 발견

### Scheduler Resume Handling
- `scheduler.py:773` 에서 커밋된 레코드를 순회하며 `_handle_resume_commit` 을 호출한다. 여기에는 `PROGRAM_RESUME` 레코드도 포함되어 즉시 재예약이 시도된다.
- `_handle_resume_commit` 은 `resume_from_suspended_axis` 를 호출하여 프로그램 메타를 `_ongoing_ops` 로 복구하고(`scheduler.py:445`), 그 종료 시각에 새 `OP_END` 이벤트를 큐잉한다(`scheduler.py:498`).
- `_tracking_axis` 는 `PROGRAM_*` (SUSPEND/RESUME 제외) 에만 `PROGRAM` 축을 부여하므로, resume 이후에도 동일 `op_uid` 를 가진 PROGRAM 이 다시 suspend 후보가 될 수 있다(`scheduler.py:235`).

### ResourceManager Re-suspension Mechanics
- `move_to_suspended_axis` 는 진행 중인 PROGRAM 메타를 꺼내 소비된 시간만큼 상태를 잘라내고 남은 시간을 `remaining_us` 로 기록한다(`resourcemgr.py:1531`, `resourcemgr.py:1595`).
- suspend 시 plane/window 예약을 자르고 `_suspended_ops_program` 스택에 메타를 push 하여 이후 resume 시점에 꺼낼 수 있게 한다(`resourcemgr.py:1608`, `resourcemgr.py:1697`).
- `resume_from_suspended_axis` 는 재예약 성공 시 메타를 `_ongoing_ops` 목록에 다시 추가해 이후에도 동일 메타가 suspend 대상이 되도록 허용한다(`resourcemgr.py:1816`, `resourcemgr.py:1825`).

### Test Evidence
- `tests/test_suspend_resume.py:69` 는 동일 PROGRAM 의 suspend→resume→재-suspend 경로에서 `remaining_us` 가 30→20 으로 감소함을 검증한다.
- 같은 테스트는 재개 시 plane 예약이 새 구간으로 확장되었다가 재-suspend 시 다시 잘리는 것을 확인하여 반복 suspend/resume 흐름이 설계된 동작임을 보여준다(`tests/test_suspend_resume.py:99`, `tests/test_suspend_resume.py:108`).

## 코드 참조
- `scheduler.py:773` – 커밋된 모든 op 레코드에서 `_handle_resume_commit` 호출
- `scheduler.py:445` – `resume_from_suspended_axis` 호출로 PROGRAM 메타 재개
- `scheduler.py:498` – 재개된 메타 종료 시각에 `OP_END` 이벤트 push
- `resourcemgr.py:1595` – suspend 시 남은 시간 기록
- `resourcemgr.py:1816` – resume 성공 후 메타를 `_ongoing_ops` 에 재삽입
- `tests/test_suspend_resume.py:69` – 반복 suspend/resume 단위 테스트

## 아키텍처 인사이트
- Scheduler 는 RESUME 이후 별도 방어 장치를 두지 않아 suspend 정책은 전적으로 proposer/ResourceManager 의 결정에 의존한다.
- ResourceManager 는 남은 시간과 소비 시간을 누적 관리해 반복 suspend/resume 시에도 일관된 종료 이벤트를 유지하지만, 잦은 suspend 는 동일 PROGRAM 의 여러 `OP_END` 지연을 유발할 수 있어 상위 정책 제어가 필요하다.

## 관련 연구
- `research/2025-09-26_13-02-33_suspend_state_op_end.md`
- `research/2025-09-24_14-45-00_scheduler_resume_state.md`
- `research/2025-09-22_01-07-35_repeat_suspend_remaining_us_regression.md`

## 미해결 질문
- proposer 가 suspend 재요청을 얼마나 자주 생성하는지, 그리고 이를 제한할 정책이 있는지 추가 분석이 필요하다.
- 반복 suspend 가 이벤트 큐 지연 또는 주소 관리자 동기화에 미치는 영향에 대한 시뮬레이션이 필요하다.
