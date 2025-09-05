---
date: 2025-09-05T12:15:00+09:00
researcher: codex
git_commit: HEAD
branch: main
repository: nandseqgen_v2
topic: "PRD 훅 명칭 ↔ 구현 훅/틱 주기 대응, config 키 정합성"
tags: [research, scheduler, hooks, cadence, admission_window, config]
status: complete
last_updated: 2025-09-05
last_updated_by: codex
---

# 연구: 훅/틱 주기 대응 및 설정 키 정합성

## 1-Pager

- 배경: PRD §5.3은 이벤트 훅 기반의 결정적 시간 전진과 훅/윈도잉 규칙을 정의한다. 현재 데모(`nandsim_demo.py`)는 힙에 `(time, seq, type, payload)`를 넣고 하나씩 처리한다. `config.yaml`에는 `policies.*` 키가 이미 존재한다.
- 문제: (1) PRD의 훅 명칭(QUEUE_REFILL/PHASE_HOOK/OP_START/OP_END)을 구현 훅/틱 주기와 정확히 매핑하고, 동시각(co-timed) 훅 처리 순서를 결정한다. (2) `scheduler.*`로 제안한 설정 키와 기존 `config.yaml` 키의 중복·충돌을 방지한다.
- 목표: 결정적·재현 가능한 훅 처리 순서/틱 정의, run_until/윈도우 준수, 기존 설정 키 재사용 정책 확정.
- 비목표: Proposer/RM의 로직 변경, UI/CLI 개편.

## 소스 근거

- PRD `docs/PRD_v2.md:211` ~ `:241` — 훅 정의, 윈도우, RNG 분기, run_until.
- PRD `docs/PRD_v2.md:141` ~ `:206` — CFG 정책 및 phase_conditional 규칙.
- 데모 러너 `nandsim_demo.py:2394` ~ `:3090` — 현재 힙 기반 훅 처리 구현, 주기적 REFILL/부트스트랩 처리.
- 설정 `config.yaml:18` ~ `:27` — `policies.*` 키(admission_window, queue_refill_period_us, topN, epsilon, maxplanes, maxtry_candidate 등).

## 결론 1 — 훅 ↔ 틱/주기 매핑(권장 구현)

1) Tick 정의(동시각 훅 처리):
   - “동일 시각(time)의 모든 훅 묶음 = 1 틱”으로 간주한다(PRD §5.3).
   - 구현: 최소시간 `now`를 결정한 뒤, 큐(front)의 time이 `now`와 같을 동안 이벤트를 계속 팝/처리한다. 처리 중 동일 시각으로 푸시된 새 이벤트도 같은 틱에서 이어 처리한다.

2) 동시각 훅 처리 우선순위(안정적·결정적):
   - OP_END → PHASE_HOOK → QUEUE_REFILL → OP_START
   - 이유:
     - OP_END가 먼저 리소스/래치를 해제해야 직후 PHASE_HOOK에서 최신 상태로 제안 가능.
     - PHASE_HOOK는 제안/예약을 발생시키므로 REFILL보다 앞서 실행되면 불필요한 REFILL 노이즈를 줄임.
     - QUEUE_REFILL는 훅/큐 고갈 보전 목적이므로 마지막에 새 PHASE_HOOK를 주입하되, 같은 틱 내에서 즉시 처리되도록 “동시각 반복 루프”가 흡수한다.
     - OP_START는 관측/로그 성격이 강하고 자원 해제/제안에 영향 없으므로 최후순.
   - 구현 팁: 힙 pop 시 `(time, type_rank, seq, payload)`로 보조 정렬을 사용하거나, 같은 time에서 버퍼링 후 타입별 정렬 처리.

3) 훅 생성 주기 및 시점:
   - QUEUE_REFILL: `CFG[policies][queue_refill_period_us]` 주기로 생성(시작 시 1회 프라임). payload 없음.
   - PHASE_HOOK: 오퍼레이션 스케줄 시 각 state 구간 전/후에 생성. ISSUE state는 생성 금지(PRD 규정). state 경계 시간에 ±epsilon 난수 변형은 “훅별 RNG 분기” 스트림에서 발생.
   - OP_START/OP_END: 예약 확정 시 정확한 시작/종료 시각에 생성. END에서 커밋·래치 전이를 수행.

