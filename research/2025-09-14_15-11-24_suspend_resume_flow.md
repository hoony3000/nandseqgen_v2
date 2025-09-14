---
date: 2025-09-14T15:11:24+09:00
researcher: codex
git_commit: 2ce0ed07bb28ff7bf08684142bc14666adab48aa
branch: main
repository: nandseqgen_v2
topic: "SUSPEND/RESUME에서 ongoing_ops·suspended_ops를 이용한 중단·재스케줄 흐름"
tags: [research, codebase, scheduler, resourcemgr, suspend, resume, ongoing_ops, suspended_ops]
status: complete
last_updated: 2025-09-14
last_updated_by: codex
---

# 연구: SUSPEND/RESUME에서 ongoing_ops·suspended_ops 기반 중단·재스케줄 흐름

**Date**: 2025-09-14T15:11:24+09:00
**Researcher**: codex
**Git Commit**: 2ce0ed07bb28ff7bf08684142bc14666adab48aa
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
SUSPEND/RESUME 동작 수행 시에 ongoing_ops, suspended_ops를 활용하여 진행 중인 PROGRAM/ERASE 동작이 어떤 흐름으로 중단되고 다시 리스케줄되는지?

## 요약
- 스펙상 흐름: 진행 중 ERASE/PROGRAM을 ongoing_ops로 추적 → SUSPEND 시점에 CORE_BUSY 꼬리를 절단하고 메타를 suspended_ops로 이동 → RESUME 이후 잔여 CORE_BUSY를 바로 연속 재예약한다.
- 현재 코드: SUSPEND/RESUME 축 상태(ERASE/PROGRAM) 자체는 커밋 시 갱신되지만, 타임라인 절단·메타 이동·RESUME 체이닝은 미연결 상태다. API는 준비되어 있으나 호출이 누락됐다.
- proposer는 SUSPEND/RESUME를 후보로 제안하며, PROGRAM_SUSPEND 시 RECOVERY_RD.SEQ가 suspended_ops의 페이지를 상속하는 로직이 있다.
- 구현 공백: Scheduler가 ERASE/PROGRAM 예약 시 register_ongoing을 호출하지 않고, SUSPEND 커밋 시 move_to_suspended, RESUME 후 잔여 구간 재예약 체이닝이 없다.

## 상세 발견

### PRD 기대 동작(요약)
- SUSPEND→RESUME 워크플로와 메타 관리 규칙이 명시됨.
  - docs/PRD_v2.md:362 — ERASE/PROGRAM 예약 시 ongoing_ops에 기록
  - docs/PRD_v2.md:363 — CORE_BUSY 중 SUSPEND 예약
  - docs/PRD_v2.md:364 — time_suspend 이후 CORE_BUSY 절단, suspended_ops로 이동, suspend_states 갱신
  - docs/PRD_v2.md:366 — RESUME는 별도 루틴: RESUME 종료 직후 중단됐던 원래 동작을 잔여 시간으로 재스케줄

### ResourceManager
- 축 상태 관리: 커밋 시 ERASE_SUSPEND/PROGRAM_SUSPEND 활성화, *_RESUME로 해제.
  - resourcemgr.py:582
  - resourcemgr.py:583
  - resourcemgr.py:585
  - resourcemgr.py:587
  - resourcemgr.py:592
- 타임라인 편집: `_StateTimeline`은 삽입/조회만 있고 절단(truncate) API가 없다(계획 문서에 제안되어 있음).
  - resourcemgr.py:21
- ongoing/suspended 메타 API는 존재(등록/이동/복귀)하나 호출 경로 없음.
  - resourcemgr.py:903 — register_ongoing(op 메타 기록)
  - resourcemgr.py:908 — move_to_suspended(remaining_us 계산 포함)
  - resourcemgr.py:929 — resume_from_suspended
- proposer 조회용 스냅샷 API는 제공됨.
  - resourcemgr.py:843 — ongoing_ops()
  - resourcemgr.py:873 — suspended_ops()

### Scheduler
- 예약 성공 시 커밋과 이벤트 방출(OP_START/OP_END, PHASE_HOOK)만 수행. ongoing/suspended 메타 연동 없음.
  - scheduler.py:425 — d.rm.commit(txn)
  - scheduler.py:396 — 예약 레코드 축적 및 메트릭
  - scheduler.py:418 — 예약 배치 커밋 후 루프에서 이벤트 생성
- SUSPEND/RESUME 특례(타임라인 절단/메타 이동/잔여 구간 체이닝) 구현 없음.

