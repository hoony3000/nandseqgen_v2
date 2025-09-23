---
date: 2025-09-23T11:18:51.663249+09:00
researcher: Codex
git_commit: 052170ac4547f550742ad40107123138e500f49a
branch: main
repository: nandseqgen_v2
topic: "SUSPEND 동작 시 기존에 예약됐던 ERASE/PROGRAM 동작 ResourceManager.commit, ResourceManager.reserve 의해 resource 예약, state 변경(e.g bus, latch_state)이 발생하는데, 어떤 부분이 SUSPEND 동작으로 바뀌고, 바뀌지 않는지 모두 research 해줘."
tags: [research, codebase, ResourceManager, Scheduler]
status: complete
last_updated: 2025-09-23
last_updated_by: Codex
---

# 연구: SUSPEND 동작 시 기존에 예약됐던 ERASE/PROGRAM 동작 ResourceManager.commit, ResourceManager.reserve 의해 resource 예약, state 변경(e.g bus, latch_state)이 발생하는데, 어떤 부분이 SUSPEND 동작으로 바뀌고, 바뀌지 않는지 모두 research 해줘.

**Date**: 2025-09-23T11:18:51.663249+09:00
**Researcher**: Codex
**Git Commit**: 052170ac4547f550742ad40107123138e500f49a
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
SUSPEND 동작 시 기존에 예약됐던 ERASE/PROGRAM 동작 ResourceManager.commit, ResourceManager.reserve 의해 resource 예약, state 변경(e.g bus, latch_state)이 발생하는데, 어떤 부분이 SUSPEND 동작으로 바뀌고, 바뀌지 않는지 모두 research 해줘.

## 요약
- PROGRAM_SUSPEND/ERASE_SUSPEND 은 cfg 에서 `instant_resv` 로 표시되어 Reserve 단계에서 버스만 재예약하고 기존 plane/die 윈도우와 배타 토큰은 그대로 유지된다.
- Commit 시에는 축 구분된 suspend 상태를 열고, 진행 중 OP 메타를 `_suspended_ops_*` 로 이동시키며 CORE_BUSY 타임라인을 잘라내지만, 기존 plane/bus 예약과 래치는 해제되지 않는다.
- Scheduler 는 ERASE/PROGRAM 에만 `_tracking_axis` 를 부여해 suspend 동작 자체를 새로운 ongoing 으로 등록하지 않으며, ResourceManager 의 `program_suspend_state`/`erase_suspend_state` 를 통해 외부에서 상태를 조회한다.
- latch/addr_state/배타윈도우 등은 suspend 에 의해 변경되지 않아 중단된 OP 의 리소스 점유가 재개 시점까지 남는다.

## 상세 발견

### ResourceManager.reserve (instant suspend path)
- cfg 에서 `PROGRAM_SUSPEND` 의 `instant_resv: true` 로 인해 Reserve 는 즉시 경로를 타며 버스 충돌만 검사한다(`config.yaml:578`).
- instant 경로는 "Only reserve bus segments" 주석 그대로 `txn.bus_resv` 와 `txn.st_ops` 만 채우고 plane/die 배타 윈도우나 latch 는 추가하지 않는다(`resourcemgr.py:499`, `resourcemgr.py:513`, `resourcemgr.py:516`, `resourcemgr.py:524`).
- `_latch_kind_for_base` 가 suspend 베이스를 반환하지 않아 latch 상태는 변하지 않고, `_update_overlay_for_reserved` 역시 ERASE/PROGRAM_SLC 에만 반응하여 addr_state 도 그대로다(`resourcemgr.py:1445`, `resourcemgr.py:1461`).

### ResourceManager.commit (suspend axis bookkeeping)
- Commit 은 txn 내용 전체를 반영하되 suspend 예약에서는 plane_resv 가 비어 있으므로 새 plane 윈도우가 추가되지 않고 기존 값이 유지된다(`resourcemgr.py:581`).
- `PROGRAM_SUSPEND` 분기에서 축 전용 상태를 열고 `_suspended_ops_program` 으로 meta 를 이동, 잔여 시간을 계산한다(`resourcemgr.py:629`, `resourcemgr.py:642`, `resourcemgr.py:1092`).
- 동일 분기에서 방금 이동한 meta 의 타깃 plane 목록을 이용해 이전 PROGRAM CORE_BUSY 세그먼트를 suspend 시각 이후로 잘라 잔여 시간을 보존한다(`resourcemgr.py:654`, `resourcemgr.py:661`).
- Commit 은 suspend 자원 해제 로직을 두지 않아 기존 plane/bus/latch/exclusion 은 유지되고, suspend 자체의 버스 구간만 `_bus_resv` 에 추가된다(`resourcemgr.py:587`).

