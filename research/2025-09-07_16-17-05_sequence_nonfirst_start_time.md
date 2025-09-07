---
date: 2025-09-07T16:17:05+0900
researcher: Codex
git_commit: 306b495154519224c6511bd6f5f3c7b7cc546347
branch: main
repository: nandseqgen_v2
topic: "Operation sequence: start time for non-first ops"
tags: [research, codebase, proposer, scheduler, resourcemgr, sequence]
status: complete
last_updated: 2025-09-07
last_updated_by: Codex
---

# 연구: 연쇄(Sequence) 내 후속 오퍼레이션의 예약 시간 산정

**Date**: 2025-09-07T16:17:05+0900
**Researcher**: Codex
**Git Commit**: 306b495154519224c6511bd6f5f3c7b7cc546347
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
operation sequence를 예약할 때 첫 operation이 아닌 후속 operation의 예약 시간은 어떻게 설정되는가?

## 요약
- 제안 단계(Proposer): 첫 op 종료 시각에 `policies.sequence_gap`을 더한 시각을 힌트로 `feasible_at`을 호출하여, 각 후속 op의 "계획 start"를 산출한다. 즉, "이전 op 종료(+gap) 이후 가능한 가장 이른 시각"으로 계획된다.
- 예약 단계(Scheduler/ResourceManager): 실제 예약 시에는 `ProposedOp.start_us`를 강제하지 않는다. 각 op는 다시 `reserve()`에서 `start = max(txn.now_us, earliest_planescope)`로 재산정되어 가능한 한 이른 시각으로 예약된다(동일 txn 내 선행 예약을 반영하지 않음). 따라서 실제 시작 시각은 proposer가 계획한 시작보다 앞당겨질 수도 있다(정책/자원 제약에 의해 제한될 뿐, 체이닝 자체가 강제되지는 않음).

## 상세 발견

### Proposer: 후속 op 계획 시각 산출
- 첫 op: `feasible_at(..., start_hint=now)`로 시작 시각 `t0` 산출 후 배치에 추가.
- 후속 op: `tcur = t_prev_end + sequence_gap`를 계산하고, `feasible_at(..., start_hint=tcur)`로 각 op의 계획 시작 시각을 산출한다.
- 참조:
  - `proposer.py:766` — `_preflight_schedule`: 체이닝 로직의 시작.
  - `proposer.py:776` — 첫 op `feasible_at(..., start_hint=now)`.
  - `proposer.py:784` — `gap = policies.sequence_gap` 적용.
  - `proposer.py:789` — 후속 op `feasible_at(..., start_hint=tcur)`로 계획.

### ResourceManager: feasible_at 의미
- `feasible_at`은 주어진 `start_hint` 이상에서 plane/bus/제외창 충돌이 없는 가장 이른 시각을 반환한다.
- 참조:
  - `resourcemgr.py:338` — `feasible_at` 구현 시작; `t0 = max(start_hint, earliest_planescope)`로 산출 후 각 제약 검사.

### Scheduler/ResourceManager: 실제 예약 시각 결정
- Scheduler는 proposer의 배치를 순회하며 `reserve(txn, op, targets, scope)`를 호출한다. 이때 `ProposedOp.start_us`는 전달되지 않는다.
- ResourceManager의 `reserve`는 내부에서 다시 시작 시각을 계산한다: `start = max(txn.now_us, earliest_planescope)`.
- 동일 txn 내 선행 예약은 commit 이전이므로 `_avail/_plane_resv`에 반영되지 않아, 후속 op가 선행 op의 종료 이후로 밀리도록 강제되지는 않는다(다이 레벨 단일/멀티 배제 등 일부 제약은 txn 페딩윈도우로 반영됨).
- 참조:
  - `scheduler.py:253` — 배치 예약 루프 진입.
  - `scheduler.py:284` — `rm.reserve(txn, op, p.targets, p.scope)` 호출(계획 start 미전달).
  - `resourcemgr.py:363` — `reserve` 구현 시작.
  - `resourcemgr.py:366` — `start = max(txn.now_us, earliest_planescope(...))`로 실제 시작 산정.
  - `resourcemgr.py:415` — `commit` 시점에 `_plane_resv/_avail` 갱신(후속 op 예약 중에는 반영 안 됨).

## 코드 참조
- `proposer.py:766` - `_preflight_schedule`으로 연쇄 계획 산출 시작.
- `proposer.py:784` - `policies.sequence_gap` 적용 위치.
- `proposer.py:789` - 후속 op의 `feasible_at(..., start_hint=tcur)` 호출.
- `resourcemgr.py:338` - `feasible_at`에서 `start_hint` 이상 가장 이른 시각 결정.
- `scheduler.py:284` - 실제 예약에서 `ProposedOp.start_us` 미사용, `reserve` 직접 호출.
- `resourcemgr.py:366` - `reserve`에서 시작 시각 재산정(`txn.now_us` 기준).

## 아키텍처 인사이트
- Proposer는 "계획" 상으로 연쇄를 이전 op 종료(+gap) 이후로 배치하지만, Scheduler/RM은 그 계획 시각을 강제하지 않는다. 결과적으로, 후속 op의 실제 예약 시각은 현재 tick 시각과 자원 가용성/정책 제약에 의해 다시 결정된다.
- 연쇄 간의 시간적 연속성을 보장하려면: (a) `reserve`에 계획 start를 전달하여 하한으로 존중하거나, (b) txn 내 선행 예약을 `feasible_at/reserve`에서 고려하도록 pending 상태를 검사하도록 확장하는 방안이 필요하다.

## 관련 연구
- `research/2025-09-07_07-01-11_read_not_scheduled_after_erase_program.md` — 창(window) 정책과 배치 예약 상호작용 분석.

## 미해결 질문
- 후속 op의 계획 시각(`ProposedOp.start_us`)을 실제 예약에 반영(최소 시작 시각 하한)할지 정책적으로 확정 필요.

