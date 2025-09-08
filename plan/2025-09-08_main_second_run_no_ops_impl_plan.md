---
title: "Plan — Align run start with RM time (fix: second run has no ops)"
date: 2025-09-08
author: Codex
status: draft
topic: main second run no-ops (num_runs>=2)
related: research/2025-09-08_10-18-44_main_second_run_no_ops.md, docs/PRD_v2.md
---

# Problem 1‑Pager

- 배경: CLI `--num-runs>=2`에서 run 간 연속성을 유지하려고 `ResourceManager`(RM)는 누적 타임라인을 보존하고, 각 run은 `main.py` 루프에서 새 `Scheduler`로 실행된다.
- 문제: 2번째 run부터 `Scheduler.now_us=0`으로 재시작하는 반면 RM의 각 (die,plane) `avail`은 이전 run 종료 시각을 유지한다. 그 결과 proposer의 최초 feasible `t0`가 `now`보다 크게 벌어져 `admission_window`(기본 1us)를 초과하며 대부분 후보가 `window_exceed`로 탈락해 커밋 0이 발생한다.
- 목표: 각 run을 RM의 전역 시각에서 재개해, 즉시 proposal/commit이 가능하도록 한다. CLI `--run-until`은 "run당 진행 시간" 의미를 유지한다.
- 비목표: RM의 예약/락/배제 규칙을 변경하지 않는다. proposer 정책, phase_conditional, 부가 CSV 스키마 변경 없음.
- 제약: 변경 범위를 최소화(호출/참조 안정성), 결정성/재현성 유지, 파일/함수 복잡도 제한(<=300 LOC/<=50 LOC) 준수.

# Root Cause (간단)
- `Scheduler.__init__`: `self.now_us=0.0` 고정, 초기 `QUEUE_REFILL`도 0에서 시딩.
- `run_until_us`는 절대 종료 시각으로 처리됨(`Scheduler.run`), per‑run 길이가 아님.

# Approach (권장: 옵션 A — Scheduler 시작 시각 정렬)

핵심 아이디어: 새 run의 시작 시각을 RM 전역 시각 `t0`로 맞추고, run 종료 시각을 `t_end = t0 + run_until_per_run`로 설정한다.

## 변경 사양

1) `Scheduler`에 선택적 `start_at_us` 인자 추가
   - 시그니처: `__init__(..., start_at_us: Optional[float] = None)`
   - 동작: 주어지면 `self.now_us = quantize(start_at_us)`로 설정하고, 초기 `QUEUE_REFILL`을 해당 시각에 시딩한다.
   - 기본(None)일 때는 현행과 동일(0.0에서 시작).

2) `main.run_once(...)`에서 시작/종료 시각 정합화
   - `snap = rm.snapshot()`에서 `t_avail = max(snap["avail"].values(), default=0.0)`.
   - (옵션) 버스 예약 고려: `t_bus = max(e for (_,e) in snap.get("bus_resv", [])) if any else 0.0`.
   - `t0 = max(t_avail, t_bus)`을 후보로 두되, 1차 구현은 `t_avail`만 사용(단순/안전). 플래그로 확장 가능.
   - `sched = InstrumentedScheduler(..., start_at_us=t0)`
   - per‑run 종료 시각: `t_end = t0 + run_until_us`로 변환하여 `sched.run(run_until_us=t_end)` 호출.

3) 가드/로깅
   - 로깅: run i 시작 시 `print(f"run{i+1} start_at={t0:.3f} end_at={t_end:.3f}")` (디버그 가독성).
   - `t0`/`t_end`는 `quantize` 적용.

## 영향 범위 / 호출 경로
- 호출: `main.py: run_once` -> `InstrumentedScheduler.__init__` -> `Scheduler.__init__`(start time 시딩) -> `Scheduler.run`.
- 참조: `resourcemgr.ResourceManager.snapshot().avail`와 `bus_resv`만 읽기. proposer/reserver 내부 로직 불변.

## 대안 비교
- 옵션 A(본안): Scheduler 시작 시각 정렬
  - 장점: 최소 변경, 정책/결정성 유지, PRD 스냅샷/재개 철학과 합치.
  - 단점: `run_until`이 절대 시각인 가정을 유지해야 하므로 변환층 필요.
  - 위험: 낮음.
