---
date: 2025-09-07T07:58:08+00:00
researcher: Codex
git_commit: 306b495154519224c6511bd6f5f3c7b7cc546347
branch: main
repository: nandseqgen_v2
topic: "READ ok/selected but not scheduled; DOUT rejected by exclusion_multi"
tags: [research, codebase, scheduler, proposer, resourcemgr, exclusions, READ, DOUT]
status: complete
last_updated: 2025-09-07
last_updated_by: Codex
---

# 연구: READ ok/selected이나 스케줄 미반영 — DOUT가 exclusion_multi로 거절

**Date**: 2025-09-07T07:58:08+00:00
**Researcher**: Codex
**Git Commit**: 306b495154519224c6511bd6f5f3c7b7cc546347
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
ERASE→PROGRAM 이후 READ가 proposer 단계에서 ok 및 selected로 보이지만, 실제 스케줄/예약(operation_timeline/sequence)에는 반영되지 않는다. 원인은 READ의 후속 DOUT가 `exclusion_multi` 사유로 거절되기 때문. 원인 후보(우선순위)와 개선 방안을 제시하라.

## 요약
- 핵심 원인(높은 확률): Scheduler가 proposer의 계획된 `start_us`를 사용하지 않고, 같은 트랜잭션 내 모든 op를 `txn.now_us` 기준으로 예약한다. 그 결과 첫 op(READ)와 두 번째 op(DOUT)가 동일한 시간창에서 겹치며, ResourceManager의 die‑level 단일/멀티 배제 규칙(single×multi/multi×multi 금지, single×single은 제한적 허용)에 걸려 DOUT가 `exclusion_multi`로 실패한다.
- 보조 원인: DOUT는 `scope: NONE`이며 READ의 `inherit: ['multi']`에 의해 다중 plane 타깃을 상속하므로 MULTI로 분류되기 쉽다. READ와 DOUT이 같은 창에 겹치면 MULTI×MULTI(또는 SINGLE×MULTI) 충돌이 된다.
- 해결 우선안: Scheduler에서 배치 내 연쇄 예약 시, 각 op를 이전 op의 `end_us` 이후로 예약하도록 `txn.now_us`를 갱신하거나, RM의 reserve가 트랜잭션 보류 창(pending windows)을 고려해 earliest start를 계산하도록 보완한다. 전자는 변경 영향이 작고 의도에 부합한다.

## 상세 발견

### DOUT 거절 사유: die‑level 단일/멀티 배제
- `resourcemgr.py:205` 단일/멀티 분류: `scope==DIE_WIDE`면 MULTI, 그 외는 plane_set 길이>1이면 MULTI, 아니면 SINGLE.
- `resourcemgr.py:218` 이후 `_single_multi_violation`:
  - multi×multi, single×multi는 항상 충돌
  - single×single은 둘 다 허용 목록(`_ALLOWED_SINGLE_SINGLE_BASES`)에 있을 때만 허용. 기본값은 `{PLANE_READ, PLANE_READ4K, PLANE_CACHE_READ}`로 DOUT 미포함
- `resourcemgr.py:372` `reserve`는 트랜잭션의 보류 창(pending wins)까지 포함해 충돌 평가하며, 충돌 시 `Reservation(False, "exclusion_multi", ...)` 반환

### Proposer는 연쇄를 "순차"로 계획하지만 Scheduler는 실제 예약 시각을 따르지 않음
- Proposer는 READ 다음 단계를 1‑스텝 확장하고, `_preflight_schedule`에서 첫 op의 종료 뒤에 다음 op의 earliest feasible을 계산해 `ProposedOp.start_us`로 반환
  - `proposer.py:766` `_preflight_schedule`
- Scheduler는 예약 시 `rm.reserve(txn, op, targets, scope)`만 호출해 RM이 `start=max(txn.now_us, earliest_planescope)`로 계산하게 하고, proposer가 준 `p.start_us`를 사용하지 않음
  - `scheduler.py:286` 예약 호출; `txn.now_us`를 op 간에 갱신하지 않음
- RM.reserve는 self._plane_resv(커밋된 창)만 planescope 충돌 검사에 사용하고, 트랜잭션 보류 창은 die‑level 배제에서만 고려함. 따라서 두 번째 op(DOUT)는 같은 `txn.now_us`로 시도되어 첫 op(READ)의 보류 창과 "시간상 겹치게" 되고, 배제 규칙에 걸림.

