---
date: 2025-10-02T11:20:40+09:00
git_commit: ace8d3d2e513c1bedf817234be7ac7ef6a9d2bb7
branch: main
repository: nandseqgen_v2
topic: "SUSPEND->RESUME metrics validation"
tags: [research, codebase, scheduler, resourcemgr, suspend-resume]
status: complete
last_updated: 2025-10-02
---

# 연구: SUSPEND->RESUME metrics validation

**Date**: 2025-10-02T11:20:40+09:00
**Git Commit**: ace8d3d2e513c1bedf817234be7ac7ef6a9d2bb7
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
SUSPEND->RESUME 동작 시 operation 이 중단됐다가 재예약 되는 흐름과, 그 때 사용되는 변수들을 조사해줘. 목적은, SUSPEND->RESUME 이 정상동작 했는지 확인하는 metric 을 만들려는 목적이야.

## 요약
- ResourceManager 는 suspend 시점에 ongoing 메타를 축별 스택으로 옮기고 `remaining_us`, `suspend_time_us`, 예약 창을 잘라낸 뒤 Scheduler 가 OP_END 를 취소할 수 있도록 `_suspend_transfers` 를 채운다.
- Scheduler 는 동일 축/다이 backlog 를 통해 후속 오퍼레이션을 대기시키고, RESUME 커밋에서 ResourceManager 재예약 결과를 받아 재등록된 OP_END 와 기대 타깃을 추적한다.
- 관측 지표는 `suspended_op_end_cancelled`, `backlog_*`, `program_resume_page_mismatch` 같은 Scheduler 메트릭과 `suspended_ops_*` 스냅샷, `last_resume_error` 등을 결합해 SUSPEND→RESUME 정상 동작 여부를 판별할 수 있다.

## 상세 발견

### ResourceManager Suspend/Resume 파이프라인
- 런타임 상태 초기화에서 축별 suspended 스택과 `_suspend_transfers` 버킷을 준비해 축/다이 단위 추적이 가능하다 (`resourcemgr.py:162`).
- SUSPEND 커밋 시 `move_to_suspended_axis` 를 호출해 최신 ongoing 메타를 꺼내어 `consumed_us`, `remaining_us`, `suspend_time_us`, `planes` 등을 갱신하고 향후 재예약을 위해 `bus_resv`·`plane_resv`·배타 토큰을 잘라낸다 (`resourcemgr.py:882`, `resourcemgr.py:1606`).
- Suspend 된 메타의 `op_id` 는 `_suspend_transfers[(axis, die)]` 로 큐잉되어 Scheduler 가 기존 OP_END 이벤트를 제거할 수 있도록 전달된다 (`resourcemgr.py:1760`).
- `resume_from_suspended_axis` 는 지정 축 스택에서 메타를 pop 하여 `remaining_us` 기반 resume 오퍼레이션을 만들고, 예약 성공 시 새 `start_us`/`end_us` 와 상태/버스 세그먼트를 재구성한다 (`resourcemgr.py:1803`).
- 재예약 실패 시 메타를 원위치시키고 `_last_resume_error` 로 사유·축·die·start 힌트를 기록, 추후 Scheduler 가 로그를 남길 수 있다 (`resourcemgr.py:1876`, `resourcemgr.py:2304`).

### Scheduler 연계 및 이벤트 관리
- Scheduler 메트릭 맵은 suspend/resume 관련 항목으로 `suspended_op_end_cancelled`, `backlog_*`, `program_resume_page_mismatch` 등을 초기화해 관찰 가능하도록 구성한다 (`scheduler.py:120`).
- `batch.ops` 처리 중 동일 축/다이에 대한 suspend 정보가 발견되면 이후 오퍼레이션을 backlog 엔트리로 전환하여 RESUME 이후로 실행을 미룬다 (`scheduler.py:1122`).
- SUSPEND 커밋 후 ResourceManager 가 넘겨준 suspended op id 를 소비하여 기존 OP_END 이벤트를 제거하고 메트릭을 증가시킨다 (`scheduler.py:1318`).
- `_handle_resume_commit` 은 RESUME 커밋을 감지해 `resume_from_suspended_axis` 를 호출, 성공 시 `_resumed_op_uids` 와 `_resume_expected_targets` 를 채운 뒤 새로운 OP_END 이벤트를 등록한다 (`scheduler.py:766`).
- OP_END 이벤트가 발생하면 기록된 기대 타깃과 실제 타깃을 비교해 mismatch 시 `program_resume_page_mismatch` 메트릭을 상승시키고, 관련 오퍼레이션 로그를 남긴다 (`scheduler.py:428`).