- 옵션 B: `admission_window` 확대(런 기준)
  - 장점: 코드 변경 없음.
  - 단점: 윈도 정책 무력화, 미래 시점 예약 허용으로 결과 해석 왜곡.
  - 위험: 중간.
- 옵션 C: run마다 RM 재초기화
  - 장점: 독립 실험 용이.
  - 단점: 연속성 상실, PRD §6 의도와 충돌.
  - 위험: 중간.

# Tasks (작업 순서)

1) Scheduler에 `start_at_us` 인자 추가 및 초기 시딩 시각 정렬
   - 파일: `scheduler.py` — `Scheduler.__init__`
   - 수용 기준: `self.now_us == start_at_us`일 때 첫 `QUEUE_REFILL`가 같은 시각에 push됨.

2) `main.run_once`에서 `t0` 계산 및 `t_end` 변환 적용
   - 파일: `main.py`
   - 수용 기준: run2 이상에서 `t0 > 0`일 때도 per‑run 길이가 정확히 `--run-until`만큼 진행.

3) 로깅/관찰성 개선(선택)
   - 시작/종료 시각 출력, proposer_debug 로그에 now 시각 보존(현행 `propose_now` 키 사용).

4) 리그레션 테스트 추가(간단 E2E)
   - 케이스 A: `--num-runs 2 --run-until 20000 --seed 42` — 두 run 모두 `ops_committed > 0`.
   - 케이스 B: `--num-runs 3` — 각 run 커밋이 0이 아님(토폴로지/CFG에 따라 값은 달라도 비제로).
   - 케이스 C(경계): run2 시작 직후 종료 조건 검증(작은 `--run-until`로 즉시 종료되는지 확인).

# 테스트 계획 (PRD 규칙 준수)

- 목표: 결정성/연속성/윈도 정책 유지 검증.
- 성공 경로(E2E):
  - 동일 설정(시드·CFG)에서 기존 run1 산출물과 동일. run2부터 커밋이 발생하고, `operation_timeline_*.csv` start 시각이 run1 마지막 시각 이후로 연속.
- 실패 경로:
  - `start_at_us=None`일 때 기존 동작 보존(회귀 없음).
  - `--run-until`이 매우 작을 때(예: 1us), 즉시 종료하며 `ops_committed==0` 허용.
- 결정성:
  - 동일 시드에서 run 간 시작/종료 시각이 재현 가능해야 하며, CSV 해시 동일.

# 구현 메모

- `t0` 후보 산출
  - 1차: `t_avail = max(avail.values())`.
  - 확장: `t_bus = max(e for (_,e) in bus_resv)` 고려 → `t0 = max(t_avail, t_bus)`을 플래그로 활성화(`features.resume_consider_bus_end: true`).
- `run_until` 해석
  - `Scheduler.run()`이 절대 종료 시각을 비교하므로, per‑run 길이 보장을 위해 `t_end = t0 + run_until`로 변환해서 전달.
- 호환성
  - `start_at_us`가 None이면 기존과 동일. 서브클래스(`InstrumentedScheduler`)는 super 호출만으로 호환.

# Affected Files

- main.py — `run_once`, per‑run 루프(로그만 추가)
- scheduler.py — `Scheduler.__init__`, (필요 시) 타입 힌트 업데이트
- (읽기 전용) resourcemgr.py — `snapshot()` 구조 이용

# 리스크와 완화

- 리스크: 잘못된 `t_end` 계산로 조기 종료 또는 무한 진행 위험.
  - 완화: 단위 테스트로 `t0=0`/`t0>0` 케이스를 분리 검증. `quantize` 적용.
- 리스크: 이벤트 순서 불변 가정이 깨질 가능성.
  - 완화: 초기 `QUEUE_REFILL`만 시작 시각에 시딩하며, 나머지는 동일 주기로 유지.

# 산출물(수용 기준)

- run2 이상의 `Run i results: ops_committed > 0` 확인.
- `operation_timeline_*.csv`에서 run 간 시간 축 연속성(단조 증가) 보장.
- 회귀 없음: run1 결과는 동일(시드/CFG 고정 시).

# 다음 단계(선택)

- CLI/CFG 플래그: `--resume-align=rm|rm+bus|none` 또는 `features.resume_consider_bus_end` 추가.
- 스냅샷 파일(`save_snapshot`)에 run i 시작 시각 기록.

