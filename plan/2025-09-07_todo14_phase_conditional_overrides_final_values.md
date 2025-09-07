# TODO 14 — phase_conditional_overrides 값을 최종값으로 강제 적용 계획

## Problem 1‑Pager

- 배경: 제안 로직은 `cfg['phase_conditional'][<BASE.STATE>|DEFAULT]` 분포를 사용해 후보 오퍼레이션을 가중치 샘플링한다. 구성 파일에는 전역/상태별 오버라이드(`phase_conditional_overrides`)가 있으며 PRD에 따라 오버라이드 값은 “최종 확률 질량”으로 취급되어야 한다.
- 문제: `phase_conditional_overrides.global`에 지정한 값이 일부 상태에서 최종값으로 반영되지 않는다. 반면 특정 상태 키(예: `ERASE.END`)에 지정한 값은 기대대로 최종값으로 반영된다.
- 목표: 오버라이드 우선순위(global → per‑state)를 지키면서, 어떤 상태에서도 입력한 오버라이드 값이 최종 분포로 강제 반영되도록 한다. 분포 합은 1로 유지한다.
- 비목표: 오버라이드 스키마 변경(필드명/형식) 및 대규모 리팩터링은 하지 않는다. 기본 분포 생성 로직의 규칙(배제 규칙 등)은 바꾸지 않는다.
- 제약: 함수 ≤ 50 LOC, 순환복잡도 ≤ 10, 파일 ≤ 300 LOC. 기존 공개 API/CLI 옵션은 유지. 민감정보 로깅 금지.

## 영향도 및 호출 경로

- 소비 지점: `proposer.propose()` → `_phase_dist(cfg, key)` → 가중치 기반 후보 선정
- 공급 지점: `cfg_autofill.ensure_from_file_or_build()`가 `phase_conditional`을 파일에서 로드/빌드하고 오버라이드를 적용함
- 현 증상 가설: 전역 오버라이드가 “해당 키의 후보 집합에 존재하는 항목”에만 분배되기 때문에, 특정 상태 분포가 해당 base/op_name을 후보로 포함하지 않으면 전역 오버라이드가 무효화됨. 런타임에 최종 강제 적용이 필요.

## 대안 비교(2가지 이상)

1) Build‑time 전용 강제화 강화
   - 장점: 런타임 오버헤드 없음. 단일 진실원천 유지.
   - 단점: 이미 저장된 `op_state_probs.yaml`의 후보 집합 제약을 넘어서기 어려움(전역 오버라이드가 후보에 없으면 여전히 반영 불가).

2) Runtime 강제화(선호)
   - 장점: 로드된 분포가 어떻든 global/per‑state 오버라이드를 즉시 최종값으로 반영. 회귀에 강함.
   - 단점: 런타임 분포 조정 코드 추가. 이중 적용 위험(빌드/런타임) 관리 필요.

→ 선택: 2) Runtime 강제화. 빌드타임 적용은 유지하되, 런타임에서 “최종 보정층”으로 동일 규칙을 재적용(멱등적)하여 일관성 확보.

## 설계(규칙 정의)

- 우선순위: `global` → `per‑state(<BASE.STATE>)` (뒤가 앞을 덮어씀)
- 키 해석: base 또는 op_name 모두 허용
- 후보 집합: 기본은 해당 상태의 기존 후보(dist.keys). 단, 전역/상태별 base 또는 op_name 오버라이드에 의해 “추가되어야 하는 후보”가 있으면, 아래 규칙을 만족하는 범위에서 후보에 합류시킴
  - 추가 허용 조건: (a) `cfg.op_names`에 존재, (b) 해당 상태의 배제 규칙(`exclusions_by_op_state`→`exclusion_groups`)에 의해 금지되지 않음
- 정규화 규칙(PRD 반영):
  1) 오버라이드의 총합 `sum_overrides` 계산(op_name 단위로 집계; base‑단위 항목은 해당 base의 후보(op_name)들에게 비례/균등 분배, 아래 “배분 규칙” 참조)
  2) `sum_overrides >= 1` 이면 오버라이드 항목만 남기고 상호 정규화하여 최종 합 1로 만듦(비오버라이드 항목 제거)
  3) `sum_overrides < 1` 이면 비오버라이드 항목의 기존 가중치를 비례 스케일링하여 합이 `1 - sum_overrides`가 되도록 정규화(0 또는 음수는 무시)
- 배분 규칙:
  - base‑단위 오버라이드는 “해당 base의 후보 op_name”들에 배분. 기본은 기존 분포 비중 비례, 모두 0이거나 미존재 시 균등 분배.
  - 동일 op_name에 대해 global과 per‑state가 모두 지정되면 per‑state가 최종치를 “치환”함.
- 후보 불일치 처리:
  - 오버라이드가 후보에 없는 op_name을 가리키면(또는 배제 규칙에 걸리면) 무시하고 경고 로그.
  - base가 후보 op_name을 하나도 제공하지 못하면 무시하고 경고 로그.

## 구현 계획(작업 단위)

1) proposer 런타임 보정 함수 추가
   - 파일: `proposer.py`
   - 함수: `_apply_phase_overrides(cfg, key, dist) -> Dict[str,float>`
     - 입력: 기존 분포(dist: name→weight)
     - 처리: 상기 규칙대로 후보 확장(필요 시), 오버라이드 집계 및 정규화
     - 출력: 최종 분포(name→prob), 합≈1

