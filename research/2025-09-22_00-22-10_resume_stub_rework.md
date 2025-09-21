---
date: 2025-09-22T00:22:10.573182+09:00
researcher: Codex
git_commit: f352a740151d2d58d11967905f1bfb2144c06f4b
branch: main
repository: nandseqgen_v2
topic: "Resume stub logic realignment"
tags: [research, scheduler, resourcemgr, addrman, suspend-resume]
status: complete
last_updated: 2025-09-22
last_updated_by: Codex
---

# 연구: Resume stub logic realignment

**Date**: 2025-09-22T00:22:10.573182+09:00  
**Researcher**: Codex  
**Git Commit**: f352a740151d2d58d11967905f1bfb2144c06f4b  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
아래의 목표를 달성하기 위한 구현 방법에 대해서 research 를 진행해
---
-. 비정상적인 apply_pgm, OP_END 중복호출로 인한 비정상적인 PROGRAM page address 증가현상 제거를 위해 현재의 RESUME stub 로직을 변경
-. 새롭게 구현된 로직에서 SUSPEND_RESUME_RULES.md 의 내용을 충실히 반영.

## Problem 1-Pager
- **배경**: suspend→resume 체인을 `_chain_stub` 으로 구현하면서 동일 PROGRAM 작업의 OP_END 이벤트가 복수로 실행되어 상태가 왜곡된다.
- **문제**: `AddressManager.apply_pgm` 이 resume 이후에도 두 번 호출되어 programmed page 주소가 비정상적으로 증가한다.
- **목표**: SUSPEND_RESUME_RULES 를 준수하면서 OP_END/addr_state 가 한 번만 반영되도록 RESUME 스텁 흐름을 재설계할 방안을 도출한다.
- **비목표**: proposer 의 샘플링 알고리즘이나 AddressManager 내부 데이터 모델 전면 재작성.
- **제약**: suspend 동안 동일 die 의 동일 family 가 금지되어야 하며, remaining_us 기반 재개와 ongoing/suspended 메타 이동 규칙을 지켜야 한다.

## 요약
- 기존 체인 스텁은 원래 PROGRAM 커밋에서 push 된 OP_END 이벤트를 제거하지 않은 채, resume 시점에 동일 base 로 새 OP_END 를 추가해 `apply_pgm` 이 중복 실행된다 (`scheduler.py:239`, `scheduler.py:533`, `event_queue.py:17`, `addrman.py:607`).
- ResourceManager 는 suspend 시 남은 시간을 `remaining_us` 에 저장하지만 meta.end_us 를 조정하지 않아 반복 suspend 시 잔여 시간이 0으로 수렴하고, resume 스텁은 원본 이벤트와 분리되지 않은 상태로 다시 등록된다 (`resourcemgr.py:628`, `resourcemgr.py:1091`).
- SUSPEND_RESUME_RULES 는 suspend 시 기존 종료 이벤트가 “무기한 연장” 되어야 하고 resume 직후 remaining 시간만큼만 재개되어야 함을 명시하나, 현재 구현은 큐에 남은 이벤트와 체인 stub 두 경로가 동시에 존재하며 규칙과 어긋난다 (`docs/SUSPEND_RESUME_RULES.md:4`).

## 상세 발견

### Scheduler
- `_handle_op_end` 는 모든 OP_END 에서 `_am_apply_on_end` 를 호출하며, PROGRAM 베이스가 화이트리스트에 있으면 `apply_pgm` 이 실행된다 (`scheduler.py:239`, `scheduler.py:324`).
- `_propose_and_schedule` 는 `PROGRAM_RESUME` 커밋 직후 suspended 메타를 꺼내 `_build_core_busy_stub` 으로 동일 base 의 CORE_BUSY 작업을 예약하고 `_emit_op_events` 로 또 다른 OP_END 를 push 한다 (`scheduler.py:400`, `scheduler.py:533`, `scheduler.py:638`).
- 체인 스텁 레코드는 `_chain_stub` 플래그만으로 구분되며, 이벤트 payload 에는 RESUME 전후를 식별할 정보가 없어 기본 핸들러에서 차별 없이 처리된다.

### ResourceManager
- `commit` 은 `PROGRAM_SUSPEND` 시점에 `_suspended_ops_program` 으로 meta 를 이동시키고 CORE_BUSY 타임라인을 truncate 하지만, 기존 queue 이벤트는 그대로 남는다 (`resourcemgr.py:628`, `resourcemgr.py:665`).
- `move_to_suspended_axis` 는 meta.end_us 를 유지한 채 `remaining_us = end_us - now` 로 계산만 하므로, 이후 resume 스텁이 meta 를 재사용할 때 잔여 시간과 종료 이벤트가 분리되지 않는다 (`resourcemgr.py:1091`).
- `resume_from_suspended_axis` 는 스택에서 meta 를 꺼내 그대로 ongoing 리스트에 push 하며, 체인 스텁 종료 후에도 원래 meta 의 end_us 가 과거 값을 유지한다 (`resourcemgr.py:1135`).

### EventQueue
- `EventQueue.push` 는 단순 append + sort 로직으로 중복 이벤트 제거 기능이 없다. suspend 전 등록된 OP_END 와 resume 스텁이 새로 푸시한 OP_END 가 동일 시간대에 공존한다 (`event_queue.py:17`).

### AddressManager
- `apply_pgm` 은 블록별 등장 횟수를 누적하여 programmed page 수를 증가시키므로, 동일 block 이 중복 등장하면 그만큼 addr_state 가 증가한다 (`addrman.py:607`).
- undo 버퍼는 마지막 호출만 되돌릴 수 있어 중복 호출을 사후 복원하기 어렵다.

