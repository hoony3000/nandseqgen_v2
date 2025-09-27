---
date: 2025-09-27T15:48:50+0900
git_commit: c63331cd5dc037b7077df671c9066d54510d8e68
branch: main
repository: nandseqgen_v2
topic: "Scheduler resume duplicate OP_END"
tags: [research, codebase, scheduler, resourcemgr]
status: complete
last_updated: 2025-09-27
---

# 연구: Scheduler resume duplicate OP_END

**Date**: 2025-09-27T15:48:50+0900
**Git Commit**: c63331cd5dc037b7077df671c9066d54510d8e68
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
Scheduler._propose_and_schedule 에서 SUSPEND → RESUME 으로 재예약된 PROGRAM 의 OP_END 이벤트가 중복으로 발생하는 원인은 무엇인가?

## 요약
- 초기 PROGRAM 커밋 때 생성된 OP_END 이벤트가 큐에 남아 있는 상태에서 재개가 완료되면 동일 op_uid 에 대한 새로운 OP_END 가 추가로 스케줄된다.
- 재개 시점에는 ResourceManager 가 해당 op_uid 를 더 이상 "suspended" 로 보고하지 않아 기존 이벤트가 무조건 실행되며, 중복 실행을 방지하는 정리가 없다.

## 상세 발견

### Scheduler 이벤트 큐
- `scheduler.py:738` 첫 커밋에서 모든 PROGRAM 계열 예약에 대해 OP_START/OP_END 이벤트를 즉시 큐에 넣는다.
- `scheduler.py:269` OP_END 처리 시 `rm.is_op_suspended(op_uid)` 체크로 일시중지 상태만 건너뛰는데, RESUME 이후에는 False 를 반환하므로 기존 이벤트가 그대로 소비된다.
- `scheduler.py:498` RESUME 커밋 시 `_handle_resume_commit` 이 동일 op_uid 로 새로운 OP_END 이벤트를 추가하지만 기존 큐 엔트리를 제거하거나 갱신하지 않는다.

### ResourceManager 재개 흐름
- `resourcemgr.py:1726` RESUME 요청에서 중단된 메타를 꺼내 새 예약을 만들고 `_ongoing_ops` 리스트로 옮겨 `is_op_suspended` 에서 제외시킨다.
- `resourcemgr.py:1829` 이후 `is_op_suspended` 가 False 를 돌려 Scheduler 가 첫 번째(OP_END) 이벤트를 정상 처리하게 되어, 재예약된 종료 이전에 기존 종료가 먼저 실행된다.

### EventQueue 특성
- `event_queue.py:6` EventQueue 는 단순 정렬 리스트이며 기존 항목을 제거하거나 업데이트하는 API 가 없어 RESUME 이전에 스케줄된 OP_END 엔트리가 그대로 남는다.

### 재개 메타 진단 버퍼 소거
- `scheduler.py:480` 재개 시 `_resumed_op_uids` 가 세팅되지만 첫 번째 OP_END 처리에서 바로 discard 되기 때문에 중복 탐지 신호로 남지 않는다.

## 코드 참조
- `scheduler.py:269` - OP_END 핸들러가 suspend 상태만 건너뛰는 구조
- `scheduler.py:498` - RESUME 커밋이 새 OP_END 이벤트를 enqueue
- `scheduler.py:738` - 최초 PROGRAM 커밋 시 OP_END 이벤트 생성
- `resourcemgr.py:1726` - resume_from_suspended_axis 가 메타를 ongoing 으로 이동
- `resourcemgr.py:1829` - is_op_suspended 가 RESUME 후 False 반환
- `event_queue.py:6` - EventQueue 가 단순 push/pop 로만 동작

## 아키텍처 인사이트
- suspend 이후 재개가 원래 스케줄된 종료 이벤트를 무효화하지 않아 이벤트 큐 정합성이 깨질 수 있다.
- 재개 로직이 ResourceManager 상태만 갱신하고 Scheduler 큐 정리는 수행하지 않아 두 컴포넌트 사이의 책임 분리가 모호하다.

## 새 진단 로그
- Scheduler `_am_apply_on_end` 계측으로 PROGRAM 커밋 시마다 `apply_pgm_log.csv`에 `call_seq`, `resume` 플래그, 대상 좌표가 기록된다.
- 로그는 run/site `out/` 디렉터리마다 출력되어 중복 `apply_pgm` 호출 여부를 즉시 확인할 수 있다.

## 관련 연구
- 없음

## 미해결 질문
- EventQueue 에서 동일 op_uid OP_END 를 갱신/제거하는 보완책이 필요한지 확인해야 한다.
- RESUME 커밋 시 기존 종료 이벤트의 시간을 새 종료로 업데이트하는 방식이 가능한지 검토가 필요하다.
