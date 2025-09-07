---
date: 2025-09-07
owner: Codex
topic: QUEUE_REFILL phase_key past‑key fix (REFILL hook label + boundary preference)
status: planned
links:
  - research: research/2025-09-07_21-57-06_queue_refill_phase_key_past_key.md
---

# Problem 1-Pager

- 배경: 경계 시각(now가 직전 세그먼트 종료 지점)에 `ResourceManager.op_state(d,p,now)`가 None을 반환하면, proposer가 훅 `label(BASE.STATE)`을 그대로 신뢰하여 과거 상태 키를 유지한다. REFILL 훅은 기본적으로 `label="DEFAULT"`라 컨텍스트가 빈약해 now 기준 키 선정이 더 불안정하다.
- 문제: QUEUE_REFILL 제안 시 과거 phase_key가 사용되거나(`DEFAULT`/부정확), PHASE_HOOK 경계에서는 과거 라벨을 우선해 now 기준 `.END` 키 일관성이 깨진다.
- 목표: 제안(now) 기준으로 일관된 phase_key를 사용한다. 구체적으로 REFILL 경로는 now 기준 가상 키를 훅 라벨에 주입하고, 경계 상황에서도 `.END`를 선호하도록 옵션을 제공한다.
- 비목표: 이벤트 큐 payload 형식 변경(QUEUE_REFILL payload는 여전히 `{}`), PHASE_HOOK 생성 정책 변경, 기존 phase 분포 스키마 변경.
- 제약: 결정성 유지, 변경 범위 최소화(스케줄러/프로포저 국한), 함수 ≤ 50 LOC, 복잡도 증가 최소화, 기존 테스트/PRD와 호환(필요 시 문서 주석 추가).

## 대안 평가(장점/단점/위험)

- A. proposer 폴백 강화 유지(현행) — REFILL에서 `DEFAULT`이면 RM 가상 키 사용
  - 장점: 코드 존재, 플래그 가드(`features.phase_key_rm_fallback`), 위험 낮음
  - 단점: `label`이 유효(BASE.STATE)인 PHASE_HOOK 경계에서는 과거 라벨을 여전히 우선
  - 위험: 낮음
- B. REFILL 훅 라벨에 now 기준 가상 키 주입(권장)
  - 장점: proposer 변경 없이 REFILL 경로 즉시 개선, 결정성 유지
  - 단점: PRD의 “QUEUE_REFILL 데이터: None”과 약간 괴리(내부 훅 라벨에 한함)
  - 위험: 낮음(외부 출력/큐 payload 불변)
- C. 경계 시 라벨보다 RM 우선(선택적 플래그)
  - 장점: PHASE_HOOK post_t 경계에서도 `.END` 일관성 확보
  - 단점: 라벨 신뢰도 저하 가능성; 호환성 영향 우려
  - 위험: 중간(기능 플래그로 가드 권장)

## 구현 요약

- scheduler.py
  - `_next_refill_hook()`에서 현재 RR 커서(die, plane)와 `now_us`를 사용해 `rm.phase_key_at(d,p,now, default="DEFAULT", derive_end=True, prefer_end_on_boundary=True, exclude_issue=True)`를 계산하고 `label`에 주입한다.
  - 라운드로빈 순서는 유지(plane-major → wrap 시 die 증가). QUEUE_REFILL 이벤트 payload는 계속 `{}`로 유지.

- proposer.py (옵션 C)
  - `_phase_key(...)`에서 `op_state(...) is None`이고 `hook.label`이 `BASE.STATE`인 경우, `features.phase_key_prefer_rm_at_boundary=true`일 때 `rm.phase_key_at(...)`가 `.END`를 반환하면 이를 우선하도록 분기 추가.

- docs/PRD_v2.md
  - 5.3 Scheduler/QUEUE_REFILL 섹션에 “런타임 내부적으로 proposer 호출 시 REFILL 훅 라벨에 now 기준 phase_key를 주입한다(이벤트 큐 payload에는 영향 없음)” 주석 추가.

## 변경 상세(파일/심볼)

- scheduler.py:180 `_next_refill_hook()`
  - before: `{"label": "DEFAULT", "die": rr_die, "plane": rr_plane}` 반환
  - after: `key = rm.phase_key_at(rr_die, rr_plane, self.now_us, default="DEFAULT", derive_end=True, prefer_end_on_boundary=True, exclude_issue=True)` 계산 후 `{"label": key, "die": rr_die, "plane": rr_plane}` 반환

- proposer.py:823 `_phase_key(...)` (옵션 C)
  - 플래그 `features.phase_key_prefer_rm_at_boundary` 신설(default=false). 경계에서 라벨(BASE.STATE)보다 `phase_key_at(...).END`를 우선할지 결정.

- tests
  - `tests/test_scheduler_queue_refill_phase_key.py` 신설
    - 시나리오 1(REFILL outside): 이전 `ERASE`가 t<now에 종료된 상태에서 now에 REFILL 실행 → proposer metrics `phase_key == "ERASE.END"` 확인(라벨 주입 효과).
    - 시나리오 2(PHASE_HOOK boundary, 옵션 C): 이전 `ERASE`가 t에 종료, 다음 `READ`가 t에 시작. 플래그 on → proposer metrics `phase_key == "ERASE.END"`; 플래그 off → `READ.<STATE>` 유지.

## 테스트 전략

- 단위: proposer `_phase_key` 경계/세그먼트 외/ISSUE 제외 케이스 검증(현행 `tests/test_proposer_phase_key.py` 보강 또는 신규).
- 통합: Scheduler 한 틱에서 QUEUE_REFILL 처리 후 `metrics.last_reserved_records[0].phase_key`가 now 기준 가상 키와 일치하는지 확인.
- 회귀: `export_operation_timeline_*`에서 `phase_key_used`(instant 베이스 보정 경로)와 `phase_key_virtual` 간 괴리 감소 확인(샘플 기반 assert).

## 롤아웃/가드

- 기본값: `features.phase_key_rm_fallback=true`(존치), `features.phase_key_prefer_rm_at_boundary=false`(신설, 기본 off).
- 점진 배포: 옵션 C는 실험 플래그로 시작해 모니터링 후 기본 전환 고려.

## 영향도/리스크

- 영향도: proposer 분포 선택이 now 기준으로 안정화되어 경계에서 `.END` 키 채택 증가. RR 라벨 주입으로 REFILL 제안 품질 상승.
- 리스크: PRD 문구와의 사소한 괴리(문서 주석으로 해소). 경계 우선 플래그 on 시 과거 라벨 기반 로직에 의존한 테스트가 있다면 수정 필요.

## 완료 정의(DoD)

- REFILL 라벨 주입 코드 및(선택) 경계 우선 분기 추가.
- 신규/보강 테스트 통과, 기존 테스트 비회귀.
- PRD 문서 주석 반영.
- `out/operation_timeline_*.csv`에서 경계 근방 `phase_key_used`와 `phase_key_virtual` 불일치 빈도 감소를 수치로 확인(개발자 노트).

