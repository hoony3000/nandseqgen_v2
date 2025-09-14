---
date: 2025-09-14
author: codex
status: draft
topic: "SUSPEND/RESUME 연계: ongoing_ops/suspended_ops 메타 + 타임라인 절단 + 잔여 구간 재스케줄"
refs:
  - research/2025-09-14_15-11-24_suspend_resume_flow.md
  - docs/PRD_v2.md:340
  - resourcemgr.py:535
  - resourcemgr.py:843
  - resourcemgr.py:903
  - resourcemgr.py:908
  - resourcemgr.py:929
  - scheduler.py:292
  - scheduler.py:424
  - proposer.py:980
---

# 구현 계획: SUSPEND/RESUME에서 ongoing_ops·suspended_ops 기반 중단·재스케줄 흐름

본 계획은 research/2025-09-14_15-11-24_suspend_resume_flow.md의 결과를 반영하여, ERASE/PROGRAM 진행 중 SUSPEND/RESUME 동작 시 메타데이터(ongoing_ops/suspended_ops)와 op_state 타임라인을 일관되게 유지하고, RESUME 직후 잔여 CORE_BUSY를 재스케줄하는 경로를 추가한다.

## Problem 1-Pager
- 배경: 현재 `ResourceManager`는 SUSPEND/RESUME 축 상태 플래그만 관리하며, 진행 중 동작의 메타 전이(ongoing→suspended)와 타임라인 절단, RESUME 후 잔여 구간 재스케줄 체이닝이 누락되어 있음. `proposer`는 `suspended_ops()` 스냅샷을 일부 활용하나 기록이 비어 동작 이득이 제한됨.
- 문제: PRD 명세(문서 참조)에 따른 중단/복구 워크플로가 구현되지 않아, 분석 타임라인과 메타가 실제 의도와 불일치. RESUME 후 즉시 원래 동작을 이어붙이는 동작도 부재.
- 목표:
  - ERASE/PROGRAM 예약 성공 시 `ongoing_ops`에 메타 기록.
  - *_SUSPEND 커밋 시점에서 CORE_BUSY 타임라인 꼬리를 절단하고, 해당 메타를 `suspended_ops`로 이동하며 축 상태를 갱신.
  - *_RESUME 커밋 직후, `suspended_ops`의 잔여 시간을 가진 CORE_BUSY 단편을 연속 재스케줄.
  - 이벤트/후크/CSV 출력 일관성 유지, 되돌리기 가능하고 가드가 있는 변경.
- 비목표:
  - 주소 상태(AddressManager) 세부 규칙 변경, 전면적인 스케줄러 알고리즘 리라이트.
  - 외부 I/O 도입 또는 네트워크 통신.
- 제약:
  - 파일 ≤ 300 LOC, 함수 ≤ 50 LOC, 파라미터 ≤ 5, 순환 복잡도 ≤ 10을 유지하도록 분리/캡슐화.
  - 기존 이벤트/메트릭/CSV 포맷 호환성 유지.

## 현재 상태 요약(근거)
- PRD 요건: ERASE/PROGRAM 예약 시 ongoing 기록 → SUSPEND 시점에 CORE_BUSY 절단 + suspended_ops 이동 → RESUME 종료 직후 잔여 CORE_BUSY 재스케줄. `docs/PRD_v2.md:340` 부근.
- RM API: `ongoing_ops()`/`suspended_ops()` 스냅샷, `register_ongoing()`/`move_to_suspended()`/`resume_from_suspended()` 제공. `resourcemgr.py:843`, `resourcemgr.py:903`, `resourcemgr.py:908`, `resourcemgr.py:929`.
- RM 타임라인: `_StateTimeline` 삽입/조회는 있으나 절단(truncate) API 부재. `resourcemgr.py` 상단.
- Scheduler: 예약→`rm.commit()`→이벤트 방출 흐름에 메타 연동/RESUME 체이닝 없음. `scheduler.py:292`, `scheduler.py:424`.
- Proposer: PROGRAM_SUSPEND 관련하여 `res_view.suspended_ops(die)`를 조회해 RECOVERY_RD 등의 타겟 상속. `proposer.py:980`.

## 대안 비교(2가지 이상)
- 대안 A(권고): Scheduler 체이닝 + RM 타임라인 절단
  - 장점: 일반 예약 경로 재사용, 이벤트/후크/CSV 일관성, 책임 분리 명확
  - 단점: 잔여 시간 계산/타겟 동기화 주의 필요
  - 위험: 경계 시각 처리(quantize, 경계=세그먼트 시작/끝) 오류 시 타임라인 비일관성
