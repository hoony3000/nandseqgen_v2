---
date: 2025-09-08T02:01:12+0900
researcher: codex
git_commit: 466c7452461bd6dfa5bad9cb3836462ed045c41d
branch: main
repository: nandseqgen_v2
topic: "PHASE_HOOK generation change risk — non-READ single pre/post; READ post per plane with sequence_gap"
tags: [research, codebase, scheduler, proposer, resourcemgr, prd]
status: complete
last_updated: 2025-09-08
last_updated_by: codex
---

# 연구: PHASE_HOOK 생성 정책 변경에 따른 리스크

**Date**: 2025-09-08T02:01:12+0900
**Researcher**: codex
**Git Commit**: 466c7452461bd6dfa5bad9cb3836462ed045c41d
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
PRD 규격대로 PHASE_HOOK을 변경할 때의 리스크를 분석:
- READ 계열이 아닌 경우: targets 수와 무관하게 operation 당 전/후 1개씩 생성
- READ 계열(READ/READ4K/PLANE_READ/PLANE_READ4K/CACHE_READ/PLANE_CACHE_READ/COPYBACK_READ/RECOVER_RD): t_end 전 시점 1개, t_end 후 시점은 plane_set 갯수만큼 생성하며, 각 후속 hook 사이에 `CFG[policies][sequence_gap]` 간격 적용

참조: docs/PRD_v2.md:280

## 요약
- 이벤트 수 감소(비-READ): per-target → per-op로 줄이면 제안 기회가 감소해 단기적으로 제안량/다양성이 줄 수 있음. QUEUE_REFILL 의존도 상승 위험.
- READ 후속 다중 훅: proposer가 이미 시퀀스 간격(`sequence_gap`)을 체인 스케줄에 적용 중이라, 훅 레벨 간격까지 더해 "이중 간격" 적용 위험.
- 중복 제안/중복 스케줄 시도: READ 종료 후 여러 post 훅이 순차 발생하면 첫 훅에서 DOUT 체인을 예약한 뒤, 뒤따르는 훅이 동일 후보를 반복 제안하여 preflight 실패/중복 시도가 늘 수 있음(성능/로그 노이즈).
- 훅 컨텍스트 축소(비-READ): per-op 훅에서 단일 (die, plane) 선택이 필요. 잘못 선택하면 기존 per-plane 훅이 유도하던 평면별 후속 제안 편향/기아 가능.
- 테스트/지표 영향: 훅 개수/타이밍에 의존한 테스트나 메트릭이 변경 필요.

## 상세 발견

### 현행 PHASE_HOOK 구현 요약
- scheduler는 각 state마다 target plane마다 pre/post 훅을 생성함. PRD의 "targets 무관 1개"와 상이함.
  - per-target pre/post 생성: `scheduler.py:472`, `scheduler.py:478`, `scheduler.py:486`
- READ 계열 훅 payload는 `plane_set`/`targets`로 보강되어 proposer가 후속 비‑EPR(DOUT 등)에 활용함.
  - 보강 조건/구성: `scheduler.py:429`, `scheduler.py:419-421`
- 이벤트 처리 우선순위: OP_END(0) → PHASE_HOOK(1) → QUEUE_REFILL(2) → OP_START(3)
  - `event_queue.py:6`

### READ 후속 체인과 sequence_gap 적용 위치
- proposer는 READ 이후 DOUT/CACHE_READ_END 등을 시퀀스로 확장하고, 후속 op들의 계획 start를 이전 op 종료 시각에 `policies.sequence_gap`을 더해 산출함.
  - 시퀀스 간격 적용: `proposer.py:1144`
- 또한 멀티‑플레인 READ의 후속 DOUT는 plane별로 분할(splitting) 가능하고, plane 순서를 유지함.
  - plane별 분할: `proposer.py:1096-1107`

이로 인해 훅 레벨에서도 post 훅을 plane_set 수만큼 `sequence_gap`으로 벌려 생성하면, 훅 간격 + 체인 간격이 중첩 적용될 수 있음(이중 간격).

### 리스크 상세
- 이벤트 수 감소(비‑READ)
  - 변화: per-target → per-op 2개(pre/post)
  - 영향: 훅 기반 제안 트리거 감소 → 동일 시간대 제안 다양성 저하 가능. 다만 QUEUE_REFILL(라운드 로빈)가 보완하나, 단기 반응성은 떨어질 수 있음.
  - 코드 맥락: 훅이 proposer의 1차 정상 참조(phase-key 정확)가 되는 경로임. 감소는 phase-key 정합 제안 기회 축소로 이어짐.

