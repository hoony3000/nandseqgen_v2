---
date: 2025-09-16T00:26:04+0900
researcher: codex
git_commit: 721cb7a78ed7ed56b00aa8db52318e72e560385e
branch: main
repository: nandseqgen_v2
topic: "num_runs>=2: previous One_Shot_PGM_MSB_23h causes next run ops missing; snapshot handoff and exclusion_groups"
tags: [research, codebase, scheduler, resourcemgr, latches, exclusions, snapshot]
status: complete
last_updated: 2025-09-16
last_updated_by: codex
---

# 연구: num_runs>=2: previous One_Shot_PGM_MSB_23h causes next run ops missing; snapshot handoff and exclusion_groups

**Date**: 2025-09-16T00:26:04+0900
**Researcher**: codex
**Git Commit**: 721cb7a78ed7ed56b00aa8db52318e72e560385e
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
main.py 실행 시 `--num_runs >= 2`에서 직전 run이 `One_Shot_PGM_MSB_23h`로 종료되면, 다음 run에서 대부분의 `op_name`/`op_base`가 생성되지 않는다. 원인 분석과 함께 snapshot 인수인계(hand‑off) 과정, 그리고 `exclusion_groups`와의 연관성을 조사한다.

## 요약
- 원인: `ONESHOT_PROGRAM_MSB` 단계에서 설정된 die‑wide latch(`LATCH_ON_MSB`)가 run 경계에서 해제되지 않고 다음 run으로 지속되어, `exclusions_by_latch_state -> after_oneshot_program_msb` 그룹에 의해 대부분의 제안이 차단된다.
- 왜 지속되는가: OP_END 이벤트가 run 경계에서 처리되지 않거나 이벤트 큐가 run 간 인계되지 않기 때문. latch 해제는 Scheduler의 OP_END 핸들러에서 수행되는데, 다음 run에는 해당 이벤트가 사라진 상태다.
- 결과: 다음 run에서는 허용된 일부 베이스(예: `ONESHOT_PROGRAM_EXEC_MSB` 등) 외 거의 모든 제안이 RM의 latch 기반 배제에 걸려 빠진다. operation_sequence CSV가 빈약해 보이는 이유.

## 상세 발견

### Run 경계와 이벤트 처리
- 새 run 시작 시간 설정: 이전 상태의 최대 avail을 기반으로 시작 시간을 정렬.
  - `main.py:982` — RM 스냅샷 조회로 시작 시간 산출
  - `main.py:990` — `InstrumentedScheduler(..., start_at_us=t0)` 생성
  - `main.py:991` — 새 스케줄러의 `run(...)` 호출
- 스케줄러는 OP_END/PHASE_HOOK/QUEUE_REFILL 순으로 이벤트 배치를 처리하지만, 루프 조건상 `now_us >= run_until`이면 추가 tick을 수행하지 않아, 경계 시각의 OP_END가 남을 수 있다.
  - `scheduler.py:108` — run 루프의 종료 조건 체크
  - `scheduler.py:138` — tick 내 OP_END 우선 처리

### Latch 설정과 해제, 그리고 배제 규칙
- 예약/커밋 시 `ONESHOT_PROGRAM_*`는 die‑wide latch를 설정한다. 이 latch는 plane‑scoped 엔트리로 모든 plane에 기록되며 `end_us=None`로 남아 활성 상태가 지속된다.
  - `resourcemgr.py:557` — 프로그램 계열 latch 기록(plane 전반)
- latch에 따른 배제는 RM의 `_latch_ok`에서 수행: 활성 latch kind를 `cfg.exclusions_by_latch_state`로 그룹에 매핑하고, 해당 그룹 내 베이스면 차단한다.
  - `resourcemgr.py:397` — `_latch_ok` 진입
  - `resourcemgr.py:407` — `exclusions_by_latch_state` 조회
  - `resourcemgr.py:408` — `exclusion_groups` 정의 사용
- MSB 단계 종료 시 latch 해제는 OP_END 이벤트 핸들러에서만 수행된다. 이벤트가 run 경계에서 처리되지 않으면 latch는 다음 run에도 남는다.
  - `scheduler.py:202` — OP_END 처리 진입
  - `scheduler.py:208` — MSB/EXEC_MSB 종료 시 RM 해제 호출
  - `resourcemgr.py:695` — `release_on_exec_msb_end(die, ...)`: 모든 plane의 latch 제거

### exclusion_groups 구성과 영향
- `LATCH_ON_MSB`는 `after_oneshot_program_msb` 그룹으로 매핑되어, 그룹 내 베이스를 배제한다.
  - `config.yaml:2304` — `LATCH_ON_MSB: ['after_oneshot_program_msb']`
- `after_oneshot_program_msb` 그룹은 많은 베이스를 포함(주요 동작 대부분을 금지). 주석 처리된 항목은 허용된다.
  - `config.yaml:1819` — 그룹 정의 시작(대부분의 PROGRAM/READ/IO 등 포함; 일부는 주석으로 허용)
