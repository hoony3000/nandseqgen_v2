---
date: 2025-09-14T20:59:24.360853+09:00
researcher: codex
git_commit: f375d829ceebe7e2beaa6367ac3d4a27ee3e2cd9
branch: main
repository: nandseqgen_v2
topic: "PROGRAM_RESUME에서 중단된 operation 재스케줄 불가 원인 분석"
tags: [research, codebase, scheduler, resource-manager, suspend-resume, reschedule]
status: complete
last_updated: 2025-09-14
last_updated_by: codex
last_updated_note: "PROGRAM_RESUME pre-skip 및 latch 실패 재현 실험 결과 추가"
---

# 연구: PROGRAM_RESUME에서 중단된 operation 재스케줄 불가 원인 분석

**Date**: 2025-09-14T20:59:24.360853+09:00
**Researcher**: codex
**Git Commit**: f375d829ceebe7e2beaa6367ac3d4a27ee3e2cd9
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
ERASE_RESUME의 경우는 중단되었던 operation이 재스케줄되는데, PROGRAM_RESUME에서는 중단된 operation이 재스케줄되지 않는 현상의 근본 원인은 무엇인가?

## 요약
- 원인: 재스케줄 체인 로직이 "가장 최근 suspended op"만을 참조하고, 해당 op이 RESUME된 패밀리(ERASE/PROGRAM)에 속하는지 필터링하지 않아 PROGRAM_RESUME 시 다른 패밀리(예: ERASE)가 마지막으로 suspend된 경우 체인이 건너뛰어진다. 또한 마지막 suspended op의 `remaining_us`가 0.0일 경우에도 체인이 스킵된다.
- 결과: ERASE_RESUME는 보통 마지막 suspended op이 ERASE여서 정상 체인이 일어나지만, PROGRAM_RESUME는 마지막 suspended op이 ERASE이거나 rem이 0.0인 경우가 종종 발생하여 재스케줄이 누락된다.
- 구조적 결함: ResourceManager가 suspend 메타를 패밀리별로 분리 관리하지 않고, Scheduler의 체인 로직도 패밀리 일치 필터 없이 "마지막 항목"만 본다.

## 상세 발견

### 체인 로직(Scheduler)
- *_RESUME 직후 남은 CORE_BUSY를 체인하는 로직이 존재하며, feature flag로 활성화됨.
  - `scheduler.py:463` — `_chain_enabled` 체크 후 ERASE_RESUME/PROGRAM_RESUME에만 체인 시도
  - `scheduler.py:466` — `suspended_ops(die)`에서 마지막 메타(`sus[-1]`)만 취함
  - `scheduler.py:468` — `remaining_us`가 0.0이면 체인 스킵
  - `scheduler.py:472` — 현재 RESUME 패밀리와 `meta.base`의 패밀리 일치 시에만 체인 진행

설명: 마지막 suspended 메타가 다른 패밀리(예: ERASE)거나 `remaining_us == 0.0` 이면 체인이 발생하지 않는다. 이때 로그는 `[chain] skip(pre): ...`로 출력될 수 있다.

### Suspend/Resume 메타 관리(ResourceManager)
- SUSPEND 커밋 시, "해당 die의 최신 ongoing op"를 suspended에 옮기지만 패밀리 일치를 보장하지 않는다.
  - `resourcemgr.py:629` — ERASE_SUSPEND/PROGRAM_SUSPEND 공통 처리 분기
  - `resourcemgr.py:642` — `move_to_suspended(die, op_id=None, now_us=start)` 호출(패밀리 미검증)
  - `resourcemgr.py:990` — `move_to_suspended`: die의 ongoing 리스트에서 마지막 항목을 pop하여 suspended로 이동, `remaining_us` 계산

- RESUME 커밋 시에는 축 suspend 축을 닫기만 하고, 메타 이동은 하지 않는다(체인은 Scheduler가 수행).
  - `resourcemgr.py:669` — ERASE_RESUME 축 종료/정리
  - `resourcemgr.py:674` — PROGRAM_RESUME 축 종료/정리

설명: suspend 시점에 어떤 op이 ongoing 리스트의 마지막이었는지에 따라 suspended에 쌓이는 "마지막" 메타가 ERASE 또는 PROGRAM 중 무엇이 될지 달라진다. 이후 Scheduler는 `sus[-1]`만 보고 체인을 시도하므로, PROGRAM_RESUME 시점에 마지막 메타가 ERASE이면 체인이 건너뛰어진다.

### Ongoing 등록 경로 확인
- ERASE/PROGRAM 류 커밋 시에만 ongoing 메타가 등록된다(READ 등은 제외).
  - `scheduler.py:519` — ERASE 또는 PROGRAM(단, *_SUSPEND/*_RESUME/*CACHE 제외)의 경우에만 `register_ongoing`

설명: ongoing은 E/P 두 축에서만 쌓이며, 둘 다 동일 리스트에 뒤섞여 저장된다. 최신 항목 기준 pop이므로 패밀리 미일치가 쉽게 발생할 수 있다.

## 코드 참조
- `scheduler.py:463` — ERASE_RESUME/PROGRAM_RESUME에서 체인 로직 시작
- `scheduler.py:466` — `suspended_ops(die)`의 마지막 항목만 선택
- `scheduler.py:472` — RESUME 패밀리와 `meta.base` 패밀리 일치 조건
- `resourcemgr.py:642` — SUSPEND 시 최신 ongoing을 suspended로 이동(패밀리 검증 없음)
- `resourcemgr.py:990` — `move_to_suspended` 구현(마지막 ongoing만 이동, rem 계산)
- `resourcemgr.py:669` — ERASE_RESUME 축 종료 처리
- `resourcemgr.py:674` — PROGRAM_RESUME 축 종료 처리
- `scheduler.py:519` — ongoing 메타 등록 대상(ERASE/PROGRAM 류 한정)

## 아키텍처 인사이트
- 단일 `suspended_ops` 스택(die별) 구조와 "마지막 항목"만을 사용하는 체인 전략은 두 패밀리(ERASE/PROGRAM)가 교차 suspend될 때 오동작 가능성이 높다.
- 체인 로직이 패밀리 일치를 사전 필터링하지 않으면, RESUME 패밀리와 다른 패밀리 메타가 마지막으로 쌓여 있는 경우 재스케줄이 누락된다.
- `remaining_us == 0.0`인 suspended 메타가 마지막이면, 패밀리 일치여도 체인이 스킵된다(설계상 의도이긴 하나, 관측된 증상에 기여 가능).

## 관련 연구
- 없음

## 미해결 질문
- 두 축이 교차 suspend된 시나리오에서 `suspended_ops`를 패밀리별로 분리할지, 혹은 Scheduler에서 `suspended_ops`를 역순 스캔하여 패밀리 일치 항목을 선택할지에 대한 정책 결정. -> (검토완료) 패밀리별로 분리

---

추가 제안(수정 방향):
- 간단: Scheduler 체인에서 `sus`를 역순 스캔하여 `meta.base`가 RESUME 패밀리와 일치하고 `remaining_us > 0`인 첫 항목을 사용.
- 구조적: ResourceManager에 `suspended_ops_program`, `suspended_ops_erase`로 축을 분리해 저장/조회하여 모호성 제거. -> (구현)
- 보수: `move_to_suspended` 호출 시 현재 SUSPEND 패밀리에 속하는 ongoing만 이동(일치하지 않으면 skip).
