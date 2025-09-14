---
date: 2025-09-14
author: codex
status: draft
topic: "Option C — operation_timeline을 state timeline 기반의 'effective' 뷰로 내보내기 (feature flag)"
refs:
  - docs/PRD_v2.md
  - research/2025-09-14_15-11-24_suspend_resume_flow.md
  - plan/2025-09-14_suspend_resume_flow_impl_plan.md
  - main.py: export_operation_timeline
  - resourcemgr.py: snapshot().timeline
  - scheduler.py: InstrumentedScheduler._rows
---

# Problem 1-Pager
- 배경: 현재 `operation_timeline_*.csv`는 "예약(window) 관점"으로, `Scheduler`의 예약 시각/지속을 그대로 기록한다. SUSPEND/RESUME가 발생해도 기존 ERASE/PROGRAM 예약 레코드는 소급 수정되지 않으며, 잔여 구간은 별도의 ERASE 레코드로 이어진다. 반면 `op_state_timeline_*.csv`는 실제 state 기준으로 SUSPEND 시점에서 정확히 절단된다.
- 문제: 분석/시각화에서 "효과적/실제 동작" 경계를 보고 싶은 경우, `operation_timeline_*.csv`만 보면 중단 지점에서 잘라지지 않아 혼동을 일으킨다.
- 목표: Feature flag를 통해 `operation_timeline_*.csv`를 "effective(상태 기준)" 뷰로도 출력할 수 있게 한다. 즉, ERASE/PROGRAM 계열은 SUSPEND 경계에서 잘리고 RESUME 이후는 별도 레코드로 이어지게 한다.
- 비목표: 스케줄러/예약 로직 변경, 기존 CSV의 필드 포맷 변경, 기본 동작(default=false) 변경.
- 제약: 코드 변경 ≤ 300 LOC, 함수 ≤ 50 LOC, 명시적/간결한 구현. 기존 export와의 호환을 유지(플래그 off 시 기존과 동일).

# 설계 (Option C)
1) Feature flag 추가
   - `config.yaml` → `features.operation_timeline_effective: false` (기본값)
   - true일 때만 효과적 뷰를 사용하여 `operation_timeline_*.csv`를 생성

2) Export 경로 분기 (main.py: export_operation_timeline)
   - 기존: `InstrumentedScheduler._rows`를 그대로 CSV로 변환
   - 신규(effective=true): ERASE/PROGRAM 계열에 한해 state timeline(RM.snapshot().timeline) 기반으로 시계열을 재구성
     - READ/DOUT/기타는 기존 로우 그대로 유지(버스/단위 이벤트 중심이라 효과적 커팅의 의미가 희박)

3) 효과적 시계열 구축 알고리즘(요지)
   - 입력:
     - `rows`: `InstrumentedScheduler._rows` (op_name, op_base, target, start_us, end_us, op_uid 등)
     - `tl`: `rm.snapshot().timeline` ([die,plane,op_base,state,start,end])
   - 기준: ERASE/PROGRAM family만 효과적 커팅 적용
     - ERASE family 판정: `base == 'ERASE'`
     - PROGRAM family 판정: `('PROGRAM' in base) and ('SUSPEND' not in base) and ('RESUME' not in base) and ('CACHE' not in base)`
   - 절차:
     1) rows를 (die, plane, op_uid) 단위로 그룹화
     2) 각 그룹에 대해, 해당 (die, plane)에서 timeline의 `[base, 'CORE_BUSY']` 세그먼트를 해당 row의 [start_us, end_us] 범위로 자르고(intersect) 연속 구간으로 머지
     3) intersect 결과가 비어 있으면(예: 전부 suspend로 잘림) → 해당 row는 드랍
     4) intersect 결과가 1개면 → row의 [start,end]를 그 구간으로 치환
     5) intersect 결과가 N>1개면 → 첫 구간으로 row를 치환하고, 나머지 (N-1) 구간은 동일 `op_name/op_base/targets`를 사용해 추가 행을 생성(op_uid는 exporter에서 새로 배정)
   - 세부 규칙:
     - `*_RESUME` 행은 그대로 유지(마커). 단, op_state_timeline과의 일관성을 위해 ISSUE는 이미 제외됨.
     - ERASE/PROGRAM의 ISSUE/DATA_IN/OUT은 효과적 창 내에서만 보존하되, 기본 구현에서는 CORE_BUSY 구간 기준으로 전체 [start,end]를 잡는다(단순화). 필요 시 후속 확장.
   - 성능: 한 run 내 (die,plane)별 이진 탐색+선형 병합. 규모가 크지 않아 충분히 경량.

4) UID/정렬/출력 규칙
   - 기존 `InstrumentedScheduler._rows`의 uid 체계는 예약 시점 기준. 효과적 뷰에서 분할로우는 새로운 uid가 필요.
   - Exporter에서 로컬 카운터(초기값: max(uid)+1)를 사용해 추가 행에 uid 부여.
   - 정렬: 기존 정렬(최소 start_us → uid) 유지.

5) 구현 상세(대략 120~180 LOC 목표)
   - main.py
     - `def _is_program_family(base: str) -> bool`
     - `def _effective_windows_for(row, tl) -> List[Tuple[start,end]]`: (die,plane,base, row.[start,end]) 교집합 + 연속 구간 머지
     - `def _build_effective_rows(rows, rm, cfg) -> List[Dict]]`: rows 순회, ERASE/PROGRAM만 치환/분할
     - `export_operation_timeline(...)`에서 feature flag 확인 후 분기

# 테스트 계획
1) 단위 시나리오: ERASE → SUSPEND(중간) → RESUME → ERASE 잔여
   - op_state_timeline: ERASE.CORE_BUSY가 suspend에서 절단됨 확인
   - operation_timeline (effective=false): 원래 ERASE row 그대로 + 잔여 ERASE 새 row
   - operation_timeline (effective=true): 첫 ERASE row가 suspend 직전으로 단축, 이어서 잔여 ERASE row가 RESUME 직후로 시작(동일 타겟/이름)
   - ERASE_RESUME.* 는 그대로 출력(ISSUE 제외), ERASE_SUSPEND 는 그대로 유지

2) PROGRAM 계열도 동일 시나리오로 회귀 테스트

3) 경계/빈 케이스
   - intersect 결과가 empty → 해당 ERASE/PROGRAM row 제거
   - intersect 결과가 다수 → 추가 uid 생성 및 정렬/출력 확인

# 롤아웃/리스크
- 기본값 off로 머지 → 기존 소비자는 영향 없음
- on 시 exporter에서만 적용되며 내부 스케줄러/타임라인/메트릭에는 영향 없음
- 리스크: 분할 행의 uid가 runtime uid와 다를 수 있음(문서화). 필요 시 별도 `effective_uid` 컬럼 추가(후속 옵션)

# 작업 단위
1) cfg: `features.operation_timeline_effective` 추가(default=false)
2) main.py: effective builder 헬퍼 3개 추가 + export 분기
3) 테스트: 단일 run 재현 스크립트/노트 + CSV diff 확인
4) 문서: PRD 내 exporter 섹션에 Option C 설명/플래그 명시

# 완료 기준(DoD)
- flag off: 기존 CSV 완전 동일
- flag on: ERASE/PROGRAM에서 suspend/resume 경계 반영된 effective operation timeline 생성
- 회귀 검증 통과 및 샘플 run에서 시각적으로 절단 반영 확인

