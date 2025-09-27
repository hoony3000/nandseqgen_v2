---
date: 2025-09-27T18:48:25+09:00
git_commit: c42470ff607935cf5d6e586d08e786a29775be86
branch: main
repository: nandseqgen_v2
topic: "Scheduler suspend OP_END cleanup"
tags: [research, codebase, scheduler, resourcemgr, event_queue]
status: complete
last_updated: 2025-09-27
last_updated_by: codex
last_updated_note: "미해결 질문 후속 연구 추가"
---

# 연구: Scheduler suspend OP_END cleanup

**Date**: 2025-09-27T18:48:25+09:00
**Git Commit**: c42470ff607935cf5d6e586d08e786a29775be86
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
Scheduler._propose_and_schedule 에서 SUSPEND 예약 시 suspended_ops 의 대상이 되는 operation 으로 발생한 OP_END 이벤트를 삭제할 수 있게 리팩토링할 수 있는 방안을 연구해줘.

## 요약
- Scheduler 는 예약된 모든 operation 에 대해 OP_END 이벤트를 큐에 넣고, suspend 상태가 되더라도 큐에서 제거하지 않으며, 실행 시점에 ResourceManager.is_op_suspended 로 우회한다.
- ResourceManager.commit 은 *_SUSPEND 커밋 시 move_to_suspended_axis 를 호출하여 대상 op 메타를 suspended_ops_* 스택으로 옮기지만, Scheduler 에게 어느 op 가 이동했는지 알려주지 않는다.
- EventQueue 는 push/pop 만 지원해 특정 이벤트를 명시적으로 삭제할 수 없으므로, suspend 시점에서 OP_END 이벤트 제거를 위해서는 큐에 대한 취소 API 와 Scheduler 가 사용할 식별자 관리가 필요하다.
- 리팩토링은 “OP_END 이벤트 핸들 저장 + suspend 시점 취소”가 핵심이며, 이를 위해 ResourceManager 가 최신 suspend 메타 데이터를 노출하거나 Scheduler 가 자체적으로 차집합을 계산해야 한다.

## 상세 발견

### Scheduler 의 이벤트 발행과 ongoing 등록
- `_propose_and_schedule` 은 commit 성공 후 모든 예약 레코드에 대해 `_emit_op_events` 를 호출하여 OP_START/OP_END 를 무차별적으로 push 한다; SUSPEND 에 대한 예외가 없다 (`scheduler.py:781`, `scheduler.py:785`, `scheduler.py:870`, `scheduler.py:875`).
- Tracking axis 가 있는 op 는 `_next_op_uid` 로 고유 ID 를 부여하고, 이후 ResourceManager.register_ongoing 으로 전달된다 (`scheduler.py:742`, `scheduler.py:807`).
- OP_END 처리기는 suspend 된 op 를 감지하면 조기 반환하지만 이벤트 자체는 소비된다 (`scheduler.py:271`–`scheduler.py:317`). 이로 인해 불필요한 이벤트 처리와 metrics 노이즈가 발생한다.

### ResourceManager 의 suspend 처리
- `_Txn` commit 경로는 ERASE/PROGRAM_SUSPEND 시 move_to_suspended_axis 를 호출하여 최신 ongoing 메타를 suspended_ops_* 리스트로 이동시키고 타임라인을 자른다 (`resourcemgr.py:877`–`resourcemgr.py:934`).
- move_to_suspended_axis 는 대상 meta 를 pop 한 뒤 remaining_us, planes, bus_resv 등을 갱신하고 suspended_ops_*에 push 하지만 호출자에게 meta 를 반환하지 않는다 (`resourcemgr.py:1531`–`resourcemgr.py:1679`).
- suspend 여부 확인용 is_op_suspended 만 제공되므로 Scheduler 는 OP_END 시점까지 어떤 op 가 suspend 됐는지 알기 어렵다 (`resourcemgr.py:1829`–`resourcemgr.py:1838`).

