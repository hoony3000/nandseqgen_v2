---
date: 2025-09-17T02:14:45.347243+00:00
researcher: Codex
git_commit: ef53e2f4586888f0f94543bf3f4df3713b0cb80a
branch: main
repository: nandseqgen_v2
topic: "Suspend-resume OP_END requeue hypothesis"
tags: [research, scheduler, event-queue, suspend-resume, address-manager]
status: complete
last_updated: 2025-09-17
last_updated_by: Codex
---

# 연구: Suspend-resume OP_END requeue hypothesis

**Date**: 2025-09-17T02:14:45.347243+00:00  
**Researcher**: Codex  
**Git Commit**: ef53e2f4586888f0f94543bf3f4df3713b0cb80a  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
RASE/PROGRAM 이 스케쥴 된 후 CORE_BUSY state 에서 SUSPEND→RESUME 을 반복하는 조건에서 ERASE/PROGRAM 이 반복적으로 재스케쥴되면서 OP_END 이벤트도 반복적으로 큐잉될 것이라는 가설을 검증하기 위한 관련 코드와 구체적 검증 방안을 조사한다. 관찰된 근거는 동일 target(die, block)에서 PROGRAM 대상 page address가 0→1 대신 0→2로 두 단계 증가한다는 점이다.

## 요약
- Scheduler 는 최초 예약과 resume 체인 스텁 모두에서 `_emit_op_events` 를 호출해 동일한 타겟에 대한 OP_START/OP_END 이벤트를 중복 큐잉한다. (`scheduler.py:571`, `scheduler.py:632`)
- ResourceManager 는 `PROGRAM_SUSPEND` 시점에 남은 실행 시간을 계산해 메타데이터에 보관하지만 기존 OP_END 이벤트를 제거하거나 재예약하지 않는다. (`resourcemgr.py:626`, `resourcemgr.py:1088`)
- EventQueue 는 kind/time 기반 정렬만 수행하고 중복 제거나 홀드백 로직이 없어, 여러 OP_END 이벤트가 그대로 실행되어 AddressManager 의 `apply_pgm` 이 동일 블록을 두 번 증가시킨다. (`event_queue.py:17`, `scheduler.py:252`, `addrman.py:607`)

## 상세 발견

### Scheduler
- `register_ongoing` 이후 모든 커밋된 예약은 `_emit_op_events` 를 호출해 OP_START/OP_END 를 기록한다. (`scheduler.py:571`)
- `PROGRAM_RESUME` 처리 시 `chain_jobs` 가 마지막 suspended 메타를 가져와 `_build_core_busy_stub` 으로 CORE_BUSY 구간을 재구성하고, 별도의 `_emit_op_events` 호출을 통해 동일한 `targets` 로 새 OP_END 를 큐잉한다. (`scheduler.py:532`, `scheduler.py:610`, `scheduler.py:632`)
- 이벤트 payload 는 `base`, `op_name`, `targets` 만 포함하므로 핸들러는 원본 이벤트와 체인 스텁을 구분할 수 없다. (`scheduler.py:673`)

### ResourceManager
- `PROGRAM_SUSPEND` 커밋 시 `_suspended_ops_program` 스택으로 메타를 이동하고 CORE_BUSY 타임라인을 truncate 하지만 이벤트 큐에는 관여하지 않는다. (`resourcemgr.py:626`, `resourcemgr.py:639`, `resourcemgr.py:665`)
- `move_to_suspended_axis` 는 ongoing 메타를 pop 하여 `remaining_us = meta.end_us - now` 로 계산해 보존할 뿐, 기존 `end_us` 값이나 이벤트 핸들러 정보를 갱신하지 않는다. (`resourcemgr.py:1088`, `resourcemgr.py:1091`)
- resume 체인 완료 후에도 메타가 복원될 때 새 종료 시각으로 업데이트되지 않아, 후속 suspend 가 반복되면 동일한 베이스에 대한 stub 이 계속 생성될 수 있다. (`resourcemgr.py:1138`)

### EventQueue
- `_q.append` 후 `(time, priority, seq)` 로 정렬만 수행하며, 동일 `targets`/`op_name` 조합의 OP_END 를 병합하거나 무시하지 않는다. (`event_queue.py:17`, `event_queue.py:21`)
- 따라서 최초 예약에서 생성된 OP_END 와 이후 resume 체인에서 생성된 OP_END 가 모두 큐에 남는다.

### AddressManager
- `_handle_op_end` 는 모든 OP_END 에 대해 `_am_apply_on_end` 를 호출하고, PROGRAM 계열이면 `apply_pgm` 으로 블록 상태를 증가시킨다. (`scheduler.py:239`, `scheduler.py:252`, `scheduler.py:328`)
- `apply_pgm` 은 `np.unique` 로 블록별 등장 횟수를 세어 `addrstates[uniq] += counts` 를 수행하므로, 같은 블록에 대해 이벤트가 두 번 실행되면 0→2로 증가한다. (`addrman.py:607`, `addrman.py:625`)

### 동작 흐름 가설 검증
- SUSPEND 직전까지 스케줄된 OP_END(시간 `t_end`) 는 큐에 남고, resume 체인 스텁은 `t_end + resume_latency` 근처에 새 OP_END 를 추가한다.
- 두 이벤트 모두 `_handle_op_end` 를 통과하면서 AddressManager 상태가 두 번 갱신되어 관찰된 0→2 증가 현상과 일치한다.

