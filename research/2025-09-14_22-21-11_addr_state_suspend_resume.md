---
date: 2025-09-14T22:21:11+09:00
researcher: codex
git_commit: f375d829ceebe7e2beaa6367ac3d4a27ee3e2cd9
branch: main
repository: nandseqgen_v2
topic: "SUSPEND 시 진행 중 addr_state 롤백, RESUME 시 재적용 흐름"
tags: [research, codebase, scheduler, resourcemgr, addrman, suspend, resume, addr_state]
status: complete
last_updated: 2025-09-14
last_updated_by: codex
---

# 연구: SUSPEND 시 진행 중 addr_state 롤백, RESUME 시 재적용 흐름

**Date**: 2025-09-14T22:21:11+09:00
**Researcher**: codex
**Git Commit**: f375d829ceebe7e2beaa6367ac3d4a27ee3e2cd9
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
SUSPEND 시 진행 중이던 addr_state 값이 롤백되었다가, RESUME 이후 다시 적용되는 정확한 워크플로는 무엇인가?

## 요약
- AddressManager(addr_state)는 OP_END 시점에만 동기화된다. PROGRAM/ERASE 실행 중에는 아직 미반영 상태다.
- SUSPEND 커밋 시 ResourceManager가 CORE_BUSY 구간을 절단하고 해당 op 메타를 suspended_ops로 이동한다. AddressManager에는 변화가 없다(즉, 사실상 롤백 상태 유지).
- RESUME 커밋 직후 Scheduler가 남은 CORE_BUSY를 같은 base로 “stub”으로 연속 예약(체인)한다. 이 stub의 OP_END에서 AddressManager.apply_*가 호출되어 addr_state가 최종 적용된다.
- 결과적으로 “SUSPEND 시 롤백, RESUME 시 재적용”처럼 관찰된다. 내부적으로는 “OP_END에서만 반영 + SUSPEND 절단 + RESUME 후 잔여 구간 완료” 조합으로 구현됨.

## 상세 발견

### AddressManager 적용 타이밍(OP_END)
- Scheduler는 OP_END 이벤트에서만 AddressManager를 갱신한다. ERASE는 `apply_erase`, PROGRAM류는 허용된 base에 한해 `apply_pgm`을 호출한다.
  - scheduler.py:202 — `_handle_op_end`에서 AM 동기화 훅 호출
  - scheduler.py:221 — `_am_apply_on_end(...)` 정의 시작
  - scheduler.py:244 — PROGRAM-like 판별(`SUSPEND`/`RESUME` 제외)
  - scheduler.py:285 — PROGRAM 커밋 허용 목록 계산 후 `apply_pgm` 호출 분기
  - addrman.py:471 — `apply_erase` 정의(ERASE 시 -1로 설정 및 모드 갱신)
  - addrman.py:606 — `apply_pgm` 정의(페이지 수 증가 및 모드 갱신)

설명: 실행 중(PROGRAM/ERASE의 CORE_BUSY 도중)에는 AddressManager 상태가 바뀌지 않는다. 최종 OP_END 때만 동기화되므로, 도중 SUSPEND가 발생하면 “이미 반영된 것을 되돌리는” 별도 롤백 동작이 필요 없고, 애초에 반영이 안 되었기 때문에 이전 상태로 남아있게 된다.

### SUSPEND 시 커밋 동작(타임라인 절단 + 메타 이동)
- ResourceManager.commit에서 ERASE_SUSPEND/PROGRAM_SUSPEND를 처리할 때:
  - 축 상태(ERASE/PROGRAM_SUSPENDED) 시작을 기록
  - 최신 ongoing op 메타를 축 일치 조건으로 axis별 suspended 스택으로 이동하고 remaining_us 계산
  - 상태 타임라인에서 해당 패밀리의 CORE_BUSY 꼬리를 suspend 시각 이후로 절단
  - resourcemgr.py:632 — SUSPEND 처리 분기 주석(분할 축 + 절단 + 메타 이동)
  - resourcemgr.py:667 — 절단 대상 프레디킷(CORE_BUSY && fam && not SUSPEND/RESUME)
  - resourcemgr.py:1091 — move_to_suspended_axis의 패밀리 일치 판정

설명: 이 단계에서 AddressManager는 건드리지 않는다. 예약 당시 트랜잭션 오버레이에만 addr_state 변화가 반영되며(같은 txn 내 EPR 검사용), 커밋 후에는 해당 오버레이가 소멸한다. 즉, 전역 addr_state는 그대로이므로 관찰상 “롤백된 것처럼” 보인다.

