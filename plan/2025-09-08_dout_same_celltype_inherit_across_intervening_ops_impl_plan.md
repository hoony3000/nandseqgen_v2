---
title: "구현 계획: DOUT same_celltype 상속이 SR/Delay 개입 시에도 유지"
date: 2025-09-08
based_on: research/2025-09-08_17-05-45_dout_same_celltype_inherit_across_intervening_ops.md
status: draft
owners: ["Codex"]
---

# Problem 1‑Pager

- 배경: PRD 3.1(Operation Sequence)와 `config.yaml[payload_by_op_base]`에 따라 DOUT/DOUT4K payload에는 `celltype`이 포함된다. 기존 구현은 직전 op 하나만 확인해 READ 계열의 `same_celltype` 상속을 복원한다.
- 문제: READ와 DOUT 사이에 `SR`/`SR_ADD`/`Delay(NOP)` 등 비상속성 op가 개입되면, 직전 op는 상속 규칙이 없어 `celltype`이 None/"NONE"으로 출력된다.
- 목표
  - G1: DOUT/DOUT4K가 같은 (die,plane) 타임라인에서 역방향으로 상속 자격이 있는 op를 찾아 `same_celltype`을 견고하게 상속한다.
  - G2: `inherit` 조건 중 `same_page` 등이 함께 명시된 경우 해당 제약을 준수한다(불일치 시 상속 보류).
  - G3: 변경 범위를 exporter(`main.py`)로 한정하여 회귀/리스크를 최소화한다.
- 비목표
  - proposer/scheduler의 메타데이터 전파 구조 변경(별도 대안으로 보류).
  - CFG를 celltype별 DOUT 변형으로 분기하는 방법.
- 제약
  - 함수 ≤ 50 LOC, 파일 ≤ 300 LOC, 복잡도 ≤ 10 유지(핵심 로직은 헬퍼로 분리).
  - JSON 인코딩 정책(PRD 3.1.1) 준수, 입력 정규화 및 예외 안전.

# Root Cause (간단)

- `export_operation_sequence`는 (die,plane)별로 "직전 하나"만 `_prev_of(...)`로 조회해 상속 판정한다. READ와 DOUT 사이에 `SR`/`Delay(NOP)`가 끼면 상속 규칙이 없어 `celltype` 복원이 실패한다.

# Approach (권장: 옵션 A — Exporter 역탐색)

- 같은 (die,plane) 타임라인에서 DOUT 시각 직전으로 역방향 탐색하며, CFG의 `sequence.inherit`에 현재 타깃 base(`DOUT`/`DOUT4K`)가 존재하고 조건 배열에 `same_celltype`이 포함된 첫 op를 찾는다. `SR`/`NOP` 등은 자동으로 건너뜀.

## 변경 사양

- main.py — `export_operation_sequence` 내부
  - by‑dp 인덱스 확장: `by_dp[(d,p)] -> [(t, base, name, block, page)]`로 저장.
  - `_prev_of(d,p,t)`를 일반화한 `_prev_match(d,p,t,target_base, cur_block, cur_page)` 헬퍼 추가.
    - 이진 탐색으로 시작 인덱스(첫 t_ref 미만) 계산 후 역순 선형 탐색.
    - 각 후보의 `inherit[target_base]` 조건에 `same_celltype`이 있는지 확인.
    - 조건 배열에 `same_page`가 있으면 `prev_page == cur_page`를, 필요 시 `same_block`(향후)도 검사.
    - 일치 시 `prev_name`에서 `celltype` 획득하여 반환. 미일치 시 계속 탐색.
    - 탐색 상한: `MAX_BACKTRACK = 64`(상수)로 제한해 병목 방지.
  - DOUT/DOUT4K 처리 분기에서 `_prev_match(...)`로 상속 후보를 찾고, 성공 시 `celltype` 치환. 실패 시 기존 기본값 유지.
  - NULL/"None"/"NONE" 정규화 가드 유지.

## 영향 범위 / 호출 경로

- 호출: `main.py: export_operation_sequence` 내부 헬퍼와 payload 조립부만 수정.
- 참조: `config.yaml[op_bases.*.sequence.inherit]`, `config.yaml[op_names.*.celltype]`, `payload_by_op_base`.
- 기타 파일/테스트에 대한 인터페이스 변경 없음.

## 대안 비교

