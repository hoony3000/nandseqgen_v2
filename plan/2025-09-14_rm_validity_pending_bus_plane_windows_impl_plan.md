---
title: RM validity 개선 — 같은 시각 다중 예약 방지 (pending bus/plane 창 반영)
date: 2025-09-14
author: codex
status: draft
source: research/2025-09-14_13-48-44_rm_validity_pending_bus_plane_windows.md
related:
  - research/2025-09-14_13-14-52_rm_validity_same_time_ops.md
  - plan/2025-09-07_instant_resv_bus_only_now_impl_plan.md
---

# Problem 1‑Pager

- 배경: ResourceManager(RM)에서 같은 시각에 여러 operation이 예약되는 문제가 재현됨. 원인은 버스/플레인 충돌 검사가 커밋된 창만 보고 트랜잭션 보류창(pending)을 무시하기 때문. instant 경로는 plane/die 창 생성도 생략하여, 같은 트랜잭션 내 선예약과 후속예약이 겹쳐도 검출되지 않는 케이스 존재.
- 문제: `_bus_ok`/`_planescope_ok`가 `txn.*` 보류창을 고려하지 않아 동일 시각 예약 통과. instant 경로는 버스 직렬화 보장이 약함.
- 목표: (1) 버스/플레인 검사에 pending 창을 포함, (2) instant 경로에 최소한의 버스 직렬화 가드 추가. (3) 재배치 알고리즘(충돌 시 start 이동)은 이번 범위에서 제외.
- 비목표: 스케줄러의 전반 정책 변경, die 배타 로직 변경(현행 유지), instant 경로의 plane 창 생성 도입(현행 유지), 대규모 리팩터링.
- 제약: 함수 ≤ 50 LOC, 파라미터 ≤ 5, 순환복잡도 ≤ 10. 기존 퍼포먼스 가드 유지(보류창은 소수 개 전제). 외부 API/CSV 스키마 불변.

# 대안 비교

1) `_bus_ok`/`_planescope_ok`에 pending 포함(권장)
- 장점: 정확성 즉시 개선, 다이 배타와 전략 일관성. 변경 범위 제한적.
- 단점: 시그니처/내부 분기 추가. 미미한 성능 비용.
- 위험: 낮음(기존 커밋 창 검사는 유지).

2) instant 경로 버스 직렬화 최소 가드 추가(권장)
- 장점: 동일 트랜잭션 내 버스 중첩 구조적으로 차단. 실패 대신 지연.
- 단점: 보수적 직렬화로 불필요 대기가 소폭 증가 가능.
- 위험: SR 빈도/동시성 감소(의도된 효과일 수 있음).

3) 충돌 시 동트랜잭션 재배치(이번 제외)
- 장점: 실패 없이 가능한 한 빠른 시각으로 이동.
- 단점: 구현/검증 복잡, 성능 비용. 스케줄러 정책과 상호작용 고려 필요.
- 위험: 중간 복잡도 확대. 현재는 제외.

선택: 1 + 2를 적용, 3은 보류.

# 변경 범위(High‑Level)

- `resourcemgr.py`
  - `_bus_ok(self, op, start, pending=None) -> bool`: pending 버스 창 포함 검사.
  - `_planescope_ok(self, die, scope, plane_set, start, end, pending=None) -> bool`: plane 보류창 포함.
  - `reserve(...)`: instant/normal 경로에서 위 함수 호출 시 `pending=txn.bus_resv`/`pending=txn.plane_resv` 전달. instant 경로는 plane/die 창 생성은 생략 유지.
  - instant 경로 시작시각 보정: `start = max(quantize(txn.now_us), last_end(txn.bus_resv))` 후 `_bus_ok(..., pending=txn.bus_resv)` 재확인.
- `tests/` 단위/회귀 테스트 추가.
- `config.yaml`(선택): 정책 키 주석 명시로 회귀 방지(기능변경 없음).

# 구현 계획(Incremental)

1. 버스 검사 pending 지원 추가
- `_bus_ok` 시그니처에 `pending: Optional[List[Tuple[float,float,int]]] = None` 추가(타입은 코드 실재 구조에 맞춤).
- 내부에서 `wins = chain(self._bus_resv, pending or [])`로 중첩검사.
- 기존 호출부는 변경 없이 동작(기본값 None).

2. 플레인 검사 pending 지원 추가
- `_planescope_ok(..., pending: Optional[Dict[(die,plane)], List[(s,e)]]] = None)` 추가.
- 검사 시 `committed + pending[(die, p)]`를 합쳐 중첩검사.
- normal 경로의 호출부에서 `pending=txn.plane_resv` 전달.

