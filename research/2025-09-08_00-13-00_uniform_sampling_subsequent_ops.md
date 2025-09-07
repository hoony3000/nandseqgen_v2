---
date: 2025-09-08T00:13:00+09:00
researcher: Codex
git_commit: 466c7452461bd6dfa5bad9cb3836462ed045c41d
branch: main
repository: nandseqgen_v2
topic: "Uniform sampling for subsequent operation selection in sequence generation"
tags: [research, codebase, proposer, sequence, uniform, PRD]
status: complete
last_updated: 2025-09-08
last_updated_by: Codex
last_updated_note: "generate_seq_rules 내 uniform 적용 시점/정책 조사 결과 추가"
---

# 연구: 후속 operation 선정 시 uniform 샘플링 반영 방안

**Date**: 2025-09-08T00:13:00+09:00
**Researcher**: Codex
**Git Commit**: 466c7452461bd6dfa5bad9cb3836462ed045c41d
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
operation sequence 생성 과정에서 후속 operation을 선정하는 과정에서, PRD의 “groups_by_base에서 후보를 균등 확률로 선택” 요구가 현 구현에 반영되지 않았다. 이를 어떻게 개선할 수 있는가?

## 요약
- PRD v2는 sequence[probs]가 BASE를 가리키는 경우, `groups_by_base[BASE]`에서 후속 op_name을 균등(uniform) 샘플링하라고 명시한다.
  - docs/PRD_v2.md:332 — “후보를 uniform 확률로 샘플링하여 후속 operation 선택”.
- 현 구현은 `_choose_op_name_for_base`가 힌트(multi, same_celltype)로 필터 후 순서 의존적(first-match) 폴백을 사용한다. 균등 샘플링이 아니다.
  - proposer.py:837 — 비-SEQ 1스텝 확장에서 `_choose_op_name_for_base` 호출.
  - proposer.py:987 — 체인 전개 비-SEQ 분기에서도 동일 호출.
  - proposer.py:668 — `_choose_op_name_for_base`: 순차 탐색 후 첫 매치 또는 names[0] 폴백.
- 개선은 “후속 op_name 선택” 경로에 RNG 기반의 균등 선택을 도입하고, 재현성은 훅별 RNG 스트림을 그대로 사용하여 보장한다.

## 상세 발견

### PRD 요구사항 — 균등 샘플링 명시
- docs/PRD_v2.md:332 — sequence 생성 루틴 1): ‘.SEQ’가 아닌 key의 경우 `CFG[groups_by_base][key]` 후보를 uniform 확률로 샘플링해 후속 op_name을 정한다.

### Proposer의 현행 동작
- 비-SEQ 단일 스텝 전개(6-1 경로)
  - proposer.py:832 — base2 도출
  - proposer.py:835-837 — inherit 규칙으로 same_celltype/multi 힌트 도출 후 `_choose_op_name_for_base` 호출
  - proposer.py:668-683 — `_choose_op_name_for_base`는 필터 통과 첫 항목 또는 폴백 반환(비균등)
- SEQ 전개(generate_seq_rules)에서도 동일 이름 선택기 사용
  - proposer.py:907-919 — 단계별 base_i에 대해 `_choose_op_name_for_base`로 op_name 결정(균등 아님)

### 영향
- 동일 base에 다수의 op_name이 정의된 경우, 특정 이름이 항상 우선 선택되거나 YAML 선언 순서에 편향이 생긴다.
- PRD 요구(균등 샘플링)와 괴리되어 시퀀스 다양성이 저하될 수 있다.

## 개선안 비교

- 대안 A: 균등 선택 전용 헬퍼 추가(권장)
  - 내용: `_choose_op_name_for_base_uniform(cfg, base, multi, celltype, rng)`를 추가해 후보 필터링 후 `rng`로 균등 선택. `_expand_sequence_once`/`_expand_sequence_chain`(비-SEQ) 및 `_expand_sequence_seq`에서 이 함수를 사용.
  - 장점: 변경 범위가 국소적이고 PRD 요구를 정확히 반영. 동일 시드에서 결정성 유지.
  - 단점: 선택기 함수가 2종류로 분기(균등 vs 기존 폴백)되어 호출처 판단 필요.

- 대안 B: 기존 `_choose_op_name_for_base`를 균등 로직으로 변경
  - 내용: 함수 자체를 균등 샘플러로 바꾸고, 엄격 필터에 실패하면 ‘폴백 금지(=None)’로 전환. 호출부에서 None 처리.
  - 장점: 단일 선택기로 일원화.
  - 단점: 다른 경로(균등을 요구하지 않는 곳)까지 영향을 미칠 수 있음. 호환성 리스크.

- 대안 C: CFG로 개별 op_name을 probs에 직접 열거(운영상 해결)
  - 내용: `op_bases[base].sequence.probs`에 base가 아닌 op_name들을 명시하고 가중치를 부여.
  - 장점: 코드 변경 불필요, 선언적으로 제어.
  - 단점: PRD의 “base 단위 정의 + 균등” 원칙과 어긋남. 유지보수 비용 증가.

## 제안 구현(대안 A)

- 핵심 아이디어: 그룹 후보 산출 → 힌트로 필터 → 균등 선택.
- 코드 스케치(참고용):
  - proposer.py: 추가 함수
    - 입력: `cfg, base, multi:Optional[bool], celltype:Optional[str], rng`
    - 로직: `_op_names_by_base(cfg)[base]`에서 필터 → 후보 없으면 (multi/celltype 완화하여 2차 필터) → `idx = int((rng.random() or random()) * len(cands))`로 균등 선택.
  - 적용 지점:
    - proposer.py:837 — `_expand_sequence_once`의 `name2` 선택을 균등 버전으로 교체
    - proposer.py:986-987 — `_expand_sequence_chain` 비-SEQ 분기 동일 교체
    - proposer.py:905-919 — `_expand_sequence_seq` 단계별 이름 선택도 균등 버전 사용 권장