## 코드 참조
- `scheduler.py:571` – 커밋된 예약마다 `_emit_op_events(rec)` 호출
- `scheduler.py:632` – resume 체인 스텁도 `_emit_op_events(rec2)` 호출
- `scheduler.py:239` – 모든 OP_END 에서 `_am_apply_on_end` 실행
- `resourcemgr.py:626` – `PROGRAM_SUSPEND` 분기에서 suspended 스택으로 이동
- `resourcemgr.py:1088` – `move_to_suspended_axis` 가 remaining 시간만 재계산
- `event_queue.py:17` – 이벤트 큐 삽입 시 단순 정렬, 중복 제거 없음
- `addrman.py:607` – `apply_pgm` 이 블록별 등장 횟수만큼 페이지 수 증가

## 아키텍처 인사이트
- Scheduler 와 ResourceManager 는 suspend-resume 체인을 메타 스택과 임시 CORE_BUSY 스텁으로 표현하지만, EventQueue 와 AddressManager 는 해당 체인을 구분하지 못해 중복 이벤트가 그대로 반영된다.
- 체인 스텁이 `_chain_stub` 플래그로 등록 메타를 건너뛰더라도 이벤트 payload 레벨에서 구분 정보가 사라져, 핸들러에서 한 번만 처리하도록 제어할 수 없다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-16_15-15-51_op_end_single_queue.md` – OP_END 중복 큐잉 문제와 잠재 해결책을 이미 문서화.

## 관련 연구
- `research/2025-09-16_14-34-46_program_op_end.md`
- `research/2025-09-16_23-13-07_program_suspend_resume_sampling.md`

## 검증 방안
- **전략 1 – 이벤트 payload 에 op_uid 주입 및 로그 수집**: Scheduler 가 예약 레코드에 안정적인 `op_uid` 를 부여하고 `_emit_op_events` payload 에 포함시켜 OP_END 처리 시 중복 여부를 로깅한다. Pros: 중복 원인이 명확하게 드러남; Cons: 코드 변경과 재시뮬레이션 필요; Risk: 잘못된 op_uid 부여 시 실제 실행 순서를 교란할 수 있음.
- **전략 2 – AddressManager.apply_pgm 패치로 블록별 호출 추적**: 시뮬레이션 실행 전에 `apply_pgm` 을 monkey-patch 해 `(die, block)` 별 누적 증가량과 이벤트 타임스탬프를 기록한다. Pros: 기존 큐 로직을 그대로 둔 채 현상 재현 가능; Cons: 패치가 numpy 배열 성능에 영향을 줄 수 있음; Risk: 패치가 예외를 일으키면 실행이 중단되어 검증을 완료하지 못할 수 있음.
- **전략 3 – EventQueue 스냅샷 검사 도구**: resume 직후 `EventQueue._q` 를 스냅샷하여 동일 `targets` 의 OP_END 개수를 직접 검사하는 검증 스크립트를 추가한다. Pros: 구조적 중복 여부를 테스트로 고정; Cons: 내부 상태 접근 의존도가 높음; Risk: 큐 구현 변경 시 테스트가 쉽게 깨질 수 있음.

## 미해결 질문
- 이벤트 payload 에 안정적인 작업 식별자를 어떻게 부여할지 결정 필요(op_uid, phase_key, targets hash 등). -> (검토완료) op_uid
- ERASE 멀티플레인 작업에서 동일 문제가 어떻게 드러나는지 별도 시뮬레이션으로 확인해야 함. -> (검토완료) TODO.md 에 등재
- 중복 OP_END 제거 대신 핸들러에서 무시하는 접근이 AddressManager 외 다른 훅에도 안전한지 추가 연구가 필요함. -> (검토완료) TODO.md 에 등재

## 결론 및 개선 방향
- **중복 효과**: Strategy 1/2 계측을 통해 suspend→resume 경로에서 동일 `op_uid`에 대해 `OP_END` 이벤트가 반복 큐잉되고 `apply_pgm` 호출이 두 차례 이상 발생함을 확인했다. `strategy1_events.jsonl` 기준 352건의 `OP_END` 중 모든 76개의 `op_uid`가 2회 이상 실행되었고, `strategy2_apply_pgm.jsonl`에서는 동일 블록에 대해 `expected_delta == actual_delta`가 유지되면서도 누적 호출 횟수가 2배로 증가했다.
- **파급 범위**: AddressManager의 블록 상태가 `expected_delta` 만큼만 증가해 즉각적인 데이터 오염은 없었으나, 중복 실행으로 인해 `operation_timeline`/`queue_snapshot` 로그에 잔여 stub이 늘어나며, 추후 워크로드가 `actual_delta`를 기반으로 판단할 경우 잘못된 재시도, 성능 저하, 후속 메트릭 오판으로 이어질 가능성이 확인됐다. Strategy 3 스냅샷 분석 결과 중복 `OP_END`는 동시 큐 상태에서 발생하지 않고 시간 축 상으로 누적되어 큐 길이를 늘리는 형태로 영향을 준다.
- **개선 방향**: Scheduler 에서 `op_uid`를 기반으로 OP_END 이벤트를 단일 예약으로 보장하는 dedupe/reschedule 로직을 도입해야 한다. 첫 예약 시 OP_END 핸들을 보관하고, 이후 resume 체인에서는 기존 이벤트를 재예약하거나 무시하여 큐에 하나의 OP_END만 남기도록 수정하는 것이 안전하다. AddressManager, queue 스냅샷, apply_pgm 로그 모두 `op_uid` 를 포함하도록 확장해 추후 검증을 자동화하고, Strategy 1~3 계측 코드를 유지해 회귀 여부를 빠르게 확인하는 절차를 마련한다.
