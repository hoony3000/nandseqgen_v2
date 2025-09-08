---
title: suspend_state 분리 및 NOT_* 기본 그룹 적용 — 구현 계획
date: 2025-09-08
author: codex
source: research/2025-09-08_12-36-14_suspend_state_split_and_not_defaults.md
status: completed
---

# Problem 1‑Pager

- 배경: 현재 ResourceManager는 die 별 단일 suspend 상태(`_suspend_states[die]`)만 관리합니다. 활성 상태에서만(`*_SUSPENDED`) `exclusions_by_suspend_state` 매핑을 통해 차단 그룹을 적용합니다. 기본 상태(None)에서는 NOT_* 그룹이 적용되지 않아 대응되는 RESUME 동작이 후보로 제안될 수 있습니다.
- 문제: ERASE/PROGRAM 축이 혼재되어 있으며, 기본 상태에서 ‘not_erase_suspended’, ‘not_program_suspended’ 그룹이 적용되지 않습니다. 또한 오탈자(`PRGRAM_SUSPENDED`)와 `NESTED_SUSPEND` 같은 합성 상태가 혼란을 야기합니다.
- 목표:
  - ERASE/PROGRAM 두 축으로 suspend 상태를 분리하여 명확히 모델링한다.
  - 두 축 모두 기본 상태를 NOT_* 값으로 평가하여, 기본 상태에서도 대응 RESUME 류를 차단한다.
  - RM/Proposer 양쪽에서 두 축을 모두 평가해 차단 그룹의 합집합을 적용한다.
  - 스냅샷/복원에 분리된 축을 반영한다.
- 비목표: 기존 구성(config.yaml) 변경, 과거 스냅샷 호환 보장(필요 없음), `NESTED_SUSPEND` 유지.
- 제약: 함수 ≤ 50 LOC, 파일 ≤ 300 LOC 는 이미 지켜지고 있으며, 변경은 최소화한다. 테스트는 결정적이어야 한다.

# 설계 요약 (대안 비교)

- A) 기존 `suspend_states()`가 비활성 시에도 NOT_* 값을 리스트로 반환
  - 장점: 호출부 변경 최소
  - 단점: 반환 타입 불일치, 의미 모호
- B) 내부 상태 ERASE/PROGRAM 축 분리 + 새 질의 API 2개 + 호출부 2곳 수정
  - 장점: 명확/일관, 기본 NOT_* 자연스런 적용, 오탈자/중첩 제거
  - 단점: RM/Proposer 일부 수정 필요
- C) Proposer에서만 두 축 처리
  - 장점: 변경 범위 최소
  - 단점: 일관성 저하, RM 규칙과 불일치 위험

선택: B

# 변경 사항 상세

1) ResourceManager 내부 상태 분리
- 신규 구조:
  - `_erase_susp: Dict[int, Optional[_AxisState]]`
  - `_pgm_susp: Dict[int, Optional[_AxisState]]`
  - `_AxisState`: `die, state('ERASE_SUSPENDED'|'PROGRAM_SUSPENDED'), start_us, end_us`
- 커밋 훅 변경:
  - `ERASE_SUSPEND` → `_erase_susp[die] = ('ERASE_SUSPENDED', start)`
  - `PROGRAM_SUSPEND` → `_pgm_susp[die] = ('PROGRAM_SUSPENDED', start)`
  - `ERASE_RESUME` → `_erase_susp[die].end = end; _erase_susp[die] = None`
  - `PROGRAM_RESUME` → `_pgm_susp[die].end = end; _pgm_susp[die] = None`
  - `NESTED_SUSPEND` 제거, 오탈자 `PRGRAM_SUSPENDED` → `PROGRAM_SUSPENDED` 수정
- 새 쿼리 API:
  - `erase_suspend_state(die, at_us) -> 'ERASE_SUSPENDED' | 'NOT_ERASE_SUSPENDED'`
  - `program_suspend_state(die, at_us) -> 'PROGRAM_SUSPENDED' | 'NOT_PROGRAM_SUSPENDED'`
- 기존 `suspend_states()`는 하위호환용으로 유지(단일 활성 상태만 반환; 기본 None).

2) 규칙 적용 (두 축 합집합)
- RM: `_rule_forbid_on_suspend`에서 두 축 상태를 조회해 그룹을 합친 뒤 차단 여부 평가.
- Proposer: `_candidate_blocked_by_states`에서 두 축 상태를 조회(가능시 새 API, 폴백 시 기존 `suspend_states`).

3) 스냅샷/복원
- `snapshot()`에 `suspend_states_erase`, `suspend_states_program` 추가.
- `restore()`에서 새 키를 복원. 구 키(`suspend_states`)가 있으면 보조 폴백으로만 사용.

4) 테스트
- 기존 suspend 단일/중첩 테스트를 축 분리 모델로 갱신.
- 기본 상태에서 `NOT_*` 적용으로 `*_RESUME` 금지 회귀 테스트 추가.
- state_forbid_suspend 테스트를 새 키(`ERASE_SUSPENDED` 등)와 기본 NOT_*에 맞게 갱신.

# 구현 단계

1. RM에 축 상태/새 API/커밋 훅 수정 추가
2. RM 규칙: `_rule_forbid_on_suspend`를 두 축 합집합 평가로 변경
3. Proposer 후보 차단 로직에 두 축 평가 추가(폴백 포함)
4. 스냅샷/복원 경로 분리 키 추가(+구 키 폴백)
5. 테스트 업데이트 및 회귀 테스트 추가
6. pytest로 검증 후 필요시 보완

# 영향도/리스크
- 장점: 모델 명확화/통합, 기본상태 RESUME 차단 일관성, 오탈자/중첩 제거.
- 리스크: 테스트 갱신 필요, 스냅샷 키 변경으로 외부 소비자가 있다면 영향 가능(본 리포 내 소비만 있음).

# 검증 기준
- 기본 상태에서 `ERASE_RESUME`/`PROGRAM_RESUME`이 `state_forbid_suspend`로 차단.
- `ERASE_SUSPEND` 중에는 기존과 동일하게 관련 그룹 차단.
- 스냅샷/복원 후 동일 상태 재현.
