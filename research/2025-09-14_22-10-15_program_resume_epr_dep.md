---
date: 2025-09-14T22:09:58+09:00
researcher: codex
git_commit: f375d829ceebe7e2beaa6367ac3d4a27ee3e2cd9
branch: main
repository: nandseqgen_v2
topic: "PROGRAM_RESUME 체인에서 epr_dep로 인해 suspended_ops_program 재스케줄 실패 원인"
tags: [research, codebase, scheduler, resource-manager, suspend-resume, epr, addr-dep]
status: complete
last_updated: 2025-09-14
last_updated_by: codex
---

# 연구: PROGRAM_RESUME 체인에서 epr_dep로 인해 suspended_ops_program 재스케줄 실패 원인

**Date**: 2025-09-14T22:09:58+09:00
**Researcher**: codex
**Git Commit**: f375d829ceebe7e2beaa6367ac3d4a27ee3e2cd9
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
PROGRAM_RESUME 시 `suspended_ops_program`에 저장된 operation이 다시 스케줄링되지 않고, 체인 단계에서 `[chain] post-commit reserve_fail reason=epr_dep`로 거절되는 현상의 근본 원인은 무엇인가?

## 요약
- 직접 원인: post-commit 체인에서 생성된 "CORE_BUSY 잔여 스텁"이 원래 PROGRAM 계열 base/targets로 `ResourceManager.reserve(...)` 검증을 통과해야 하는데, 주소 의존 규칙(addr_dep, EPR)이 활성화되어 있어 `addrman.check_epr`가 실패를 반환하면서 `epr_dep`로 거절된다.
- 규칙 트리거 가능성: PROGRAM 계열에 대해 다음이 유력
  - `epr_programs_on_same_page`: 동일 (die, block, page) 조합이 다중 타겟(plane 무시)으로 중복될 경우 실패. 체인 스텁이 다중 plane 대상으로 동일 page를 재개하려 할 때 해당.
  - `epr_program_before_erase`: 대상 block의 유효 상태가 ERASE(-1)가 아닌 경우 실패. 특정 타이밍/이전 OP_END 반영에 따라 발생 가능.
- 배경 조건: 기본 설정이 addr_dep 활성화 상태이며 EPR 통합이 켜져 있다. 따라서 체인 스텁도 일반 예약과 동일하게 EPR 검사를 받는다.

## 상세 발견

### 체인 예약 흐름(Scheduler)
- *_RESUME 배치 커밋 후, 마지막 suspended 메타에서 잔여 CORE_BUSY 시간을 스텁으로 구성해 바로 예약을 시도한다.
  - `scheduler.py:595` — 체인 스텁 예약 실패 시 `[chain] post-commit reserve_fail reason=...` 출력
  - 스텁 구성 및 예약 호출은 `_build_core_busy_stub(base, rem_us)` + `rm.reserve(txn2, stub, targets, Scope.PLANE_SET)` 경로에서 수행됨.

### 규칙 평가(ResourceManager)
- 모든 예약은 `_eval_rules(stage="reserve", ...)`를 거친다. addr_dep가 활성화되면 `addr_policy`(EPR) 콜백을 호출하고, 실패 시 `reason="epr_dep"`로 거절한다.
  - `resourcemgr.py:1440` — `_eval_rules` 진입점
  - `resourcemgr.py:1539` — 실패 메타 기록: `failed_rule: "epr_dep"`
  - `resourcemgr.py:1545` — `(False, "epr_dep")` 반환 -> 예약 실패 사유로 전파

### EPR 규칙(AddressManager)
- PROGRAM 계열 스텁에 대해 아래 두 규칙이 특히 영향 크다.
  - `epr_programs_on_same_page`: 동일 (die, block, page) 조합의 다중 프로그램 금지. plane 구분 없이 중복을 검사한다.
    - `addrman.py:1155`, `addrman.py:1157`, `addrman.py:1178`
  - `epr_program_before_erase`: block 상태가 ERASE(-1)가 아니면 프로그램 금지.
    - `addrman.py:1121`, `addrman.py:1123`, `addrman.py:1131`
- 체인 스텁은 원본 PROGRAM 타겟을 그대로 사용하므로, 다중 plane 동일 page 재개나, 선행 OP_END 반영 상태에 따라 위 규칙이 트리거될 수 있다.

### 구성(설정) 게이트 확인
- addr_dep/EPR는 기본적으로 활성화되어 있어 모든 예약(체인 스텁 포함)에 적용된다.
  - `config.yaml:39` — `features.suspend_resume_chain_enabled: true`
  - `config.yaml:4623` — `constraints.enabled_rules: ["state_forbid", "addr_dep"]`
  - `config.yaml:4626` — `constraints.enable_epr: true`

## 코드 참조
- `scheduler.py:595` — post-commit 체인 예약 실패 로그 출력
- `resourcemgr.py:1440` — 예약 시 규칙 평가 진입점
- `resourcemgr.py:1539` — `failed_rule: epr_dep` 메타 기록
-, `resourcemgr.py:1545` — `epr_dep` 사유로 예약 거절 반환
- `addrman.py:1121` — PROGRAM on non-ERASE 금지 규칙 시작
- `addrman.py:1155` — 동일 page 중복 프로그램 금지 규칙 시작
- `config.yaml:39` — 체인 기능 플래그
- `config.yaml:4623` — addr_dep 규칙 활성화
- `config.yaml:4626` — EPR 사용

## 아키텍처 인사이트
- 체인 스텁은 "기존 프로그램의 연속 수행"이지만, 현재는 "새로운 PROGRAM 예약"으로 동일하게 검증된다. 이로 인해 주소 의존 규칙이 체인에도 그대로 적용되어 `epr_dep`로 차단될 수 있다.
- 특히 `epr_programs_on_same_page`가 plane 차원을 무시하고 (die, block, page)로만 중복을 판정하기 때문에, 다중 plane 동일 page를 대상으로 하는 PROGRAM 연속 수행이 체인 단계에서 거절될 여지가 있다.
- 규칙 타이밍 상, 원본 PROGRAM의 OP_END 시점에 AddressManager 상태가 갱신되며(스케줄러 측 `OP_END` 훅), 체인 예약 시점 상태와의 불일치가 `epr_program_before_erase`를 자극할 수 있다.

## 관련 연구
- `research/2025-09-14_20-59-24_program_resume_reschedule.md` — PROGRAM_RESUME 체인 프리체크 스킵/래치 실패 분석(비-EPR)

## 미해결 질문
- 체인 스텁을 EPR에서 예외로 취급(continuation)할 것인지, 아니면 EPR 규칙(예: 동일 page 중복 판정)을 plane-aware하게 수정할 것인지 정책 결정 필요.
- 체인 예약 시 op_name/celltype을 충분히 전달하여 규칙의 오탐을 줄일지 여부.
- 프로그램 연속 수행에 한해 EPR 검증을 "한 번만" 수행하고 체인 구간은 면제할지에 대한 논의.

