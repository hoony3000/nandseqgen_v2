---
date: 2025-09-08T13:55:13+09:00
researcher: codex
git_commit: cba55e0dc0e35d8697e8aeac3f7004443a7f3a80
branch: main
repository: nandseqgen_v2
topic: "SUSPEND→RESUME 타임라인 반영 및 재스케줄"
tags: [research, codebase, resourcemgr, scheduler, suspend, resume, state_timeline]
status: complete
last_updated: 2025-09-08
last_updated_by: codex
---

# 연구: SUSPEND→RESUME 타임라인 반영 및 재스케줄

**Date**: 2025-09-08T13:55:13+09:00
**Researcher**: codex
**Git Commit**: cba55e0dc0e35d8697e8aeac3f7004443a7f3a80
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
SUSPEND→RESUME 시에 스케줄 상 SUSPEND 시점의 중단이 `op_state_timeline`에 반영되지 않고, RESUME 이후에도 중단되었던 ERASE/PROGRAM 동작이 재스케줄되지 않는다. PRD 5.5(ResourceManager) ‘state_timeline 관리 방법’의 SUSPEND→RESUME 규칙과 다르다. 이를 개선하는 방법은?

## 요약
- 현재 구현에서 ResourceManager는 SUSPEND/RESUME 축 상태(ERASE/PROGRAM)를 분리해 추적하지만, SUSPEND 시점에 기존 `ERASE/PROGRAM .CORE_BUSY` 세그먼트를 절단(truncate)하거나 `suspended_ops`로 메타데이터를 옮기는 타임라인 수술이 없다.
- Scheduler도 RESUME 처리에 특례 루틴이 없어, RESUME 후에 원래 동작의 잔여 구간을 추가로 예약하지 않는다.
- 개선 방향: (1) RM에 타임라인 수술 + ongoing→suspended 메타 이전을 추가, (2) Scheduler에 RESUME 특례 체이닝(RESUME 끝 직후 잔여 CORE_BUSY 예약)을 추가. 필요 시 RESUME의 타임라인 등록은 비등록(affect_state=false)로 일치화.

## 상세 발견

### 스펙 근거 (PRD)
- `docs/PRD_v2.md:360` — state_timeline 관리 방법 개요
- `docs/PRD_v2.md:366-374` — SUSPEND→RESUME 워크플로: CORE_BUSY 꼬리 절단, suspended_ops로 이전, RESUME 종료 직후 잔여 구간 재등록 규칙

### 현재 코드 상태
- RM 커밋 훅: ODT/CACHE/SUSPEND 축 상태는 반영되지만, SUSPEND 시 타임라인 절단/메타 이전은 없음
  - `resourcemgr.py:508` — `commit(...)` 본체 시작
  - `resourcemgr.py:531` — ODT 상태 갱신
  - `resourcemgr.py:542` — CACHE 상태 갱신
  - `resourcemgr.py:557` — ERASE/PROGRAM SUSPEND/RESUME 축 상태 갱신(분리 축)
- RM 타임라인 편집 API 부재: `_StateTimeline`에 삭제/절단 메서드 없음
  - `resourcemgr.py:19` — `_StateTimeline` 정의 시작
- ongoing/suspended 메타용 API는 존재하나 미사용
  - `resourcemgr.py:876` — `register_ongoing(...)`
  - `resourcemgr.py:881` — `move_to_suspended(...)`
  - `resourcemgr.py:902` — `resume_from_suspended(...)`
- Scheduler: RESUME 특례 루틴 부재(예약 레코드 후 추가 예약 없음)
  - `scheduler.py:300` — 후보 예약 루프와 커밋/롤백 처리
  - `scheduler.py:522` — OP_START/OP_END/PHASE_HOOK 생성. 특례 없음

## 개선안(대안 비교)

- 대안 A: Scheduler 주도 체이닝 + RM 타임라인 수술 (권장)
  - 내용: 
    - RM: SUSPEND 커밋 시 (die 전체 plane에 대해) `ERASE|PROGRAM.*.CORE_BUSY` 세그먼트를 `time_suspend`에서 절단하고 이후 세그먼트 제거. `ongoing_ops` → `suspended_ops`로 메타 이동(잔여 시간 계산).
    - Scheduler: `ERASE_RESUME/PROGRAM_RESUME` 예약이 성공하면, 같은 트랜잭션에서 RESUME의 `end_us` 직후에 중단된 원래 동작의 “잔여 CORE_BUSY만”을 가짜 op(stub)로 추가 예약. 정상 커밋 경로를 타므로 타임라인·PHASE_HOOK·OP_* 이벤트가 모두 생성됨.
    - RESUME 자체는 타임라인 비등록이 스펙상 자연스러움(affect_state=false). 현재 `config.yaml`은 true이므로, 코드에서 예외 처리하거나 config 수정 필요.
  - 장점: 
    - 재개 구간도 일반 예약처럼 다뤄져 후속 HOOK/통계/CSV에 일관 반영
    - 구현 경계 명확(RM: 수술/메타, Scheduler: 체이닝)
  - 단점/위험:
    - Scheduler에 특례 분기가 추가됨(유지보수 포인트 증가)
    - 잔여 구간 stub 생성 시 대상 plane 집합/범위 정확성 필요

- 대안 B: RM 자동 복원(RESUME 커밋 시 타임라인 직접 삽입)
  - 내용: RM이 RESUME 커밋 처리에서 잔여 CORE_BUSY 세그먼트를 `end_us` 이후에 직접 타임라인에 삽입(스케줄러 관여 없음).
  - 장점: Scheduler 변경 최소화
  - 단점/위험:
    - PHASE_HOOK/OP_* 이벤트가 생성되지 않아 분석/후속 제안 구동력이 약화
    - `operation_timeline_*.csv`에 재개 구간이 누락되어 사용자 관찰과 불일치

