---
date: 2025-09-16T15:15:09.846705+00:00
researcher: Codex
git_commit: ca07cf6d0ee8e19a59e2111a9b06e27bc1d54462
branch: main
repository: nandseqgen_v2
topic: "Ensure single OP_END queue for suspend/resume ERASE/PROGRAM"
tags: [research, scheduler, event-queue, suspend-resume, resource-manager]
status: complete
last_updated: 2025-09-17
last_updated_by: Codex
---

# 연구: Ensure single OP_END queue for suspend/resume ERASE/PROGRAM

**Date**: 2025-09-16T15:15:09.846705+00:00  
**Researcher**: Codex  
**Git Commit**: ca07cf6d0ee8e19a59e2111a9b06e27bc1d54462  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
ERASE/PROGRAM 이 스케쥴 된 후 CORE_BUSY state 에서 SUSPEND→RESUME 을 반복하는 조건에서 ERASE/PROGRAM 이 반복적으로 재스케쥴되면서 OP_END 이벤트도 반복적으로 큐잉되는 문제가 있어. OP_END 가 단 한 번만 큐잉되도록 하는 방법을 research 해줘.

## 요약
- Scheduler 는 SUSPEND 이후 생성한 resume 체인(stub)마다 `_emit_op_events` 를 다시 호출하여 동일한 프로그램/erase 작업에 대해 별도의 OP_END 이벤트를 계속 추가한다. (`scheduler.py:603`)
- EventQueue 는 중복된 OP_END 를 정리하지 않고 타임스탬프/우선순위 기준으로 그대로 유지하므로, 최종 구간과 동일 타임스탬프에 여러 OP_END 가 존재해 AddressManager 후크가 반복 실행된다. (`event_queue.py:6`, `scheduler.py:715`)
- `ResourceManager.move_to_suspended_axis` 는 남은 실행 시간(`remaining_us`)을 보존해 resume 시 stub 을 만들지만, 기존 OP_END 일정은 보정되지 않아 체인 스텁이 새 이벤트를 추가하지 않으면 최종 완료 시점이 뒤로 밀려도 반영되지 않는다. (`resourcemgr.py:1080`)
- 해결 방안은 (1) stub 예약 시 기존 OP_END 를 찾아 새 종료 시각으로 재예약하거나 제거 후 재삽입하여 큐에 단 한 개만 남기거나, (2) Handler 에서 suspended 상태를 검사해 중복 이벤트를 무시하는 방식이 있다. 전자는 명세를 충족하며 후자는 큐에서 중복을 제거하지 못한다.

## 상세 발견

### Scheduler resume 체인에서의 중복 이벤트 등록
- `_propose_and_schedule` 는 SUSPEND 후 `remaining_us` 가 있는 경우 `_build_core_busy_stub` 으로 잔여 CORE_BUSY 전용 op 을 만들고 `_emit_op_events` 를 호출한다. (`scheduler.py:603-633`)
- `_emit_op_events` 는 레코드가 체인 스텁인지 여부와 무관하게 OP_START/OP_END 를 큐에 추가한다. (`scheduler.py:673-716`)
- 따라서 동일 `targets` 와 `op_name` 에 대해 기존 OP_END 가 큐에 남아 있는 상태에서 체인 스텁이 또 하나의 OP_END 를 추가한다.

### EventQueue 의 중복 허용과 실행 순서
- EventQueue 는 `(time, priority, seq)` 정렬만 수행하며, 동일 시각/타입의 근접 이벤트를 제거하거나 병합하지 않는다. (`event_queue.py:6-33`)
- 중복된 OP_END 가 존재하면 `_handle_op_end` 가 같은 타겟에 대해 여러 번 호출돼 AddressManager `apply_pgm/apply_erase` 가 반복 실행된다. 이는 이전 연구에서 확인한 증상과 일치한다. (`scheduler.py:232-259`, `research/2025-09-16_14-34-46_program_op_end.md`)

### Suspend bookkeeping 과 잔여 시간 관리
- `ResourceManager.move_to_suspended_axis` 는 원래 예약된 `end_us` 를 기준으로 남은 실행 시간을 계산해 `remaining_us` 로 저장한다. (`resourcemgr.py:1080-1095`)
- SUSPEND 시점 이후 타임라인에서 원래 CORE_BUSY 구간을 `truncate_after` 로 잘라내고, resume 체인이 새 CORE_BUSY 구간을 추가한다. (`resourcemgr.py:640-665`, `out/op_state_timeline_250916_0000001.csv:1`)
- 관측된 타임라인에는 같은 `op_uid` 에 대해 기본 세그먼트와 `source=RESUME_CHAIN` 세그먼트가 번갈아 등장한다. (`out/operation_timeline_250916_0000001.csv:6`, `out/operation_timeline_250916_0000001.csv:33`)

