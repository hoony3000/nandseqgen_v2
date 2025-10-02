---
date: 2025-09-29T15:33:39.812919+09:00
git_commit: 7826919573d0ac03d86e680b8203c2cfb64f17d5
branch: main
repository: nandseqgen_v2
topic: "scheduler backlog resume refactor"
tags: [research, codebase, scheduler, resourcemgr, suspend]
status: complete
last_updated: 2025-09-29
last_updated_by: assistant
last_updated_note: "follow-up: ResourceManager backlog API vs scheduler backlog creation"
---

# 연구: scheduler backlog resume refactor

**Date**: 2025-09-29T15:33:39.812919+09:00
**Git Commit**: 7826919573d0ac03d86e680b8203c2cfb64f17d5
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
scheduler_backlog_resume.md 에서 batch 로 예약된 operation 을 backlog 로 옮기게끔 구현했는데, 2025-09-29_15-06-41_batch_suspend_truncation.md 에 따르면 의도대로 동작하지 않는 것으로 보인다. 어떻게 리팩터링 해야할 지 연구해줘

## 요약
- `scheduler._propose_and_schedule` 는 `suspend_axes` 전파로 후속 op 를 backlog 로 보내려 하지만, 대상 op 에 `targets` 가 없거나 die 추론이 실패하면 `die_candidate is not None` 조건 때문에 그대로 예약을 진행한다. 이 경로는 문서에서 관측된 “후속 operation 이 그대로 남음” 현상을 재현한다.
- 커밋 후 cleanup 은 `consume_suspended_op_ids` 로 돌아온 uid 만 취소하는데, `resourcemgr.move_to_suspended_axis` 가 최신 메타 한 건만 pop 하므로 같은 배치에 있었던 나머지 op 의 OP_END/state 가 그대로 살아남는다.
- RESUME 이후 backlog flush 는 die 키가 명확히 있는 큐만 처리한다. suspend 구간에서 die 가 비어 있던 op 들은 큐에 들어가지 못해 재예약 루프와 metric 이 모두 비게 되고, 테스트도 단일 backlog 엔트리만 검증해 다중 시나리오를 놓친다.

## 상세 발견

### Scheduler backlog gating (`scheduler.py:1100-1184`)
- 후속 op 가 backlog 로 이동하려면 `die_candidate` 가 필요하다. `targets` 가 비어 있거나 Address 스텁이 die 속성을 제공하지 않으면 `die_candidate` 가 `None` 으로 남고, 조건 `die_candidate is not None` 때문에 backlog 분기 자체가 실행되지 않는다.
- `suspend_axes` 에는 `phase_hook_die` 를 비롯한 메타가 들어오지만, 현재 구현은 해당 정보를 사용해 fallback 키를 만들지 않아 axis 수준 큐만 존재할 뿐 기존 엔트리와 연결되지 않는다.
- 결과적으로 proposer 가 SUSPEND 이후 axis-wide op (예: die-wide flush, slice-less read) 를 배치에 넣을 경우 scheduler 는 그대로 reserve/commit 해 버리고, 문서와 동일하게 state/timeline 이 잔류한다.

### Commit 단계의 단일 uid 취소 (`scheduler.py:1309-1344`, `resourcemgr.py:1598-1774`)
- 커밋 루프는 `PROGRAM_SUSPEND`/`ERASE_SUSPEND` 레코드마다 `consume_suspended_op_ids` 로 받은 uid 를 `_cancel_op_end` 에 태운다. 하지만 ResourceManager 는 `_ongoing_ops[die]` 에서 matching axis 의 마지막 meta 한 건만 pop 하도록 설계돼 있으며, 그 uid 만 `_suspend_transfers` 에 저장한다.
- 같은 batch 에서 연속으로 예약된 PROGRAM 계열 op 는 여전히 `_ongoing_ops` 스택에 남아 있고, OP_END 이벤트도 큐에 유지된다. 연구 노트의 “첫 operation 의 OP_END 만 사라지고” 현상이 코드상으로 재현된다.
- pop 대상 외에 같은 die 의 이전 meta 를 backlog 로 넘길 통로가 없어 scheduler 는 재예약 시점에 사용할 데이터를 잃는다.

