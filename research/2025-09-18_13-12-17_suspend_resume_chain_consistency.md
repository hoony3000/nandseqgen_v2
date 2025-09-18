---
date: 2025-09-18T13:12:17.171014+00:00
researcher: Codex
git_commit: 9887f587f18cc35b83a1bea32d8e46395933994c
branch: main
repository: nandseqgen_v2
topic: "Suspend→Resume 체인에서 meta 일관성과 단일 OP_END 보장 방안"
tags: [research, codebase, scheduler, resource-manager, suspend-resume]
status: complete
last_updated: 2025-09-18
last_updated_by: Codex
last_updated_note: "Approach C(베스트 프랙티스 리디자인) 추가"
---

# 연구: Suspend→Resume 체인에서 meta 일관성과 단일 OP_END 보장 방안

**Date**: 2025-09-18T13:12:17.171014+00:00  
**Researcher**: Codex  
**Git Commit**: 9887f587f18cc35b83a1bea32d8e46395933994c  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
반복적인 `PROGRAM_SUSPEND` → `PROGRAM_RESUME` 체인에서 `ongoing_ops` ↔ `suspended_ops` 전환이 일관되게 유지되면서, 최종적으로는 `OP_END` → commit 루틴이 단 한 번만 실행되도록 보장하는 방법은 무엇인가?

## Problem 1-Pager
- **Background**: 체인 스텁 기능(`features.suspend_resume_chain_enabled`)은 RESUME 직후 남은 CORE_BUSY 시간만큼 stub 을 예약해 타임라인을 메꾼다. 그러나 기존 연구(`research/2025-09-18_12-51-24_resume_stub_remaining_us_meta.md`)에서 stub 이 `register_ongoing`에 등록되지 않아 meta `end_us` 가 갱신되지 않고, 재차 suspend 시 `remaining_us` 가 0으로 떨어지는 문제가 확인되었다.
- **Problem**: meta 가 반복 suspend 사이클에서 최신 종료 시각을 잃어버리면서 stub 생성이 건너뛰어지고, EventQueue 에는 최초 커밋과 stub 커밋에서 각각 `OP_END` 이벤트가 남아 주소 상태를 중복 변경할 위험이 있다.
- **Goal**: ① meta 가 suspend/resume 반복 동안 정확한 `end_us`/`remaining_us` 를 유지하고, ② chain stub 으로 재스케줄하더라도 `OP_END` → commit이 한 번만 일어나도록 보장할 수 있는 설계 대안을 제시한다.
- **Non-goals**: 실 구현, 테스트 실행, config 변경 반영.
- **Constraints**: 함수 50 LOC 이하 유지, ASCII, 기존 validation/instrumentation와 호환, EventQueue 구조 변경 시 정합성 확보, Python 3.11.

## 요약
- 체인 스텁은 `_chain_stub` 플래그 때문에 `register_ongoing`에서 배제되어 원본 meta 의 `end_us` 가 stub 종료 시각으로 갱신되지 않는다. 그 결과 다음 `PROGRAM_SUSPEND` 는 `remaining_us = max(0, end_us - now)` 계산에서 0을 반환하고 stub 생성이 생략된다 (`scheduler.py:735`, `scheduler.py:753`; `resourcemgr.py:1273-1304`).
- `resume_from_suspended_axis` 는 meta 를 `ongoing_ops` 로 되돌리지만 내용을 수정하지 않으므로, stub 이 진행되는 동안에도 meta 의 종료 시각은 과거값으로 남아 있다 (`scheduler.py:839-845`; `resourcemgr.py:1345-1361`).
- `OP_END` 는 최초 커밋과 chain stub 커밋 모두에서 `_emit_op_events` 로 큐잉되어 AddressManager 가 동일 페이지를 두 번 적용할 수 있어 단일 commit 이 보장되지 않는다 (`scheduler.py:748-769`, `scheduler.py:793-838`).
- 권장 방향은 **(a)** stub 성공 직후 ResourceManager 에게 meta `end_us` 를 stub 종료 시각으로 재바인딩시키고 `remaining_us` 를 재계산하며, **(b)** EventQueue 에서 동일 `op_uid` 의 기존 `OP_END` 를 새로운 시각으로 치환하여 중복 이벤트를 제거하는 것이다.

