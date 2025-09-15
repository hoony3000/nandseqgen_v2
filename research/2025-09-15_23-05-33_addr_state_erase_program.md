---
date: 2025-09-15T23:05:33+0900
researcher: codex
git_commit: 93072e613c9653a632a6906acba3a3ddfe18115b
branch: main
repository: nandseqgen_v2
topic: "ERASE/PROGRAM 완료 시 addr_state 반영 흐름과 분류 기준"
tags: [research, codebase, scheduler, addrman, config, op_base, op_name, ERASE, PROGRAM]
status: complete
last_updated: 2025-09-15
last_updated_by: codex
---

# 연구: ERASE/PROGRAM 완료 시 addr_state 반영 흐름과 분류 기준

**Date**: 2025-09-15T23:05:33+0900
**Researcher**: codex
**Git Commit**: 93072e613c9653a632a6906acba3a3ddfe18115b
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
ERASE, PROGRAM 이 끝날 시에 addr_state 를 반영하는 workflow 를 조사하고, 어떤 op_base 또는 op_name 으로 ERASE, PROGRAM 으로 분류하는지?

## 요약
- OP_END 처리 시 `scheduler._am_apply_on_end`가 ERASE/PROGRAM 계열에 한해 AddressManager에 상태를 커밋한다.
- ERASE는 항상 커밋하며, PROGRAM은 화이트리스트에 포함된 base만 커밋한다(예: `PROGRAM_SLC`, `ONESHOT_CACHE_PROGRAM` 등).
- 커밋 시 celltype은 `config.yaml`의 해당 `op_name`의 `celltype`을 참조한다.
- ERASE/PROGRAM 분류는 base 문자열로 판정한다: `ERASE`는 일치, `PROGRAM`은 포함(단, `*_SUSPEND`/`*_RESUME` 제외).
- `op_name` → `op_base` 매핑은 `config.yaml`의 `op_names` 섹션으로 정의된다. 예: `Block_Erase_SLC → ERASE`, `Cache_Program_SLC → CACHE_PROGRAM_SLC`.

## 상세 발견

### OP_END → AddressManager 반영 흐름
- `scheduler._handle_op_end`에서 ERASE/PROGRAM 완료 시 AM 반영을 호출한다.
  - scheduler.py:214
  - scheduler.py:216
- 구체 구현은 `_am_apply_on_end`:
  - ERASE: 대상 블록을 `ERASE(-1)`로 표시하고 erase 모드(celltype) 기록.
  - PROGRAM: 허용된 base만 페이지 수 증가 및 program 모드 설정(처음 ERASE에서 시작 시).
  - scheduler.py:221
  - scheduler.py:243
  - scheduler.py:286

### AddressManager 상태 변화
- ERASE 반영: `apply_erase(addrs, mode)` — 상태를 `ERASE(-1)`로, erase 모드 설정, program 모드 리셋.
  - addrman.py:472
  - addrman.py:489
- PROGRAM 반영: `apply_pgm(addrs, mode)` — 블록별 등장 횟수만큼 페이지 수 증가, 처음 ERASE였다면 program 모드 설정.
  - addrman.py:607
  - addrman.py:625
  - addrman.py:629

### PROGRAM 커밋 화이트리스트
- `_am_apply_on_end` 내 허용 base 집합:
  - `{ PROGRAM_SLC, COPYBACK_PROGRAM_SLC, ONESHOT_PROGRAM_MSB_23H, ONESHOT_PROGRAM_EXEC_MSB, ONESHOT_CACHE_PROGRAM, ONESHOT_COPYBACK_PROGRAM_EXEC_MSB }`
  - scheduler.py:267
- 추가 허용은 `features.extra_allowed_program_bases`로 확장 가능.
  - scheduler.py:275
- 주의: `CACHE_PROGRAM_SLC`는 기본 화이트리스트에 없으므로 OP_END에서 addr_state 커밋 안 함. 캐시형 프로그램은 후속 `ONESHOT_CACHE_PROGRAM` 등으로 커밋.

### ERASE/PROGRAM 분류 기준
- 스케줄러의 분류 로직:
  - ERASE: `base == "ERASE"`.
  - PROGRAM 계열: `"PROGRAM" in base` 이고 `SUSPEND`/`RESUME` 미포함.
  - scheduler.py:243
- 부트스트랩 집계에서도 유사한 분류 사용(진행량 추적용):
  - ERASE: `base.startswith("ERASE") or base == "ERASE"`.
  - PROGRAM: `PROGRAM_*`, `CACHE_PROGRAM_*`, `ONESHOT_PROGRAM_*`, 또는 `PROGRAM_SLC`.
  - bootstrap.py:50
  - bootstrap.py:52

### op_name → op_base 매핑 예시(config.yaml)
- ERASE 계열 예:
  - `Block_Erase_SLC: base: ERASE` — config.yaml:2646
  - `Block_Erase_TLC: base: ERASE` — config.yaml:2639
- PROGRAM 계열 예:
  - `Cache_Program_SLC: base: CACHE_PROGRAM_SLC` — config.yaml:2782
  - `Cache_Program_A0SLC: base: CACHE_PROGRAM_SLC` — config.yaml:2702
  - `Cache_Program_MSB: base: ONESHOT_CACHE_PROGRAM` — config.yaml:2814

## 코드 참조
- `scheduler.py:214` - OP_END에서 AM 동기화 진입
- `scheduler.py:221` - `_am_apply_on_end` 정의 및 동작 설명
- `scheduler.py:243` - ERASE/PROGRAM 분류 조건
- `scheduler.py:267` - PROGRAM 커밋 허용 base 집합
- `addrman.py:472` - `apply_erase` 시그니처
- `addrman.py:489` - ERASE 상태/모드 반영
- `addrman.py:607` - `apply_pgm` 시그니처
- `addrman.py:625` - PROGRAM 페이지 증가
- `addrman.py:629` - 처음 ERASE에서 시작 시 program 모드 설정
- `bootstrap.py:50` - ERASE 분류 로직(집계)
- `bootstrap.py:52` - PROGRAM 분류 로직(집계)
- `config.yaml:2639` - Block_Erase_TLC → ERASE
- `config.yaml:2646` - Block_Erase_SLC → ERASE
- `config.yaml:2782` - Cache_Program_A0SLC → CACHE_PROGRAM_SLC
- `config.yaml:2814` - Cache_Program_MSB → ONESHOT_CACHE_PROGRAM

## 아키텍처 인사이트
- OP_END에서의 상태 커밋을 Scheduler에 일원화함으로써, 제약/예약 로직과 상태 반영이 느슨하게 결합됨.
- PROGRAM의 커밋 타이밍은 base 화이트리스트로 엄격 관리하여, 캐시 기반/시퀀스형 PROGRAM은 최종 스텝에서만 반영되도록 함.
- 분류는 문자열 패턴 매칭에 의존하므로, 새로운 PROGRAM 파생 base 추가 시 화이트리스트/분류 규칙 업데이트가 필요.

## 관련 연구
- `research/2025-09-14_22-21-11_addr_state_suspend_resume.md` - SUSPEND/RESUME 시 addr_state 롤백/재적용 흐름

## 미해결 질문
- `CACHE_PROGRAM_SLC`류의 커밋 시점은 어떤 후속 op들에서 최종 커밋되는지(현 화이트리스트 기준 `ONESHOT_CACHE_PROGRAM` 포함). 시퀀스 전개 규칙과 함께 추가 확인 필요.
---
