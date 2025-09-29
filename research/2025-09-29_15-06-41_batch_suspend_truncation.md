---
date: 2025-09-29T15:06:41+09:00
git_commit: 7826919573d0ac03d86e680b8203c2cfb64f17d5
branch: main
repository: nandseqgen_v2
topic: "Batch suspend truncation mismatch"
tags: [research, scheduler, resourcemgr, suspend]
status: complete
last_updated: 2025-09-29
---

# 연구: Batch suspend truncation mismatch

**Date**: 2025-09-29T15:06:41+09:00
**Git Commit**: 7826919573d0ac03d86e680b8203c2cfb64f17d5
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
batch 로 예약된 operation 이 SUSPEND 대상이 될 때 첫 operation 의 OP_END 만 사라지고 state 및 후속 operation 이 그대로 남으며, RESUME 시에는 batch 마지막 operation 의 OP_END 만 복구되는 원인을 규명한다.

## 요약
Scheduler 는 batch 내 모든 resv_records 에 대해 OP_START/OP_END 를 즉시 발행하고, SUSPEND 를 만난 경우에도 후속 operation 이 동일 txn 에서 커밋된다. ResourceManager 는 `move_to_suspended_axis` 호출로 현재 진행 중이던 단일 op meta 만 축약하여 `_suspend_transfers` 에 전달하기 때문에 첫 operation 의 OP_END 만 제거된다. 그러나 동일 batch 의 나머지 operation 은 ongoing 으로 남아 있으므로 state timeline 도 truncate 되지 않는다. RESUME 시에는 `_handle_resume_commit` 이 축별 스택의 최상단 meta 하나만 복구해 동일 batch 마지막 op 의 OP_END 만 재등록되면서 관측된 불일치가 발생한다.

## 상세 발견

### Scheduler commit keeps post-suspend ops alive
- `scheduler.py:1309`~`scheduler.py:1375` 는 batch 전 항목을 순회하며 `_emit_op_events` 를 호출하고, 이후에야 `_cancel_op_end` 로 suspend 대상 op 의 OP_END 를 제거한다. `Program_Suspend_Reset` 이후에도 나머지 operation 의 이벤트가 큐에 남으며 state registration 도 유지된다.
- `scheduler.py:1344` 의 취소 루프는 `ResourceManager.consume_suspended_op_ids` 가 넘겨준 uid 목록만 제거하며, 동일 batch 내 다른 uid 는 untouched 상태다. 따라서 첫 operation 의 OP_END 만 사라진다.

### ResourceManager only moves one ongoing op per suspend
- `resourcemgr.py:1606`~`resourcemgr.py:1774` 의 `move_to_suspended_axis` 는 지정 die 의 `_ongoing_ops` 스택에서 조건에 맞는 마지막 meta 하나만 pop 하여 `_suspended_ops_program[(die)]` 에 push 한다. 후속 operation 에 대한 메타는 그대로 ongoing 리스트에 남는다.
- OP_END 취소용으로 `_suspend_transfers[(axis, die)]` 에 append 되는 uid 도 pop 한 meta 하나 뿐이어서, cancel 단계에서 첫 operation 의 이벤트만 제거된다.
- `_suspend_axes_targets` 가 반환한 plane/bases 정보로 `self._st.truncate_after` 를 호출하지만, 대상 meta 외에는 건드리지 않으므로 동일 배치 후속 op 의 state 구간은 그대로 남는다(`resourcemgr.py:920`~`resourcemgr.py:937`).

### Resume path restores only top-of-stack meta
- `scheduler.py:766`~`scheduler.py:877` 의 `_handle_resume_commit` 은 `resume_from_suspended_axis` 로부터 단일 meta 를 받아 해당 uid 의 OP_END 를 재삽입한다. 스택 구조 때문에 suspend 시점 가장 나중에 push 된 meta, 즉 배치 마지막 operation 이 우선 복구된다.
- `resourcemgr.py:1810`~`resourcemgr.py:1904` 에서도 `resume_from_suspended_axis` 는 `_suspended_ops_program[die]` 의 마지막 항목을 pop 하도록 설계되어 있어, 여러 op 가 suspend 되었더라도 동일 tick 의 RESUME 은 마지막 한 개만 돌려준다.

### CSV evidence shows truncated outputs limited to final op
- `out/op_event_resume.csv:21` 는 `Page_Program_SLC` 의 재개 OP_END(`is_resumed=True`) 가 기록된 반면, 동일 배치의 `Cache_Program_SLC` 는 원래 OP_END 가 유지되어 첫 operation 만 제거된 것이 확인된다.
- `out/op_state_timeline_250929_0000001.csv:30`~`out/op_state_timeline_250929_0000001.csv:73` 구간에서는 suspend 직후에도 `CACHE_PROGRAM_SLC.DATAIN` 과 `PROGRAM_RESUME` 이전 state 들이 그대로 남아 있어 state truncate 가 되지 않은 사실을 뒷받침한다.

## 코드 참조
- `scheduler.py:1309`
- `scheduler.py:1344`
- `resourcemgr.py:1606`
- `resourcemgr.py:1768`
- `scheduler.py:766`
- `resourcemgr.py:1810`
- `out/op_event_resume.csv:21`
- `out/op_state_timeline_250929_0000001.csv:30`

## 아키텍처 인사이트
SUSPEND 를 배치 분기점으로 취급하지 않고 동일 txn 에서 후속 작업까지 commit 하고 있어, ResourceManager 가 단일 meta 만 이동하는 현재 설계와 충돌한다. suspend 시점에서 나머지 resv_records 를 backlog 로 넘기거나, 최소한 여러 meta 를 일괄 move/restore 할 수 있게 인터페이스를 확장해야 batch 단위 일관성을 확보할 수 있다.

## 관련 연구
- `research/2025-09-28_11-35-16_suspend-batch-resume.md`

## 미해결 질문
- Suspend 이후 동일 batch 나머지 operation 을 backlog 로 이동하거나, ResourceManager 가 다중 meta 를 반환하도록 바꾸는 경로 중 어떤 것이 요구사항에 부합하는지 결정이 필요하다.
