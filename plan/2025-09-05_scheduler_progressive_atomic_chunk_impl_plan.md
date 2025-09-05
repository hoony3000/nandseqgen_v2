---
title: "Implementation Plan — Scheduler Progressive/Hybrid (Strict Atomic Chunk)"
author: codex
date: 2025-09-05
status: draft
owners: [scheduler]
reviewers: [proposer, resourcemgr, addrman, validator]
adr_ref: research/2025-09-05_01-35-43_scheduler_standalone_vs_embedded.md
---

## Problem 1‑Pager

- 배경: PRD 스케줄러는 이벤트 훅 기반으로 결정적으로 시간 전진하고, admission window 내 earliest feasible 슬롯을 찾아 배치를 전부/없음으로 커밋한다. 동일 틱 내 부분 스케줄 금지가 명시됨(`docs/PRD_v2.md:221`). Progressive/Hybrid 모드에서는 한 훅에서 여러 오퍼레이션을 사전검증(preflight) 후 커밋할 수 있어야 한다.
- 문제: Progressive/Hybrid 모드의 체크포인트/롤백 정책을 “대안 1 — 완전 원자적 청크(부분 성공 금지)”로 구현해야 한다. 즉, 한 훅에서 계획된 청크가 하나라도 실패하면 전체를 롤백하고 아무 것도 커밋하지 않는다.
- 목표:
  - 체크포인트/롤백 정책: `allow_partial_success=false`, `checkpoint_interval=max_ops_per_chunk` 강제
  - 사전검증(preflight)로 청크 전체 feasibility를 확인 후 단일 트랜잭션으로 커밋
  - PRD 전부/없음 원칙 준수, 결정성 보장, 관측 지표(성공/롤백/커밋 수) 노출
  - 기존 Proposer/ResourceManager/AddressManager 경계를 유지(포트 주입)
- 비목표:
  - Adaptive(동적) 체크포인트 크기, 부분 성공 허용 정책 구현
  - RM/AM 내부 구조 변경 또는 대규모 리팩터링
  - 레거시 `nandsim_demo.py` 전체 치환
- 제약:
  - 결정성: 전역 시드 + 훅별 RNG 분기, 시스템 시간 금지 (docs/PRD_v2.md:231)
  - 전부/없음: 동일 훅 내 청크는 단일 트랜잭션으로 원자 커밋 (docs/PRD_v2.md:221)
  - 윈도우: 첫 op의 시작은 `[t, t+W)` 내여야 하며 instant_resv 예외 준수 (docs/PRD_v2.md:215, docs/PRD_v2.md:221)
  - 이벤트 훅: `QUEUE_REFILL`, `PHASE_HOOK`, `OP_START`, `OP_END`의 의미와 순서를 준수 (docs/PRD_v2.md:235, docs/PRD_v2.md:240, docs/PRD_v2.md:247)
  - PHASE_HOOK 생성 규칙: ISSUE state에는 생성하지 않으며, 각 state 종료 전/후 랜덤 시점 생성 (결정적 RNG로 샘플) (docs/PRD_v2.md:240)
  - 종료 루틴: `run_until` 이후 propose 차단, 잔여 op 종료 대기, 출력/스냅샷 수행 (docs/PRD_v2.md:248-258, docs/PRD_v2.md:270-277, docs/PRD_v2.md:3.6, 6)

## 접근 대안 및 결정

1) Strict atomic chunk(선택): 한 훅당 1 청크, 사전검증 후 전체 커밋/전체 롤백. 처리량은 보수적이지만 PRD 원칙과 일치.
2) Partial with checkpoints: 배치를 나눠 부분 성공 허용 — 본 계획 범위 외.

결정: 1) Strict atomic chunk.

## 설계/아키텍처

- 구성요소 경계(포트/어댑터):
  - Proposer: `propose(now, hook, cfg, res_view, addr_sampler, rng) -> ProposedBatch | None` (proposer.py:534)
  - ResourceManager: `feasible_at`/`reserve`/`commit`/`rollback` 경로 사용 (resourcemgr.py:338, resourcemgr.py:415)
  - 상태 조회: `op_state`/`has_overlap`/ODT/CACHE/SUSPEND (resourcemgr.py:483, resourcemgr.py:486)
