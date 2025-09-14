---
date: 2025-09-14T18:32:37+09:00
researcher: Codex Agent
git_commit: 6bcd2d5b903803b2e9f24a7ae48e75ddb1dc775f
branch: main
repository: nandseqgen_v2
topic: "Second run schedules nothing after SUSPEND alters suspend_states"
tags: [research, codebase, scheduler, proposer, resourcemgr]
status: complete
last_updated: 2025-09-14
last_updated_by: Codex Agent
---

# 연구: Second run schedules nothing after SUSPEND alters suspend_states

**Date**: 2025-09-14T18:32:37+09:00
**Researcher**: Codex Agent
**Git Commit**: 6bcd2d5b903803b2e9f24a7ae48e75ddb1dc775f
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
main.py 실행 시 --num_runs 2 이상에서 첫 번째 run 중 SUSPEND 수행으로 suspend_states가 변경된 뒤, 두 번째 run 시작 시 아무 operation도 예약되지 않는 문제의 원인과 개선 방안은?

## 요약
- 원인: 두 번째 run 시작 시점(now_us)이 SUSPEND op_state 구간(예: PROGRAM_SUSPEND.CORE_BUSY) 안에 걸리면, proposer가 사용한 phase key가 해당 SUSPEND 상태가 되어 `exclusions_by_op_state`의 on_busy 그룹이 적용되고, 이 그룹이 PROGRAM_RESUME/ERASE_RESUME까지 차단하여 후보가 소거된다. 그 결과 첫 refill에서는 후보가 없어 예약이 안 된다.
- 확인 포인트: `exclusions_by_op_state`에서 `PROGRAM_SUSPEND.CORE_BUSY`와 `ERASE_SUSPEND.CORE_BUSY`가 on_busy에 매핑되어 있고, on_busy가 RESUME류를 포함해 광범위 차단한다.
- 개선(권장):
  1) 설정 수정: on_busy에서 PROGRAM_RESUME/ERASE_RESUME를 제외하거나, SUSPEND.CORE_BUSY용 별도 그룹(on_busy_allow_resume)을 도입해 RESUME만 허용.
  2) 로직 수정: proposer의 phase key 파생에서 SUSPEND.*를 해당 패밀리의 가상 END(예: PROGRAM.END)로 취급하여 RESUME 제안을 가능하게 함(플래그 가드).
  3) 보완책: run 시작 시간을 state timeline 끝 이후로 정렬하거나(queue_refill 주기 단축), 첫 refill 실패 시 빠른 재시도를 스케줄.

## 상세 발견

### Run 경계와 now_us 정렬
- 두 run 사이에 동일 `ResourceManager` 인스턴스를 공유하며, 새 run 시작 시점은 plane avail의 최대값에 정렬됨: `main.py:945` (`run_once`)와 `main.py:1118`.
- SUSPEND은 plane 예약 창(plane_resv)을 만들지 않는 “instant” 경로지만, op_state 타임라인에는 `PROGRAM_SUSPEND.*` 또는 `ERASE_SUSPEND.*` 세그먼트를 추가한다: `resourcemgr.py:1200`, `resourcemgr.py:569`.

### SUSPEND의 타임라인/축 상태 처리
- 커밋 시 SUSPEND는 축 상태를 열고, 진행 중인 op를 suspended로 이동, 해당 패밀리의 CORE_BUSY를 절단: `resourcemgr.py:1193`, `resourcemgr.py:1219`.
- RESUME 시 축 상태를 닫음: `resourcemgr.py:1065` 부근.

### Proposer의 phase key 및 분포 결정
- phase key 파생: 우선 op_state, 없으면 hook 라벨, 그 외 RM의 가상 키(END) 사용: `proposer.py:1078`.
- 분포 계산 시 `exclusions_by_op_state`를 조회하여 현재 key에서 금지된 base를 제거: `proposer.py:258` (`_excluded_bases_for_op_state_key` 경유), `proposer.py:220` (`_apply_phase_overrides`).
- 상태 차단(ODT/서스펜드/캐시) 사전 필터도 존재: `proposer.py:1376`.

### 현재 설정의 상호작용 문제
- `config.yaml:2311` 이하에서 `exclusions_by_op_state`에 `PROGRAM_SUSPEND.CORE_BUSY: ['on_busy']`/`ERASE_SUSPEND.CORE_BUSY: ['on_busy']`가 선언됨.
- on_busy 그룹에 PROGRAM_RESUME/ERASE_RESUME가 포함되어 있어, SUSPEND.CORE_BUSY 구간에서는 RESUME까지 차단됨: `config.yaml:726` 부근(on_busy).
- `phase_conditional`는 비어 있으나, `phase_conditional_overrides.global`에서 PROGRAM_RESUME/ERASE_RESUME 등에 가중치를 부여함: `config.yaml:4728`~`config.yaml:4780`.
- 하지만 현재 key가 SUSPEND.CORE_BUSY이면 on_busy 차단으로 인해 해당 후보들이 분포에서 제거되어 첫 refill 시 “no_candidate”가 발생할 수 있음.

