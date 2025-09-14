---
date: 2025-09-14T20:11:03+0900
researcher: codex
git_commit: 6bcd2d5b903803b2e9f24a7ae48e75ddb1dc775f
branch: main
repository: nandseqgen_v2
topic: "PROGRAM_SUSPEND→PROGRAM_RESUME 이후 중단 작업 미재개 원인 조사"
tags: [research, codebase, scheduler, resourcemgr, suspend-resume]
status: complete
last_updated: 2025-09-14
last_updated_by: codex
---

# 연구: PROGRAM_SUSPEND→PROGRAM_RESUME 이후 중단 작업 미재개 원인 조사

**Date**: 2025-09-14T20:11:03+0900
**Researcher**: codex
**Git Commit**: 6bcd2d5b903803b2e9f24a7ae48e75ddb1dc775f
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
PROGRAM_SUSPEND→PROGRAM_RESUME 시 중단됐던 operation이 재스케줄되지 않는 반면, ERASE_SUSPEND→ERASE_RESUME는 정상 재개되는 차이를 어떻게 확인하고, 원인은 무엇인가?

## 요약
- 원인: PROGRAM_SUSPEND이 PROGRAM.CORE_BUSY 구간 “중간”이 아니라 “끝(END) 경계”에서 발생해, ResourceManager가 기록하는 remaining_us가 0으로 계산됨. Scheduler의 체인 로직은 remaining_us>0일 때만 잔여 CORE_BUSY 스텁을 붙이므로 PROGRAM은 재스케줄이 생략됨. ERASE는 SUSPEND가 실제 CORE_BUSY 중간에 걸리며 remaining_us>0이 되어 정상 재개됨.
- 확인 방법: op_state_timeline과 operation_timeline에서 PROGRAM_SLC 종료와 PROGRAM_SUSPEND 시작이 같은 경계에 붙어 있는지 확인하고, Scheduler의 [chain] 로그와 rm.suspended_ops 메타의 remaining_us를 점검한다.

## 상세 발견

### 타임라인 증거
- out/op_state_timeline_250914_0000001.csv:15 — `PROGRAM_SLC.CORE_BUSY`가 `43009.9344→44500.0`에서 종료됨
- out/op_state_timeline_250914_0000001.csv:16 — 직후 `PROGRAM_SUSPEND.ISSUE`가 `44500.0→44500.185`에 시작됨
- out/op_state_timeline_250914_0000001.csv:17 — 이어 `PROGRAM_SUSPEND.CORE_BUSY` `44500.185→44600.185`
- out/operation_timeline_250914_0000001.csv:9 — `Program_Suspend_Reset(PROGRAM_SUSPEND)`의 phase_key_used가 `PROGRAM_SLC.END`로 기록됨. 즉, SUSPEND가 실제로 END 경계에서 발화됨.
- 결과적으로 SUSPEND 시각(now_us)과 직전 PROGRAM의 end_us가 같아져 remaining_us=end_us−now_us=0이 됨 → 체인 스텁 생략.

### Scheduler 체인 로직
- scheduler.py:513 — ERASE/PROGRAM 커밋 시 `register_ongoing(...)`로 메타 기록
- scheduler.py:542-590 — `*_RESUME` 커밋 직후 체인 작업 생성. `rm.suspended_ops(die)[-1].remaining_us>0`일 때만 CORE_BUSY 1-세그먼트 스텁을 예약/커밋하고, 이후 `rm.resume_from_suspended(...)` 호출로 메타 복귀
- 체인 디버그 로그: remaining_us<=0 인 경우 `[chain] skip(pre): remaining_us=...` 메시지 출력(표준출력)

### ResourceManager의 SUSPEND/RESUME 처리
- resourcemgr.py:520-579 — `commit(...)`에서 SUSPEND/RESUME 반영
  - SUSPEND 분기에서: 축 상태 시작 기록, `move_to_suspended(die, now_us=start)` 호출, 그리고 해당 family의 `CORE_BUSY` 세그먼트를 `truncate_after(start)`로 절단
  - RESUME 분기에서: 축 상태 종료만 수행; 체인 예약은 Scheduler가 담당
- resourcemgr.py:990 — `move_to_suspended(...)`: 현재 die의 “마지막 ongoing 메타”를 꺼내 remaining_us=end_us−now_us로 계산해 suspended 큐에 저장
  - 주: ongoing 목록은 커밋 시점에 push만 하고 OP_END에서 자동 제거하지 않음. 따라서 SUSPEND가 END 경계에서 발생하면 선택된 메타의 end_us와 now_us가 동일해 remaining_us=0이 된다.

