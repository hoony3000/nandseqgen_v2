---
date: 2025-09-14T12:28:28+0900
researcher: Codex
git_commit: 2ce0ed07bb28ff7bf08684142bc14666adab48aa
branch: main
repository: nandseqgen_v2
topic: "Implement expected_value for SR/SR_ADD payload (PRD 3.1)"
tags: [research, codebase, exporter, SR, SR_ADD]
status: complete
last_updated: 2025-09-14
last_updated_by: Codex
---

# 연구: PRD 3.1의 SR/SR_ADD payload.expected_value 생성 구현

**Date**: 2025-09-14T12:28:28+0900
**Researcher**: Codex
**Git Commit**: 2ce0ed07bb28ff7bf08684142bc14666adab48aa
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
PRD 3.1의 payload 정의에 따라 SR/SR_ADD 오퍼레이션의 `expected_value`(busy/extrdy/ready)를 생성하는 안전하고 일관된 구현 방법은 무엇인가?

## 요약
- 권장 구현 지점: `export_operation_sequence`에서 `ResourceManager`를 사용해 제안시각(예약시각) 기준의 상태를 조회하고, PRD 규칙에 따라 `expected_value`를 산출한다.
- 시간 기준: 스케줄러가 보존하는 `phase_key_time`(propose_now)을 우선 사용, 없으면 해당 row의 `start_us` 사용.
- 규칙 구현: 
  - 공통(di e 전체): 한 plane라도 `.CORE_BUSY`면 'busy'; 그렇지 않고 `.DATA_OUT`/`.DATA_IN`만 존재하면 'extrdy'; 둘 다 아니면 'ready'.
  - 특례(72h/7Ch): 대상 plane이 `PLANE_READ*`/`PLANE_CACHE_READ`의 `.CORE_BUSY`이면 즉시 'busy', 아니면 공통 규칙으로 폴백.
- 변경 범위: `export_operation_sequence` 시그니처에 `rm: ResourceManager` 추가, 호출부 2곳 업데이트, SR/SR_ADD payload 조립 시 `expected_value` 주입.

## 상세 발견

### PRD 규칙 출처
- `docs/PRD_v2.md:28`: SR/SR_ADD의 `expected_value`는 예약 당시의 target die/plane들의 진행 중 state에 따라 결정.
- `docs/PRD_v2.md:29-36`: op_name별 분기 규칙 요약
  - Read_Status_Enhanced_70h/71h/78h/7Ah/7Bh/7Dh/7Eh → die 전체 기준 busy/extrdy/ready
  - Read_Status_Enhanced_72h/7Ch → 대상 plane이 `PLANE_READ/PLANE_READ4K/PLANE_CACHE_READ`의 CORE_BUSY면 busy, 아니면 die 전체 규칙
  - LUN_Status_Read_for_LUN0..3 → 특정 die(해당 LUN) 기준 die 전체 규칙

### 현재 코드 흐름과 부족점
- CSV export(시퀀스): `main.py:421` `export_operation_sequence(rows, cfg, out_dir, run_idx)`는 `cfg[payload_by_op_base]`에 따라 payload를 구성하지만 `expected_value`는 계산하지 않음. SR/SR_ADD에서 누락됨.
- 설정: `config.yaml:761-762`에 SR/SR_ADD payload 스키마가 `expected_value`를 요구함.
- 밸리데이터: `scripts/validate_payload_by_op_base.py:37`는 스키마 키 존재만 검사하며, plan 결과들에 `expected_value` 누락 이슈가 다수 기록됨.
- 상태 조회 API: `resourcemgr.py:540` `op_state(die, plane, at_us)`가 `<BASE>.<STATE>` 또는 None을 반환. die/plane 전수 조회 가능.
- 제안시각 보존: 스케줄러가 예약 시각 `propose_now`를 row에 싣고(`scheduler.py:353`), exporter는 이를 `phase_key_time`으로 보존하여 다른 export에서 사용(`main.py:35-62`, `main.py:514-540`).

### 구현 방안 A(권장): Exporter에서 RM으로 상태 조회하여 계산
핵심 아이디어: Exporter가 `ResourceManager`를 받아, 각 SR/SR_ADD row마다 제안시각의 상태를 조회해 `expected_value`를 산출.

- 시그니처 변경
  - `main.py:421`: `def export_operation_sequence(rows, cfg, *, out_dir, run_idx)` → `def export_operation_sequence(rows, cfg, rm, *, out_dir, run_idx)`
  - 호출부: `main.py:942`, `main.py:1032` 두 곳 모두 `rm` 추가 인자 전달

- 시간 선택 로직
  - row 그룹의 대표 row에서 `phase_key_time`을 우선 사용, 없으면 `start_us`.
  - `t_eval = quantize(phase_key_time or start_us)`로 정규화.

