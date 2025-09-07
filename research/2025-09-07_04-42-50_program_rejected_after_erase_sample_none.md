---
date: 2025-09-07T04:42:50Z
researcher: Codex CLI
git_commit: 9424de5ee928bfc1bc7b25e724a3d053589c2e78
branch: main
repository: nandseqgen_v2
topic: "PROGRAM rejected after ERASE (sample_none) under SLC-only PC overrides"
tags: [research, codebase, proposer, scheduler, AddressManager, phase_conditional, SLC]
status: complete
last_updated: 2025-09-07
last_updated_by: Codex CLI
last_updated_note: "RM overlay와 AM 상태 이중 관리 동기화에 대한 후속 연구 추가"
---

# 연구: PROGRAM rejected after ERASE (sample_none)

**Date**: 2025-09-07T04:42:50Z
**Researcher**: Codex CLI
**Git Commit**: 9424de5ee928bfc1bc7b25e724a3d053589c2e78
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
ERASE 수행 이후 PROGRAM 동작이 proposer 단계에서 `sample_none` 사유로 거절됨. 단순화를 위해 `config.yaml`의 `phase_conditional_overrides`를 조정해 celltype=SLC의 ERASE/PROGRAM/READ만 남기고 확률을 조정(기타는 0)한 상태. 원인 코드를 찾아 개선 방안을 제시하라.

## 요약
- 원인: Scheduler가 커밋된 ERASE/PROGRAM의 주소 상태 효과를 AddressManager(AM)에 적용하지 않아, Proposer가 다음 후보로 PROGRAM을 샘플링할 때 AM 상태에서 "지워진 블록"이 존재하지 않는다. 그 결과 `_sample_targets_for_op(... sample_pgm ...)`이 빈 결과를 반환하며 `sample_none`으로 기록된다.
- 증거: proposer 로그에서 첫 ERASE 커밋 후 다수의 `Page_Program_SLC -> sample_none`이 이어짐. AM의 `sample_pgm` 조건상(ERASE 완료 블록 필요) 현재 코드 경로로는 당연한 결과.
- 추가 요인: Admission window(기본 0.5us)와 긴 ERASE tBUSY(약 7000us)로, ERASE 진행 중에는 Block Erase 자체도 `window_exceed`, PROGRAM은 `sample_none`이 반복된다. ERASE.END 시점에 PHASE_HOOK가 발생하더라도 AM 상태 갱신이 없어 PROGRAM 샘플링이 계속 실패한다.
- 개선 방향(우선순위):
  1) Scheduler의 `OP_END` 처리에서 ERASE/PROGRAM 류의 효과를 AddressManager에 즉시 반영(apply)한다.
  2) 보완: `phase_conditional_overrides`로 CORE_BUSY 단계에서 PROGRAM 가중치를 0으로 낮춰 불필요한 `sample_none` 로그를 줄인다.
  3) 대안(장단점 있음): RM 트랜잭션 오버레이를 Proposer 샘플링에 반영(보다 침습적).

## 상세 발견

### Proposer 샘플링 경로와 `sample_none`
- proposer는 E/P/R 후보에 대해 AddressManager를 통해 타겟을 샘플링한다.
  - `proposer.py:1032` 이후 분기에서 E/P/R 계열은 AddressManager 샘플링 호출.
  - 샘플 실패 시 `sample_none` 기록 후 다음 후보로 이동.
  - 코드: `proposer.py:1045` → `proposer.py:1049-1053` (`sample_none` 로그)

- PROGRAM 샘플링 조건(단일 플레인):
  - `addrman.py:492` 시작의 `sample_pgm` 구현 참조.
  - 핵심 마스크: `states >= ERASE` 이고 `(fresh & erase_mode==mode) or (cont & pgm_mode==mode)`이어야 함. `addrman.py:510-519`.
  - 즉, ERASE가 완료되어 해당 블록의 상태가 -1(ERASE)로 반영되어 있어야 PROGRAM 첫 페이지 샘플이 가능.

### Scheduler의 상태 반영 부재
- Scheduler는 커밋 시 ResourceManager 타임라인과 배타/버스/래치 등을 갱신하지만, AddressManager 상태를 갱신하지 않는다.
  - `scheduler.py:139-168` `OP_END` 핸들러는 ODT/CACHE 같은 RM 내부 상태만 처리.
  - AddressManager의 `apply_erase`/`apply_pgm`는 어디에서도 호출되지 않음(검색 결과 참조).

### 로그 증거(현상 재현)
- 첫 제안/커밋 직후:
  - `out/proposer_debug_250907_0000001.log:7-9`: `Block_Erase_SLC -> ok`, 이후 선택됨.
  - 이후 주기 리필 훅에서:
    - `out/proposer_debug_250907_0000001.log:14-16`: `Page_Program_SLC -> sample_none`, `Read_SLC -> sample_none`, ERASE는 `window_exceed`(t0≈7000.110, W=0.5).
  - 이 패턴이 ERASE 종료 전까지 반복됨(예: `log:175-177`, `log:217-219`, …).

