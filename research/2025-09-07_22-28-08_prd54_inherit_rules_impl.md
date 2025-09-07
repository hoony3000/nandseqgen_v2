---
date: 2025-09-07T22:28:08+0900
researcher: Codex
git_commit: 6b8638740683e8462da97574fee57becff2f3d64
branch: main
repository: nandseqgen_v2
topic: "PRD 5.4 Proposer step 6 — inherit rule implementation plan for operation sequence"
tags: [research, codebase, proposer, generate_seq_rules, inherit]
status: complete
last_updated: 2025-09-07
last_updated_by: Codex
last_updated_note: "미해결 질문 해소 및 구현 방안 보정"
---

# 연구: PRD 5.4 Proposer step 6 — inherit 생성 규칙 구현 방안

**Date**: 2025-09-07T22:28:08+0900
**Researcher**: Codex
**Git Commit**: 6b8638740683e8462da97574fee57becff2f3d64
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
operation sequence 를 만들 때 PRD 5.4 Proposer → 6번 → inherit 생성 규칙 모두를 구현하기 위한 방안을 research

## 요약
- 현행 구현은 PRD 5.4의 “6) sequence 생성 루틴” 중 6.1(비 SEQ 레이블: 다음 base 하나 선택 + inherit 일부 반영)까지만 처리한다.
- 6.2(SEQ 레이블: `CFG[generate_seq_rules][key][sequences]`를 펼쳐 다단계 시퀀스를 생성)는 미구현이다.
- 또한 `inherit` 규칙 중 `prev_page`, `pgm_same_page`, `same_page_from_program_suspend`, `same_reg` 등은 부분/미구현 상태다.
- 구현 방안: Proposer에 SEQ 전개기 추가, inherit 규칙 해석을 위한 컨텍스트(최근 PROGRAM 페이지, 멀티‑플레인 plane set, suspended_ops 등) 보강, DOUT 계열 plane별 분할(splitting) 및 preflight 스케줄 정합성 강화.

## 상세 발견

### Proposer 구현 현황
- 단일 확장(비 SEQ) 처리:
  - `proposer.py:750` `_expand_sequence_once`: `sequence[probs]`에서 가중치 선택 → base 추출 → `same_celltype`/`multi` 반영 → `_targets_with_inherit`로 타겟 상속 → 1개 후속 op 반환.
- inherit 파서/타겟 상속:
  - `proposer.py:692` `_seq_inherit`: `inherit` 섹션을 맵으로 정규화.
  - `proposer.py:708` `_targets_with_inherit`: `same_page`/`inc_page`(및 동치 처리)만 적용. `prev_page`/`pgm_same_page`/`same_page_from_program_suspend` 의미는 실제로 분기 구분 없이 “same_page”로 취급됨.
- SEQ 다단계 전개: 미구현
  - PRD 5.4의 “6.2 ‘... 두 번째 원소가 SEQ 라면 ... generate_seq_rules ...’” 경로가 코드에 없음.
- DOUT plane 분할: 미구현
  - 계획 문서에 설계가 있음: `plan/2025-09-07_dout_exclusion_multi_fix_plan.md`.

### CFG/PRD 스펙 포인트
- PRD 5.4 Proposer — Step 6 요지: 
  - 6.1: 선택된 `sequence[probs]`가 `BASE`라면 `groups_by_base[BASE]`에서 op_name 선택 후 `inherit` 적용.
  - 6.2: `...SEQ`라면 `CFG[generate_seq_rules][key][sequences]` 전체를 순서대로 생성하고, 각 항목의 “inherit 생성 규칙”으로 타겟/속성을 상속.
- `config.yaml` 정의 예:
  - `READ.sequence.probs: { DOUT: 0.9, CACHE_READ.SEQ: 0.1 }` (`config.yaml:200` 부근)
  - `generate_seq_rules.CACHE_READ.SEQ.sequences` 존재, DOUT에 `prev_page` 명시 (`config.yaml:2191` 부근)
  - `PROGRAM_SUSPEND.sequence` → `RECOVERY_RD.SEQ` + `same_page_from_program_suspend` (`config.yaml:566` 근방)

