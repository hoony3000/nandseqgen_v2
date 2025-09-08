date: 2025-09-08T14:10:00+09:00
owner: codex
status: planned
source: research/2025-09-08_13-55-13_suspend_resume_timeline_and_reschedule.md
related:
  - docs/PRD_v2.md:366
  - resourcemgr.py:508
  - scheduler.py:480

# Implementation Plan — SUSPEND→RESUME Timeline + Reschedule

## Problem 1‑Pager
- 배경: ERASE/PROGRAM 진행 중 SUSPEND 후 RESUME 시, 타임라인에서 SUSPEND 시점의 `CORE_BUSY` 절단이 이뤄지지 않고, RESUME 이후 잔여 구간이 재예약되지 않는다. PRD v2 §5.5의 SUSPEND→RESUME 워크플로(타임라인 수술 + 잔여 구간 재스케줄)와 불일치.
- 문제: `op_state_timeline`이 연속으로 남아 유한·비중첩 불변식이 약화되며, 재개 후 잔여 `CORE_BUSY` 미반영으로 지표/후속 제안에 오류가 생긴다.
- 목표: (1) ResourceManager에 SUSPEND 시점 절단 및 ongoing→suspended 메타 이전을 구현, (2) Scheduler에 RESUME 특례 체이닝(RESUME 종료 직후 잔여 구간 예약)을 추가. PRD 규칙을 준수하며, 기존 이벤트/후크 경로와 일관성을 유지한다.
- 비목표: RESET 규칙 전반 재설계, 분석/CSV 출력 포맷 변경, E2E UI/시각화 작업. RESUME의 affect_state 설정 변경은 하지 않는다(현 구성 유지).
- 제약: 
  - 유한·비중첩 타임라인 불변식 유지(실세그먼트에 `.END` 미추가).
  - 파일/함수 크기와 복잡도 가이드 준수(함수 ≤ 50 LOC, CC ≤ 10; 초과 시 분리).
  - 기존 테스트 결정성/독립성 보장.

## 근거 및 참조
- 스펙: `docs/PRD_v2.md:366` SUSPEND→RESUME 워크플로, state_timeline 관리 규칙.
- 연구: `research/2025-09-08_13-55-13_suspend_resume_timeline_and_reschedule.md`
- 코드 진입점: `resourcemgr.py:508` `commit(...)`, `_StateTimeline` 편집 API 부재(`resourcemgr.py:21`), `register_ongoing/move_to_suspended/resume_from_suspended` (`resourcemgr.py:876/881/902`), `scheduler.py:480` 이벤트 방출/후크.
- config: `config.yaml` 내 `ERASE_RESUME`/`PROGRAM_RESUME`는 `affect_state: true` 유지.

## 대안 비교(요약)
- 대안 A: Scheduler 체이닝 + RM 타임라인 수술(권장)
  - 장점: 레이어링 보존, 일반 예약 경로 재사용, 관측지표/후크 일관.
  - 단점: 양측 모듈 수정 필요, 메타 동기화가 필요.
  - 위험: 잔여 시간/타겟 산출 오류 시 재스케줄 오동작.
- 대안 B: RM 단독 처리(RESUME 커밋 시 RM이 자체 재예약)
  - 장점: 한 곳에서 처리.
  - 단점: 스케줄링 책임 혼재, 검증/규칙 우회 위험.
  - 위험: 배치 윈도우/중첩 규칙 위반 가능.
→ 선택: 대안 A.

## 설계 개요
1) ResourceManager — 타임라인 수술 + 메타 이전
  - `_StateTimeline`에 절단 API 추가:
    - `truncate_after(die:int, plane:int, t:float, pred:Callable)` — 시각 `t` 이후 구간 제거, 경계 교차 세그먼트는 `end_us=t`로 절단. `starts` 인덱스 동기화.
    - 용도: `pred(seg) == (seg.op_base in ERASE/PROGRAM* and seg.state == 'CORE_BUSY')` 등 축별 조건.
  - `commit(...)` 내 SUSPEND 분기에서 수행(시각 = 해당 SUSPEND `start_us`):
    - `move_to_suspended(die, op_id=None, now_us=start)` 호출(ongoing→suspended 메타 이동 및 `remaining_us` 계산).
    - 대상 plane 범위: 우선 die 전체 plane 처리. `ongoing_ops` 메타에 targets가 있으면 그 plane만 타겟팅(정밀화).
    - `_StateTimeline.truncate_after(...)`로 `ERASE/PROGRAM.*.CORE_BUSY`를 절단/제거.
  - 예약 시 기록 강화: ERASE/PROGRAM 예약 성공 시 `register_ongoing(...)` 호출(스케줄된 `start_us/end_us`, targets 저장) — 호출 지점은 Scheduler가 보유한 예약 레코드에서 RM API 호출로 위임.
2) Scheduler — RESUME 특례 체이닝
  - `commit(txn)` 이후 예약 레코드 순회에서 `base in {ERASE_RESUME, PROGRAM_RESUME}` 발견 시:
    - `die = targets[0].die` 기준으로 `rm.suspended_ops(die)`에서 마지막 항목 조회(meta).
    - `remaining = meta.remaining_us`가 0보다 크면 잔여 구간용 stub op 생성:
      - `base = meta.base`(예: `ERASE`, `PROGRAM_SLC` 등)
      - `states = [("CORE_BUSY", remaining, bus=False)]` 단, 특수 케이스: `CACHE_PROGRAM_SLC/ONESHOT_CACHE_PROGRAM` 재개 시 DATA_IN 등 비바스 상태도 포함 필요(연구 결론). 이 경우 config의 원본 states를 참조해 잔여 비율로 분할 또는 최소 `CORE_BUSY`만 우선 구현 후 추후 확장.
      - `targets = meta.targets`
      - scope: 기존 base 규칙 함수 `_base_scope` 활용.
    - 동일 txn 상에서 `rm.reserve(txn, op_stub, targets, scope, duration_us=remaining)` 호출로 RESUME 종료 직후 연속 예약(원자성 확보).
    - RM의 `commit(txn)`에 의해 타임라인/이벤트/후크 일관 유지.
  - RM 메타 동기화: RESUME 커밋 시 `rm.resume_from_suspended(die, op_id=None)` 호출(선택; 메타를 다시 ongoing으로 이동).

