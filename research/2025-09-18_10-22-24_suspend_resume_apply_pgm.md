---
date: 2025-09-18T10:22:24.298675+09:00
researcher: Codex
git_commit: 96093c66ec34b7ff8d917ed7850af3f35cb24679
branch: main
repository: nandseqgen_v2
topic: "Suspend-resume PROGRAM apply_pgm duplication"
tags: [research, scheduler, resourcemgr, event-queue, addrman, suspend-resume]
status: complete
last_updated: 2025-09-18
last_updated_by: Codex
---

# 연구: Suspend-resume PROGRAM apply_pgm duplication

**Date**: 2025-09-18T10:22:24.298675+09:00  
**Researcher**: Codex  
**Git Commit**: 96093c66ec34b7ff8d917ed7850af3f35cb24679  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
PROGRAM 이 예약된 후 완전히 종료되기 전에 SUSPEND→RESUME 을 반복하는 시나리오에서 `apply_pgm` 이 반복 호출되어 비정상적인 page address 샘플링이 일어나는 책임 코드와 작업 흐름은 무엇인가?

## 요약
- PROGRAM 최초 커밋과 RESUME 체인 스텁 모두에서 `_emit_op_events` 가 동일 대상의 OP_START/OP_END 이벤트를 큐에 추가해 중복 OP_END 가 남는다 (`scheduler.py:726`, `scheduler.py:827`, `scheduler.py:913`).
- EventQueue 는 단순 정렬만 수행해 동일 작업의 OP_END 를 제거하지 않으며 (`event_queue.py:17`), 각 항목은 `_handle_op_end` 를 통해 AddressManager 동기화를 강제한다 (`scheduler.py:410`).
- PROGRAM 계열 OP_END 는 `_am_apply_on_end` 내부에서 `AddressManager.apply_pgm` 을 호출하고, 이 함수는 블록별 등장 횟수만큼 페이지 카운터를 증가시키므로 중복 실행이 그대로 누적된다 (`scheduler.py:511`, `addrman.py:607`).
- 동일 블록의 `addrstates` 가 두 배 증가하면 이후 `sample_pgm` 호출이 "다음 페이지"로 가정하는 값이 건너뛰게 되어 비정상적인 페이지 샘플이 관측된다 (`addrman.py:552`).

## 상세 발견

### Scheduler 이벤트 배출과 체인 스텁
- `_propose_and_schedule` 은 PROGRAM_RESUME 커밋 직후 suspend 메타의 남은 시간을 읽어 `chain_jobs` 에 원본 targets/op_name 을 보관한다 (`scheduler.py:726`).
- 체인 스텁이 커밋되면 `_emit_op_events` 를 재호출해 기존 프로그램과 동일한 targets 로 OP_END 를 재발행한다 (`scheduler.py:827`).
- `_emit_op_events` 는 `op_uid` 가 None 이면 payload 에 식별자를 넣지 않아 큐에서 중복을 구분할 수 없다 (`scheduler.py:913`).

### ResourceManager 의 suspend 메타 관리
- `commit` 경로는 `PROGRAM_SUSPEND` 시점에 ongoing 메타를 축적하고 타임라인을 자른 뒤에도 원래 `end_us` 를 유지한다 (`resourcemgr.py:639`).
- `move_to_suspended_axis` 는 남은 실행 시간을 `remaining_us` 로 기록할 뿐 기존 OP_END 정보를 재예약하지 않는다 (`resourcemgr.py:1088`).
- `suspended_ops_program` 은 resume 직전에 동일 targets 과 `remaining_us` 를 그대로 노출해 체인 스텁이 동일 프로그램을 재사용하도록 만든다 (`resourcemgr.py:1011`).

### EventQueue 와 OP_END 처리
- EventQueue 는 삽입 시 (time, priority, seq) 정렬만 수행하며 중복 항목 제거 로직이 없다 (`event_queue.py:17`).
- Tick 처리 중 모든 OP_END 는 `_handle_op_end` 를 거쳐 AddressManager 동기화를 강제하므로 중복 이벤트도 동일하게 적용된다 (`scheduler.py:410`).

