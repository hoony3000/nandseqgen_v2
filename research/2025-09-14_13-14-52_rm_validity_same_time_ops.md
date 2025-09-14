---
date: 2025-09-14T13:14:52+0900
researcher: codex
git_commit: 2ce0ed0
branch: main
repository: nandseqgen_v2
topic: "동일 시각 다중 예약 발생 원인: ResourceManager validity가 왜 스크린하지 못했는가"
tags: [research, codebase, resourcemgr, scheduler, bus, instant_resv]
status: complete
last_updated: 2025-09-14
last_updated_by: codex
last_updated_note: "동일 시각 다중 예약 방지 방안 비교 및 제안 추가"
---

# 연구: 동일 시각 다중 예약 발생 원인 — RM validity 미차단 이유

**Date**: 2025-09-14T13:14:52+0900
**Researcher**: codex
**Git Commit**: 2ce0ed0
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
동일한 시각에 operation이 다수 예약되는 문제가 있어. ResourceManager의 validity에서 왜 스크린 되지 않는지?

## 요약
- 즉시 예약(instant_resv)인 `SR`/`SR_ADD`(상태 읽기) 계열이 버스 전용 검증 경로로 예약되고, RM의 버스/플레인 충돌 검사가 “커밋된 창(self.*)”만 확인하고 “동일 트랜잭션 보류 창(txn.*)”은 확인하지 않아, 같은 트랜잭션 내 다중 예약이 필터링되지 않을 수 있음.
- 스케줄러는 한 트랜잭션 내 후속 op의 시작을 이전 op 종료 시점으로 밀기 위해 `txn.now_us`를 갱신하지만, 제안 배치 구성에 따라 같은 시각에 버스 구간이 겹치는 조합(SR ↔ ERASE/PROGRAM 등)이 함께 들어오면 RM의 현행 검증만으로는 충돌을 차단하지 못함.
- 결과적으로 `out/operation_sequence_...csv`에서 같은 타임스탬프에 `Read_Status_Enhanced_70h`와 `Block_Erase_*`/`One_Shot_PGM_*`가 공존.

## 상세 발견

### Instant 예약 경로 (SR/SR_ADD)
- `op_bases.SR`, `op_bases.SR_ADD`는 `scope: NONE`, `instant_resv: true`로 설정됨.
  - `config.yaml:400` 부근(`SR`, `SR_ADD` 항목) 및 `config.yaml:4320` 이후의 `op_names.Read_Status_Enhanced_*`에서 base 매핑 확인.
  - `config.yaml:4320` — `Read_Status_Enhanced_70h: base: SR`
  - `config.yaml:4336` — 여러 `Read_Status_Enhanced_7xh: base: SR_ADD`
- RM는 instant 경로에서 플레인/다이 배타 및 예약창을 건너뛰고 버스/래치/규칙만 검사함.
  - `resourcemgr.py:432` — instant path 분기 주석 및 로직
  - `resourcemgr.py:444` — instant 경로는 `txn.bus_resv`만 추가(plane/die 예약창 미생성)

### 버스 충돌 검사의 범위 제한
- 버스 충돌 검사 구현은 커밋된 버스 창(`self._bus_resv`)만 확인하고, 같은 트랜잭션에서 방금 추가한 보류 창(`txn.bus_resv`)은 고려하지 않음.
  - `resourcemgr.py:248` — `_bus_ok(...)`는 `self._bus_resv`만 순회
  - 동일 트랜잭션 내 다중 예약 시, 선예약된 op의 버스 구간과 후속 op의 버스 구간이 겹쳐도 검출하지 못할 수 있음.

### 플레인 예약창 검사도 커밋 상태만 참조
- `_planescope_ok(...)` 역시 커밋된 플레인 창(`self._plane_resv`)만 본다. 보류 중인 `txn.plane_resv`는 미반영.
  - `resourcemgr.py:240` — `_planescope_ok(...)` 구현