### 설정 관점: READ→DOUT 연쇄와 MULTI 상속
- READ base는 확률적으로 DOUT 또는 CACHE_READ.SEQ를 2단계로 연쇄하며 `inherit`에 `multi` 포함
  - `config.yaml:194` READ.sequence 정의(확률/상속)
- DOUT는 `scope: NONE`, `instant_resv: false`
  - `config.yaml:423` DOUT base 정의

## 코드 참조
- `scheduler.py:286` - `d.rm.reserve(txn, op, p.targets, p.scope)` 호출(계획된 `start_us` 미반영)
- `scheduler.py:281` - admission window는 첫 op만 검사(현행 코드는 윈도우 이슈 아님)
- `resourcemgr.py:214` - `_multiplicity_kind`: scope/plane_set 기반 단일/멀티 판정
- `resourcemgr.py:218` - `_single_multi_violation`: single×multi/multi×multi 금지, single×single 제한적 허용
- `resourcemgr.py:372` - `reserve`에서 `exclusion_multi` 반환 경로
- `config.yaml:194` - READ → DOUT 연쇄와 `inherit: ['multi']`
- `config.yaml:423` - DOUT base: `scope: NONE`, `instant_resv: false`

## 아키텍처 인사이트
- Proposer는 "첫 op만 창 내 보장" + "연쇄는 사전(feasible_at) 점검" 모델을 취함.
- Scheduler는 현재 배치 예약 시각을 일관되게 전달하지 않아, 같은 트랜잭션 내 연쇄 op 간 시간 중첩이 생김(특히 READ↔DOUT). die‑level 배제 규칙이 이를 즉시 탐지하여 `exclusion_multi`로 실패 처리.

## 역사적 맥락(thoughts/ 기반)
- 직전 연구 문서에서는 window 재검증으로 인한 롤백을 지적했으나, 코드 최신화로 첫 op만 window 검사하도록 개선됨. 이번 이슈는 배치 시각 전달 미흡에 따른 die‑level 배제 충돌로 성격이 다름.

## 관련 연구
- `research/2025-09-07_07-01-11_read_not_scheduled_after_erase_program.md`

## 미해결 질문
- 연쇄 op(DOUT/CACHE_READ_END 등)의 시간 모델: READ와 동시/중첩을 허용해야 하나, 반드시 후행해야 하나? PRD 기준 후행이라면, 스케줄러가 강제해야 함. -> (검토완료) 반드시 후행해야 함. 그리고 DOUT 의 경우 READ 가 multi-plane 이었다면 `PRD_v2.md:333` 에서처럼 plane_set 의 각 plane 모두에 대해서 DOUT 을 생성해야 한다. 예를 들면, READ 가 plane_set {0,1,2} 였다면, DOUT for 0,1,2 를 각 각 순차적으로 예약해야 한다.

## 개선 방안
- 우선안(권장, 코드 변경 작음): Scheduler가 배치 내 각 op 예약 사이에 `txn.now_us`를 직전 예약의 `end_us`로 업데이트하여 중첩을 방지.
  - 구현 예시: `r = reserve(...)` 성공 후 `txn.now_us = float(r.end_us)`로 갱신.
  - 효과: proposer의 `_preflight_schedule` 가정과 실제 예약의 시간 정렬이 일치하며, die‑level 배제 위반 제거.
- 대안 A: RM.reserve가 트랜잭션 보류 창(txn.plane_resv)의 최신 종료시각을 start 산정에 포함(earliest_planescope에 보류 창 반영) — 스케줄러 수정 없이 완화 가능하지만 책임 경계가 모호해짐.
- 대안 B: DOUT를 `instant_resv: true`로 설정해 admission window만 우회(현재 문제는 배제 충돌이므로 근본 해결 아님).
- 대안 C: `_ALLOWED_SINGLE_SINGLE_BASES`에 DOUT 추가로 READ(SINGLE)와 DOUT의 중첩만 완화 — READ가 MULTI거나 타 op와의 충돌은 여전.

장단점/위험(요약):
- 스케줄러 갱신: 단순·의도 일치/회귀 위험 낮음.
- RM 보완: 범용성 높음/모듈 경계 혼탁·이중 정책 위험.
- 설정 편법(DOUT instant 또는 허용 목록 확장): 빠름/근본 원인(시간 중첩) 미해결.

