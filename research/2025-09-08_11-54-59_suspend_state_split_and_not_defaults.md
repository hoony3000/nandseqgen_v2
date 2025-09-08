---
date: 2025-09-08T11:54:59+0900
researcher: codex
git_commit: cba55e0dc0e35d8697e8aeac3f7004443a7f3a80
branch: main
repository: nandseqgen_v2
topic: "suspend_state 분리 및 NOT_* 기본 그룹 적용"
tags: [research, codebase, resourcemgr, proposer, config, suspend_state]
status: complete
last_updated: 2025-09-08
last_updated_by: codex
---

# 연구: suspend_state 분리 및 NOT_* 기본 그룹 적용

**Date**: 2025-09-08T11:54:59+0900
**Researcher**: codex
**Git Commit**: cba55e0dc0e35d8697e8aeac3f7004443a7f3a80
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
suspend_state 의 상태에 따라 제외할 operation group 을 선택하는 현재 로직에서, ERASE_SUSPEND/PROGRAM_SUSPEND 를 하나의 변수로서 관리하고 있는데, 이를 두 개로 분리하고 각각 기본 상태일 때는 ‘not_erase_suspended’, ‘not_program_suspended’ 그룹이 적용되도록 변경할 수 있는 방법은?

## 요약
- 현재 ResourceManager 는 die별 단일 `_suspend_states[die]` 에 상태 문자열을 보관하고, Proposer/RM 룰은 활성 상태(ERASE_SUSPENDED/PROGRAM_SUSPENDED 등)일 때만 차단 그룹을 적용한다. 기본(비활성) 상태에서는 그룹을 적용하지 않아 RESUME 제안이 허용되는 문제가 있다.
- 해결책: suspend 상태를 ERASE/PROGRAM 두 축으로 분리하여 각각의 쿼리 API에서 기본 상태를 명시적으로 반환하고(NOT_ERASE_SUSPENDED/NOT_PROGRAM_SUSPENDED), Proposer와 RM의 차단 로직이 두 축 모두를 평가해 그룹을 합집합으로 적용한다.
- 구성 변경: `exclusion_groups.not_program_suspended` 정의 추가(현재 없음), `not_erase_suspended` 중복 키 정리. 코드 변경: ResourceManager 내부 상태 분리 및 새 API 제공, Proposer/RM 룰에서 두 축을 모두 조회해 적용.

## 상세 발견

### 구성 매핑과 그룹 정의
- `config.yaml:2260`의 매핑은 네 가지 키를 이미 정의한다: `NOT_ERASE_SUSPENDED`, `ERASE_SUSPENDED`, `NOT_PROGRAM_SUSPENDED`, `PROGRAM_SUSPENDED`.
  - config.yaml:2260
  - config.yaml:2261
  - config.yaml:2263
  - config.yaml:2264
- 그룹 정의 측면에서 `erase_suspended`, `program_suspended`는 존재하나(`config.yaml:2064`, `config.yaml:2117`), `not_program_suspended` 그룹 정의가 없다. 또한 `not_erase_suspended`가 두 번 정의되어 마지막 정의가 덮어쓰는 중복 문제가 있다(`config.yaml:2115`, `config.yaml:2172`).

### 현재 런타임 상태 관리와 소비 지점
- 단일 상태 변수: ResourceManager는 `_suspend_states: Dict[int, Optional[_SuspState]]`로 단일 축 상태를 보관한다.
  - resourcemgr.py:104
- 커밋 훅에서 SUSPEND/RESUME 처리 로직:
  - ERASE_SUSPEND 예약 시: 상태를 `ERASE_SUSPENDED`로 설정
  - PROGRAM_SUSPEND 예약 시: (의도상) 중첩/NESTED 처리, 아니면 `PROGRAM_SUSPENDED` 설정. 현재 오탈자(`PRGRAM_SUSPENDED`)와 중첩 판정 비교값 오류 존재
  - RESUME 시: 상태 종료 후 None으로 리셋
  - resourcemgr.py:545
  - resourcemgr.py:547
  - resourcemgr.py:554
