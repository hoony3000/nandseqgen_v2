---
date: 2025-09-29T11:37:31+09:00
git_commit: 7826919573d0ac03d86e680b8203c2cfb64f17d5
branch: main
repository: nandseqgen_v2
topic: "SUSPEND/RESUME resource coverage"
tags: [research, codebase, resourcemgr, scheduler]
status: complete
last_updated: 2025-09-29
---

# 연구: SUSPEND/RESUME resource coverage

**Date**: 2025-09-29T11:37:31+09:00
**Git Commit**: 7826919573d0ac03d86e680b8203c2cfb64f17d5
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
SUSPEND, RESUME 시 어떤 resource, state 를 중단, 복구하게 되어 있는지 조사하고, latch state 도 포함되는지 확인한다.

## 요약
SUSPEND 커밋은 ResourceManager가 해당 다이에 대해 ERASE/PROGRAM 축을 `*_SUSPENDED` 상태로 열고, 진행 중인 작업 메타를 축별 누적 스택으로 이동시키면서 남은 state/bus 구간을 슬라이스하고 plane·bus·die 배타 예약과 op_state 타임라인을 중단 시각 이후로 잘라낸다. 동시에 Scheduler 는 같은 축/다이의 후속 오퍼레이션을 backlog 로 밀어 두고, ResourceManager 가 넘겨준 `op_uid` 를 활용해 기존 OP_END 이벤트를 취소한다. RESUME 커밋은 저장해 둔 메타를 기반으로 새 예약을 시도해 plane/bus/exclusion 을 재적용하고, 성공 시 다시 ongoing 목록에 복귀시킨 뒤 축별 suspend state 를 해제한다. 이 과정에서 latch 버킷은 갱신되지 않으며 SUSPEND/RESUME 흐름은 기존 latch entry 를 그대로 유지한다.

## 상세 발견

### ResourceManager (resourcemgr.py)
- SUSPEND 커밋 시 축 상태를 열고 `move_to_suspended_axis` 로 마지막 ongoing 메타를 축별 스택으로 이동한 뒤, 대상 plane/bases 의 op_state 타임라인을 잘라낸다 (`resourcemgr.py:882`, `resourcemgr.py:895`, `resourcemgr.py:908`, `resourcemgr.py:934`).
- `move_to_suspended_axis` 는 남은 state/bus 를 슬라이스해 메타에 보존하고, plane 예약·die/global exclusion·bus 예약을 중단 시각 이후로 잘라내며, 축별 suspended 큐와 `_suspend_transfers` 버퍼에 op_uid 를 기록한다 (`resourcemgr.py:1606`, `resourcemgr.py:1675`, `resourcemgr.py:1680`, `resourcemgr.py:1739`, `resourcemgr.py:1760`, `resourcemgr.py:1767`).
- RESUME 시 `resume_from_suspended_axis` 가 잔여 state 를 재구성해 새 reserve 트랜잭션을 실행하고, 성공하면 메타를 ongoing 목록에 복구하며 남은 시간·축 상태를 초기화한다 (`resourcemgr.py:1803`, `resourcemgr.py:1867`, `resourcemgr.py:1893`, `resourcemgr.py:1899`, `resourcemgr.py:1901`).
- 축 suspend 상태는 `_erase_susp` / `_pgm_susp` 에서 기동·종료를 기록해 `erase_suspend_state` / `program_suspend_state` 쿼리가 동작하며, config 의 `exclusions_by_suspend_state` 매핑을 통해 다른 작업을 막는다 (`resourcemgr.py:885`, `resourcemgr.py:945`, `resourcemgr.py:1140`, `resourcemgr.py:1154`, `resourcemgr.py:2362`, `resourcemgr.py:2462`).
- latch 버킷 복사는 커밋 초입 `txn.latch_locks` 처리로만 이뤄지고, SUSPEND/RESUME 분기에는 latch 제거/갱신 코드가 존재하지 않는다. latch 해제는 별도 이벤트 훅에서만 수행되어 suspend 흐름이 latch 상태를 건드리지 않는다 (`resourcemgr.py:832`, `resourcemgr.py:954`, `resourcemgr.py:962`).

