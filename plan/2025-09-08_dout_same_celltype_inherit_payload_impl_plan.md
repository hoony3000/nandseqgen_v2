---
title: "구현 계획: DOUT same_celltype 상속을 payload에 반영"
date: 2025-09-08
based_on: research/2025-09-08_16-07-44_dout_same_celltype_inherit_payload.md
status: draft
owners: ["Codex"]
---

# Problem 1-Pager

- 배경: PRD 3.1(Operation Sequence)에 따라 `payload` 스키마는 `config.yaml[payload_by_op_base]`로 정의된다. DOUT/DOUT4K payload에는 `celltype`이 포함된다.
- 문제: 현재 `export_operation_sequence`는 `celltype`을 항상 `cfg.op_names[op_name].celltype`에서만 가져온다. DOUT 계열의 op_name은 `celltype: NONE`이라, READ 계열에서 상속된 셀 타입이 최종 payload에 반영되지 않는다.
- 목표
  - G1: DOUT/DOUT4K가 직전 READ 계열에서 `same_celltype` 상속 조건을 만족할 경우, 해당 `celltype`을 payload에 반영한다.
  - G2: 상속 조건이 없거나 직전 참조가 불가능한 경우, 기존 동작을 유지한다(회귀 없음).
  - G3: 변경 범위를 `main.py` 내 exporter로 최소화하고 결정성/성능을 유지한다.
- 비목표
  - proposer/scheduler의 구조적 인터페이스 변경(메타데이터 전파) — 별도 대안으로 검토.
  - CFG에 DOUT 변형을 celltype별로 세분화해 추가하는 방식.
- 제약
  - 함수 ≤ 50 LOC 유지(핵심 로직은 헬퍼로 분리 가능).
  - 파일 ≤ 300 LOC 유지, 복잡도 ≤ 10.
  - 입력 검증 및 JSON 인코딩 정책(PRD 3.1.1) 준수.

# 변경 개요(Where & What)

- main.py
  - `export_operation_sequence(...)` 내 `celltype` 결정 로직 보강.
  - DOUT/DOUT4K 이고 payload 필드에 `celltype`이 포함될 때, (die,plane)별 직전 READ 계열 op의 `celltype`을 상속하여 payload에 기록(조건: 직전 base의 `sequence.inherit`에 DOUT/DOUT4K로 `same_celltype` 명시된 경우).
- tests/
  - 회귀 테스트 추가: READ→DOUT, READ4K→DOUT4K 체인에서 payload `celltype`이 상속되는지 검증.
- docs/PRD_v2.md (선택)
  - 3.1 절에 "상속된 힌트는 exporter에서 복원 가능" 노트 1줄.

# 상세 설계

## A) Exporter 보강(권장, 최소침습)

1) (die,plane) 타임라인 인덱스 구축
   - 입력 `rows` 전체에서 키 `(die,plane)`별로 `start_us` 기준 정렬된 리스트를 만든다: `by_dp[(d,p)] -> [(t, base, name)]`.
   - 메모리/시간: `rows` 크기 선형, 각 리스트는 이미 정렬된 `rows` 순회 중 `append` 후 최종 sort 한 번.

2) 상속 가능 여부 판정
   - 현재 그룹의 `base`가 `DOUT` 또는 `DOUT4K`인지 확인.
   - payload 필드(`payload_by_op_base[base]`)에 `celltype`이 포함되어 있는지 확인.
   - 각 항목 r에 대해 `t_prev < t_ref`를 만족하는 직전 항목을 찾는다.
     - `t_ref`: 그룹의 대표 시각 `t0`(=min start_us) 또는 각 항목의 `start_us`(더 보수적). 기본은 `r.start_us` 사용.
     - 이웃 탐색: `bisect`로 O(logN). 구현 난이도를 낮추기 위해 단순 역순 선형 탐색도 허용(일반적으로 리스트가 짧음).

3) 규칙 확인 및 `celltype` 결정
   - `prev_base = row_prev.op_base`, `prev_name = row_prev.op_name`.
   - CFG 조회: `cfg['op_bases'][prev_base]['sequence']['inherit']`에서 `DOUT` 또는 `DOUT4K` 키를 찾아 조건 배열에 `same_celltype` 포함 여부를 확인.
   - 포함 시: `cell = cfg['op_names'][prev_name]['celltype']`로 덮어씀.
   - 미포함/조회 불가/직전 없음: 기존 `cell = cfg['op_names'][name]['celltype']` 유지.

