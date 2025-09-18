---
date: 2025-09-18T01:57:32.935751+00:00
researcher: Codex
git_commit: 97e9f5494187fedb0c1a2e081b43c4564f7714f6
branch: main
repository: nandseqgen_v2
topic: "Suspend-resume PROGRAM apply_pgm duplication workflow"
tags: [research, codebase, scheduler, resourcemgr, addrman]
status: complete
last_updated: 2025-09-18
last_updated_by: Codex
---

# 연구: Suspend-resume PROGRAM apply_pgm duplication workflow

**Date**: 2025-09-18T01:57:32.935751+00:00  
**Researcher**: Codex  
**Git Commit**: 97e9f5494187fedb0c1a2e081b43c4564f7714f6  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
PROGRAM 이 예약된 후 완전히 종료되기 전에 SUSPEND→RESUME 을 반복하는 시나리오에서 `apply_pgm` 이 반복 호출되어 비정상적인 page address 샘플링이 일어나는 책임 코드와 작업 흐름은 무엇인가?

## 요약
- 동일 PROGRAM 작업에 대해 초기 예약과 resume 스텁 모두 `Scheduler._emit_op_events` 가 OP_END 이벤트를 큐잉하지만, EventQueue 는 기존 항목을 제거하지 않아 중복 OP_END 가 남는다 (`scheduler.py:741`, `scheduler.py:803`, `event_queue.py:17`).
- `_handle_op_end` 는 모든 OP_END 에서 `_am_apply_on_end` 를 실행하고, PROGRAM 커밋이면 `AddressManager.apply_pgm` 이 블록별 페이지 수를 중복 증가시킨다 (`scheduler.py:408`, `scheduler.py:493`, `addrman.py:607`, `addrman.py:625`).
- suspend 시 ResourceManager 는 남은 실행 시간만 `remaining_us` 로 저장하고 OP_END 스케줄을 건드리지 않아, resume 체인이 기존 이벤트와 동일 `op_uid` 로 새 이벤트를 추가하게 된다 (`resourcemgr.py:626`, `resourcemgr.py:639`, `resourcemgr.py:1091`, `scheduler.py:702`).
- AddressManager 상태가 예상보다 빨리 증가하므로 이후 proposer 의 `sample_pgm` 호출이 다음 주소를 가져와, 사용자 관찰과 같은 비정상적인 page address 샘플링 패턴이 발생한다 (`proposer.py:592`, `proposer.py:630`).

## 상세 발견

### Scheduler
- 최초 PROGRAM 커밋 루프가 `resv_records` 를 순회하면서 `_emit_op_events(rec)` 를 호출해 OP_START/OP_END 페어를 큐잉한다 (`scheduler.py:741`).
- `_emit_op_events` 는 payload 에 `op_uid` 를 넣은 뒤 OP_END 를 그대로 push 하며, dedupe 로직이 없다 (`scheduler.py:887`, `scheduler.py:891`, `scheduler.py:893`).
- suspend 후 `chain_jobs` 가 남은 CORE_BUSY 구간을 `_build_core_busy_stub` 로 재구성하고 동일 `targets`/`op_uid` 로 `_emit_op_events(rec2)` 를 다시 호출한다 (`scheduler.py:702`, `scheduler.py:732`, `scheduler.py:803`).
- resume 스텁의 payload 는 `source="RESUME_CHAIN"` 만 다르고, `_handle_op_end` 단계에서는 구분 정보가 사라져 원본과 같은 방식으로 처리된다 (`scheduler.py:799`, `scheduler.py:408`).

### ResourceManager
- `commit` 의 `PROGRAM_SUSPEND` 분기는 ongoing 메타를 `_suspended_ops_program` 으로 이동시키고 CORE_BUSY 타임라인을 truncate 하지만 EventQueue 와는 독립적으로 동작한다 (`resourcemgr.py:626`, `resourcemgr.py:639`).
- `move_to_suspended_axis` 가 meta 의 `remaining_us` 를 계산해 보관할 뿐, 기존 `end_us` 나 스케줄러 이벤트 핸들을 정리하지 않는다 (`resourcemgr.py:1091`).
- resume 시 스택에서 meta 를 꺼내 다시 ongoing 으로 붙이고, scheduler 가 chain stub 를 예약하면서 원래 OP_END 와 추가 OP_END 가 공존한다 (`resourcemgr.py:1076`, `scheduler.py:702`).

### EventQueue
- `EventQueue.push` 는 (time, priority, seq) 기준으로 append 후 sort 만 수행하며 중복 제거가 없다 (`event_queue.py:17`, `event_queue.py:20`).
- suspend 이전에 큐에 남은 OP_END 와 resume 후 새로 push 된 OP_END 모두 실행 순서대로 처리되어 duplication 을 유발한다.

### AddressManager
- `_handle_op_end` 는 모든 OP_END 에 대해 `_am_apply_on_end` 를 호출한다 (`scheduler.py:382`, `scheduler.py:408`).
- 프로그램 계열이면 `apply_pgm` 이 호출되어 `np.unique`로 블록별 카운트를 세고 `addrstates[uniq] += counts` 로 누적한다 (`scheduler.py:493`, `addrman.py:607`, `addrman.py:625`). 동일 이벤트가 두 번 실행되면 페이지 수가 0→2와 같이 증가한다.
- undo 버퍼는 마지막 호출만 기억하므로 반복 호출 시 원상 복구가 어렵다 (`addrman.py:611`).

