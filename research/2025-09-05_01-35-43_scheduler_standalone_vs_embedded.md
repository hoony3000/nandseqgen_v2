---
date: 2025-09-05T01:35:43.959097+09:00
researcher: Codex CLI
git_commit: fc87b49
branch: main
repository: nandseqgen_v2
topic: "Scheduler를 독립형 모듈로 구현할지 vs 시뮬레이터 내부에 내장할지 비교"
tags: [research, codebase, scheduler, proposer, resourcemanager, simulator, architecture]
status: complete
last_updated: 2025-09-05
last_updated_by: Codex CLI
last_updated_note: "Progressive/Hybrid 모드의 체크포인트/롤백 정책 연구 추가"
---

# 연구: Scheduler를 독립형 vs 시뮬레이터 내 구현 비교

**Date**: 2025-09-05T01:35:43.959097+09:00
**Researcher**: Codex CLI
**Git Commit**: fc87b49
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
PRD에 정의된 Scheduler를 독립 모듈로 분리하는 것이 나은가, 아니면 시뮬레이터 코드 내부에 내장해 구현하는 것이 나은가?

## 요약
- 권고: 독립 모듈(파일/클래스)로 구현하고, 시뮬레이터는 이를 조합·실행하는 런너로 구성한다. 결정성(이벤트 훅, 윈도우드 스케줄링)과 경계(트랜잭션 커밋)를 Scheduler에 집중시키고, Proposer/Validator/ResourceManager/AddressManager는 포트 인터페이스로 주입한다.
- 근거: PRD의 책임 분리(이벤트 훅, 결정적 윈도우, 전부/없음)와 기존 코드 구조(독립 proposer.py, RM 트랜잭션·질의 API)와 정합적. 테스트 용이성/재사용성/정책 실험(Top‑N, Progressive 등)이 크게 개선된다.
- 내장 방식은 단기 개발은 빠르나 결합도 상승으로 장기 유지보수·테스트 비용이 크다. 기존 `nandsim_demo.py` 스타일이 그 예시다.

## 상세 발견

### Scheduler 책임(요약)
- 이벤트 훅 구동, 결정적 시간 전진, Admission Window 내 earliest feasible 탐색, PHASE_HOOK 생성, 트랜잭션 커밋 경계 관리.
  - 참조: `docs/PRD_v2.md:211` (Scheduler), `docs/PRD_v2.md:215` (윈도우·결정성), `docs/PRD_v2.md:219` (전부/없음), `docs/PRD_v2.md:221` (instant_resv), `docs/PRD_v2.md:231` (RNG 분기), `docs/PRD_v2.md:235` (QUEUE_REFILL), `docs/PRD_v2.md:240` (PHASE_HOOK), `docs/PRD_v2.md:247` (OP_START/END), `docs/PRD_v2.md:376` (Workflow 초기화 순서).

### 통합 포인트(현 코드 기준)
- Proposer: 후보 생성/사전검증용 순수 함수. `proposer.py:534` (`propose`), DTO `ProposedBatch` `proposer.py:22`.
- ResourceManager: earliest feasible/예약/커밋/타임라인·락·배제 질의. `resourcemgr.py:338` (`feasible_at`), `resourcemgr.py:415` (`commit`), `resourcemgr.py:483` (`op_state`), `resourcemgr.py:486` (`has_overlap`), `resourcemgr.py:282` (`_latch_ok`).
- 정책/설정: Admission Window·Refill 주기 등 `config.yaml` 정책 필드(`config.yaml:14` `policies.admission_window`, `config.yaml:16` `queue_refill_period_us`).
- 레거시 내장 예시: `nandsim_demo.py:2394`(`class Scheduler`) — 단일 파일 내 복합 책임 내장.

### 대안 A — 독립 모듈(권장)
- 형태: `scheduler.py` 내 `Scheduler` 클래스. 생성자에 `cfg`, `ResourceManager`, `Proposer`, `AddressManager`, 선택적 `Validator`/`Logger` 주입. `tick()/run()` 제공.
- 장점: 테스트 용이(훅/시간/RNG 주입으로 결정적 단위 테스트), 재사용성(다른 런너/실험에 삽입), 관심사 분리(시뮬레이터는 조립·I/O만 담당), 성능/정책 실험 분리(Top‑N, beam, progressive 등 교체 용이).
- 단점: 통합 배선(의존성 주입) 보일러플레이트 증가, 초기 런너 작성 필요.

### 대안 B — 시뮬레이터 내장
- 형태: `nandsim_demo.py` 유사 구조에서 스케줄링/제안/커밋/로깅 혼재.
- 장점: 단일 스크립트로 빠른 프로토타이핑, 내부 상태 공유로 디버깅 용이.
- 단점: 결합도·복잡도 상승(경계 모호), 테스트 어려움(비결정적 전파 위험), 재사용성 낮음, PRD의 모듈 경계 원칙과 상충.

