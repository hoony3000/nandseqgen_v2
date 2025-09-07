---
title: "구현 계획: 후속 operation 균등 샘플링 적용"
date: 2025-09-08
based_on: research/2025-09-08_00-13-00_uniform_sampling_subsequent_ops.md
status: draft
owners: ["Codex"]
---

# Problem 1-Pager

- 배경: PRD v2는 sequence 생성 시 비-SEQ 분기에서 `groups_by_base[BASE]` 후보를 균등(uniform) 확률로 샘플링하여 후속 op_name을 선택하도록 요구한다.
- 문제: 현 구현의 `_choose_op_name_for_base`는 힌트(same_celltype/multi) 필터 후 선언 순서 기반의 첫 매치 또는 names[0] 폴백을 사용하여 편향이 존재한다(균등 아님).
- 목표
  - G1: 비-SEQ 경로와 `.SEQ` 전개(generate_seq_rules) 단계 모두에서 후속 op_name 선택을 균등 샘플링으로 전환.
  - G2: 동일 시드/입력에서 결정성 유지(RNG 스트림 사용).
  - G3: 필요 시 per-base 가중치 예외(name_weights)만 허용하고, 미지정 시 균등이 기본.
- 비목표
  - ResourceManager 정책/락/ODT 등 스케줄링 정책 변경.
  - 주소 상속 규칙 자체의 의미 변경(현행 구현 유지).
- 제약
  - 함수 ≤ 50 LOC, 파일 ≤ 300 LOC, 복잡도 ≤ 10.
  - RNG는 주입된 `rng`만 사용(시스템 시간 금지).

# 변경 개요(Where & What)

- proposer.py
  - 신규: `_choose_op_name_for_base_uniform(cfg, base, multi=None, celltype=None, rng=None, weights=None)` 추가 — 후보 필터 후 균등(또는 가중치) 샘플.
  - 적용: 비-SEQ 1단계 `_expand_sequence_once`(line 807 부근)와 체인 전개 `_expand_sequence_chain`의 비-SEQ 분기(line 936/987), 그리고 `.SEQ` 전개 `_expand_sequence_seq`(line 844/917)에서 균등 선택기를 사용.
  - 현 `_choose_op_name_for_base`는 호환성 유지(타 경로 영향 최소화). 호출처에서 균등 버전으로 전환.
- config.yaml
  - 선택적 예외: `generate_seq_rules[<key>].name_weights[op_base][op_name]: weight` 스키마 도입(존재 시 해당 단계에 한해 가중치 분포 사용; 미지정 시 균등).
- docs/PRD_v2.md
  - 구현 반영 노트 추가: 비-SEQ 및 `.SEQ` 단계의 기본 샘플링을 균등으로 표준화, 예외는 선언적 가중치로 한정.

# 상세 설계

## 1) 균등 이름 선택기

- 시그니처: `_choose_op_name_for_base_uniform(cfg, base, multi=None, celltype=None, rng=None, weights=None) -> Optional[str]`
- 입력
  - `cfg`: 전체 설정
  - `base`: 오퍼레이션 베이스 문자열(예: `DOUT`)
  - `multi`: Optional[bool] — 힌트. True면 multi=true op만, False면 multi=false op만 포함. None이면 미필터.
  - `celltype`: Optional[str] — 힌트. 일치하는 celltype만 포함.
  - `rng`: 난수 소스(`rng.random()` 존재 시 사용; 없으면 `random.random()`)
  - `weights`: Optional[Dict[str, float]] — 가중치 예외(해당 base의 일부 op_name에만 지정 가능). None이면 균등.
- 로직
  - 후보 집합: `_op_names_by_base(cfg)[base]`에서 시작.
  - 1차 필터: `multi`/`celltype` 엄격 적용.
  - 후보 0개 시: 기존 의미 유지 차원에서 완화 폴백 적용(우선순위: celltype 무시 → multi 무시 → base 전체). 그래도 0개면 `None`.
  - 분포 구성:
    - `weights`가 None이면 균등.
    - `weights` 존재 시: 후보 중 명시된 이름들의 가중 합(F)을 구함. F≥1이면 F만 정규화해 샘플. F<1이면 남은 후보에 (1-F)을 균등 분배해 합성 분포로 샘플.
  - 샘플링: 누적합(acc) 방식으로 `r = rng.random() * tot` 인덱싱.
  - 반환: 선택된 op_name 또는 None.
- 결정성: 모든 난수는 인자로 전달된 `rng`에서만 획득.

## 2) 적용 지점

- `_expand_sequence_once`(proposer.py:807): `name2 = _choose_op_name_for_base_uniform(...)`로 교체. 힌트는 기존 로직 그대로 계산.
- `_expand_sequence_chain` 비-SEQ 분기(선택한 key가 `.SEQ`가 아닐 때) — proposer.py:936/987: 동일 교체.
- `_expand_sequence_seq`(proposer.py:844 loop 내부 917): 단계별 `base_i` 이름 선택 시 균등 선택기로 교체. 필요 시 `weights`는 `cfg['generate_seq_rules'][choice_key].get('name_weights', {}).get(base_i)` 전달.

## 3) 설정 스키마(가중치 예외)

```yaml
generate_seq_rules:
  CACHE_READ.SEQ:
    sequences:
      - CACHE_READ: ['same_page', 'same_celltype']
      - DOUT: ['prev_page', 'multi', 'same_celltype']
      - CACHE_READ_END: ['inc_page', 'same_celltype']
      - DOUT: ['same_page', 'multi', 'same_celltype']
    name_weights:            # 선택 사항(존재 시 해당 단계에서만 가중치 사용)
      DOUT:
        Enhanced_Random_Data_Output_Primary: 0.7
        Enhanced_Random_Data_Output_Secondary: 0.3
```