### Resume/backlog 연동 부족 (`scheduler.py:842-848`, `tests/test_suspend_resume.py:747-807`)
- `_handle_resume_commit` 은 backlog 큐 키 `(axis, die)` 가 존재해야만 `BACKLOG_REFILL` 이벤트를 푸시한다. 상기 조건 때문에 큐에 들어오지 못한 op 는 RESUME 이후에도 재시도 루프가 돌지 않는다.
- 현재 테스트는 `_setup_scheduler_with_backlog` 가 강제로 die 가 있는 op 하나를 만들고 검증하는 수준이라 multi-op 또는 target-less 케이스가 빠진다.
- metrics (`backlog_size`, `backlog_flush_pending`) 의 값이 0 으로 남아도 실패를 감지하지 못해, 실제 시나리오와 괴리가 커진다.

## 코드 참조
- `scheduler.py:1100` – `suspend_axes` 기반 backlog 감지 로직이 `die_candidate` 를 필수로 요구
- `scheduler.py:1309` – 커밋 후 `_emit_op_events` 및 `_cancel_op_end` 루프가 단일 uid 만 제거
- `scheduler.py:842` – RESUME 시 backlog flush 트리거 조건이 `(axis, die)` 키 존재에 의존
- `resourcemgr.py:1598` – `move_to_suspended_axis` 가 `_ongoing_ops` 에서 단일 meta 만 이동
- `resourcemgr.py:1770` – `_suspend_transfers` 리스트에 마지막 op_uid 하나만 push
- `tests/test_suspend_resume.py:747` – backlog 관련 테스트가 단일 엔트리 happy-path 만 커버
- `plans/scheduler_backlog_resume.md:51` – 동일 die 후속 op 를 FIFO 백로그로 옮기겠다는 계획과 현 구현의 괴리

## 아키텍처 인사이트
- backlog 는 `(axis, optional_die)` 키를 받아들여야 하며, die 가 명확하지 않은 경우에도 axis 레벨 큐에 적재하도록 `_enqueue_backlog_entry`/`_backlog_queue` 를 확장할 필요가 있다. `suspend_info` 의 `phase_hook_die`/`planes` 를 fallback 으로 사용해 die 를 역추론하는 편이 안전하다.
- ResourceManager 는 suspend 시점 이후 동일 axis/die 에 예약된 나머지 meta 를 함께 스캔해 `_suspend_transfers` 로 uid 를 돌려주고, backlog 재예약에 활용할 최소한의 정보를 반환해야 한다. 옵션으로 `move_to_suspended_axis` 가 pop 한 meta 묶음을 반환해 scheduler 가 즉시 backlog 엔트리를 만들도록 바꿀 수 있다.
- RESUME 플로우는 backlog 엔트리가 없더라도 axis-level retry 이벤트를 걸어주는 방식을 채택하면, suspend 시점에 die 를 확보하지 못한 op 도 재예약 경로에 올릴 수 있다.
- 테스트는 multi-op batch(동일 die, 일부 op 의 targets 비어 있음) 과 ResourceManager 가 여러 meta 를 반환하도록 변형한 시나리오를 추가해 regression 을 차단해야 한다.

## 관련 연구
- `research/2025-09-29_15-06-41_batch_suspend_truncation.md`
- `plans/scheduler_backlog_resume.md`
- `research/2025-09-28_11-35-16_suspend-batch-resume.md`

## 미해결 질문
- ResourceManager 에서 여러 meta 를 한 번에 backlog 로 되돌리는 API 를 도입할지, 아니면 scheduler 가 commit 직후 바로 backlog 엔트리를 생성하도록 인터페이스를 재조정할지 결정 필요 -> (TODO) 두 가지 방법의 risk 를 비교한 후 best practice 에 가까운 것을 선택.
- die 정보 없이 backlog 로 보내야 하는 op 의 범위를 config 로 제한할지(예: axis-wide 관리 op), 혹은 자동 감지 로직을 강화할지 검토 필요 -> (검토완료) die 정보가 없는 것은 suspend 대상이 되는 첫 번쨰 operation 의 것을 참고한다.
- backlog 재예약 실패 시 proposer fallback 을 어디서 트리거할지 정책 정의가 요구됨 -> (검토완료) fallback 정책은 도입하지 않는다. 예약된 조건 그대로 복구하지 못하면 log 를 출력하고, resume 동작 자체를 취소한다.

