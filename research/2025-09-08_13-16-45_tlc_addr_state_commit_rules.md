---
date: 2025-09-08T04:16:45.859631+00:00
researcher: codex
git_commit: cba55e0dc0e35d8697e8aeac3f7004443a7f3a80
branch: main
repository: nandseqgen_v2
topic: "TLC 프로그램의 addr_state 커밋 시점 확인 및 개선안"
tags: [research, codebase, scheduler, resourcemgr, addrman, config]
status: complete
last_updated: 2025-09-08
last_updated_by: codex
---

# 연구: TLC 프로그램의 addr_state 커밋 시점 확인 및 개선안

**Date**: 2025-09-08T04:16:45.859631+00:00
**Researcher**: codex
**Git Commit**: cba55e0dc0e35d8697e8aeac3f7004443a7f3a80
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
TLC program 의 경우 addr_state 를 commit 하는 시점은 ONESHOT_PROGRAM_MSB_23h/ONESHOT_PROGRAM_EXEC_MSB/ONESHOT_CACHE_PROGRAM/ONESHOT_COPYBACK_PROGRAM_EXEC_MSB 일 경우에만 한번 수행되어야 하는데, 현재 코드 구현이 그렇게 돼있는지, 그렇지 않다면 어떻게 개선해야 하는지?

## 요약
- 현재 구현은 OP_END 시점에 base 이름에 "PROGRAM"이 포함되면 무조건 AddressManager.apply_pgm을 호출한다. 그 결과 ONESHOT_PROGRAM_LSB/CSB 등 중간 단계에서도 addr_state가 증가한다. 이는 요구사항(최종 단계에서 1회만 커밋)에 어긋난다.
- ResourceManager의 예약 시점 오버레이(txn.addr_overlay)는 ERASE와 SLC 계열(PROGRAM_SLC, COPYBACK_PROGRAM_SLC)만 반영하고 있어 TLC one‑shot 중간 단계에 대한 조기 반영은 하지 않는다. 문제는 Scheduler의 OP_END 동기화에서 발생한다.
- 개선: Scheduler._am_apply_on_end에서 PROGRAM 커밋을 허용하는 base를 화이트리스트로 제한한다.
  - 허용: PROGRAM_SLC, COPYBACK_PROGRAM_SLC, ONESHOT_PROGRAM_MSB_23h, ONESHOT_PROGRAM_EXEC_MSB, ONESHOT_CACHE_PROGRAM, ONESHOT_COPYBACK_PROGRAM_EXEC_MSB
  - 차단: ONESHOT_PROGRAM_LSB, ONESHOT_PROGRAM_CSB, ONESHOT_PROGRAM_MSB(plain), ONESHOT_COPYBACK_PROGRAM_LSB/CSB/MSB 등

## 상세 발견

### Scheduler(OP_END 시점 AddressManager 동기화)
- 현행 로직: base 문자열에 "PROGRAM"이 포함되면 apply_pgm 호출
  - 파일: `scheduler.py:238`–`scheduler.py:360`
  - 근거 코드: `elif is_program and hasattr(am, "apply_pgm"): am.apply_pgm(addrs, mode=mode)` → `is_program = ("PROGRAM" in b) and ...`
- OP_END 핸들러는 항상 `_am_apply_on_end(...)`를 호출
  - 파일: `scheduler.py:199`–`scheduler.py:216`, `scheduler.py:218`
- 영향: ONESHOT_PROGRAM_LSB/CSB 같은 중간 단계에서도 addr_state가 증가하여 "한 번만 커밋" 규칙 위반.

### ResourceManager(예약 시점 오버레이/EPR)
- 예약 중 addr_state 오버레이 반영은 ERASE 및 SLC 프로그램 계열만 해당
  - 파일: `resourcemgr.py:1087`–`resourcemgr.py:1105`
  - TLC one‑shot 중간 단계에 대한 오버레이는 없음(조기 반영 방지 측면에서는 안전).
- PRD 준수: AddressManager 반영 시점은 OP_END
  - 파일: `docs/PRD_v2.md:7`

### 구성(config) 확인
- 문제에서 언급한 대상 베이스는 모두 CFG 상 존재
  - `ONESHOT_PROGRAM_MSB_23h`, `ONESHOT_PROGRAM_EXEC_MSB`, `ONESHOT_CACHE_PROGRAM`, `ONESHOT_COPYBACK_PROGRAM_EXEC_MSB`
  - 파일 예시: `config.yaml:3337`(One_Shot_PGM_MSB_23h), `config.yaml:3158`(One_Shot_PGM_Execution_MSB), `config.yaml:2529`(Cache_Program_MSB), `config.yaml:3098`(One_Shot_Copyback_Program_Execution_MSB)

