---
title: "구현 계획: PRD 5.4 Proposer Step 6 — inherit 규칙 및 SEQ 전개"
date: 2025-09-07
based_on: research/2025-09-07_22-28-08_prd54_inherit_rules_impl.md
status: draft
owners: ["Codex"]
---

# Problem 1-Pager

- 배경: PRD 5.4의 Proposer Step 6 구현이 부분 상태. 6.1(비‑SEQ 1단계 전개)은 동작하지만, 6.2(SEQ 다단계 전개)와 `inherit` 규칙의 일부(`prev_page`, `pgm_same_page`, `same_page_from_program_suspend`, `same_reg`)가 미/부분 구현 상태.
- 문제: `CFG.generate_seq_rules`에 정의된 시퀀스를 전개하지 못해 목표 연쇄(예: `CACHE_READ.SEQ`, `RECOVERY_RD.SEQ`)가 생성되지 않음. `inherit` 규칙도 `same_page`/`inc_page` 중심의 축약 처리로 의미 차이가 반영되지 않음.
- 목표
  - G1: SEQ 다단계 전개기 추가. `generate_seq_rules[key].sequences` 전체를 순서대로 생성.
  - G2: `inherit` 규칙의 의미 차이를 구현: `prev_page`, `pgm_same_page`, `same_page_from_program_suspend`(+ 기존 `same_page`/`inc_page`/`same_plane`/`same_celltype`/`multi`).
  - G3: READ→DOUT 멀티‑플레인 분할 및 순차 스케줄(계획 문서 준수).
  - G4: 결정성/성능/관찰성 유지 및 회귀 최소화.
- 비목표
  - RM의 배제/타임라인 정책 변경.
  - `same_reg` 구현(현 범위 제외 결정).
- 제약
  - 함수 ≤ 50 LOC, 파일 ≤ 300 LOC, 복잡도 ≤ 10 유지(필요 시 분리).
  - RNG는 주입된 `rng`만 사용. 시스템 시간 금지.

# 변경 개요(Where & What)

- proposer.py
  - `proposer.py:692` `_seq_inherit` 그대로 유지(SEQ 선택 확률 → 상속규칙 매핑 추출 용도).
  - `proposer.py:708` `_targets_with_inherit`: 간단 규칙 유지하고, 확장 규칙은 신규 헬퍼로 분리.
  - `proposer.py:731` `_expand_sequence_once`: 선택 결과가 `*.SEQ`면 다단계 전개기로 위임.
  - 신규: `_expand_sequence_seq(...)` — `generate_seq_rules` 기반 전체 체인 생성.
  - 신규: `SeqCtx` — `pgm_same_page`/`same_page_from_program_suspend` 등 컨텍스트 제공.
  - 신규: `_apply_inherit_rules(rules, prev_targets, ctx)` — 확장 상속 규칙 적용.
  - `proposer.py:768` `_preflight_schedule`: DOUT 계열 분할/시간 정렬(기획 문서 적용). 정책 플래그로 on/off.
  - `proposer.py:1009` `propose(...)`: 1단계 전개 → 다단계 전개 호출 경로 연계 및 로깅 보강.

- config.yaml
  - `generate_seq_rules`는 현존. 추가 정책 플래그만 도입.
  - 신규 `policies.split_dout_per_plane: true`(기본 on).

- resourcemgr.py
  - 변경 없음. `suspended_ops(die)` 조회만 사용(이미 존재: `resourcemgr.py:780`).

# 상세 설계

## 1) SEQ 다단계 전개기