- 스케줄러 상태/루프: 내부 min‑heap 이벤트 큐(QUEUE_REFILL, PHASE_HOOK, OP_START, OP_END). 레거시 참조(구성 및 훅 처리 순서): `nandsim_demo.py:2394`.
- Progressive 모드 동작(원자 청크):
  1) PHASE_HOOK 팝 → earliest_start = now, 빈 planned 리스트
  2) 최대 `max_ops_per_chunk`까지 다음을 반복: Proposer 호출 → batch(시퀀스 포함) 수집 → 사전검증(전체 시퀀스 preflight) → feasible이면 planned에 추가, 아니면 중단
  3) planned가 비어있지 않으면 트랜잭션 스냅샷 취득 → planned 내 모든 op에 대해 예약 시도 → 하나라도 실패하면 스냅샷으로 롤백 → 성공 시 커밋
  4) 성공한 op들에 대해 OP_START/OP_END 및 PHASE_HOOK 생성(ISSUE 제외; 각 state의 종료 전/후에 결정적 난수로 오프셋된 훅 생성)

- RNG 분기(결정성): 훅 처리 시 `(global_seed, hook_counter)` 기반 스트림 사용. PHASE_HOOK 시점 선택과 후보 타이브레이크 등에 동일 분기 사용 (docs/PRD_v2.md:231).

## 알고리즘(사전검증/커밋 의사코드)

```
def _handle_phase_hook(hook):
    planned = []
    for _ in range(cfg.scheduler.chunk.max_ops_per_chunk):
        pb = proposer.propose(now, hook, cfg, res_view, addr_sampler, rng)
        if not pb: break
        if not preflight(pb.ops): break
        planned.extend(pb.ops)
    if not planned: return
    snap = rm.begin(now)
    ok = True
    for op in planned:
        if not schedule(op, snap): ok = False; break
    if not ok: rm.rollback(snap); return
    rm.commit(snap); emit_hooks(planned)
```

## 구성/파라미터

- 새 섹션(권장):
  - `scheduler.chunk.max_ops_per_chunk: int` (기본 5)
  - `scheduler.chunk.allow_partial_success: false` (강제 false)
  - `scheduler.chunk.checkpoint_interval: =max_ops_per_chunk` (강제 동치)
- 기존 레거시 키와의 매핑(임시): `propose.chunking.max_ops_per_chunk`, `allow_partial_success`, `checkpoint_interval` (nandsim_demo.py:2971, nandsim_demo.py:2975)
  
- 스케줄러 공통 정책:
  - `policies.admission_window`(us): 제안 윈도우 폭 (docs/PRD_v2.md:215)
  - `policies.queue_refill_period_us`: QUEUE_REFILL 주기 (docs/PRD_v2.md:235)
  - `scheduler.run_until_us`: 시뮬레이션 종료 시각 (docs/PRD_v2.md:5.3 종료 조건)
  - `scheduler.num_runs`: 반복 횟수(배치 생성 반복) (docs/PRD_v2.md:5.3 attributes)

## 변경 사항(작업 단위)

1) `scheduler.py` 스켈레톤 추가: 이벤트 큐/훅 처리기/사전검증/원자 커밋 루프
2) 구성 로더: `scheduler.chunk.*` 키 해석 + 기본값 주입
3) 사전검증(preflight) 함수: RM `feasible_at`로 시퀀스 전체 건너뛰기 없이 평가
4) 트랜잭션 스냅샷 사용: `rm.begin(now)` → 예약 → 실패 시 `rm.rollback(snap)` → 성공 시 `rm.commit(snap)` (resourcemgr.py:415)
5) 훅 생성: OP_START/OP_END/PHASE_HOOK 정책 적용(ISSUE 제외, state 종료 전/후의 결정적 랜덤 오프셋, docs/PRD_v2.md:240)
6) 관측 지표: `ckpt_success_batches`, `ckpt_rollback_batches`, `ckpt_ops_committed` 카운터 추가 (nandsim_demo.py:2415 참조)
7) Runner 스크립트(옵션): 구성요소 조립 및 실행 루틴
8) 종료 루틴: `run_until` 넘긴 후 propose 중단, 잔여 op 종료 대기, Required Outputs 및 스냅샷 트리거(3.1~3.6 참조)
9) 문서 업데이트: README/PRD 주석 링크 및 설정 설명

