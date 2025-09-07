---
researcher: Codex-CLI
git_commit: 306b495
branch: main
repository: nandseqgen_v2
topic: "instant_resv bus-only immediate reservation at now"
tags: [research, codebase, scheduler, resourcemgr, proposer]
status: complete
last_updated: 2025-09-07
last_updated_by: Codex-CLI
---

# 연구: instant_resv bus-only 즉시 예약(now)

**Date**: 2025-09-07T18:21:24+09:00
**Researcher**: Codex-CLI
**Git Commit**: 306b495
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
instant_resv=true 인 동작이 CORE_BUSY/DATA_OUT 등의 state 에서 예약이 안되고 있어. now 시각에서 bus 중첩 조건만 통과하면 바로 예약이 되는 것이 의도였어. 이를 어떻게 개선하면 좋을지 research 해줘.

## 요약
- 현재 `instant_resv` 는 제안 창(admission window) 우회에만 쓰이며, 실제 예약 단계에서는 plane-scope/다중성(die 단일/다중) 배제 규칙이 그대로 적용됨.
- 결과적으로 READ 계열이 `CORE_BUSY`/`DATA_OUT` 상태일 때 버스가 비어 있어도 die‑level 겹침 차단과 plane 예약 창 때문에 `instant` 기반의 버스 전용(op_base가 `scope: NONE` 또는 버스 세그먼트만 있는) 오퍼레이션이 즉시(now) 예약되지 않음.
- 개선: ResourceManager의 `feasible_at`/`reserve` 에서 `instant_resv` 베이스에 한해 시작시각을 `now` 로 고정하고, 검증을 "버스 겹침 + (선택) 래치/정책"만 적용하도록 스코프/다중성 체크를 우회. plane/die 배타 창 기록도 생략하여 후속 스케줄에 영향 주지 않도록 함.

## 상세 발견

### instant_resv의 현재 의미 (창 우회만)
- `scheduler.py:278` `instant = _is_instant_base(...)` 후 첫 오퍼만 admission window 검사 생략. 예약 로직은 동일.
- `proposer.py:1098-1099` "Admission window check unless instant reservation" — 창 체크만 우회하고, earliest feasible 계산은 `res_view.feasible_at(...)` 결과를 그대로 사용.

### 예약 차단의 실제 원인 (die/plane 겹침)
- `resourcemgr.py:338-355` `feasible_at`: `t0 = max(start_hint, earliest_planescope)` 이후
  - `:344` plane 예약 창 겹침 검사
  - `:346` 버스 겹침 검사
  - `:349-355` die 단일/다중 겹침 차단, legacy exclusion, latch 검증
- `resourcemgr.py:363-382` `reserve` 도 동일 순서로 검증하며, 성공 시 plane/die 배타 창을 기록해 `_avail` 을 뒤로 민다.
- 따라서 now 시각 버스가 비어 있어도 plane/die 배타 규칙이 선행되어 예약 실패(`reserve_fail:exclusion_multi` 또는 `planescope`) 발생 가능.

### 버스만 통과하면 즉시 예약 의도를 막는 추가 요인
- `scheduler.py:311-317` 동일 배치 내 순차 보장을 위해 `txn.now_us` 를 직전 예약 종료로 민다(READ→DOUT 직렬화). 이는 배치 내부에만 영향.
- READ 계열의 `CORE_BUSY`/`DATA_OUT` 구간은 `bus: false` 이므로 버스는 놀고 있어도, die/plane 창이 이미 점유되어 있음.

## 개선안 제안