### 관측 지표·노출 포인트
- `suspended_ops_*` API 는 suspend 상태 메타의 `remaining_us`, `suspend_time_us`, `consumed_us`, 타깃 좌표를 그대로 노출하므로 메트릭 생성 시 그대로 활용할 수 있다 (`resourcemgr.py:1220`).
- Scheduler backlog 큐는 축/다이 키별 길이와 `backlog_flush`, `backlog_retry`, `backlog_retry_events` 카운터로 RESUME 이후 재예약 진행도를 나타낸다 (`scheduler.py:515`).
- `last_resume_error` 는 재예약 실패 원인을 외부에서 읽을 수 있는 단일 지점이며, 복구 불가 상태를 메트릭으로 전환할 때 유용하다 (`resourcemgr.py:2304`).
- 설계 문서 `SUSPEND_RESUME_RULES` 는 remaining 시간 보존·스테이트 잘라내기·재예약 성공 시 재등록 같은 규범을 명시해 구현과 지표 해석 기준을 제공한다 (`docs/SUSPEND_RESUME_RULES.md:1`).
- 단위 테스트는 suspend 상태 스냅샷과 backlog flush/retry 흐름을 검증해 현재 동작을 재현 가능하게 하며, 메트릭 기대값 도출의 근거가 된다 (`tests/test_suspend_resume.py:167`, `tests/test_suspend_resume.py:747`).

## 코드 참조
- `resourcemgr.py:162` - 축별 suspended 스택과 `_suspend_transfers` 초기화
- `resourcemgr.py:882` - SUSPEND 커밋에서 meta 이동 및 타임라인 절단
- `resourcemgr.py:1606` - `move_to_suspended_axis` 로 remaining/bus/truncation 계산
- `resourcemgr.py:1803` - `resume_from_suspended_axis` 의 재예약 로직과 오류 보고
- `scheduler.py:1122` - suspend 이후 backlog 엔트리 생성 흐름
- `scheduler.py:1318` - suspended op id 소비와 OP_END 취소
- `scheduler.py:766` - RESUME 커밋 처리와 OP_END 재등록
- `scheduler.py:428` - resumed 타깃 검증과 `program_resume_page_mismatch`
- `docs/SUSPEND_RESUME_RULES.md:1` - 설계 규칙 요약
- `tests/test_suspend_resume.py:747` - backlog flush/retry 테스트 시나리오

## 아키텍처 인사이트
- Suspend/Resume 책임이 ResourceManager(상태/예약)와 Scheduler(이벤트/큐 관리)로 분리되어 데이터 평면과 제어 평면이 명확하게 나뉜다.
- `_suspend_transfers` 와 backlog 큐를 통한 느슨한 결합 덕분에 suspend 처리 중에도 Scheduler 이벤트 큐 일관성이 보장된다.
- 재예약 기대 타깃을 `_resume_expected_targets` 로 저장해 나중에 OP_END 시점에서 유효성 검사를 수행, 지표와 로그를 연결할 수 있는 후크를 제공한다.
- Remaining 시간과 bus 세그먼트를 유지한 채 재예약하는 구조는 반복 suspend/resume 시나리오에서도 누적 오차 없이 복구 가능하도록 설계되었다.

## 관련 연구
- research/2025-09-29_11-37-31_suspend-resume-resources.md

## 미해결 질문
- 다중 반복 SUSPEND→RESUME 루프에서 backlog 지표와 suspended 스택 길이를 어떻게 상호 검증할지 추가 실험이 필요하다.
- `last_resume_error` 를 주기적으로 비우거나 집계할 메커니즘이 없어 장기 실행 중 마지막 실패만 남는 점을 메트릭 설계에서 어떻게 해석할지 결정해야 한다.
- RESUME 실패 후 재시도 과정이 외부 메트릭으로 드러나는지(`backlog_retry_events` 외) 추가 훅이 필요한지 검토가 요구된다.