## 코드 참조
- `main.py:945` - 새 run의 시작 시점 정렬(t0=RM avail max).
- `resourcemgr.py:569` - commit 경로에서 타임라인/창 반영.
- `resourcemgr.py:1193` - SUSPEND 축 상태 오픈 및 ongoing→suspended 이동.
- `resourcemgr.py:1219` - 해당 패밀리 CORE_BUSY 타임라인 절단.
- `proposer.py:1078` - phase key 파생(op_state→hook→RM 가상 END).
- `proposer.py:220` - 분포 계산에서 op_state 기반 배제 적용.
- `proposer.py:1376` - 상태 기반(서스펜드/ODT/캐시) 후보 차단.
- `config.yaml:2311` - exclusions_by_op_state에 SUSPEND.CORE_BUSY 매핑.
- `config.yaml:726` - on_busy 그룹(RESUME 포함) 정의.
- `config.yaml:4728` - phase_conditional_overrides.global 가중치(RESUME 포함).

## 아키텍처 인사이트
- “상태별 차단(exclusions_by_op_state)”와 “축 상태별 차단(exclusions_by_suspend_state)”이 중첩될 때, SUSPEND.CORE_BUSY에 대한 on_busy 적용이 RESUME까지 막아 의도와 달라질 수 있다.
- phase key가 SUSPEND.*로 남아 있는 동안은 config 레벨에서 RESUME 허용을 특별 취급하지 않으면 제안 불가 상태가 발생한다.

## 관련 연구
- research/2025-09-14_15-11-24_suspend_resume_flow.md

## 미해결 질문
- RESUME를 SUSPEND.CORE_BUSY 구간에서도 허용하는 것이 정책적으로 타당한지? 만약 그렇다면 어떤 다른 op들을 함께 허용해야 하는지(READ 등).
- run 경계(now_us) 정렬을 타임라인 기반으로 더 보수적으로 잡아야 하는지(예: SUSPEND 구간 종료 이후로) 여부.

## 개선 제안(대안 비교)
- 설정 수정(권장): SUSPEND.CORE_BUSY용 차단 그룹을 on_busy에서 분리해 RESUME 허용
  - 장점: 코드 변경 없이 정책으로 즉시 해결. 위험도 낮음.
  - 단점: 설정 복잡도 증가. 그룹/키 조합 관리 필요.
  - 제안 예시:
    - on_busy에서 PROGRAM_RESUME, ERASE_RESUME 제거 또는
    - `exclusion_groups.on_busy_allow_resume` 신설(RESUME 미포함) 후
      `exclusions_by_op_state`의 `PROGRAM_SUSPEND.CORE_BUSY`/`ERASE_SUSPEND.CORE_BUSY`를 해당 그룹으로 매핑.

- proposer 로직 수정: SUSPEND.*를 가족 END 키로 매핑하는 phase key 정규화(플래그 가드)
  - 장점: 설정 의존도를 낮추고 SUSPEND 시 RESUME 제안이 일관적으로 가능.
  - 단점: phase key 의미가 달라져 분석/로그 해석에 주의 필요.
  - 위치: `proposer.py:_phase_key`에서 `... if st.endswith('SUSPEND.CORE_BUSY'):` 시 `PROGRAM.END`/`ERASE.END`로 치환(새 feature flag로 가드).

- run 정렬/스케줄링 보완: run 시작을 SUSPEND 구간 종료 이후로 이동 또는 refill 주기 단축
  - 장점: 첫 refill에서 no_candidate를 회피하거나 지속시간 최소화.
  - 단점: 시간 축 왜곡(정렬) 또는 전체 스케줄링 빈도 증가(성능 영향).
  - 위치: `main.py:933` 근처 t0 계산 시 RM 타임라인 최대 종료 시각과의 max를 사용하거나, `policies.queue_refill_period_us`를 축소.

## 재현 메모
- 실행: `python main.py --num-runs 2 --run-until 100000 -n 2 --out-dir out`
- 첫 run에 PROGRAM/ERASE 중 하나가 *_SUSPEND를 수행하도록 확률을 높인 뒤, 두 번째 run 첫 refill 로그에서 `[proposer] try ... -> state_block`/`no_candidate` 확인.

## 권장 next steps
- 단기: 설정 변경으로 RESUME 허용(위 설정 수정안 적용) 후, 2-run 시 두 번째 run의 첫 refill에서 RESUME 제안 유무 확인.
- 중기: `_phase_key` 정규화 플래그 도입 여부 결정 및 A/B 비교(설정만 vs 로직 보정).