2) 소비 지점 연결
   - 위치: `_phase_dist(cfg, key)` 반환 직전에 `_apply_phase_overrides` 호출
   - 주석: `cfg_autofill` 단계에서도 적용되지만, 런타임에서 멱등적으로 재보정하여 전역 오버라이드의 확정 반영을 보장

3) 로깅/검증 보조
   - `--validate-pc` 활성 시, 키별로 “before → overrides → after(sum, topK)”를 proposer 로그에 1라인 요약 출력
   - 민감정보/대용량 덤프 금지

4) 테스트 추가(단위/계약)
   - 파일: `tests/test_phase_overrides.py`
   - 케이스:
     - global base만 지정, 후보에 해당 base 일부만 존재: 지정 질량이 해당 부분집합에 비례/균등으로 정확 배분
     - per‑state op_name 치환(global 무시)
     - sum_overrides ≥ 1: 비오버라이드 제거 및 1로 정규화
     - sum_overrides < 1: 비오버라이드가 (1 − sum_overrides)로 스케일
     - 후보 확장: 글로벌 base 지정인데 원래 dist에 없는 op_name이 cfg에 존재하고 배제 규칙 OK인 경우 후보에 합류 후 분배
     - 음수/비수치 무시, 0은 제거의 의미(최종 0)

5) 문서 반영
   - `docs/PRD_v2.md` 오버라이드 섹션에 “런타임 강제화” 한 줄 추가 및 후보 확장 규칙 명문화

## 알고리즘 개요(pseudocode)

```
def apply_overrides(cfg, key, dist):
  names_by_base = groups_by_base(cfg)
  excluded_bases = exclusions_by_op_state(cfg, key)
  allowed = lambda name: base(name) not in excluded_bases

  # 1) 후보 확장: overrides(global + per‑state)에서 지목된 op_name/base를 dist 후보로 합류(allowed만)
  cand = set(dist.keys())
  for sym in override_symbols(cfg, key):
    if is_op_name(sym) and allowed(sym):
      cand.add(sym)
    if is_base(sym):
      for n in names_by_base[sym]:
        if allowed(n): cand.add(n)

  # 2) 오버라이드 산출: op_name 절대질량 fixed[name]
  fixed = resolve_absolute_masses(cfg, key, cand, dist)
  sum_fixed = sum(max(0,fixed[n]) for n in fixed)

  # 3) 정규화
  if sum_fixed >= 1.0:
    return {n: fixed[n]/sum_fixed for n in fixed if fixed[n] > 0}
  # 남은 질량을 비오버라이드 양수 항목에 비례 스케일
  rem = 1.0 - sum_fixed
  others = {n: max(0, dist.get(n,0)) for n in cand if n not in fixed}
  s = sum(others.values())
  out = {}
  if s > 0:
    for n,v in others.items(): out[n] = (v/s)*rem
  for n,v in fixed.items():
    if v>0: out[n] = v
  return out
```

주: `resolve_absolute_masses`는 base‑단위 지정 시 dist의 기존 비중 비례(합 0이면 균등)로 배분하며, per‑state 지정은 동일 op_name에 대해 global을 치환함.

## 수용 기준(AC)

- AC1: `config.yaml`의 `phase_conditional_overrides.global`에 지정한 값이 어떤 상태 키에서도 최종 분포 합 1 기준으로 정확히 반영됨(배제 규칙 허용 범위 내).
- AC2: 동일 키에 per‑state 오버라이드가 있으면 global 설정을 덮어쓰고 최종값으로 반영됨.
- AC3: `sum_overrides ≥ 1`이면 비오버라이드 항목이 결과에서 사라지고(혹은 0), 오버라이드끼리 합 1로 정규화됨.
- AC4: `sum_overrides < 1`이면 비오버라이드 항목의 합이 `1 − sum_overrides`가 되도록 비례 스케일됨.
- AC5: `--validate-pc` 실행 시 키별 요약 로그에 위 정규화 결과가 일관되게 기록됨.

## 검증 계획

- 유닛 테스트로 정규화/배분/우선순위/후보 확장 로직을 결정적으로 검증.
- 샘플 실행: `python main.py -t 200 -n 1 --seed 42 --out-dir out --validate-pc`
  - proposer 로그에서 특정 키의 before/after 합과 topK 비교(전역 READ/PLANE_READ 계열 설정 샘플)
  - `phase_proposal_counts_*.csv`를 집계하여 큰 틀에서 전역 오버라이드 반영 경향 확인(완벽 일치가 아닌 경향성 확인 용도)

## 롤아웃

- 1차: 런타임 적용 + 단위 테스트 + 문서 1줄 보강 → 로컬 검증
- 2차: 필요 시 `cfg_autofill.validate`에 “effective distribution” 로그 추가(선택)
- 3차: 장기간 시뮬에서 성능 영향 미미함 확인 후 TODO #14 종료 체크

## 리스크 및 대응

- 중복 적용(빌드+런타임)로 의도치 않은 2중 정규화: 멱등 설계(오버라이드 절대값→병합→정규화)로 안전. 테스트로 보장.
- 후보 확장으로 예상치 못한 이름 유입: 배제 규칙 필터와 cfg.op_names 존재 검증으로 보호. 경고 로그 남김.
- 기존 분포가 0 또는 비어있는 경우: 오버라이드 합이 1 미만이고 others 합이 0이면, 오버라이드만으로 합 1이 되도록(=오버라이드끼리 재정규화) 처리.

## 작업 분해 및 추정

- 구현: 2.0h — 함수 추가, 배제 규칙/배분 구현, 로깅
- 테스트: 1.0h — 핵심 케이스 6종
- 문서: 0.5h — PRD 섹션 보강

