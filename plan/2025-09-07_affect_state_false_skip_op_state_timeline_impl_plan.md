title: 계획 — affect_state=false 인 operation의 op_state timeline 미등록
date: 2025-09-07
owner: Codex CLI
status: proposed
related:
  - research/2025-09-07_21-21-37_affect_state_false_skip_op_state_timeline.md
  - docs/PRD_v2.md
  - resourcemgr.py
  - scheduler.py
---

## Problem 1‑Pager

- 배경: PRD v2에 따라 affect_state=false 인 operation 은 분석/스케줄 전 구간에서 "op_state timeline"에 등록되지 않아야 한다.
- 문제: 현재 `ResourceManager.commit()`는 `txn.st_ops`의 모든 항목을 `_StateTimeline`에 추가하여, `DOUT/GETFEATURE/SETFEATURE` 등 비상태 operation 도 `BASE.STATE` 세그먼트를 갖게 된다.
- 목표: affect_state=false 인 base 에 대해서는 타임라인 세그먼트 등록만 건너뛰되, ODT/CACHE/SUSPEND 등의 런타임 상태 갱신은 그대로 유지한다.
- 비목표: 스케줄러 PHASE_HOOK 정책 변경(이미 적용됨), proposer의 phase key 파생 로직 변경(연구상 무관), cfg 스키마 변경.
- 제약: 함수 ≤ 50 LOC, 파일 ≤ 300 LOC, 결정성 유지, 민감정보 로깅 금지. 기존 테스트/출력 스키마 불변.

## 영향도 및 호출 경로

- 타임라인 등록: `resourcemgr.py:497` — `ResourceManager.commit()` 내 `self._st.reserve_op(die, plane, base, st_list, start)` 호출로 세그먼트 추가
- 동일 루프 내 상태 갱신: ODT/CACHE/SUSPEND 토글/엔드 로직이 함께 수행됨(`resourcemgr.py:501` 이후)
- 스케줄러 훅 정책: `scheduler.py:458` — affect_state=false 시 PHASE_HOOK 미생성(이미 구현됨)
- PRD 근거: `docs/PRD_v2.md:355` — affect_state=false → op_state_timeline 미등록, `docs/PRD_v2.md:278` — PHASE_HOOK 미생성

## 대안 비교(선택 사유 포함)

1) RM 커밋에서 `reserve_op`만 affect_state로 게이트(선택)
   - 장점: 변경 최소, 일관 적용, 회귀 위험 낮음
   - 단점: `txn.st_ops`는 유지되어 루프 오버헤드 소폭 잔존
   - 위험: 낮음 — 타임라인 축소 외 의미 변화 없음

2) `reserve()` 단계에서 비상태 base 자체를 `txn.st_ops`에 넣지 않음
   - 장점: 루프·메모리 경감
   - 단점: 같은 루프에서 처리하는 ODT/CACHE/SUSPEND 갱신이 누락될 수 있어 분기 추가 필요
   - 위험: 상태 갱신 누락·불일치 위험

3) 익스포트/시각화 단계에서만 필터링
   - 장점: 국소 변경
   - 단점: 런타임 질의(`rm.op_state`)와 분석 결과 불일치
   - 위험: 제안/검증 로직 혼선

→ 선택: 1) RM 커밋에서 `reserve_op` 게이트.

## 설계 및 변경 요약

- `ResourceManager.commit()`에서 각 `(die, plane, base, st_list, start)` 처리 시, `affect_state(base) == True` 인 경우에만 `_StateTimeline.reserve_op(...)` 호출.
- `affect_state(base)` 조회는 `self.cfg['op_bases'][base]['affect_state']`를 안전하게 탐색하며 기본값은 `True`.
- ODT/CACHE/SUSPEND 업데이트와 기타 메타 갱신은 기존 로직 그대로 유지.

## 구현 계획(작업 단위)

1) affect_state 조회 헬퍼 추가
   - 파일: `resourcemgr.py`
   - 내용: `ResourceManager` 내부에 프라이빗 메서드 `_affects_state(self, base: str) -> bool` 추가. `try/except`로 안전 조회, 기본 `True` 반환.