## 코드 참조
- `docs/PRD_v2.md:332` — 후속 op_name은 `groups_by_base`에서 uniform 샘플링
- `proposer.py:807` — `_expand_sequence_once` 비-SEQ 1스텝 전개 경로
- `proposer.py:936` — `_expand_sequence_chain` 비-SEQ 경로
- `proposer.py:844` — `_expand_sequence_seq` SEQ 전개 경로
- `proposer.py:668` — `_choose_op_name_for_base`(현재: 비균등, 순서 의존)

## 아키텍처 인사이트
- 균등 선택은 선언된 `op_names` 그룹의 다양성을 보장한다. RNG는 스케줄러가 주입한 훅별 스트림을 사용해 재현성을 유지한다.
- 이름 선택을 균등화하되, 유효성은 여전히 프리플라이트/Validator가 보장하므로 충돌 리스크는 증가하지 않는다.

## 관련 연구
- `research/2025-09-07_22-28-08_prd54_inherit_rules_impl.md` — inherit/SEQ 전개 흐름 정리
- `research/2025-09-07_23-41-59_op_chain_multi_consistency.md` — 체인 내 multi 일관성 이슈와 가드 방안

## 미해결 질문
- generate_seq_rules 단계에서도 항상 균등을 적용할지, 일부 베이스는 정책적으로 가중치/우선순위를 둘지 결정 필요. -> (결론) 기본은 균등, 필요 시 per‑key `name_weights`로 가중치 적용(적용 지점: proposer.py:908-919)
- 동일 base 내 다수 이름이 존재하지만 실제로 사용 불가한 변형(정책/자원 제약)이 있을 때, 균등 분모에서 제외하는 시점(선택 전 vs 프리플라이트 실패 후 재시도) 정의. -> (검토완료) 선택 전, 후보 샘플링 전 단계에서 제외.

## 후속 연구 2025-09-08T00:20:23+09:00 — generate_seq_rules 단계의 균등/가중치 적용 시점

요약 결론
- 기본 정책: `.SEQ` 전개(generate_seq_rules) 단계에서도 op_name 선택은 “균등”을 기본으로 적용한다. 즉, 각 단계의 `op_base`에 대해 `groups_by_base[op_base]` 후보를 힌트(multi, same_celltype)로 필터링한 뒤 균등 샘플링한다.
- 예외(정책적 가중치): 특정 베이스에서 제품/연구 목적상 변형별 우선순위를 두고자 할 때만 가중치(비균등)를 허용한다. 이때도 결정성(RNG) 유지.

구체적 적용 지점
- `.SEQ` 전개 로직: `proposer.py:908-919`에서 단계별 이름 선택 수행
  - 현재: `_choose_op_name_for_base(cfg, base_i, ...)`가 첫 매치/선언순 폴백(비균등)
  - 변경: 기본은 균등 선택기(예: `_choose_op_name_for_base_uniform`) 사용
  - 가중치 존재 시: 해당 단계에서만 가중치 분포로 샘플링

권장 설정 스키마(가중치 예외용)
```yaml
generate_seq_rules:
  CACHE_READ.SEQ:
    sequences:
      - CACHE_READ: ['same_page', 'same_celltype']
      - DOUT: ['prev_page', 'multi', 'same_celltype']
      - CACHE_READ_END: ['inc_page', 'same_celltype']
      - DOUT: ['same_page', 'multi', 'same_celltype']
    name_weights:            # 선택 사항(존재 시 균등 대신 사용)
      DOUT:                  # op_base 단위로 가중치 지정
        Enhanced_Random_Data_Output_Primary: 0.7
        Enhanced_Random_Data_Output_Secondary: 0.3
```

샘플링 규칙(가중치 병용 시)
- 후보 집합 C: `groups_by_base[base]`를 multi/celltype 힌트로 필터링한 결과
- 고정 질량 F: `name_weights[base]`에 명시된 이름들의 양수 값 합계(후보 외 이름은 무시)
- 합성 분포:
  - F 합계 ≥ 1: F를 정규화해 그 분포로만 샘플링
  - F 합계 < 1: F는 고정, 남은 후보(C \ keys(F))에 남은 질량(1-F)을 균등 분배 후 샘플링
- 모든 난수는 `rng`(훅별 스트림) 사용으로 재현성 유지

이유 및 근거
- PRD 명시적 균등 요구는 “비-.SEQ 분기(단일 후속)”에 대해 확정적이지만(`docs/PRD_v2.md:332`), `.SEQ` 단계는 순차 생성이며 per-base 이름 선택 가이드가 없다. 따라서 기본 균등을 채택해 편향 제거.
- 가중치가 필요하면 선언적으로 허용하되, 미지정 시 균등을 기본으로 해 YAML 순서 편향을 없앤다.

코드 참조
- `.SEQ` 전개 이름 선택: `proposer.py:908-919`
- 비-.SEQ 균등 요구: `docs/PRD_v2.md:332`
- generate_seq_rules 정의: `config.yaml:2174`

**추가 메모**
- multi-plane READ 체인의 DOUT 분할 정책은 시퀀싱(스케줄링) 문제로, 이름 선택 균등 여부와 독립(`proposer.py:1006-1030`).
