---
date: 2025-09-27T18:17:24+0900
git_commit: c42470ff607935cf5d6e586d08e786a29775be86
branch: main
repository: nandseqgen_v2
topic: "Scheduler _handle_op_end suspend skip failure"
tags: [research, codebase, scheduler, resourcemgr, suspend-resume]
status: complete
last_updated: 2025-09-27
---

# 연구: Scheduler _handle_op_end suspend skip failure

**Date**: 2025-09-27T18:17:24+0900
**Git Commit**: c42470ff607935cf5d6e586d08e786a29775be86
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
Scheduler._handle_op_end 에서 SUSPEND 된 PROGRAM 으로 인해 큐에 남아 있던 OP_END 이벤트를 스킵하지 못해 중복으로 실행되는 이유는 무엇인가?

## 요약
- 프로그램이 SUSPEND 되면 기존 OP_END 이벤트가 이벤트 큐에 남아 있지만, RESUME 이후에는 ResourceManager 가 해당 op_uid 를 suspended 집합에서 제거하므로 `_handle_op_end`의 skip 가드가 더 이상 발동하지 않는다.
- `_handle_resume_commit` 이 동일 op_uid 로 새로운 OP_END 를 추가 enqueue 하지만 EventQueue 가 기존 항목을 갱신/삭제할 수 없어, 초기 이벤트가 원래 종료 시각에 그대로 실행된다.
- 재현 실행(`python3 main.py -t 10000`) 결과 `out/op_event_resume.csv`에서 op_uid 1에 대해 두 개의 OP_END 레코드가 생성되는 것을 확인했다(309.82µs, 660.0µs).

## 상세 발견

### Scheduler 이벤트 처리 (`scheduler.py`)
- `_handle_op_end` 는 `rm.is_op_suspended(op_uid)`가 True일 때만 조기 반환한다 (`scheduler.py:271`).
- `ResourceManager`가 RESUME 시 해당 메타를 `_ongoing_ops`로 옮기면 `is_op_suspended`는 False를 돌려 주기 때문에, SUSPEND 시점에 생성된 기존 OP_END 이벤트는 그대로 처리된다.
- `_handle_resume_commit`은 resume된 메타의 `end_us`에 맞춰 같은 op_uid로 새로운 OP_END를 enqueue하지만, 기존 이벤트를 무효화할 수단이 없어 큐에 두 엔트리가 공존한다 (`scheduler.py:485`-`scheduler.py:510`).

### ResourceManager 재개 흐름 (`resourcemgr.py`)
- `resume_from_suspended_axis`는 suspend 스택에서 메타를 pop 한 뒤 `_ongoing_ops`에 append하여 다시 활성 상태로 만든다 (`resourcemgr.py:1825`).
- `is_op_suspended`는 축 별 suspended 스택만 검사하기 때문에, RESUME 직후에는 대상 op_uid에 대해 False를 반환한다 (`resourcemgr.py:1829`).
- 따라서 SUSPEND 중에 생성된 OP_END 이벤트가 RESUME 이후 도착하면 skip 가드가 동작하지 않는다.

### 이벤트 큐 특성 (`event_queue.py`)
- EventQueue는 단순 정렬 리스트로 `push`/`pop`만 제공하며, 기존 항목을 키 기반으로 갱신하거나 제거하는 API가 없다 (`event_queue.py:6`).
- RESUME 시점에 기존 OP_END를 제거할 방법이 없으므로 `_handle_op_end`의 가드가 유일한 방어선이다.

### 재현 로그 관찰
- `python3 main.py -t 10000` 실행 후 `out/op_event_resume.csv`를 확인하면 `Page_Program_SLC` op_uid 1에 대해 `OP_END` 이벤트가 두 번 기록된다(309.82µs, 660.0µs, 앞선 항목은 `is_resumed=True`).
- `out/apply_pgm_log.csv`는 현재 환경에 `numpy`가 설치되어 있지 않아 `Scheduler._am_apply_on_end`가 early-return 하면서 비어 있다; 로그가 없지만 `op_event_resume.csv`로 중복 현상을 확인할 수 있다.

## 코드 참조
- `scheduler.py:271` - `_handle_op_end`가 suspend 여부만 확인하고 나머지 처리를 진행
- `scheduler.py:485` - `_handle_resume_commit`이 동일 op_uid로 새로운 OP_END 이벤트 enqueue
- `scheduler.py:874` - 최초 커밋 시 OP_END 이벤트를 즉시 큐에 push
- `resourcemgr.py:1825` - resume 시 메타를 `_ongoing_ops`로 이동
- `resourcemgr.py:1829` - `is_op_suspended`가 suspended 스택만 검사
- `event_queue.py:6` - EventQueue가 항목 제거/갱신 기능을 제공하지 않음

## 아키텍처 인사이트
- suspend/resume 경로가 Scheduler와 ResourceManager 사이에서 책임을 분담하지만, 이벤트 큐 정리가 빠져 있어 상태 머신이 두 개의 종료 이벤트를 허용한다.
- 이벤트 무효화 전략(큐에서 제거하거나 `_handle_op_end`가 resume된 op_uid도 필터링하도록 개선)이 없으면 suspend/resume을 반복할 때마다 주소 상태 적용과 metrics가 중복될 수 있다.

## 관련 연구
- `research/2025-09-27_15-48-50_scheduler-suspend-resume-op-end.md`

## 미해결 질문
- RESUME 시 기존 OP_END 이벤트를 재스케줄하거나 무효화할 수 있는 큐 관리 API를 추가할 필요가 있는가?
- `_handle_op_end` 가드가 resume 직후의 진행 중(op_uid가 `_ongoing_ops`에만 존재) 상태도 인지하도록 ResourceManager에 보조 쿼리를 추가해야 하는가?