4) 유틸리티/가드
   - NULL/"None" 문자열 케이스를 모두 정규화.
   - 에러는 억제하고 안전 폴백(None 유지)로 진행. 로깅은 디버그 1줄 수준.

## B) Proposer 메타데이터 전파(대안)

- `ProposedOp`에 `celltype_hint: Optional[str]` 추가 후 전 파이프라인 전파.
- Exporter는 힌트가 존재하면 우선 사용.
- 장점: 의미론적으로 명확. 단점: 변경 범위 확대/테스트 영향 큼.

# 구현 단계(Tasks)

1) 헬퍼 준비: `main.py`에 (die,plane)별 타임라인 인덱싱/직전 항목 조회 헬퍼 추가.
2) 로직 삽입: `export_operation_sequence` 내 DOUT/DOUT4K 케이스에서 상속 규칙 검사 및 `celltype` 치환.
3) 문서/주석: 코드에 가드/폴백 정책과 근거(상속 규칙 확인)를 1~2문장으로 명시.
4) 테스트 추가: READ→DOUT, READ4K→DOUT4K, PLANE_READ→DOUT(+interleave) 시나리오 커버.
5) 샘플 실행 점검: `out/operation_sequence_*.csv`에서 상속된 `celltype`이 표기되는지 확인.

# 테스트 전략

- 단위 테스트
  - T1: 단일 plane — READ(TLC) → DOUT. payload `celltype` == TLC.
  - T2: 4K 경로 — READ4K(SLC) → DOUT4K. payload `celltype` == SLC.
  - T3: 규칙 미존재 — 직전 base의 inherit에 `same_celltype`이 없으면 기존 값 유지(None 또는 op_name의 celltype).
  - T4: 직전 없음 — 첫 DOUT에 대해 폴백 동작 확인.
- 통합 테스트
  - I1: 멀티-플레인 interleave — 각 plane 별로 올바른 직전 READ 계열의 `celltype`을 상속.
  - I2: CSV/JSON 라운드 트립 — PRD 3.1.1 규칙에 부합.

# 대안 비교(요약)

- A) Exporter 보강(선택)
  - 장점: 변경 범위 최소(main.py), 요구 충족, 빠른 롤아웃.
  - 단점/위험: 타임라인 기반 유추(약결합). 잘못된 시퀀스 입력 시 오탐 가능.
  - 완화: inherit 규칙 확인 가드로 오탐 감소.

- B) Proposer 전파
  - 장점: 의미론적 정확, 확장성.
  - 단점: 인터페이스 변경/영향 범위 큼, 작업량 증가.

# 수용 기준(AC)

- AC1: DOUT/DOUT4K payload에 상속된 `celltype`이 반영된다(동일 (die,plane)의 직전 READ 계열 기준, 규칙 충족 시에만).
- AC2: 규칙이 없거나 직전 없음 등 예외 상황에서 기존 동작과 동일하다(회귀 없음).
- AC3: PRD 3.1/3.1.1의 출력 스키마/인코딩을 유지한다.
- AC4: 신규/기존 테스트 모두 결정적으로 통과한다.

# 위험과 완화

- 멀티-플레인 interleave에서 잘못된 이전 참조 → 같은 (die,plane) 기준으로 제한하고, 그룹 내 각 항목의 `start_us`를 기준으로 직전 검색.
- 비정상 입력(누락/정렬 불량) → 안전 폴백(None 유지), 디버그 로그로 추적.
- 성능: 대규모 `rows`에서 직전 탐색 비용 → by-plane 인덱스와 이진 탐색으로 제한.

# 롤아웃

1) 기능 구현 → 단위 테스트 추가/통과.
2) 통합 테스트 작성/통과(멀티-플레인/라운드 트립).
3) 소규모 샘플 실행으로 `out/operation_sequence_*.csv` 수기 확인.
4) 문서 보강 후 머지.

# 참고(파일/라인)

- PRD: `docs/PRD_v2.md:24`(3.1 Operation Sequence), `docs/PRD_v2.md:51`(3.1.1 인코딩)
- config: `config.yaml:726`(payload_by_op_base), `config.yaml:758`(DOUT), `config.yaml:759`(DOUT4K), `config.yaml:255,296,341,361,407`(inherit same_celltype)
- 코드: `main.py:413`(export_operation_sequence 시작), `main.py:431-447`(payload 구성부)