### Scheduler 및 상태 노출
- Scheduler 는 `_tracking_axis` 로 ERASE/PROGRAM 만 ongoing 메타 등록 대상으로 삼아 suspend 동작을 독립 실행으로 취급하지 않는다(`scheduler.py:250`, `scheduler.py:742`).
- suspend 후 상태는 ResourceManager 의 `program_suspend_state`/`erase_suspend_state` 를 통해 조회되며, RESUME 커밋이 끝나야 `_pgm_susp`/`_erase_susp` 가 해제된다(`resourcemgr.py:885`, `resourcemgr.py:677`).
- Suspend 규칙 문서는 기존 ERASE/PROGRAM OP_END 를 즉시 중단하고 remaining 기반으로 RESUME 해야 함을 명시하며, 현재 구현은 타임라인 truncate + meta 이동을 통해 이를 반영한다(`docs/SUSPEND_RESUME_RULES.md:1`).

## 코드 참조
- `config.yaml:578` - PROGRAM_SUSPEND scope/instant_resv/states 정의.
- `resourcemgr.py:499` - instant reserve 경로 진입 조건과 버스/룰 검사.
- `resourcemgr.py:513` - instant 경로가 bus segment 만 예약함.
- `resourcemgr.py:516` - latch-kind 가 없으면 래치 미생성.
- `resourcemgr.py:629` - Commit 시 suspend 축 상태 오픈.
- `resourcemgr.py:642` - `move_to_suspended_axis` 호출로 meta 이동.
- `resourcemgr.py:661` - CORE_BUSY 타임라인 truncate 로직.
- `resourcemgr.py:587` - suspend 의 버스 예약이 `_bus_resv` 로 합쳐짐.
- `resourcemgr.py:1092` - `move_to_suspended_axis` 잔여 시간 계산과 스택 이동.
- `resourcemgr.py:885` - program suspend 상태 조회 API.
- `scheduler.py:250` - suspend 베이스가 `_tracking_axis` 대상이 아님.
- `scheduler.py:742` - ongoing 메타 등록이 axis 존재할 때만 실행.
- `docs/SUSPEND_RESUME_RULES.md:1` - suspend/resume 규칙 기대치.

## 아키텍처 인사이트
- suspend 를 instant 경로로 정의한 덕분에 버스만 소비하고 plane 예약은 변하지 않지만, 그 결과 중단된 작업의 plane/die 배타윈도우가 유지되어 동일 die/plane 에 다른 작업이 끼어들 수 없다.
- CORE_BUSY 타임라인을 잘라내면서도 plane 예약을 유지하는 설계는 재개 시 남은 시간 계산을 ResourceManager 메타에 맡기고, 스케줄러는 RESUME 및 후속 stub 으로 잔여 실행을 복원한다.
- latch/addr_state 를 건드리지 않기 때문에 suspend 는 관측 상태만 변경하며 데이터 경합 위험을 최소화한다.

## 역사적 맥락(thoughts/ 기반)
- `docs/SUSPEND_RESUME_RULES.md:1` - suspend 시 종료 이벤트 무기한 연장 및 remaining 기반 재개 요구사항.
- `research/2025-09-22_01-07-35_repeat_suspend_remaining_us_regression.md` - remaining_us 누적 문제와 suspend/resume 체인 관찰 기록.

## 관련 연구
- `research/2025-09-22_00-22-10_resume_stub_rework.md`
- `research/2025-09-22_01-07-35_repeat_suspend_remaining_us_regression.md`

## 미해결 질문
- plane_resv 를 truncate 하지 않는 설계가 장시간 suspend 시 스케줄링 병목을 유발하지 않는지 추가 확인 필요.
- suspend 동안 addr_state / exclusion overlay 가 stale 해도 문제가 없는지 시나리오 검증이 요구된다.
