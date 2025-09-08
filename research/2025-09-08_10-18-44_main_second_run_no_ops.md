---
date: 2025-09-08T10:18:44+09:00
researcher: Codex
git_commit: cba55e0
branch: main
repository: nandseqgen_v2
topic: "main 함수에서 num_runs>=2 이후 두 번째 run부터 operation이 생성되지 않음"
tags: [research, codebase, scheduler, proposer, resourcemgr]
status: complete
last_updated: 2025-09-08
last_updated_by: Codex
---

# 연구: main 함수에서 num_runs>=2 이후 두 번째 run부터 operation이 생성되지 않음

**Date**: 2025-09-08T10:18:44+09:00
**Researcher**: Codex
**Git Commit**: cba55e0
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
main 함수에서 num_runs>=2 조건에서, 두 번째 run 부터 operation 이 생성되지 않는 문제가 있어. 이것을 진단하고 개선하기 위한 방법을 research 해줘.

## 요약
- 원인: 첫 run 종료 후 `ResourceManager`의 타임라인은 진행된 시간(각 (die,plane)별 avail)이 누적된 반면, 두 번째 run의 `Scheduler`는 `now_us=0`에서 새로 시작한다. 그 결과 `proposer`가 찾는 earliest feasible start `t0`가 `now`에 비해 매우 미래여서 `admission_window`(기본 1us)를 초과, 모든 후보가 `window_exceed`로 탈락한다. 즉, 두 번째 run은 설정된 `run_until_us` 내에서 `now`가 충분히 진전되기 전에 종료되어 커밋이 0이 된다.
- 개선안(권장): run 간 연속성을 유지하기 위해, 각 새 run을 `ResourceManager`의 글로벌 시뮬레이션 시각(예: 모든 (die,plane) avail의 최대값)에서 시작하도록 `Scheduler`의 시작 시각을 설정한다. 이렇게 하면 `t0`와 `now`가 정렬되어 `admission_window` 조건을 만족하며 두 번째 run에서도 즉시 proposal/commit이 발생한다.

## 상세 발견

### Run 루프와 초기화
- `main.py:746` — run 루프는 `for i in range(args.num_runs)`로 반복하며 매 run마다 새 `InstrumentedScheduler`를 생성한다.
- `main.py:787` — 각 run에서 `run_once(cfg_run, rm, am, ...)` 호출로 스케줄 실행. `ResourceManager`(`rm`)는 run 간 공유됨(연속성 의도).

### Scheduler의 시작 시각과 이벤트 시딩
- `scheduler.py:55` — `Scheduler.__init__`에서 `self.now_us = 0.0`으로 초기화.
- `scheduler.py:92` — 초기 `QUEUE_REFILL` 이벤트를 `self.now_us`(0.0)에 시딩.
- `scheduler.py:165` — `QUEUE_REFILL`는 기본 주기 50us로 반복해 시간 전진을 유도하지만, 두 번째 run의 `run_until_us`가 첫 run 종료 시각과 비슷하면 창구에 도달하지 못함.

### Proposer의 윈도 체크와 RM 시간
- `proposer.py:1492` — `t0 >= (now + W)`이면(instant가 아닌 경우) `window_exceed`로 후보 탈락. 기본 `W`는 `policies.admission_window`이며 `main.py:633`에서 1.0us로 기본값 채움.
- `resourcemgr.py:395` — `feasible_at`는 `t0 = max(start_hint(now), earliest_planescope)`로 계산. run1 이후에는 각 plane의 `avail`이 큰 값이므로 run2에서 `now=0`일 때 `t0`도 매우 큼.

### 증상 연결 고리
- run2 시작 시점: `now=0` vs RM의 `avail≈run1_end` → 대부분의 후보 `t0≈run1_end` → `t0 - now ≫ W` → 모든 후보 `window_exceed` → 커밋 0.

## 코드 참조
- `main.py:746` — per-run 루프 진입.
- `main.py:787` — `run_once` 호출(공유 `rm` 사용).
- `scheduler.py:55` — `now_us` 초기값 0.0.
- `scheduler.py:92` — 초기 `QUEUE_REFILL` 시딩 시각이 `now_us`에 종속.
- `scheduler.py:310` — admission window 체크(`p.start_us >= (now + W)`).
- `proposer.py:1481` — `feasible_at(..., start_hint=now)` 호출.
- `resourcemgr.py:395` — `t0 = max(start_hint, earliest_planescope)` 계산.

## 아키텍처 인사이트
- 연속 실행(스냅샷/재개) 철학은 PRD §6의 “스냅샷/재개”와 일치: 시뮬레이션 시간을 이어서 전개해야 함.
- run 간 `ResourceManager`는 누적 타임라인을 보존하는 반면, `Scheduler`의 시간 원점은 재설정되는 현재 구조가 불일치의 직접 원인.

## 개선 방안 비교
- 옵션 A — Scheduler 시작 시각 정렬(권장)
  - 장점: 최소 변경으로 연속성 보장; window 정책 유지; 결정성 유지.
  - 단점: 전역 최대 avail 이후에 남은 버스 예약이 있으면 일부 후보는 여전히 지연될 수 있음(리필 주기로 자연 전진).
  - 위험: 없음(시뮬레이션 시간 연속성 증가는 의도와 부합).
- 옵션 B — 두 번째 run 이후 `admission_window`를 크게 설정
  - 장점: 코드 변경 없이 CLI로 해결 가능.
  - 단점: 윈도 정책이 의미를 잃음; 매우 미래 시점의 예약 허용으로 해석 혼동.
  - 위험: 테스트/분석 결과의 시간 해석 왜곡.
- 옵션 C — run마다 `ResourceManager` 초기화
  - 장점: 각 run을 독립 시뮬레이션으로 유지.
  - 단점: 요구한 “연속성” 상실; 기존 출력 비교 단절.
  - 위험: PRD §6 의도와 충돌.

## 구체적 제안(옵션 A 구현 스케치)
- 새 run 시작 전 RM의 현재 전역 시각을 계산: `t0 = max(avail_us for (_, _), avail_us in rm.snapshot()['avail'].items())`.
- `Scheduler`에 선택적 `start_at_us` 인자를 추가해 초기 `now_us`와 초기 `QUEUE_REFILL` 시각을 `t0`에 맞춰 시딩.
- `main.run_once(...)`에서 두 번째 run부터 `start_at_us=t0`로 생성.
- 보완: 로그/스냅샷에 run 경계가 반영되도록 파일명에 run 인덱스를 지속 포함(현행 유지).

## 관련 연구
- `research/2025-09-07_21-57-06_queue_refill_phase_key_past_key.md` — PHASE_HOOK 타이밍과 키 파생에 대한 맥락.
- `research/2025-09-07_23-41-59_op_chain_multi_consistency.md` — 연쇄/타임라인 일관성 이슈.

## 미해결 질문
- 버스 예약이 plane avail보다 늦게 끝나는 경우, run 경계에서 `start_at_us`를 버스 최종 종료에 맞출 필요가 있는가? (현 구조에서는 리필 주기 전진으로 수렴하나, 초기 now를 `max(plane_avail, last_bus_end)`로 설정하는 옵션도 고려 가능.) -> (검토완료) 최종 경로에 맞추면 됨.