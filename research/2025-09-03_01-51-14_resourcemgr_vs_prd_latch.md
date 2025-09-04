---
date: 2025-09-03T01:51:14.369297+09:00
researcher: codex
git_commit: 013916e2bf4c7c33f0cf2b0f45b7ada4ec14edb9
branch: main
repository: nandseqgen_v2
topic: "resourcemgr.py vs PRD_v2.md; latch behavior deep-dive"
tags: [research, codebase, resourcemgr, prd_v2, latch]
status: complete
last_updated: 2025-09-03
last_updated_by: codex
---****

# 연구: ResourceManager 구현 vs PRD v2 명세 — 래치 동작 집중 분석

**Date**: 2025-09-03T01:51:14.369297+09:00
**Researcher**: codex
**Git Commit**: 013916e2bf4c7c33f0cf2b0f45b7ada4ec14edb9
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
resourcemgr.py 구현이 docs/PRD_v2.md와 일치하는지 검토하고, 특히 래치(latch) 동작을 별도 항목으로 심층 분석한다.

## 요약
- 일치: plane/die 단위 점유, BUS 충돌, 기본 타임라인 기록(op states), 예약 가능성 검증(feasible/reserve/commit) 등 핵심 골격은 PRD의 의도와 유사하게 구현됨.
- 부분 일치: 배제 윈도우는 cfg에서 파생하도록 구조가 있으나, 현재 키 경로가 `constraints.exclusions`에 고정되어 실제 `config.yaml`의 `exclusion_groups`/`exclusions_by_*`와 직접 연결되지 않음.
- 불일치(중요): 래치 동작이 READ에만 적용됨. PRD의 프로그램(LSB/CSB/MSB) 관련 래치 및 캐시 관련 래치/상태 관리가 부재. READ4K/PLANE_READ/CACHE_READ 등 READ 변형들에 대한 래치 설정도 누락.
- 불일치(보조): PRD의 `op_name.END`를 end_time=inf로 추가하는 규칙과 SUSPEND/RESUME 예외 처리 등 타임라인 상세 규칙이 미구현.

## 상세 발견

### ResourceManager 핵심 동작
- plane/die 예약: `_planescope_ok`로 (die, plane_set/die-wide) 구간 중첩을 거부하고 earliest-available을 기준으로 예약 시간 산정.
- BUS 충돌: `_bus_ok`가 ISSUE 구간 중첩을 거부.
- 타임라인: `commit`에서 `_StateTimeline.reserve_op`로 각 상태 구간을 삽입해 조회 가능(op_state 조회 제공).
- 스냅샷: `snapshot/restore` 제공(plane_resv, bus, excl, latch, timeline 포함)으로 PRD 3.6의 일부 취지 반영.

참조
- `resourcemgr.py:169` — `reserve` 예약 파이프라인(planescope/bus/excl/latch 검사)
- `resourcemgr.py:200` — `commit`에서 예약 반영 및 타임라인 기록
- `resourcemgr.py:223` — `op_state` 조회

### 배제 윈도우(exclusion windows)
- 구현: `_derive_excl`가 `cfg.constraints.exclusions`에서 규칙을 읽어 상태별 윈도우 생성.
- PRD/Config의 기대: `exclusion_groups`, `exclusions_by_*`(op_state/latch/suspend/odt/cache) 기반 배제 규칙을 사용.
- 갭: 현재 `constraints.exclusions` 경로는 `config.yaml`에 존재하지 않음. 결과적으로 파생 윈도우는 빈 목록이 되며, PRD상의 배제 그룹 연동이 동작하지 않음. (검토완료) constraints.exclusion 는 사용하지 않을 것. 대신 single-multi, multi-multi 를 배제하기 위한 용도로 사용

참조
- `resourcemgr.py:272` — `_derive_excl` 구현(현재 경로 미스매치)
- `config.yaml:595` — `exclusion_groups` 루트 키
- `config.yaml:1920` — `exclusions_by_latch_state` 루트 키

### 래치 동작(Deep-Dive)
- PRD 요건 요약
  - 래치 관리 대상: `latches`는 READ/PROGRAM 계열 동작 완료 후 특정 금지 그룹을 활성화.
  - READ/캐시리드 완료 후 cache_latch: `exclusion_groups.after_read`(문서) 및/또는 `after_cache_read`(config) 연동.
  - ONESHOT_PROGRAM_LSB/CSB/MSB 완료 후 각 래치: `after_oneshot_program_*` 그룹.
  - 스코프: READ/PROGRAM 둘 다 (die, plane) 타겟
  - 검증 시 `exclusions_by_*`에 따른 금지 op 제거/거절. -> (TODO) exclusions_by_latch_state 을 통해 구현
- 현재 구현
  - 래치 저장소: `_latch: Dict[(die,plane) -> _Latch]`로 per-plane만 존재. die-wide 래치 구조 부재. -> (TODO) die-wid 래치 별도 만들 필요 없고 PROGRAM 등록시 모든 plane 에 등록
  - 래치 설정: `reserve`에서 base가 "READ"일 때만, 해당 타겟 plane에 `start_us=end`로 래치 생성. 종료 시점은 `release_on_dout_end` 호출 시 해제(None pop).
  - 래치 검사: `_latch_ok`에서 DOUT/SR만 예외 허용, 그 외는 잠금된 plane(PLANE_SET) 또는 동일 die 내 임의 plane 잠김 시(DIE_WIDE) 거부. -> (TODO) _latch_ok 에서 특정 동작만 허용하는 부분 제거. phase_conditional 을 통해 제외/허락 동작 분리됨
  - READ 파생 베이스(READ4K, PLANE_READ, CACHE_READ 등)에 대한 래치 설정 없음. -> (TODO) READ 파생도 동일하게 적용
  - PROGRAM(LSB/CSB/MSB) 계열 래치 설정/해제 루틴 없음. ->(TODO) exclusions_by_latch_state 을 통해 구현