- 옵션 A(선택): Exporter 역탐색
  - 장점: 변경 범위 최소, SR/Delay 개입 시나리오 전반 해결, CFG 기반 일반화로 하드코딩 불필요.
  - 단점: 타임라인 유추(약결합). 비정상 로그에도 적용될 수 있음.
  - 위험: 역탐색 비용 증가. 완화: 이진 탐색 시작 + 상한(backtrack cap) 도입.

- 옵션 B: 메타데이터 전파(`celltype_hint`)
  - 장점: 의미론적 정확성, 다른 소비자도 재사용 가능.
  - 단점: dataclass/시그니처 변경 많음, 테스트 영향 큼, 릴리스 리스크.

- 옵션 C: CFG 세분화(DOUT_TLC 등)
  - 장점: exporter 변경 불필요.
  - 단점: CFG 폭발, 유지보수 악화. 비권장.

# Tasks (작업 순서)

1) 인덱스 확장: `by_dp`에 `(block,page)` 포함하도록 변경.
2) 헬퍼 추가: `_prev_match(d,p,t,target_base,cur_block,cur_page)` 구현.
3) 상속 로직 교체: DOUT/DOUT4K 분기에서 `_prev_of` → `_prev_match` 사용, `same_page` 제약 반영.
4) 가드/정규화: None/문자열 None/대소문자 케이스 통일.
5) 단위 테스트 추가: SR/Delay 개입, 4K 경로, 제약 불일치(미상속) 케이스.
6) 통합 테스트: 멀티‑플레인 interleave, CSV/JSON 라운드트립 확인.
7) 문서 업데이트(선택): PRD 3.1에 상속 복원 규칙 노트 1줄.

# 테스트 계획 (PRD 규칙 준수)

- 단위
  - T1: READ(TLC) → SR → Delay → DOUT → payload.celltype == TLC
  - T2: READ4K(SLC) → SR_ADD → DOUT4K → payload.celltype == SLC
  - T3: 상속 규칙 없음(prev_base가 DOUT 상속 미지원) → 기존 값 유지(None)
  - T4: same_page 제약 위반(prev.page != cur.page) → 상속 보류
- 통합
  - I1: 멀티‑플레인 READ interleave 후 각 plane의 DOUT 상속 정상
  - I2: CSV/JSON 라운드 트립 무결성(PRD 3.1.1)

# 구현 메모

- `_inherit_map_for(prev_base)` 재사용: 반환된 조건 배열에서 `same_celltype` 필수, `same_page` 등 추가 제약은 가능한 범위에서 검사.
- by‑dp 요소를 튜플로 유지하되 가독성을 위해 `NamedTuple` 또는 간단 dict unpack 사용(50 LOC 제한 고려).
- 성능: 평균 수십 항목 역탐색. 상한과 조기 종료로 제한. 대용량에서도 O(N log N + K) 수준.
- 로깅: 예외/조회 실패는 억제하고 폴백. 필요 시 디버그용 주석만.

# Affected Files

- main.py — `export_operation_sequence`(by‑dp 작성부, prev‑lookup 헬퍼, DOUT 분기)
- tests/ — `test_exporter_dout_celltype_inherit_across_ops.py`(신규)
- (참고) config.yaml — READ/READ4K/PLANE_READ/COPYBACK_READ의 `inherit` 규칙 활용

# 리스크와 완화

- 잘못된 이전 참조 → 같은 (die,plane)로 제한하고 `inherit` 규칙 유무로만 매칭, `same_page` 제약 준수.
- 성능 저하 → 바이너리 서치 + backtrack cap.
- 회귀 위험 → 기존 테스트 유지 + 신규 회귀 테스트 추가.

# 수용 기준(AC)

- AC1: READ/SR/Delay 개입 후 DOUT/DOUT4K의 payload.celltype이 올바르게 상속된다.
- AC2: `inherit` 제약 불만족/후보 없음 시 기존 동작과 동일(None 유지).
- AC3: PRD 3.1/3.1.1 스키마 및 인코딩 준수.
- AC4: 모든 관련 테스트 결정적으로 통과.

# 참고(파일/라인)

- 연구: `research/2025-09-08_17-05-45_dout_same_celltype_inherit_across_intervening_ops.md:1`
- 코드: `main.py:420`(by‑dp 구축), `main.py:486`(기본 celltype), `main.py:497`(DOUT 분기), `main.py:499`(`_prev_of`), `main.py:502`(inherit 판정)
- CFG: `config.yaml:236,288,332,353,397`(READ family inherit), `config.yaml:726+`(payload_by_op_base), `config.yaml:500+`(SR/SR_ADD instant_resv), `config.yaml:3028+`(NOP)