## 상세 발견

### Scheduler 체인 스텁 흐름
- `register_ongoing` 는 `_chain_stub` 레코드를 건너뛰어 meta 재등록이 일어나지 않는다 (`scheduler.py:735-769`).
- chain stub 생성 시 `resume_from_suspended_axis` 로 meta 를 ongoing 으로 되돌리지만, end 시각을 조정하거나 `remaining_us` 를 갱신하는 단계는 없다 (`scheduler.py:787-845`).
- `_emit_op_events` 는 stub 레코드에도 동일한 `OP_START`/`OP_END` 페이로드를 발행하므로, 원본 이벤트와 stub 이벤트가 모두 EventQueue 에 남는다 (`scheduler.py:793-838`, `scheduler.py:899-938`).

### ResourceManager suspend/resume 메타 처리
- `commit` 이 `PROGRAM_SUSPEND` 를 처리할 때 meta 를 `suspended_ops_program` 스택으로 옮기고, 이동 직전에 저장된 `end_us` 기반으로 `remaining_us` 를 계산한다 (`resourcemgr.py:831-874`, `resourcemgr.py:1273-1309`).
- `resume_from_suspended_axis` 는 스택에서 meta 를 꺼내 `ongoing_ops` 끝에 append 할 뿐 내부 필드를 수정하지 않는다 (`resourcemgr.py:1345-1361`).
- 따라서 stub 실행을 위해 meta 가 되돌아오더라도 종료 시각은 과거값으로 유지되고, 다음 suspend 시점에 `remaining_us` 가 0으로 계산된다 (`resourcemgr.py:1300-1304`).

### EventQueue / commit 중복 리스크
- EventQueue 는 `(time, priority, seq)` 기반 단순 정렬만 수행하여 동일 op 에 대한 중복 `OP_END` 를 필터링하지 않는다 (`event_queue.py:6-26`).
- 기존 연구도 chain stub 경로에서 `AddressManager.apply_pgm` 이 중복으로 실행된다고 보고했다 (`research/2025-09-17_15-57-35_suspend_resume_op_end_dedupe.md`).

## 대안 비교
- **Approach A – 메타 재바인딩 + OP_END 재스케줄 (권장)**  
  - *장점*: 기존 stub 흐름을 유지하면서 meta/이벤트만 조정해 반복 suspend 를 지원하고 단일 commit 을 보장한다. 수정 범위를 Scheduler + ResourceManager + EventQueue 로 한정할 수 있다.  
  - *단점*: `op_uid` 를 항상 부여해야 하고(EventQueue 식별 용도), ResourceManager 에 meta handle 을 노출하는 API가 필요하다.
- **Approach B – chain stub 을 정식 ongoing 으로 재등록**  
  - *장점*: 별도 meta mutate 없이 `register_ongoing` 재사용으로 `end_us` 업데이트를 자연스럽게 처리할 수 있다.  
  - *단점*: `resume_from_suspended_axis` 가 이미 meta 를 append 하므로 중복 등록이 발생하고, stub 용 OP_START/OP_END 이벤트가 그대로 남아 중복 commit 문제는 해결하지 못한다. 또한 관측/metrics 가 stub 기준으로 덮어써지는 부작용이 있다.
- **Approach C – Canonical Resume Pipeline(베스트 프랙티스 리디자인)**  
  - *요약*: chain stub 개념을 제거하고, `OperationLedger`(신규) 가 모든 ERASE/PROGRAM 작업을 단일 원천으로 관리한다. `PROGRAM_SUSPEND` 는 ledger 엔트리를 `status="suspended"` 로 전환하면서 원본 `OP_END` 이벤트를 제거하고 `remaining_us` 를 ledger 에 기록한다. `PROGRAM_RESUME` 는 `ledger.reactivate(operation_id, now_us)` 를 호출해 잔여 CORE_BUSY 구간을 재계산하고, Scheduler 는 ledger 가 돌려준 새 종료 시각으로 단일 `OP_END` 만 재삽입한다. 이때 ResourceManager 는 ledger 에서 가져온 canonical meta 만을 `ongoing_ops`/`suspended_ops` 로 노출한다.
  - *장점*: 단일 진실 공급원을 통해 meta/이벤트/metrics 가 항상 동기화된다. chain stub 과 `_chain_stub` 플래그가 사라지며, 모든 suspend/resume 이 같은 코드 경로를 사용해 테스트/추론이 쉬워진다. EventQueue 는 `ledger.schedule(op_uid, end_us)` API 호출만 받으므로 중복 이벤트 가능성이 구조적으로 제거된다. 향후 retry, split resume, multi-axis 지원을 일반화하기에도 유리하다.
  - *단점*: Scheduler ↔ ResourceManager 경계, metrics 수집, validation(Strategy2/3) 까지 광범위하게 재설계해야 한다. 기존 stub 기반 실험 로그와 호환성이 깨질 수 있고, `OperationLedger` 도입 시 thread-safety 및 restore(snapshot) 경로까지 검토해야 한다.