### Proposer
- phase_conditional에 따라 ERASE.CORE_BUSY/PROGRAM.CORE_BUSY에서 *_SUSPEND, *_RESUME를 제안.
  - op_state_probs.yaml:377 — ERASE_SUSPEND.CORE_BUSY
  - op_state_probs.yaml:389 — ERASE_RESUME.CORE_BUSY
  - op_state_probs.yaml:401 — PROGRAM_SUSPEND.CORE_BUSY
  - op_state_probs.yaml:413 — PROGRAM_RESUME.CORE_BUSY
- PROGRAM_SUSPEND가 선택되면 선택적으로 RECOVERY_RD.SEQ를 이어 붙이며, suspended_ops에서 최근 PROGRAM 타겟 페이지를 상속하는 규칙 보유.
  - proposer.py:980 — suspended_ops(die) 조회하여 마지막 PROGRAM 메타 활용

### Config 요약
- *_SUSPEND: scope=DIE_WIDE, affect_state=true, instant_resv=true (즉시 예약)
  - config.yaml:537
  - config.yaml:539
- *_RESUME: scope=DIE_WIDE, affect_state=true, instant_resv=false
  - config.yaml:548
  - config.yaml:550
- suspend 상태에 따른 금지 규칙(exclusions_by_suspend_state) 정의되어 제안 차단/허용 제어.
  - config.yaml:2298

## 현재 흐름 vs. 스펙 기대 비교
- 예약(ERASE/PROGRAM):
  - 기대: 예약 성공 시 register_ongoing(die, op_id, …, start/end)로 메타 기록
  - 실제: 호출 없음 → ongoing_ops 비어있음
- SUSPEND 발생:
  - 기대: time_suspend에서 CORE_BUSY 절단 + move_to_suspended(die, op_id?, now)로 remaining_us 계산/이동
  - 실제: 축 상태만 ERASE_SUSPENDED/PROGRAM_SUSPENDED로 표기, 타임라인 절단/메타 이동 없음
- RESUME 발생:
  - 기대: RESUME 종료 직후 남은 CORE_BUSY를 동일 txn에서 stub로 재예약
  - 실제: 특례 없음(잔여 구간 미등록)

## 구현 대안 및 권고
- 대안 A(권고): Scheduler 체이닝 + RM 타임라인 수술
  - 장점: 일반 예약 경로 재사용, 이벤트/후크/CSV 일관성, 책임 분리 명확
  - 단점/위험: 잔여 시간 계산/타겟 동기화 필요
- 대안 B: RM 단독 자동 삽입
  - 장점: Scheduler 변경 최소화
  - 단점: 이벤트/후크/CSV 누락, 예약 규칙 우회 위험

권고 구현 포인트(요지):
- resourcemgr._StateTimeline에 `truncate_after(die,plane,t,pred)` 추가하고, commit에서 *_SUSPEND 시 호출.
- scheduler._propose_and_schedule 커밋 경로에서 ERASE/PROGRAM 예약 성공 시 register_ongoing 호출.
- 같은 커밋 경로에서 *_RESUME 발견 시, rm.suspended_ops(die) 마지막 항목으로 remaining_us>0이면 원래 base의 CORE_BUSY만 갖는 stub를 end_us 직후에 즉시 연속 예약 후 커밋.

## 코드 참조
- docs/PRD_v2.md:362
- docs/PRD_v2.md:364
- docs/PRD_v2.md:366
- resourcemgr.py:21
- resourcemgr.py:582
- resourcemgr.py:903
- resourcemgr.py:908
- resourcemgr.py:929
- scheduler.py:425
- proposer.py:980
- config.yaml:537
- config.yaml:548
- op_state_probs.yaml:377
- op_state_probs.yaml:401

## 아키텍처 인사이트
- 타임라인은 유한·비중첩 불변식을 유지해야 하며, SUSPEND 절단은 이를 강화한다. 잔여 구간을 일반 예약으로 넣으면 HOOK/지표/CSV가 자연스럽게 일치한다.
- *_SUSPEND에 instant_resv를 부여하여 동작 중단을 즉시 적용하고, *_RESUME는 일반 경로로 처리해 배타/버스/래치 규칙을 준수한다.

## 역사적 맥락
- plan/2025-09-08_suspend_resume_timeline_and_reschedule_impl_plan.md: 문제 진단과 구현 계획(절단 API, 메타 이동, RESUME 체이닝)이 문서화됨.
- research/2025-09-08_13-55-13_suspend_resume_timeline_and_reschedule.md: 동일 주제의 선행 연구.

## 관련 연구
- research/2025-09-08_13-55-13_suspend_resume_timeline_and_reschedule.md

## 미해결 질문
- RESUME 자체의 affect_state를 true로 유지할지(false로 바꿔 타임라인을 깔끔히 할지) — 현재는 true(설정 기준).
- PROGRAM 잔여 구간을 단일 CORE_BUSY로 충분히 모델링 가능한가(특수 PROGRAM 변형 확장 고려 필요).