### 구성 영향
- `phase_conditional_overrides`에서 기본 분포가 SLC ERASE/PROGRAM/READ로 제한됨:
  - `config.yaml:4526-4534`: `Block_Erase_SLC: 0.1`, `Page_Program_SLC: 0.2`, `Read_SLC: 0.7` (기본 분포.
- ERASE SLC tBUSY가 매우 큼: `config.yaml:2368` 앵커 `erase_slc_busy: 7000.0` → `Block_Erase_SLC.CORE_BUSY`가 장시간 지속.
- Admission window 기본 0.5us: `config.yaml:18` 인근 `admission_window: 0.5`.

## 코드 참조
- `proposer.py:1049` - 샘플 실패 시 `sample_none` 기록 경로.
- `addrman.py:510` - PROGRAM 샘플링 기본 조건(ERASE 완료 필요).
- `scheduler.py:144` - `OP_END` 처리(AM 상태 갱신 부재).
- `out/proposer_debug_250907_0000001.log:14` - `Page_Program_SLC -> sample_none` 관측.
- `config.yaml:4526` - SLC 전용 분포(ERASE/PROGRAM/READ 비중).

## 아키텍처 인사이트
- PRD는 “현재 시점 참조; 미래 값은 별도 관리”를 명시한다. 현재 구현은 RM 타임라인(미래 실행 포함)을 정확히 관리하지만, 주소 상태(AM)를 시간 진행에 맞춰 반영하지 않아 Proposer의 샘플링 소스가 낡은(초기) 상태에 머문다.
- RM의 txn overlay(`resourcemgr.py:915` 등)는 EPR 검증을 위해 예약 효과를 보조적으로 담지만, Proposer 샘플링 경로에서는 사용하지 않는다.

## 개선 방안

1) OP_END 시 AddressManager 상태 갱신(권장, 최소 변경)
- 위치: `scheduler.py:_handle_op_end`.
- 규칙:
  - ERASE 종료 시: 해당 `targets` 블록을 `apply_erase(..., mode=celltype)`로 반영.
  - PROGRAM류 종료 시: `apply_pgm(..., mode=celltype)`로 페이지 증가 반영.
  - READ류: 상태 변화 없음.
- 세부:
  - `op_name`으로 celltype 조회: `cfg['op_names'][op_name]['celltype']` 또는 `proposer._op_celltype(cfg, op_name)`.
  - `apply_*` 인자 형식은 ndarray (#, *, 3) of (die, block, page). `targets`에서 (die, block, page) 추출하여 래핑.
- 장점: 간단, Proposer 샘플링이 즉시 일관화됨. PHASE_HOOK의 post-END가 OP_END 이후 처리되므로, 동일 틱에서 PROGRAM 샘플 성공.
- 단점/주의: numpy 의존(이미 `requirements.txt` 포함). 사이드이펙트 타이밍은 END 기준(예상 동작과 일치).

2) Phase 분포 보정(보완) -> **적용안함**. exclusions_group 을 통해 이미 제외하고 있음.
- CORE_BUSY 단계에서 PROGRAM 가중치를 0으로 설정해 불필요한 `sample_none`을 줄임.
  - 예: `phase_conditional_overrides['ERASE.CORE_BUSY'] = {'Page_Program_SLC': 0.0}` 등.
- 장점: 로그 소음 감소, 윈도우 낭비 최소화.
- 단점: 근본 원인(AM 상태 미반영) 해결은 아님.

3) Proposer가 RM overlay를 고려(대안)
- `proposer._sample_targets_for_op`가 RM의 pending overlay를 참조(새 인터페이스 필요)해, END 직전에도 미래 상태를 가정한 샘플링을 수행.
- 장점: END 직전 계획 수립 가능.
- 단점: 경계 간 결합 증가, 구현 난이도/리스크 큼. 현재 단계에서는 비권장.

