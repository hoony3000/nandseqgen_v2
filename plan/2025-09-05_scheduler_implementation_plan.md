---
date: 2025-09-05
author: Codex CLI
status: in_progress
source_refs:
  - research/2025-09-05_01-35-43_scheduler_standalone_vs_embedded.md
  - research/2025-09-05_10-47-20_bootstrap_phase_conditional.md
  - research/2025-09-05_12-15-00_scheduler_hooks_cadence_and_config_mapping.md
  - docs/PRD_v2.md
---

# 스케줄러 구현 계획 (v1)

## 1-페이지 요약

- 배경: PRD는 이벤트 훅 오케스트레이션, Admission Window 기반 스케줄링, earliest‑feasible 선택, 트랜잭션 커밋 경계, PHASE_HOOK 생성 및 관측 가능성을 갖춘 결정적 스케줄러를 정의한다. 연구는 스케줄러를 시뮬레이터에 내장하지 않고 Proposer/ResourceManager/AddressManager(선택: Validator)와 조합되는 독립 모듈로 구현할 것을 권고한다.
- 문제: 다음을 만족하는 독립적·테스트 가능한 결정적 스케줄러를 구축한다: (a) 시간/훅을 전진, (b) 현재 `phase_conditional`에 따라 Proposer에 후보를 요청, (c) ResourceManager로 실행 가능성 검사, (d) 배치 단위 원자적 커밋과 롤백, (e) 부트스트랩 단계에서 `phase_conditional` 오버레이(ERASE→PROGRAM→READ(+DOUT))를 동적으로 적용.
- 목표: 명확한 모듈 경계; 결정적 실행(시드 RNG, 시스템 시계 미사용); Admission Window 정합성; 배치 원자성(체크포인트 단위 전부/없음); 부트스트랩 오버레이 컨트롤러; 강한 관측성; 유닛 테스트 용이한 경계.
- 비목표: Proposer/RM/주소 모델의 리디자인; UI/드라이버 전면 개편; 완전한 E2E CLI; 기존 설정 키와 중복되는 새 네임스페이스 도입(`scheduler.*`)—대신 기존 `policies.*`/`propose.*` 재사용, 신규는 `bootstrap.*`에 한정.
- 제약: 결정성; 단일 스레드/틱 루프; (배치/ckpt) 전부/없음; Admission Window 경계 준수; 기존 `proposer.py`, `resourcemgr.py`, `addrman.py`와 통합; 함수 ≤ 50 LOC 권장; 파일 ≤ 300 LOC(필요 시 분리);
  코드/로그에 비밀값 금지; 명시적 오류 보고.

## 아키텍처 결정

- 선택: 독립 모듈 `scheduler.py`에 `Scheduler` 클래스를 두고 `ResourceManager`, `Proposer`, `AddressManager`, 선택적 `Validator`, 시드된 RNG/로거를 주입한다. 시뮬레이터는 런너/래퍼 역할만 담당한다.
- 비교한 대안:
  - 시뮬레이터 내장: +프로토타이핑 빠름; −강결합/비결정성 전파/테스트 난이도 상승.
  - 독립 모듈(선택): +분리/재사용/정책 실험 용이; −배선(와이어링) 보일러플레이트 소폭 증가.

## 상위 설계

### 공개 API(제안)

```python
class Scheduler:
    def __init__(self, cfg, rm, proposer, addrman, validator=None, rng=None, logger=None): ...

    def run(self, run_until_us: int | None = None, max_hooks: int | None = None) -> "SchedulerResult":
        """시간 또는 훅 예산을 소진할 때까지 결정적으로 PHASE_HOOK 루프를 실행한다."""

    def tick(self) -> "TickResult":
        """하나의 스케줄링 훅 윈도우를 실행: propose → feasible-at → commit/rollback → metrics."""

    def close(self) -> None: ...

class SchedulerResult(TypedDict):
    success: bool
    hooks_executed: int
    ops_committed: int
    bootstrap_completed: bool
    metrics: dict

class TickResult(TypedDict):
    committed: int
    rolled_back: bool
    reason: str | None
```

비고:
- `cfg`는 현재 사용 중인 라이브 설정 객체/딕셔너리이며, `Scheduler`는 무관한 필드를 변경하지 않는다.
- 결정성을 위해 RNG는 반드시 주입/시딩한다. 시스템 시계는 사용하지 않으며 시간은 cfg/런너가 주는 논리(us) 시간을 쓴다.

