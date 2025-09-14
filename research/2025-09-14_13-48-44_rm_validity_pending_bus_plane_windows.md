---
date: 2025-09-14T13:48:44+0900
researcher: codex
git_commit: 2ce0ed0
branch: main
repository: nandseqgen_v2
topic: "RM validity 개선: 같은 시각 다중 예약 방지 — pending bus/plane 창 반영"
tags: [research, codebase, resourcemgr, scheduler, bus, plane, overlap, pending_windows]
status: complete
last_updated: 2025-09-14
last_updated_by: codex
last_updated_note: "버스 세그먼트 분산 + instant 혼합 최소 지연 재배치 복잡도/이득 분석 추가"
---

# 연구: RM validity 개선 — 같은 시각 다중 예약 방지

**Date**: 2025-09-14T13:48:44+0900
**Researcher**: codex
**Git Commit**: 2ce0ed0
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
ResourceManager의 validity가 제대로 동작하지 않아 동일한 시각에 여러 operation이 예약된다. 2025-09-14_13-14-52_rm_validity_same_time_ops.md의 분석을 바탕으로, 이를 근본적으로 개선할 방법은 무엇인가?

## 요약
- 원인: 버스/플레인 충돌 검사가 커밋된 창만 확인하고(txn 보류창 미반영), 즉시 예약(instant_resv) 경로는 플레인/다이 창 생성도 생략한다. 그 결과 같은 트랜잭션 내에서 선예약된 op의 보류(bus/plane) 창과 후속 op가 겹쳐도 검출되지 않아 동일 시각 예약이 통과한다.
- 증거: `_bus_ok`는 `self._bus_resv`만, `_planescope_ok`는 `self._plane_resv`만 검사한다. 다이 배타 창(single/multi)은 `pending`을 반영하여 동트랜잭션 충돌을 차단한다(상반되는 동작).
- 개선안: (A) 버스/플레인 검사에 `txn.*` 보류창을 포함, (B) instant 경로에서 최소한 버스 보류창과 직렬화를 강제, (C) 충돌 시 즉시 실패 대신 트랜잭션 내부에서 가능한 가장 이른 시각으로 재배치(선택적).

## 상세 발견

### 문제 포인트
- 버스 검사: `resourcemgr.py:248`의 `_bus_ok(...)`는 커밋된 `self._bus_resv`만 순회한다. 같은 트랜잭션 내 앞서 예약된 op가 추가한 `txn.bus_resv`와의 겹침은 보지 못한다.
- 플레인 창 검사: `resourcemgr.py:240`의 `_planescope_ok(...)`도 `self._plane_resv`만 본다. `txn.plane_resv`는 미반영이다.
- 반면 다이 배타(single/multi): `resourcemgr.py:307`/`381`/`389` 인근에서 `_single_multi_violation(..., pending=...)` 경로가 있어 `txn.excl_die` 보류창을 함께 확인한다.
- instant 경로: `resourcemgr.py:432` 코멘트대로 bus/latch/rules만 검사하고 plane/die 창은 만들지 않는다. 버스 창은 `txn.bus_resv`에만 추가된다(`resourcemgr.py:445`).

### 현상 재현 근거
- CSV에서 동일 시각 공존 예시: `out/operation_sequence_250914_0000001.csv:11`와 `out/operation_sequence_250914_0000001.csv:12` — `7000.11us` 시점에 `Block_Erase_SLC`와 `Read_Status_Enhanced_70h` 동시 기록.
- 스케줄러는 트랜잭션 내 직렬화를 위해 `txn.now_us`를 직전 예약의 `end_us`로 갱신한다(`scheduler.py:419`). 그럼에도 발생하는 이유는, instant와 non-instant가 같은 트랜잭션에서 조합될 때 `_bus_ok`/`_planescope_ok`가 보류창을 무시하기 때문(순서·구성에 따라 동일 시각 허용됨).

### 관련 코드 경로
- 예약/검증 진입점: `scheduler.py:341`(reserve 호출), `scheduler.py:425`(commit)
- RM 예약 로직: `resourcemgr.py:427`(reserve), `resourcemgr.py:389`(feasible_at)
- 버스 검사: `resourcemgr.py:248` (`self._bus_resv`만 확인)
- 플레인 검사: `resourcemgr.py:240` (`self._plane_resv`만 확인)
- instant 경로 창 추가: `resourcemgr.py:445`(txn.bus_resv에만 추가)
- 커밋: `resourcemgr.py:508` 이후 (txn.* → self.* 반영)
- SR instant 스펙: `config.yaml:499`/`config.yaml:502` (`SR`), `config.yaml:507`/`config.yaml:510` (`SR_ADD`)

## 개선안 비교

1) 버스/플레인 검사에 보류창 포함 (권장, 최소침습)
- 내용: `_bus_ok(op,start,pending_bus=None)`/`_planescope_ok(..., pending_plane=None)`로 확장하여 `self.*`와 `pending`을 함께 검사. `reserve(...)`에서 `txn` 보류창을 전달. instant/normal 공통 적용.
- 장점: 정확성 즉시 개선, 설계 일관성(다이 배타와 동일한 pending 포함 전략). 구현 범위 제한적.
- 단점: `_bus_ok`/`_planescope_ok` 시그니처 변경 또는 내부에서 선택적 인자 처리 필요. 성능은 보류창 길이에 비례(일반적으로 작음).
- 위험: 없음에 가까움(기존 커밋창 검사는 그대로 유지).

