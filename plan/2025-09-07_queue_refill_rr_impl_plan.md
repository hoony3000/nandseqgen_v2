---
date: 2025-09-07
owner: Codex
topic: QUEUE_REFILL hook die/plane round-robin
status: implemented
links:
  - research: research/2025-09-07_18-56-10_queue_refill_rr_die_plane.md
---

# Problem 1-Pager

- 배경: Scheduler는 QUEUE_REFILL 처리 시 proposer를 `hook={"label":"DEFAULT"}`로 호출하여, proposer가 `die/plane` 기본값(0,0)을 사용함. 이로 인해 phase_key/타겟이 특정 die/plane에 고정되는 편향이 발생.
- 문제: QUEUE_REFILL 훅이 (die, plane)을 번갈아 순회하지 않아, 초기 구동·빈 구간 보충 시 공정성이 떨어짐.
- 목표: Scheduler가 QUEUE_REFILL 처리 시 `(die, plane)`을 라운드로빈으로 주입하여 proposer를 호출. 결정성 유지.
- 비목표: 이벤트 payload 형식을 바꾸지 않음(QUEUE_REFILL 이벤트 자체 payload는 여전히 빈 dict). PHASE_HOOK 경로 변경 없음.
- 제약: 기존 테스트/PRD 호환. 변경 최소화(스케줄러 한정), 함수 ≤50 LOC, 복잡도 증가 최소화.

## 대안 평가

- A. Scheduler 내부 RR 주입(선택)
  - 장점: 변경 범위 최소, 결정성/재현성 유지, 공정한 커버리지.
  - 단점: PRD의 "QUEUE_REFILL 데이터: None" 설명과 약간 상이(런타임 확장 수준).
  - 위험: 매우 낮음.
- B. 이벤트 payload에 내장
  - 장점: 큐 덤프만으로 다음 훅 대상 파악 용이.
  - 단점: push 지점 분산(생성자/주기 재등록), 코드 중복.
- C. 무작위 선택
  - 장점: 구현 단순.
  - 단점: 공정성·요구사항 불일치.

## 구현 요약

- scheduler.py
  - 필드: `_rr_die`, `_rr_plane` 추가.
  - 헬퍼: `_topology() -> tuple[int,int]`, `_next_refill_hook() -> dict` 추가.
  - 사용: QUEUE_REFILL 처리에서 `_propose_and_schedule(..., self._next_refill_hook())` 호출.
  - 순서: plane-major → wrap 시 die 증가.

- 테스트: `tests/test_scheduler_queue_refill_rr.py`
  - 2-die 토폴로지, READ-only 분포에서 두 틱 연속 실행 후, 큐의 READ OP_START payload를 검사해 서로 다른 die를 확인.

## 영향도

- proposer/addrman/RM 변경 없음. 결정적 실행 유지. 기존 테스트와 호환.