### AddressManager 상태 갱신과 샘플 영향
- `_am_apply_on_end` 는 PROGRAM 커밋에 대해 `apply_pgm` 을 호출하도록 허용 목록을 체크한다 (`scheduler.py:511`).
- `apply_pgm` 은 대상 블록별 등장 횟수를 누적해 `addrstates` 를 증가시키므로 두 번째 OP_END 가 동일 블록을 다시 올린다 (`addrman.py:618`).
- `sample_pgm` 은 다음 프로그램 페이지를 현재 `addrstates + 1` 로 계산하므로 증가가 두 배가 된 블록은 "예상보다 앞선" 페이지를 반환하게 된다 (`addrman.py:552`).

### Workflow Trace
1. PROGRAM 커밋 → `_emit_op_events` 가 최초 OP_END 를 큐잉 (`scheduler.py:916`).
2. Suspend 발생 → ResourceManager 가 메타를 `remaining_us` 와 함께 보관 (`resourcemgr.py:1088`).
3. Resume 커밋 → Scheduler 가 동일 targets 로 CORE_BUSY 스텁을 커밋하고 두 번째 OP_END 를 큐잉 (`scheduler.py:827`, `scheduler.py:916`).
4. EventQueue 는 두 OP_END 를 모두 보관하여 실행 시간에 도달하면 순차 처리한다 (`event_queue.py:20`).
5. 각 OP_END 가 `_handle_op_end` → `apply_pgm` 으로 이어져 페이지 카운터가 두 번 증가한다 (`scheduler.py:410`, `addrman.py:625`).
6. 이후 PROGRAM 샘플링은 증가된 `addrstates` 를 기준으로 다음 페이지를 선택해 비정상 주소가 관찰된다 (`addrman.py:552`).

## 코드 참조
- `scheduler.py:726` – Resume 직후 `chain_jobs` 에 원본 PROGRAM 메타를 복사.
- `scheduler.py:827` – 체인 스텁 커밋 시 `_emit_op_events` 재호출.
- `scheduler.py:913` – `op_uid` 가 없으면 이벤트 payload 에 식별자 미부여.
- `scheduler.py:410` – 모든 OP_END 에 대해 `_am_apply_on_end` 실행.
- `scheduler.py:511` – PROGRAM 허용 목록에 포함되면 `apply_pgm` 호출.
- `event_queue.py:17` – 이벤트 큐 삽입 시 단순 정렬만 수행.
- `resourcemgr.py:639` – `PROGRAM_SUSPEND` 시 ongoing 메타를 축적.
- `resourcemgr.py:1088` – suspend 이동 시 잔여 시간만 계산.
- `resourcemgr.py:1011` – `suspended_ops_program` 으로 동일 targets/remaining_us 노출.
- `addrman.py:607` – `apply_pgm` 이 블록별 등장 횟수만큼 상태 증가.
- `addrman.py:552` – `sample_pgm` 이 `addrstates + 1` 로 다음 페이지를 선택.

## 아키텍처 인사이트
- Scheduler 와 ResourceManager 는 suspend-resume 체인을 메타 스택과 CORE_BUSY 스텁으로 모델링하지만, 큐 레벨에서는 중복을 식별할 키(`op_uid`) 가 없어서 재개된 작업을 새로운 커밋으로 취급한다.
- EventQueue 가 시간/우선순위 기반 정렬만 수행하기 때문에 중복 제거나 재예약 책임은 Scheduler 에 남아 있으며, 현재 구현은 이를 수행하지 않는다.
- AddressManager 는 idempotent guard 가 없어 입력 횟수에 민감하므로, 스케줄링 계층에서 중복 방지가 필수다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-17_02-14-45_suspend_resume_op_end_requeue.md` – 동일 현상을 최초 분석하고 OP_END 중복을 보고.
- `research/2025-09-17_15-57-35_suspend_resume_op_end_dedupe.md` – op_uid 기반 큐 dedupe 설계를 제안.

## 관련 연구
- `research/2025-09-16_23-13-07_program_suspend_resume_sampling.md`
- `research/2025-09-17_suspend_resume_baseline_inputs.md`

## 미해결 질문
- Scheduler 가 validation 비활성 시에도 안정적인 `op_uid` 를 부여해 큐 dedupe 를 수행할 것인지 결정 필요.
- EventQueue 수준에서 동일 targets/시간의 OP_END 를 합치는 것이 안전한지 검증이 요구된다.
- 체인 스텁 대신 원본 OP_END 시각을 갱신하는 방향이 suspend-resume 재현성에 어떤 영향을 주는지 추가 실험이 필요하다.
