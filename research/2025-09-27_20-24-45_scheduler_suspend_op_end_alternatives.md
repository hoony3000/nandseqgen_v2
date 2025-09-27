---
date: 2025-09-27T20:24:17+0900
git_commit: c42470ff607935cf5d6e586d08e786a29775be86
branch: main
repository: nandseqgen_v2
topic: "Alternatives for scheduler suspend OP_END cleanup"
tags: [research, codebase, scheduler, event_queue, resourcemgr]
status: complete
last_updated: 2025-09-27
---

# 연구: Alternatives for scheduler suspend OP_END cleanup

**Date**: 2025-09-27T20:24:17+0900
**Git Commit**: c42470ff607935cf5d6e586d08e786a29775be86
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
Questions
1. Should we pursue the “queue handle + cancel API” approach from the research notes (extend EventQueue.push to return a token and add remove(...)) or is a different cancellation strategy preferred?
2. How would you like RM to surface the suspended op identifiers? For example, expand move_to_suspended_axis to return the meta, or add a small “consume suspended ids” buffer that Scheduler reads after commits?
위 Questions 1, 2 번 항목(이벤트 큐 취소 설계 및 ResourceManager suspend 메타 노출)에 대한 대안을 찾아라.

## 요약
- 이벤트 취소를 위해 큐 API 를 확장하지 않고도, `Scheduler` 가 최신 종료 시각 버전을 추적해 오래된 `OP_END` 이벤트를 건너뛰는 "버전드 가드" 전략을 적용할 수 있다. 이는 작은 dict 추가와 payload 확장만으로 해결된다.
- ResourceManager 가 별도 버퍼를 노출하지 않아도, `Scheduler` 가 `suspended_ops_program/erase` 스냅샷을 커밋 전후로 비교해 새로 이동된 `op_id` 를 도출하는 차집합 기반 접근이 가능하다.
- 양쪽 대안 모두 기존 퍼블릭 API 변경을 최소화하지만, 정확도와 성능을 위해 추가 메트릭 및 테스트가 필요하다.

## 상세 발견

### EventQueue 취소 대안: 버전드 OP_END 가드
- `Scheduler._emit_op_events` 는 현재 `OP_END` 를 enqueue 하면서 `end_us` 를 payload 에 포함하지 않는다 (`scheduler.py:829-875`). 재개 시점에도 동일 구조를 사용한다 (`scheduler.py:510`).
- `Scheduler._handle_op_end` 는 suspend 여부만 확인하고 나머지 처리를 수행한다 (`scheduler.py:271-317`). 여기에 "최신 종료 버전" 검사를 추가하면 stale 이벤트를 무시할 수 있다.
- 제안: `payload_end = {..., "scheduled_end_us": float(rec["end_us"]), "op_uid": ...}` 형태로 종료 시각을 포함하고, `self._op_end_expect[op_uid] = scheduled_end_us` 를 최신 값으로 유지한다. 재개 시 `_handle_resume_commit` 이 push 하기 전에 동일 dict 를 갱신한다 (`scheduler.py:485-510`).
- `_handle_op_end` 는 처리 전에 `expected = self._op_end_expect.get(op_uid_int)` 를 비교하고, `expected is not None` 이고 `abs(expected - payload['scheduled_end_us']) > SIM_RES_US` 일 경우 stale 로 판단, 곧바로 반환한다.
- 최종 이벤트를 정상 처리한 뒤 `self._op_end_expect.pop(op_uid_int, None)` 로 정리하면 중복 실행을 방지한다. 이 흐름은 `EventQueue` API 를 손대지 않고 scheduler 내부 상태만 확장한다.
- 영향: 스토리지 오버헤드는 추적 대상 op 수에 비례하며, 측정 가능한 지연 없이 stale 이벤트를 무시한다. 그러나 payload 형식 변경에 따라 테스트 목업과 CSV export (`scheduler.py:335-393`) 를 업데이트해야 한다.

### EventQueue 취소 대안: tick-time sweep with cached latest end
- 보조/단위 테스트에서 stale 이벤트를 야삽하는 기존 `_drain_pending_op_end_events` 가 내부 큐에 직접 접근한다 (`scheduler.py:197-222`). 이를 일반 tick 에도 활용해 runtime 에 stale 이벤트를 필터링할 수 있다.
- 제안: tick 루프 시작 시 `self._eq.pop_time_batch()` 전에 `_drop_stale_op_end()` 를 호출해 `_op_end_expect` 의 현재 값과 일치하지 않는 `OP_END` 항목을 O(n) 스캔으로 제거한다. 큐 직접 조작은 이미 드레인로직에서 사용 중이며, 성능 부담은 suspend 빈도에 따라 결정된다.
- 장점: `_handle_op_end` 를 수정하지 않아도 되고, payload 포맷 변경 없이 `self._op_end_expect` (start 시점에서 채움) 만으로 정리가 가능하다. 단, `EventQueue` 내부 접근을 공식화하거나 헬퍼 메서드를 제공해야 유지보수성이 좋아진다.