### 대안 평가
1. **Stub 예약 시 기존 OP_END 재예약/교체** — 체인 스텁을 생성하기 전에 EventQueue 에서 동일 op 를 식별하고 기존 OP_END 를 제거한 뒤 새 종료 시각으로 다시 추가한다. Pros: 큐에 단 하나의 OP_END 만 남아 명세 충족, 최종 종료 시각이 resume 지연을 반영한다. Cons: 이벤트 식별을 위해 `op_uid` 같은 안정 키를 payload 로 전달하거나 별도 맵을 유지해야 하며, 잘못된 매칭 시 다른 op 의 이벤트를 제거할 위험이 있다. Risk: 멀티 플레인/동일 타겟 병렬 작업에서 키 충돌로 잘못된 이벤트를 조작할 수 있다.
2. **OP_END handler 에서 suspended 상태 검사 후 중복 무시** — `_handle_op_end` 가 실행될 때 `rm.suspended_ops_*` 또는 `remaining_us` 를 조회하여 아직 잔여 실행이 남아있다면 `apply_pgm/apply_erase` 를 건너뛰고 마지막 이벤트에서만 실행한다. Pros: EventQueue 수정 없이 구현이 단순하다. Cons: 큐에는 여전히 여러 OP_END 가 남아 “한 번만 큐잉”이라는 요구를 충족하지 못하며, 중복 이벤트 처리 비용도 남는다. Risk: 상태 판별 버그가 있을 경우 최종 이벤트까지 모두 무시해 AddressManager 동기화가 생략될 수 있다.

## 코드 참조
- `scheduler.py:603` – resume 체인 스텁 예약 후 `_emit_op_events` 호출
- `scheduler.py:673` – `_emit_op_events` 가 OP_START/OP_END 를 모두 큐잉
- `scheduler.py:232` – OP_END 처리에서 `_am_apply_on_end` 호출 경로
- `event_queue.py:6` – 이벤트 우선순위 및 정렬 방식
- `resourcemgr.py:1080` – `move_to_suspended_axis` 가 `remaining_us` 보존
- `resourcemgr.py:640` – SUSPEND 시 타임라인 CORE_BUSY 구간 truncate
- `out/operation_timeline_250916_0000001.csv:6` – `source=RESUME_CHAIN` 세그먼트가 기본 PROGRAM 과 함께 등장
- `out/op_state_timeline_250916_0000001.csv:1` – 동일 plane 에 CORE_BUSY 구간이 분할되어 기록됨

## 아키텍처 인사이트
- Scheduler 는 suspend/resume 체인을 독립된 op 으로 취급하여 기존 예약과 동일한 이벤트 파이프라인을 사용한다. 이벤트 큐에 대한 중복 제거나 재예약 기능이 없기 때문에, 재활성화된 op 을 표현하기 위해서는 별도의 식별자와 큐 수정 기능이 필요하다.
- ResourceManager 가 잔여 실행 시간을 정밀하게 추적하고 있으므로, 재예약 시 기존 이벤트를 수정하거나 handler 에서 상태를 판정하는 근거는 이미 존재한다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-16_14-34-46_program_op_end.md` – OP_END 중복으로 AddressManager.apply_pgm 이 여러 번 호출되는 현상을 기록한 선행 연구.

## 관련 연구
- `research/2025-09-16_23-13-07_program_suspend_resume_sampling.md`
- `research/2025-09-16_14-34-46_program_op_end.md`

## 미해결 질문
- EventQueue 에서 특정 op 의 OP_END 를 안정적으로 식별하려면 어떤 키(예: op_uid, die/plane/scope 조합)가 필요한지 정의가 필요하다. -> (검토완료) op_uid 가 결정적이므로 사용. suspended_ops_erase/suspended_ops_program 의 targets 을 사용하여 완전히 조회가능.
- ERASE 체인에서도 동일한 중복 제거 전략이 동작하는지(특히 멀티 플레인 ERASE) 시뮬레이션으로 검증해야 한다. -> (검토완료) 구현 후 검증 계획에 포함
- Handler 기반 완화 방식을 택할 경우, 누락된 최종 이벤트를 탐지할 수 있는 별도 가드나 메트릭이 필요하다. -> (검토완료) Handler 기반 완화 방식 미선택
