---
title: "구현 계획 — instant_resv: 버스 기준 즉시(now) 예약"
date: 2025-09-07
based_on: research/2025-09-07_instant_resv_bus_only_at_now.md
status: draft
owners: ["Codex"]
reviewers: ["resourcemgr", "scheduler", "proposer"]
---

## Problem 1‑Pager

- 배경: `instant_resv=true` 베이스는 현재 제안 창(admission window) 우회에만 사용되고, 실제 예약에서는 plane/die 배타 규칙이 그대로 적용됨.
- 문제: READ의 `CORE_BUSY`/`DATA_OUT` 동안 버스가 비어 있어도, die/plane 겹침 차단과 plane 예약 창 때문에 `instant_resv` 기반의 버스 전용 오퍼(RESET/ZQCAL/ODT 등)가 now 시각에 즉시 예약되지 않음.
- 목표: ResourceManager에서 `instant_resv` 베이스에 한해 시작시각을 now 로 고정하고, 검증을 "버스 겹침 + (선택) 래치/정책"만 적용하도록 하여 plane/die 배타 규칙을 우회. 후속 스케줄에 영향이 없도록 plane/die 배타 창 기록은 생략.
- 비목표: Scheduler/Proposer의 주요 정책 변경, 주소 샘플링/시퀀스 전개 로직 변경, 기존 배타 규칙의 일반 케이스 완화.
- 제약: 결정성 유지(양자화 `quantize`), 함수 ≤ 50 LOC/파일 ≤ 300 LOC, 명시적 분기/가독성 우선, 민감정보 로깅 금지.

## 영향도 및 호출 경로

- proposer → rm.feasible_at: 후보의 earliest t0 확인에 사용됨.
  - proposer.py:980
- scheduler → rm.reserve: 실제 예약/커밋 경로.
  - scheduler.py:332
- RM 내부 영향 포인트:
  - resourcemgr.py: pe: Scope = Scope.PLANE_SET) -> Optional[float]:  (feasible_at 본문 시작 인접)
  - resourcemgr.py:465 (reserve 본문 시작 인접)

## 접근 대안(비교)

1) 옵션 A — instant를 "버스 기준 즉시 예약"으로 확장(권장)
   - 장점: READ `CORE_BUSY`/`DATA_OUT` 중에도 RESET/ZQCAL/ODT 등 버스 전용 오퍼가 즉시(now) 들어감. plane/die 가용성 왜곡 없음.
   - 단점: plane 타임라인(상태)과 plane 예약창의 괴리가 발생할 수 있음(버스 전용 오퍼에 한해 의도된 중첩으로 간주).
   - 위험: 검증 규칙(래치/정책) 누락 시 예기치 않은 동시성 허용 → 래치/룰 검증은 유지.

2) 옵션 B — 적용 범위 축소(버스 전용 + scope: NONE 제한)
   - 장점: 데이터 경로(READ/PROGRAM)에 영향 최소화.
   - 단점: 설정 확장성 저하. 일부 베이스(PLANE_SETPARA 등)는 제외될 수 있음.

3) 옵션 C — 다중성만 우회(plane 창은 유지)
   - 장점: 동일 plane 중첩 방지.
   - 단점: READ/PROGRAM이 점유한 plane 창 때문에 즉시(now) 시작이 지연될 수 있음(요구사항 미달).

→ 결정: 옵션 A 채택. 필요 시 정책 플래그로 B를 보조 제공.

## 설계 및 변경 요약

- `ResourceManager`에서 베이스 단위 instant 판정 유틸 추가: `self._base_instant(base:str)->bool`
- `feasible_at` instant 분기:
  - t0 = `quantize(start_hint)` 로 고정.
  - 검사: `bus_ok` + `latch_ok` + `eval_rules('feasible')`만 적용.
  - 미검사/우회: `planescope_ok`, `single_multi_violation`, `legacy excl`.
  - OK 시 t0 반환, 실패 시 None.
- `reserve` instant 분기:
  - start = `quantize(txn.now_us)` 고정, end = start + dur.
  - 검사: `bus_ok` + `latch_ok` + `eval_rules('reserve')`만 적용.
  - 기록: `txn.bus_resv` 추가, `txn.st_ops` 추가(상태 타임라인 유지), 필요 시 래치 전이 반영.
  - 생략: `txn.plane_resv`/`txn.excl_die`/legacy excl 창 기록(plane/die 영향 차단).
  - 반환: 성공 Reservation(start,end), 실패 시 사유 반영.
- 정책 가드(옵션): `cfg['policies']['instant_bus_only_scope_none']`가 true이면 scope: NONE인 베이스만 instant 분기를 허용.

