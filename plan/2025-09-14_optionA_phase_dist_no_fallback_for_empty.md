# Option A — phase_conditional empty means "no candidates"

## 배경
- proposer 는 propose 시점의 phase_key 로 `cfg.phase_conditional[key]` 를 조회한다.
- 현재 구현은 key 가 비어있는 dict(`{}`)일 때도 falsy 로 간주해 `DEFAULT` 로 폴백한다.
- 이로 인해 `PROGRAM_SLC.CORE_BUSY: {}` 와 같이 명시적으로 비워 둔 상태에서도 `DEFAULT` 분포에서 `ONESHOT_PROGRAM_*` 등이 선택되는 문제가 발생한다.

## 목표
- `phase_conditional[key]` 가 "존재하지만 비어있으면" 폴백하지 않고, 해당 훅에서는 후보가 없다고 간주한다.
- key 가 "존재하지 않는 경우" 에만 `DEFAULT` 로 폴백한다.

## 비목표
- phase 선택/타깃 샘플링/윈도우/룰 검증 등의 나머지 로직 변경은 하지 않는다.
- config 파일(op_state_probs.yaml) 내용을 자동 수정하지 않는다.

## 설계/변경점
- `proposer._phase_dist(cfg, key)` 를 다음과 같이 변경
  - `pc = cfg['phase_conditional']`
  - `dist = pc.get(key)` 를 조회
  - `dist is None` (키 부재) 인 경우에만 `pc.get('DEFAULT', {})` 로 폴백
  - `dist` 가 존재하나 비 dict 이면 빈 dict 로 처리
  - 이후 기존대로 float coercion + `phase_conditional_overrides` 적용
  - 결과가 빈 dict 이면 proposer 는 해당 훅에서 None 반환(스킵)

## 대안 비교
- A) 키 부재에만 DEFAULT 폴백(이번 선택)
  - 장점: 설정의 의도를 정확히 반영(명시적 `{}`는 금지 의미)
  - 단점: 빈 키가 많으면 제안 기회가 줄 수 있음
- B) 빈 dict 에도 DEFAULT 를 병합(override 우선) 후 남은 것을 허용
  - 장점: 제안 기회 보존
  - 단점: 금지 의도를 약화

## 리스크/완화
- 실행 중 제안률 감소 가능: 훅 수/키 구성에 따라 no-op 빈도가 증가할 수 있음.
  - 완화: 필요 시 config 의 `DEFAULT`/각 key 분포를 보강하거나, 빈 키를 제거(=미정의로 만들어 DEFAULT 폴백 유도).

## 검증 계획
- 단위: `_phase_dist`에 대해
  1) 키 미존재 → DEFAULT 반환
  2) 키 존재·빈 dict → 빈 dict 반환
  3) 키 존재·유효 분포 → 그 분포 반환(+override 적용)
- 통합: 동일 시나리오에서 `PROGRAM_SLC.CORE_BUSY` 훅에서 `ONESHOT_PROGRAM_*`가 더 이상 선택되지 않는지
  - `out/phase_proposal_counts_*.csv` 에서 `phase_key_used=PROGRAM_SLC.CORE_BUSY` 행들에 `One_Shot_PGM_*` 가 사라지는지 확인

## 롤백
- 필요 시 `_phase_dist` 의 기존 폴백(`if not dist: dist=DEFAULT`) 로 되돌린다.