- 파싱: `_expand_sequence_seq`에서 `rules_root = cfg['generate_seq_rules'][choice_key]` 하위의 `name_weights`를 조회.
- 유효성: 후보 외 op_name은 무시. 음수/NaN은 무시. 합이 0이면 무시하고 균등.

## 4) 오류 처리/로깅

- 후보 0개 시: `None` 반환하여 호출부에서 해당 단계 스킵(현행과 동일 수준). 필요 시 `_log`로 디버그 메시지 1줄.
- 가중치 분포 비정상(합 0/음수): 무시 후 균등으로 진행, 1줄 경고.
- 로깅 톤: 기존 `_log` 유틸 사용, 민감정보 없음.

## 5) 호환성/결정성/성능

- 호환성: 기존 함수는 그대로 두고 호출부만 교체하여 영향 범위를 명확히 통제.
- 결정성: 동일 `rng` 시드/입력에서 동일 결과.
- 성능: 후보 개수는 소수(보통 1~3). 누적합 샘플 비용은 무시 가능.

# 구현 단계(Tasks)

1) 헬퍼 추가: `_choose_op_name_for_base_uniform` 구현(필터/분포 구성/샘플링). 주석에 폴백 순서 명시.
2) 비-SEQ 경로 교체: `_expand_sequence_once`와 `_expand_sequence_chain` 비-SEQ 분기에 균등 선택기 적용.
3) `.SEQ` 경로 교체: `_expand_sequence_seq` 이름 선택부를 균등 선택기로 교체하고 `name_weights`(있다면) 전달.
4) 설정/문서: `config.yaml` 주석에 `name_weights` 예시 추가. `docs/PRD_v2.md`에 구현 노트 1줄.
5) 테스트 추가: 단위/통합 테스트(아래 참조). RNG 스텁으로 결정적 검증.

# 테스트 전략

- 단위 테스트
  - T1: 후보 3개 균등 — RNG 스텁(0.0→idx0, 0.34→idx1, 0.99→idx2)로 선택이 균등 인덱싱 매핑되는지 검증.
  - T2: 힌트 필터 — `celltype`/`multi` 필터 적용으로 후보 축소 후 올바른 인덱싱. 후보 0개면 폴백 단계별 동작 확인.
  - T3: 가중치 합성 — F<1, F≥1 두 경우 분포 구성 로직 검증(스텁 RNG로 경계 선택).
- 통합 테스트
  - I1: 비-SEQ 1단계 전개 — `sequence[probs]`가 `DOUT` 등 비-SEQ key일 때 후속 op_name이 균등/결정적으로 선택되는지.
  - I2: `.SEQ` 전개 — `CACHE_READ.SEQ` 체인에서 DOUT 변형 간 가중치 미지정 시 균등, 지정 시 분포 반영.
  - I3: 회귀 — 기존 `test_proposer_seq_chain.py`, `test_proposer_inherit_rules.py` 통과.

# 대안 비교(요약)

- A) 신규 균등 선택기 추가(선택)
  - 장점: 국소 변경, PRD 요구 정확 반영, 결정성 유지.
  - 단점: 선택기 2종 공존(역할 분리 주석 필요).
- B) 기존 선택기 자체를 균등화
  - 장점: API 단일화.
  - 단점: 다른 경로까지 영향/호환성 리스크. 호출처 None 처리 필요.
- C) CFG에 op_name 가중치만 선언(코드 변경 없음)
  - 장점: 구현 용이.
  - 단점: PRD의 base 단위 균등 원칙과 불일치, 유지보수 부담.

# 수용 기준(AC)

- AC1: PRD 문서 요구(`docs/PRD_v2.md:332`)에 따라 비-SEQ 후속 op_name 선택이 균등으로 동작.
- AC2: `.SEQ` 단계 기본 균등, `name_weights` 지정 시 해당 단계에서만 가중치 분포 반영.
- AC3: 동일 시드/입력에서 결과 결정성 유지(테스트로 검증).
- AC4: 기존 통합 테스트 모두 통과, 신규 테스트 결정적 통과.

# 위험과 완화

- 위험: 힌트 충돌 시 폴백 전략이 의도와 다를 수 있음.
  - 완화: 현행 폴백 의미를 유지하고, 필요 시 별도 ADR로 정책 명시.
- 위험: `.SEQ` 단계의 기본 균등 적용이 일부 시나리오에서 기존 결과와 달라질 수 있음.
  - 완화: 가중치 예외로 선언적 제어 제공, 변경 영향은 테스트로 감시.

# 롤아웃

1) 기능 구현 → 유닛 테스트 추가/통과.
2) 통합 테스트 보강 및 통과.
3) 시뮬 샘플 실행 후 `out/` 타임라인 편향 제거 확인.
4) 필요 시 `name_weights` 문서화/예시 포함해 머지.

# 참고(파일/라인)

- PRD: `docs/PRD_v2.md:332`
- proposer: `proposer.py:658`(기존 선택기), `proposer.py:807`(once), `proposer.py:844`(SEQ), `proposer.py:917`(SEQ 내 이름 선택), `proposer.py:936`(chain), `proposer.py:987`(chain 내 이름 선택)
- config: `config.yaml:2174`(generate_seq_rules 루트)

