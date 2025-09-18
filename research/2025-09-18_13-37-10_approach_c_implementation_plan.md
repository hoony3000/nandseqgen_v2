---
date: 2025-09-18T13:37:10+00:00
researcher: Codex
git_commit: 4c1b6945bfe880542fc7355f319562c43caa8708
branch: main
repository: nandseqgen_v2
topic: "Approach C 기반 suspend/resume 정규 파이프라인 구현 체크리스트"
tags: [research, planning, scheduler, resource-manager, event-queue, suspend-resume]
status: draft
last_updated: 2025-09-18
last_updated_by: Codex
last_updated_note: "ERASE/PROGRAM 축 상호작용 요구사항 반영"
---

# 연구: Approach C 구현 체크리스트

**Date**: 2025-09-18T13:37:10+00:00  
**Researcher**: Codex  
**Repository**: nandseqgen_v2

## Problem 1-Pager
- **Background**: 현행 suspend/resume 체인은 임시 체인 스텁과 중복된 OP_END 스케줄링에 의존하여 scheduler, resource manager, event queue 전반에서 meta `end_us` 드리프트와 이중 커밋을 유발한다. `config.yaml:2124-2326` 기반 정책은 ERASE 축에서는 PROGRAM 축과의 중첩을 허용하지만, PROGRAM 축이 `PROGRAM_SUSPENDED` 상태일 때는 `ERASE_SUSPEND` 를 금지한다.
- **Problem**: Approach C 는 스텁 중심 흐름을 정규 OperationLedger 로 대체하므로, 재설계 착수 전에 필요한 코드·테스트·도구 변경 사항과 전달 리스크를 구체적으로 정리해야 한다. 특히 Ledger 가 ERASE/PROGRAM 양 축을 독립적으로 추적하면서도 상호 배타 조건을 보존해야 한다.
- **Goal**: Approach C 롤아웃을 작고 관측 가능하며 롤백 가능한 범위로 유지하기 위한 변경 체크리스트, 리스크 레지스터, 완화 전략, 미해결 질문을 마련한다.
- **Non-goals**: 현재 시점에서 ledger 구현, 런타임 동작 수정, 프로덕션 설정 변경, 기존 메트릭 제거는 포함하지 않는다.
- **Constraints**: 함수 길이 50 LOC 이하, 순환 복잡도 10 이하, 숨겨진 부수효과 금지, ASCII 기본(필요 시 예외), 기존 validation 훅과 호환, Python 3.11, 마이그레이션 중에도 기존 디버깅 도구가 suspend/resume 상태를 추적 가능해야 한다.

## Alternatives Review
- **Approach A – 점진적 meta 재바인딩**: + 표면적 변경이 최소이고 가장 빠르게 배포할 수 있다. - 체인 스텁 복잡성이 그대로 남는다. ! 새 경로가 생기면 OP_END 중복과 meta 왜곡 위험이 지속된다.
- **Approach B – 체인 스텁을 일반 ongoing op 으로 취급**: + `register_ongoing` 경로를 재사용해 자연스럽게 `end_us` 업데이트를 얻을 수 있다. - 메트릭과 이벤트 배포가 여전히 이중화된다. ! 이중 커밋 회귀 가능성이 높다.
- **Approach C – 정규 ledger 기반 파이프라인**: + meta·이벤트·메트릭을 단일 진실 공급원으로 통합하고 체인 스텁 분기 자체를 제거한다. - scheduler/resource manager 전반을 폭넓게 리팩터링해야 한다. ! 동시성 및 상태 복구 시맨틱까지 마이그레이션 범위가 확장된다.
- **결정**: 구조적 중복을 제거하고 향후 suspend/resume 변형을 열어준다는 장점 때문에, 내부 플래그와 백스톱 테스트를 전제로 Approach C 진행을 택한다.