### Phase Key 경계 처리
- resourcemgr.py:176-200 — `phase_key_at(...)`는 경계에서 이전 세그먼트의 `<BASE>.END`를 선호(prefer_end_on_boundary). 이로 인해 SUSPEND가 CORE_BUSY 말단 경계에서 선택될 가능성이 높음(위 operation_timeline의 `phase_key_used=PROGRAM_SLC.END` 일치).

## 코드 참조
- `scheduler.py:513` — ERASE/PROGRAM 예약 후 `register_ongoing(...)`
- `scheduler.py:542` — `*_RESUME` 직후 체인 스텁 예약 로직 시작
- `scheduler.py:549` — `_build_core_busy_stub(...)`로 잔여 CORE_BUSY 스텁 구성
- `scheduler.py:580` — `rm.resume_from_suspended(...)`로 메타 복귀
- `resourcemgr.py:520` — `commit(...)` 내 SUSPEND/RESUME 처리 루프
- `resourcemgr.py:640` — SUSPEND 시 `move_to_suspended(...)` 연동 및 타임라인 절단
- `resourcemgr.py:990` — `move_to_suspended(...)` remaining_us 계산
- `resourcemgr.py:176` — `phase_key_at(...)` 경계에서 END 선호
- `config.yaml:38` — `features.suspend_resume_chain_enabled: true` 확인

## 아키텍처 인사이트
- 설계상 체인 재개는 “남은 실행 시간이 있는 경우에만” 작동하도록 되어 있음. PROGRAM이 END 경계에서 SUSPEND되는 현재 제안/후킹 시점 때문에 잔여 시간이 0이 되어 체인이 생략된다.
- ERASE는 CORE_BUSY가 길어 pre-hook(종료 직전 훅) 타이밍에 SUSPEND가 실제 구간 중간에 들어가는 경향이 있어 잔여가 양수로 계산되고 체인이 작동한다.
- 잠재적 개선안(대안 비교):
  - 개선안 A: SUSPEND 제안을 pre-hook 시점(세그먼트 종료 직전)으로 강제하여 now_us<end_us 보장. 장점: 간단, 체인 즉시 활성. 단점: 훅 타이밍 민감, 다른 베이스에 영향 가능.
  - 개선안 B: `move_to_suspended(...)`가 “현재 시각에 실제로 커버 중인 ongoing 메타”를 선택하도록 변경(단순히 마지막 메타가 아닌). 장점: 경계/정렬 문제 완화. 단점: 추가 탐색/비용, 영향 범위 큼.
  - 개선안 C: remaining_us==0이어도 동일 family의 다음 스텁을 최소 단위로 한 번 붙이는 정책. 장점: 증상 완화. 단점: 실제로 남은 작업이 없는데 스텁 생성을 유도할 위험.
  - 현재 목적은 원인 파악과 확인 방법 제시이므로 코드 변경은 보류.

## 관련 연구
- `research/2025-09-14_15-11-24_suspend_resume_flow.md` — 체인 설계와 RM/Scheduler 역할 분담
- `research/2025-09-08_13-55-13_suspend_resume_timeline_and_reschedule.md` — 타임라인 절단과 재개 체인 컨셉

## 미해결 질문
- ERASE와 PROGRAM에서 SUSPEND 제안 타이밍(훅/우선순위)이 왜 다르게 관측되는지 추가 실험 필요. 동일 조건에서 PROGRAM도 pre-hook에서 확실히 제안되도록 하는 규칙이 있는가?
- `move_to_suspended(...)`가 경계 시점에 잘못된 메타(이미 끝난 op)를 선택할 가능성 정량화 및 보정 필요.

## 재현/확인 절차
- op_state_timeline/operation_timeline 점검
  - `out/op_state_timeline_250914_0000001.csv:15` — PROGRAM_SLC 종료
  - `out/op_state_timeline_250914_0000001.csv:16` — 바로 이어지는 PROGRAM_SUSPEND 시작
  - `out/operation_timeline_250914_0000001.csv:9` — Program_Suspend_Reset의 phase_key_used가 PROGRAM_SLC.END
- 체인 스킵 로그 확인
  - 실행 로그에서 `[chain] skip(pre): remaining_us=0.0 meta_ok=True` 류 출력 확인(Program_Resume 직후)
- RM 메타 스냅샷 확인(선택)
  - SUSPEND 직후 `rm.suspended_ops(die)`의 `remaining_us`가 0임을 디버그 출력으로 점검하면 현상과 부합.

