# Approach C Operation Ledger Implementation & Verification Plan

## Problem 1-Pager
- **배경**: 현행 suspend/resume 체인이 `_chain_stub` 기반으로 중복 OP_END 이벤트를 발행하면서 scheduler, resource manager, event queue 전반에 meta `end_us` 드리프트와 이중 커밋을 초래한다. ERASE/PROGRAM 축 정책은 `config.yaml:2124` 범위에서 유지되지만 실행 경로가 분기되며 추론이 어렵다.
- **문제**: 연구 문서(`research/2025-09-18_13-37-10_approach_c_implementation_plan.md`)에서 제시한 Approach C 를 실제 코드 변경으로 안전하게 이행할 수 있도록 구체적인 구현 단계와 검증 전략을 수립해야 한다. 기능 플래그, 동시성 제약, 관측 가능성 확보가 핵심 난제다.
- **목표**: OperationLedger 를 도입해 suspend/resume 경로를 정규화하고, 단계별 통합·검증·롤아웃 체크리스트와 리스크 완화 계획을 마련한다. 변경은 롤백 가능하고 관측 가능한 작은 단위로 끊는다.
- **비목표**: 본 계획은 실제 코드/설정 변경을 포함하지 않으며, ERASE 축 외 추가 기능 설계나 프로덕션 롤아웃 일정 확정, 외부 도구 배포는 다루지 않는다.
- **제약 조건**:
  - 함수 ≤ 50 LOC, 파일 ≤ 300 LOC, 순환 복잡도 ≤ 10 유지.
  - 가상환경 `.venv` 내에서만 Python 실행, 외부 네트워크 호출 금지.
  - 입력 검증·출력 인코딩 준수, 구조화된 로깅 사용, 비밀값 기록 금지.
  - 기존 validation 훅·diagnostic 툴이 suspend/resume 상태를 계속 추적 가능해야 한다.
- **가정**:
  - `scheduler.py:735`, `resourcemgr.py:800`, `event_queue.py` 관련 코드가 최신 main 과 일치한다.
  - 기능 플래그 추가 시 `features.py` 와 `config.yaml` 에 신규 필드를 안전하게 주입할 수 있다.

## 접근 대안 비교 (결정 전 ≥2)
1. **대안 A – 단일 릴리스(Big Bang) 전환**
   - *방법*: `_chain_stub` 경로를 제거하고 OperationLedger, scheduler/resource manager/event queue 변경을 한 PR/배포에서 수행.
   - *장점*: 개발 기간이 짧고 이중 유지 코드가 없다.
   - *단점*: 회귀 발생 시 원인 추적이 어렵고 롤백 범위가 넓다.
   - *위험*: 동시성/큐 정합성 버그가 발견되면 전체 suspend/resume 기능이 중단될 수 있다.
2. **대안 B – 기능 플래그 + 호환 쉬므(shim) 기반 단계적 전환**
   - *방법*: OperationLedger 를 추가하되 기존 `_chain_stub` 흐름과 병행 실행(shadow mode)으로 시작, 단계별로 소비자 모듈을 플래그 뒤에서 교체.
   - *장점*: 각 단계마다 회귀 범위를 좁히고 구체적인 관측을 수집할 수 있다.
   - *단점*: 초기에는 이중 기록(dual write)과 코드 복잡도가 증가한다.
   - *위험*: shadow 모드 데이터 불일치가 장기간 누적되면 운영 비용이 커질 수 있다.
3. **대안 C – 이벤트 재생 기반 후처리 전환**
   - *방법*: 기존 파이프라인은 유지하되 EventQueue 에서 ledger-compatible 이벤트 스트림을 만들어 다른 모듈이 소비하도록 재구성.
   - *장점*: 핵심 모듈을 즉시 변경하지 않아 안전하다.
   - *단점*: ledger 가 실시간 단일 진실 공급원(Single Source of Truth)이 되지 못하고, 일관성 검증이 어렵다.
   - *위험*: 후처리 스트림과 실시간 스트림 간 드리프트가 발생하면 suspend/resume 교착을 해소하지 못한다.

> **결정**: 대안 B 채택. 기능 플래그와 shadow 데이터를 활용해 각 모듈을 순차 전환하면 리스크를 제어하면서도 최종적으로 구조적 중복을 제거할 수 있다. 구현 난도는 증가하지만 회귀 범위를 좁히는 것이 우선이다.

