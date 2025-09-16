---
date: 2025-09-16T23:13:07+0900
researcher: Codex
git_commit: ca07cf6d0ee8e19a59e2111a9b06e27bc1d54462
branch: main
repository: nandseqgen_v2
topic: "PROGRAM suspend/resume sampling behavior"
tags: [research, codebase, proposer, address-manager]
status: complete
last_updated: 2025-09-16
last_updated_by: Codex
---

# 연구: PROGRAM suspend/resume sampling behavior

**Date**: 2025-09-16T23:13:07+0900
**Researcher**: Codex
**Git Commit**: ca07cf6d0ee8e19a59e2111a9b06e27bc1d54462
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
PROGRAM 이 스케쥴 된 후 CORE_BUSY state 에 SUSPEND->RESUME 을 반복적으로 수행하는 조건에서 PROGRAM 반복적으로 스케쥴 되면 AddressManager.sample_pgm 도 반복적으로 호출이 되나?

## 요약
- proposer 는 base 문자열에 "PROGRAM" 이 포함되고 "SUSPEND"/"RESUME" 가 없는 경우에만 AddressManager.sample_pgm 을 호출한다.
- PROGRAM_RESUME 처럼 "RESUME" 이 포함된 base 는 hook 이 주는 target 을 재사용하며 sample_pgm 을 호출하지 않는다.
- 따라서 새 PROGRAM 동작이 다시 선택될 때마다 sample_pgm 이 호출되지만, suspend 이후 resume 을 반복하는 동안 이미 진행 중인 PROGRAM 을 재개할 때는 재호출되지 않는다.

## 상세 발견

### proposer target selection
- `_is_addr_sampling_base` 는 base 에 "PROGRAM" 을 포함하되 "SUSPEND"/"RESUME" 을 제외한 경우에만 True 를 반환한다 (`proposer.py:630-656`).
- propose 루프에서 이 함수가 True 인 후보는 `_sample_targets_for_op` 를 통해 AddressManager.sample_pgm 을 호출한다 (`proposer.py:1488-1499`).
- base 에 RESUME/SUSPEND 이 포함된 후보는 hook target 또는 phase fallback 을 사용하며 AddressManager 를 호출하지 않는다 (`proposer.py:1506-1537`).

### AddressManager sampling semantics
- `AddressManager.sample_pgm` 은 상태를 변경하지 않고 다음 program page 후보를 반환하는 순수 샘플링 함수다 (`addrman.py:493-520`).
- sequential 매개변수와 oversample 플래그를 제외하고, 호출될 때마다 현재 addrstate 기준으로 새로운 target 을 계산한다.

## 코드 참조
- `proposer.py:630` - `_is_addr_sampling_base` 가 PROGRAM_* 중 RESUME/SUSPEND 를 제외하도록 정의.
- `proposer.py:1488` - PROGRAM 후보에 대해 `_sample_targets_for_op` 로진입하며 AddressManager.sample_pgm 호출.
- `proposer.py:1506` - PROGRAM_RESUME 등은 hook target 재사용 경로로 이동.
- `addrman.py:493` - sample_pgm 이 상태 비파괴적이며 호출 때마다 새 주소를 선정.

## 아키텍처 인사이트
- suspend/resume 흐름은 proposer 가 hook 기반 target 재사용을 강제하여 동일 주소로 resume 되도록 보장한다.
- AddressManager 호출은 "fresh" PROGRAM/ERASE/READ 범주에 한정되어, 재개 작업 중 중복 호출을 방지한다.

## 역사적 맥락
- 이번 조사에서는 thoughts/ 디렉터리 자료를 추가로 참조하지 않았다.

## 관련 연구
- (none)

## 미해결 질문
- 실제 run 로그에서 suspend 이후 resume hook 이 항상 target 을 포함하는지 확인 필요.
- resume 이후 동일 block/page 가 유지되는지 회귀 테스트로 검증할 수 있는지 평가 필요.
