---
date: 2025-09-28T18:23:53+0900
git_commit: c8943647d1769f821d1973acd6441cc4d3f6a4f2
branch: main
repository: nandseqgen_v2
topic: "CACHE_PROGRAM_SLC 를 대상으로 하는 SUSPEND 시, DATAIN state 는 resource, state 가 제거되지 않아 op_state_timeline*csv 에 그대로 기록되는데 원인을 연구해줘"
tags: [research, codebase, resourcemgr, config]
status: complete
last_updated: 2025-09-28
---

# 연구: CACHE_PROGRAM_SLC 를 대상으로 하는 SUSPEND 시, DATAIN state 는 resource, state 가 제거되지 않아 op_state_timeline*csv 에 그대로 기록되는데 원인을 연구해줘

**Date**: 2025-09-28T18:23:53+0900
**Git Commit**: c8943647d1769f821d1973acd6441cc4d3f6a4f2
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
CACHE_PROGRAM_SLC 를 대상으로 하는 SUSPEND 시, DATAIN state 는 resource, state 가 제거되지 않아 op_state_timeline*csv 에 그대로 기록되는데 원인을 연구해줘

## 요약
ResourceManager 는 예약 단계에서 전체 state 목록을 한 번에 타임라인에 적재하고, SUSPEND 커밋 시 `CORE_BUSY` 세그먼트만 잘라내도록 제한된 필터를 사용한다. `CACHE_PROGRAM_SLC` 는 `DATAIN` 상태를 추가로 가지므로, suspend 시점 이후에 예정된 `DATAIN` 구간이 제거되지 않고 그대로 남아 CSV 로 내보내진다. 동일한 필터가 plane/die 배타 창도 완전히 정리하지 못해 snapshot 상에서도 `DATAIN` 구간이 지속된다.

## 상세 발견

### ResourceManager Timeline Truncation Filters
- 커밋 시 `_st.reserve_op` 는 각 plane 에 대해 전체 state 시퀀스를 선형으로 배치한다(`resourcemgr.py:843`).
- SUSPEND 처리는 `_pred` 를 통해 `CORE_BUSY` 이고 suspend/resume 가 아닌 base 만 제거하도록 한정되어 있다(`resourcemgr.py:927`, `resourcemgr.py:929-935`).
- 결과적으로 `CORE_BUSY` 로 매칭되지 않는 `DATAIN` 세그먼트는 잘리지 않고 유지된다.

### Config 정의에 포함된 DATAIN 단계
- `CACHE_PROGRAM_SLC` base 는 ISSUE, CORE_BUSY 에 더해 `DATAIN` 상태를 동일한 die scope 로 예약한다(`config.yaml:136-155`).
- 따라서 suspend 이전에 예약된 타임라인에는 항상 `DATAIN` 세그먼트가 뒤따르며, 상기 필터 제한으로 인해 삭제 대상에서 제외된다.

### Export 및 Snapshot 증거
- 실행 산출물인 `op_state_timeline` CSV 에서 suspend 직후에도 `CACHE_PROGRAM_SLC.DATAIN` 구간이 그대로 남아 있다(`out/op_state_timeline_250928_0000001.csv:26-29`).
- 동일 시점 snapshot 의 plane reservation 목록 역시 해당 작업의 종료 시각까지 유지되어 suspend 시점에서 truncate 되지 않았음을 확인했다(`out/snapshots/state_snapshot_20250928_160522_0000001.json:1`).

## 코드 참조
- `resourcemgr.py:843` – `_st.reserve_op` 가 전체 state 시퀀스를 타임라인에 기록
- `resourcemgr.py:929` – suspend 시 `_pred` 가 `CORE_BUSY` 상태만 제거 대상으로 한정
- `resourcemgr.py:1597` – `move_to_suspended_axis` 가 plane/window 정리를 시도하지만 원 구간을 그대로 사용
- `config.yaml:136` – `CACHE_PROGRAM_SLC` 에 `DATAIN` 상태가 정의되어 있음
- `out/op_state_timeline_250928_0000001.csv:26` – suspend 후에도 남아 있는 `CACHE_PROGRAM_SLC.DATAIN` 행
- `out/snapshots/state_snapshot_20250928_160522_0000001.json:1` – plane reservation 이 작업 종료 시각까지 유지된 사례

## 아키텍처 인사이트
타임라인과 자원 윈도우는 예약 시점에 완전한 상태 시퀀스를 선형으로 작성하고, 나중 조정은 세그먼트 유형 기반 필터에 의존한다. Suspend/Resume 관련 처리는 `CORE_BUSY` 중심으로 설계되어 있어, 추가 상태가 있는 프로그램류(base)에서는 truncate 정책을 확장하지 않으면 잔여 상태가 누적된다. 재사용 가능한 predicate 구성이나 state 목록 기반 일반화를 도입해야 각 base 의 특수 상태를 안정적으로 제거할 수 있다.

## 관련 연구
- `research/2025-09-28_18-00-11_cache_program_slc_suspend_resume_states.md`

## 미해결 질문
- Suspend 시 `DATAIN` 을 비롯한 후속 상태 세그먼트를 일반화하여 제거할 필요가 있는지, 혹은 특정 base 에서만 허용해야 하는지 정책 결정이 필요하다. -> (검토완료) 일반화하여 제거 필요
- `move_to_suspended_axis` 의 창 정리 로직을 state 이름이 아닌 예약된 duration 기반으로 재검토하면 plane/bus 윈도우 누수를 줄일 수 있는지 확인해야 한다. ->(검토완료) duration 기반 제거 및 RESUME 시 재등록 필요.