### 판단 근거 및 코드 정합성
- PRD의 모듈 경계: Scheduler는 오케스트레이션·커밋 경계, Proposer/Validator/AddressManager는 순수/조회형 구성요소로 정의됨(`docs/PRD_v2.md:211`, `docs/PRD_v2.md:250`, `docs/PRD_v2.md:353`).
- 현재 코드가 이미 경계를 따름: 독립 `proposer.py`(`proposer.py:534`), RM 질의·트랜잭션 포트(`resourcemgr.py:338`, `resourcemgr.py:415`).
- 연구 기록도 포트/어댑터 권고: `research/2025-09-02_23-58-12_interfaces.md:62`, `research/2025-09-04_23-20-57_proposer_interface_decoupling.md:18`.

## 코드 참조
- `docs/PRD_v2.md:211` - Scheduler 역할과 이벤트 훅 설계
- `docs/PRD_v2.md:376` - 초기화 워크플로(구성요소 조립)
- `proposer.py:534` - `propose` API (Scheduler가 호출)
- `resourcemgr.py:338` - `feasible_at` (earliest feasible 탐색)
- `resourcemgr.py:415` - `commit` (커밋 경계)
- `resourcemgr.py:483` - `op_state` (phase key 계산/PHASE_HOOK 구동)
- `nandsim_demo.py:2394` - 내장형 Scheduler 예시(레거시)

