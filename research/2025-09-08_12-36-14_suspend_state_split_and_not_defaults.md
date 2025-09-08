---
date: 2025-09-08T12:36:14+0900
researcher: codex
git_commit: cba55e0dc0e35d8697e8aeac3f7004443a7f3a80
branch: main
repository: nandseqgen_v2
topic: "suspend_state 분리 및 NOT_* 기본 그룹 적용"
tags: [research, codebase, resourcemgr, proposer, config, suspend_state]
status: complete
last_updated: 2025-09-08
last_updated_by: codex
last_updated_note: "미해결 질문 3(기본 그룹 범위) 후속 연구 추가"
---

# 연구: suspend_state 분리 및 NOT_* 기본 그룹 적용

**Date**: 2025-09-08T12:36:14+0900
**Researcher**: codex
**Git Commit**: cba55e0dc0e35d8697e8aeac3f7004443a7f3a80
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
suspend_state 의 상태에 따라 제외할 operation group 을 선택하는 현재 로직에서, ERASE_SUSPEND/PROGRAM_SUSPEND 를 하나의 변수로 관리하고 있습니다. 이를 두 개로 분리하고, 기본 상태에서는 각각 ‘not_erase_suspended’, ‘not_program_suspended’ 그룹이 적용되도록 변경할 수 있는 방법은 무엇인가?

## 요약
- 현재 RM은 die별 단일 `_suspend_states[die]` 문자열 상태로만 관리하며, 활성 상태(ERASE_SUSPENDED/PROGRAM_SUSPENDED 등)인 경우에만 차단 그룹을 적용합니다. 기본 상태(None)에서는 NOT_* 그룹이 적용되지 않아 RESUME 제안이 가능해집니다.
- 해결: ERASE/PROGRAM 두 축으로 상태를 분리하고, 각 축의 기본 상태를 명시적 값(NOT_ERASE_SUSPENDED/NOT_PROGRAM_SUSPENDED)으로 평가해 두 축 모두의 그룹을 합집합으로 적용합니다.
- 구성은 이미 네 가지 키 매핑을 갖추었습니다. 코드 측면에서 RM 내부 상태 분리, 질의 API 추가, RM/Proposer 차단 로직을 두 축으로 평가하도록 변경하면 됩니다.

## 상세 발견

### 구성 매핑과 그룹 정의
- `config.yaml:2261` `NOT_ERASE_SUSPENDED: ['not_erase_suspended']`
- `config.yaml:2262` `ERASE_SUSPENDED: ['erase_suspended']`
- `config.yaml:2263` `NOT_PROGRAM_SUSPENDED: ['not_program_suspended']`
- `config.yaml:2264` `PROGRAM_SUSPENDED: ['program_suspended']`
- 그룹 정의 예시:
  - `config.yaml:2115` `not_erase_suspended:` → 기본적으로 `['ERASE_RESUME']`
  - `config.yaml:2172` `not_program_suspended:` → 기본적으로 `['PROGRAM_RESUME']`

### 현재 런타임 상태 관리와 소비 지점
- 단일 상태 변수 초기화: `resourcemgr.py:104`
- 커밋 훅에서 SUSPEND/RESUME 처리(단일 축):
  - ERASE_SUSPEND → 상태를 `ERASE_SUSPENDED`로 설정: `resourcemgr.py:546`
  - PROGRAM_SUSPEND → 의도상 `PROGRAM_SUSPENDED` 설정이나 오탈자(`PRGRAM_SUSPENDED`) 존재, 중첩 판정 비교값 오류: `resourcemgr.py:547`, `resourcemgr.py:550-553`
  - RESUME(ERASE/PROGRAM 공통) → 종료 및 None 리셋: `resourcemgr.py:554-558`
- 상태 질의 API: 비활성 시 None 반환(기본 상태 미표현): `resourcemgr.py:732-741`
- RM 차단 룰: 활성 상태에서만 `exclusions_by_suspend_state[state]` 적용: `resourcemgr.py:1168-1181`
- Proposer 후보 차단: 활성 상태에서만 동일 매핑 적용: `proposer.py:392-398`