## 구현 단계(Tasks)

1) RM: instant 판정 유틸 추가
   - 파일: resourcemgr.py:1
   - 내용: `def _base_instant(self, base:str)->bool` 구현. `cfg['op_bases'][base]['instant_resv']` 조회.

2) RM: `feasible_at` instant 분기 추가
   - 파일: resourcemgr.py:338
   - 변경:
     - `base = self._op_base(op)` 직후 `if self._base_instant(base) and _instant_scope_ok(scope, base): ...` 분기 추가.
     - 내부에서 `t0 = quantize(start_hint)`, `end = quantize(t0 + duration)` 계산 후 `bus_ok`/`latch_ok`/`eval_rules`만 검사.

3) RM: `reserve` instant 분기 추가
   - 파일: resourcemgr.py:363
   - 변경:
     - `base = self._op_base(op)`/`dur` 산정 직후 instant 분기.
     - `start = quantize(txn.now_us)`/`end` 계산.
     - OK 시: `txn.bus_resv.append(...)`, 래치 전이/`txn.latch_locks`/`txn.st_ops`만 기록.
     - plane/die 창 기록 생략(`txn.plane_resv`/`txn.excl_die`/legacy excl 미사용).

4) 정책 가드(옵션)
   - 파일: resourcemgr.py:1
   - 내용: `def _instant_scope_ok(self, scope:Scope, base:str)->bool` 추가. `cfg['policies']['instant_bus_only_scope_none']`(기본 false)일 때 `scope==Scope.NONE`만 통과.

5) 단위/통합 테스트 추가
   - 파일: tests/test_rm_instant_bus_only.py
   - 케이스:
     - T1: READ(CORE_BUSY 진행 중) 상태에서 `RESET` 예약 시도: 버스 비어 있으면 now로 OK, plane/die 창 비기록.
     - T2: BUS 겹침: now 시점에 다른 버스 세그먼트가 겹치면 거절(`reason='bus'`).
     - T3: 래치 금지: 금지 규칙 활성 시 거절(`reason='latch'`).
     - T4: 정책 가드 on: scope!=NONE instant 베이스는 일반 경로로 처리.
   - 보조: 기존 RM 예약/배타 테스트 회귀 확인.

6) 문서/주석
   - 파일: docs/PRD_v2.md
   - 내용: "instant_resv는 버스 기준 즉시 예약 의미로 확장, RM에서 plane/die 배타 우회(버스/래치/룰만 검사)" 1–2줄 명시.

## 테스트 전략

- 단위(RM)
  - now+READ(CORE_BUSY) 상황 구성: `_st`에 READ 진행 세그먼트 삽입 또는 시뮬레이션 후 `feasible_at`/`reserve` 경로 테스트.
  - bus 충돌/비충돌/래치 충돌 분기별 결과 확인. `snapshot()`으로 `plane_resv`/`excl_die` 변화 없음 검증.

- 통합(Scheduler 경유)
  - admission window와 무관하게 RESET/ZQCAL이 now에 들어가는지 확인. `metrics.last_commit_bases`에 즉시 커밋 반영.

## 수용 기준(AC)

- AC1: READ `CORE_BUSY`/`DATA_OUT` 구간에서 `instant_resv=true` 베이스가 버스만 비어 있으면 now에 예약된다.
- AC2: instant 경로로 예약된 오퍼는 `plane_resv`/`excl_die`에 창을 남기지 않는다(스냅샷 검증).
- AC3: BUS 겹침/래치 금지는 그대로 적용되어 실패 사유가 정확히 보고된다.
- AC4: 기존 비‑instant 경로의 동작/테스트는 회귀가 없다.

## 리스크와 완화

- 타임라인 중첩: plane 타임라인에는 상태 세그먼트가 기록되지만 plane 예약창은 증가하지 않음 → 분석/후속 로직은 타임라인을 주로 참조하도록 문서화. 필요 시 특정 베이스의 타임라인 기록을 끌 수 있는 플래그 도입 검토.
- 정책 혼동: PRD/주석에 instant 의미 확장을 명시. 옵션 플래그로 scope 제한 가능.

## 롤백 계획

- resourcemgr.py의 instant 분기 블록 제거로 즉시 복구 가능. 테스트는 조건부 스킵 마커로 임시 비활성화.

## 참고 코드 포인터(파일:라인)

- resourcemgr.py:338
- resourcemgr.py:363
- scheduler.py:278
- proposer.py:1098
- research/2025-09-07_instant_resv_bus_only_at_now.md:1