## 아키텍처 인사이트
- 결정성 보장: 이벤트 훅·윈도우 탐색·RNG 분기는 Scheduler 내부에서 표준화. 외부 구성요소는 순수/조회에 집중.
- 트랜잭션 경계: `begin→reserve→commit` 경계는 Scheduler+RM에서만 발생. Proposer/Validator/AM은 부수효과 없음.
- 정책 주입: Admission Window/Queue Refill 등은 `cfg.policies`로 주입해 실험·튜닝을 용이화.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-02_23-58-12_interfaces.md:27` - 구성요소 경계·트랜잭션 경계 제시.
- `research/2025-09-04_22-04-48_validator_integration_in_resourcemgr.md:40` - RM 내 Validator 통합 방향과 규칙 카테고리.
- `research/2025-09-04_23-20-57_proposer_interface_decoupling.md:28` - Proposer 디커플링과 Scheduler 호출 흐름.

## 관련 연구
- 위 세 문서를 포함한 인터페이스/Validator 통합 연구 일체.

## 미해결 질문
- 예약된 operation과 runtime 제안의 우선순위 조정(`docs/PRD_v2.md:404`).
- Validator 구현 위치(외부 모듈 vs RM 내부 룰 레지스트리) 최종 결정과 스케줄러 호출 시점.
- Progressive/Hybrid 모드의 체크포인트·롤백 세부 정책(부분 성공 허용 여부, 배치 크기, 관측 항목).

## 후속 연구 2025-09-05T01:45:48.185509+09:00 — Progressive/Hybrid 체크포인트·롤백 정책

### 목표
- Progressive/Hybrid 모드에서 한 훅 처리 내 다수의 오퍼레이션을 사전검증(preflight) 후 안전하게 커밋하기 위한 체크포인트/롤백 정책을 정의한다.
- PRD의 “동일 틱 내 부분 스케줄 금지” 원칙(`docs/PRD_v2.md:221`)과 성능(처리량) 사이의 균형점을 찾는다.

### 현 구현 레퍼런스
- 구성 키: `max_ops_per_chunk`, `allow_partial_success`, `checkpoint_interval` (nandsim_demo.py:2971, nandsim_demo.py:2972, nandsim_demo.py:2975)
- 트랜잭션 스냅샷/롤백 훅: `_begin_txn`, `_rollback_txn` (nandsim_demo.py:2467, nandsim_demo.py:2506)
- 관측 지표: `ckpt_success_batches`, `ckpt_rollback_batches`, `ckpt_ops_committed` 등 (nandsim_demo.py:2415)

### 대안 1 — 완전 원자적 청크(부분 성공 금지)
- 정책: `allow_partial_success=false`, `checkpoint_interval = max_ops_per_chunk`.
- 동작: 청크 전체를 하나의 트랜잭션으로 커밋. 하나라도 실패 시 전부 롤백.
- 장점: PRD의 원자성 의도에 가장 부합, 타임라인/래치/배제 일관성 분명.
- 단점: 보수적이라 처리량 저하 가능, 실패가 잦은 환경에서 기아(starvation) 위험.
- 위험: 긴 청크일수록 실패 확률↑ → 유효 slot을 낭비.

### 대안 2 — 체크포인트 분할 원자성(부분 성공 허용)
- 정책: `allow_partial_success=true`, `checkpoint_interval <= max_ops_per_chunk`.
- 동작: 청크를 체크포인트 단위로 나누고 각 배치를 원자적으로 커밋/실패 시 그 배치만 롤백. 이전 배치 커밋분은 유지.
- 장점: 실패 지역화를 통한 처리량 개선, 스케줄 진행성 확보.
- 단점: 동일 훅 내 “시퀀스 전부/없음” 해석과 충돌 소지. 해결책으로 “체크포인트=시퀀스 경계”를 강제 권장.
- 위험: 체크포인트 경계가 시퀀스를 가로지르면 PRD 의도 위배. 반드시 시퀀스 단위로 경계 설정.

### 대안 3 — 적응형 체크포인트(동적 배치 크기)
- 정책: 실패율/충돌율/버스 점유율 기반으로 `checkpoint_interval`과 `max_ops_per_chunk`를 동적으로 조정.
- 휴리스틱 예시:
  - 최근 N훅 롤백률>p → `ckpt=1`(보수) / 성공률 높음→ `ckpt=min(ckpt*2, max_ops)`.
  - 실패 사유별 가중치(버스 충돌>래치 금지>배제창 충돌 등)에 따라 감소폭 차등.
- 장점: 환경 적응으로 안정적 처리량, 실패 폭발 시 급제동.
- 단점: 튜닝 복잡도, 결정성 관리 주의(같은 시드·상태에서 동일 결과 보장 필요: 상태-유도적 알고리즘 사용).

### 대안 4 — 보수/공격 모드 토글(정책 기반)
- 정책: 워크로드/목표에 따른 모드 전환: `mode=conservative|balanced|aggressive`로 미리 튜닝된 `(ckpt,max_ops,allow_partial)` 세트 제공.
- 장점: 런타임 파라미터 최소화, 운영 단순.
- 단점: 세밀 조정 불가, 워크로드 변화에 둔감.

### 권고안
- 기본: 대안 2(체크포인트 분할 원자성) 채택 + “체크포인트=시퀀스 경계” 강제.
  - 한 체크포인트 배치에는 하나의 시퀀스(또는 시퀀스들의 독립 집합)만 포함하여 PRD의 전부/없음 원칙을 준수.
  - 실패 시 해당 배치만 롤백, 이전 커밋분 유지로 진행성 확보.
- 혼잡 시: 대안 3의 적응형으로 자동 축소(ckpt→1)하여 안정화.
- 테스트: PRD §7의 E2E 성공·실패 경로에 대해 “부분 성공=OFF/ON” 모두 결정적 재현성 검증.

### 체크리스트(불변식)
- 동일 훅 처리 중 체크포인트 단위 원자성 보장(배치 내 전부/없음).
- 체크포인트 경계는 시퀀스 경계를 넘지 않는다(`docs/PRD_v2.md:221`).
- Admission Window 내에서만 커밋(각 배치의 최초 op가 윈도우를 넘지 않도록 사전검증).
- RM 트랜잭션 스냅샷은 배치마다 별도 생성/롤백(nandsim_demo.py:2980, nandsim_demo.py:2987, nandsim_demo.py:3034, nandsim_demo.py:3042).

### 관측 항목(Observability)
- 배치 지표: `ckpt_success_batches`, `ckpt_rollback_batches`, `ckpt_ops_committed`(nandsim_demo.py:2415).
- 실패 원인 분포: plane/bus/싱글×멀티/래치/ODT/캐시/EPR 별 카운트.
- 윈도우 히트율: 사전검증 통과율, 윈도우 초과 거절율.
- 지연: `now→t0` 대기 시간 평균/분포, 훅 당 propose/schedule 시간.
- 기아 지표: 훅/다이/플레인별 미처리 연속 횟수(재가중/NUDGE 정책 판단에 활용).

### 구성 키 제안(스케줄러 섹션)
- `scheduler.chunk.max_ops_per_chunk: int`
- `scheduler.chunk.checkpoint_interval: int`
- `scheduler.chunk.allow_partial_success: bool`
- `scheduler.chunk.adaptive.enable: bool`
- `scheduler.chunk.adaptive.rollback_threshold: float` (예: 0.2)
- `scheduler.chunk.adaptive.cooldown_hooks: int`

### 구현 메모
- 결정성: 적응형 로직은 “관측된 결정적 통계”에만 의존(시드/상태로 재현 가능). 시스템 시간 금지.
- 로깅: 배치 ID/훅 ID/시퀀스 ID를 상관ID로 사용. 민감정보 금지.
- 유닛 테스트: 실패 유도(inject)로 롤백 경로 강제, 배치 원자성 보장 여부/카운터 상승 검증.
