---
date: 2025-09-28T15:52:08+0900
git_commit: 62176a93729cb0c34ee9f5a6d084b7c67ced9461
branch: main
repository: nandseqgen_v2
topic: "Scheduler._propose_and_schedule 에서 CACHE_PROGRAM_SLC 는 SUSPEND 의 대상이 되지 않고 있는데, 그 이유를 research 해줘."
tags: [research, codebase, scheduler, resourcemgr, config]
status: complete
last_updated: 2025-09-28
---

# 연구: Scheduler._propose_and_schedule 에서 CACHE_PROGRAM_SLC 는 SUSPEND 의 대상이 되지 않고 있는데, 그 이유를 research 해줘.

**Date**: 2025-09-28T15:52:08+0900
**Git Commit**: 62176a93729cb0c34ee9f5a6d084b7c67ced9461
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
Scheduler._propose_and_schedule 에서 CACHE_PROGRAM_SLC 는 SUSPEND 의 대상이 되지 않고 있는데, 그 이유를 research 해줘.

## 요약
`CACHE_PROGRAM_SLC`는 스케줄러 레벨의 backlog 처리 전에 ResourceManager가 `state_forbid_suspend` 규칙으로 차단하기 때문에, `_propose_and_schedule`에서 SUSPEND 대기열로 이동하지 않는다. 구성 파일은 `PROGRAM_SUSPENDED` 상태에서 `CACHE_PROGRAM_SLC`를 명시적으로 금지하고 있어 `rm.reserve()` 단계에서 즉시 실패가 발생하며, 스케줄러는 이를 롤백으로 처리하고 backlog를 만들 기회를 갖지 못한다.

## 상세 발견

### Scheduler
- `Scheduler._propose_and_schedule`는 SUSPEND 이후 같은 batch에서 제안된 작업만 backlog로 넘길 수 있으며, 이후 batch에서는 ResourceManager 예약 결과에 의존해 차단된다(`scheduler.py:1083`, `scheduler.py:1091`, `scheduler.py:1162`).
- 예약 실패 시 `reserve_fail:*` 이유를 기록하고 전체 batch를 롤백하므로 해당 작업은 backlog 큐에 들어가지 않는다(`scheduler.py:1162`-`scheduler.py:1165`).

### ResourceManager
- `reserve()`는 `state_forbid_suspend` 규칙이 활성화되어 있으면 `PROGRAM_SUSPENDED` 상태에서 금지된 base를 즉시 거부한다(`resourcemgr.py:2298`-`resourcemgr.py:2303`).
- `state_forbid_suspend` 규칙은 현재 suspend 상태를 조회해 관련 exclusion group을 찾고, 대상 base가 포함되어 있으면 `state_forbid_suspend`를 반환한다(`resourcemgr.py:2390`-`resourcemgr.py:2415`).

### Config
- `CACHE_PROGRAM_SLC` base 는 `scope: "DIE_WIDE"`, `affect_state: true` 로 정의되어 일반 프로그램 축에 속한다(`config.yaml:136`-`config.yaml:155`).
- `exclusion_groups.program_suspended` 에 `CACHE_PROGRAM_SLC` 가 포함되어 suspend 상태에서 금지 대상으로 분류된다(`config.yaml:2173`-`config.yaml:2205`).
- `exclusions_by_suspend_state.PROGRAM_SUSPENDED` 는 `program_suspended` 그룹을 참조하여 ResourceManager 규칙 평가로 연결된다(`config.yaml:2317`-`config.yaml:2321`).

## 코드 참조
- `scheduler.py:1082` - suspend 축 후보와 backlog 진입 조건 계산
- `scheduler.py:1091` - `_tracking_axis`로 PROGRAM 축 판별
- `scheduler.py:1153` - `rm.reserve()` 호출 및 실패 시 롤백
- `resourcemgr.py:2298` - state_forbid_suspend 규칙 적용 진입점
- `resourcemgr.py:2398` - suspend 상태별 exclusion 그룹 평가
- `config.yaml:136` - `CACHE_PROGRAM_SLC` base 정의
- `config.yaml:2173` - `program_suspended` 그룹에 `CACHE_PROGRAM_SLC` 포함
- `config.yaml:2317` - suspend 상태별 exclusion 그룹 매핑

## 아키텍처 인사이트
Suspend 처리 로직은 스케줄러 backlog와 ResourceManager 규칙으로 이중화되어 있으며, `CACHE_PROGRAM_SLC`와 같이 config에서 금지된 base는 ResourceManager 단계에서 즉시 차단된다. 따라서 suspend 이후 동작을 backlog로 넘기고 싶다면 ResourceManager 룰세트나 exclusion 그룹 구성을 조정해야 한다.

## 관련 연구
- `research/2025-09-28_15-36-10_scheduler_program_suspend_targets.md`

## 미해결 질문
- suspend 이후 `CACHE_PROGRAM_SLC`를 재개하려면 exclusion 그룹 조정 외에 다른 우회 전략이 필요한지 검증 필요.
