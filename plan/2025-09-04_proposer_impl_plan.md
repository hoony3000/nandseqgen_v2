---
title: "Implementation Plan — Proposer (Top‑N Greedy, Pure Ports)"
author: codex
date: 2025-09-04
status: draft
owners: [proposer]
reviewers: [scheduler, resourcemgr, addrman, validator]
adr_ref: research/2025-09-04_23-20-57_proposer_interface_decoupling.md
---

## Problem 1‑Pager

- 배경: `docs/PRD_v2.md:248` Proposer는 op_state에 따라 후보 operation/sequence를 확률적으로 제안하고, Scheduler는 이를 window(`[t, t+W)`) 내 earliest feasible 슬롯으로 예약한다(`docs/PRD_v2.md:215-221`). 주소 샘플링은 AddressManager, 자원 타당성은 ResourceManager, 규칙(EPR/상태금지)은 Validator가 담당한다.
- 문제: Proposer를 구현하되 ResourceManager/AddressManager에 복잡성을 전파하지 않도록 경계를 유지하고, admission window 내 다수 후보가 있을 때 결정적이고 효율적인 탐색 전략을 제공해야 한다.
- 목표:
  - 순수(Proposer 내부 상태변이 금지) `propose(now, hook, cfg, res_view, addr_sampler, rng)` 구현
  - Top‑N Greedy 탐색(최소 설정으로 결정성·성능·다양성 균형)
  - 시퀀스 전개와 전부/없음 원칙 준수, Validator 사전검증 연동
  - 테스트/관측 가능성(성공률, 탐색 비용, 다양도) 확보
  - PRD의 cache/ODT/suspend/latch 금지 규칙, multi‑plane, sequence 규칙을 반영(`docs/PRD_v2.md:263-279`)
- 비목표:
  - ResourceManager/AddressManager의 API 확장/리팩터링
  - 전체 스케줄러 재작성 또는 새로운 이벤트 시스템 도입
  - 고비용 탐색(beam search/MCTS 등) 도입
- 제약:
  - 결정성: 전역 시드 + 훅별 분기만 사용(`docs/PRD_v2.md:220-221`)
  - 전부/없음: 동일 틱 내 부분 스케줄 금지(`docs/PRD_v2.md:219`)
  - 후보생성 소스: `CFG[phase_conditional]`(자동 채움/오버라이드 반영, `docs/PRD_v2.md:138-186`)
  - 예외 규칙: `CFG[op_specs][op_name][instant_resv]=true`인 경우 admission window 상한과 무관하게 now 이후 earliest feasible 시각에 예약 가능(동일 틱 원자성은 유지)
  - 파일 ≤ 300 LOC, 함수 ≤ 50 LOC, 매개변수 ≤ 5, 순환복잡도 ≤ 10 (AGENTS.md)
 - 가정:
   - Scheduler는 별도 모듈로 구현되며, 본 계획은 Proposer 단독 구현과 포트 기반 연동을 전제로 한다.

## 접근 대안

1) RM‑coupled Proposer (RM 내부 검증과 강결합)
   - 장점: 호풀 경로 단순
   - 단점: 결합도↑, 테스트 난이도↑, 변경 파급↑
   - 위험: RM 파일 비대화 및 회귀 위험

2) Port 기반 순수 Proposer + Validator 사전검증(권장)
   - 장점: 경계 명확, 테스트 용이, 변경 파급 최소화
   - 단점: 포트 정의/어댑터 필요
   - 위험: 포트 설계 미스 시 데이터 누락 가능

3) Beam Search/우선 탐색 트리
   - 장점: 글로벌 최적화 근사
   - 단점: 구현/성능 비용 과다, 결정성 관리 부담
   - 위험: 틱 시간 초과 및 복잡성 전파

결정: 2) 채택. 탐색은 Top‑N Greedy + 확률 타이브레이크, ε‑greedy는 기본 off.

## 설계/아키텍처

- DTO(새로 추가):
  - `ProposedOp(op_name:str, base:str, targets:List[Address], scope:Scope, start_us:float)`
  - `ProposedBatch(ops:List[ProposedOp], source:str, hook:dict)`
- 포트(Protocol; proposer 내부 정의):
  - `ResourceView`: `feasible_at`, `op_state`, `has_overlap`, `odt_state`, `cache_state`, `suspend_states` (참조 `resourcemgr.py:338`, `resourcemgr.py:512`, `resourcemgr.py:521`, `resourcemgr.py:570`)
  - `AddressSampler`: `sample_erase`, `sample_pgm`, `sample_read` (참조 `addrman.py:412`, `addrman.py:492`, `addrman.py:630`)