- 상태 질의 API: `suspend_states(die, at_us)`는 활성 구간에서만 상태 문자열을 반환하고, 비활성 시 `None`을 반환한다.
  - resourcemgr.py:732
- RM 차단 룰: 활성 상태일 때만 `exclusions_by_suspend_state[state]` 조회 후 차단한다. 비활성(None)인 경우 NOT_* 기본 그룹이 적용되지 않는다.
  - resourcemgr.py:1169
  - resourcemgr.py:1174
- Proposer 후보 차단: 활성 상태일 때만 동일 매핑을 적용한다.
  - proposer.py:396

### PRD/TODO 맥락
- PRD에 suspend 관리 개요와 예외 흐름이 정의되어 있음(ERASE/PROGRAM 공통 패턴).
  - docs/PRD_v2.md:364
  - docs/PRD_v2.md:367
- TODO에 정확히 본 변경 요청이 기록되어 있음: 두 상태 분리, NOT_* 키 활용, NESTED_SUSPEND 삭제.
  - docs/TODO.md:83

## 제안 설계(대안 비교)
- 대안 A: 기존 단일 `suspend_states()`를 비활성 시 `NOT_*` 값을 조합해 반환하도록 변경(예: 리스트나 튜플).
  - 장점: Proposer/RM 호출부 최소 변경
  - 단점: 기존 문자열 반환 타입과 호환되지 않음; 두 축 동시 표현이 애매(문자열로는 손실)
- 대안 B: RM 내부 상태를 ERASE/PROGRAM 두 축으로 분리하고, 새로운 쿼리 API 두 개를 제공. Proposer/RM 룰은 두 축을 모두 조회해 그룹 합집합을 적용. 기존 `suspend_states()`는 하위호환용으로 유지(가능하면 폐기 경로).
  - 장점: 명확한 모델링, NOT_* 기본 적용 자연스럽게 지원, 중첩/오탈자 문제 제거
  - 단점: 호출부 2곳(Proposer, RM 룰) 수정 필요
- 대안 C: Proposer에서만 두 축 로직을 흉내내고 RM은 그대로 두기.
  - 장점: 변경 범위 더 축소
  - 단점: Proposer/Validator와 RM 규칙 불일치 위험, 일관성 저하

선택: 대안 B(가장 단순·명확하며 일관성 보장)

## 변경안 세부

### 1) 구성 수정
- `exclusion_groups.not_program_suspended` 추가: 기본 상태에서 `PROGRAM_RESUME` 차단
  - 예: `not_program_suspended: ['PROGRAM_RESUME']`
- `exclusion_groups.not_erase_suspended` 중복 키 정리: 최종적으로 `['ERASE_RESUME']`만 유지

### 2) ResourceManager 내부 상태 분리
- 새 구조(개념):
  - `_erase_susp: Dict[int, Optional[_AxisState]]`
  - `_pgm_susp: Dict[int, Optional[_AxisState]]`
  - `_AxisState`: `die, state('ERASE_SUSPENDED'|'PROGRAM_SUSPENDED'), start_us, end_us`
- 커밋 훅 수정:
  - `ERASE_SUSPEND` → `_erase_susp[die] = ('ERASE_SUSPENDED', start)`
  - `ERASE_RESUME` → `_erase_susp[die].end = end; _erase_susp[die] = None`
  - `PROGRAM_SUSPEND` → `_pgm_susp[die] = ('PROGRAM_SUSPENDED', start)`
  - `PROGRAM_RESUME` → `_pgm_susp[die].end = end; _pgm_susp[die] = None`
  - NESTED_SUSPEND 제거
- 새 쿼리 API:
  - `erase_suspend_state(die, at_us) -> 'ERASE_SUSPENDED' | 'NOT_ERASE_SUSPENDED'`
  - `program_suspend_state(die, at_us) -> 'PROGRAM_SUSPENDED' | 'NOT_PROGRAM_SUSPENDED'`