## Implementation Strategy
### Phase 0 – 플래그 및 인프라 준비
- `features.py` 와 `config.yaml:2124-2234` 에 `operation_ledger_enabled`, `operation_ledger_shadow_mode` 플래그 추가; 기본값은 `false`.
- `config.schema.json` 또는 내부 검증 로직을 갱신해 새 플래그에 대한 타입/제약을 명시한다.
- 모듈 공통 로깅 헬퍼에 `op_uid`, 축(axis) 정보를 포함한 구조화 필드 추가 준비.
- 문서화: `docs/feature_flags.md` 또는 ADR 초안에 신규 플래그 목적/rollout 가이드 명시.

### Phase 1 – OperationLedger 코어 구축 (shadow 안전 장치)
- `operation_ledger.py` 신설: thread-safe `OperationLedger` 클래스 정의.
  - 내부 구조: per-`op_uid` entry (`start_us`, `end_us`, `remaining_us`, `axis_state`, `event_handle`).
  - API: `schedule`, `suspend`, `resume`, `finalize`, `cancel`, `snapshot`, `restore`.
  - ERASE/PROGRAM 축 분리 상태와 상호 배제 검사 메서드 (`assert_axis_transition`) 포함.
- Shadow mode 지원: 모든 API 가 기존 meta/stub 경로를 변형하지 않고 단순 기록만 수행하도록 구현.
- 단위 테스트(`tests/test_operation_ledger.py`):
  - 정상 전이, 반복 suspend/resume, invalid transition 예외, snapshot/restore idempotency.
  - 다중 축 시나리오: ERASE_SUSPEND 이후 PROGRAM_SUSPEND 금지 검증.

### Phase 2 – Scheduler 통합 (shadow → gated 활성화)
- `scheduler.py:735-960` 경로에서 `_chain_stub` 사용 지점을 식별.
- Shadow 단계:
  - `operation_ledger_enabled` 가 `false` 라도 ledger 에 schedule/suspend 이벤트를 dual write 하되 스케줄링 흐름은 기존 코드 유지.
  - OP_END 발행 시 ledger 에 핸들을 저장하고 검증 로깅(`DEBUG`) 추가.
- 활성화 단계:
  - 플래그 체크 후 ledger 제공 정보로 OP_END 를 단일 발행, `_chain_stub` 분기를 건너뜀.
  - Resume 경로가 ledger `remaining_us` 를 사용하도록 경로 조정.
  - `scheduler/tests/test_suspend_resume.py` 등을 확장해 axis 별 전이를 커버.
- 락 전략 검토: scheduler 락과 ledger 락 중첩 시 데드락 예방을 위한 순서 문서화.

### Phase 3 – ResourceManager 통합
- `resourcemgr.py:800-930`, `resourcemgr.py:1260-1400` 에서 `suspended_ops_program` 등 스택 사용 지점 파악.
- Shadow 단계: ledger snapshot 과 기존 스택 내용을 비교 로깅(`INFO`→`DEBUG`)으로 추가, mismatch 탐지 시 카운터 증가.
- 활성화 단계:
  - `commit` / `resume_from_suspended_axis` 가 ledger 엔트리를 단일 진실 공급원으로 사용.
  - `remaining_us` 직접 계산 제거, ledger 에서 가져온 값 사용.
  - 축 상호 배제 로직을 `ledger.assert_axis_transition(...)` 호출로 대체.
- ResourceManager 단위 테스트: suspend/resume 반복, 축 병행 허용/금지 케이스, rollback 경로.

### Phase 4 – EventQueue 및 Downstream 소비자 조정
- `event_queue.py` 에 ledger 핸들 기반으로 OP_END 교체/삭제 API (`replace_op_end`, `cancel_op_end`) 추가.
- 기존 큐 자유함수 호출부 업데이트: `scheduler`, `address_manager.py`, 기타 enqueuer.
- EventQueue 테스트: 동일 `op_uid` 에 대해 중복 이벤트가 남지 않는지, 시퀀스 보존 여부, `remaining_us` 계산 검증.
- Downstream 소비자(`address_manager.py`, analyzer)에서 stub 가정 제거, ledger 타임스탬프를 소비하도록 조정.

### Phase 5 – 관측, 메트릭, 도구 업데이트
- 메트릭 모듈에서 `resume_remaining_us`, `chained_stubs` 대체: ledger 기반 지표(`ledger_active_program_ops`, `ledger_duplicate_event_drops`).
- Validation 전략(`validation/suspend_resume_op_end`)과 리플레이 도구가 ledger 데이터를 사용할 수 있도록 입력 포맷 확장.
- 신규 대시보드/알람 정의: 듀얼 write 기간 동안 구/신 지표 동시 노출, 편차 감시.

### Phase 6 – 릴리스 & 롤백 플랜
- Stage 1: shadow mode on (`operation_ledger_shadow_mode=true`), mismatch 카운터 < threshold 확인.
- Stage 2: 제한된 실험(예: 특정 die/bank)에서 `operation_ledger_enabled=true`.
- Stage 3: 전체 배포, shadow flag 제거. 롤백 시 순서 역순(3→2→1)으로 진행.
- 운영 가이드: oncall runbook 업데이트, ledger snapshot 복구 절차 명시.