## Approach C 적용을 위한 변경 항목
- `operation_ledger.py` (신규): 작업별 타이밍, 상태, `remaining_us`, 이벤트 핸들을 저장하는 정규 OperationLedger 를 추가하고 `schedule`, `suspend`, `reactivate`, `finalize` 등 스레드 안전 API 를 제공한다. ERASE/PROGRAM 축 상태를 분리 보관하고 상호 배타 정책을 질의할 수 있는 인터페이스를 포함한다.
- `scheduler.py`: `_chain_stub` 분기를 제거하고 suspend/resume 스케줄링을 ledger 에 위임하며, `ledger.schedule(op_uid, end_us)` 를 통해 단일 OP_END 만 발행하고 resume 경로는 정규 `remaining_us` 를 참조하도록 조정한다. 다축 suspend 상황에서도 단일 `op_uid` 경로가 유지되도록 ledger 질의 결과를 사용한다 (`scheduler.py:735-960`).
- `resourcemgr.py`: `suspended_ops_program` 스택을 ledger 기반 뷰로 대체하고, `commit`/`resume_from_suspended_axis` 가 원시 meta 대신 ledger 엔트리를 변경하도록 하며 직접 `remaining_us` 연산을 제거한다. `resourcemgr.py:800-930`, `resourcemgr.py:1260-1400` 에 구현된 ERASE/PROGRAM 축 상태 전환과 config 기반 차단(`exclusions_by_suspend_state`)을 ledger 계산으로 이동한다.
- `event_queue.py`: `op_uid` 기준으로 대기 중인 OP_END 교체·삭제 기능을 추가하고 ledger 가 발행한 핸들을 활용해 중복을 차단하며, 큐잉 API 가 구조화된 식별자를 받도록 확장한다.
- `address_manager.py` 및 관련 커밋 소비자: `op_uid` 당 한 번만 반응하도록 보장하고 스텁 아티팩트를 가정하지 않은 채 ledger 타임스탬프를 처리하도록 조정한다.
- 기능 플래그/설정(`config.yaml`, `features.py`): `features.operation_ledger_enabled`(또는 기존 체인 플래그 재사용)을 도입해 신규 흐름을 가드하고 `suspend_resume_chain_enabled` 폐지 계획을 수립한다. `config.yaml:2124-2234` 의 축별 block 리스트가 ledger 기반 신호와 동등하게 유지되도록 마이그레이션 경로를 정의한다.
- 메트릭/텔레메트리 모듈: `resume_remaining_us`, `chained_stubs`, OP 생애주기 카운터를 ledger 이벤트 기반으로 전환하고 ledger 상태 건전성 지표(ERASE/PROGRAM 축 활성 카운터, 교차 차단 이벤트 등)를 추가한다.
- 검증/계측 스위트: Strategy2/Strategy3 검증이 ledger 데이터를 소비하도록 업데이트하고 스냅샷·리플레이 도구가 새로운 상태 소스를 이해하도록 보강한다. ERASE_SUSPEND→PROGRAM_SUSPEND 중첩과 PROGRAM_SUSPEND 상태에서의 ERASE_SUSPEND 금지 사례를 커버하는 패턴을 추가한다.
- 테스트: OperationLedger 상태 전환 단위 테스트를 신설하고 suspend/resume 통합 테스트를 다회 재개, 실패 경로, OP_END 유일성까지 확장하며 이벤트 순서 변경에 영향을 받는 골든 트레이스를 갱신한다. 축 상호작용 규칙(허용/금지 시퀀스)을 확인하는 테스트를 포함한다.
- 문서/ADR: ledger 책임, 상태 머신, suspend/resume 소비자와의 하위 호환 전략을 정리한 ADR 을 작성한다. ERASE vs PROGRAM 축 규칙과 config 연동 방식을 명시한다.

## Risks
- **R1 – Ledger 상태 손상**: scheduler 와 resource manager 가 전환 중 경합하거나 중단되면 suspend/resume 상태가 불일치할 수 있다.
- **R2 – EventQueue 회귀**: OP_END 교체가 큐 엔트리 불변성이나 시퀀스 번호를 기대하는 다른 서브시스템을 깨뜨릴 수 있다.
- **R3 – 메트릭/관측 공백**: 기존 대시보드와 실험은 `chained_stubs` 등 신호를 잃을 수 있어 회귀 탐지가 느려질 수 있다.
- **R4 – 실서비스 전환 호환성**: 플래그 전환 시점에 처리 중인 작업이 구·신 흐름을 걸치면 ledger 엔트리가 고아가 되거나 이중 커밋이 발생할 수 있다.
- **R5 – 성능 오버헤드**: ledger 간접층이 조회·락 비용을 추가하여 스케줄러 핫루프 지연을 키울 수 있다.
- **R6 – 축 상호 배타성 붕괴**: ERASE_SUSPEND ↔ PROGRAM_SUSPEND 허용/차단 조건을 잘못 복원하면 하드웨어 제약을 위반하거나 불필요한 스로틀이 발생한다.

