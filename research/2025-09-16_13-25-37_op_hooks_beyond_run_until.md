---
date: 2025-09-16T13:25:37+0900
researcher: Codex
git_commit: ca1c11a4fbac51d59b3f4fe8052a2911963d7781
branch: main
repository: nandseqgen_v2
topic: "Handling of OP_START/OP_END hooks when operations extend past run_until"
tags: [research, codebase, scheduler, instrumentation]
status: complete
last_updated: 2025-09-16
last_updated_by: Codex
last_updated_note: drain 통합 위치(Scheduler vs main) 비교 연구 추가
---

# 연구: Handling of OP_START/OP_END hooks when operations extend past run_until

**Date**: 2025-09-16T13:25:37+0900
**Researcher**: Codex
**Git Commit**: ca1c11a4fbac51d59b3f4fe8052a2911963d7781
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
main.py —num_run >= 2 이상인 조건에서 Scheduler.run(run_until_us) 을 넘어서서 예약된 operation 이 있을 경우, OP_START, OP_END event_hook 이 어떻게 처리되는지 연구한다.

## 요약
- Scheduler.run() only checks the stop condition _before_ each tick using the current now_us. Operations whose start time is at or before the run limit have both OP_START and OP_END emitted, but runs terminate immediately after the OP_END tick advances now_us past run_until. (`scheduler.py:108-157`, `scheduler.py:636-709`)
- For reservations whose start time itself lies beyond run_until_us, the loop still executes a tick to advance to the OP_START time, emits OP_START, and then stops without ever dequeuing the later OP_END event. The OP_END hook (and _handle_op_end side effects) are therefore skipped. (`scheduler.py:108-157`, `scheduler.py:202-292`, `event_queue.py:17-33`)
- In multi-run mode main.py creates a fresh InstrumentedScheduler per run and never replays leftover events, so any OP_END left in the queue at the stop boundary is dropped. ResourceManager state persists across runs, but hook emissions do not. (`main.py:978-1252`)

## 상세 발견

### Scheduler loop behavior
- `Scheduler.run` advances until `_hooks` budget or `now_us >= run_until_us`. The boundary check happens before calling `tick`, so the final tick can advance `now_us` beyond the limit while still delivering queued events. (`scheduler.py:108-123`)
- `Scheduler.tick` pops the earliest time batch from `EventQueue`, advances `now_us`, and processes events in priority order. OP_END triggers `_handle_op_end`, PHASE_HOOK drives new proposals, QUEUE_REFILL reschedules itself, OP_START is a no-op. (`scheduler.py:125-157`, `scheduler.py:202-214`)
- `_emit_op_events` enqueues OP_START/OP_END at exact start/end timestamps for each committed reservation. (`scheduler.py:636-680`)

### Operations ending after run_until_us
- When start_us ≤ run_until_us < end_us, the OP_END event is still dequeued because the scheduler executes the tick at end_us before evaluating the stop condition again. Both hooks fire. (`scheduler.py:108-157`, `event_queue.py:17-33`)
- When start_us itself is greater than run_until_us, the scheduler performs one more tick to reach the first OP_START timestamp, then immediately exits with now_us > run_until_us. The OP_END event (at a later timestamp) remains in the queue and never executes, so `_handle_op_end`’s release logic (address manager sync, resource release) does not fire. (`scheduler.py:108-157`, `scheduler.py:202-292`)

### Multi-run orchestration
- `run_once` instantiates a new InstrumentedScheduler per run, aligned to the ResourceManager’s current max avail, and invokes `run(run_until_us=t_end)`. (`main.py:978-992`)
- The multi-run loop (`args.num_runs > 1`) reuses the same ResourceManager/AddressManager but discards the scheduler after exporting rows. Any leftover queued events (including skipped OP_END) are not drained in later runs. (`main.py:1170-1252`)

## 코드 참조
- `scheduler.py:108-157` — while-loop stop condition and tick sequencing.
- `scheduler.py:202-292` — `_handle_op_end` releases resources and AddressManager state; only fires when OP_END dequeues.
- `scheduler.py:636-709` — `_emit_op_events` enqueues OP_START/OP_END/PHASE_HOOK events.
- `event_queue.py:17-33` — EventQueue ordering ensures OP_START/OP_END fire strictly by timestamp and priority.
- `main.py:978-992` — run_once builds scheduler with absolute run_until and collects timeline rows per run.
- `main.py:1170-1252` — multi-run loop shares ResourceManager while discarding scheduler after each run.

