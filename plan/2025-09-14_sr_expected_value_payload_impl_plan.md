---
title: "구현 계획: SR/SR_ADD payload.expected_value 생성"
date: 2025-09-14
based_on: research/2025-09-14_12-28-28_sr_expected_value_payload.md
status: draft
owners: ["Codex"]
---

# Problem 1-Pager

- 배경: PRD 3.1(Operation Sequence)에서 SR/SR_ADD 계열은 payload에 `expected_value`(busy/extrdy/ready)를 포함해야 한다. 판단 기준은 "SR/SR_ADD이 예약되던 당시" target die/plane들의 진행 중 op_state에 따른다.
- 문제: 현재 `export_operation_sequence`는 SR/SR_ADD의 `expected_value`를 계산/주입하지 않아, `config.yaml[payload_by_op_base]` 스키마(`SR: [die,expected_value]`, `SR_ADD: [die,plane,expected_value]`)를 충족하지 못한다.
- 목표
  - G1: Export 단계에서 `ResourceManager`의 상태 조회를 기반으로 SR/SR_ADD의 `expected_value`를 정확히 계산해 payload에 주입한다.
  - G2: 시간 기준은 제안 시각을 우선: row의 `phase_key_time`(없을 경우 `start_us`)를 `quantize`하여 사용한다.
  - G3: 변경 범위를 최소화한다. `export_operation_sequence`에 `rm: ResourceManager`를 인자로 추가하고, 호출부 2곳만 갱신한다.
- 비목표
  - 스케줄러에서 `expected_value`를 산출해 row에 저장하는 구조 변경(관심사 침투). 
  - rows/cfg만으로 타임라인을 재구성하여 계산(RM 미사용). 
- 제약
  - 함수 ≤ 50 LOC, 파일 ≤ 300 LOC, 복잡도 ≤ 10을 지향(핵심 로직은 작은 헬퍼로 분리).
  - JSON 인코딩 정책 준수(PRD 3.1.1). 입력/출력 검증과 안전한 폴백을 유지.

# 변경 개요(Where & What)

- main.py
  - `export_operation_sequence(rows, cfg, rm, *, out_dir, run_idx)`로 시그니처 수정 및 호출부 2곳 갱신.
  - SR/SR_ADD payload 구성 시 `expected_value` 필드 주입.
  - 보조 헬퍼 추가: (a) die 전체 상태 분류(die-wide busy/extrdy/ready), (b) SR plane-특례(72h/7Ch) 처리.
  - 시간 선택: row별 `phase_key_time`이 있으면 우선, 없으면 `start_us`; `quantize`로 정규화.
- resourcemgr.py
  - 변경 없음. 기존 `op_state(die, plane, at_us) -> Optional[str]` 사용.
- config.yaml / docs
  - 변경 없음. 단, PRD 3.1 규칙 준수를 구현(문서 주석 보강은 선택).

# 상세 설계

## 규칙 정리(PRD 3.1)
- 공통(die 기준):
  - 하나라도 `.CORE_BUSY` 존재 → 'busy'
  - `.DATA_OUT` 또는 `.DATA_IN`만 존재(하나 이상) → 'extrdy'
  - 위 상태가 하나도 없으면 → 'ready'
- 특례: Read_Status_Enhanced_72h / 7Ch
  - 대상 plane에 대해 `PLANE_READ/PLANE_READ4K/PLANE_CACHE_READ`의 `.CORE_BUSY`가 진행 중이면 즉시 'busy'.
  - 아니면 공통(die 기준) 규칙 적용.
- LUN_Status_Read_for_LUN0..3
  - 해당 LUN 번호 = die address로 간주하고 공통(die 기준) 규칙 적용.

## 시간 선택
- row 단위 제안 시각 보존 필드 사용: `t_eval = quantize(row['phase_key_time'] or row['start_us'])`.
- SR/SR_ADD 모두 동일 기준 적용. 그룹 대표시각이 아닌, 각 payload 항목이 참조하는 row의 시각을 사용해 더 보수적으로 평가.

## 구현 포인트
- 읽기 베이스 집합: `{ 'PLANE_READ', 'PLANE_READ4K', 'PLANE_CACHE_READ' }`.
- plane-특례 op_name 집합: `{ 'Read_Status_Enhanced_72h', 'Read_Status_Enhanced_7Ch' }`.
- die-기준 SR/SR_ADD op_name: 70h/71h/78h/7Ah/7Bh/7Dh/7Eh 및 `LUN_Status_Read_for_LUNx`.

### 의사 코드
```python
def _die_status(rm, die: int, num_planes: int, t: float) -> str:
    busy = False
    has_io = False
    for p in range(num_planes):
        st = rm.op_state(die, p, t)
        if not st:
            continue
        try:
            base, state = st.split('.', 1)
        except ValueError:
            continue
        if state == 'CORE_BUSY':
            busy = True
            break
        if state in ('DATA_OUT', 'DATA_IN'):
            has_io = True
    if busy:
        return 'busy'
    return 'extrdy' if has_io else 'ready'

def _expected_value_for_sr(name: str, die: int, plane: int, t: float, rm, num_planes: int) -> str:
    plane_pref = {'Read_Status_Enhanced_72h', 'Read_Status_Enhanced_7Ch'}
    if name in plane_pref:
        st = rm.op_state(die, plane, t)
        if st:
            try:
                base, state = st.split('.', 1)
            except ValueError:
                base, state = st, ''
            if state == 'CORE_BUSY' and base in {'PLANE_READ','PLANE_READ4K','PLANE_CACHE_READ'}:
                return 'busy'
    return _die_status(rm, die, num_planes, t)
```