3. `reserve(...)` 호출 경로 보강
- normal 경로: `_planescope_ok(..., pending=txn.plane_resv)` 및 `_bus_ok(..., pending=txn.bus_resv)` 사용.
- instant 경로: plane/die 창 생성은 생략 유지하되, `_bus_ok(..., pending=txn.bus_resv)`를 사용.

4. instant 경로 최소 직렬화 가드
- `start = max(quantize(txn.now_us), max((e for (_, e) in txn.bus_resv), default=0.0))` 적용.
- 이후 `_bus_ok(..., pending=txn.bus_resv)` 통과 시에만 bus 세그먼트 보류 등록.

5. 주석/도큐먼트 반영
- 함수/경로 docstring에 pending 포함 근거를 명시(다이 배타와의 일관성 강조).
- `config.yaml` 관련 항목에 정책 코멘트(기능 불변).

6. 테스트 추가(우선순위: 단위 → 회귀)
- 단위: 같은 트랜잭션에서 (ERASE → SR), (SR → ERASE) 모두 시간 겹침이 없음을 검증.
- 회귀: 다이 배타/래치/규칙 경로가 기존과 동일하게 동작함을 확인.
- E2E(수기): 기존 CSV 동일 시각 공존 사례가 사라짐을 확인.

# 상세 설계/의사코드

// 버스 검사
def _bus_ok(self, op, start_us, pending=None):
    # pending: List[(s, e, bus_id?)] 또는 구현에 맞게
    wins = itertools.chain(self._bus_resv, pending or [])
    for (s, e, bus) in wins:
        if overlap(start_us, start_us + op.bus_dur, s, e):
            return False
    return True

// 플레인 검사
def _planescope_ok(self, die, scope, plane_set, start_us, end_us, pending=None):
    for p in plane_set:
        committed = self._plane_resv.get((die, p), [])
        pend = (pending.get((die, p), []) if pending else [])
        for (s, e) in itertools.chain(committed, pend):
            if overlap(start_us, end_us, s, e):
                return False
    return True

// instant 경로 일부
start = max(quantize(txn.now_us), max((e for (_, e) in txn.bus_resv), default=0.0))
if not self._bus_ok(op, start, pending=txn.bus_resv):
    return Fail("BUS_CONFLICT")
txn.bus_resv.append((start, start + op.bus_dur, op.bus_id))

# 테스트 전략

- 케이스 A: ERASE(bus ISSUE 0.4us) → SR(instant)
  - 기대: SR 시작 시각 ≥ ERASE ISSUE 종료 시각
- 케이스 B: SR(instant) → ERASE(normal)
  - 기대: ERASE 시작 시각 ≥ SR 종료 시각(스케줄러 직렬화 + pending 검사로 보장)
- 경계: 동일 트랜잭션 내 여러 SR 연속, 분산된 버스 세그먼트(ISSUE/DOUT) 혼재 시에도 겹침 없음.

# 검증/롤백 전략

- 메트릭: out/operation_sequence_*.csv에서 동일 타임스탬프 중복 건수(before/after).
- 실패 시 롤백: `_bus_ok`/`_planescope_ok`의 pending 분기만 제거하여 즉시 원상복구 가능.
- 가드: 변경은 기본값 None 경로로 backward compatible. 문제가 있으면 호출부에서 pending 전달을 일시 중단 가능.

# 영향도 및 가정

- 영향도: `_bus_ok`/`_planescope_ok` 호출부 및 instant 경로 일부에 한정. scheduler는 비변경.
- 성능: 트랜잭션 보류창 길이에 선형. 일반적으로 매우 작아 영향 미미.
- 가정: `txn.bus_resv`/`txn.plane_resv` 구조가 연구 문서와 일치. 동일 트랜잭션 내 now_us 직렬화는 기존 로직 유지.

# 작업 체크리스트

- [ ] `_bus_ok`에 pending 추가, 호출부 연결
- [ ] `_planescope_ok`에 pending 추가, 호출부 연결
- [ ] instant 경로 start 보정 + `_bus_ok(..., pending=...)` 적용
- [ ] 주석/도큐먼트 업데이트
- [ ] 테스트 2~3건 추가 및 통과
- [ ] CSV 수기 점검(동일 시각 없음)

# 변경 파일(예상)

- resourcemgr.py
- tests/test_resourcemgr_pending_windows.py (신규)
- config.yaml (주석 보강, 선택)

# 타임라인(예상)

- D0: 구현(half-day), 단위 테스트 작성/통과
- D0: 간단한 시뮬레이션 실행 후 CSV 스팟 체크
- D1: 리뷰/정리 및 머지

# 참고 링크

- research/2025-09-14_13-48-44_rm_validity_pending_bus_plane_windows.md
- out/operation_sequence_250914_0000001.csv

