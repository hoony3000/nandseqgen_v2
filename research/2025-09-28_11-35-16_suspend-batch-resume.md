---
date: 2025-09-28T11:35:16+09:00
git_commit: 62176a93729cb0c34ee9f5a6d084b7c67ced9461
branch: main
repository: nandseqgen_v2
topic: "Batch suspend backlog refactor"
tags: [research, scheduler, resourcemgr, suspend, resume]
status: complete
last_updated: 2025-09-28
last_updated_by: assistant
last_updated_note: follow-up: unresolved questions
---

# 연구: batch 형태에서 suspend 이후 후속 operation 재예약 리팩토링

**Date**: 2025-09-28T11:35:16+09:00
**Git Commit**: 62176a93729cb0c34ee9f5a6d084b7c67ced9461
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
batch 형태로 둘 이상의 operation 이 예약된 경우 SUSPEND 시 첫번째 operation 에 대해서만 truncate 하게 되면, 후속 operation 의 OP_START,OP_END 이벤트 및 resource, state 는 그대로 남아 있게 된다. 이것을 저장했다가, RESUME 시에 첫번째 operation 에 이어 재예약하게 리팩토링 하려는데 best practie 의 방법을 연구해줘.

## 요약
- Scheduler 는 commit 시 모든 resv_records 에 대해 OP_START/OP_END 를 즉시 발행해서, SUSPEND 이후 후속 operation 도 이벤트 큐와 리소스에 남는다 (`scheduler.py:786-823`).
- ResourceManager 는 move_to_suspended_axis 호출로 현재 진행 중인 op 만 축약하고 future reservation 을 부분적으로 제거하지만 동일 배치의 나머지 operation 정보를 따로 저장하지 않는다 (`resourcemgr.py:1533-1662`).
- Best practice 는 SUSPEND 를 배치 분기점으로 인정해 후속 operation 을 commit 에서 제외하고 backlog 로 저장한 뒤, RESUME 완료 시 backlog 를 우선 재예약하는 queue 를 도입하는 것이다.
- Backlog 에는 ProposedOp 형태와 상대 타이밍 delta 를 포함시켜 deterministic 재예약을 보장하고, ResourceManager 와 Scheduler 간에 axis/die 키 기반으로 연결한다.

## 상세 발견

### Scheduler batch 커밋 흐름
- `scheduler.py:686-784` 에서 batch.ops 를 순회하며 reservation 을 완료한 뒤, commit 시 전 op 에 대한 이벤트를 큐에 넣는다. SUSPEND 가 포함돼도 후속 op 들이 그대로 `_emit_op_events` 를 통해 큐에 올라간다.
- `scheduler.py:801-823` 는 SUSPEND 로 인해 truncate 된 op 의 OP_END 만 cancel 하지만, 이후 op 들에 대한 cleanup 은 없다. 따라서 후속 op 의 state/timeline 이 잔류한다.
- `_handle_op_end` 는 ResourceManager 가 suspended 로 표시한 uid 는 조용히 무시하지만 (`scheduler.py:281-328`), 이미 큐에 남은 후속 op 는 평소처럼 처리된다.

### ResourceManager suspend 처리
- `resourcemgr.py:878-937` 은 SUSPEND 으로 진입한 op 에서 ongoing 메타를 suspended 리스트로 옮기고 CORE_BUSY segment 를 truncate 한다. 이후 future plane/bus reservation 을 cutoff 시점 이후 제거한다 (`resourcemgr.py:1610-1665`).
- 그러나 동일 txn 에 같이 있었던 다른 ProposedOp 들은 ResourceManager 에서 별도 추적하지 않아, Scheduler 가 emit 한 이벤트가 그대로 남는다.
- `_suspend_transfers` 는 SUSPEND target 의 op_uid 만 넘겨 Scheduler 에서 OP_END 취소 용도로 사용한다 (`resourcemgr.py:1852-1859`), 후속 op backlog 와는 연관이 없다.

### 이벤트 큐 및 재예약 고려
- EventQueue 는 단순한 시간/우선순위 큐로, 후속 op 이벤트를 제거하려면 scheduler 가 명시적으로 remove 해야 한다 (`event_queue.py:5-38`).
- 기존 resume 경로 (`scheduler.py:470-523`) 는 ResourceManager 가 반환한 meta 로 resume op 하나만 재스케줄링하며, 이어질 op 에 대한 Hook 이 없다.

## 코드 참조
- `scheduler.py:686-823` – batch commit 시 SUSPEND 포함한 resv_records 처리와 OP 이벤트 발행.
- `scheduler.py:281-328` – OP_END 핸들러가 suspend op 를 early-return 처리하지만 후속 이벤트는 그대로임.
- `resourcemgr.py:878-937` – SUSPEND 시 ongoing op truncation 및 상태 갱신.
- `resourcemgr.py:1533-1665` – move_to_suspended_axis 가 단일 op 메타만 옮기고 future 예약을 자르는 방식.
- `event_queue.py:5-38` – EventQueue 가 단순 remove 제공, backlog 이벤트 삭제는 외부 책임.

