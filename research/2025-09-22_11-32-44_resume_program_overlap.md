---
date: 2025-09-22T11:32:44.345206+09:00
researcher: Codex
git_commit: 63127c53461d485275ba33e6b4e4a4617100106d
branch: main
repository: nandseqgen_v2
topic: "Program resume reservation overlap"
tags: [research, codebase, scheduler, resourcemgr, suspend-resume]
status: complete
last_updated: 2025-09-22
last_updated_by: Codex
last_updated_note: "미해결 질문 답변 반영"
---

# 연구: Program resume reservation overlap

**Date**: 2025-09-22T11:32:44.345206+09:00
**Researcher**: Codex
**Git Commit**: 63127c53461d485275ba33e6b4e4a4617100106d
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
RESUME 된 PROGRAM 이 OP_END 가 끝나기 전에 또다른 PROGRAM 이 예약되는 현상이 RESUME 진행 중에서만 발생하는지, 아니면 일반적인 상황에서도 발생하는지 확인한다.

## 요약
- Page_Program_SLC 의 OP_START 가 직전 OP_END 이전에 들어온 사례는 모두 `is_resumed=True` 인 resume 종료 직후였다. 정상 흐름에서는 OP_START 가 항상 선행 OP_END 이후에 배치된다.
- Scheduler 는 resume 시 OP_END 만 재주입하고 ResourceManager 는 잔여 구간에 대한 plane 예약을 복원하지 않아 후속 PROGRAM 예약이 조기 통과한다.
- 사양 검토 결과 resume 중에는 일반 PROGRAM 과 동일하게 리소스가 점유되어야 하므로, resume OP_END 이전에는 다른 PROGRAM 이 예약되면 안 된다. 현재 동작은 의도에 어긋나는 버그로 확인됐다.

## 상세 발견

### Event Log 분석
- `Page_Program_SLC` 4번 재개가 15260us 에 끝나기 전에 5번이 15200us 에 시작하여 동일 plane 을 겹친다 (`out/op_event_resume.csv:17`, `out/op_event_resume.csv:18`).
- 동일 패턴이 8→9, 11→12 사이에서도 반복되며, 겹친 직전 OP_END 는 모두 `is_resumed=True` 로 표시된다 (`out/op_event_resume.csv:41`, `out/op_event_resume.csv:42`, `out/op_event_resume.csv:59`, `out/op_event_resume.csv:60`).
- Resume 이전 구간(예: 3→4)은 OP_START 가 직전 OP_END 이후에 배치되며 중첩이 없다 (`out/op_event_resume.csv:11`, `out/op_event_resume.csv:12`).

### Scheduler resume 플로우
- `_handle_resume_commit` 은 resume 메타를 꺼내 `_eq` 에 OP_END 이벤트만 push 하며 OP_START/재예약은 생성하지 않는다 (`scheduler.py:392`).
- 재개 대상 op_uid 만 추적 세트에 넣은 뒤 종료 시점까지 기다리므로 이벤트 로그에는 `is_resumed=True` 인 OP_END 만 남는다 (`scheduler.py:458`).

### Resource Manager resume 처리
- PROGRAM 커밋 시 `_plane_resv` 에 plane 창이 기록되고 `_ongoing_ops` 메타가 등록된다 (`resourcemgr.py:577`, `resourcemgr.py:1040`).
- Suspend 시 `_ongoing_ops` 항목이 `_suspended_ops_program` 으로 이동하지만 기존 plane 예약을 축소하거나 재구성하지 않는다 (`resourcemgr.py:1087`).
- `resume_from_suspended_axis` 는 잔여 시간만 재계산해 `_ongoing_ops` 로 복귀시키며 plane 예약은 복원하지 않는다 (`resourcemgr.py:1153`, `resourcemgr.py:1185`). 이로 인해 ResourceManager 는 plane 이 비어 있다고 판단하여 새로운 PROGRAM 예약을 통과시킨다.

## 코드 참조
- `out/op_event_resume.csv:17`
- `out/op_event_resume.csv:41`
- `out/op_event_resume.csv:59`
- `scheduler.py:392`
- `resourcemgr.py:1087`
- `resourcemgr.py:1185`

## 아키텍처 인사이트
- plane 예약은 commit 시 1회만 생성되며 suspend/resume 경로에서는 재적용되지 않는다. 설계 의도는 resume 중에도 해당 plane 을 점유한 상태를 유지하는 것이므로, 예약 복원 또는 스케줄러 차단 로직이 필요하다.
- Resume 플래그(`is_resumed`) 는 중첩 진단에 유용하므로 후속 테스트에서 회귀 여부 검출 지표로 사용할 수 있다.

## 역사적 맥락(thoughts/ 기반)
- 기존 resume 연구에서 plane 예약 재적용/차단 전략을 어떻게 처리했는지 추가 검토 필요(예: 2025-09-22_00-22-10_resume_stub_rework.md).

## 관련 연구
- 2025-09-22_00-22-10_resume_stub_rework.md – resume 경로 재작업 기록.

## 후속 연구 2025-09-22T11:40:30.015217+09:00
- 사양 검토 결과 resume 로 재스케줄된 PROGRAM 이 끝나기 전에는 동일 die/plane 의 다른 PROGRAM 이 허용되지 않아야 함을 확인했다. 현재 구현은 이를 위반하므로 `_plane_resv` 복원 또는 스케줄러 차단 개선이 필요하다.
- ResourceManager 는 resume 된 작업에 대해서도 일반 PROGRAM 과 동일한 방식으로 리소스를 재예약해야 하며, 이는 의도된 동작과 맞지 않는 현행 구현 차이로 분류된다.

## 미해결 질문
없음