## 테스트 계획

- 단위 테스트
  - 동일 훅 내 일부 실패 유도 시 전체 롤백 확인(커밋 없음)
  - admission window 경계에서 첫 op만 윈도우 내, 후속은 자연스레 now 이후로 배치됨을 확인(사전검증 통과 시 전체 커밋)
  - instant_resv op가 포함된 배치의 윈도우 예외 동작 확인
- 통합 테스트
  - IO_bus 중첩/래치 금지/ODT/CACHE/SUSPEND가 혼재된 시나리오에서 원자 청크 불변식 유지
  - 결정성: 동일 시드/설정/스냅샷에서 결과 동일(타임라인·출력)
  - PHASE_HOOK 정책: ISSUE state 미생성, state 종료 전/후 훅 배치 결정성 검증
  - QUEUE_REFILL 주기: 설정값에 맞춰 훅 생성·소비 주기 검증
  - 종료 루틴: `run_until` 이후 propose 차단, 잔여 op 정상 종료 및 스냅샷/출력 생성 검증
- 성공 기준(AC)
  - AC1: `allow_partial_success=false` 설정에서 배치 단위 전부/없음 준수
  - AC2: 실패 시 `ckpt_rollback_batches` 증가, 커밋 없음
  - AC3: 성공 시 `ckpt_success_batches`/`ckpt_ops_committed` 집계 일치
  - AC4: 결정성 및 PRD 규칙 위반 없음(테스트 녹색)

## 리스크/완화

- 처리량 저하: 청크 크기 튜닝 및 Proposer Top‑N 개선으로 완화
- 장시간 실패 연속 시 기아: QUEUE_REFILL/NUDGE 정책으로 훅 재추진(nandsim_demo.py:3002)
- 시퀀스 경계 무시 위험: 한 배치에 시퀀스 전체만 포함하도록 preflight에서 경계 강제
 - 종료 경계 미흡: `run_until` 가드 및 propose 차단 플래그로 보호; E2E 테스트로 보증

## 코드 참조

- `docs/PRD_v2.md:211` — Scheduler 역할
- `docs/PRD_v2.md:215` — admission window
- `docs/PRD_v2.md:221` — 동일 틱 내 부분 스케줄 금지(전부/없음)
- `docs/PRD_v2.md:240` — PHASE_HOOK 생성 원칙
- `docs/PRD_v2.md:235` — QUEUE_REFILL 주기
- `docs/PRD_v2.md:5.3 종료 조건` — run_until/종료 루틴
- `proposer.py:22` — `ProposedBatch` DTO
- `proposer.py:534` — `propose` API
- `resourcemgr.py:338` — `feasible_at`
- `resourcemgr.py:415` — `commit`
- `resourcemgr.py:483` — `op_state`
- `resourcemgr.py:486` — `has_overlap`
- `nandsim_demo.py:2394` — 레거시 Scheduler
- `nandsim_demo.py:2971` — `max_ops_per_chunk`
- `nandsim_demo.py:2972` — `allow_partial_success`
- `nandsim_demo.py:2975` — `checkpoint_interval`
- `nandsim_demo.py:2467` — `_begin_txn`
- `nandsim_demo.py:2506` — `_rollback_txn`
- `nandsim_demo.py:2415` — 관측 지표 필드

## 작업 목록(TODO)

1) `scheduler.py` 생성 및 이벤트 루프 스켈레톤 구현
2) `scheduler.chunk.*` 파라미터 파싱/기본값 주입
3) `_preflight_chunk_atomic()` 구현(시퀀스 포함 전체 feasibility 확인)
4) `_begin_txn/_rollback/_commit` 경로 연결 및 원자 커밋 루프
5) 훅/로그/메트릭 삽입(ckpt 성공/롤백/커밋 수)
6) 단위 테스트(원자성/결정성/윈도우/instant_resv)
7) 통합 테스트(버스/래치/배제/상태 금지 혼합 시나리오)
8) 문서화(README/PRD 주석 링크, 설정 예시)