- 분류 함수(의사 코드)
  ```python
  def _die_status(rm, die, num_planes, t):
      busy = False; di_do = False
      for p in range(num_planes):
          st = rm.op_state(die, p, t)  # e.g., 'READ.CORE_BUSY'
          if not st: continue
          base, state = st.split('.', 1)
          if state == 'CORE_BUSY':
              busy = True
              break
          if state in ('DATA_OUT','DATA_IN'):
              di_do = True
      if busy: return 'busy'
      return 'extrdy' if di_do else 'ready'

  def _expected_value_for_sr(name, die, plane, t, rm, num_planes):
      plane_pref_names = {'Read_Status_Enhanced_72h','Read_Status_Enhanced_7Ch'}
      if name in plane_pref_names:
          st = rm.op_state(die, plane, t)
          if st:
              base, state = st.split('.', 1)
              if state == 'CORE_BUSY' and base in {'PLANE_READ','PLANE_READ4K','PLANE_CACHE_READ'}:
                  return 'busy'
      # Fallback and for all die-level names (70h/71h/78h/7A/7B/7D/7E, LUN_Status_Read_for_LUNx)
      return _die_status(rm, die, num_planes, t)
  ```

- Exporter 적용 지점
  - `export_operation_sequence` 내부 루프에서 `base in {"SR","SR_ADD"}`인 경우, 각 payload item을 만들 때 `expected_value`를 추가:
    - SR: item에는 `die`만 포함하므로 동일 `die` 기준의 값을 계산해 `item['expected_value'] = ...`.
    - SR_ADD: 대상 plane별로 계산하며 `item['expected_value'] = ...`.
  - planes 개수는 `cfg['topology']['planes']` 사용.

### 구현 방안 B: 스케줄러에서 예약 시 `expected_value`를 계산해 row에 포함
- 장점: export는 단순화, 시뮬레이션 시점의 RM 상태를 직접 사용.
- 단점: 스케줄러가 PRD 3.1의 출력 정책을 알게 되어 관심사 침투. row 스키마 변경 필요.

### 구현 방안 C: rows+cfg 기반으로 상태 타임라인을 재구성하여 계산(RM 미사용)
- 장점: export가 순수(rows/cfg만)하며 과거 CSV만으로도 재계산 가능.
- 단점: `affect_state=false` 게이트, 상속/변형된 durations 반영 등 로직이 복잡하고 RM과 일치성 보장 어려움.

→ 비교 결론: A가 가장 단순·정확·변경 최소. B는 결합도 증가. C는 복잡성·위험 증가.

## 코드 참조
- `main.py:421` — `export_operation_sequence`가 payload 조립; 현재 `expected_value` 미계산
- `main.py:942` / `main.py:1032` — export 호출부(여기서 `rm` 전달 필요)
- `resourcemgr.py:540` — `op_state(die, plane, at_us)` API
- `scheduler.py:353` — 예약 시각 `propose_now` 캡처(Exporter에서 `phase_key_time`으로 사용)
- `config.yaml:761` — `payload_by_op_base.SR: [die,expected_value]`
- `config.yaml:762` — `payload_by_op_base.SR_ADD: [die,plane,expected_value]`
- `config.yaml:4328-4392` — SR/SR_ADD op_name 리스트
- `docs/PRD_v2.md:28-36` — SR/SR_ADD expected_value 분류 규칙

## 아키텍처 인사이트
- Exporter에서 RM 주입은 이미 다른 export(`export_operation_timeline`, `export_op_state_timeline`, `export_phase_proposal_counts`)가 RM을 사용하는 것과 일관됨. 출력 계층이 시뮬레이터 상태를 조회하는 패턴 확장에 해당.
- `phase_key_time`를 일관된 "예약시각"으로 사용하면 PRD의 "예약 당시" 요건에 부합. 부재 시 `start_us` 폴백은 합리적.
- SR/SR_ADD는 `affect_state=false`라서 타임라인에 세그먼트를 남기지 않지만, 조회 대상은 READ/PROGRAM류의 state이므로 영향 없음.

## 역사적 맥락(thoughts/ 기반)
- 전용 thoughts/ 디렉터리는 없으나, `plan/payload_by_op_base_validation_results_*.json`에 SR/SR_ADD의 `expected_value` 누락 이슈가 반복적으로 기록됨(예: `plan/payload_by_op_base_validation_results_20250914_104520.json`).

## 관련 연구
- `plan/payload_by_op_base_validation_plan.md` — payload 스키마 검증 계획

## 미해결 질문
- extrdy 판정에서 "만"의 해석: die 전체에서 유효 state가 하나도 없을 때는 'ready'가 맞고, DI/DO가 하나 이상 존재하되 CORE_BUSY가 없으면 'extrdy'로 구현(본 연구의 해석). 필요시 예시/테스트로 명확화 필요. -> (검토완료) DI/DO가 하나 이상 존재하되 CORE_BUSY가 없으면 'extrdy'로 구현
- LUN_Status_Read_for_LUNx의 대상 die 결정은 row.targets의 die를 신뢰하는 것으로 가정. 만약 별도 매핑이 필요하면 op_name→die 인코딩 로직을 별도 보강 필요.
-> (검토완료) row.targets 의 die 적용