### PRD/TODO 맥락
- PRD에 suspend/resume 처리 개요와 예외 흐름: `docs/PRD_v2.md:491`
- TODO: 본 변경 요구가 명시됨 — 두 상태 분리, NOT_* 키 활용, NESTED_SUSPEND 삭제: `docs/TODO.md:81-83`

## 제안 설계(대안 비교)
- 대안 A: 기존 `suspend_states()`가 비활성 시에도 NOT_* 값을 함께 반환(예: 리스트/튜플)
  - 장점: 호출부 변경 최소화
  - 단점: 반환 타입 불일치(문자열→컬렉션), 두 축 동시 표현의 의미 애매
- 대안 B: 내부 상태를 ERASE/PROGRAM 두 축으로 분리하고, 새로운 쿼리 API 두 개를 제공. RM/Proposer는 두 축 모두를 평가해 그룹을 합집합으로 적용. 기존 `suspend_states()`는 하위호환용으로 유지.
  - 장점: 명확한 모델링, NOT_* 기본 적용 자연스럽게 지원, 중첩·오탈자 문제 해소
  - 단점: 호출부(2곳) 일부 수정 필요
- 대안 C: Proposer에서만 두 축 로직을 구현, RM은 유지
  - 장점: 변경 범위 축소
  - 단점: RM/Proposer 불일치 위험, 일관성 저하

선택: B(명확·일관, 리스크 낮음)

## 변경안 세부

### 1) ResourceManager 내부 상태 분리
- 구조:
  - `_erase_susp: Dict[int, Optional[_AxisState]]`
  - `_pgm_susp: Dict[int, Optional[_AxisState]]`
  - `_AxisState`: `die, state('ERASE_SUSPENDED'|'PROGRAM_SUSPENDED'), start_us, end_us`
- 커밋 훅 수정(단일 축 로직 교체):
  - `ERASE_SUSPEND` → `_erase_susp[die] = ('ERASE_SUSPENDED', start)`
  - `ERASE_RESUME` → `_erase_susp[die].end = end; _erase_susp[die] = None`
  - `PROGRAM_SUSPEND` → `_pgm_susp[die] = ('PROGRAM_SUSPENDED', start)`
  - `PROGRAM_RESUME` → `_pgm_susp[die].end = end; _pgm_susp[die] = None`
  - `NESTED_SUSPEND` 제거, 오탈자 `PRGRAM_SUSPENDED` → `PROGRAM_SUSPENDED` 수정
- 새 쿼리 API:
  - `erase_suspend_state(die, at_us) -> 'ERASE_SUSPENDED' | 'NOT_ERASE_SUSPENDED'`
  - `program_suspend_state(die, at_us) -> 'PROGRAM_SUSPENDED' | 'NOT_PROGRAM_SUSPENDED'`
- 스냅샷/복원:
  - `snapshot()`에 `suspend_states` 대신 `suspend_states_erase`, `suspend_states_program` 두 키로 직렬화: `resourcemgr.py:874`
  - `restore()`도 동일하게 분리된 키를 복원: `resourcemgr.py:927-934`

### 2) 룰 적용(두 축 합집합)
- RM: `ResourceManager._rule_forbid_on_suspend`를 두 축으로 평가하도록 변경
  - `es = erase_suspend_state(...)`, `ps = program_suspend_state(...)`
  - `groups = groups_by_state.get(es, []) + groups_by_state.get(ps, [])`
  - `_blocked_by_groups(base, groups)`로 판정
- Proposer: `_candidate_blocked_by_states`에서 동일하게 두 축을 조회
  - 새 API가 없으면(하위호환) 기존 `suspend_states()` 경로로 폴백

### 3) 구성 영향도
- `exclusions_by_suspend_state`는 그대로 사용(네 가지 키 이미 존재).
- 그룹 정의는 기본 상태에서 `RESUME` 류를 차단하는 구성이 반영되어 있음:
  - `not_erase_suspended: ['ERASE_RESUME']` — `config.yaml:2115`
  - `not_program_suspended: ['PROGRAM_RESUME']` — `config.yaml:2172`