## 권장 방향
1. 단기적으로는 Approach A 를 적용해 현 구조를 보완하되, API/metrics 를 ledger 중심으로 정돈하기 위한 준비(고유 `op_uid`, meta handle 공개 범위 축소)를 병행한다.
2. 중장기적으로는 Approach C 를 타깃으로 `OperationLedger`(또는 `SuspendResumeCoordinator`) 를 설계하여 suspend/resume 파이프라인을 단일 상태 머신으로 재편하고, EventQueue 와 ResourceManager 가 ledger 의 스케줄링 결과만 소비하도록 구조를 단순화한다.
3. chain stub 제거 이후의 telemetry 요구사항(`resume_remaining_us` 로깅, `chained_stubs` 메트릭 등)을 ledger 이벤트 기반으로 대체하고, 기존 연구 로그와의 비교를 위해 마이그레이션 전략을 마련한다.

## 코드 참조
- `scheduler.py:735` — commit 루프에서 `_chain_stub` 레코드를 `register_ongoing` 대상에서 제외.
- `scheduler.py:793-845` — chain stub 예약, 이벤트 발행, `resume_from_suspended_axis` 호출.
- `resourcemgr.py:831-874` — `PROGRAM_SUSPEND` 커밋 시 meta 이동 및 remaining_us 계산.
- `resourcemgr.py:1273-1361` — suspend 스택 push/pop, remaining_us 갱신 로직.
- `event_queue.py:6-26` — 중복 제거 없는 단순 정렬 EventQueue 구현.

## 아키텍처 인사이트
- suspend/resume 체인은 Scheduler 와 ResourceManager 가 동일 meta 객체를 공유한다. stub 이후 종료 시각을 재설정하지 않으면 다음 suspend 경로에서 chain stub 자체가 비활성화된다.
- 단일 `OP_END` 보장을 위해선 EventQueue 수준에서 안정적인 식별자(`op_uid`)가 필수이며, 이는 validation 기능과 분리되어야 한다.
- ResourceManager 의 axis 스택은 순차 append/ pop 구조이므로 meta 업데이트는 동일 객체를 mutate 하는 형태가 가장 안전하며, 별도 재등록보다 일관성을 유지한다. 장기적으로는 ledger 기반 상태 머신으로 승격해 meta/이벤트/metrics 를 통합 관리하는 편이 suspend/resume 의 복잡성을 최소화한다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-18_12-51-24_resume_stub_remaining_us_meta.md` — stub 종료 이후 meta.end_us 를 직접 갱신했을 때 remaining_us=0 문제가 사라진 실험 기록.
- `research/2025-09-17_15-57-35_suspend_resume_op_end_dedupe.md` — suspend-resume 경로에서 OP_END 중복을 제거하기 위한 선행 계획.

## 관련 연구
- `research/2025-09-17_02-14-45_suspend_resume_op_end_requeue.md`
- `research/2025-09-18_08-13-53_suspend_resume_addr_state.md`

## 미해결 질문
- `finalize_resume_chain` 구현 시 meta 객체 레퍼런스를 어떻게 안전하게 노출/갱신할지에 대한 API 설계.
- EventQueue 재바인딩을 다중 이벤트(`PHASE_HOOK`)에도 적용할지 여부.
- 반복 suspend 환경에서 validation/metrics(`chained_stubs`, `resume_remaining_us`) 를 어떻게 유지할지.
- Approach C 전환 시 snapshot/restore, instrumentation, replay 도구가 ledger 기반으로 어떻게 이행될지.
