---
date: 2025-09-18T08:13:53.812587+00:00
researcher: Codex
git_commit: 9887f587f18cc35b83a1bea32d8e46395933994c
branch: main
repository: nandseqgen_v2
topic: "SUSPEND->RESUME addr_state anomaly"
tags: [research, codebase, scheduler, resource-manager, suspend-resume]
status: complete
last_updated: 2025-09-18
last_updated_by: Codex
---

# 연구: SUSPEND->RESUME addr_state anomaly

**Date**: 2025-09-18T08:13:53.812587+00:00  
**Researcher**: Codex  
**Git Commit**: 9887f587f18cc35b83a1bea32d8e46395933994c  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
목적은 SUSPEND->RESUME 반복 시에 나타나는 addr_state 이상 증가현상을 분석하려는 거야. SUSPEND->RESUME 시에 호출되는 함수와 호출되는 시점, ongoing_ops 가 suspended_ops 로 전환되는 과정, 그리고 재스케쥴 되는 operation 이 ongoing_ops 에 재등록 되는지를 research 해줘.

## 요약
- `PROGRAM`류 커밋 시 Scheduler 가 `ResourceManager.register_ongoing` 을 호출해 die별 `ongoing_ops` 에 메타를 쌓고 즉시 OP_START/OP_END 이벤트를 큐잉한다. (`resourcemgr.py:1031`, `scheduler.py:741`)
- `PROGRAM_SUSPEND` 커밋은 `ResourceManager.commit` 내부에서 즉시 `move_to_suspended_axis` 를 호출해 최신 ongoing 메타를 `suspended_ops_program` 으로 옮기고 남은 실행 시간(`remaining_us`)을 계산하지만, 기존에 이미 큐에 있던 OP_END 이벤트는 제거하지 않는다. (`resourcemgr.py:626`, `resourcemgr.py:639`, `resourcemgr.py:1091`)
- `PROGRAM_RESUME` 커밋 후 Scheduler 의 체인 스텁 로직이 마지막 suspended 메타를 꺼내 남은 CORE_BUSY만큼의 임시 작업을 예약하고, 완료 직후 `resume_from_suspended_axis` 로 meta 를 다시 `ongoing_ops` 에 push 한다. (`scheduler.py:683`, `scheduler.py:699`, `scheduler.py:811`, `resourcemgr.py:1125`)
- 체인 스텁 역시 `_emit_op_events` 를 호출해 동일 타겟의 OP_END 를 다시 큐잉하고, `EventQueue` 에 dedupe 가 없어 AddressManager.apply_pgm 이 두 번 실행되며 `addr_state` 가 0→2 등으로 증가한다. (`scheduler.py:803`, `event_queue.py:13`, `scheduler.py:382`)

## 상세 발견

### ResourceManager
- `register_ongoing` 이 최초 PROGRAM 커밋 시점에 호출되어 die별 리스트에 `_OpMeta(start_us, end_us)` 를 저장한다. (`resourcemgr.py:1031`)
- `commit` 의 `PROGRAM_SUSPEND` 분기는 die별 axis state 를 열고, guard 로 중복 처리를 막은 뒤 `move_to_suspended_axis` 를 호출한다. (`resourcemgr.py:626`, `resourcemgr.py:637-639`)
- `move_to_suspended_axis` 는 최신 ongoing 메타를 pop 해 `remaining_us = end_us - now` 로 계산 후 axis별 `suspended_ops_*` 스택에 보관한다. (`resourcemgr.py:1087-1095`)
- `PROGRAM_RESUME` 커밋은 axis state 를 닫을 뿐 메타 이동은 하지 않으므로, Scheduler 측 체인 스텁에서 `resume_from_suspended_axis` 를 호출할 때까지 메타는 suspended 상태에 머문다. (`resourcemgr.py:674-678`, `resourcemgr.py:1125-1141`)

### Scheduler
- `suspend_resume_chain_enabled` 플래그가 true 이어서, `PROGRAM_RESUME` 예약 직후 `suspended_ops_program(die)` 의 마지막 메타와 `remaining_us` 를 읽어 `_build_core_busy_stub` 작업을 준비한다. (`config.yaml:39`, `scheduler.py:683-703`)
- chain job 이 커밋되면 `_emit_op_events` 가 다시 실행되어 같은 targets/OP_END 를 추가하고, 이어서 `resume_from_suspended_axis` 로 원래 meta 를 `ongoing_ops` 로 복귀시킨다. (`scheduler.py:803`, `scheduler.py:811-817`)
- 체인 스텁 레코드는 `_chain_stub` 로 표시되어 `register_ongoing` 재호출은 생략하지만, meta 객체 자체는 재사용되므로 `ongoing_ops` 리스트에 다시 push 된다. (`scheduler.py:746-754`, `resourcemgr.py:1140-1141`)