2) instant 경로 직렬화 강화 (간단한 가드)
- 내용: instant 예약 시 `start = max(txn.now_us, last_end_in(txn.bus_resv))`로 보정하고, `_bus_ok` 검사 시 `txn.bus_resv`도 함께 확인.
- 장점: 트랜잭션 내부에서 버스 중첩을 구조적으로 차단. 실패보다는 늦춤을 우선하여 배치 성공률 유지.
- 단점: last_end 기준 직렬화는 보수적이라, 실제로 겹치지 않는 버스 세그먼트가 있어도 대기할 수 있음.
- 위험: 경합이 많은 시나리오에서 SR 빈도가 감소(의도된 효과일 수 있음).

3) 충돌 시 동트랜잭션 내 재배치 (가장 친절하지만 복잡)
- 내용: `_earliest_non_conflicting_bus_time(start, op, pending_bus, committed_bus)`를 계산해 즉시 경로/일반 경로 모두 충돌 시 해당 시각으로 `start`를 이동시켜 계속 예약 시도.
- 장점: 실패 없이 가능한 한 빨리 밀어 넣음. 배치 성공률 향상.
- 단점: 구현 복잡도 증가(윈도우 탐색/스캔 필요), 성능 비용 약간 증가.
- 위험: 스케줄러의 순차화 정책과 상호작용 면밀 검토 필요.

권장 조합: (1)을 기본으로 적용하고, instant 경로에는 (2)를 함께 적용. (3)은 필요 시 추가 최적화로 고려.

## 제안하는 구체 변경안

- `_bus_ok(self, op, start, pending=None) -> bool`:
  - 로직: `wins = itertools.chain(self._bus_resv, (pending or []))` 후 중첩 검사.
  - 호출부: `reserve(...)`의 instant/normal 경로에서 `pending=txn.bus_resv` 전달.

- `_planescope_ok(self, die, scope, plane_set, start, end, pending=None) -> bool`:
  - 로직: 각 plane별로 `self._plane_resv[(die,p)] + (pending.get((die,p), []) if pending else [])`를 함께 검사.
  - 호출부: `reserve(...)` normal 경로에서 `pending=txn.plane_resv` 전달. instant 경로는 여전히 plane 창 생성/검사를 생략(설계 유지).

- instant 경로 직렬화(선택):
  - `start = max(quantize(txn.now_us), max((e for (_,e) in txn.bus_resv), default=0.0))`
  - 이후 `_bus_ok(..., pending=txn.bus_resv)`로 재확인.

- Config 가드 활성화(정책):
  - `config.yaml[policies.instant_bus_only_scope_none] = true` 활용 가능. 이미 기본 동작과 합치되나, 정책 명시로 회귀 방지.

## 테스트 전략
- 단위 테스트: 같은 트랜잭션에서 (ERASE → SR) 순서 및 (SR → ERASE) 순서 모두에 대해, 예약 시각이 겹치지 않음을 검증.
  - 케이스 A: ERASE(버스 ISSUE 0.4us) 예약 후 SR 예약 시, SR 시작 ≥ ERASE 버스 창 종료.
  - 케이스 B: SR 먼저 예약 후 ERASE 예약 시, ERASE 시작 ≥ SR 종료(스케줄러의 txn.now_us 직렬화도 보장).
- 회귀 테스트: 기존 동작(다이 배타, 래치, 규칙 평가) 비변화 검증. CSV 스키마/정렬 불변.
- E2E 확인: `out/operation_sequence_*.csv`에서 같은 시각 공존 사례가 사라짐을 수기 확인.

## 코드 참조
- `resourcemgr.py:240` — `_planescope_ok`: 커밋된 plane 창만 검사(보류창 미반영)
- `resourcemgr.py:248` — `_bus_ok`: 커밋된 bus 창만 검사(보류창 미반영)
- `resourcemgr.py:432` — reserve instant 경로: plane/die 창 생략, bus만 `txn.bus_resv`에 추가
- `resourcemgr.py:483` — normal 경로: `txn.plane_resv`에 plane 창 추가
- `resourcemgr.py:485` — normal 경로: bus 세그먼트를 `txn.bus_resv`에 추가
- `resourcemgr.py:508` — commit: `txn.*` → `self.*` 반영
- `scheduler.py:419` — 트랜잭션 직렬화: `txn.now_us = quantize(r.end_us)`
- `config.yaml:499`/`config.yaml:502` — `SR` instant_resv
- `config.yaml:507`/`config.yaml:510` — `SR_ADD` instant_resv

## 아키텍처 인사이트
- 동일한 예약 축(plane/bus/excl)에서 커밋/보류 일관성 유지가 중요하다. 현재 다이 배타는 보류창을 반영하지만, plane/bus는 반영하지 않아 일관성이 깨진다.
- instant 경로는 빠른 예약을 위해 설계되었지만, 그럴수록 같은 트랜잭션 내부 상호배제를 강화해야 한다.

## 역사적 맥락
- `research/2025-09-14_13-14-52_rm_validity_same_time_ops.md` — 동일 시각 다중 예약 원인 분석과 관측 예시.

## 관련 연구
- `research/2025-09-08_17-05-45_dout_same_celltype_inherit_across_intervening_ops.md`
- `plan/scheduler_event_hook_phase_hook_guard.md`

## 미해결 질문
- instant 경로 재배치(옵션 3)를 도입할 때, 제안 배치 내 순서 안정성과 성능 영향의 균형점은 어디인가? ->(검토완료) 옵션 3은 우선 배제.
- 버스 세그먼트가 분산된 op(예: ISSUE, DOUT 모두 bus)와 instant op의 혼합 시, 최소 지연 재배치 알고리즘의 복잡도/이득은 어느 정도인가? -> (검토완료) 재배치는 불허.