- 정합성 평가
  - READ 계열: PRD는 READ 완료 후 래치 락 및 DOUT 허용을 기대. 구현은 READ base에 한정되어 부분 반영. READ4K/PLANE_READ/CACHE_READ 등은 누락.
  - PROGRAM 계열: PRD의 die-wide 래치 요구(LSB/CSB/MSB) 미구현. -> (TODO) exclusions_by_latch_state 을 통해 구현
  - 배제 그룹 연동: PRD는 래치 상태에 따른 `exclusions_by_latch_state` → `exclusion_groups` 매핑을 강조하나, 구현은 단순 락 기반 포괄 거부이며 그룹 기반 금지 리스트를 사용하지 않음. 세분화·상태표시 부재.
  - 해제 조건: READ의 경우 `release_on_dout_end`가 존재해 DOUT 종료 시 해제 흐름과 일치. 캐시/프로그램 래치 해제 조건은 미구현.

참조
- 문서
  - `docs/PRD_v2.md:284` — 5.5 ResourceManager 섹션 시작
  - `docs/PRD_v2.md:308` — latches: 대상과 각 래치 후 금지 그룹 정의
  - `docs/PRD_v2.md:391` — 7.4 래치 전이 검증 항목
- 코드
  - `resourcemgr.py:192` — READ 완료 시 래치 생성(plane 단위)
  - `resourcemgr.py:121` — `_latch_ok`: DOUT/SR 허용, 그 외 잠금 거부
  - `resourcemgr.py:219` — `release_on_dout_end`: DOUT 종료 시 래치 해제

### 타임라인 세부 규칙
- PRD는 각 op에 `op_name.END` 상태(end_time=inf)를 추가하고 SUSPEND/RESUME 등의 예외 처리(꼬리 제거/복구)를 요구.
- 구현은 states 목록에 정의된 구간만 예약하고 END 상태 추가/예외 루틴 미구현.

참조
- `docs/PRD_v2.md:289` — END 상태 추가 규칙
- `docs/PRD_v2.md:293` — SUSPEND/RESUME 예외 처리 개요
- `resourcemgr.py:213` — 타임라인 구간 삽입(END 없음)

## 코드 참조
- `resourcemgr.py:59` — `_latch` 저장소(dict[(die,plane) -> _Latch])
- `resourcemgr.py:121` — `_latch_ok` 래치 검사 로직
- `resourcemgr.py:192` — READ 완료 시 래치 생성
- `resourcemgr.py:219` — DOUT 종료 시 래치 해제
- `resourcemgr.py:169` — 예약 API(planescope/bus/excl/latch 검사)
- `docs/PRD_v2.md:284` — ResourceManager 요구사항 헤더
- `docs/PRD_v2.md:308` — 래치 종류 및 금지 그룹 정의
- `config.yaml:1920` — `exclusions_by_latch_state` 루트 정의
- `config.yaml:595` — `exclusion_groups` 루트 정의

## 아키텍처 인사이트
- 래치/배제는 단순 플래그 수준이 아니라(plane/die 범위, 발생원소 구분), 그룹 매핑을 통한 금지 연산 집합의 상태 머신으로 관리되어야 함.
- 현재 구조를 유지하되 다음 확장이 자연스러움:
  - 래치 상태 타입(enum)과 스코프(plane vs die) 분리 관리 ->(TODO) state 는 plane scope 로 관리, PROGRAM 시에는 모든 plane 등록
  - READ 계열(READ4K/PLANE_READ/CACHE_READ/PLANE_CACHE_READ/COPYBACK_READ) 포함
  - PROGRAM 계열(LSB/CSB/MSB) 래치 시작/해제 루틴 추가
  - `exclusions_by_latch_state`와 연결해, 단순 포괄 거부 대신 그룹 기반 거부로 정밀도 향상

## 역사적 맥락(thoughts/ 기반)
- 해당 리포지토리에 `thoughts/` 디렉터리가 존재하지 않거나 관련 자료 없음.

## 관련 연구
- 현재 없음

## 미해결 질문
- READ 변형(READ4K/PLANE_READ/CACHE_READ/PLANE_CACHE_READ/COPYBACK_READ)에 대한 래치 정책을 단일화할지, 구분 적용할지? config의 그룹 분류(예: after_read/after_cache_read)와 동기화 필요. -> READ 변형도 동일하게 READ 처럼 적용
- PROGRAM 래치 해제 시점: 후속 단계(예: EXEC_MSB 또는 별도 STATUS READ) 기준 정의 필요. ->(TODO) PROGRAM 은 exclusions_by_latch_state 에 나온대로 2가지 케이스 존재
  - LATCH_ON_LCB(ONESHOT_PROGRAM_LSB 스케쥴뒤)->LATCH_ON_CSB(ONESHOT_PROGRAM_CSB 스케쥴뒤)->LATCH_ON_MSB(ONESHOT_PROGRAM_MSB 스케쥴뒤)->release(ONESHOT_PROGRAM_EXEC_MSB 스케쥴뒤)
  - LATCH_ON_LCB(ONESHOT_PROGRAM_LSB 스케쥴뒤)->LATCH_ON_CSB(ONESHOT_PROGRAM_CSB 스케쥴뒤)->release(ONESHOT_PROGRAM_EXEC_MSB 스케쥴뒤)
- `_derive_excl`를 어디서 소비할지(Scheduler/Validator vs ResourceManager): 책임 경계 확정 필요. -> (TODO) ResourceManager