### EventQueue 제한 사항
- EventQueue 는 push 와 pop_time_batch 만 제공하며, 특정 이벤트를 취소하거나 갱신할 수 있는 API 가 없다 (`event_queue.py:17`–`event_queue.py:33`).
- Scheduler 는 OP_END 이벤트를 취소하려면 큐 내부 리스트 `_q` 를 직접 조작해야 하지만 현재는 그런 헬퍼가 존재하지 않는다.

### 리팩토링 옵션
1. **큐 핸들 기반 취소 (권장)**
   - `EventQueue.push` 가 삽입된 튜플의 sequence ID 를 반환하도록 확장하고, 새로운 `remove(kind: str, seq: int) -> bool` API 를 추가한다.
   - Scheduler 는 `_emit_op_events` 내에서 `op_uid` 를 key 로 `self._pending_op_end_events[op_uid] = seq` 를 보관한다.
   - ResourceManager 는 `move_to_suspended_axis` 실행 시 이동한 meta(op_id 포함)를 수집해 `self._suspend_transfers[(axis, die)]` 같은 버퍼에 기록한다. commit 이후 Scheduler 가 SUSPEND rec 를 처리할 때 해당 버퍼를 소비하여 op_uid 를 받아 `_cancel_op_end(op_uid)` 호출로 이벤트를 제거한다.
   - 장점: suspend 대상만 정확히 취소 가능, resume 시 새 OP_END 를 재등록해도 충돌 없음. 단점: RM/Scheduler 간 새로운 계약과 테스트 추가 필요.

2. **Scheduler 기반 차집합 추적**
   - Scheduler 가 die/axis 별 suspended_ops_* 길이를 캐시하고, SUSPEND 예약 직후 다시 조회하여 새로 증가한 항목의 `op_id` 를 찾는다.
   - 버퍼 없이도 동작하지만, suspend 리스트에 여러 항목이 쌓여 있을 때 정확한 대상 계산을 위한 정렬(예: start_us)과 race 케이스 처리 로직이 필요하다. 이벤트 핸들 관리는 옵션 1 과 동일하게 적용.

3. **지연 취소 + lazy 검사**
   - EventQueue 에 `compact(predicate)` 같은 API 를 추가하여 tick 순환 전에 불필요한 OP_END 를 삭제한다. Scheduler 는 tick 시작 시 `rm.suspended_ops_*` 와 `_pending_op_end_events` 를 비교해 suspended 상태인 op 의 이벤트를 한꺼번에 제거한다.
   - 코드 경로가 단순하지만 suspend 직후에도 이벤트가 잠깐 존재하며, 지연 시 삭제가 늦어질 수 있다. 또한 tick 마다 전수검사 비용이 증가한다.

각 옵션은 큐 API 확장과 Scheduler/RM 간 데이터 동기화를 요구하며, 1안이 즉시성 및 명확성 측면에서 가장 직관적이다.

## 코드 참조
- `scheduler.py:781`
- `scheduler.py:785`
- `scheduler.py:742`
- `scheduler.py:807`
- `scheduler.py:271`
- `resourcemgr.py:877`
- `resourcemgr.py:934`
- `resourcemgr.py:1531`
- `resourcemgr.py:1679`
- `resourcemgr.py:1829`
- `event_queue.py:17`
- `event_queue.py:33`

## 아키텍처 인사이트
- suspend/resume 체인은 Scheduler 의 이벤트 수명 주기와 ResourceManager 의 메타 이동이 서로 독립적으로 작동한다; 이벤트 큐와 suspend 메타 간 교차 정보를 보존하면 양쪽 모두 간단히 유지할 수 있다.
- 큐 정렬이 sequence 기반이므로, 제거 시에도 순서를 보존하려면 직접 리스트 정리 대신 API 제공이 안전하다.
- EventQueue 성능은 리스트 정렬에 의존하므로, 대량 이벤트 환경에서는 제거 로직이 추가 비용을 발생시킬 수 있다. sequence 기반 인덱싱을 유지하면 선형 스캔을 피할 수 있다.

## 관련 연구
- `research/2025-09-27_16-04-21_scheduler_resume_re_suspension.md`

