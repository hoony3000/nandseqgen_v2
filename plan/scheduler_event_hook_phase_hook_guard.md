# Scheduler event_hook → PHASE_HOOK 제어 계획 (PRD v2 §5.3 반영)

## Problem 1‑Pager
- 배경: PHASE_HOOK는 proposer를 구동하는 핵심 트리거다. 현재 구현은 ISSUE를 제외한 모든 state(pre/post)에 대해 훅을 생성한다. READ 계열의 DATA_OUT, PROGRAM 계열의 DATA_IN에서도 훅을 만들면서 SR 등 비상태(affect_state=false) 오퍼레이션 제안 빈도가 높아져 훅이 과도하게 증가한다.
- 문제: SR 같은 가벼운 오퍼레이션이 대량 제안되며 PHASE_HOOK가 시간슬라이스마다 폭발적으로 증가. 큐 처리 비용 상승, 의미 없는 후보 평가 증가, 결정성 디버깅 난이도 상승.
- 목표: PRD §5.3의 주의 사항을 구현해 PHASE_HOOK 발생을 줄인다.
  - ISSUE/DATA_IN/DATA_OUT state에서는 PHASE_HOOK을 생성하지 않는다.
  - affect_state=false인 오퍼레이션은 어떤 state에서도 PHASE_HOOK을 생성하지 않는다.
  - 결정성/순서(동일 틱 원자성) 유지, READ→DOUT 등 연쇄 제안은 sequence 로직에 맡긴다.
- 비목표: proposer의 phase_conditional 분포/알고리즘 변경, op_state_probs.yaml 구조 변경, 광범위한 리팩터링.
- 제약: 이벤트 순서 안정성(‘OP_END→PHASE_HOOK→QUEUE_REFILL→OP_START’) 유지, hook payload 호환성 보존, 코드 변경 범위 최소화.

## 근거(레퍼런스)
- PRD 문서: docs/PRD_v2.md:253 이후
  - 5.3 Scheduler/event_hook/PHASE_HOOK 주의: 275–277
    - ISSUE/DATA_IN/DATA_OUT state 는 PHASE_HOOK 생성하지 않음.
    - affect_state=false 인 operation 은 PHASE_HOOK 생성하지 않음.
- 현행 코드 위치(생성 지점)
  - scheduler.py:348 — `_emit_op_events` 내 state별 pre/post PHASE_HOOK push (현재 ISSUE만 스킵)
- 설정 근거(affect_state)
  - config.yaml:447 — `SR` base 정의(affect_state=false)

## 변경 설계(가장 단순한 해법)
1) 상태 필터 추가(정책):
   - SKIP_STATES = {"ISSUE", "DATA_IN", "DATA_OUT"}
   - `_emit_op_events`에서 `if state in SKIP_STATES: continue` 적용.
2) affect_state 게이트 추가:
   - 현재 op의 base에 대해 cfg.op_bases[base].affect_state를 조회.
   - `affect_state == false`이면 해당 op의 모든 state에서 PHASE_HOOK 생성을 완전히 건너뜀(OP_START/OP_END는 기존대로 유지).
3) pre/post 훅 생성 정책은 유지:
   - 다른 state는 기존처럼 pre(끝나기 직전) + post(끝난 직후) 훅을 생성.
   - 향후 필요 시 `policies.phase_hook_skip_states`, `policies.phase_hook_pre_enabled`, `policies.phase_hook_post_enabled` 토글을 도입 가능(기본값: SKIP_STATES만 적용, pre/post 모두 활성).
4) 중복 방지(옵션, 사소):
   - 동일 틱·동일(die,plane,label)의 중복 push를 한 틱 내에서만 dedup하는 set 가드(초기 버전에서는 생략 가능; 훅 폭증은 주로 스킵 규칙으로 해소됨).

## 구현 단계(작고 안전한 변경)
- scheduler.py 수정
  - `_emit_op_events` 서두에서 `affect_state` 조회 함수 추가.
  - state 루프에서 `if name.upper() in SKIP_STATES: continue` 적용.
  - `affect_state == false`면 PHASE_HOOK 생성 블록 전체를 단락(safe return) — OP_START/OP_END push는 그대로.
- 설정/문서
  - 본 계획서 저장(이 문서).
  - PRD 5.3의 주의 문구를 구현 주석으로 링크(코드 내부 간단 주석 수준).

## 테스트 계획(결정적·격리)
- READ(DATA_OUT) 훅 미생성:
  - 멀티플레인 READ 예약 후 이벤트 큐에서 DATA_OUT 라벨의 PHASE_HOOK이 없음을 확인.
- PROGRAM(DATA_IN) 훅 미생성:
  - CACHE_PROGRAM_SLC 등 DATA_IN state 보유 op 예약 후 DATA_IN 라벨의 PHASE_HOOK이 없음을 확인.
- affect_state=false 게이트:
  - SR/SR_ADD/READID 등 예약 시 PHASE_HOOK이 전혀 없고 OP_START/OP_END만 있음을 확인.
- 연쇄 보장:
  - READ 예약 시 DOUT이 sequence 로직으로 이어서 예약되는지 확인(훅 감소가 연쇄를 방해하지 않음).
- 회귀(결정성/요약지표):
  - 동일 시드, 동일 config에서 실행 두 번 결과가 동일.
  - `phase_proposal_counts_*.csv`에서 SR 제안 건수가 유의미하게 감소.

## 대안 비교(결정 전에 ≥2개)
- A. Scheduler에서 스킵(제안안): 단순/국소적, PRD 규칙 직관적 반영. 위험 낮음.
- B. op_state_probs.yaml에서 SR 확률 축소: 환경 종속/취약, 유지보수 어려움. 규칙적 제어 아님.
- C. Proposer에서 SR rate‑limit: 복잡/광범위 변경, 교차 모듈 영향 큼.
→ 가장 단순하고 안전한 A를 채택.

## 영향도/리스크
- 영향 모듈: scheduler.py만 실질 변경. proposer/resourcemgr 비침습.
- 리스크: 훅 감소로 일부 분포 키(예: *.DATA_OUT)가 덜 사용될 수 있음. 연쇄(sequence) 경유로 읽기 흐름은 유지되므로 기능 리스크는 낮음.

## 완료 기준(Definition of Done)
- 코드 반영 후 단위 테스트 통과.
- SR/DATA_OUT/DATA_IN 유발 PHASE_HOOK 수치가 이전 대비 현저히 감소(샘플 런 기준).
- 기존 출력 CSV 스키마 불변, 결정성 유지.

## 파일 참조
- docs/PRD_v2.md:253 — Scheduler/event_hook 섹션 시작
- docs/PRD_v2.md:275 — 주의: ISSUE/DATA_IN/DATA_OUT 훅 미생성
- scheduler.py:348 — `_emit_op_events`(현행 PHASE_HOOK 생성 지점)
- config.yaml:447 — `SR` base 정의(affect_state=false)