### Implementation Checklist
- [ ] 기능 플래그 + 문서 업데이트
- [ ] OperationLedger 모듈 + 단위 테스트
- [ ] Scheduler dual write & flag 전환
- [ ] ResourceManager 통합
- [ ] EventQueue/소비자 정비
- [ ] 메트릭/도구 업데이트
- [ ] 단계별 실험 & 롤백 가이드 정리

## Verification Plan
### 단위 테스트
- `tests/test_operation_ledger.py`: 전이 그래프, snapshot/restore, 예외 케이스.
- `tests/scheduler/test_suspend_resume_chain.py`: ledger 기반 resume 타이밍 검증, OP_END 단일성 확인.
- `tests/resourcemgr/test_suspend_resume_axes.py`: ERASE/PROGRAM 축 허용/차단, remaining_us 정확성.
- `tests/event_queue/test_op_end_dedupe.py`: 동일 `op_uid` 이벤트 교체·취소 동작.

### 통합 테스트 / 시나리오
- Suspend→Resume→Suspend 반복, 다축 혼합(ERASE_SUSPEND 허용, PROGRAM_SUSPEND 중 ERASE_SUSPEND 거부).
- 플래그 Off/Shadow/On 세 단계 실행 시나리오 비교; 동일 워크로드의 스냅샷 diff 분석.
- 크래시/재시작 시 ledger.snapshot()→restore() 경로 검증.

### E2E & 성능 검증
- 기존 Strategy2/3 검증 흐름을 ledger 데이터로 확장(`validation/suspend_resume_op_end`).
- 스트레스 테스트: 다중 die/bank 환경에서 suspend/resume 폭주 시 큐 지연, 락 경합 프로파일링.
- 성능 회귀 기준: scheduler 핫 루프 latency, EventQueue enqueue/dequeue throughput.

### 관측 및 메트릭 검증
- Dual metrics 비교: 기존 `chained_stubs` vs 신규 ledger 지표 편차 <= 5%.
- 구조화 로그 샘플 점검: `op_uid`, `axis_state`, `remaining_us` 필드 존재.
- Grafana/내부 대시보드 업데이트 시각 동기화 및 알람 재조정.

### 롤아웃 검증 & 운영 체크
- Shadow 기간 mismatch 카운터 0 유지 확인.
- 플래그 전환 전 후에 oncall runbook 따라 복구 시뮬레이션 실행.
- 롤백 시 ledger snapshot 삭제/복원 절차가 안전하게 완료되는지 dry-run.

## 성공 기준
- Suspend/Resume 경로에서 OP_END 이벤트가 ledger 기준 1회만 발행되고, EventQueue 중복이 관찰되지 않는다.
- ERASE/PROGRAM 축 허용/금지 시퀀스가 `config.yaml:2124` 정의와 일치하며 테스트가 통과한다.
- 기존 메트릭이 신규 ledger 파생 지표와 ±5% 이내로 일치하고, 관측 공백이 없다.
- 기능 플래그를 단계적으로 전환해도 중단 없는 롤백이 가능하다는 사실이 검증된다.

## 리스크 & 대응 요약
- **Ledger 상태 손상**: API 전이시 전후 조건(Runtime assertion) + snapshot 단위 테스트.
- **EventQueue 회귀**: replace/cancel 기능에 대한 회귀 테스트 및 shadow 로그 비교.
- **메트릭 공백**: 구/신 지표 동시 발행 기간 확보, 대시보드 사전 업데이트.
- **성능 오버헤드**: 스트레스 테스트, 핫 루프 캐시 전략, 락 세분화 검토.
- **플래그 전환 중단**: 드레인 절차(ongoing_ops empty) 문서화 및 자동화 스크립트 준비.

## 오픈 이슈 및 후속 과제
- Ledger 데이터를 외부 진단 도구로 내보낼 포맷 확정 필요.
- EventQueue 핸들 갱신 시 타 모듈이 참조 무결성을 가정하는지 검증해야 함.
- 장기적으로 `_chain_stub` 관련 메트릭/코드를 완전 제거할 시점 결정 필요.

## 참고 문서
- research/2025-09-18_13-37-10_approach_c_implementation_plan.md
- research/2025-09-18_13-12-17_suspend_resume_chain_consistency.md
- research/2025-09-17_15-57-35_suspend_resume_op_end_dedupe.md
- scheduler.py:735
- resourcemgr.py:800
- event_queue.py
- config.yaml:2124