## 후속 연구 2025-09-29T15:59:33.801261+09:00

**ResourceManager 다중 meta 회수안**
- `resourcemgr.py:1598` 에서 `_ongoing_ops[die]` 스택에서 마지막 meta 하나만 pop 하도록 고정되어 있어, 여러 meta 를 돌려주려면 동일 루프 안에서 조건에 맞는 이전 항목들도 찾아야 하고, 각 항목마다 plane/exclusion/bus 예약을 다시 잘라야 한다.
- `_suspend_transfers` 는 (axis, die) → `[op_uid]` 형태(`resourcemgr.py:1772`, `resourcemgr.py:1922`)라 scheduler 에게 uid 외 정보를 넘기는 통로가 없다. meta 묶음을 전달하려면 구조를 확장하거나 새 버킷을 도입해야 하며 snapshot/restore(`resourcemgr.py:2037`, `resourcemgr.py:2162`)도 모두 수정된다.
- pop 대상 meta 가 아직 실행을 시작하지 않은 경우에는 `remaining_us`, `bus_segments` 가 원본 값으로 남아 있어 `move_to_suspended_axis` 의 슬라이스 로직을 다시 적용하는 것이 애매하다. 잘못 다루면 `plane_resv`/`_excl_die` 가 무효화되거나 다른 die 의 진행 중 오퍼레이션과 충돌할 수 있다.

**Scheduler 즉시 backlog 전환안**

Implementation note (2025-09-29 verification)
- 계획: Scheduler 측에서 SUSPEND 직후 같은 batch 안의 후속 op를 backlog 로 보내도록 분기를 강화한다.
- 관찰: 실제 run에서는 `Program_Suspend_Reset` 제안이 항상 `len_batch=1`로 잡혀 suspend 배치와 후속 배치가 서로 다른 tick에서 커밋됐다(`out/proposer_debug_250929_0000001.log:523-528`).
- 영향: `_propose_and_schedule` 가 호출될 때마다 `suspend_axes` 를 새로 만드는 구조라, 후속 배치의 `Cache_Program_SLC`/`Program_Resume` 들은 backlog 조건을 만나지 못하고 그대로 예약되었다 (`out/op_event_resume.csv`). Scheduler 만 고쳐서는 백로그 이동이 이뤄지지 않으므로, suspend 상태를 tick 간에 유지하거나 commit 시 전역 캐시로 넘기는 추가 설계가 필요하다.

- `scheduler.py:1100-1184` 의 backlog 분기에서 `die_candidate is not None` 검사를 완화해 `suspend_info` 가 들고 있는 `phase_hook_die` 나 `hook_die` 를 fallback 으로 사용하면 SUSPEND 이후 op 를 reserve 이전에 바로 backlog 로 옮길 수 있다.
- `_backlog_queue` 키가 `(axis, die)` 로 고정되어 있으므로, fallback die 를 확보하면 기존 deque/metrics(`scheduler.py:515-742`)를 그대로 재사용할 수 있고, ResourceManager 인터페이스는 손댈 필요가 없다.
- commit 루프가 backlog 로 빠진 rec 를 건너뛰기 때문에 `_emit_op_events` 와 `rm.register_ongoing` 호출도 발생하지 않아 OP_END 정리나 `_suspend_transfers` 확장 없이도 후속 정합성을 확보할 수 있다. 테스트는 die fallback 시나리오를 추가하면 된다(`tests/test_suspend_resume.py:747` 이후 케이스 확장 필요).

**권고**
- ResourceManager 쪽을 확장하는 접근은 내부 예약/스냅샷 구조까지 광범위하게 손대야 하고, 아직 시작하지 않은 meta 처리 규칙을 새로 정의해야 한다는 리스크가 크다.
- Scheduler 에서 backlog 분기를 강화하면 변경 범위가 scheduler 한정이고, 기존 메트릭/이벤트 경로를 유지하면서도 SUSPEND 이후 op 를 재예약 큐로 확실히 보낼 수 있다. 따라서 commit 직전 backlog 엔트리를 생성하는 scheduler 측 리팩터링을 우선 추진하는 것이 안전하다.