## 변경 상세(파일/함수)
- resourcemgr.py
  - `_StateTimeline`에 `truncate_after(...)` 신규 메서드 추가(간단 루프 + bisect 인덱싱 업데이트; 40~50 LOC 이내).
  - `ResourceManager.commit(...)`의 SUSPEND 분기 보강:
    - SUSPEND `start` 시각 기준으로 die/targets plane에 대해 `truncate_after(..., pred=CORE_BUSY of ERASE/PROGRAM*)` 실행.
    - `move_to_suspended(die, op_id=None, now_us=start)` 호출.
  - 공개 메타 조회 API 유지(`suspended_ops`, `ongoing_ops`).
- scheduler.py
  - 예약 성공 처리 구간에서 `ERASE/PROGRAM` 커밋 레코드에 대해 `rm.register_ongoing(die, op_id, op_name, base, targets, start, end)` 호출 추가.
  - `_emit_op_events(...)` 호출 전후 또는 같은 루프에서 `base == ERASE_RESUME/PROGRAM_RESUME`일 때 RESUME 특례 체이닝:
    - `meta = rm.suspended_ops(die)[-1]` 활용, `remaining_us` 기반 stub op 구성 후 `rm.reserve(txn, ...)`로 연속 예약.
    - 스텁 op는 `bus=False`로 상태만 등록되게 함.

## 테스트 계획(회귀 포함)
- 단위: tests/test_resourcemgr_suspend_resume_timeline.py
  - 케이스1: ERASE 진행 중 SUSPEND → 타임라인에서 ERASE.CORE_BUSY가 `t_suspend`에서 절단되고 이후 구간 제거.
  - 케이스2: PROGRAM 진행 중 SUSPEND → 동일 절단 확인. CACHE_PROGRAM 계열 특이 케이스는 우선 CORE_BUSY만 검증.
- 통합: tests/test_scheduler_resume_chain.py
  - 케이스3: ERASE 예약(100us) → t=20us SUSPEND → t=30us RESUME → RESUME 종료 직후 잔여 80us가 연속 예약됨. `phase_key_at` 및 이벤트/후크(OP_START/OP_END/PHASE_HOOK) 일관성 확인.
  - 케이스4: PROGRAM_SLC 유사 시나리오 재현.
- 실패 경로: 잔여 시간이 0 이하인 경우 체이닝 미수행, 중복 예약/겹침 없이 정상 종료.

## 관측/로깅
- 구조화 메트릭: `scheduler.metrics['last_commit_bases']`, `['last_reserved_records']`로 검사 가능.
- RM 스냅샷: `rm.snapshot()['timeline']` 및 `suspended_ops/ongoing_ops`로 검증.
- CSV: 필요 시 `out/operation_timeline_*.csv`에 CORE_BUSY 절단 및 잔여 구간 반영 여부 확인.

## 마이그레이션/호환성
- config: RESUME `affect_state: true` 유지(`config.yaml:550, 578`). 특례 체이닝은 코드 레벨에서 처리.
- RESET과의 상호작용: 기존 `reset()`/스냅샷 복원 경로들이 `_suspended_ops/_ongoing_ops`를 초기화하므로 추가 변경 불필요.

## 작업 순서(작게 나누기)
1) `_StateTimeline.truncate_after(...)` 추가 + 단위 테스트(독립).
2) RM `commit(...)`에 SUSPEND 절단 + `move_to_suspended(...)` 연동.
3) Scheduler에 `register_ongoing(...)` 호출 추가.
4) Scheduler RESUME 특례 체이닝 구현(동일 txn 내 stub 예약).
5) 단위/통합 테스트 작성 및 통과.

## 예상 영향도(요약)
- 타임라인 편집으로 유한·비중첩 불변식 강화. RESUME 후 잔여 구간이 타임라인/후크/CSV에 반영되어 분석/제안 일관성 개선.
- 리스크: 잘못된 잔여 계산/타겟 매칭 시 예약 실패 또는 겹침. 테스트로 커버.

## 파일 레퍼런스
- `docs/PRD_v2.md:366` — SUSPEND→RESUME 규칙 원문.
- `resourcemgr.py:21` — `_StateTimeline` 정의 시작(편집 API 추가 지점).
- `resourcemgr.py:508` — `commit(...)` 진입점(SUSPEND 분기 보강).
- `resourcemgr.py:876` — `register_ongoing(...)` 메타 기록.
- `resourcemgr.py:881` — `move_to_suspended(...)` 메타 이전.
- `resourcemgr.py:902` — `resume_from_suspended(...)` 메타 복귀.
- `scheduler.py:400` — 예약 성공 처리/이벤트 방출 경로.
- `scheduler.py:480` — OP_START/OP_END/PHASE_HOOK 생성.

## 노트
- RESUME의 타임라인 비등록(affect_state=false) 대안은 검토했으나, 현 도메인 합의상 RESUME도 `CORE_BUSY`를 갖고 `affect_state=true` 유지. 체이닝 스텁으로 자연스럽게 반영한다.