- 본 증상은 주로 버스 충돌에서 관측되었으나, 같은 트랜잭션 내 동일 플레인 중복 예약도 구조적으로 허용될 소지가 있음(스케줄러가 실제로 같은 플레인을 중복 제안하지 않는 한 드물게 발생).

### 다이 레벨 배타 창은 보류분 반영(정상)
- 단, 다이 레벨 single/multi 배타 검사는 `pending` 창을 함께 확인해 동트랜잭션 충돌을 차단.
  - `resourcemgr.py:269` — `_single_multi_violation(..., pending=...)`
  - `resourcemgr.py:487` — 예약 시 `txn.excl_die`에 OPBASE 토큰 포함 창 추가

### 스케줄러 동작과 같은 시각 발생 패턴
- 스케줄러는 예약 성공마다 `txn.now_us = r.end_us`로 갱신하여 같은 트랜잭션 내 순차화를 시도함.
  - `scheduler.py:408` — `txn.now_us` 갱신
- 그러나 하나의 propose 배치에 (1) `SR`(instant, bus-only)와 (2) `ERASE/PROGRAM`(ISSUE 단계 bus 보유)이 함께 포함되면, RM의 버스 검사가 `txn.bus_resv`를 고려하지 않아 같은 시각 시작이 허용될 수 있음.
- 관측 예시: `out/operation_sequence_250914_0000001.csv`에서 다음처럼 동일 시각 공존
  - `7000.11us`: `Block_Erase_SLC`(ERASE) + `Read_Status_Enhanced_70h`(SR)
  - `21500.11us`: `Block_Erase_TLC` + `Read_Status_Enhanced_70h`
  - `29029.82us`: `One_Shot_PGM_CSB` + `Read_Status_Enhanced_70h`
  - `29059.64us`: `One_Shot_PGM_MSB_23h` + `Read_Status_Enhanced_70h`

## 코드 참조
- `resourcemgr.py:248` — `_bus_ok`: 커밋된 버스 창만 검사
- `resourcemgr.py:240` — `_planescope_ok`: 커밋된 플레인 창만 검사
- `resourcemgr.py:389` — `feasible_at`: instant 경로는 plane/die 배타 우회, bus/래치/규칙만
- `resourcemgr.py:432` — `reserve`: instant 경로 예약(plane/die 창 미생성, bus만 `txn`에 추가)
- `resourcemgr.py:508` — `commit`: `txn.*`을 실제 상태로 반영
- `scheduler.py:408` — 트랜잭션 내 순차화 위해 `txn.now_us = r.end_us` 갱신
- `config.yaml:4320` — `Read_Status_Enhanced_70h: base: SR` 매핑(instant)
- `config.yaml:400` — `op_bases.SR`/`SR_ADD`: `scope: NONE`, `instant_resv: true`

## 아키텍처 인사이트
- RM 검증은 세 축으로 구성됨: (1) 타임라인 창(plane/bus), (2) 다이 배타(single/multi), (3) 상태·주소 규칙(rule). 이 중 (1)에서 보류(txn) 창 미반영이 있어 원자적 예약 배치 내 상호 충돌을 놓칠 수 있음.
- instant_resv는 설계상 빠른 예약(버스 전용)을 허용하지만, 그 경우일수록 동트랜잭션 내 버스 상호배제 검사가 필요.

## 관련 연구
- `research/2025-09-08_13-55-13_suspend_resume_timeline_and_reschedule.md`
- `plan/2025-09-07_dout_exclusion_multi_fix_plan.md`

## 미해결 질문
- 스케줄러의 배치 구성 순서/정책이 같은 시각 교차를 더 자주 유발하는지(예: 상태 폴링과 이슈 발행을 한 배치로 함께 제안).
- instant_resv의 범위를 `scope: NONE`로 강제하는 정책(`policies.instant_bus_only_scope_none`)을 활성화할 필요 여부.
- 버스 폭주 방지를 위해 `_bus_ok`에 `txn.bus_resv`를 포함하는 변경이 요구되는지(같은 트랜잭션 내 상호배제 강화).
