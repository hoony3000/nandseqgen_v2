---
title: "구현 계획: READ ok/selected이나 스케줄 미반영 — DOUT exclusion_multi 해결"
date: 2025-09-07
based_on: research/2025-09-07_07-58-08_read_not_scheduled_due_to_dout_exclusion_multi.md
status: draft
owners: ["Codex"]
---

# Problem 1-Pager

- 배경: proposer가 READ 이후 후속 DOUT을 계획(planned.start_us)하지만, scheduler가 같은 트랜잭션 내 연쇄 op를 모두 동일한 `txn.now_us` 기준으로 예약하여 READ와 DOUT이 시간상 중첩됨. `ResourceManager`의 die‑level 단일/멀티 배제 규칙(single×multi, multi×multi 금지)에 의해 DOUT가 `exclusion_multi`로 거절되는 현상이 재현됨.
- 문제: READ는 ok/selected로 선택되지만 실제 reservation/commit에는 반영되지 않음(후속 DOUT 실패로 전체 롤백). 원인은 scheduler의 연쇄 시각 전파 미흡 + DOUT 타겟 구성(멀티) 조합.
- 목표
  - G1: 동일 트랜잭션 내 연쇄 op의 시간 중첩 제거(READ → DOUT은 반드시 후행).
  - G2: PRD 준수: 멀티‑플레인 READ 이후 DOUT는 plane별로 순차 생성/예약.
  - G3: 회귀 최소화, 변경 범위 최소화(스케줄러/프로포저 경계 유지).
- 비목표
  - RM의 단일/멀티 배제 정책 변경(허용 목록 확대 등) — 근본 해결이 아님.
  - 버스 모델링/상태타임라인의 대폭 변경.
  - 하드웨어 타이밍/파라미터의 재보정.
- 제약
  - 결정성 유지(시계/시스템시간 사용 금지).
  - 함수 ≤ 50 LOC, 파일 ≤ 300 LOC 준수(필요 시 분리).
  - 기존 admission window 정책: 첫 op만 검사 유지.

# 원인 요약(근거 코드)

- scheduler가 proposer의 `ProposedOp.start_us`를 사용하지 않고 `rm.reserve(...)`만 호출하며, op 간 `txn.now_us`를 갱신하지 않음 → 같은 now에서 READ와 DOUT을 시도하여 중첩 유발.
  - `scheduler.py: _propose_and_schedule` 루프 참조: `scheduler.py:200` 이후 예약 루프에서 `txn.now_us` 갱신 없음.
- RM은 die‑level 단일/멀티 배제 규칙(single×multi/multi×multi 금지, single×single 제한적 허용)을 커밋 창과 보류 창(txn) 모두에 적용 → 중첩 시 DOUT가 `exclusion_multi` 실패.
  - 단일/멀티 판정 및 배제: `resourcemgr.py:200`대 `_multiplicity_kind`, `_single_multi_violation`
- READ → DOUT 연쇄와 'multi' 상속 규칙으로, DOUT가 멀티 타깃을 가지기 쉬움.
  - 설정 참조: `config.yaml:194`(READ.sequence), `config.yaml:520` 부근(DOUT base 정의)

# 미해결 질문(해결)

- 후속 op(DOUT/CACHE_READ_END 등)의 시간 모델은 반드시 후행해야 하는가? — 예, PRD 기준 후행. 또한 READ가 multi-plane이면 PRD_v2.md:333에 따라 plane별 DOUT를 각각 순차적으로 생성해야 함.
  - 근거: `docs/PRD_v2.md:333`

# 대안 비교(의사결정)

1) 우선안: Scheduler가 연쇄 예약 간 `txn.now_us = r.end_us`로 갱신
   - 장점: 변경 작음, 의도 일치(연쇄는 후행), 회귀 위험 낮음
   - 단점: proposer의 planned.start_us를 직접 반영하지는 않음(단, 목적 달성에는 충분)
   - 위험: 없음에 가까움(동일 트랜잭션 내 순차성 강화)

2) 대안 A: RM.reserve가 `earliest_planescope` 산정 시 txn 보류 창을 포함하여 earliest start를 상향
   - 장점: Scheduler 수정 불필요
   - 단점: 경계 혼탁(시각 정렬 책임이 RM으로 확장), 정책 이중화 위험

3) 대안 B: 설정 편법(DOUT instant_resv=true 또는 single×single 허용 목록에 DOUT 추가)
   - 장점: 빠른 완화
   - 단점: 근본 원인(시간 중첩) 미해결, 멀티×멀티 충돌 지속

→ 결정: 1) Scheduler 갱신 + 2) Proposer에서 multi-plane READ 후속 DOUT를 plane별로 분할(splitting) 생성. 설정 플래그로 on/off 가능하도록 함.

# 설계 개요

- Scheduler
  - 예약 루프에서 각 `reserve` 성공 직후 `txn.now_us = r.end_us`로 갱신하여 동일 txn 내 중첩을 제거.
  - admission window는 첫 op만 검사(현행 유지).

- Proposer
  - `_preflight_schedule`에서 2번째 op가 DOUT/DOUT4K/CACHE_READ_END/PLANE_CACHE_READ_END 류이고, 첫 op의 targets에 다수 plane이 포함되면 plane별로 2번째 op를 분할하여 연속으로 계획(planned). `policies.sequence_gap` 적용.
  - 기능 플래그 `policies.split_dout_per_plane`(기본 true)로 제어.

- ResourceManager
  - 변경 없음(정책/배제는 그대로 유지). Scheduler/Proposer의 시간 정렬 및 분할 생성으로 충돌 해소.