### 책임

- 이벤트/훅 구동: PRD에 따른 결정적 훅 호출과 시간 전진.
- Admission Window: earliest‑feasible 탐색/커밋을 설정된 윈도우 경계 내로 제한.
- 제안/실행가능성: `Proposer.propose(cfg, now, window)`로 후보를 받고 `ResourceManager.feasible_at`로 실행 가능성 확인.
- 트랜잭션 경계: 체크포인트(ckpt) 단위 원자적 커밋, 위반 시 롤백.
- 부트스트랩 컨트롤러: 단계별(E→P→R(+DOUT))로 `cfg['phase_conditional']` 오버레이를 교체/복원.
- 관측성: 커밋/롤백/사유별 분포/윈도우 히트율/기아 지표 등의 카운터 유지.

## 주요 통합 지점

- Proposer: `proposer.propose()`는 `cfg['phase_conditional']`을 소비하므로, 분포 스왑은 Proposer 변경 없이 즉시 반영된다.
- ResourceManager(RM): `feasible_at(...)`, `commit(...)`, 중첩/락 질의, 배치/ckpt별 트랜잭션 스냅샷을 사용.
- AddressManager: 샘플링은 이미 멀티플레인 대응. BootstrapController의 커버리지/볼륨 계산에도 활용.
- 선택적 Validator: 사전 커밋 검증을 플러그형으로 주입(읽기 전용 경로, 부수효과 없음).

## 이벤트 훅/틱 규칙(결정적 처리)

- 틱 정의: 동일 시각(time)의 모든 이벤트 훅 세트를 하나의 틱으로 처리한다.
- 동시각 처리 우선순위: `OP_END → PHASE_HOOK → QUEUE_REFILL → OP_START`.
- RNG 분기: 훅별로 `(global_seed, hook_counter)` 기반 스트림을 사용(시스템 시계 금지).
- REFILL 주기: `policies.queue_refill_period_us` 간격으로 REFILL 훅 생성(초기 1회 프라임 포함).
- Admission Window: `policies.admission_window` 값을 사용해 `[now, now+W)` 내 earliest‑feasible만 허용. `op_specs[*].instant_resv=true`인 base는 상한 무시.
- 런 종료: `now >= run_until`이면 종료 루틴 실행. 그 시간대에는 Proposer propose 금지(PRD의 on_termination_routine).
 - PHASE_HOOK 생성: 각 operation state 경계 전/후에 생성하되 ISSUE state에는 생성하지 않음(경계 시각은 훅별 RNG 스트림으로 ±epsilon 변형).

## 스케줄링 흐름(틱 단위)

1) cfg에서 현재 논리 시간과 Admission Window `[now, now + window_us]`를 계산한다.
2) `Proposer.propose(now, hook, cfg, res_view, addr_sampler, rng)`로 후보를 얻는다. Proposer는 설정에 따라 시퀀스(READ→DOUT)를 내부에서 확장한다.
3) Earliest Feasible: RM으로 윈도우 내 가장 이른 실행 가능 시각 `t0`를 찾고, 배치의 최초 op가 윈도우 시작 경계를 넘지 않도록 강제한다.
4) 트랜잭션 스냅샷: 배치/ckpt에 대한 RM 스냅샷을 시작한다.
5) 커밋 시도: 순서대로 op를 적용; 위반(중첩/래치/ODT 등) 발생 시 스냅샷 롤백하고 `ckpt_rollback_batches`로 계수한다.
6) 성공 시 `ops_committed`를 갱신하고 상태/시간을 전진한다. 필요 시 PHASE_HOOK을 방출한다.
7) 메트릭: 카운터와 사유별 분포를 갱신한다.
8) 부트스트랩: 활성화 상태라면 진행률을 갱신하고, 단계 목표 달성 시 오버레이를 교체한다. 최종 완료 시 기본 분포를 복원한다.

## BootstrapController 설계