### ResourceManager suspend 메타 노출 대안: 차집합 기반 동기화
- `ResourceManager.suspended_ops_program` 및 `suspended_ops_erase` 는 공개 API 로 모든 suspend 스택을 반환한다 (`resourcemgr.py:1295-1327`). 커밋 직후, `Scheduler` 는 이 리스트 길이를 즉시 조회할 수 있다.
- `Scheduler._propose_and_schedule` 는 commit 성공 시 `resv_records` 를 순회한다 (`scheduler.py:772-810`). 여기서 `PROGRAM_SUSPEND` 나 `ERASE_SUSPEND` 를 감지하면, 커밋 직전 snapshot 과의 차이를 이용해 새로 이동된 `op_id` 들을 확인할 수 있다.
- 제안: `self._suspend_snap[(axis, die)]` 에 commit 전에 `rm.suspended_ops_program(die)` 의 `op_id` 집합을 저장하고, commit 직후 다시 조회해 `new_ids = current_ids - previous_ids` 를 계산한다. 각 `op_id` 에 대해 `_op_end_expect` 혹은 `_pending_op_end_events` 를 정리한다.
- 이 방식은 RM 변경 없이도 작동하지만, 정렬된 리스트 전체를 복제하므로 suspend 스택이 길 경우 비용이 커질 수 있다. 다이 단위 suspend 빈도가 낮다면 허용 가능한 오버헤드다.

### ResourceManager suspend 메타 대안: move_to_suspended_axis hook
- `move_to_suspended_axis` 내부는 meta 를 pop 한 후 plane/window 를 잘라낸다 (`resourcemgr.py:1531-1698`). 여기에서 콜백 훅을 등록해 Scheduler 가 직접 통지 받을 수도 있다.
- 제안: RM 에 `register_suspend_listener(callable)` 을 추가하고, move 함수 마지막에서 `listener(axis=fam, die=die, meta=meta)` 를 호출한다. Scheduler 는 초기화 시 이 리스너를 등록하여 `op_id` 와 `die` 를 즉시 기록하고, commit 루프가 끝난 뒤 `_cancel_op_end_for(op_id)` 를 호출한다.
- 장점: 리스트 diff 가 필요 없고, meta 객체를 그대로 전달받을 수 있다. 단점: RM 이 Scheduler 에 의존하는 역방향 콜백이 생겨 결합도가 증가하며, 멀티 리스너 관리 및 스레드/테스트 격리가 추가 요구된다.

## 코드 참조
- `scheduler.py:271-317` – `_handle_op_end` suspend 가드 및 OP_END 후처리
- `scheduler.py:829-875` – `_emit_op_events` 가 OP_END payload 를 구성하는 위치
- `scheduler.py:485-510` – `_handle_resume_commit` 이 재개된 메타로 새 OP_END 를 큐에 넣는 경로
- `scheduler.py:197-222` – `_drain_pending_op_end_events` 가 이벤트 큐를 직접 조작하는 예시
- `resourcemgr.py:1295-1327` – `suspended_ops_program/erase` 퍼블릭 API
- `resourcemgr.py:1531-1698` – `move_to_suspended_axis` 가 meta 를 옮기는 과정
- `event_queue.py:17-33` – EventQueue push/pop 인터페이스

## 아키텍처 인사이트
- Scheduler 는 suspend/resume 경로의 대부분을 이미 추적(dict 기반)으로 해결하고 있어, 동일한 패턴으로 stale 이벤트를 무시하는 것이 자연스럽다.
- ResourceManager 가 풍부한 조회 API 를 제공하므로, Scheduler 가 자체적으로 차집합을 계산하는 방법은 기존 책임 경계를 유지한다. 반면 콜백 방식은 결합도를 높이나 suspend 이벤트의 즉시성을 확보한다.
- EventQueue 수준에서 API 를 확장하기보다 상위 레이어에서 버전 관리를 수행하면, 큐를 단순한 시간 기반 컨테이너로 유지할 수 있다.

## 관련 연구
- `research/2025-09-27_18-48-25_scheduler_suspend_op_end_cleanup.md`
- `research/2025-09-27_18-17-24_scheduler-op-end-suspend-resume.md`
- `research/2025-09-27_16-04-21_scheduler_resume_re_suspension.md`

## 미해결 질문
- 버전드 가드 적용 시, resume 이후 OP_END 지연이 길어져 `scheduled_end_us` 가 재조정되는 추가 시나리오가 있는지 시뮬레이션이 필요하다.
- 차집합 기반 접근법의 비용을 줄이기 위해, suspend 스택이 커질 때 메타데이터 샘플링이나 lazy 동기화를 도입해야 할지 검토가 필요하다.
- 콜백 방식 선택 시 ResourceManager – Scheduler 사이의 의존성을 어떻게 일반화할지(예: 이벤트 버스) 추가 설계가 요구된다.