- READ post 훅 다중화로 인한 중복 제안
  - 변화: READ 종료 직후 post 훅을 plane_set 크기만큼 생성(간격 적용)
  - 영향: 첫 post 훅이 READ→DOUT 체인을 한 번에 예약하면, 뒤따르는 post 훅이 같은 후보(DOUT)를 반복 제안할 수 있음. 대부분 preflight에서 겹치기/배제/래치로 실패하며, propose 호출 증가와 로그 노이즈, 성능 저하 초래.
  - 코드 맥락: proposer는 단일 호출에서 전체 체인을 계획/예약하려고 시도함(`proposer.propose` → `_expand_sequence_chain` → `_preflight_schedule`).

- 이중 간격 적용 위험
  - 변화: post 훅 간 `sequence_gap` + 체인 예약 간 `sequence_gap`이 모두 적용됨
  - 영향: 의도보다 큰 시간 간격으로 DOUT가 밀려나 전체 처리량 저하 가능. admission window는 첫 op에만 적용되므로 기능적 실패는 아니지만 성능 스펙과 시각화가 달라질 수 있음.
  - 코드 맥락: 체인 간격 적용은 `proposer.py:1144`에 이미 존재. 훅 간격까지 추가되면 2중 반영.

- 훅 컨텍스트 축소에 따른 편향(비‑READ)
  - 변화: pre/post 훅을 1개로 만들 때 (die, plane)을 무엇으로 설정할지 결정 필요(첫 target? die-wide? plane=None 불가)
  - 영향: 비‑EPR fallback 타깃 선택 경로에서 단일 plane으로 치우칠 수 있음. QUEUE_REFILL 보완이 있으나 단기 편향 발생 가능.
  - 코드 맥락: 비‑EPR일 때 훅 제공 타깃이 없으면 훅의 die/plane을 사용해 기본 타깃을 구성함(`proposer.py:1478-1509` 인근 경로).

- 테스트/메트릭 변동
  - 변화: 훅 개수/타이밍/라벨이 바뀜
  - 영향: 훅 수를 가정한 테스트, 메트릭(`last_commit_bases`와 별개로 훅수)에 의존한 검증 로직 조정 필요.

## 코드 참조
- `docs/PRD_v2.md:280` - PHASE_HOOK 개수/READ 예외 및 `sequence_gap` 요구사항
- `scheduler.py:472` - per-target 훅 생성 주석(현행 동작과 상이)
- `scheduler.py:478` - pre_t PHASE_HOOK push
- `scheduler.py:486` - post_t PHASE_HOOK push
- `scheduler.py:419` - PHASE_HOOK 생성 가드/READ 보강 정책
- `event_queue.py:6` - 이벤트 우선순위(OP_END → PHASE_HOOK → QUEUE_REFILL → OP_START)
- `proposer.py:1096` - READ 후속 DOUT plane별 분할 시작 근방
- `proposer.py:1144` - 체인 예약 간 `sequence_gap` 적용
- `resourcemgr.py:570` - `phase_key_at`(경계에서 `<BASE>.END` 유도)

## 아키텍처 인사이트
- 간격 적용의 단일 진실: 훅 간격과 체인 간격을 동시에 적용하면 비직관적 타이밍이 발생. 간격 적용은 한 레이어(체인 또는 훅)로 통일하는 편이 안전.
- 중복 훅 대비: READ post 훅을 다중으로 만들 경우, 첫 훅이 체인을 예약했다면 후속 훅에서는 동일 체인을 재시도하지 않도록 가드(예: 해당 READ 타깃과 연계된 DOUT 예약/진행 여부 체크)가 필요.
- 비‑READ 훅 컨텍스트: per-op 훅에서 die/plane 선택 규칙을 명확히(예: 첫 target의 (die,plane) 또는 die만 의미 있고 plane은 REFILL에 위임) 정의해야 편향을 제어할 수 있음.

## 관련 연구
- `research/2025-09-07_19-37-19_queue_refill_phase_key_fallback.md` - PHASE_HOOK/QUEUE_REFILL의 phase-key 정합성 및 END 키 활용
- `research/2025-09-07_21-57-06_queue_refill_phase_key_past_key.md` - post 훅 경계에서의 키 해석 이슈와 RM fallback

## 미해결 질문
- 비‑READ per‑op 훅의 (die, plane) 결정 규칙은 무엇으로 할지? 첫 target 고정 vs die‑wide 의미화.
- READ post 훅 다중화 시, proposer가 한 번에 전체 DOUT 체인을 계획하는 현 접근과의 중복 시도 문제를 어떻게 방지할지?
- `sequence_gap`은 훅 간 vs 체인 간 어디에 적용할지 단일화할지?