- 대안 B: RM 단독 자동 삽입(RESUME 시 RM이 잔여 세그먼트를 직접 추가)
  - 장점: Scheduler 변경 최소화
  - 단점: 이벤트/후크 누락 가능, 예약 규칙 우회 위험, 추후 유지보수 어려움

→ 선택: 대안 A. RM은 타임라인 수술과 메타 전이, Scheduler는 예약/커밋 경로에 최소 훅만 추가.

## 구현 설계(요지)
1) RM 타임라인 절단 API 추가
   - `_StateTimeline.truncate_after(die:int, plane:int, at_us:float, pred:Callable)` 추가: `at_us` 이후의 세그먼트를 제거/절단. pred는 대상 세그먼트(예: `op_base in {ERASE,PROGRAM} && state==CORE_BUSY`) 선택.
   - `commit()` 내 *_SUSPEND 처리에서 호출해 CORE_BUSY 꼬리 절단.

2) Scheduler 예약 성공 시 ongoing 기록
   - `_propose_and_schedule()` 루프에서 ERASE/PROGRAM 예약 성공 후, 첫 타겟의 `die` 기준으로 `rm.register_ongoing(die, op_id=None, op_name, base, targets, start_us, end_us)` 호출.
   - 가드: `affect_state==true`인 ERASE/PROGRAM 류만 기록. 실패 시 무해(fail-closed).

3) *_SUSPEND 커밋 시 메타 전이 + 타임라인 절단
   - `rm.commit()`에서 `base==ERASE_SUSPEND/PROGRAM_SUSPEND` 분기 시:
     - 축 상태 시작 시각을 기록(기존 유지) + `move_to_suspended(die, op_id=None, now_us=start)` 호출.
     - 각 plane에 대해 `truncate_after(die, plane, start, pred=match_core_busy_of(base_family))` 수행.

4) *_RESUME 커밋 직후 잔여 CORE_BUSY 재스케줄(체이닝)
   - Scheduler 커밋 전/후 훅 중 커밋 성공 분기에서 최근 커밋된 베이스들을 검사.
   - 베이스 중 *_RESUME 발견 시, 해당 `die`의 `suspended_ops(die)`에서 마지막 항목을 조회하여 `remaining_us>0`이면, 동일 `base_family`의 CORE_BUSY 1‑세그먼트만 갖는 "stub op"를 `end_us(RESUME)` 직후에 즉시 연속 예약 후 같은 트랜잭션으로 커밋.
   - 커밋 완료 후 `rm.resume_from_suspended(die, op_id=None)` 호출로 suspended→ongoing 복귀(또는 제거).
   - 기능 가드: `CFG[features][suspend_resume_chain_enabled]=true`일 때만 활성화.

5) 관찰/메트릭/로그
   - 체이닝 동작 활성/비활성, 절단 수행, remaining_us 계산값을 디버그 수준 구조화 로그로 남김(민감정보 제외). 에러는 구체 메시지로 표기.

6) 호환성/롤백
   - `features.suspend_resume_chain_enabled=false` 기본값으로 머지 → 단계적 활성화.
   - 기능 가드로 롤백 즉시 가능. 타임라인 절단/메타 전이는 RESUME 체이닝 비활성 시에도 독립적으로 동작.

## 변경 포인트(정밀)
- `resourcemgr.py`
  - `_StateTimeline`에 `truncate_after(...)` 도입. 최대 30 LOC 내 구현, 내부 정렬 리스트를 bisect로 탐색 후 절단.
  - `commit()`의 SUSPEND 분기에서:
    - 기존 축 상태 갱신 유지.
    - `move_to_suspended(die, op_id=None, now_us=start)` 호출 추가.
    - 모든 plane에 대해 `truncate_after(die, plane, start, pred=match_family)` 호출.
  - 유틸: `match_family(seg)`는 `seg.op_base`가 ERASE/PROGRAM 계열이고 `seg.state=='CORE_BUSY'`일 때 True.

