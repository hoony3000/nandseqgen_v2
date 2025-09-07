title: 계획 — READ not scheduled after ERASE→PROGRAM despite proposer ok
date: 2025-09-07
owner: Codex CLI
status: proposed
related:
  - research/2025-09-07_07-01-11_read_not_scheduled_after_erase_program.md
  - docs/PRD_v2.md
---

## Problem 1‑Pager

- 배경: ERASE→PROGRAM 이후 READ가 proposer 단계에서 반복적으로 ok/selected 되지만, 실제 스케줄 타임라인에는 나타나지 않음.
- 문제: Scheduler가 배치 내 모든 연쇄 op에 대해 admission window를 강제해, READ 다음 두 번째 연쇄(DOUT/CACHE_READ.SEQ)가 창(Window)을 넘어가면 배치 전체를 롤백함. Proposer는 "첫 op만 창 내 보장" 가정을 두고 있음.
- 목표: Scheduler의 admission window 적용 범위를 "배치의 첫 op"로 제한하여 proposer와 정합성을 맞추고, READ→DOUT 등 짧은 후속 연쇄가 창 밖이어도 배치 커밋이 가능하도록 한다.
- 비목표: Proposer의 시퀀스 확장 로직/분포/후보 샘플링 구조 변경, RM의 타이밍/자원 모델 변경, 외부 I/O 추가.
- 제약: 함수 ≤ 50 LOC, 파일 ≤ 300 LOC, 순환복잡도 ≤ 10. 결정성 유지(시드 RNG, 시스템 시계 미사용). 민감정보 로깅 금지.

## 영향도 및 호출 경로

- 스케줄 루프: `scheduler.py:114` `tick()` → `PHASE_HOOK`/`QUEUE_REFILL` 시 `_propose_and_schedule` 호출
- admission window 재검증: `scheduler.py:279` 현재 배치의 모든 `p in batch.ops`에 대해 `p.start_us >= (now + W)` 검사
- proposer 의도: `proposer.py:1147` "Whole-batch return (first op inside admission window already enforced)"
- 정책/설정: `config.yaml:19` `policies.admission_window`, `config.yaml:26` `policies.sequence_gap`(기본 1.0us)
- READ 연쇄 정의: `config.yaml:200`~ READ base에서 `DOUT`/`CACHE_READ.SEQ` 확률적 연결

## 대안 비교(선택 사유 포함)

1) Scheduler에서 첫 op만 창 검사(선택)
   - 장점: 의도 일치, 변경 최소, 회귀 위험 낮음
   - 단점: 후속 짧은 연쇄가 창 밖으로 나갈 수 있음(정책상 허용)
   - 위험: 창 집중도 저하 우려 → 실제 후속은 짧고 RM 타이밍 제약 존재로 영향 제한적

2) 후속 연쇄(DOUT 등)를 `instant_resv`로 마킹하여 창 검사 우회
   - 장점: 정책적으로 명시적
   - 단점: 여러 op_bases 변경 필요, 설정 의존성↑, 유지보수성 저하
   - 위험: 잘못된 instant 지정 시 자원 경합 규칙 우회 위험

3) `admission_window` 확장(예: 1.5us)
   - 장점: 코드 변경 없음, 즉시 효과
   - 단점: near‑term 집중도 저하, 부작용 큼, 근본 원인(범위 불일치) 해결 아님
   - 위험: 다른 워크로드에서 스케줄 품질 저하

→ 선택: 1) 첫 op 한정. 2) / 3)은 옵션으로 남김.

## 설계 및 변경 요약

- 핵심 아이디어: `_propose_and_schedule`에서 `for p in batch.ops` 루프를 `for idx, p in enumerate(batch.ops)`로 바꾸고, admission window 검사를 `idx == 0`인 경우에만 적용.
- 즉시 예약 예외: 기존 `instant_resv` 베이스는 그대로 창 검사 제외 유지.
- 메트릭: 기존 `window_attempts`/`window_exceeds` 의미는 "첫 op 기준"으로 해석. 필요한 경우 주석으로 명시.

## 구현 계획(작업 단위)

1) admission window 범위 수정
   - 파일: `scheduler.py:276`
   - 변경: 
     - 현재: 모든 `p`에 대해 `if (not instant) and W > 0 and p.start_us >= (now + W): ... break`
     - 수정: `for idx, p in enumerate(batch.ops):` 후 `if idx == 0 and (not instant) and W > 0 and p.start_us >= (now + W): ...`
   - 주석 추가: "window is enforced only for the first op; proposer already guarantees it"

2) 회귀 테스트 추가 — "첫 op만 창 검사"
   - 파일: `tests/test_scheduler.py`
   - 케이스: READ→DOUT 2‑스텝 배치, `admission_window < sequence_gap` 환경에서
     - 기대: 첫 READ가 창 내면 배치 커밋 성공, `window_exceeds` 비증가
   - 보조 케이스: 첫 op가 창 밖인 경우 롤백(기존 테스트 보강/참고)

3) 문서 업데이트(간단 주석)
   - 파일: `docs/PRD_v2.md`
   - 내용: "Admission window는 proposer 보장에 따라 ‘배치의 첫 op’에만 적용" 한 줄 명시

## 테스트 전략

- 단위 회귀
  - 설정: `policies.admission_window=0.5`, `policies.sequence_gap=1.0`, READ 후보만 강제
  - RM/AM: 기본 토폴로지, 빈 리소스
  - 실행: `Scheduler.tick()` 1회
  - 검증: `committed ≥ 1`, `last_commit_bases`에 READ/DOUT 포함, `window_exceeds` 변화 없음

- 기존 테스트 영향
  - `tests/test_scheduler_respects_admission_window_when_busy`: 첫 op가 창 밖이면 여전히 롤백되어야 함(불변)

## 수용 기준(AC)

- AC1: 첫 op가 창 내인 READ→DOUT 배치가 롤백 없이 커밋된다.
- AC2: 첫 op가 창 밖이면 배치는 롤백되고 `window_exceeds`가 증가한다(현행과 동일).
- AC3: 기존 스케줄러/프로포저 결정성 테스트가 통과한다.
- AC4: CSV/로그 스키마 변경 없음; `metrics.window_*`의 의미는 첫 op 기준으로 일관.

## 리스크와 완화

- 후속 연쇄가 창 밖: 실제 후속은 짧고 RM 타이밍 제약(점유/락)으로 폭주 방지. 필요 시 2) 대안(instant_resv)로 보완 가능.
- 정책 혼동: PRD와 소스 주석에 범위를 명시해 팀 합의를 고정.

## 롤백 계획

- `scheduler.py`의 변경 블록을 되돌리면 즉시 복구. 테스트 케이스는 조건부 스킵 표시로 임시 비활성화 가능.

## 참고 코드 포인터

- `scheduler.py:279` — 현재 창 검사 위치(모든 p 대상)
- `proposer.py:1147` — "first op inside admission window already enforced" 주석
- `config.yaml:19` — `policies.admission_window`
- `config.yaml:26` — `policies.sequence_gap`