## 아키텍처 인사이트
- Event-driven sequencing relies on OP_END hooks to synchronize AddressManager and release resources; losing those hooks (when start_us > run_until) can leave state unsynchronized for the next run.
- The stop condition uses the scheduler’s current now_us rather than the timestamp of the next queued event, so reservations beyond the run window may need explicit draining if their eventual hooks matter.

## 역사적 맥락(thoughts/ 기반)
- `thoughts/` 디렉터리가 존재하지 않아 관련 히스토리를 찾지 못했다.

## 관련 연구
- (none)


## 후속 연구 2025-09-16T13:38:05+0900

**Follow-up Question**: run_once가 run_until 경계에서 중단된 뒤 남아 있는 OP_END 이벤트를 즉시 처리해야 하는가, 아니면 다음 run에서 다시 제출해야 하는가?

### Observations
- `run_once`는 매 run 종료 시 새 `InstrumentedScheduler`를 폐기하므로 큐에 남은 이벤트를 재생하지 않는다. (`main.py:978-992`)
- OP_END이 실행되는 경로는 `_handle_op_end` 하나이며, 여기에서 AddressManager 동기화와 latch 해제를 수행한다. (`scheduler.py:202-217`)
- OP_END가 건너뛰어지면 `ResourceManager.release_on_dout_end`가 호출되지 않아 latch가 계속 남고, ERASE/PROGRAM 종료 시점의 AddressManager 갱신도 발생하지 않는다. (`resourcemgr.py:691-694`)
- EventQueue는 동일 타임스탬프에서 OP_END를 우선 처리하도록 설계되어 있지만, run_until 경계에서는 큐가 비워지기 전에 루프를 종료한다. (`event_queue.py:6-33`, `scheduler.py:108-157`)

### Options
1. **Bounded drain at shutdown**: run 종료 직후 남은 예약 중 `end_us > run_until_us` 항목만 골라 `_handle_op_end`를 호출한다. PHASE_HOOK/QUEUE_REFILL를 재생하지 않으므로 run_until 이후 추가 proposal을 막을 수 있다.
2. **Deferred replay**: 다음 run 시작 전에 누락된 OP_END 정보를 재주입(예: snapshot 기반 재생)하여 새 Scheduler가 훅을 발사하도록 한다. EventQueue 복원이나 `_handle_op_end` 직접 호출이 필요해 구현 부담이 크다.

### Recommendation
- 드레인 방식이 AddressManager·latch 일관성을 즉시 회복시켜 다음 run에서의 예약 실패 위험을 줄인다. Deferred replay는 새 Scheduler에 별도의 큐 복원을 요구하므로 복잡도 및 중복 실행 위험이 크다.

### Risks
- `_handle_op_end`를 직접 호출하려면 원본 payload를 보존해야 하며, 잘못된 필터링은 OP_END를 중복 실행할 수 있다.
- 드레인 과정에서 PHASE_HOOK가 실수로 실행되면 run_until 이후 새로운 예약이 발생해 시뮬레이션 경계를 깨뜨릴 수 있다.


## 후속 연구 2025-09-16T13:54:27+0900

**Follow-up Question**: 드레인 단계에서 OP_END payload를 재사용하고 PHASE_HOOK 오발동을 방지하려면?

### Observations
- `EventQueue._q`는 남은 이벤트의 원본 payload를 보존하므로 여기서 바로 꺼내면 proposer가 전달한 targets를 그대로 사용할 수 있다. (`event_queue.py:17-33`)
- `_emit_op_events`가 OP_START/OP_END/PHASE_HOOK을 유일하게 생성하므로 큐에서 꺼낸 OP_END payload는 `_handle_op_end`가 기대하는 구조와 동일하다. (`scheduler.py:636-709`)
- `_handle_op_end`의 부수효과는 RM latch 해제와 AddressManager 동기화에 국한되어 있어 대응되는 큐 항목만 소비하면 안전하게 재호출할 수 있다. (`scheduler.py:202-217`, `resourcemgr.py:691-698`)