- 옵션 A — instant를 "버스 기준 즉시 예약"으로 확장 (권장)
  - `ResourceManager` 내에서 op.base가 `instant_resv=true` 인 경우:
    - `feasible_at`: `t0 = quantize(start_hint)` 로 설정, `planescope_ok`/`single_multi_violation` 스킵, `bus_ok` + `latch_ok` + 규칙만 적용.
    - `reserve`: `start = quantize(txn.now_us)`, `planescope_ok`/`single_multi_violation` 스킵.
    - 커밋 영향 축소: plane 예약창 및 die 배타창 기록을 생략하여 `_avail`/겹침 창이 불필요하게 뒤로 밀리지 않게 함. 버스 예약(`txn.bus_resv`)과 상태 타임라인만 기록.
  - 장점: READ `CORE_BUSY`/`DATA_OUT` 중에도 SR/RESET/ODT 등 버스 전용 오퍼가 즉시 들어감. 부작용 최소화(plane/die 가용성 왜곡 없음).
  - 단점: plane 타임라인이 겹칠 수 있음(의도된 모델이라면 OK). 검증 규칙이 충분히 엄격하지 않다면 예기치 않은 동시성 조합 위험.

- 옵션 B — 범위를 더 좁혀서 적용
  - instant 적용 대상을 `scope: NONE` 또는 `affect_state: false` 인 베이스로 제한.
  - 장점: 데이터 경로(READ/PROGRAM)에는 영향 최소화.
  - 단점: 향후 확장성 제한. 설정으로 제어 필요.

- 옵션 C — 다중성만 우회, plane 창은 유지
  - `single×multi`/`multi×multi` 배타만 우회하고 plane 창은 유지.
  - 장점: 동일 plane 의 중첩은 방지.
  - 단점: READ/PROGRAM 이 점유한 plane 창 때문에 여전히 now 시작이 지연될 수 있음(요구사항 미달 가능).

## 코드 반영 포인트
- `resourcemgr.py:338` `feasible_at`:
  - instant 시 `t0 = quantize(start_hint)` 로 설정, `planescope_ok`/`_single_multi_violation` 분기 우회.
- `resourcemgr.py:363` `reserve`:
  - instant 시 `start = quantize(txn.now_us)` 로 설정, `planescope_ok`/`_single_multi_violation` 우회.
  - 커밋 전 단계에서 plane/die 배타창 기록 생략(instant 분기).
- instant 판정 util 추가:
  - `resourcemgr.py` 에서 `base -> cfg['op_bases'][base]['instant_resv']` 확인 함수 도입(스케줄러/프로포저와 의미 일치).

## 코드 참조
- `scheduler.py:278` - instant 베이스 판정으로 admission window만 우회.
- `scheduler.py:286` - 실제 예약은 ResourceManager.reserve 로 위임.
- `proposer.py:1098` - feasible_at 결과에 대해 instant인 경우 창 검사만 스킵.
- `resourcemgr.py:341` - earliest_planescope 적용 지점(now 즉시 시작을 막는 원인).
- `resourcemgr.py:344` - plane 창 겹침 검사.
- `resourcemgr.py:350` - die 단일/다중 겹침 차단.
- `resourcemgr.py:367` - reserve 시작시각 계산(earliest_planescope 포함).

## 아키텍처 인사이트
- 현재 instant 의미가 "창 우회"로만 한정되어 있어, 실제 동시성 모델(버스/코어 분리) 요구를 만족하지 못함.
- 동시성 제어는 ResourceManager 단에서 일관되게 수행되므로, instant semantics 확장은 이 레이어에서의 분기가 가장 자연스러움.
- plane/die 배타창을 기록하지 않도록 해야 후속 스케줄링에 부작용이 없다.

## 관련 연구
- `research/2025-09-07_07-01-11_read_not_scheduled_after_erase_program.md` — READ 예약 지연 이슈 분석.
- `research/2025-09-07_16-17-05_sequence_nonfirst_start_time.md` — 배치 내 연쇄 예약 시각 통제.

## 미해결 질문
- instant 를 `scope: NONE` 등으로 제한할지, 베이스별 설정으로 완전 위임할지? -> (검토완료) 제한 없음.
- latch/ODT/cache/suspend 규칙을 instant에도 동일 적용할지 추가 완화가 필요한지? -> (검토완료) 동일 적용 필요. SUSPEND 의 경우 latch_state 를 바꾸기 때문.
- DOUT/READ4K 등 READ 연쇄 내 보조 오퍼에도 instant를 확대할지 정책 결정 필요. (검토완료) 확대 필요.