## 아키텍처 인사이트
- SUSPEND 는 배치 내에서 논리적 경계이므로, Scheduler 가 commit 단계에서 이를 감지해 즉시 batch 를 split 해야 한다. 즉, SUSPEND 이후 op 는 같은 txn 내에서 reserve 하지 않고 backlog 로 이동하는 편이 안전하다.
- Backlog 는 `(axis, die)` 키 기반 FIFO 로 관리하면 resume 시 해당 축(die) 의 실행 순서를 보존할 수 있다. 내용물은 `ProposedOp` + `start_delta_us` + 원본 source/hook 정도가 적절하다.
- RESUME 처리기 (`scheduler.py:_handle_resume_commit`) 는 meta 재예약 후 backlog 를 새로운 내부 이벤트(예: `QUEUE_DEFERRED`) 형태로 enqueue 해서, 다음 tick 에 `_propose_and_schedule` 이전에 우선 소진하도록 하는 것이 deterministic 하다.
- ResourceManager 는 backlog 를 위해 큰 변경이 필요 없고, 다만 move_to_suspended_axis 가 성공했을 때 Scheduler 에게 축약된 duration/planes 정보를 돌려줄 수 있으면 재예약 delta 계산이 쉬워진다(선택). 백로그 항목 예약 시 기존 reserve 경로를 그대로 사용해 검증을 유지한다.
- 관측/디버깅을 위해 backlog 크기와 flush 시각을 metrics 로 노출하고, SUSPEND 시 제거된 resv_records 의 UID 를 로그에 남기면 추적이 용이하다.

## 관련 연구
- 없음

## 미해결 질문
- 없음 (2025-09-28 후속 연구에서 주요 쟁점 해소)

## 후속 연구 2025-09-28T11:46:48+09:00

### Backlog 범위 정책
- `scheduler.py:786`: SUSPEND 이후에도 동일 배치 op 가 즉시 이벤트 큐에 올라가므로, SUSPEND 를 만난 즉시 같은 배치에서 그 이후 항목을 모두 backlog 로 옮기는 것이 안전하다.
- `scheduler.py:795` 와 `resourcemgr.py:878` 는 처리 중인 op 의 die/plane 정보를 그대로 갖고 있으므로, 후속 op 대상이 동일 die 또는 plane 집합과 겹치는지 검사해 선택적으로 backlog 에 넣을 수 있다. 기본 원칙은 SUSPEND 를 생성한 rec 와 동일 die 를 포함하는 op 를 backlog 로 보내고, 다른 die 는 유지하되 telemetry 로 검증한다.
- `resourcemgr.py:1533` 이후의 move_to_suspended_axis 는 axis 단위로만 메타를 옮기기 때문에, backlog 는 `(axis, die)` 키를 기준으로 FIFO 로 관리하면 resume 시 순서 보존과 리소스 안전성을 동시에 확보할 수 있다.

### Scheduler backlog flush
- `scheduler.py:153-186` 의 이벤트 처리 순서는 OP_END → PHASE_HOOK → QUEUE_REFILL → OP_START 로 고정되어 있다. backlog flush 는 RESUME 직후 새로운 제안이 오기 전에 실행되어야 하므로, `event_queue.py:5` 의 `_PRIO` 맵에 `BACKLOG_REFILL`(우선순위 1.5 수준) 같은 전용 타입을 추가해 PHASE_HOOK 전에 소비하게 설계하는 것이 권장된다.
- `scheduler.py:470-523` 에서 resume commit 이 일어날 때, 백로그가 존재하면 `EventQueue.push(resume_at, 'BACKLOG_REFILL', {{'axis': axis, 'die': die}})` 를 호출하도록 확장한다. 이 이벤트 핸들러는 보관 중인 `ProposedOp` 묶음을 꺼내 기존 `_propose_and_schedule` 와 동일한 `reserve`/`commit` 경로를 재사용한다.
- backlog flush 는 OP 이벤트를 직접 생성하지 말고, 새로운 txn 으로 `ResourceManager.begin()` 이후 `reserve()` 를 호출해 유효성 검사를 재수행해야 한다. 이렇게 하면 연쇄 재예약이 기존 규칙과 동일한 검증을 거친다.

### Backlog 재예약 실패 대응
- `_propose_and_schedule` 가 실패 시 txn 을 `rollback` 하고 `last_reason` 을 업데이트하는 패턴을 재사용한다 (`scheduler.py:686-865`). backlog flush 도 동일하게 실패 시 해당 묶음을 원래 순서대로 재삽입하고, `metrics['backlog_retry']`, `metrics['backlog_last_reason']` 등을 갱신한다.
- `resourcemgr.py:1811` 부근처럼 resume 실패 시 `_last_resume_error` 를 남기는 구조가 이미 존재하므로, backlog 실패도 이유를 기록해 추후 재시도 타이밍을 조정할 수 있다. 필요하면 실패한 항목을 `EventQueue.push(now + window, 'BACKLOG_REFILL', ...)` 로 재예약한다.
- 연속 실패에 대비해 retry 카운터 한도를 두고, 한도를 초과하면 proposer 로 fallback 하도록 hook 을 제공한다(예: `metrics['backlog_drop_total']`). 이렇게 하면 무한 재시도 루프를 방지하고 오류 원인 분석이 수월해진다.