2) 커밋 루프 게이트 적용
   - 파일: `resourcemgr.py:483`~`resourcemgr.py:520`
   - 변경: `for (die, plane, base, st_list, start) in txn.st_ops:` 내에서 `if self._affects_state(base): self._st.reserve_op(...)`로 조건부 호출.
   - 비고: 이후 ODT/CACHE/SUSPEND 로직은 베이스 대문자 문자열(`b = str(base).upper()`)로 평가되므로 영향 없음.

3) 회귀 테스트 추가 — 타임라인 미등록
   - 파일: `tests/test_resourcemgr_states.py` (또는 신규 파일 `tests/test_resourcemgr_affect_state_false_timeline.py`)
   - 케이스 A: `affect_state=false` 베이스(DOUT 등) 커밋 시, 해당 시간대 `rm.op_state(die, plane, t)`가 `None` 이어야 함.
   - 케이스 B: `affect_state=true` 베이스(READ/PROGRAM 등)는 기존대로 `"BASE.STATE"`가 반환됨을 확인.
   - 케이스 C: ODT/CACHE/SUSPEND 토글/엔드가 그대로 동작(이미 존재하는 테스트 보강 불필요하나, 연계 확인 1케이스 추가 권장).

4) 문서 확인/주석 보강
   - 파일: `docs/PRD_v2.md`
   - 내용: §5.3/§5.4 주변에 구현 포인터(파일/라인) 간단 표기 또는 주석 정리. PRD 문장 자체는 이미 충족.

5) 출력 영향 스폿 체크
   - 도구: `viz_required_outputs.py`, `gantt_bokeh.py`
   - 내용: 일부 비상태 op의 `op_state` 레이블이 사라져도 시각화가 `op_name`로 폴백하는지 확인(`gantt_bokeh.py:61`, `gantt_bokeh.py:116`). 스키마 변경은 없음.

## 테스트 전략

- 단위 테스트(결정성):
  - 설정: 최소 cfg에 `op_bases[DOUT].affect_state=false` 주입(예: 기본 `config.yaml:432` 참조하되 테스트용 미니멀 cfg 사용).
  - 실행: `reserve/commit` 후 `rm.op_state(...)`로 검증.
- 기존 회귀: `tests/test_resourcemgr_states.py` 내 ODT/CACHE/SUSPEND, 스냅샷 복원 테스트가 통과해야 함.

## 수용 기준(AC)

- AC1: affect_state=false 베이스는 어떤 시각에서도 `_StateTimeline` 세그먼트를 남기지 않는다(`rm.op_state(...) is None`).
- AC2: affect_state=true 베이스는 기존과 동일하게 `BASE.STATE`가 조회된다.
- AC3: ODT/CACHE/SUSPEND의 런타임 상태 API(`odt_state`, `cache_state`, `suspend_states`)의 동작이 회귀 없이 유지된다.
- AC4: 시각화·CSV 스키마 변화 없음(라벨 폴백 정상).

## 리스크와 완화

- 분석 타임라인 축소: 기대된 변화이며 PRD에 부합. END/DEFAULT 기반 분포 사용 경로는 `phase_key_at`로 보완됨.
- 설정 누락 시 오탐: 기본값을 `True`로 두어 보수적으로 동작. cfg 자동채움/검증은 범위 외.

## 롤백 계획

- `resourcemgr.py`의 조건부 호출을 제거(원복)하면 즉시 이전 동작 복구. 테스트는 케이스 A를 스킵 처리하여 임시 무력화 가능.

## 참고 코드 포인터

- `resourcemgr.py:497` — `_StateTimeline.reserve_op(...)` 호출 위치
- `resourcemgr.py:551` — `ResourceManager.op_state(...)` 질의 API
- `scheduler.py:458` — affect_state=false 시 PHASE_HOOK 미생성 게이트
- `docs/PRD_v2.md:355` — 타임라인 미등록 규정

