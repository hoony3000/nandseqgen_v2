---
date: 2025-09-23T11:46:57.061276+09:00
researcher: Codex
git_commit: 052170ac4547f550742ad40107123138e500f49a
branch: main
repository: nandseqgen_v2
topic: "addr_overlay 가 suspend 동작 중에 stale 해져도 안전한지"
tags: [research, codebase, ResourceManager, addr_overlay, suspend]
status: complete
last_updated: 2025-09-23
last_updated_by: Codex
---

# 연구: addr_overlay 가 suspend 동작 중에 stale 해져도 안전한지

**Date**: 2025-09-23T11:46:57.061276+09:00  
**Researcher**: Codex  
**Git Commit**: 052170ac4547f550742ad40107123138e500f49a  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
2025-09-23_11-25-24_suspend_resource_effects_refresh.md 의 미해결 질문 2번 — `addr_overlay` 가 suspend 동작으로 인해 stale 상태가 되어도 안전한지, 특히 multi-die 혼합 시나리오에서 어떤 영향이 있는지 확인한다.

## 요약
- `ResourceManager.reserve` 는 instant 경로에서도 `_update_overlay_for_reserved` 를 호출하지만, suspend 베이스는 조건 분기에서 제외되어 `txn.addr_overlay` 가 빈 상태로 남는다(`resourcemgr.py:499-528`, `resourcemgr.py:1458-1475`).
- 각 suspend/ resume 요청은 새로운 `_Txn` 으로 처리되므로 커밋 이후 overlay 정보가 전혀 보존되지 않는다; 이후 트랜잭션은 AddressManager 의 실제 `addr_state` 만 본다(`resourcemgr.py:94-115`, `resourcemgr.py:577-587`).
- 중단된 동안 충돌을 차단하는 책임은 plane/die 윈도우와 `state_forbid_suspend` 규칙에 있으며, config 가 해당 rule 을 유지하는 한 동일 die/plane 작업은 막힌다(`resourcemgr.py:552-558`, `resourcemgr.py:629-681`, `resourcemgr.py:1637-1655`).
- AddressManager/EPR 은 PROGRAM/READ 베이스에만 pending overlay 를 반영하므로 suspend 가 overlay 를 남기지 않아도 multi-die 의 다른 작업 평가에는 영향이 없다(`addrman.py:1044-1211`).

## 상세 발견

### ResourceManager overlay lifecycle
- Suspend/Resume 는 instant 예약 경로를 사용하지만 `_update_overlay_for_reserved` 가 ERASE/PROGRAM 계열만 처리하여 overlay 엔트리를 생성하지 않는다(`resourcemgr.py:499-528`, `resourcemgr.py:1458-1475`).
- `_Txn.addr_overlay` 는 트랜잭션 스코프이며 `commit` 이후 유지되지 않아, suspend 트랜잭션 완료 시 overlay 가 자동으로 GC 된다(`resourcemgr.py:94-115`, `resourcemgr.py:577-587`).
- Multi-die 대상이라도 키가 `(die, block)` 으로 구분되므로 overlay 는 die 간에 공유되지 않는다.

### Suspend commit gating
- 원래 PROGRAM 커밋이 생성한 plane/die 윈도우가 `_plane_resv` 및 `_excl_die` 에 남아 있어 suspend 이후에도 동일 die/plane 에 대한 새 예약이 시간 창을 벗어나기 전에는 거부된다(`resourcemgr.py:552-558`).
- `PROGRAM_SUSPEND` 커밋은 axis 상태를 열고 ongoing meta 를 suspend 스택으로 이동시켜 resume 시점까지 유지하며, CORE_BUSY 타임라인을 중단 시점까지 잘라낸다(`resourcemgr.py:629-669`).
- `state_forbid_suspend` 규칙은 config 의 `exclusions_by_suspend_state` 매핑을 이용해 suspend 상태인 die 에서 금지된 베이스를 거른다. 이 룰이 켜져 있으면 overlay 없이도 새 PROGRAM/ERASE 가 차단된다(`resourcemgr.py:1637-1655`).

### AddressManager & EPR semantics
- EPR 계산은 pending overlay 가 있을 때만 `addr_state` 를 덮어쓰지만, `_is_program_base` 가 PROGRAM_SLC/COPYBACK_PROGRAM_SLC 만 true 로 반환하므로 suspend 베이스는 영향을 주지 않는다(`addrman.py:1055-1211`).
- overlay 가 비어 있는 경우 `_effective_state` 는 실시간 `addrstates` 를 사용하므로 multi-die 혼합 시에도 각 die 가 자신의 실측 상태로 평가된다(`addrman.py:1044-1052`).
- 새 트랜잭션에서 동일 block 을 대상으로 한 PROGRAM 이 예약되려면 plane/die 윈도우 해제 + suspend 룰 해제가 동시에 필요하므로 overlay 부재만으로는 충돌을 허용하지 않는다.

### Residual risk assessment
- Config 에서 `state_forbid_suspend` 규칙이 비활성화되거나 die 윈도우가 조기 해제되면, suspend 상태에서 plane 윈도우가 원래 종료 시각 이후 비어 있어 동일 block 프로그램이 제안될 수 있다. overlay 는 이를 막지 못하므로 해당 조합에 대한 회귀 테스트가 필요하다.

## 코드 참조
- `resourcemgr.py:499` – instant 예약 경로와 overlay 업데이트 호출
- `resourcemgr.py:1458` – `_update_overlay_for_reserved` 가 ERASE/PROGRAM 계열만 처리
- `resourcemgr.py:552` – plane/die 윈도우 필드에 커밋된 예약 저장
- `resourcemgr.py:629` – suspend 커밋이 axis 상태를 열고 ongoing meta 를 이동
- `resourcemgr.py:1637` – `state_forbid_suspend` 규칙이 suspend 상태에서 베이스를 필터링
- `addrman.py:1044` – `_effective_state` 가 pending overlay 와 실제 addr_state 를 병합
- `addrman.py:1133` – EPR 규칙이 PROGRAM/READ 베이스에 대해서만 overlay 를 참조

## 아키텍처 인사이트
- overlay 를 트랜잭션 스코프에 한정해 suspend/resume 시나리오에서도 AddressManager 상태가 단일 소스로 남도록 설계되어 있다.
- 실제 충돌 방지는 overlay 가 아니라 예약 윈도우와 suspend 상태 룰에 의해 이루어지며, 이는 overlay 스테일 여부와 무관하게 안전성을 제공한다.
- Multi-die 토폴로지에서도 overlay 키가 die 를 포함하기 때문에 die 간 간섭이 없다.

## 역사적 맥락
- `research/2025-09-18_08-13-53_suspend_resume_addr_state.md` – suspend/resume 시 OP_END 중복으로 addr_state 가 증가한 사례와 보호 메커니즘의 부재가 기록되어 있다.

## 관련 연구
- `research/2025-09-23_11-25-24_suspend_resource_effects_refresh.md`

## 미해결 질문
- `exclusions_by_suspend_state` 구성이 비활성화된 경우를 대비한 회귀 테스트(동일 block PROGRAM 제출 시 거부 여부) 추가가 필요하다.
- 장기 suspend 동안 plane/die 윈도우와 suspend 룰 조합이 scheduler 병목을 일으키지 않는지 시뮬레이션이 요구된다.
