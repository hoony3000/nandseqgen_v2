---
date: 2025-09-29T11:00:54+09:00
git_commit: 7826919573d0ac03d86e680b8203c2cfb64f17d5
branch: main
repository: nandseqgen_v2
topic: "ONESHOT_PROGRAM 단계별 래치 해제 여부"
tags: [research, codebase, ResourceManager, Scheduler, Config]
status: complete
last_updated: 2025-09-29
last_updated_by: codex
last_updated_note: "batch proposal latch exclusion 분석 추가"
---

# 연구: ONESHOT_PROGRAM 단계별 래치 해제 여부

**Date**: 2025-09-29T11:00:54+09:00
**Git Commit**: 7826919573d0ac03d86e680b8203c2cfb64f17d5
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
ONESHOT_PROGRAM_LSB → ONESHOT_PROGRAM_CSB → ONESHOT_PROGRAM_MSB_23H 순서로 예약될 때 래치 상태가 단계별로 중첩되어 유지되는지, 아니면 각 오퍼레이션 종료 시 래치가 해제되는지를 확인한다.

## 요약
- ResourceManager는 각 ONESHOT_PROGRAM 단계가 끝날 때 해당 단계의 래치를 모든 플레인에 기록하고, 해제 시점을 지정하지 않는다. 따라서 LSB/CSB 래치는 이후 단계 완료 전까지 유지된다.
- Scheduler는 ONESHOT_PROGRAM_MSB_23H 또는 ONESHOT_PROGRAM_EXEC_MSB 종료 이벤트에서만 프로그램 래치를 일괄 해제한다. 중간 단계 종료 시 별도의 릴리스가 없다.
- 설정 파일의 `exclusions_by_latch_state`와 그룹 정의는 단계별 래치를 누적하되, 다음 단계 실행에 필요한 오퍼레이션은 차단 목록에서 주석 처리하여 허용한다. 결과적으로 제외 규칙은 누적되지만 설계상 다음 단계 진행에는 영향이 없다.

## 상세 발견

### ResourceManager 래치 등록 구조
- `reserve`/`commit` 경로는 ONESHOT_PROGRAM_LSB/CSB/MSB 예약 시 래치 엔트리를 `start_us=end, end_us=None`으로 모든 플레인에 기록하여 종료 이후에도 활성 상태를 유지시킨다(`resourcemgr.py:720-799`).
- `_latch_ok` 검사는 활성 래치 종류를 `exclusions_by_latch_state`에 매핑해 해당 그룹의 오퍼레이션을 차단한다. 여러 래치가 동시에 존재하면 그룹이 합집합으로 적용된다(`resourcemgr.py:602-636`).
- `_latch_kind_for_base`는 ONESHOT 프로그램 단계별로 서로 다른 래치 키를 반환하여 중첩 관리가 가능하도록 한다(`resourcemgr.py:2258-2267`).

### Scheduler 종료 훅
- `_handle_op_end`는 ONESHOT_PROGRAM_MSB_23H 또는 ONESHOT_PROGRAM_EXEC_MSB 종료 시점에만 `release_on_exec_msb_end`를 호출한다(`scheduler.py:362-386`).
- `release_on_exec_msb_end`는 해당 다이의 모든 플레인에서 LSB/CSB/MSB 래치를 한꺼번에 제거한다. 중간 단계에는 호출 경로가 없다(`resourcemgr.py:958-962`).

### Config 기반 제외 그룹
- `exclusions_by_latch_state`는 각 래치 종류를 `after_oneshot_program_*` 그룹에 매핑한다(`config.yaml:2311-2315`).
- `after_oneshot_program_lsb` 등 그룹은 광범위한 오퍼레이션을 차단하지만 다음 단계에 필요한 항목(`ONESHOT_PROGRAM_CSB`, `ONESHOT_PROGRAM_MSB` 등)은 주석 처리해 허용한다. 따라서 래치가 유지되더라도 순차 진행은 가능하다(`config.yaml:1716-1794`, `config.yaml:1830-1863`).
- `exclusion_groups`는 각 프로그램 단계의 END 상태에서 동일한 그룹을 활성화하여 래치가 시작되는 시점과 제외 정책이 일치하도록 한다(`config.yaml:2244-2249`).

### 검증 테스트 관찰
- `test_multi_latch_release_per_kind`는 READ 및 PROGRAM 래치가 동시에 유지되다가, READ 종료 훅과 MSB 종료 훅을 통해 개별적으로 제거됨을 보여준다(`tests/test_resourcemgr_multi_latch.py:69-87`). 이는 프로그램 래치가 명시적 해제 전까지 유지된다는 동작을 확인해 준다.

## 코드 참조
- `resourcemgr.py:720-799` - ONESHOT 프로그램 예약 시 래치 엔트리 기록
- `resourcemgr.py:602-636` - 활성 래치 기반 제외 검사
- `resourcemgr.py:2258-2267` - 래치 종류 매핑
- `scheduler.py:362-386` - MSB 종료 이벤트에서만 래치 해제 호출
- `resourcemgr.py:958-962` - 프로그램 래치 일괄 해제 구현
- `config.yaml:1716-1863` - 래치별 제외 그룹 정의
- `config.yaml:2244-2249` - 프로그램 END 상태에서 그룹 활성화
- `config.yaml:2311-2315` - 래치 상태별 그룹 매핑
- `tests/test_resourcemgr_multi_latch.py:69-87` - 래치 유지 및 해제 동작 테스트

## 아키텍처 인사이트
- 래치 해제는 단계별이 아닌 최종단계 이벤트 기반으로 설계되어, 프로그램 시퀀스 전체를 보호하는 다이-범위 레버리지 역할을 한다.
- 제외 그룹은 래치 누적을 전제로 하되, 다음 단계 진행을 허용하도록 config 레벨에서 세밀하게 제어한다. 따라서 비즈니스 규칙 변경 시 config 조정만으로 정책을 재구성할 수 있다.
- Scheduler의 OP_END 훅이 래치 해제의 단일 진입점이므로, 비정상 종료나 누락 시 래치가 잔존할 수 있어 로깅/모니터링이 중요하다.

## 관련 연구
- 없음

## 미해결 질문
- ONESHOT_PROGRAM_MSB_23H 이전에 실패/중단되는 경로에서 래치를 강제 해제할 수 있는 복구 루틴이 필요한지 추가 확인이 필요하다.

## 후속 연구 2025-09-29T11:18:46+09:00
- 배치 예약은 하나의 트랜잭션에서 순차적으로 `rm.reserve`를 호출하며, 이전 예약이 아직 커밋되지 않은 상태에서도 `_latch_ok`는 커밋된 래치만 조회한다. 따라서 동일 배치 내 후속 오퍼레이션은 직전에 기록된 래치에 의해 차단되지 않는다(`scheduler.py:1075-1299`, `resourcemgr.py:602-636`).
- `_txn_record_latch`로 수집된 래치는 `commit` 단계에서 일괄 `_set_latch_entry`로 적용되므로, 배치가 완료되면 LSB/CSB/MSB 래치가 동시에 활성화되어 이후 배치/작업에서 누적된 제외 규칙이 적용된다(`resourcemgr.py:212-837`).
- `proposer._preflight_schedule`은 리소스 스냅샷을 공유한 채 각 오퍼레이션의 가능 시간만 계산하고, 배치 내 선행 예약을 가정한 상태 변화를 반영하지 않으므로 제안 과정에서도 래치 누적은 고려되지 않는다(`proposer.py:1250-1328`).