### Scheduler Integration (scheduler.py)
- 배치 예약 루프는 동일 축/다이에 대해 직전의 SUSPEND 커밋 정보를 기억했다가 후속 오퍼레이션을 backlog 큐로 보내 재예약 시점을 지연시킨다 (`scheduler.py:1100`, `scheduler.py:1122`, `scheduler.py:1156`, `scheduler.py:1184`).
- 커밋 직후 SUSPEND 베이스를 만나면 ResourceManager 에서 전달된 suspended op id 목록을 소비해 기존 OP_END 이벤트를 취소한다 (`scheduler.py:1318`, `scheduler.py:1336`, `scheduler.py:1338`, `scheduler.py:1344`).
- RESUME 커밋은 `_handle_resume_commit` 을 통해 해당 축/다이의 suspend 큐에서 메타를 꺼내 `resume_from_suspended_axis` 를 호출하고, 새로 계산된 종료 시각으로 OP_END 이벤트와 기대 타깃을 재등록한다 (`scheduler.py:766`, `scheduler.py:798`, `scheduler.py:812`, `scheduler.py:840`, `scheduler.py:875`).
- backlog 처리기는 실패 시 재시도 이벤트를 스케줄링하고, 성공 시 다음 항목을 곧바로 복구하게 만들어 suspend 동안 큐에 남아 있던 작업의 순서를 유지한다 (`scheduler.py:720`, `scheduler.py:729`, `scheduler.py:755`, `scheduler.py:763`).

### 정책 및 문서
- `docs/SUSPEND_RESUME_RULES.md` 는 ERASE/PROGRAM suspend 흐름이 remaining 시간과 state/bus 구간을 보존하고, RESUME 시 ResourceManager 가 다시 예약을 복구한다는 설계 규칙을 명문화한다.
- 테스트 `tests/test_suspend_resume.py` 는 남은 state/bus 슬라이스, op_uid 전달, 재예약 실패 시 상태 유지 등을 검증해 구현이 규칙을 따름을 보장한다 (`tests/test_suspend_resume.py:190`, `tests/test_suspend_resume.py:217`, `tests/test_suspend_resume.py:573`, `tests/test_suspend_resume.py:661`).

## 코드 참조
- `resourcemgr.py:882` – SUSPEND 커밋 분기에서 축 상태 초기화 및 meta 이동 호출
- `resourcemgr.py:1606` – `move_to_suspended_axis` 자원 정리 로직
- `resourcemgr.py:1867` – RESUME 시 재예약 트랜잭션 수행
- `scheduler.py:1122` – SUSPEND 이후 동일 축 작업을 backlog 로 이동
- `scheduler.py:1318` – SUSPEND 커밋 직후 suspended op 의 OP_END 취소
- `scheduler.py:766` – `_handle_resume_commit` 의 RESUME 재예약 경로
- `docs/SUSPEND_RESUME_RULES.md` – ERASE/PROGRAM suspend-resume 규칙 명세

## 아키텍처 인사이트
SUSPEND/RESUME 처리에서 ResourceManager 가 모든 시간·배타 자원을 단일 지점에서 잘라내고 복구하는 반면, Scheduler 는 이벤트 큐 및 후속 예약 흐름을 조정하는 역할로 분리되어 있다. 축별 suspend state 와 `exclusions_by_suspend_state` 정책은 다른 베이스 예약을 즉시 차단할 수 있는 보호막을 형성하며, latch 시스템과는 독립적으로 유지되어 latch 는 각 오퍼레이션의 자체 훅에 의해 해제된다. 이 구조 덕분에 suspend 로 인한 시뮬레이터 상태 조정이 데이터 평면(타임라인·예약)과 제어 평면(이벤트·backlog)이 명확히 나뉘어 수행된다.

## 관련 연구
- `research/2025-09-27_18-17-24_scheduler-op-end-suspend-resume.md`
- `research/2025-09-28_11-35-16_suspend-batch-resume.md`

## 미해결 질문
- latch 를 suspend 시점에 해제하지 않는 설계가 의도된 것인지, 장시간 suspend 중 latch 차단이 필요한 시나리오가 있는지는 추가 확인이 필요하다.
- resume 재예약이 반복 실패할 때의 장기 처리(예: backlog 누적 모니터링)에 대한 정책은 문서화되지 않았다.