### 갭 분석(미구현/부족)
- SEQ 전개기 부재: `generate_seq_rules`에 따른 다단계 시퀀스 생성 미지원.
- inherit 규칙 범위 부족:
  - `prev_page`: 미지원
  - `pgm_same_page`: “직전 program의 page” 해석 필요(현재는 직전 op page로 대체됨)
  - `same_page_from_program_suspend`: RM.suspended_ops 기반 주소 상속 필요(현재 미사용)
  - `same_reg`: Address 스키마에 없음. 비‑EPR 계열(SET/GET) 메타 상속 설계 필요.
- DOUT ‘multi’ 상속에 따른 plane별 분할 및 시간 정렬: 계획만 있고 구현 전.

## 구현 방안(제안)

### 1) SEQ 전개기 추가
- 함수: `_expand_sequence_seq(cfg, first_name, first_targets, hook, res_view, rng) -> List[Tuple[str, List[Address]]]`
  - 입력 `choice_key='BASE.SEQ'`를 사용, `cfg['generate_seq_rules'][choice_key]['sequences']` 순회를 통해 [(base_i, rules_i)...]를 획득
  - 각 단계마다:
    - op_name 선택: `_choose_op_name_for_base`에 `multi`/`same_celltype` 힌트 적용
    - 타겟 상속: `rules_i`를 해석하여 이전 스텝 타겟/컨텍스트에서 생성
  - 출력: (첫 op 포함) 전체 체인 반환
- propose 루틴 연계:
  - `_expand_sequence_once`에서 `choice`가 `*.SEQ`이면 `_expand_sequence_seq` 호출로 교체
  - `ops_chain`을 전체 체인으로 설정 후 `_preflight_schedule` 호출

### 2) inherit 규칙 해석 컨텍스트(SeqCtx) 도입
- 구조: `SeqCtx { first_name, first_targets, last_program_targets, plane_set, celltype, die, planes_hint, suspended_program_targets }`
- 채우는 법:
  - `first_*`는 최초 후보에서 설정
  - `last_program_targets`: 체인 내 직전 PROGRAM류의 타겟을 업데이트
  - `suspended_program_targets`: `getattr(res_view, 'suspended_ops', ...)`로 조회하여 die별 마지막 PROGRAM 계열 타겟 획득(RECOVERY_RD.SEQ용)
- 규칙 대응:
  - `same_page`: 직전 스텝 타겟의 page 유지
  - `inc_page`: page+1 (None이면 0)
  - `prev_page`: page-1 (하한 0)
  - `pgm_same_page`: 체인 내부 최신 PROGRAM 기준으로 `ctx.last_program_targets`에서 page 상속(체인 외부 조회는 하지 않음). 없으면 직전 스텝 타겟 fall‑back
  - `same_page_from_program_suspend`: `ctx.suspended_program_targets`(= `res_view.suspended_ops(die)`의 최신 PROGRAM) 사용, 없으면 직전 스텝 타겟 fall‑back
  - `same_celltype`: 이름 선택 힌트로만 사용(타겟 수정 없음)
  - `multi`: 이름 선택 힌트(+ 후속 DOUT 분할 정책과 결합)
  - `same_plane`: 기본적으로 직전 타겟의 plane set 유지(현 구현도 동일 plane 세트를 복제함)
  - `same_reg`: 현재 범위 제외(미구현 유지). 필요 시 ProposedOp.meta/hook.meta 경계로 전달하는 방식을 채택 예정.

### 3) DOUT 계열 분할 및 시간 정렬
- `_preflight_schedule` 확장(계획 문서 준수):
  - 조건: 두 번째 이후 op의 base ∈ {DOUT, DOUT4K, CACHE_READ_END, PLANE_CACHE_READ_END} ∧ 첫 op의 plane_set 길이 > 1 ∧ `CFG[policies][split_dout_per_plane] == true`
  - 동작: plane별로 DOUT류를 분할하여 체인 확장(READ 1 + DOUT N), `sequence_gap` 적용
  - 유효성: 아무 하나라도 불가능하면 전체 preflight 실패(원자성 유지)

