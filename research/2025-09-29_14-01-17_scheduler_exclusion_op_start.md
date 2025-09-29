---
date: 2025-09-29T14:01:17+09:00
git_commit: 7826919573d0ac03d86e680b8203c2cfb64f17d5
branch: main
repository: nandseqgen_v2
topic: "Risk of deferring ResourceManager commit to _handle_op_start"
tags: [research, codebase, scheduler, resourcemgr]
status: complete
last_updated: 2025-09-29
---

# 연구: Risk of deferring ResourceManager commit to _handle_op_start

**Date**: 2025-09-29T14:01:17+09:00
**Git Commit**: 7826919573d0ac03d86e680b8203c2cfb64f17d5
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
여러 state 에 의한 exclusion 적용이 현재 Scheduler 의 `_propose_and_schedule` 와 `_flush_backlog_entry` 에서 `rm.commit` 호출로 구현되어 있는데, 이 커밋을 `_handle_op_start` 단계로 미루면 어떤 위험이 발생하는지 조사

## 요약
- `rm.commit` 이 예약 직후 실행되면서 plane/die 배타 윈도우, 버스 예약, state 타임라인, 래치 상태가 즉시 반영되어 후속 제안이 동일 자원을 재사용하지 못하도록 막고 있다.
- 커밋을 `_handle_op_start` 로 지연하면 스케줄 시점과 실제 시작 시점 사이에 `ResourceManager` 전역 상태가 비어 있어 후속 배치가 동일 타임슬롯/플레인에 중첩될 수 있으며, state 기반 배타 규칙을 우회하게 된다.
- 지연 커밋은 `register_ongoing`, `move_to_suspended_axis`, `phase_key_at` 등 커밋 시점 부가 효과를 끊어 suspend/resume 재시작과 phase-key 가드에 중대한 회귀를 유발한다.

## 상세 발견

### Scheduler Commit Timing
- `_flush_backlog_entry` 와 `_propose_and_schedule` 모두 `rm.reserve` 성공 후 같은 tick 안에서 `rm.commit` 을 호출해 예약 결과를 즉시 전역 상태로 승격한다 ([scheduler.py:593](scheduler.py#L593), [scheduler.py:1186](scheduler.py#L1186)).
- 커밋 단계에서 `op_uid` 를 할당하고 `register_ongoing` 을 호출함으로써, ResourceManager 가 순간적으로라도 작업을 "진행 중"으로 인식하도록 보장한다 ([scheduler.py:639](scheduler.py#L639), [scheduler.py:1357](scheduler.py#L1357)).

### ResourceManager Exclusion Mechanics
- `rm.reserve` 는 트랜잭션 로컬 버퍼에 plane/die 윈도우와 버스 슬롯을 적재하지만, 실질적인 배타성은 `rm.commit` 으로 `_plane_resv`, `_excl_die`, `_bus_resv` 등에 반영될 때 확정된다 ([resourcemgr.py:695](resourcemgr.py#L695), [resourcemgr.py:818](resourcemgr.py#L818)).
- 커밋 시 state 타임라인 `_st.reserve_op` 와 래치, SUSPEND 메타 이동, cache/ODT 토글 등 부수 효과가 실행되어 동일 플레인/다이에 대한 state 기반 exclusion 과 재개 로직을 구동한다 ([resourcemgr.py:833](resourcemgr.py#L833), [resourcemgr.py:886](resourcemgr.py#L886)).

### Risks When Commit Moves To `_handle_op_start`
- scheduler tick 사이에 커밋이 지연되면 후속 `rm.reserve` 호출이 이전 배치의 state 타임라인/배타 윈도우를 보지 못해, state overlap 검사가 통과해버리고 다중 state exclusion 규칙이 손상된다; `_handle_op_start` 는 단순 로깅만 수행하므로 현재 구조에서는 커밋을 옮길 수 있는 저장소가 없다 ([scheduler.py:412](scheduler.py#L412), [resourcemgr.py:818](resourcemgr.py#L818)).
- 지연 커밋은 `register_ongoing` 호출도 늦춰 resume 경로가 `consume_suspended_op_ids` 로 기대하는 진행 중 메타를 찾지 못하게 하고, `PROGRAM_SUSPEND` 커밋시 이동되어야 할 중단 메타가 제때 이동하지 않아 backlog/refill 파이프라인이 깨질 수 있다 ([scheduler.py:1357](scheduler.py#L1357), [resourcemgr.py:894](resourcemgr.py#L894)).
- `phase_key_at` 은 커밋 시점에 state 타임라인이 업데이트된다는 가정 하에 즉시 Query 되는데, 커밋 지연은 동일 시각에 예약된 다른 배치가 잘못된 phase key 를 읽게 하여 Option-B 강제 규칙이 무력화될 위험이 있다 ([scheduler.py:1234](scheduler.py#L1234), [resourcemgr.py:834](resourcemgr.py#L834)).

## 코드 참조
- `scheduler.py:593` - backlog flush 경로에서 즉시 `rm.commit`
- `scheduler.py:1186` - 프로포즈 경로에서 `rm.commit` 으로 배치 확정
- `scheduler.py:412` - `_handle_op_start` 는 현재 이벤트 로깅만 수행
- `resourcemgr.py:818` - 커밋이 plane/die/window, state 타임라인, 래치 등을 전역 상태에 반영
- `resourcemgr.py:894` - 커밋 시 suspend 메타 이동 및 state 트렁케이션 수행

## 아키텍처 인사이트
- Scheduler 는 제안 성공 즉시 ResourceManager 상태를 갱신하고, 이벤트 큐는 이후 시작/종료 시점을 재현하는 순수 로깅 계층이다. 커밋을 이벤트 단계로 미루면 제어 루프가 두 단계로 갈라져, proposal-validation 과 state 갱신이 분리되어 일관성이 깨진다.
- ResourceManager 의 배타 규칙은 전적으로 커밋된 윈도우를 기반으로 하므로, 트랜잭션을 장시간 보존하거나 별도 pending 저장소를 마련하지 않는 한 커밋 지연은 다이/플레인 동시성 모델을 허무는 변경이다.

## 관련 연구
- [research/2025-09-29_11-37-31_suspend-resume-resources.md](research/2025-09-29_11-37-31_suspend-resume-resources.md)

## 미해결 질문
- 커밋을 지연하면서도 동일한 배타 보장을 유지하려면 트랜잭션 상태를 지속적으로 공유하거나 `rm.reserve` 에 pending 윈도우를 주입하는 보완 설계가 필요한데, 기존 코드와의 호환성을 어떻게 확보할지 추가 검토 필요
- `_handle_op_start` 로 커밋을 옮기는 요구가 정확히 어떤 타이밍 시맨틱을 목표로 하는지 명확히 정의되어야 하며, suspend/resume 및 phase-key 기반 기능에 대한 회귀 테스트 계획이 필요