- 결과적으로 다음 run에서 허용되는 것은 주로 `ONESHOT_PROGRAM_EXEC_MSB` 등 소수이며, 나머지는 `_latch_ok`에서 거부된다. 사용자가 관찰한 “일부 op만 생성” 현상과 일치.

### Snapshot hand‑off
- 저장: 각 run 종료 후 RM 스냅샷을 JSON으로 내보내지만, 이는 관찰용이며 런타임 복원에 사용되지 않는다.
  - `main.py:816` — `save_snapshot(rm, ...)`
- 인계: 단일‑사이트 경로에서 동일한 RM 인스턴스를 재사용하여 상태 연속성을 유지한다. 이벤트 큐는 새 스케줄러마다 초기화되므로 OP_END 기반 부수효과(예: latch 해제)는 run 간 자동 반영되지 않는다.
  - `main.py:1157` — 단일‑사이트 루프 내 RM 재사용

## 코드 참조
- `main.py:990` — 새 스케줄러를 `start_at_us=t0`로 생성(이벤트 큐는 매 run 초기화)
- `scheduler.py:108` — run 루프가 `now_us >= run_until`이면 tick을 중단(경계 이벤트 미처리 가능)
- `scheduler.py:206` — DOUT 계열 종료 시 latch 해제
- `scheduler.py:208` — `ONESHOT_PROGRAM_MSB_23H`/`ONESHOT_PROGRAM_EXEC_MSB` 종료 시 latch 해제 트리거
- `resourcemgr.py:397` — `_latch_ok`: latch 상태에 따른 제안 차단 로직
- `resourcemgr.py:557` — `ONESHOT_PROGRAM_*` 시 latch 설정(plane 전체)
- `resourcemgr.py:695` — `release_on_exec_msb_end`: die 단위 latch 일괄 해제
- `config.yaml:2304` — `exclusions_by_latch_state`에서 LATCH_ON_MSB → `after_oneshot_program_msb`
- `config.yaml:1819` — `exclusion_groups.after_oneshot_program_msb` 상세 정의

## 아키텍처 인사이트
- 이벤트 기반 부수효과(OP_END 트리거)와 상태 스냅샷/연속성 사이의 비대칭성으로 인해 run 경계에서 불일치가 발생한다. latch/배타/캐시 같은 런타임 상태는 이벤트 큐가 초기화되면 다음 run 시작 시점에 재평가/정리되지 않는다.

## 대안 비교와 권고
- 옵션 A — run 경계에서 이벤트 플러시: `Scheduler.run`이 종료하기 전, `now_us <= run_until` 범위의 이벤트 배치를 모두 소진하도록 한 번 더 tick/flush 수행.
  - 장점: 간단, latch 해제/캐시 종료 등 OP_END 부수효과가 확실히 반영됨.
  - 단점: 경계에서 추가 tick으로 인해 아주 소량의 시간이 더 소요될 수 있음.
- 옵션 B — run 사이 정합 단계 추가: 다음 run 시작 전 RM에 `reconcile(now)` 같은 훅을 두어, `timeline`/`ongoing_ops`를 기준으로 latch/캐시 등을 정리(예: 최근 `ONESHOT_PROGRAM_MSB_23H`/`EXEC_MSB` 종료 기록이 있으면 `release_on_exec_msb_end` 호출).
  - 장점: 스케줄러 변경 없이 상태 일관성 회복.
  - 단점: 새로운 정합 로직 필요, 이중소스(timeline/ongoing_ops) 관리 복잡성.
- 옵션 C — 이벤트 큐 지속화: run 간 EventQueue를 보존/재생.
  - 장점: 의미론 보존.
  - 단점: 구현 복잡, 관찰/디버깅 비용 증가.

권고: 옵션 A가 가장 단순하고 리스크가 낮다. 최소한 OP_END가 run 경계에 정확히 도달했을 때는 처리되도록 보장하는 것이 안전하다. 보완적으로 옵션 B의 간단한 정합(예: MSB 종료시 latch 해제)만 추가해도 현 증상은 사라진다.

## 관련 연구
- `research/2025-09-14_13-14-52_rm_validity_same_time_ops.md` — 동일 시각 이벤트와 배타 윈도우 상호작용 기록
- `plan/2025-09-14_operation_timeline_effective_optionC_impl_plan.md` — RM timeline 활용 방안(경계 처리 관련)

## 미해결 질문
- run 경계에서 정확히 같은 timestamp의 OP_END 처리 정책을 어떻게 정의할지(<= vs <). 현재는 `<`만 처리되는 경향이 있어 보이며, 이는 경계 누락을 유발. -> (검토완료) 모든 예약된 이벤트를 소진
- `after_oneshot_program_msb`에서 READ/PLANE_READ 일부가 허용(주석)되어 있음에도 실제로 제안이 적은 이유: EPR/addr 정책이나 다른 constraints가 추가로 제한하는지 교차 검증 필요. -> (검토완료) 현재는 고려하지 않음