- 알고리즘(요약):
  1) 후보 생성: `phase_conditional[op_state]` 기반 확률 후보 생성/정렬(`docs/PRD_v2.md:263`)
  2) 상태 기반 제거: exclusions_by_* + cache/odt/suspend로 금지 op 제거(`docs/PRD_v2.md:263-265`)
     - cache 진행 규칙 반영(`docs/PRD_v2.md:264`): cache 진행 중에는 cache end 전까지 대상 plane/die에 동일 celltype의 후속 cache_read/cache_program만 허용
  3) Top‑N Greedy: 상위 N 후보에 대해
     - 주소 샘플(AM `sample_*`)
     - `res_view.feasible_at`로 earliest t0 평가(기본 `[t, t+W)`, 단 `op_specs[op_name][instant_resv]=true`면 상한 무시)
     - 시퀀스 전개(`docs/PRD_v2.md:251-277`) 후 Validator 사전검증(전부/없음)
     - 가장 이른 t0 선택; 동률은 확률 가중 RNG로 타이브레이크
  4) 실패 시 대안/재시도: `cfg.policies.maxtry_candidate` 상한까지 대안 후보/주소 비복원 샘플링(`docs/PRD_v2.md:265`, `docs/PRD_v2.md:278`)
- RNG 주입: 훅별 독립 스트림 사용(결정성, `docs/PRD_v2.md:220-221`)
- Validator 연동: 외부 `validator.validate(batch, res_view, cfg)` 호출(EPR은 AM `check_epr` 통해 RM에 주입되어 있음, `addrman.py:1200`, `resourcemgr.py:858`)

— PRD 정합성 세부사항 —
- 후보 분포 초기화: `CFG[phase_conditional]`은 자동 채움 정책/오버라이드가 반영된 최종 분포를 사용(`docs/PRD_v2.md:138-186`). Proposer는 누락/불일치 시 예외 대신 빈 후보 처리(no‑op)로 안전 종료.
- multi‑plane: `CFG[op_specs][op_name][multi]=true`인 경우 plane_set 조합을 생성하고 샘플링 실패 시 점진 축소(최소 2) 재시도(`docs/PRD_v2.md:266`).
- sequence 전개: `op_specs[op_name][sequence]` 존재 시 확률/규칙 기반 전개, inherit 규칙 `inc_page/same_page/pgm_same_page/same_celltype/multi/same_page_from_program_suspend/sequence_gap` 지원(`docs/PRD_v2.md:267-277`).
- 훅 연동: `QUEUE_REFILL`/`PHASE_HOOK` 등에서 호출 가능하되, ISSUE state 시 PHASE_HOOK 미생성은 Scheduler 책임(`docs/PRD_v2.md:231-238`). Proposer는 `hook.source`/`hook.payload`를 그대로 `ProposedBatch.source/hook`에 반영.
 - instant reservation: `op_specs[op_name][instant_resv]=true`면 admission window 바깥이어도 now 이후 earliest feasible 시각에 예약되도록 제안. 전부/없음·검증 규칙은 동일하게 적용.

## 코드 변경 계획

1) `proposer.py` 신설
   - DTO/Protocol 정의, `Proposer` 클래스 + `propose(...)` 스텁/구현
   - 내부 helper: 후보 생성, 상태 기반 필터, 주소 샘플, 시퀀스 전개, 사전검증 호출

2) 설정 추가(비파괴)
   - `config.yaml`에 `policies.topN`(기본 6), `policies.epsilon_greedy`(기본 0.0) 키 추가
   - PRD 키 일관성 확인: `phase_conditional`/`groups_by_base`/`exclusions_by_*`/`generate_seq_rules`/`op_specs` 사용(정규화는 CFG 로더 책임, `docs/PRD_v2.md:138-186`)

3) Scheduler 연동(옵션 단계)
   - 별도 Scheduler 구현체에서 훅 처리 루프 내 `Proposer.propose` 호출 지점 연결
   - 성공 시 batch 반환 → RM 트랜잭션으로 예약/커밋(전부/없음 준수)

4) Validator 의존성
   - `validator.py`(경량 어댑터) 도입 또는 기존 RM 규칙 스켈레톤 호출 래퍼 구성