## 코드 참조
- `resourcemgr.py:104` — 단일 `_suspend_states` 초기화
- `resourcemgr.py:545-558` — SUSPEND/RESUME 상태 반영(단일 축 및 오탈자)
- `resourcemgr.py:732-741` — `suspend_states(die, at_us)` 구현
- `resourcemgr.py:1168-1181` — `_rule_forbid_on_suspend`(활성 상태에서만 차단)
- `proposer.py:392-398` — Proposer의 suspend 기반 후보 차단
- `config.yaml:2261` — NOT_ERASE_SUSPENDED 매핑
- `config.yaml:2263` — NOT_PROGRAM_SUSPENDED 매핑

## 아키텍처 인사이트
- 두 축 분리로 `NESTED_SUSPEND` 같은 합성 상태가 불필요해지고, 상태 표현과 룰 적용이 단순해집니다.
- 기본 상태를 NOT_*로 명시하여 구성 기반 정책(RESUME 금지)을 일관되게 집행할 수 있습니다.
- 스냅샷 포맷은 가급적 새 키를 추가하고, 구형 `suspend_states` 키는 읽기 전용 호환 경로로 유지하는 것이 안전합니다.

## 관련 연구
- 유사 주제 선행 문서: `research/2025-09-08_11-54-59_suspend_state_split_and_not_defaults.md`

## 미해결 질문
- 하위호환 API(`suspend_states`)를 계속 유지할지, 호출부를 전면 교체할지? -> (검토완료) 전면 교체
- 과거 스냅샷과의 호환(마이그레이션/폴백) 범위를 어디까지 보장할지? - (검토완료) 과거 스냅샷 고려 불필요.
- 기본 그룹에서 차단할 대상(특히 `not_program_suspended`)의 범위가 도메인 합의와 일치하는지? -> (후속연구) 아래 참조

## 후속 연구 2025-09-08T12:36:14+0900

질문: 기본 그룹에서 차단할 대상(특히 `not_program_suspended`)의 범위가 도메인 합의와 일치하는가?

요약 결론: 기본 상태에서 금지되어야 하는 것은 대응되는 RESUME 동작뿐이다. 즉, `not_program_suspended`는 `['PROGRAM_RESUME']`만 포함하면 충분하며, ERASE 축과 대칭이다. 현재 구성(`config.yaml:2172-2173`)이 이와 일치한다.

근거:
- PRD의 SUSPEND→RESUME 흐름은 두 동작의 짝 관계를 강조하며, SUSPEND 이전에는 RESUME를 예약할 수 없어야 함을 암시한다.
  - `docs/PRD_v2.md:364-369`, `docs/PRD_v2.md:476-477`
- 구성 값 확인:
  - `config.yaml:2115-2116` — `not_erase_suspended: ['ERASE_RESUME']`
  - `config.yaml:2172-2173` — `not_program_suspended: ['PROGRAM_RESUME']`
  - `config.yaml:2261-2264` — `exclusions_by_suspend_state` 4종 키 매핑 존재
- 다른 후보들의 필요성:
  - `RECOVERY_RD.SEQ`는 `PROGRAM_SUSPEND.sequence` 경로에서만 선택되므로 기본 상태에서는 제안 자체가 되지 않는다(별도 차단 불필요). `config.yaml:555-566`, `research/2025-09-07_22-28-08_prd54_inherit_rules_impl.md:52`

코드 적용 관점:
- 두 축 평가 도입 후 RM/Proposer는 기본 상태에서도 `NOT_PROGRAM_SUSPENDED`에 매핑된 그룹을 적용하여 `PROGRAM_RESUME`를 차단한다.
  - RM 규칙: `resourcemgr.py:1168-1181`
  - Proposer 필터: `proposer.py:390-398`

최종 정리: `not_program_suspended`의 차단 대상은 `PROGRAM_RESUME`만으로 충분하다. 추가 차단은 도메인/구현 상 불필요하며 ERASE 축과 동일한 패턴을 유지한다.