## 미해결 질문
- ResourceManager 가 suspend 이동 메타를 외부에 노출할 최적의 인터페이스는 무엇인가? (예: consume API vs snapshot)
- suspend 이벤트가 연속적으로 발생할 때(복수 die) cancellation 로직이 race 없이 정확히 동작하는지에 대한 단위 테스트 범위가 필요하다.

## 후속 연구 2025-09-27T19:42:46+09:00

### ResourceManager suspend 메타 노출 경로
- `move_to_suspended_axis` 가 meta 를 내부 스택으로만 이동시키고 반환하지 않는 것이 핵심 제약점이다 (`resourcemgr.py:1531`–`resourcemgr.py:1679`). 두 가지 설계를 비교했다.
  1. **반환값 확장**: `move_to_suspended_axis(...) -> Optional[_OpMeta]` 로 바꿔 commit 경로에서 직접 meta 를 회수하도록 한다. `_Txn` commit 루프(`resourcemgr.py:877`–`resourcemgr.py:934`)는 이미 axis/plane 정보를 갖고 있어, 반환된 meta 를 `(axis, die)` 키로 일시 버퍼에 저장한 뒤 Scheduler 가 읽어갈 수 있다. 호출자 변동 범위는 ResourceManager 내부라 호환성 영향이 제한적이다.
  2. **전용 버퍼 API**: ResourceManager 내부에 `self._suspend_transfers: Dict[Tuple[str,int], List[int]]` 를 신설하고, move 함수가 op_id 를 push 하도록 한다. Scheduler 는 SUSPEND 커밋 직후 `rm.consume_suspended_op_ids(axis, die)` 같은 메서드로 op_uid/op_id 리스트를 회수한다. 기존 퍼블릭 API 와의 충돌이 없고, multiple suspend 가 발생해도 순차성을 보존한다.
- 반환값 확장은 간결하지만 기존 외부 호출자(테스트 등)가 None 을 기대하는지 확인해야 한다. 전용 버퍼는 인터페이스가 명확해 Scheduler 가 여러 die 에 대한 suspend 정보를 배달받기 쉬우며, 동시에 resume 케이스와 분리되어 안전하다. 구현 시 `_suspend_transfers` 초기화와 snapshot/restore (`resourcemgr.py:1907` 이후) 반영 여부를 점검해야 한다.

### 멀티 다이 suspend 이벤트 검증 범위
- 다중 die 환경에서 OP_END 취소 로직이 경쟁 없이 동작하는지 검증하려면 Scheduler 수준 테스트가 필요하다. 제안 시나리오:
  1. 토폴로지를 2개 die 로 설정한 Scheduler 인스턴스를 구성하고, 두 die 에서 PROGRAM 작업을 예약해 `_emit_op_events` 를 통해 서로 다른 `op_uid` 와 OP_END 핸들을 확보한다 (`scheduler.py:742`, `scheduler.py:870`).
  2. 각 die 에 대해 `PROGRAM_SUSPEND` 를 커밋하도록 ResourceManager 를 조작해 `move_to_suspended_axis` 가 두 번 연속 호출되게 한다 (`resourcemgr.py:877`). 이때 전용 버퍼 API 혹은 반환값을 이용해 Scheduler 가 두 op_id 를 순차적으로 수신하는 경로를 테스트한다.
  3. Scheduler 가 도입할 `_cancel_op_end(op_uid)` 헬퍼가 두 handle 을 모두 제거했는지 EventQueue 내부 상태를 검사하거나, 이후 tick 실행 시 `_handle_op_end` 가 호출되지 않는지 확인한다 (`scheduler.py:271`).
- 테스트는 기존 `tests/test_suspend_resume.py` 를 확장해 새 케이스를 추가하거나, Scheduler 전용 테스트 파일을 만들어 EventQueue Mock 을 통해 핸들 제거를 단언할 수 있다. 예상 assertion: suspended_ops 스택 길이, EventQueue `_q` 에 남은 OP_END 수, metrics(`drain_op_end_processed`) 변화 등.
- 복수 die 케이스 외에도 동일 die 에서 연속 suspend 가 일어날 때 마지막 op 만 취소되는지, resume 후 OP_END 가 다시 등록되는지 경계 테스트를 구성해야 한다.