- 하위호환: `suspend_states(die, at_us)`는 유지하되, 필요 시 순서 우선(예: ERASE 우선)으로 활성 한 축만 반환하거나 사용처 제거를 권장

### 3) 차단 로직 적용(두 축 합집합)
- RM: `ResourceManager._rule_forbid_on_suspend`
  - 기존 단일 `state = suspend_states(...)` 대신:
    - `es = erase_suspend_state(...)`, `ps = program_suspend_state(...)`
    - `groups = groups_by_state.get(es, []) + groups_by_state.get(ps, [])`
    - `_blocked_by_groups(base, groups)`로 판정
  - resourcemgr.py:1169
- Proposer: `_candidate_blocked_by_states`
  - 가능하면 덕 타이핑으로 새 API를 우선 호출, 실패 시 구형 `suspend_states()` 폴백
  - proposer.py:396 인근

### 4) 버그/정합성 정리
- 오탈자 수정: `PRGRAM_SUSPENDED` → `PROGRAM_SUSPENDED`
- 중첩 판정 로직 제거(두 축 분리로 불필요)
- 스냅샷/로드 경로 업데이트: `_suspend_states` 직렬화 대신 두 축(`erase`, `program`) 각각을 직렬화/복원

## 코드 참조
- `config.yaml:2260` - exclusions_by_suspend_state 키 4종 정의
- `config.yaml:2115` - exclusion_groups.not_erase_suspended(중복 1)
- `config.yaml:2172` - exclusion_groups.not_erase_suspended(중복 2)
- `resourcemgr.py:104` - 단일 `_suspend_states` 초기화
- `resourcemgr.py:545` - ERASE_SUSPEND 처리
- `resourcemgr.py:547` - PROGRAM_SUSPEND 처리(오탈자 포함)
- `resourcemgr.py:554` - ERASE/PROGRAM_RESUME 처리
- `resourcemgr.py:732` - `suspend_states(die, at_us)` 구현
- `resourcemgr.py:1169` - `_rule_forbid_on_suspend` 상태 차단 매핑 사용
- `proposer.py:396` - Proposer 후보 차단에서 suspend 상태 사용
- `docs/TODO.md:83` - 본 변경 요청 문맥 기록

## 아키텍처 인사이트
- 상태 축 분리는 PRD §5.5의 예외 처리(ERASE/PROGRAM 공통)와도 자연스럽게 부합하며, `NESTED_SUSPEND`처럼 합성 상태를 유지할 필요가 없다.
- 기본 상태를 명시적으로 표현(NOT_*)하면 구성 기반 차단(RESUME 금지)을 동형으로 취급할 수 있어 룰 일관성이 높아진다.
- 상태-그룹 매핑은 단일 소스(config.yaml)로 유지하되, 런타임은 다축 상태를 독립적으로 평가해 합집합을 적용한다.

## 역사적 맥락(thoughts/ 기반)
- TODO에 기록된 배경: "SUSPEND 수행 전에는 RESUME 금지"를 구성으로 집행하려는 요구. 현재 단일 상태 모델이 이를 방지하지 못함.
  - `docs/TODO.md:83` — 상태 분리, NOT_* 키 추가, NESTED_SUSPEND 삭제 제안

## 관련 연구
- PRD v2 suspend/resume 예외 흐름 설명: `docs/PRD_v2.md:364`, `docs/PRD_v2.md:367`

## 미해결 질문
- 하위호환 API(`suspend_states`)를 그대로 둘지, 호출부를 전면 교체할지? 제안은 유지하되 호출부에서 새 API 우선 사용.
- 스냅샷 포맷 변경 시 과거 스냅샷과의 호환을 어떻게 유지할지(마이그레이션 스텁 필요).
- `exclusion_groups.not_program_suspended`의 차단 대상 범위는 `PROGRAM_RESUME`만인지, 추가 제약이 필요한지 도메인 확인.