### Exporter 통합
- `export_operation_sequence` 그룹 루프 내 payload item 생성 직전:
  - `fields = cfg['payload_by_op_base'][base]` 확인.
  - `base in {'SR','SR_ADD'}`일 때만 `expected_value` 계산/주입.
  - SR: `item`에는 `die`만 남도록 필드 필터링되므로, `die=int(r['die'])`, `plane=int(r['plane'])`로 계산 후 `item['expected_value']`를 넣고 필터링.
  - SR_ADD: 동일하되 plane 기준으로 계산.
- planes 개수는 `cfg['topology']['planes']` 사용.
- 안전성: RM 조회 실패/비정상 문자열 분해 실패 시 공통 규칙에서 자연스럽게 'ready' 혹은 'extrdy'로 수렴(에러 억제, 결정성 유지).

# 구현 단계(Tasks)

1) main.py: `export_operation_sequence` 시그니처에 `rm: ResourceManager` 추가, 두 호출부 갱신.
2) main.py: 내부에 `_die_status`, `_expected_value_for_sr` 두 헬퍼 구현(각 ≤ 25 LOC), 상수 세트 정의.
3) main.py: payload 조립부에서 SR/SR_ADD 분기 시 `expected_value` 계산 후 `item['expected_value']` 주입.
4) 샘플 실행 및 검증: `scripts/validate_payload_by_op_base.py`를 실행해 SR/SR_ADD가 스키마를 충족하는지 확인.
5) 문서/주석: PRD 규칙 출처 및 시간 선택 근거를 1~2줄 주석으로 명시.

# 테스트 전략

- 결정적 샘플 실행(E2E 유사)
  - T1: 기본 실행 1회로 `operation_sequence_*.csv` 생성 후 validator로 SR/SR_ADD의 누락 없는지 확인(키 셋 검사 통과).
  - T2: 의도된 busy 상황(READ CORE_BUSY 중)에서 SR_ADD 72h/7Ch가 'busy'로, 그 외가 die 기준으로 분류되는지 spot-check(수작업/로그).
- 단위 수준(선택)
  - 헬퍼 함수에 대해 모의 RM을 사용해 busy/extrdy/ready 분류 케이스 각각 검증.

# 대안 비교(요약)

- A) Exporter에서 RM 조회(선택안)
  - 장점: 정확/단순, 변경 최소(main.py 함수+호출부). 
  - 단점/위험: exporter가 RM에 의존(이미 다른 exporter들에서 선례 있음).
- B) 스케줄러 산출
  - 장점: exporter 단순.
  - 단점: 관심사 침투/스키마 변경 필요.
- C) rows+cfg로 타임라인 재구성
  - 장점: exporter 순수성.
  - 단점: 복잡/오탐 위험, RM과의 일치성 보장 어려움.

# 수용 기준(AC)

- AC1: SR/SR_ADD payload에 `expected_value`가 항상 존재하며 키 셋이 `config.yaml[payload_by_op_base]`와 일치한다.
- AC2: 72h/7Ch plane-특례가 올바르게 반영되고, 나머지는 die 기준 분류를 따른다.
- AC3: 시간 기준은 `phase_key_time` 우선, 부재 시 `start_us` 사용이 일관되게 적용된다.
- AC4: 기존 exporter/기능 회귀가 없다(기존 파일 포맷/정렬/결정성 유지).

# 위험과 완화

- 멀티-타겟 SR 그룹에서 die 중복으로 인한 payload 항목 중복 위험 → 현행 스케줄링에서는 SR/SR_ADD가 `multi: false`라 영향 작음. 필요시 die-기준 dedup 고려(스코프 외).
- 상태 문자열 파싱 오류 → try/except로 방어, 안전 폴백.
- 성능 영향 → per-item O(planes) 루프이며 일반 설정에서 planes가 작음(≤8). 문제시 캐시/메모이제이션 고려.

# 롤아웃

1) 구현 및 로컬 샘플 실행 → validator 통과.
2) SR/SR_ADD-heavy 시나리오로 spot-check 1~2건(Big-O 영향/정확성).
3) 문서 보강(선택) 후 머지.

# 참고(파일/라인)

- PRD: `docs/PRD_v2.md:28-36`(SR/SR_ADD expected_value 규칙)
- CFG: `config.yaml:761-762`(payload_by_op_base SR/SR_ADD), `config.yaml:4328-4392`(SR/SR_ADD op_name 리스트)
- 코드: `main.py:421`(export_operation_sequence), `main.py:939`/`main.py:1029`(호출부), `resourcemgr.py:583`(op_state), `scheduler.py:359`(propose_now/phase_key_time)

