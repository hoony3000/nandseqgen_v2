---
date: 2025-09-05T02:25:23.905437+09:00
researcher: codex
git_commit: fc87b49
branch: main
repository: nandseqgen_v2
topic: "scheduler 부트스트랩으로 블록/페이지 사전 ERASE·PROGRAM 비율을 설정해 초기 program/read 거절율을 높이는 방법 (bootstrap 예약 오퍼레이션을 최종 출력에 포함)"
tags: [research, codebase, scheduler, bootstrap, preconditioning, EPR, AddressManager, ResourceManager]
status: complete
last_updated: 2025-09-05
last_updated_by: codex
---

# 연구: scheduler 부트스트랩으로 블록/페이지 사전 ERASE·PROGRAM 비율을 설정해 초기 program/read 거절율을 높이는 방법 (bootstrap 예약 오퍼레이션을 최종 출력에 포함)

**Date**: 2025-09-05T02:25:23.905437+09:00
**Researcher**: codex
**Git Commit**: fc87b49
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
@2025-09-05_scheduler_progressive_atomic_chunk_impl_plan.md 을 참고해서, scheduler 에 bootstrap 을 통해서 erase/program 을 전체 block 에 얼마의 비율로 erase, block 내 page 에 얼마의 비율로 program, 해놓아서 초기 prgram/read 거절율을 높일 수 있는 방법을 research 해줘. bootstrap 으로 예약된 operation 들도 최종 아웃풋 형태에 포함되어야 해.

## 요약
- 목표: 시뮬레이터 시작 시점에 Scheduler 부트스트랩 단계에서 ERASE/PROGRAM 작업을 비율 기반으로 "예약"해 초기 상태를 의도적으로 만들고, 이 예약 작업을 Required Outputs(시퀀스/타임라인)에도 포함한다.
- 핵심 아이디어:
  - 블록 단위: 전체 블록의 `pre_erase.block_ratio`만큼을 무작위 ERASE 예약 → ERASE 비율이 낮을수록 초기 PROGRAM 후보 고갈로 인해 PROGRAM 거절(샘플 불가)이 증가.
  - 페이지 단위: ERASE된 블록 중 `pre_program.block_ratio_within_erased`만큼을 선택해 각 블록에서 `pre_program.page_fill_ratio`(또는 정수 `pages_per_block`)만큼 순차 PROGRAM 예약 → READ는 `constraints.epr.offset_guard`와 AddressManager.offset 조건으로 유효 페이지가 줄어 초기 READ 거절(샘플 불가)이 증가.
  - 예약 실체: 부트스트랩도 일반 스케줄과 동일하게 RM 트랜잭션(begin→reserve→commit)으로 처리하고, 커밋 직후 AddressManager에 동등한 apply(erase/pgm)를 수행해 주소 상태를 일치시킨다. 예약 직후 TimelineLogger.log_op를 호출해 최종 출력에 반영한다.
- Progressive(원자 청크)와의 정합: 부트스트랩 작업은 "strict atomic chunk"(전부/없음)로 여러 청크에 나눠 커밋(참조: plan/2025-09-05_scheduler_progressive_atomic_chunk_impl_plan.md). 청크 내 일부 실패 시 롤백 후 더 작은 청크로 재시도.
- 구성 키 제안: `scheduler.bootstrap.*` 섹션으로 비율·모드·연결 정책 정의. `constraints.epr.*` 및 `AddressManager.offset`을 함께 조정해 READ 거절율을 제어.

## 상세 발견

### 구성 요소/영역 1 — AddressManager(주소 상태·샘플링)
- 샘플링 경로는 상태에 따라 유효 후보만 반환하므로, 상태 전처리(사전 ERASE/PROGRAM 비율)로 "샘플 실패(sample_none)"를 유도 가능.
- `sample_pgm`/`random_pgm`: ERASE 또는 같은 모드의 연속 PROGRAM만 허용. ERASE가 적으면 PROGRAM 후보가 급감 → 제안 실패율 증가.
  - `addrman.py:606` — `apply_pgm`
  - `addrman.py:730` — `sample_pgm` 비(非)순차/순차 로직(라인 수는 파일 최신 기준 근사)
- `sample_read`는 `offset`을 가드로 사용. 프로그램된 마지막 페이지−offset 이 읽기 가능 상한. 프로그램 페이지가 적고 offset이 크면 READ 후보가 없어짐.
  - `addrman.py:630` — `sample_read(offset=...)`
- 초기 상태 유틸 참조: 벤치 툴에서 ERASE/PROGRAM 혼합 상태 구성 예시가 있음.
  - `tools/bench_addrman.py:38` — `_prepare_initial_state(am, seed, pre_erase_ratio, pre_pgm_pages, mode)`