- 기본/오버레이: `pc_base = cfg['phase_conditional']`를 보관한다. 각 단계에 대해 비대상 op 확률을 0으로 둔 오버레이 `pc_stage_E`/`pc_stage_P`/`pc_stage_R`를 구성한다.
- 스왑 의미: 단계 시작 시 `cfg['phase_conditional'] = pc_stage_X`로 설정하고, 최종 완료 시 기본 분포로 복원한다.
- 단계 목표(권장 기본값, cfg로 조정 가능):
  - Erase 커버리지(다이 기준): erased blocks / good blocks ≥ threshold_E(예: 0.20)
  - Program 볼륨: 다이당 정규화된 프로그램 페이지 ≥ threshold_P(예: 0.05)
  - Read 커버리지+볼륨: 블록당 최소 1회 READ 커버리지 ≥ threshold_Rc 및 READ 타깃 수 ≥ min_Rv
- 메트릭 소스:
  - ERASE/PROGRAM은 AddressManager 배열을 활용해 커버리지/볼륨 계산
  - READ는 Timeline/Logger 기반, 또는 타임라인이 없으면 커밋 시 내부 카운터 캐시로 근사
- 셀타입 혼합: 허용 셀타입으로 op_name을 필터. 기본은 균등, 필요 시 `bootstrap.celltype_weights`로 우선순위 부여.
- 결정성: 모든 단계 결정은 결정적 카운터와 주입된 RNG에만 의존. 시스템 시계 사용 금지.

## 설정 키(정합/재사용)

- 재사용(중복 금지):
  - `policies.admission_window` — Admission Window 폭(기존 config.yaml과 정합; PRD의 `admission_window_us` 표기는 후속 문서 정합화 대상)
  - `policies.queue_refill_period_us` — REFILL 주기
  - `policies.topN`, `policies.epsilon_greedy`, `policies.maxtry_candidate`, `policies.maxplanes` — Proposer 정책
  - `phase_conditional`, `phase_conditional_overrides` — 분포/오버라이드
- 재사용(데모 경로):
  - `propose.mode` ∈ {legacy, progressive, hybrid}
  - `propose.chunking.max_ops_per_chunk`
  - `propose.chunking.checkpoint_interval`
  - `propose.chunking.allow_partial_success`
- 신규(부트스트랩만 추가):
  - `bootstrap.enable: bool`
  - `bootstrap.thresholds.erase_coverage: float`
  - `bootstrap.thresholds.program_volume: float`
  - `bootstrap.thresholds.read_coverage: float`
  - `bootstrap.minimums.read_volume: int`
  - `bootstrap.celltype_weights: {SLC: float, TLC: float, ...}` (선택)

비고: 이미 존재하는 키는 그대로 사용하고, 신규 키는 `bootstrap.*` 네임스페이스로만 추가한다.

## 데이터 구조(타입)

- `ProposedBatch`: Proposer의 기존 DTO, 수정하지 않음.
- `PhaseHook`/`HookId`: 필요 시 훅 진행을 표현하는 enum/별칭.
- `SchedulerMetrics`: 관측성 섹션의 카운터 딕셔너리.

## 관측/가시성(카운터)

- `ckpt_success_batches`, `ckpt_rollback_batches`, `ckpt_ops_committed`
- 실패 사유 분포: latch/bus/overlap/ODT/cache/EPR/etc
- 윈도우 통계: propose 통과율, 윈도우 초과 거절 수
- 지연: now→t0 대기, 훅별 propose/feasible/commit 소요(논리 시간)
- 기아: 훅/다이/플레인별 연속 미처리 횟수(누지 정책 입력)

## 오류와 메시지

- 설정 오류에는 구체적 예외와 명확한 메시지를 사용(예: bootstrap 활성화 시 임계치 누락).
- 민감한 데이터는 로그에 남기지 말고, 상관관계를 위해 batch_id/hook_id/sequence_id만 포함한다.

## 구현 마일스톤

1) 스켈레톤: `scheduler.py`에 `Scheduler.__init__`, `run`, `tick`, 메트릭 스텁, RNG 주입 추가.
2) Admission Window: cfg에서 계산해 Proposer/실행 가능성 검사에 전달.
3) 트랜잭션 경계: ckpt 주변 RM 스냅샷/커밋/롤백 통합 및 카운터 구현.
4) 관측성: 메트릭 배선과 구조화 로거 훅 연결.
5) BootstrapController: 오버레이 구성, AddressManager/Timeline 기반 단계 메트릭, 교체/복원 로직.
6) 설정 표면: 기존 `policies.*`/`propose.*` 키 재사용, `bootstrap.*` 신규 키 추가 및 기본값.
7) 테스트: 커밋/롤백, 윈도우 경계, 오버레이 스왑, 결정성(고정 RNG), 에러 경로 유닛 테스트.
8) 데모 러너: 기존 구성요소로 Scheduler를 조립해 한 세션 실행하는 최소 어댑터.