### Mitigation Strategy
- run 종료 직후 `Scheduler.drain_pending_end_events(cutoff_us)` 헬퍼를 호출한다.
  1. `self._eq._q`를 시간/우선순위 순으로 순회하며 `kind == "OP_END"`인 항목을 선별한다.
  2. 저장된 payload를 그대로 `_handle_op_end`에 전달하고, 동일 엔트리를 큐에서 제거해 중복 실행을 방지한다.
  3. `(when, payload.get('op_name'), id(payload.get('targets')))` 같은 서명을 기록해 헬퍼가 반복 호출되더라도 같은 이벤트를 두 번 처리하지 않도록 한다.
- OP_END를 소모한 뒤 `PHASE_HOOK`, `QUEUE_REFILL` 항목을 `cutoff_us` 이후 시간에 한해 제거해 run_until 이후 새로운 제안을 차단한다.

### Verification
- READ→DOUT 시퀀스가 run_until을 넘기도록 구성한 테스트를 만들고, 헬퍼 호출 후 `rm.ongoing_ops()`가 비어 있으며 해당 plane latch가 해제됐는지 확인한다.
- `source == "RESUME_CHAIN"` 인 체인 스텁에 대해 drain 서명과 exporter timeline(`op_uid`)을 비교해 OP_END가 정확히 한 번만 실행됐는지 검증한다.

### Residual Risks
- 부트스트랩 활성화 시 경계에 걸린 QUEUE_REFILL 제거가 의도치 않은 진행 중단을 만들 수 있으므로, 기능 플래그로 헬퍼 활성화를 제어하거나 부트스트랩 단계에서는 큐 정리를 건너뛰는 조건이 필요하다.


## 후속 연구 2025-09-16T14:39:09+0900

**Follow-up Question**: drain 헬퍼가 RESUME 체인 메타와 부트스트랩 이벤트를 누락하지 않는다는 것을 어떻게 보증할까?

### Observations
- RESUME 체인 스텁은 `_propose_and_schedule`에서 `_chain_stub` 플래그와 `source="RESUME_CHAIN"`을 달고 예약되며, 커밋 직후 `_emit_op_events`로 OP_START/OP_END가 큐에 들어간다. (`scheduler.py:547-596`)
- 체인 스텁이 성공적으로 끝나면 `rm.resume_from_suspended_axis`가 호출되어 해당 die의 `suspended_ops_*` 리스트가 비워진다. (`scheduler.py:600-609`, `resourcemgr.py:1070-1149`)
- BootstrapController는 커밋 단계에서만 stage를 진전시키며, Queue 이벤트 자체에는 부트스트랩 지표가 없다. 하지만 drain 시 `QUEUE_REFILL`을 제거하면 다음 hook가 없을 수 있으므로 새 run 초기화가 동일한 이벤트를 다시 넣어주는지 확인해야 한다. (`scheduler.py:125-157`, `scheduler.py:191-197`, `bootstrap.py:29-77`)

### Safeguards
1. **Resume-chain reconciliation**
   - drain 수행 전후로 `rm.suspended_ops_erase()`와 `rm.suspended_ops_program()`을 스냅샷하여 잔여 항목이 있는지 검사한다.
   - drain 후에도 항목이 남아 있으면 로그에 경고를 남기고 다음 run 시작 시 `_emit_op_events` 재주입 경로를 통해 다시 OP_END를 실행하도록 fallback(예: 스냅샷 복원)한다.
   - 테스트: 체인 스텁을 의도적으로 run_until 경계에 걸치게 한 뒤 drain 적용 전후로 suspended 리스트 길이가 0인지 검증한다.
2. **Bootstrap continuity**
   - drain 전에 `sched.metrics["bootstrap_stage"]`와 `sched.metrics["bootstrap_active"]`를 저장하고, drain 후 변경이 없는지 assert 한다.
   - `QUEUE_REFILL` 제거는 `when > cutoff_us`인 항목만 대상으로 하고, 다음 run에서 `InstrumentedScheduler` 초기화 시 기본 `QUEUE_REFILL`가 다시 push 되는 것을 unit test로 확인한다. (`scheduler.py:99-133`)
   - 부트스트랩이 활성화된 상태에서는 drain이 hook을 건드리지 않도록 feature flag로 skip하거나, stage progression이 완료된 뒤(bootstrap_active=False)만 drain 하도록 조건을 둔다.