### 구성 요소/영역 2 — ResourceManager(예약/커밋/EPR 오버레이)
- 예약/커밋 트랜잭션 경계 제공. 부트스트랩도 동일 경로 사용해 타임라인을 생성하고 로그에 남길 수 있다.
  - `resourcemgr.py:338` — `feasible_at`
  - `resourcemgr.py:363` — `reserve`
  - `resourcemgr.py:415` — `commit`
- EPR(주소 의존 규칙) 오버레이는 동일 트랜잭션 내 후속 작업에만 반영되므로, 부트스트랩 커밋 이후 AddressManager에도 실제 상태 반영(apply)을 수행해 일관성 유지가 필요.
  - `resourcemgr.py:836` — 예약된 ERASE/PROGRAM에 따른 `addr_overlay` 업데이트

### 구성 요소/영역 3 — Proposer/PRD 정책
- PRD 스케줄러는 동일 틱 내 전부/없음 원칙을 요구(부분 커밋 금지).
  - `docs/PRD_v2.md:221` — 동일 틱 내 부분 스케줄 금지
- 큐 리필 훅은 부트스트랩/제안 실패 대비 용도.
  - `docs/PRD_v2.md:225` — QUEUE_REFILL 목적
- Required Outputs 정의(시퀀스/타임라인/상태 타임라인). 부트스트랩 예약도 동일 스키마로 포함돼야 함.
  - `docs/PRD_v2.md:22` — Operation Sequence CSV 스키마

### 구성 요소/영역 4 — 출력/로깅
- 예약 직후 `_schedule_operation(...)` 경로에서 타임라인 로거 호출 요구.
  - `viz_tools.py:45` — `TimelineLogger`
  - `viz_tools.py:62` — `TimelineLogger.log_op(...)`

## 제안: Scheduler 부트스트랩 설계

1) 구성 스키마
- `scheduler.bootstrap.enabled: bool` — 기본 true
- `scheduler.bootstrap.seed: int|null` — null이면 전역 시드 분기 사용
- `scheduler.bootstrap.erase:`
  - `block_ratio: float` — 0..1, 전체 블록 중 ERASE할 비율(기본 0.25)
  - `op_name: str` — 기본 `Block_Erase_TLC` (config에 존재)
  - `sel_plane: int|int[]|null` — 멀티플레인 그룹화 옵션(null이면 단일 플레인 단위)
- `scheduler.bootstrap.program:`
  - `block_ratio_within_erased: float` — ERASE된 블록 중 프로그램 대상으로 선택할 비율(기본 0.5)
  - `page_fill_ratio: float` 또는 `pages_per_block: int` — 각 대상 블록의 프로그램 페이지 수(기본 ratio=0.05)
  - `sequential: bool` — true면 0..N−1 순차 페이지, false면 비순차
  - `op_name: str` — 기본 `All_WL_Dummy_Program`(base=PROGRAM_SLC)
  - `celltype: str` — `SLC|A0SLC|ACSLC` 등(ERASE 모드와 호환)
- 거절율 제어용 보조 키(권장):
  - `constraints.enable_epr: true` + `constraints.enabled_rules: ["addr_dep"]`
  - `constraints.epr.offset_guard: int` — READ 가드(AM.offset과 병행 고려)
  - `policies.admission_window: float` — 윈도 한정으로 창 내 배치 실패율 조절

2) 알고리즘(부트스트랩 트랜잭션)
- 입력: CFG, RNG, AM, RM, Logger
- 파이프라인:
  1. 파라미터 해석 및 난수 분기(전역시드, hook_counter=0 등). 토폴로지에서 `dies, blocks_per_die, pages_per_block` 확보.
  2. ERASE 대상 샘플링: 각 die에서 글로벌 블록 인덱스로 변환하여 `ceil(total_blocks * block_ratio)`개 무작위 선택.
  3. ERASE 예약 청크 실행: `max_ops_per_chunk` 단위로 RM `begin(now=0) → reserve(ERASE) → commit` 반복. 실패 시 롤백·청크 축소 재시도.
  4. PROGRAM 대상 샘플링: ERASE된 블록 중 `block_ratio_within_erased` 비율 선택. 각 블록에 대해 `pages = ceil(pages_per_block * page_fill_ratio)` 계산(상한 `pagesize-1`). sequential이면 페이지 0..pages-1로 채움.
  5. PROGRAM 예약 청크 실행: 각 페이지를 개별 PROGRAM_SLC로 예약(오버레이가 페이지를 "마지막 프로그램 페이지"로 반영). 청크 실패 시 롤백·축소.
  6. 커밋 직후 AM 동기화: 커밋된 ERASE/PROGRAM 타깃을 AM에 `apply_erase`/`apply_pgm`로 반영해 이후 Proposer 샘플링과 EPR 평가가 동일한 상태를 보게 함.
  7. 로깅/출력: 각 예약 성공 시 `_schedule_operation(...)`에서 `TimelineLogger.log_op(op, start_us, end_us, label_for_read)` 호출. Operation Sequence CSV에도 `source: "bootstrap"` 메타 포함.