### 4) ResourceManager/Interface 보강(비침투적)
- Proposer는 duck‑typing으로 다음을 옵션 호출:
  - `res_view.suspended_ops(die)` → 마지막 PROGRAM 계열 타겟 획득
  - 기존 사용 중인 `odt_state()/suspend_states()/cache_state()`와 동일 패턴(try/except) 적용
  - 추가 확장 불필요(이미 `resourcemgr.py`에 `suspended_ops` 존재, `resourcemgr.py:720` 부근)

### 5) 안전/결정성/성능
- 결정성: RNG는 입력된 `rng`만 사용, 시스템 시간 미사용(현행 유지)
- 윈도우 정책: 첫 op만 `admission_window` 검사(현행 유지)
- 성능: preflight는 소수 op 체인만 다루며, 분할은 plane 수 만큼의 선형 증가 → 영향 경미

## 코드 참조
- `proposer.py:692` — `_seq_inherit`: inherit 섹션 정규화
- `proposer.py:708` — `_targets_with_inherit`: same/inc page 상속(확장 필요)
- `proposer.py:750` — `_expand_sequence_once`: 비 SEQ 전개, SEQ 분기 추가 지점
- `proposer.py:818` — `_preflight_schedule`: 체인 스케줄, DOUT 분할 로직 추가 지점
- `proposer.py:1060` — `propose`: 시퀀스 전개/사전검증 진입점
- `config.yaml:2173` — `generate_seq_rules` 루트
- `config.yaml:2191` — `CACHE_READ.SEQ` sequences + `prev_page` 규칙
- `docs/PRD_v2.md:330` — Step 6 진입
- `docs/PRD_v2.md:332` — 6.1 비 SEQ 분기
- `docs/PRD_v2.md:340` — 6.2 SEQ 분기

## 아키텍처 인사이트
- PRD의 분포(phase_conditional)는 후보 op_name 선택, 시퀀스 전개는 별도의 규칙(generate_seq_rules)로 분리되어야 유지보수성이 좋다.
- inherit 규칙은 주소(page)만 다루지 않는다(same_reg 등). 주소 상속과 메타 상속을 구분해 경계면(ProposedOp.meta/hook.meta)로 전달하는 것이 확장에 안전하다.
- DOUT 분할은 RM의 배제 규칙을 바꾸지 않고도 충돌을 해소하는 설계로, 스케줄러/프로포저 경계에서 해결하는 것이 적절하다.

## 역사적 맥락(thoughts/ 기반)
- 계획 문서: `plan/2025-09-07_dout_exclusion_multi_fix_plan.md` — READ→DOUT 시간 중첩/멀티 충돌 해결을 위한 분할/시각 정렬 제안.

## 관련 연구
- research/2025-09-07_07-58-08_read_not_scheduled_due_to_dout_exclusion_multi.md (참조됨)

## 결정 사항(미해결 질문 해소)
- `same_reg`: (검토완료) 현재 불필요. 구현 범위에서 제외한다.
- `pgm_same_page`: (검토완료) 체인 밖은 참조하지 않는다. 동일 제안 체인 내 가장 가까운 과거 PROGRAM의 targets을 사용해 page 상속한다.
- `same_page_from_program_suspend`: (검토완료) `ResourceManager._suspended_ops`를 상속한다. 구현은 `res_view.suspended_ops(die)`에서 최신 PROGRAM 계열의 targets를 취해 사용한다.

## 후속 연구 2025-09-07T22:28:08+0900
- 구현 방안 보정 요약
  - SEQ 전개기: 유지(변경 없음)
  - inherit 규칙: `pgm_same_page` 체인 내부 참조로 확정, `same_page_from_program_suspend`는 `res_view.suspended_ops` 사용으로 확정, `same_reg`는 범위 제외로 확정
  - 코드 가이드: `SeqCtx.last_program_targets`를 체인 생성 중 PROGRAM/COPYBACK_PROGRAM_SLC 단계에서 최신으로 갱신. `SeqCtx.suspended_program_targets`는 `res_view.suspended_ops(die)`를 조회하여 최신 항목으로 설정.
  - 부정 해소: RM.timeline/overlay 조회는 도입하지 않음(성능/복잡도 고려)
  - 참고 코드: `resourcemgr.py:747`(suspended_ops), `resourcemgr.py:720` 근방 상태 관리