- `scheduler.py`
  - `_propose_and_schedule()` 예약 루프에서 ERASE/PROGRAM 성공 시 `rm.register_ongoing(...)` 호출.
  - 커밋 성공 후 베이스 목록 검사(`self.metrics["last_commit_bases"]` 또는 직전 `resv_records`)로 *_RESUME 존재 시:
    - 해당 die를 식별(RESUME 레코드의 `targets[0].die`).
    - `rm.suspended_ops(die)`에서 마지막 항목 조회 → `remaining_us`가 양수인 경우만 처리.
    - 동일 family의 CORE_BUSY 1‑세그먼트 스텁 op 구성 후, `rm.reserve(txn2, stub, targets, scope)` → `rm.commit(txn2)`로 연속 예약.
    - 성공 시 `rm.resume_from_suspended(die, op_id=None)` 호출.
  - Guard: `CFG[features][suspend_resume_chain_enabled]` false 시 no-op.

## 테스트 계획(회귀 포함)
- 단위(Unit)
  - `_StateTimeline.truncate_after()` 경계 케이스: 정확히 경계, 경계 이전/이후, 세그먼트 없음, 다중 세그먼트.
  - `move_to_suspended()` remaining_us 계산 검증(양수/0/음수→0 고정).

- 통합(Integration)
  1) ERASE 예약 → ongoing_ops 기록됨 → 일정 시각에 ERASE_SUSPEND → 타임라인 `ERASE.CORE_BUSY`가 suspend 시각에서 절단, `suspended_ops`로 이동, 축 상태=ERASE_SUSPENDED. CSV(op_state_timeline) 검증.
  2) 위 상태에서 ERASE_RESUME 예약 → 체이닝: RESUME 종료 직후 잔여 CORE_BUSY 스텁 예약됨 → `resume_from_suspended` 호출됨 → 최종 축 상태 해제. 일련의 OP_START/OP_END 이벤트 순서 검증.
  3) PROGRAM 동작도 동일 시나리오 반복. proposer의 RECOVERY_RD.SEQ가 `suspended_ops` 타겟을 상속하는 경로 상호작용 점검.
  4) 기능 가드 off 시: SUSPEND 절단/메타 전이는 수행되나 잔여 스텁은 생성되지 않음을 검증.

- 실패 경로
  - SUSPEND 시 ongoing 비어있음: move_to_suspended no-op, 예외 미발생 확인.
  - RESUME 시 suspended 비어있음: 체이닝 no-op, 예외 미발생 확인.

## 위험/대응
- 경계 시각 정합성: quantize 단위를 일관 적용. 절단 시 end_us==start_us 인 0‑길이 세그먼트 금지.
- 잔여 시간 0인 케이스: 체이닝 생략(방어 코드 포함).
- 이벤트/CSV 일관성: stub op도 정상 경로로 예약해 동일 훅/메트릭을 생성.

## 마이그레이션/롤아웃
- 1단계: 타임라인 절단 + 메타 전이(가드 불필요) → 테스트 통과 후 머지.
- 2단계: Scheduler 체이닝 기능 behind flag 추가 → 내부 검증 후 flag on.

## 작업 단위(작고 안전한 PR로 분할)
1) RM: `_StateTimeline.truncate_after()` 추가 + 단위 테스트.
2) RM: `commit()`의 *_SUSPEND 분기에 `move_to_suspended()` + 절단 호출 추가.
3) Scheduler: ERASE/PROGRAM 예약 성공 시 `register_ongoing()` 호출.
4) Scheduler: *_RESUME 체이닝 로직 추가(behind `features.suspend_resume_chain_enabled`).
5) 통합 테스트: ERASE/PROGRAM 경로 suspend→resume 성공/실패 케이스.
6) 문서화: PRD 교차 참조, config.feature 플래그 설명.

## 영향도(1–3줄)
- proposer: `suspended_ops()`에 메타가 채워져 RECOVERY_RD.SEQ 타겟 상속 정확도 상승.
- RM/Scheduler 공용 스냅샷/CSV: 타임라인이 실제 의도(중단 시 절단, 재개 시 이어붙임)와 일치.
- 성능 영향 미미: 절단은 bisect 기반 선형 인접 수정, 체이닝도 소량 스텁 예약만 추가.

## 완료 기준(Definition of Done)
- Suspend/Resume 시나리오에서 타임라인과 메타가 PRD 기대와 일치하며, 가드 on/off 모두 결정적 테스트 통과.
- 회귀 테스트(프로그램/이스 부분) 통과, 기존 이벤트/CSV 포맷 변화 없음.
- 기능 가드 기본 off로 머지 가능, on 시에도 성능/정확성 이슈 없음.

## 참고
- 연구: `research/2025-09-14_15-11-24_suspend_resume_flow.md`
- 관련 선행 계획: `plan/2025-09-08_suspend_resume_timeline_and_reschedule_impl_plan.md`