3) 예약 오퍼레이션 선택(예시 op_name)
- ERASE: `config.yaml:2334` — `Block_Erase_TLC`(base=ERASE, multi=false)
- PROGRAM: `config.yaml:2299` — `All_WL_Dummy_Program`(base=PROGRAM_SLC)
- 주의: RM 오버레이는 `PROGRAM_SLC`/`COPYBACK_PROGRAM_SLC`에만 addr_state를 갱신(`resourcemgr.py:836`). 초기 부트스트랩에서는 이 두 베이스를 사용하는 것이 안전.

4) 초기 거절율(예상 메커니즘)
- PROGRAM 거절 증가:
  - ERASE 비율을 낮추면 `AddressManager.sample_pgm` 후보가 고갈되어 Proposer에서 `sample_none` 비율 증가.
  - 추가로 admission_window를 짧게 설정하면 타임라인 충돌로 `window_exceed`도 증가.
- READ 거절 증가:
  - PROGRAM 페이지 채움이 적고 `constraints.epr.offset_guard`(또는 AM.offset)가 크면 `sample_read` 후보가 없어서 `sample_none` 증가.
  - celltype 불일치(ERASE SLC vs PROGRAM 모드 불일치)는 EPR `epr_different_celltypes_on_same_block` 실패를 유도할 수 있으나, AM 샘플러는 모드 일관성을 유지하려 하므로 기본값에서는 샘플 실패 쪽이 주로 관측됨.

5) 아토믹 청크와의 통합
- 부트스트랩도 plan에서 정의한 Strict Atomic Chunk를 그대로 사용:
  - 청크 크기: `scheduler.chunk.max_ops_per_chunk`(기본 5)
  - 부분 성공 금지: `allow_partial_success=false`
  - 청크 실패 시 롤백 후 작은 청크로 재시도(예: 이진 탐색적 축소)
- 참조: `plan/2025-09-05_scheduler_progressive_atomic_chunk_impl_plan.md`

## 코드 참조
- `plan/2025-09-05_scheduler_progressive_atomic_chunk_impl_plan.md:1` — Strict Atomic Chunk 구현 계획
- `docs/PRD_v2.md:221` — 동일 틱 내 부분 스케줄 금지(전부/없음)
- `docs/PRD_v2.md:225` — QUEUE_REFILL(부트스트랩/실패 대비)
- `docs/PRD_v2.md:22` — Operation Sequence CSV
- `resourcemgr.py:338` — `feasible_at`
- `resourcemgr.py:363` — `reserve`
- `resourcemgr.py:415` — `commit`
- `resourcemgr.py:836` — 예약 오버레이로 addr_state 반영
- `addrman.py:606` — `apply_pgm`
- `addrman.py:630` — `sample_read`
- `tools/bench_addrman.py:38` — 초기 상태 혼합 샘플 코드
- `viz_tools.py:62` — `TimelineLogger.log_op`
- `config.yaml:2334` — `Block_Erase_TLC`
- `config.yaml:2299` — `All_WL_Dummy_Program`

## 아키텍처 인사이트
- 샘플러는 기본적으로 유효 후보만 반환하므로, "규칙 위반으로 인한 거절"보다 "샘플 불가/윈도우 초과"가 먼저 늘어난다. 초기 거절율 튜닝에는 사전 상태 비율·offset·윈도우/멀티플레인 점유(타임라인 충돌)가 효과적.
- RM 오버레이는 트랜잭션 한정이므로, 부트스트랩 커밋 후 AddressManager.apply_*로 상태를 반영해 Proposer/EPR과 일치시켜야 함.
- PROGRAM 페이지를 부트스트랩에서 개별 페이지 단위로 예약하면 출력이 많아진다. 실행 시간·출력 크기 제약을 고려해 청크 크기/대상 블록 수/페이지 수를 조정해야 함.

## 관련 연구
- `research/2025-09-05_01-35-43_scheduler_standalone_vs_embedded.md` — Scheduler와 RM 경계, 트랜잭션/오버레이 개념

## 미해결 질문
- 프로그램 페이지 예약을 캐시/원샷(ONESHOT) 계열로 축약해도 addr_state 반영이 필요함. RM 오버레이/AM 반영 경로를 어떻게 단순화할지?
- 부트스트랩 예약과 런타임 제안의 우선순위: 단일 레일에서 무해한 동시 제안 허용 정책(오픈 이슈, `docs/PRD_v2.md:426`).
- 출력 사이즈 관리: 초기 부트스트랩에서 수천~수만 건의 PROGRAM이 발생할 수 있음. 페이지 샘플링을 압축하는 정책(예: per-block aggregate) 도입 여부.