선택: 대안 A (분석/후속 제안/CSV 일관성 확보, 스펙 기대치와 부합)

## 제안 구현 지침

### 1) RM: SUSPEND 시 타임라인 절단 + 메타 이동
- `_StateTimeline`에 편집 API 추가(개념):
  - `truncate_from(die:int, plane:int, t:float, pred:Callable[[seg], bool]) -> None`
    - `seg.start < t < seg.end`이면 `seg.end = t`로 절단
    - `seg.start >= t`이고 `pred(seg)` 참이면 해당 seg 제거
- `commit(...)` 내 SUSPEND 처리 분기에서 호출:
  - ERASE_SUSPEND: 
    - 대상: 모든 plane의 `op_base.startswith('ERASE') and state == 'CORE_BUSY'`
    - 동작: `truncate_from(d,p,time_suspend,pred)` 실행
    - 메타: `register_ongoing(...)`가 존재한다면 `move_to_suspended(die, op_id=None, now_us=time_suspend)`로 잔여 시간 기록
  - PROGRAM_SUSPEND: 
    - 대상: `('PROGRAM' in op_base) and state == 'CORE_BUSY'`
    - 동일 동작

### 2) Scheduler: RESUME 특례 체이닝
- 예약 루프에서 각 예약 레코드 `rec` 처리 시, `base in {ERASE_RESUME, PROGRAM_RESUME}`라면:
  - `die = rec.targets[0].die`, `t_resume_end = rec.end_us`
  - `meta = rm.suspended_ops(die)`에서 해당 축 최신 1건 조회(또는 축별 API 도입)
  - `remaining = meta.remaining_us`가 0보다 크면 잔여 CORE_BUSY만 갖는 stub op 생성:
    - `states = [("CORE_BUSY", remaining)]` (bus=false)
    - `base = meta.base` (예: `ERASE`, `PROGRAM_SLC` 등)
    - `targets = meta.targets` (원래 예약 대상)
    - `scope = _base_scope(cfg, base)`
  - 동일 트랜잭션 `txn`에 `rm.reserve(txn, op_stub, targets, scope, duration_us=remaining)`로 추가 예약
  - 필요 시 RESUME 자체는 타임라인 미등록: 
    - 방법1: `resourcemgr._affects_state` 예외 처리로 `ERASE_RESUME/PROGRAM_RESUME`만 False 취급
    - 방법2: `config.yaml`에서 해당 op_bases의 `affect_state: false`로 교정

### 3) 보조 사항
- ERASE/PROGRAM 예약 시 `register_ongoing(...)` 호출(현재 미사용)로 SUSPEND 대비 메타 기록 강화
- Unit Test 초안(7.5 항목 구현):
  - ERASE 중 SUSPEND → `op_state_timeline`에서 CORE_BUSY가 절단되고 `ERASE_SUSPEND`(있다면)만 반영됨
  - RESUME → RESUME 종료 직후 잔여 CORE_BUSY가 재예약되어 타임라인/CSV 모두에 반영됨

## 코드 참조
- `docs/PRD_v2.md:355` — ResourceManager 관리 대상 목록
- `docs/PRD_v2.md:360` — state_timeline 관리 방법
- `docs/PRD_v2.md:366` — SUSPEND→RESUME 워크플로
- `resourcemgr.py:508` — `commit(...)` 진입점
- `resourcemgr.py:19` — `_StateTimeline` 구조(편집 API 필요)
- `resourcemgr.py:876` — `register_ongoing(...)`
- `resourcemgr.py:881` — `move_to_suspended(...)`
- `resourcemgr.py:902` — `resume_from_suspended(...)`
- `scheduler.py:300` — 예약 루프
- `scheduler.py:522` — HOOK/OP 이벤트 생성(재개 구간도 동일 경로 필요)

## 아키텍처 인사이트
- 타임라인은 “유한·비중첩” 불변식을 유지해야 하며(`docs/PRD_v2.md:88`), SUSPEND 절단은 이 불변식을 강화한다.
- RESUME 이후 잔여 구간을 일반 예약으로 다루면, 후속 제안/통계/가시화 경로가 모두 동일하게 작동한다.
- RESUME의 타임라인 비등록은 스펙에 더 근접하나, 현 config와의 충돌을 해소하려면 코드 예외 또는 config 교정이 필요하다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-08_12-36-14_suspend_state_split_and_not_defaults.md` — suspend 축 분리 및 NOT_* 기본 그룹 적용 배경/적용

## 관련 연구
- `research/2025-09-08_11-54-59_suspend_state_split_and_not_defaults.md`
- `research/2025-09-08_10-18-44_main_second_run_no_ops.md`

## 미해결 질문
- RESUME 자체를 타임라인에 비등록(affect_state=false)로 보는 것이 도메인 합의로 확정인가? (현재 config는 true) -> (검토완료) CORE_BUSY 가 있으므로 affect_state=true 이다.
- PROGRAM 계열 잔여 구간이 단일 CORE_BUSY로 충분한가? (현 구성에서는 충분하나, 다단계 PROGRAM 변형에 대비한 범용화 필요성) -> (검토완료) CACHE_PROGRAM_SLC/ONESHOT_CACHE_PROGRAM 의 경우 나머지 state(DATA_IN) 도 모두 등록.