- 함수 시그니처: `_expand_sequence_seq(cfg, first_name, first_targets, hook, res_view, rng) -> List[Tuple[str, List[Address]]]`
  - 입력 `choice_key`: `_expand_sequence_once`에서 선택된 키(예: `CACHE_READ.SEQ`).
  - 동작: `cfg['generate_seq_rules'][choice_key]['sequences']` 순회를 통해 (base_i, rules_i) 목록을 얻고, 각 단계별로 op_name/targets를 생성하여 전체 연쇄를 반환.
  - op_name 선택: `_choose_op_name_for_base`에 `multi`/`same_celltype` 힌트를 전달.
  - targets 상속: `_apply_inherit_rules`로 직전 단계 타겟과 컨텍스트에서 파생.
  - 컨텍스트: `SeqCtx`를 업데이트하며 최신 PROGRAM 계열 타겟을 추적.
  - 안전장치: `policies.maxloop_seq` 상한(이미 존재)을 재사용하여 과도 전개 방지.

## 2) inherit 규칙 해석 컨텍스트(SeqCtx)

- 구조 예시: `SeqCtx { first_name, first_targets, last_step_targets, last_program_targets, plane_set, celltype, die, planes_hint, suspended_program_targets }`
  - `first_*`: 최초 스텝 값 바인딩.
  - `last_step_targets`: 직전 스텝 타겟(기본 상속 기준).
  - `last_program_targets`: 체인 내부 최신 PROGRAM/COPYBACK_PROGRAM_SLC 단계에서 갱신.
  - `suspended_program_targets`: `res_view.suspended_ops(die)` 최신 PROGRAM 계열 타겟(RECOVERY_RD.SEQ에서 사용).

## 3) 상속 규칙 적용기(_apply_inherit_rules)

- 입력: `rules: List[str]`, `prev_targets: List[Address]`, `ctx: SeqCtx`
- 처리:
  - 공통: `same_plane`(기본)은 prev_targets의 plane 집합을 보존.
  - `same_page`: `prev_targets`의 page 유지.
  - `inc_page`: page+1; None이면 0 처리.
  - `prev_page`: page-1; 하한 0.
  - `pgm_same_page`: `ctx.last_program_targets`에서 page 상속. 없으면 `prev_targets`로 폴백.
  - `same_page_from_program_suspend`: `ctx.suspended_program_targets` 우선, 없으면 `prev_targets`로 폴백.
  - `same_celltype`: op_name 선택 힌트만. 주소 변경 없음.
  - `multi`: op_name 선택 힌트 + 다음 단계 DOUT 분할에 반영.
  - `none`: 무시.
- 출력: 새 타겟 리스트(Address 복제; 불변성 유지).

## 4) DOUT 계열 분할 및 시간 정렬

- 위치: `_preflight_schedule(...)` 내부.
- 조건: 두 번째 이후 op의 base ∈ {DOUT, DOUT4K, CACHE_READ_END, PLANE_CACHE_READ_END} ∧ 첫 op의 plane_set 길이 > 1 ∧ `CFG.policies.split_dout_per_plane == true`.
- 동작: 첫 op의 plane 순서대로 DOUT류를 plane 단위로 분할하여 체인을 확장(READ 1 + DOUT N). `policies.sequence_gap` 적용.
- 유효성: 단일 실패 시 전체 preflight 실패(원자성 유지). 관찰 로그 1줄.
- 근거: `plan/2025-09-07_dout_exclusion_multi_fix_plan.md`.

# 구현 단계(Tasks)

1) 헬퍼 추가
   - [proposer.py:692] 근방에 `SeqCtx` dataclass/typed‑dict 도입.
   - [proposer.py:708] `_apply_inherit_rules` 신규 추가(기존 `_targets_with_inherit`는 후방 호환 유지).

2) SEQ 전개기
   - [proposer.py:731] `_expand_sequence_once`에서 `choice.endswith('.SEQ')`면 `_expand_sequence_seq` 호출로 전환.
   - [proposer.py:731] 기존 비‑SEQ 경로는 변경 없이 유지.
   - [proposer.py:731] `_expand_sequence_seq` 구현: `generate_seq_rules[choice]['sequences']` 파싱 → 단계별 (name_i, targets_i) 생성.

3) inherit 규칙 확장
   - [proposer.py:708] `_targets_with_inherit`는 기존 동작 유지(단일/간단 경로).
   - [proposer.py] 확장 규칙(`prev_page`/`pgm_same_page`/`same_page_from_program_suspend`)은 `_apply_inherit_rules`로 처리.