## 삽입/연동 포인트(참조)
- `docs/PRD_v2.md:248` Proposer 워크플로
- `docs/PRD_v2.md:215` Admission window
- `resourcemgr.py:338` `feasible_at`
- `addrman.py:412`/`addrman.py:492`/`addrman.py:630` 샘플링

## 구성(신규/변경)
- `policies.topN`: int, default 6 — Top‑N 후보 평가 상한
- `policies.epsilon_greedy`: float in [0,1], default 0.0 — 희소 탐색 비율(옵션)
- 기존 `policies.maxtry_candidate` 활용, `policies.admission_window_us` 준수
 - `phase_conditional`: PRD 자동 채움/오버라이드 결과를 그대로 사용(누락 시 안전 no‑op)
 - `op_specs[*].instant_resv`: bool, default false — true면 admission window 상한을 무시하고 즉시 예약 시도

## 테스트 전략

- 단위(순수)
  - 결정성: 고정 시드/훅 카운터에서 동일 출력
  - 윈도우: `feasible_at` 결과가 `[t, t+W)` 외면 미선택
  - 즉시예약: `instant_resv=true`인 op는 `[t, t+W)` 밖이어도 now 이후 earliest feasible 시각으로 선택
  - Top‑N 동작: 후보 정렬/상한/타이브레이크 검증
  - 상태 필터: ODT/CACHE/SUSPEND 활성 시 후보 제거, cache 진행 중 celltype 동일성/범위 제한 반영(`docs/PRD_v2.md:264`)
  - 시퀀스 전개: 전부/없음 준수 + Validator 실패 시 대안 재시도
  - multi‑plane: plane_set 생성/축소/샘플링 실패 폴백 동작 검증(`docs/PRD_v2.md:266`)

- 통합(연동)
  - Scheduler 훅 → Proposer → RM 트랜잭션 → commit 경로 성공/실패 케이스
  - AM와의 샘플링 상호작용(단일/멀티플레인)
  - Validator 보고서: epr_dependencies/IO_bus_overlap/exclusion_window_violation/래치/ODT/캐시 금지 항목 반영 확인(`docs/PRD_v2.md:278`)

- 계측
  - per-hook: 후보 수/시도 수/성공 사유, 선택된 시작시각 분포
  - 전역: 성공률, 다양도(선택된 op_name 엔트로피)
  - PRD 달성 지표 연동: `op_state x op_name x input_time` 분포 수집 가능성 점검(`docs/PRD_v2.md:92`)

## 수용 기준(AC)
- AC1: `enable_epr=false`/`epsilon_greedy=0`에서 결정성 유지, 재현 결과 안정
- AC2: admission window 밖 후보는 선택되지 않음; 동일 틱 내 전부/없음 준수
  - 예외: `instant_resv=true`인 op는 window 바깥(≥now) 가능
- AC3: `topN`/`maxtry_candidate`가 탐색 비용/성공률에 영향을 주며, 경계값에서 합리적 동작
 - AC4: ODT/CACHE/SUSPEND 활성 시 금지 후보가 제안되지 않음(필터/Validator로 보장); cache 진행 중 celltype/대상 제한 준수
 - AC5: 단위/통합 테스트 녹색
 - AC6(정합성): 동일 `config.yaml`/전역 시드/동일 초기 스냅샷에서 동일 결과(`docs/PRD_v2.md:283`)

## 리스크/완화
- 탐색 비용 증가 → `topN`/`maxtry`로 상한, 조기중단
- 다양성 부족 → tie‑break 확률가중, ε 옵션
- 규칙 중복 판단 위치 혼동 → 필터는 가벼운 프리체크, 최종 판정은 Validator

## 작업 목록(TODO)
1) proposer.py 스켈레톤 + DTO/Protocol 추가
2) 후보 생성/필터/샘플/feasible/tie‑break 구현
3) 시퀀스 전개 + Validator 사전검증 연동
4) config.topN/epsilon_greedy 키 추가
5) 단위 테스트 작성(test_proposer.py) — 결정성/윈도우/Top‑N/전부‑없음
6) Scheduler 연동(옵션): propose 호출/배치 커밋 경로 연결
7) 관측 포인트/메트릭 노출(선택)
8) cache 진행 중 필터/테스트 추가(라인 264 반영)
9) multi‑plane 샘플링 축소/폴백 테스트 추가(라인 266 반영)