### RESUME 직후 잔여 CORE_BUSY 체인(재적용 트리거)
- 기능 플래그 `features.suspend_resume_chain_enabled: true`가 켜져 있으면, Scheduler는 *_RESUME 커밋 직후 axis별 `suspended_ops_*`에서 마지막(또는 일치 항목)의 remaining_us를 읽어 동일 base의 CORE_BUSY만 가진 "stub op"를 RESUME 종료 직후에 즉시 예약/커밋한다.
  - config.yaml:39 — 체인 기능 플래그
  - scheduler.py:463 — *_RESUME에서 체인 시도(플래그 게이트)
  - scheduler.py:519 — 커밋 성공 시 OP_START/OP_END 이벤트 방출
  - scheduler.py:525 — ERASE/PROGRAM 예약 성공 시 ongoing 등록(관찰용 메타)
  - scheduler.py:590 — 체인 커밋 후 suspended→ongoing 복귀 반영

이후 stub의 OP_END에서 위의 `_handle_op_end` → `_am_apply_on_end` 경로가 실행되어, ERASE는 `apply_erase`, PROGRAM은 허용된 base에서 `apply_pgm`이 호출된다. 이 시점이 addr_state의 “재적용”이다.

### 예약 중 오버레이(addr_state pending)과 EPR
- ResourceManager는 같은 트랜잭션 내에서 앞선 예약의 효과를 `txn.addr_overlay`에 반영해 EPR(주소 의존 규칙) 검사를 일관되게 한다.
  - resourcemgr.py:1394 — ERASE 예약 시 overlay.addr_state = -1
  - resourcemgr.py:1407 — PROGRAM 예약 시 overlay.addr_state = max(prev, page)
  - addrman.py:1044 — overlay가 제공되면 해당 값을 effective addr_state로 사용
- 커밋 후 overlay는 사라지므로, SUSPEND 이후 전역 addr_state는 변경되지 않은 채 유지된다. RESUME 체인 스텁도 일반 예약과 동일하게 EPR을 통과해야 하며, 활성화된 경우 `epr_dep`로 거절될 수 있다.
  - resourcemgr.py:1460 — EPR 통합 게이트 및 호출

## 코드 참조
- `scheduler.py:202` — OP_END 처리 훅(AM 동기화 진입점)
- `scheduler.py:221` — `_am_apply_on_end` 정의 시작
- `scheduler.py:244` — PROGRAM-like 판별(RESUME/SUSPEND 제외)
- `scheduler.py:285` — PROGRAM 커밋 허용 목록 적용 후 `apply_pgm`
- `scheduler.py:525` — ERASE/PROGRAM 예약 성공 시 `register_ongoing`
- `scheduler.py:590` — 체인 커밋 후 `resume_from_suspended_axis`
- `resourcemgr.py:632` — SUSPEND에서 축 상태 시작 + 메타 이동 + 타임라인 절단
- `resourcemgr.py:667` — 절단 대상 프레디킷(CORE_BUSY && fam)
- `resourcemgr.py:1091` — move_to_suspended_axis의 일치 조건
- `resourcemgr.py:1394` — overlay: ERASE → addr_state=-1
- `resourcemgr.py:1407` — overlay: PROGRAM → addr_state=max(prev, page)
- `addrman.py:471` — `apply_erase` (OP_END 반영)
- `addrman.py:606` — `apply_pgm` (OP_END 반영)
- `config.yaml:39` — 체인 기능 플래그

## 아키텍처 인사이트
- addr_state는 “예약 시 오버레이, OP_END 시 최종 커밋”이라는 두 단계 모델을 사용한다. 이로 인해 SUSPEND에서는 전역 상태를 만지지 않고 타임라인만 절단해도 일관성이 보장된다.
- RESUME 직후 잔여 CORE_BUSY를 별도 스텁으로 일반 경로 예약/커밋함으로써, 이벤트/후크/CSV/지표가 모두 동일한 파이프라인을 통과한다(특수 처리 최소화).
- 주소 의존(EPR)이 활성화된 환경에서는 체인 스텁도 동일 검사를 받으므로, 동일 페이지 중복 프로그램 등 규칙에 막혀 재적용이 지연될 수 있다.

## 역사적 맥락
- `research/2025-09-14_15-11-24_suspend_resume_flow.md` — 초기 SUSPEND/RESUME 플로우 정리 및 구현 갭 분석
- `research/2025-09-14_20-59-24_program_resume_reschedule.md` — PROGRAM_RESUME 체인 누락 원인 분석(패밀리 분리 전)
- `research/2025-09-14_22-10-15_program_resume_epr_dep.md` — 체인에서 EPR로 인해 예약 실패하는 사례 분석

## 관련 연구
- `research/2025-09-14_18-32-37_suspend_resume_second_run_no_ops.md`
- `research/2025-09-14_13-48-44_rm_validity_pending_bus_plane_windows.md`

## 미해결 질문
- 체인 스텁을 “연속 수행”으로 간주해 특정 EPR 규칙에서 부분 면제할지 정책 결정 필요.
- 다중 plane 동일 page 체인을 plane-aware하게 허용할지, 규칙을 보수적으로 유지할지.
