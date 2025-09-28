---
date: 2025-09-28T15:36:10+09:00
git_commit: 62176a93729cb0c34ee9f5a6d084b7c67ced9461
branch: main
repository: nandseqgen_v2
topic: "Scheduler._propose_and_schedule PROGRAM suspend targets"
tags: [research, codebase, scheduler, resourcemgr, config]
status: complete
last_updated: 2025-09-28
---

# 연구: Scheduler._propose_and_schedule에서 PROGRAM SUSPEND 대상

**Date**: 2025-09-28T15:36:10+09:00
**Git Commit**: 62176a93729cb0c34ee9f5a6d084b7c67ced9461
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
Scheduler._propose_and_schedule 에서 SUSPEND 대상이 되는 PROGRAM 계열 op_base 는 어떤 것이 선택이 되는가?

## 요약
Scheduler는 배치에 PROGRAM_SUSPEND가 포함되면 동일 die의 PROGRAM 축을 추적하여 해당 축에 매핑되는 모든 PROGRAM 계열 op_base를 backlog로 이동시켜 일시 중단한다. `_tracking_axis`가 "PROGRAM"을 반환하는 모든 op_base(이름에 PROGRAM이 들어가고 SUSPEND/RESUME이 아닌 것)가 대상이며, config의 PROGRAM 관련 베이스 전부가 여기에 해당한다. ResourceManager는 SUSPEND 시점에 중단된 op의 ID를 기록하고, Scheduler는 커밋 직후 이를 소비하여 OP_END를 취소하고 후속 RESUME 처리와 backlog 플러시를 조율한다.

## 상세 발견

### SCHEDULER 축 판별과 backlog 진입
- `scheduler.py:324` `_tracking_axis`가 "PROGRAM"을 반환하는 조건은 base 문자열이 PROGRAM을 포함하면서 SUSPEND/RESUME을 포함하지 않을 때이다. 따라서 모든 일반 PROGRAM 계열 base가 동일 축으로 분류된다.
- `scheduler.py:1082` 루프에서 각 제안된 op에 대해 `suspend_axes`를 조회하고, PROGRAM 축에 매칭되는 경우 `_create_backlog_entry`를 호출해 예약을 건너뛰면서 backlog에 적재한다. 이는 동일 die에서 활성화된 SUSPEND가 있을 때 PROGRAM 계열이 즉시 중단됨을 의미한다.
- `scheduler.py:1213` PROGRAM_SUSPEND가 예약되면 `suspend_axes[("PROGRAM", die)]`에 종료 시각과 hook 정보를 기록해 이후 op들이 중단 조건을 감지하도록 한다.

### PROGRAM 계열 base 정리
- config의 `op_bases` 섹션에는 PROGRAM 계열로 `PROGRAM_SLC`, `ALLWL_PROGRAM`, `CACHE_PROGRAM_SLC`, `ONESHOT_PROGRAM_LSB`, `ONESHOT_PROGRAM_CSB`, `ONESHOT_PROGRAM_MSB`, `ONESHOT_PROGRAM_MSB_23H`, `ONESHOT_PROGRAM_EXEC_MSB`, `ONESHOT_CACHE_PROGRAM` 등이 정의된다(`config.yaml:114-229`).
- 동일 섹션에 COPYBACK 파생과 CACHE 변형(`COPYBACK_PROGRAM_SLC`, `ONESHOT_COPYBACK_PROGRAM_*`)도 모두 PROGRAM을 포함하고 있어 `_tracking_axis` 조건을 충족한다(`config.yaml:439-483`).
- `program_base_whitelist`와 suspend 설정에서도 같은 명칭이 반복되어, 실행 시 whitelisting으로 인해 upper-case PROGRAM 이름 일관성이 보장된다(`config.yaml:49-56`).

### ResourceManager 연계
- ResourceManager는 SUSPEND 처리 시 `_suspend_transfers[(axis, die)]`에 중단된 op의 UID를 저장하여 Scheduler가 커밋 직후 회수할 수 있게 한다(`resourcemgr.py:1703-1709`).
- Scheduler는 PROGRAM_SUSPEND 커밋 시 `consume_suspended_op_ids("PROGRAM", die)`를 호출해 저장된 UID 목록을 받아 OP_END 이벤트를 취소한다(`scheduler.py:1268-1304`, `resourcemgr.py:1853-1860`). 이는 중단된 PROGRAM op가 실제로 종료 이벤트를 내지 않도록 보장한다.
- 이후 `_handle_resume_commit`에서 PROGRAM_RESUME가 들어오면 ResourceManager의 suspend 스택에서 동일 die의 meta를 꺼내 재예약하고, backlog를 재가동한다(`scheduler.py:766-842`).

## 코드 참조
- `scheduler.py:324-330` - PROGRAM 계열을 식별하는 `_tracking_axis` 조건
- `scheduler.py:1082-1145` - suspend_axes 조회와 PROGRAM 계열 backlog 적재 로직
- `scheduler.py:1213-1232` - PROGRAM_SUSPEND 커밋 시 suspend_axes 메타 저장
- `scheduler.py:1268-1304` - PROGRAM_SUSPEND 커밋 이후 suspend된 op ID 회수 및 OP_END 취소
- `config.yaml:114-229` - 주요 PROGRAM 계열 op_base 정의 목록
- `config.yaml:439-483` - COPYBACK 기반 PROGRAM 계열 정의
- `resourcemgr.py:1703-1709` - SUSPEND 시점에 중단된 op UID 수집
- `resourcemgr.py:1853-1860` - suspend된 op ID를 소비해 Scheduler로 반환

## 아키텍처 인사이트
PROGRAM 축의 SUSPEND 타깃 선정은 명시적 리스트가 아니라 문자열 규칙으로 결정된다. 새로운 PROGRAM 계열 base를 추가할 때 이름에 "PROGRAM" 을 포함시키면 자동으로 SUSPEND 관리 대상에 편입된다. 또한 ResourceManager와 Scheduler 간의 `_suspend_transfers` 채널을 통해 OP_END 취소와 backlog 재기동이 느슨하게 결합되어 있어, 축 기준의 확장성(ERASE/PROGRAM 분리)이 확보된다.

## 관련 연구
- 없음

## 미해결 질문
- 없음
