---
date: 2025-09-28T18:00:11.003449+09:00
git_commit: c8943647d1769f821d1973acd6441cc4d3f6a4f2
branch: main
repository: nandseqgen_v2
topic: "CACHE_PROGRAM_SLC suspend/resume state coverage"
tags: [research, codebase, resourcemgr, scheduler, config]
status: complete
last_updated: 2025-09-28
---

# 연구: CACHE_PROGRAM_SLC suspend/resume state coverage

**Date**: 2025-09-28T18:00:11.003449+09:00
**Git Commit**: c8943647d1769f821d1973acd6441cc4d3f6a4f2
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
CACHE_PROGRAM_SLC 를 대상으로 하는 SUSPEND->RESUME 반복 시나리오에서, CACHE_PROGRAM_SLC 의 CORE_BUSY, DATAIN state 모두 SUSPEND, RESUME 의 대상이 되는지

## 요약
- `PROGRAM_SUSPEND` 트리거는 `CACHE_PROGRAM_SLC.CORE_BUSY`에만 연결되어 있어 suspend 시점은 CORE_BUSY 구간으로 제한된다(`config.yaml:5160`).
- suspend 시점의 `move_to_suspended_axis`는 잔여 state 목록에 CORE_BUSY 잔여분과 DATA_IN 전체를 포함해 재개 시 모두 실행되도록 유지한다(`resourcemgr.py:1593`).
- `PROGRAM_RESUME` 완료 시 스케줄러가 같은 메타를 다시 예약해 CORE_BUSY 잔여분 이후 DATA_IN까지 재실행되지만, DATA_IN 구간 자체가 suspend 대상으로 다시 선택되지는 않는다(`scheduler.py:809`).

## 상세 발견

### Config Suspend/Resume Targets
- `CACHE_PROGRAM_SLC`는 `CORE_BUSY`와 `DATA_IN` 두 상태를 갖지만 suspend 연계는 CORE_BUSY에만 설정되어 있다(`config.yaml:136`).
- `PROGRAM_SUSPEND` 확률은 `CACHE_PROGRAM_SLC.CORE_BUSY`에만 배치되어 있어 DATA_IN 상태에서는 suspend가 제안되지 않는다(`config.yaml:5160`).
- 상태 기반 캐스케이드에서도 `Program_Suspend_Reset`이 CORE_BUSY에만 매핑되고 DATA_IN에는 `Program_Resume`만 매핑되어 suspend 반복이 CORE_BUSY에 국한된다(`op_state_probs.yaml:51`).
- program 축이 suspend 되면 `program_suspended` 그룹에 속한 `CACHE_PROGRAM_SLC`가 예약 금지되어 resume 이전에는 동일 base를 발행할 수 없다(`config.yaml:2173`).
- `exclusions_by_suspend_state`가 PROGRAM_SUSPENDED → program_suspended 그룹 연결을 제공해 ResourceManager가 rule 기반으로 막는다(`config.yaml:2317`).

### Runtime Suspend Handling
- `PROGRAM_SUSPEND` 커밋 시 ResourceManager가 프로그램 축 suspend 상태를 열고 최신 ongoing 프로그램을 suspend 스택으로 이동한 뒤 CORE_BUSY 세그먼트만 타임라인에서 잘라낸다(`resourcemgr.py:880`, `resourcemgr.py:928`).
- `_slice_states` 호출로 소비된 시간을 제외한 나머지 state 목록을 구성하며 CORE_BUSY 잔여분과 DATA_IN 전체가 그대로 남는다(`resourcemgr.py:1593`).
- resume 시 `resume_from_suspended_axis`가 남은 state를 그대로 normalize하여 재예약하므로, CORE_BUSY 잔여와 DATA_IN이 연속으로 진행된다(`resourcemgr.py:1768`).

### Resume Scheduling Flow
- `PROGRAM_RESUME` 커밋을 감지한 스케줄러가 해당 die에서 suspend된 메타를 꺼내 재예약하고 OP_END를 다시 큐잉해 잔여 state 전체를 마무리한다(`scheduler.py:809`).

## 코드 참조
- `config.yaml:136` – CACHE_PROGRAM_SLC state 정의
- `config.yaml:5160` – CORE_BUSY에서 PROGRAM_SUSPEND 발생 확률
- `op_state_probs.yaml:51` – CORE_BUSY→Program_Suspend_Reset, DATA_IN→Program_Resume 매핑
- `config.yaml:2173` – program_suspended 그룹에 CACHE_PROGRAM_SLC 포함
- `resourcemgr.py:880` – PROGRAM_SUSPEND 처리 및 CORE_BUSY 잘림
- `resourcemgr.py:1593` – 잔여 state 슬라이스에 DATA_IN 포함
- `resourcemgr.py:1768` – resume 시 잔여 state 재예약
- `scheduler.py:809` – PROGRAM_RESUME 커밋 후 재예약 흐름

## 아키텍처 인사이트
- suspend 트리거를 CORE_BUSY에만 두고 DATA_IN에서는 resume만 허용함으로써 cache program 데이터 전송 중단을 방지하고, resume는 원본 meta state를 그대로 복원해 연속성을 유지한다.
- ResourceManager의 state 슬라이싱과 Scheduler의 재예약이 결합되어 동일 meta가 다중 suspend→resume 사이클을 거치더라도 CORE_BUSY 잔여와 후속 DATA_IN이 일관된 타이밍으로 재배치된다.

## 관련 연구
- `research/2025-09-28_15-52-08_cache_program_slc_suspend.md`

## 미해결 질문
- DATA_IN 구간에서도 suspend를 허용해야 하는 요구가 있는지 여부는 정책적으로 확정되지 않았으며, 필요 시 state 확률표와 state_forbid 규칙을 함께 조정해야 한다.