## Risk Mitigations
- **R1**: 전이 전용 헬퍼(`with ledger.transition(op_uid)`)를 통해 선·후 조건을 검증하고, 크래시 복구 시맨틱 단위 테스트를 추가하며, 디버깅용 구조화 상태 스냅샷을 로깅한다.
- **R2**: 기존 정렬 계약을 보존하는 EventQueue 쉬므(shim)를 제공하고, OP_END 타이밍을 소비하는 validation 회귀 테스트를 실행하며, 변경을 런타임 토글 뒤에 배치한다.
- **R3**: 전환 기간 동안 ledger 이벤트에서 기존 메트릭을 파생하고, 폐기 예정 경고를 지속적으로 발행하며, 구 카운터 비활성화 전에 대시보드를 업데이트한다.
- **R4**: 축 활성화 시 `ongoing_ops`가 정지될 때까지 대기하는 드레인 로직과 플래그 롤백 시 ledger 스냅샷에서 스텁 상태를 복원하는 경로를 마련한다.
- **R5**: suspend/resume 스트레스 테스트로 ledger 성능을 벤치마크하고, 스케줄러 핫 패스에서 조회 빈도가 높은 필드를 캐싱하며, 락 세분화를 검토해 경합을 최소화한다.
- **R6**: `config.yaml:2124-2234` 에 정의된 `exclusions_by_suspend_state` 를 기준으로 허용·금지 시퀀스 표를 작성하고, ledger 전환 테스트에서 ERASE_SUSPEND→PROGRAM_SUSPEND(허용) / PROGRAM_SUSPEND→ERASE_SUSPEND(거부) 시나리오를 자동 검증한다.

## Open Questions
- **Ledger 스냅샷/리플레이 지속성**
  - 기본 답변: ResourceManager 스냅샷 포맷에 `ledger.snapshot()`/`ledger.restore()` 결과를 통합하고 버전 헤더를 추가해 구버전 도구는 무시하도록 한다.
  - 대안: 이벤트 로그 재생으로 ledger 상태를 재구성하되, 리플레이 시간이 허용되는 환경에서만 사용하고 기본 경로는 유지한다.
- **외부 메트릭·로그 마이그레이션**
  - 기본 답변: 일정 기간 기존 카운터와 ledger 파생 카운터를 동시에 발행하고 대시보드/알람을 2중화한 뒤, 관측 일관성이 확보되면 구 카운터를 단계적으로 끈다.
  - 대안: feature flag 기반으로 소비자 그룹별 릴레이어를 제공해, 전환 대상만 ledger 카운터를 구독하도록 분리 롤아웃 한다.
- **도입 단위(PROGRAM vs ERASE)**
  - 기본 답변: ledger 는 두 축을 모두 표현하되, 기능 플래그로 각 축의 scheduler 진입을 독립 제어해 점진적으로 돌리며 상호 제약은 중앙에서 검사한다.
  - 대안: 위험을 줄이기 위해 ERASE/PROGRAM 을 한 번에 전환하되, 사전 검증 환경에서만 부분 전환 테스트를 수행한다.
- **다축 suspend/resume 테스트 확장**
  - 기본 답변: 통합 테스트에 ERASE_SUSPEND→PROGRAM_SUSPEND 허용 플로우와 PROGRAM_SUSPEND 중 ERASE_SUSPEND 거부 플로우를 추가하고 ledger 잔여 시간 재계산을 검증한다.
  - 대안: property 기반 시뮬레이터로 여러 축 전이를 무작위 생성해 ledger 상태 불변식을 검증하고, 핵심 시나리오는 스냅샷 골든으로 유지한다.

## References
- research/2025-09-18_13-12-17_suspend_resume_chain_consistency.md
- research/2025-09-17_15-57-35_suspend_resume_op_end_dedupe.md
- research/2025-09-18_12-51-24_resume_stub_remaining_us_meta.md
- scheduler.py:735
- resourcemgr.py:800
- config.yaml:2124
