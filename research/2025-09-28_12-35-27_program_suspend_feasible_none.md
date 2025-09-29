---
date: 2025-09-28T12:35:27+00:00
git_commit: ae616d76940b526facc4d790060caeaeefaf4d2c
branch: main
repository: nandseqgen_v2
topic: "Why Program_Suspend_Reset is rejected with feasible_none during ONESHOT_PROGRAM_MSB_23H.CORE_BUSY"
tags: [research, codebase, resourcemgr, scheduler, config]
status: complete
last_updated: 2025-09-28
---

# 연구: Program_Suspend_Reset feasible_none 현상

**Date**: 2025-09-28T12:35:27+00:00
**Git Commit**: ae616d76940b526facc4d790060caeaeefaf4d2c
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
ONESHOT_PROGRAM_MSB_23H.CORE_BUSY 상태에서 Program_Suspend_Reset 제안이 `feasible_none` 으로 거절되는 원인을 규명한다.

## 요약
- Program_Suspend_Reset 은 instant 예약 경로를 사용하지만, ResourceManager 의 `\_latch_ok` 검증에서 PROGRAM 계열 래치가 활성화돼 있으면 거절된다.
- LSB/CSB 단계가 설정한 `LATCH_ON_LSB`/`LATCH_ON_CSB` 가 `after_oneshot_program_*` 그룹을 통해 `PROGRAM_SUSPEND` 를 차단하고, 해당 래치는 MSB_23H 종료 이벤트가 호출될 때까지 유지된다.
- 실행 로그와 타임라인에서도 suspend 제안이 CORE_BUSY 동안 반복적으로 `feasible_none` 을 받다가 MSB_23H 종료 직후(phase key가 `.END` 로 넘어간 시점)에만 성공하는 것을 확인했다.

## 상세 발견

### ResourceManager 래치 검증
- `ResourceManager.feasible_at` 는 instant 경로에서도 `_latch_ok` 를 호출하며, 활성 래치가 매핑된 exclusion 그룹에 대상 베이스가 포함되면 `None` 을 반환한다(`resourcemgr.py:620`~`resourcemgr.py:668`).
- PROGRAM 계열 래치는 `PROGRAM_LATCH_KINDS` 로 한 번에 관리되며, `_latch_kind_for_base` 가 `ONESHOT_PROGRAM_LSB`/`CSB`/`MSB` 에 대해 각각 `LATCH_ON_*` 을 설정한다(`resourcemgr.py:2258`~`resourcemgr.py:2267`).

### 래치 상태와 config 매핑
- `exclusions_by_latch_state` 는 `LATCH_ON_LSB`/`CSB`/`MSB` 를 각각 `after_oneshot_program_lsb`/`csb`/`msb` 그룹에 연결한다(`config.yaml:2311`~`config.yaml:2315`).
- 각 그룹에는 `PROGRAM_SUSPEND` 가 명시돼 있어, LSB/CSB/MSB 진행 중에는 suspend 명령이 금지된다 (`config.yaml:1756`, `config.yaml:1813`, `config.yaml:1870`).
- Scheduler 는 `ONESHOT_PROGRAM_MSB_23H`/`EXEC` 종료 시점에 `release_on_exec_msb_end` 를 호출해 die 전체에서 PROGRAM 래치를 제거하므로, MSB_23H 완료 이전에는 래치가 유지된다(`scheduler.py:381`~`scheduler.py:386`, `resourcemgr.py:958`~`resourcemgr.py:962`).

### 재현 로그 관찰
- 재현 실행(`python3 main.py -t 50000`) 후 proposer 로그를 보면, `Program_Suspend_Reset` 제안이 CORE_BUSY 구간에서 반복적으로 `feasible_none` 으로 실패하다가 `now=2070.060` 시점(phase hook이 `ONESHOT_PROGRAM_MSB_23H.END` 로 전환된 직후)에 처음 `ok` 로 바뀐다(`out/proposer_debug_250928_0000001.log:120`~`out/proposer_debug_250928_0000001.log:160`).
- `op_event_resume` 타임라인에서도 MSB_23H `OP_END` 직후에만 `Program_Suspend_Reset` 이 시작되는 것을 확인할 수 있어, suspend 가 실시간 중단이 아니라 종료 이후에만 성립하고 있음을 뒷받침한다(`out/op_event_resume.csv:6`~`out/op_event_resume.csv:9`).

## 코드 참조
- `resourcemgr.py:620` - `_latch_ok` 가 활성 래치에 매핑된 exclusion 그룹을 검사해 요청을 거절한다.
- `resourcemgr.py:2258` - `ONESHOT_PROGRAM_LSB`/`CSB`/`MSB` 에 대해 `LATCH_ON_*` 래치 타입을 할당한다.
- `config.yaml:2313` - `LATCH_ON_LSB` → `after_oneshot_program_lsb` 매핑.
- `config.yaml:1756` - `after_oneshot_program_lsb` 그룹에 `PROGRAM_SUSPEND` 포함.
- `config.yaml:1813` - `after_oneshot_program_csb` 그룹에 `PROGRAM_SUSPEND` 포함.
- `config.yaml:1870` - `after_oneshot_program_msb` 그룹에 `PROGRAM_SUSPEND` 포함.
- `scheduler.py:381` - `ONESHOT_PROGRAM_MSB_23H` 종료 시 `release_on_exec_msb_end` 호출로 PROGRAM 래치 해제.
- `resourcemgr.py:958` - PROGRAM 래치 제거 구현.
- `out/proposer_debug_250928_0000001.log:120` - CORE_BUSY 중 반복된 `feasible_none` 시도와 `now=2070.060` 에서의 최초 성공 기록.
- `out/op_event_resume.csv:8` - MSB_23H 종료 직후 `Program_Suspend_Reset` 가 실행된 타임라인.

## 아키텍처 인사이트
Program suspend 는 LSB/CSB 단계가 세팅한 PROGRAM 래치가 해제되기 전까지 허용되지 않도록 구성돼 있다. 따라서 현재 config/ResourceManager 조합에서는 실질적인 mid-CORE suspend 가 불가능하며, suspend 기능은 사실상 MSB_23H 종료 이벤트 이후의 정리 동작으로만 동작한다. 중단 시점을 CORE_BUSY 구간 안으로 당기려면 래치 그룹에서 `PROGRAM_SUSPEND` 를 제외하거나, suspend 예약 시 래치 우회 정책을 별도로 정의해야 한다.

## 관련 연구
- `research/2025-09-26_14-11-29_suspend_resume_plane_scope.md`

## 미해결 질문
- PROGRAM_SUSPEND 을 실제 CORE_BUSY 중단 용도로 써야 한다면, 어느 타이밍에서 래치를 해제하거나 예외 처리할지 정책 결정이 필요하다.
- 래치 기반 차단을 유지하면서도 안전하게 suspend 를 허용할 대체 메커니즘(예: suspend 전용 래치 종류) 도입이 필요한지 검토해야 한다.