## 코드 참조
- `scheduler.py:199` — `_handle_op_end`가 모든 OP_END에서 `_am_apply_on_end` 호출
- `scheduler.py:238` — PROGRAM 계열 여부를 `"PROGRAM" in b`로 판정(광범위 매칭)
- `scheduler.py:356` — PROGRAM 매칭 시 `am.apply_pgm(...)` 호출
- `resourcemgr.py:1087` — 예약 시점 addr_state 오버레이 업데이트(ERASE/SLC 프로그램만 적용)
- `docs/PRD_v2.md:7` — AddressManager 반영은 OP_END 시점 원칙

## 아키텍처 인사이트
- addr_state 커밋의 단일화는 (1) 제안/예약 단계(EPR 검증)와 (2) 실제 상태 반영(OP_END)의 타이밍 분리를 전제로 한다.
  - 예약 중에는 오버레이만 쓰고, 실 반영은 OP_END에서 단 한 번만 수행되어야 한다.
- 현재 오버레이는 TLC one‑shot 중간 단계에 값을 주지 않으므로, Scheduler의 OP_END 반영만 제한하면 요구사항을 만족한다.

## 개선안(구체)
- 변경 지점: `scheduler.py::_am_apply_on_end`
- 규칙: PROGRAM 계열 중 다음 베이스에만 apply_pgm 수행
  - 허용 집합(대문자 기준):
    - PROGRAM_SLC, COPYBACK_PROGRAM_SLC
    - ONESHOT_PROGRAM_MSB_23H, ONESHOT_PROGRAM_EXEC_MSB
    - ONESHOT_CACHE_PROGRAM
    - ONESHOT_COPYBACK_PROGRAM_EXEC_MSB
- 의사 코드:
  ```python
  ALLOWED_PROGRAM_COMMIT = {
      "PROGRAM_SLC", "COPYBACK_PROGRAM_SLC",
      "ONESHOT_PROGRAM_MSB_23H", "ONESHOT_PROGRAM_EXEC_MSB",
      "ONESHOT_CACHE_PROGRAM",
      "ONESHOT_COPYBACK_PROGRAM_EXEC_MSB",
  }
  if is_erase:
      am.apply_erase(...)
  elif b in ALLOWED_PROGRAM_COMMIT:
      am.apply_pgm(...)
  ```
- 예상 효과: TLC one‑shot 체인에서 LSB/CSB 단계는 addr_state 비변경, 최종(23h/EXEC) 또는 oneshot cache program 종료에서만 1회 증가.

## 역사적 맥락(thoughts/ 기반)
- 해당 주제의 직접적인 과거 연구 문서는 없었으나, 상태 반영/타임라인/훅 설계 관련 연구는 다수 존재함.
  - `research/2025-09-07_21-21-37_affect_state_false_skip_op_state_timeline.md` — affect_state=false 동작의 타임라인 미등록 정책
  - `research/2025-09-07_23-41-59_op_chain_multi_consistency.md` — 체인/멀티 정합성 관련

## 관련 연구
- `plan/2025-09-07_affect_state_false_skip_op_state_timeline_impl_plan.md`
- `plan/2025-09-06_op_state_virtual_end_aggregation.md`

## 미해결 질문
- COPYBACK one‑shot에서 LSB/CSB 단계에 대해 추가적인 EPR 룰(예: same‑page 금지)을 강화할 필요가 있는가? 현재 오버레이는 반영하지 않으므로, 필요 시 Validator 규칙으로 보강 가능. -> (검토완료) ONESHOT_PROGRAM 과 동일한 latch state 변경 및 금지 조건을 동일하게 적용 필요. same-page 금지 조건은 필요 없음. 하지만, operation sequence 에 의한 예약은 inherit 조건을 따르면 됨.
- 다이/플레인 latch 해제 타이밍은 EXEC_MSB(또는 23h)에 맞춰져 있는데(`scheduler.py:205`), 캐시 프로그램 종료 시점의 latch 정책이 추가로 필요한지 확인 필요. -> (검토완료) ONESHOT_PROGRAM_MSB_23h/ONESHOT_PROGRAM_EXEC_MSB/ONESHOT_CACHE_PROGRAM/ONESHOT_COPYBACK_PROGRAM_EXEC_MSB 에 모두 필요.