## 제안 패치(개요)
- `scheduler.py`에 AM 적용 추가:
  - `OP_END` 처리에서:
    - ERASE → `addrman.apply_erase(addrs, mode=cell)`
    - PROGRAM_SLC/CACHE_PROGRAM_SLC/ONESHOT_CACHE_PROGRAM/ONESHOT_PROGRAM_MSB_23h/ONESHOT_PROGRAM_EXEC_MSB/COPYBACK_PROGRAM_SLC/ONESHOT_COPYBACK_PROGRAM_EXEC_MSB/ALLWL_PROGRAM → `addrman.apply_pgm(addrs, mode=cell)`
  - `addrs` 제작: `np.dstack((dies, blocks, pages))` 형태 (#,1,3). ERASE의 page는 0로 무시됨.

## 관련 연구
- `research/2025-09-06_08-26-46_proposer_target_sampling_alignment.md` — Proposer 샘플링 대상을 E/P/R로 제한하고 후속(DOUT 등)은 훅 컨텍스트로 처리하는 정책 정리.

## 미해결 질문
- Cache Program/One-shot Program 계열(ONESHOT_*)의 상태 반영 시점: END 기준으로 충분한가? (현 PRD 관점에서는 예) -> (검토완료) END 로 충분.
- EPR 활성화 시, RM overlay와 AM 상태의 이중 관리에 대한 동기화 규칙(현재는 overlay는 검증용, AM은 샘플링용으로 분리 가정). -> 

## 후속 연구 2025-09-07T05:11:58Z

### 주제: RM overlay ↔ AM 상태 이중 관리 동기화 리스크와 가이드

- 개요: 샘플링은 AM(완료 시점 상태), 동시성/의존 검증은 RM txn overlay(동일 트랜잭션 내 예약 효과 반영)로 역할을 분리한다.
- 효과: 아직 끝나지 않은 ERASE/PROGRAM의 효과는 Proposer 샘플링에서 보이지 않으며(프리페치 방지), 같은 트랜잭션 내에서는 overlay로 보수적으로 금지한다.

구현 근거(코드):
- RM overlay 업데이트: `resourcemgr.py:915` 인근 `_update_overlay_for_reserved` — ERASE: `addr_state=-1`, PROGRAM: `addr_state=page(max)`.
- EPR 평가 경로: `resourcemgr.py:1019` — `constraints.enable_epr`와 `enabled_rules`에 `addr_dep`가 있어야 호출. 현재 `op_celltype=None`으로 전달됨.
- AddressManager EPR: `addrman.py:1112` 이후 — overlay로 `addr_state`를, AM 배열로 모드(erase/program)를 조회하여 규칙을 평가.

구체 리스크와 평가:
- R1. 읽기 vs 미래 프로그램(동일 txn)의 시간 경계 보수성
  - overlay는 예약 즉시 `addr_state`를 상승시켜, 같은 txn의 READ가 `offset_guard`로 과도 차단될 수 있음.
  - 완화: PROGRAM은 `DIE_WIDE`, READ는 `PLANE_SET`으로 die-level 배타 규칙이 중첩을 막음. 보수적 차단은 안전 관점에서 허용.

- R2. 동일 txn 내 celltype 일관성 공백
  - 현재 RM이 `op_celltype=None`을 전달하여 `epr_different_celltypes_on_same_block`은 비활성.
  - overlay는 모드 정보를 담지 않음. 동일 txn 내 즉시 모드 일관성 검증은 어려우나, 두 PROGRAM을 같은 txn에 동시 예약하는 것은 `DIE_WIDE`/배타 규칙으로 차단되어 실질 위험은 낮음. 다음 txn에서는 AM이 END 시점에 반영되므로 정상 검증 가능.

- R3. 샘플링-overlay 시점 불일치로 인한 `sample_none` 빈발
  - ERASE/PROGRAM 진행 중 AM 미반영으로 PROGRAM 샘플 실패 반복.
  - 완화: `phase_conditional_overrides`에서 `*.CORE_BUSY` 구간의 PROGRAM/READ 가중치 하향(0)으로 시도 축소.

권고 동기화 규칙:
- S1. AM 업데이트는 OP_END 시점에만 수행(샘플링의 원천 진실 유지).
- S2. RM overlay는 동일 txn 내 예약 효과를 단조 증가로 반영(보수적 금지).
- S3. EPR 활성화 설정을 명시적으로 사용:
  - `config.yaml` → `constraints.enabled_rules`에 `addr_dep` 추가, `enable_epr: true`.
  - 런타임 바인딩: `rm.register_addr_policy(am.check_epr)` (예시: `docs/VALIDATOR_INTEGRATION_GUIDE.md:29`).
  - 보강: `resourcemgr._eval_rules`에서 `op_celltype` 전달(`_proposer._op_celltype(cfg, op_name)` 사용)로 celltype 규칙 활성.

체크리스트(변경 제안):
- [ ] `scheduler.py:_handle_op_end` — ERASE/PROGRAM 계열에 대해 `addrman.apply_*` 호출(AM 동기화).
- [ ] `main.py` — 구성 허용 시 `rm.register_addr_policy(am.check_epr)` 호출.
- [ ] `docs/VALIDATOR_INTEGRATION_GUIDE.md` — `enabled_rules`/`enable_epr` 샘플 구성 명시.
- [ ] `resourcemgr._eval_rules`에서 `op_celltype` 전달 구현.

참조:
- `resourcemgr.py:915` — txn overlay 업데이트
- `resourcemgr.py:1019` — EPR 게이트/호출
- `addrman.py:1112` — EPR 규칙(overlay/AM 참조)
- `docs/VALIDATOR_INTEGRATION_GUIDE.md:20-31` — 구성/바인딩 예시