### EventQueue
- `EventQueue.push` 는 단순히 `(time, priority, seq)` 로 정렬만 수행하므로, suspend 이전에 이미 큐잉된 OP_END 와 resume 체인에서 새로 추가한 OP_END 둘 다 남는다. (`event_queue.py:13-21`)

### AddressManager & addr_state
- 모든 OP_END 이벤트는 `_handle_op_end` 를 거치며, PROGRAM 계열이면 `_am_apply_on_end` 에서 `apply_pgm` 을 호출해 대상 블록의 `addr_state` 를 +1 한다. (`scheduler.py:382-494`)
- 따라서 동일 작업에 대해 OP_END 가 두 번 이상 실행되면 `apply_pgm` 이 중복되어 `addr_state` 가 0→2 처럼 비정상 증가한다. 기존 연구도 같은 현상을 보고했다. (`research/2025-09-17_02-14-45_suspend_resume_op_end_requeue.md`)

## 호출 흐름
1. `PROGRAM_*` 커밋 → `ResourceManager.register_ongoing` → `_emit_op_events` 로 최초 OP_END 큐잉. (`resourcemgr.py:1031`, `scheduler.py:741`)
2. `PROGRAM_SUSPEND` 커밋 시 `ResourceManager.commit` → `move_to_suspended_axis` → meta 이동 + `remaining_us` 계산, 기존 OP_END 유지. (`resourcemgr.py:626-666`, `resourcemgr.py:1090-1095`)
3. `PROGRAM_RESUME` 커밋 후 Scheduler 체인 → `_build_core_busy_stub` 예약 → `_emit_op_events` 로 새 OP_END 큐잉 → `resume_from_suspended_axis` 로 meta 복귀. (`scheduler.py:683-817`, `resourcemgr.py:1125-1141`)
4. 각 OP_END 실행 시 `_am_apply_on_end` → `apply_pgm` 실행, 중복 이벤트만큼 addr_state 증가. (`scheduler.py:382-494`)

## 코드 참조
- `resourcemgr.py:626` – `PROGRAM_SUSPEND` 분기에서 suspended axis 처리 시작
- `resourcemgr.py:639` – `move_to_suspended_axis` 호출로 ongoing 메타 이동
- `resourcemgr.py:1091` – remaining_us 계산 및 axis별 스택 적재
- `resourcemgr.py:1125` – `resume_from_suspended_axis` 가 suspended 메타를 ongoing 으로 복원
- `scheduler.py:741` – 최초 커밋 시 `_emit_op_events` 로 OP_END 큐잉
- `scheduler.py:683` – resume 체인 준비 로직 진입
- `scheduler.py:803` – 체인 스텁에서도 `_emit_op_events` 호출
- `scheduler.py:811` – 체인 완료 후 `resume_from_suspended_axis` 호출
- `scheduler.py:382` – 모든 OP_END 에서 `_am_apply_on_end` 실행
- `event_queue.py:13` – EventQueue 가 단순 정렬만 수행해 중복 이벤트 유지
- `config.yaml:39` – suspend/resume 체인 기능 플래그 활성화

## 아키텍처 인사이트
- suspend/resume 시나리오에서 ResourceManager/ Scheduler 는 메타 데이터를 axis별 스택으로 이동시키지만, 이벤트 큐와 AddressManager 는 이 맥락을 알지 못해 동일 작업의 OP_END 를 중복 처리한다.
- 체인 스텁은 `_chain_stub` 플래그로 observability 를 구분하지만 이벤트 payload 에는 차별 정보가 없어 핸들러 단계에서 중복을 제어할 수 없다.
- `remaining_us` 가 원래 `end_us` 기준으로 산출되므로 resume 과정에서 추가된 latency 를 반영하지 못해, 반복 suspend 시 stub duration 이 실제 잔여 시간보다 작아질 위험이 있다.

## 역사적 맥락
- `research/2025-09-17_02-14-45_suspend_resume_op_end_requeue.md` – OP_END 중복 큐잉과 addr_state 증가 문제를 선행 분석한 문서.

## 관련 연구
- `research/2025-09-16_23-13-07_program_suspend_resume_sampling.md`

## 미해결 질문
- `OP_END` 중복을 제거하거나 재예약하는 scheduler 레벨 수정이 필요하지만, 기존 메트릭/로그에 어떤 영향이 있는지 정량 검증이 남아 있다.
- Resume 체인 없이 suspend 를 처리하는 옵션(플래그)의 필요성 및 default 값 검토가 필요하다.