## 진행 상황 (2025-09-05)

- [x] 1) 스켈레톤 구현: `scheduler.py` 추가. `Scheduler.__init__`, `run`, `tick` 구현 및 RNG 주입(시드 고정)과 메트릭 스텁 포함.
- [x] 3) 트랜잭션 경계(기본): tick 내 전부/없음 예약 → `rm.commit/rollback` 경계 적용, 카운터 반영.
- [x] 2) Admission Window(기본): `policies.admission_window` 상한을 tick 단계에서 비즉시 예약에 한해 강제.
- [ ] 4) 관측성 확장: 훅 단위 지연, 사유 분포, 윈도우 히트율 등 세부 메트릭 배선은 후속 작업.
- [x] 4) 관측성 확장(기본): 윈도우 시도/초과 카운터, per‑base 커밋, 논리 지연(wait/exec) 합계, 마지막 커밋 base 기록.
- [x] 5) BootstrapController: 단계(0:ERASE→1:PROGRAM→2:READ+DOUT→완료) 오버레이 및 임계치 기반 진행(`bootstrap.thresholds.*`, `bootstrap.minimums.*`), 기본 비활성.
- [x] 6) 설정 표면 정리(기본): `bootstrap.enabled/thresholds/minimums` 키 수용. 기본값은 코드 내 보수적 디폴트 사용.
- [x] 7) 테스트: 기본 커밋/메트릭, 윈도우 경계 롤백, 결정성, 부트스트랩 오버레이 유닛 테스트 추가.
- [x] 8) 데모 러너: `tools/scheduler_demo.py` 추가(최소 구성으로 한 세션 실행).

## 테스트 계획

- 유닛:
  - 커밋 성공: 실행 가능한 배치가 결정적으로 커밋되고 카운터가 증가.
  - 롤백 경로: ckpt 중간에 불가능 op 주입 → 롤백 및 실패 카운터 검증.
  - 윈도우 경계: 윈도우를 넘어 시작하는 후보는 거절; 재제안 또는 no-op 틱.
  - 부트스트랩 오버레이: bootstrap 활성화 시 단계 허용 op만 등장; 임계치 달성 시 오버레이 진행/복원.
  - 결정성: 동일 시드 → 동일 메트릭/커밋 시퀀스.
- 계약/페이크: Scheduler를 고립하기 위한 Fake RM/Proposer/AddressManager 어댑터 사용; 외부 시스템 의존 없음.

## 위험 및 완화

- 설정 레이스(오버레이 쓰기): 단일 스레드 틱 루프로 병렬 변경 없음. 스왑은 틱 범위에 국한.
- READ 단계 지표 소스: 타임라인 의존이 무거우면 커밋 시 갱신하는 내부 카운터 캐시로 볼륨/커버리지 근사.
- 큰 함수: 함수당 ≤ 50 LOC를 지키고 틱 단계를 헬퍼로 분리.
- 정책 드리프트: 오버레이/임계치를 cfg에 유지하고 기본값을 문서화; 유닛 테스트로 경계 검증.

## 미해결 사항

- 훅/틱 주기 및 우선순위: 본 계획의 “이벤트 훅/틱 규칙”에 따라 `OP_END → PHASE_HOOK → QUEUE_REFILL → OP_START`로 확정(연구 반영: `research/2025-09-05_12-15-00_scheduler_hooks_cadence_and_config_mapping.md`).
- 설정 키 명명: 기존 config 재사용 원칙 확정(중복 네임스페이스 도입 지양). PRD 내 `admission_window_us` 표기는 현행 config(`policies.admission_window`)와 정합화 예정.

## 수용 기준

- `scheduler.py`에 `Scheduler`와 `BootstrapController`가 존재하고 임포트/정적 검사 통과, 로컬 유닛 테스트 성공.
- 시드된 RNG로 결정적 동작(시스템 시계 미사용).
- Admission Window 준수(윈도우 밖에서 커밋 시작 금지).
- 체크포인트 단위 원자적 커밋과 위반 시 롤백; 카운터가 결과를 반영.
- 부트스트랩 단계에서 오버레이가 강제되고 완료 후 기본 분포 복원.
- 최소 데모 러너로 제어된 환경에서 E2E 훅 플로우 확인.