- 로깅/관찰성
  - proposer: 분할 로직이 활성일 때 plane 수와 분할 개수, 계획된 start_us 로그 추가(파일 로그에 한 줄).
  - scheduler: 일시적 print 디버그 제거(또는 feature-flag로 제어).

# 구현 단계(Tasks)

1) Scheduler: 연쇄 시각 갱신
   - 변경: `scheduler.py: _propose_and_schedule`
     - 각 op에 대해 `r = rm.reserve(...)` 성공 시 `txn.now_us = float(r.end_us)`로 갱신.
     - admission window는 `idx == 0`에만 적용 유지.
     - 기존 디버그 `print(...)` 제거 또는 `cfg[policies][debug_reserve_log]`로 토글.

2) Proposer: DOUT plane별 분할 생성
   - 변경: `proposer.py: _preflight_schedule`
     - 입력: `ops = [(name0, targets0), (name1, targets1?)]` 형태.
     - 분기 조건: 두 번째 op의 base가 {DOUT, DOUT4K, CACHE_READ_END, PLANE_CACHE_READ_END}, 첫 op의 plane_set 길이 > 1, 그리고 `CFG[policies][split_dout_per_plane] == true`.
     - 동작: `targets0`의 plane 순서로 `name1`을 plane 단위로 N개로 분할하여 체인 길이를 N+1로 확장한 뒤 기존 로직으로 순차 feasible_at → planned.start_us 채우기.
       - 각 분할 op의 targets는 해당 plane 하나만 포함(주소는 규칙 상속으로 얻은 `targets1`에서 해당 plane만 필터링).
       - `sequence_gap` 적용, 실패 시 전체 preflight 실패 처리(현행 일관성 유지).

3) Config: 정책 플래그 추가
   - `config.yaml`에 `policies.split_dout_per_plane: true` 기본값 추가.
   - 선택적으로 `policies.debug_reserve_log: false` 추가(개발 중 디버그 출력 제어).

4) 테스트 추가(tests/)
   - T1: 멀티‑플레인 READ(예: plane_set {0,1,2}) → DOUT. 기대: 예약 결과가 READ 1개 + DOUT 3개, 모두 reserve OK, exclusion_multi 없음, 시간상 순차(start_us/end_us 증가).
   - T2: 싱글‑플레인 READ → DOUT. 기대: READ 1 + DOUT 1, 순차, 성공.
   - T3: READ4K → DOUT4K(멀티). 기대: plane별 분할 적용 + 순차 성공.
   - T4: admission window 검증: 첫 op만 window 초과 시 거절, 분할된 후속 op는 window 미검사(현행 정책 유지).

5) 로깅/문서
   - proposer 로그에 분할 활성/비활성, 분할 개수, 계획된 t0/t_end 요약 1줄/배치 기록.
   - docs/ 변경 요약(PRD 준수 섹션 링크) 및 설정 플래그 설명 추가.

# 코드 영향도(사전/사후 조건 및 범위)

- 변경 파일
  - `scheduler.py:200` 부근 — `_propose_and_schedule` 예약 루프 내 `txn.now_us` 갱신 라인 추가, 디버그 프린트 정리.
  - `proposer.py:700` 부근 — `_preflight_schedule`에서 DOUT류 분할 로직 추가.
  - `config.yaml` — `policies.split_dout_per_plane`/`policies.debug_reserve_log` 추가.

- 사전 조건
  - proposer가 READ 후속으로 DOUT을 생성할 수 있어야 함(현행 OK).
  - scheduler는 배치 내 op들을 순서대로 처리함(현행 OK).

- 사후 조건
  - 동일 배치 내 READ → DOUT이 시간상 중첩하지 않음.
  - 멀티‑플레인 READ의 후속 DOUT는 plane별 op로 분할되어 순차 예약됨.
  - `ResourceManager.reserve`에서 `exclusion_multi`로 인한 롤백이 제거됨(테스트로 보장).

# 수용 기준(AC)

- AC1: 재현 케이스에서 `reserve_fail:exclusion_multi`가 0건이어야 함.
- AC2: 배치 커밋 건의 `metrics.last_commit_bases`가 `['READ', 'DOUT', 'DOUT', ...]` 순서(plane 수만큼 DOUT 반복)를 포함.
- AC3: `tests/`의 T1~T4가 결정적으로 통과(시드 고정, 무작위성 통제).

# 위험 및 완화

- 위험: 후속 op의 start 계산이 이전보다 보수적으로 늘어 전체 처리량이 미세 감소할 수 있음.
  - 완화: proposer의 preflight가 이미 back-to-back을 가정하고 있으며, 실 예약이 이를 따르도록 하는 변경이므로 기대 성능 영향은 경미함.

# 롤아웃 계획

1) 기능 플래그 기본 on(`split_dout_per_plane: true`)로 개발 환경에서 검증.
2) proposer 디버그 로그 활성 상태에서 샘플 러닝 후 out/*.log 점검.
3) 문제 없으면 디버그 출력 토글 off, 문서/릴리즈 노트 업데이트.

# 참고 링크(파일/라인)

- 연구: `research/2025-09-07_07-58-08_read_not_scheduled_due_to_dout_exclusion_multi.md`
- 스케줄러: `scheduler.py:200`
- 리소스매니저: `resourcemgr.py:200`, `resourcemgr.py:218`, `resourcemgr.py:372`
- 설정: `config.yaml:194`, `config.yaml:520` 근방(DOUT base)
- PRD: `docs/PRD_v2.md:333`