### Proposer & Sampling 영향
- proposer 는 base 이름에 "PROGRAM" 이 포함되고 "SUSPEND"/"RESUME" 이 없는 경우에만 AddressManager 에서 샘플링하여 새 페이지를 가져온다 (`proposer.py:592`, `proposer.py:630-646`).
- 중복 `apply_pgm` 으로 인해 block 의 programmed page 수가 앞당겨 증가하면서 후속 `sample_pgm` 호출이 이미 소진된 페이지로 판정해 다른 주소를 선택하게 되어 관측된 비정상 샘플링 패턴을 만든다.

### 작업 흐름 요약
1. PROGRAM 커밋 → `_emit_op_events(rec)` 가 OP_END(t_end) 를 큐잉 (`scheduler.py:741`, `scheduler.py:893`).
2. CORE_BUSY 중 SUSPEND → ResourceManager 가 meta 를 suspended 목록으로 이동, `remaining_us` 저장 (OP_END 큐 유지) (`resourcemgr.py:626`, `resourcemgr.py:639`, `resourcemgr.py:1091`).
3. RESUME → Scheduler 가 chain stub 을 commit 하고 `_emit_op_events(rec2)` 로 OP_END(t_end + remaining) 추가 (`scheduler.py:702`, `scheduler.py:803`).
4. EventQueue 가 두 OP_END 를 모두 순차 실행 → `_handle_op_end` → `_am_apply_on_end` → `apply_pgm` 두 번 호출 (`event_queue.py:17`, `scheduler.py:408`, `scheduler.py:493`).
5. AddressManager 상태 증가 → 다음 PROGRAM 예약에서 proposer 가 다른 페이지를 샘플링하여 비정상 패턴 발생 (`addrman.py:625`, `proposer.py:592`).

## 코드 참조
- `scheduler.py:741` – 최초 PROGRAM 커밋 시 `_emit_op_events(rec)` 호출
- `scheduler.py:702` – resume 체인을 위한 `chain_jobs` 구성
- `scheduler.py:803` – resume 스텁에서도 `_emit_op_events(rec2)` 호출
- `scheduler.py:887-893` – OP_START/OP_END 이벤트가 dedupe 없이 EventQueue 로 push
- `scheduler.py:408` – `_handle_op_end` 가 `_am_apply_on_end` 를 호출
- `scheduler.py:493` – PROGRAM 커밋에서 `am.apply_pgm` 실행
- `resourcemgr.py:626-639` – `PROGRAM_SUSPEND` 처리 및 suspended stack 이동
- `resourcemgr.py:1091` – `move_to_suspended_axis` 가 `remaining_us` 저장
- `event_queue.py:17-20` – EventQueue 가 중복 제거 없이 push/sort 수행
- `addrman.py:607-625` – `apply_pgm` 이 블록별 등장 횟수만큼 페이지 수를 증가
- `proposer.py:592` – PROGRAM 샘플링 진입점
- `proposer.py:630-646` – `_is_addr_sampling_base` 가 SUSPEND/RESUME 을 제외한 PROGRAM 만 샘플링

## 아키텍처 인사이트
- suspend-resume 흐름은 ResourceManager 의 메타 스택과 Scheduler 의 chain stub 로 표현되지만, EventQueue 와 AddressManager 는 체인을 구분하지 못해 동일 작업의 상태 업데이트를 여러 번 반영한다.
- `op_uid` 가 payload 로 전달되지만 큐 레벨에서 dedupe 되지 않아, resume 체인을 안정적으로 식별하려면 EventQueue 또는 `_handle_op_end` 에 보정 로직이 필요하다.
- 샘플링 경로와 상태 적용 경로가 분리되어 있어 상태 중복이 즉시 운영 데이터를 망가뜨리진 않지만, 주소 할당 패턴과 통계에 오차를 유발한다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-17_02-14-45_suspend_resume_op_end_requeue.md` – 동일 현상의 초기 분석으로 OP_END 중복 큐잉과 `apply_pgm` 중복 호출을 먼저 보고.
- `research/2025-09-17_15-57-35_suspend_resume_op_end_dedupe.md` – dedupe 구현 대안과 영향 범위를 정리.

## 관련 연구
- `research/2025-09-16_23-13-07_program_suspend_resume_sampling.md`
- `research/2025-09-17_suspend_resume_baseline_inputs.md`

## 미해결 질문
- EventQueue 수준에서 `op_uid` 기반 dedupe 와 핸들러 수준 guard 중 어느 쪽이 suspend-resume 반복에 더 안전한지 추가 실험이 필요하다.
- PROGRAM 체인 이외에 ERASE 체인에서도 동일 패턴이 재현되는지 확인해야 한다.
- 샘플링 오류가 장시간 워크로드에서 wear-leveling 또는 garbage-collection 전략에 어떤 장기적인 영향을 주는지 계측 필요.