4) DOUT 분할/정렬
   - [proposer.py:768] `_preflight_schedule`에 분할 로직 추가(정책 플래그 연동, 관찰 로그).

5) 구성(설정)
   - [config.yaml:18] `policies.split_dout_per_plane: true` 추가.
   - 필요 시 `features.*` 변경 없음.

6) 로깅/관찰성
   - proposer: 선택된 phase_key/분할 여부/분할 개수/스케줄 요약 로그 유지(기존 `_log` 사용).

7) 테스트
   - 유닛: `_apply_inherit_rules`
     - same/inc/prev_page 경계(0, None), pgm_same_page(내부 PROGRAM 없음→폴백), same_page_from_program_suspend(없음→폴백) 결정적 검증.
   - 통합: SEQ 전개
     - `CACHE_READ.SEQ`: READ → DOUT(prev_page, multi) → CACHE_READ_END(inc_page) → DOUT(same_page, multi). 기대 체인과 page/plane 매칭 검증.
     - `RECOVERY_RD.SEQ`: PROGRAM_SUSPEND 이후 `suspended_ops` 기반 타겟 상속 확인.
   - 스케줄: 멀티 READ → plane별 DOUT 분할 및 순차 예약(중첩 없음, exclusion_multi 없음).

# 대안 비교

- A) 기존 `_targets_with_inherit` 시그니처 확장(rules+targets+ctx)
  - 장점: 호출지 변경 최소.
  - 단점: 기존 단순 경로와 의미가 혼합되어 복잡도 상승.
- B) 신규 `_apply_inherit_rules` 추가(선택)
  - 장점: 단순 경로 보존, 확장 규칙은 명확히 분리. 테스트 용이.
  - 단점: 유사 함수 2개 존재(주석/문서로 역할 구분 필요).
→ 선택: B(신규 헬퍼 추가) — 변경 영향 최소화 및 가독성 우위.

# 수용 기준(AC)

- AC1: `CACHE_READ.SEQ`/`RECOVERY_RD.SEQ`가 `config.yaml` 정의대로 정확한 순서/상속으로 전개됨.
- AC2: READ 멀티‑플레인 후속 DOUT가 plane별로 분할되어 예약 성공(`exclusion_multi` 0건, 시간 중첩 없음).
- AC3: `pgm_same_page`는 체인 내부 최신 PROGRAM 기준으로 page 상속; 외부 참조 없음.
- AC4: `same_page_from_program_suspend`는 `res_view.suspended_ops(die)` 최신 PROGRAM에서 page 상속. 없으면 폴백 동작 검증.
- AC5: 모든 새 테스트 결정적으로 통과.

# 위험과 완화

- 위험: 분할/정렬로 인해 연쇄 시각이 늘어나 처리량 소폭 감소 가능.
  - 완화: 정책 플래그로 토글, 기본 on. 영향 경미 예상(선형 증가).
- 위험: 상속 규칙 오해석으로 비의도 주소 생성.
  - 완화: 유닛 테스트로 경계/폴백 명시 검증. 로깅 강화.

# 롤아웃

1) 기능 개발 → 로컬 시뮬 돌려 `out/` 로그 육안 점검.
2) 테스트 추가/통과 확인.
3) 플래그 on 상태로 머지. 필요 시 디버그 로그 톤다운.

# 참고(파일/라인)

- proposer: `proposer.py:692`, `proposer.py:708`, `proposer.py:731`, `proposer.py:768`, `proposer.py:1009`
- config: `config.yaml:2140`, `config.yaml:2191` (generate_seq_rules 예시), `config.yaml:18`(policies 루트)
- PRD: `docs/PRD_v2.md:330`, `docs/PRD_v2.md:332`, `docs/PRD_v2.md:340`
- RM suspend 조회: `resourcemgr.py:780`
- 관련 계획: `plan/2025-09-07_dout_exclusion_multi_fix_plan.md`