4) Admission Window 집행:
   - PHASE_HOOK 처리 중 `Proposer.propose(now, hook, cfg, res, addr, rng)` 호출.
   - `CFG[policies][admission_window]`(=W) 윈도우 내 earliest feasible `t0`만 허용. `instant_resv=true`인 base는 상한 무시.

5) run_until 및 종료 루틴:
   - run 루프는 `now >= run_until`이면 종료 루틴으로 진입. 종료 미완료 op는 지속되지만 그 시간대에는 propose 금지.
   - 복수 run 시 `num_runs>1`에서 bootstrap은 최초 run에만 적용(PRD 규정).

## 결론 2 — 설정 키 정합성(중복 회피 정책)

- 재사용(권장): 다음 키는 기존 경로를 그대로 사용한다.
  - `policies.admission_window` — 윈도 폭(초/마이크로초 단위 명명 일관 유지)
  - `policies.queue_refill_period_us` — REFILL 주기
  - `policies.topN`, `policies.epsilon_greedy`, `policies.maxtry_candidate`, `policies.maxplanes` — Proposer 정책
  - `phase_conditional`, `phase_conditional_overrides` — 분포 정의/오버라이드

- 신규(필요 시): 기존에 부재한 키만 추가한다. 중복 방지를 위해 위치는 아래를 권장.
  - `propose.mode` ∈ {legacy, progressive, hybrid} — 이미 데모에서 사용 중이므로 재사용
  - `propose.chunking.max_ops_per_chunk` — 데모와 동일 키 경로 재사용
  - `propose.chunking.checkpoint_interval` — 데모와 동일 키 경로 재사용
  - `propose.chunking.allow_partial_success` — 데모와 동일 키 경로 재사용
  - `bootstrap.enable` / `bootstrap.thresholds.*` / `bootstrap.minimums.*` / `bootstrap.celltype_weights` — 스케줄러 런타임 오버레이 제어(기존 키 없음 → 새로 추가)

- 지양(중복 위험): `scheduler.admission_window_us`, `scheduler.queue_refill_period_us`, `scheduler.chunk.*`처럼 기존 키와 의미 중복되는 신규 네임스페이스 추가는 피한다.

## 제안 구현 스펙(요약)

- 이벤트 큐: `(time, type_rank, seq, type, payload)`
  - `type_rank`: {OP_END:0, PHASE_HOOK:1, QUEUE_REFILL:2, OP_START:3}
  - `seq`: 동일 시간·동일 타입 내 안정 정렬을 위한 증가 시퀀스
- 틱 루프: `now = min_time; do { pop/handle while front.time == now } while front.time == now`
- REFILL 처리: 현재 시점(now)에 plane별/다이별 `PHASE_HOOK` NUDGE 주입 가능. REFILL 다음 주기는 `now + queue_refill_period_us`로 예약.
- 부트스트랩: `bootstrap.enable=true`면 단계별 오버레이 스왑(ERASE→PROGRAM→READ(+DOUT)). 완료 시 base 분포 복원. READ 통계는 타임라인 또는 내부 카운터 캐시로 집계.

## 리스크/완화

- 동시각 이벤트 폭주: 버퍼링 후 타입 우선순위 처리로 결정성 확보. 처리량이 많으면 per-type 핸들러에서 bulk 처리.
- 키 충돌: 위 정책으로 기존 키 재사용을 원칙으로 하되, 신규 키는 상위 네임스페이스(`bootstrap.*`, `propose.chunking.*`)로만 추가.

## 액션 아이템

1) 스케줄러 틱 구현에 co-timed 정렬/우선순위 적용.
2) 설정 스키마 문서화 업데이트: README/PRD에 “키 재사용/신규 추가 위치” 명시.
3) 부트스트랩 설정 샘플 추가: `bootstrap.enable=true`, thresholds/minimums 기본값 예시.