### 규칙 대비
- 규칙은 suspend 시 “예정된 종료 이벤트는 무기한 연장” 되어야 한다고 명시하지만, 실제 구현은 동일 종료 이벤트를 그대로 둔 채 resume 스텁에서 또 다른 종료 이벤트를 추가한다 (`docs/SUSPEND_RESUME_RULES.md:4`).
- resume 직후 remaining 시간이 지난 뒤 종료되도록 스케줄해야 하나, 현재는 original 이벤트가 선행 실행되면 remaining 시간이 의미를 잃고 주소가 먼저 증가한다.

## 대안 비교
1. **대안 A (이벤트 재스케줄)**: suspend 시 `_emit_op_events` 가 반환한 OP_END 이벤트 핸들을 추적해 큐에서 제거하고 resume 시 단일 OP_END 만 재등록 — 장점: apply 경로가 한 번만 실행되어 규칙을 그대로 따름; 단점: EventQueue 가 핸들 제거 API 를 제공하지 않아 구조 개편이 필요; 위험: 잘못된 큐 조작이 전체 이벤트 순서를 깨뜨릴 수 있음.
2. **대안 B (중복 가드 + 메타 보정)**: resume 스텁에서 원본 meta 의 `end_us`/`op_uid` 를 갱신하고 `_am_apply_on_end` 가 `payload['source'] == 'RESUME_CHAIN'` 인 2차 이벤트만 처리하도록 가드 — 장점: 큐 구조를 유지하면서 apply 중복을 차단; 단점: 이벤트 소스를 신뢰해야 하며 새로운 플래그 전파가 필요; 위험: 가드 조건이 어긋나면 실제 완료 이벤트까지 무시될 수 있음.
3. **대안 C (체인 스텁 제거)**: resume 시 남은 시간을 기존 meta 에 직접 더해 동일 PROGRAM 작업을 재예약하고, CORE_BUSY stub 없이 원본 레코드의 OP_END 시각만 갱신 — 장점: 규칙의 “remaining 시간 재개” 를 가장 직관적으로 반영; 단점: 현재 구조에 없는 “기존 예약 시간 갱신” 기능을 추가해야 함; 위험: quantize/충돌 처리 실수 시 재예약 실패로 deadlock 발생 가능.

## 코드 참조
- `scheduler.py:239` – `_handle_op_end` 가 모든 OP_END 에서 `_am_apply_on_end` 를 호출.
- `scheduler.py:324` – PROGRAM 계열 화이트리스트가 addr_state 커밋을 허용.
- `scheduler.py:533` – `PROGRAM_RESUME` 체인 스텁이 잔여 시간을 이용해 새 작업을 등록.
- `scheduler.py:638` – 체인 스텁에서도 `_emit_op_events` 로 OP_END 이벤트를 다시 push.
- `resourcemgr.py:628` – suspend 시 ongoing meta 를 axis 별 stack 으로 이동하고 remaining_us 를 계산.
- `resourcemgr.py:1091` – meta.remaining_us 계산 및 stack push.
- `resourcemgr.py:1135` – resume 시 suspended meta 를 그대로 ongoing 으로 복귀.
- `event_queue.py:17` – EventQueue 가 append/sort 만 수행해 중복 이벤트를 제거하지 않음.
- `addrman.py:607` – `apply_pgm` 이 block 등장 횟수만큼 programmed page 를 증가.
- `docs/SUSPEND_RESUME_RULES.md:4` – suspend 시 종료 이벤트를 중단하고 remaining 시간 기반으로 resume 해야 함을 명시.

## 아키텍처 인사이트
- suspend/resume 체인은 ResourceManager 의 meta 스택과 Scheduler 이벤트 체인 사이에서 명시적 식별자가 부족해, 동일 작업의 완료 이벤트를 구분하지 못한다.
- remaining_us 가 meta.end_us 와 동기화되지 않아 반복 suspend 시 시간이 0 으로 수렴하며, rules 문서가 요구하는 “무기한 연장” semantics 와 어긋난다.
- EventQueue 가 핸들 기반 조작을 지원하지 않아 resume 흐름을 안전하게 재스케줄하려면 큐 추상화 자체를 확장하거나, Scheduler 단계에서 중복을 감지해야 한다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-18_01-57-32_suspend_resume_apply_pgm_repeat.md` – OP_END 중복으로 인한 addr_state 증가를 최초 분석.
- `research/2025-09-18_08-13-53_suspend_resume_addr_state.md` – suspend/resume 체인과 addr_state 이상 증가 간의 상관관계 정리.
- `research/2025-09-18_12-51-24_resume_stub_remaining_us_meta.md` – remaining_us 메타가 0으로 소진되는 현상과 임시 보정 실험 기록.

## 관련 연구
- `research/2025-09-17_02-14-45_suspend_resume_op_end_requeue.md`
- `research/2025-09-16_23-13-07_program_suspend_resume_sampling.md`

## 미해결 질문
- EventQueue 수준에서 안전하게 특정 OP_END 를 연기/삭제할 수 있는 API 를 도입할지, 혹은 Scheduler 에서 중복을 감지하는 가드가 더 실용적인지 결정 필요.
- resume 시 meta.end_us 를 갱신할 때 quantize 오차와 remaining_us 재계산 순서를 어떻게 정의해야 반복 suspend 에서 일관성을 유지할지 추가 검증이 필요.
- ERASE 체인에도 동일한 보정이 적용되어야 하는지, PROGRAM 전용 해결책으로 충분한지 실험이 필요.