### Verification Plan
- **Resume Chain Scenario**: 구성 파일에서 ERASE_RESUME → ERASE 체인을 만드는 시나리오를 실행하고 run_until을 chain stub OP_END 직전으로 설정한다. drain 후 `rm.suspended_ops_*`가 비어 있고, exporter 로그에서 `source == "RESUME_CHAIN"` 항목의 OP_END가 한 번만 기록됐는지 확인한다.
- **Bootstrap Scenario**: `bootstrap.enabled=true` 설정으로 초기화한 뒤 drain을 실행하고 다음 run이 문제없이 QUEUE_REFILL를 통해 proposal을 계속하는지 확인한다. Metrics snapshot을 비교해 stage 번호가 보존되는지 검증한다.

### Residual Risks
- feature flag 조건이 잘못 걸리면 실제로 필요할 때 drain이 건너뛰어져 AddressManager 불일치가 발생할 수 있다.
- 부트스트랩 Skip 조건이 너무 보수적이면 장시간 bootstrap_active 상태로 남아 run 경계마다 OP_END가 누락될 수 있으므로, 이후에는 bootstrap 종료 시점에 자동으로 drain을 재시도하는 보강 전략이 필요하다.


## 후속 연구 2025-09-16T14:46:14+0900

**Follow-up Question**: drain 헬퍼를 Scheduler 내부에 통합할지, main.py 오케스트레이션에서 통합할지 어떤 기준으로 선택할까?

### Option A — Scheduler 통합
- `Scheduler`는 EventQueue와 `_handle_op_end`에 직접 접근하므로 drain 로직을 내부 메서드로 두면 캡슐화가 유지된다. (`scheduler.py:108-157`, `scheduler.py:202-217`)
- `run()` 종료 직후나 `close()`에서 자동 호출하도록 훅을 추가하면 다른 호출자(예: tests에서 직접 Scheduler를 사용할 때)도 일관되게 보호된다.
- Bootstrap 단계/feature flag 판단 등 런타임 상태를 Scheduler가 이미 보유하고 있어 조건 분기 구현이 단순하다. (metrics[`bootstrap_active`], `self._boot`).
- 단점: 기존 외부 코드가 Scheduler를 상속하거나 monkey patch할 경우 동작 변경이 발생할 수 있으며, drain 시점 구성이 고정돼 유연성이 줄어든다.

### Option B — main.py 오케스트레이션 통합
- `run_once`가 `InstrumentedScheduler` 인스턴스를 반환하므로 main 루프에서 drain 여부를 제어할 수 있다. (`main.py:978-1252`)
- Feature flag/실험 설정을 CLI 인자나 cfg 기반으로 쉽게 제어할 수 있고, multi-site 루프에서만 활성화하는 등의 정책 적용이 용이하다.
- Scheduler 클래스에 새로운 자동 동작을 추가하지 않아 다른 소비자(별도 툴/테스트)가 opt-in으로 drain을 사용할지 결정할 수 있다.
- 단점: 다른 진입점이 Scheduler를 직접 생성한다면 동일한 안전 장치가 적용되지 않을 수 있으며, InstrumentedScheduler 전용 메서드를 common Scheduler API로 노출해야 한다.

### Decision Factors
- **재사용 범위**: 코드베이스 내 Scheduler 사용처가 main.py에 국한된다면 main 통합이 단순하지만, 향후 스크립트/테스트가 Scheduler를 직접 사용할 가능성이 있다면 Scheduler 통합이 더 안전하다.
- **구성 유연성**: drain을 실험적으로 켜고 끌 필요가 있다면 main.py에서 CLI 플래그를 통해 제어하는 옵션이 유리하다.
- **API 안정성**: Scheduler에 drain을 내장하면 기존 행동 변경을 최소화하기 위해 기본 비활성 플래그/옵션이 필요하고, main.py 경로에서는 새 CLI 인자 추가가 필요하다.
- **테스트 전략**: Scheduler 통합 시 단위 테스트를 Scheduler 레벨에서 작성 가능하고, main 통합 시 end-to-end run 테스트를 통해 보장한다.

### Recommendation Snapshot
- 초기 구현은 Scheduler에 `drain_pending_end_events()` 메서드를 추가한다.


## 미해결 질문
- 하이브리드 접근 시 Scheduler 메서드 서명과 CLI 플래그/구성 스키마를 정의하고, 기존 스크립트 호환성(예: scripts/exp_chain_tests.py)을 검토해야 한다. -> (검토완료) Scheduler 내 통합