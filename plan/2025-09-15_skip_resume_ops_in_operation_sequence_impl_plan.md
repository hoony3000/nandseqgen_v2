---
title: Export filter — Skip RESUME‑chained ops in operation_sequence
date: 2025-09-15
author: codex
status: draft
source: research/2025-09-15_00-04-28_skip_resume_ops_in_operation_sequence.md
refs:
  - docs/PRD_v2.md:366
  - main.py:103
  - main.py:539
  - scheduler.py: chain stub creation
  - resourcemgr.py: suspended_ops_erase/suspended_ops_program
---

# Problem 1‑Pager

- 배경: `export_operation_sequence`는 스케줄된 모든 `op_uid` 그룹을 CSV로 내보낸다. SUSPEND 이후 `*_RESUME` 직후 체인(stub)으로 재개되는 ERASE/PROGRAM 잔여 작업도 동일하게 포함되어 PRD와 상충한다.
- 문제: PRD 규정(문서 366행) — “RESUME 으로 다시 추가된 operation 은 `operation_sequence_*.csv` 에 추가하지 않는다.” 현재 구현은 이를 분리 식별/제외하지 못한다.
- 목표: RESUME 직후 체인으로 재개된 ERASE/PROGRAM 잔여 작업을 `operation_sequence_*.csv`에서 제외한다. 기본값 on의 feature flag로 제어하며, 타 CSV(op_state/operation_timeline 등)는 변화시키지 않는다.
- 비목표: 스케줄러의 예약/체인 로직 변경, RM 내부 상태 모델 변경, 휴리스틱 기반 판별 도입, 기존 CSV 스키마 변경.
- 제약: 변경 ≤ 200 LOC, 함수 ≤ 50 LOC, 명시적/결정적 태깅과 필터링. 기존 동작과의 호환(플래그 off 시 완전 동일).

# 대안 비교

1) Source 태깅 + Export 필터(권장)
- 변경: 체인 경로 레코드에 `source='RESUME_CHAIN'` 태그 → InstrumentedScheduler row로 전파 → exporter에서 flag 기반 필터.
- 장점: 명시적·결정적, 변경 범위 작음, 유지보수 용이.
- 단점: 경로 간 태깅·전파 코드 추가 필요.
- 위험: 낮음(기존 소비자는 `source` 미사용).

2) 휴리스틱 필터(RESUME 직후 동일 die/시각 인접성)
- 장점: 태깅 없이 구현 가능.
- 단점: 오탐/누락 위험, 정렬·정밀도 의존, 리팩터링 취약.
- 위험: 중간 수준.

3) RM 기반 판정 API 추가
- 장점: 명시적·일관성.
- 단점: 내부 상태 확장 필요, 변경 범위 큼.
- 위험: 회귀 리스크 증가.

선택: 1) Source 태깅 + Export 필터.

# 변경 범위(High‑Level)

- `scheduler.py`
  - 체인 stub 생성부에서 `rec2['source'] = 'RESUME_CHAIN'` 추가.
  - 대상: `_chain_enabled(...)` 분기 내 post‑commit stub 예약 경로.

- `main.py`
  - `InstrumentedScheduler._emit_op_events`: row 생성 시 `source=rec.get('source')` 보존(문자열 'None' → None 정규화).
  - `export_operation_sequence(...)`: `cfg.pattern_export.skip_resume_chained_ops`가 true면 `row.source == 'RESUME_CHAIN'` 행(및 해당 uid 그룹)을 제외. 구현은 사전 row 필터로 단순화.

- `config.yaml`
  - 새 플래그 추가: `pattern_export.skip_resume_chained_ops: true` (기본 on; 미지정 시 True로 처리).

- 문서
  - `docs/PRD_v2.md` Exporter 섹션에 플래그 명시(스펙 유지, 구현 디테일 추가 설명).

# 구현 단계(Incremental)

1. scheduler 체인 stub 태깅
- 위치: `scheduler.py` 체인 예약 후 `rec2 = {..., '_chain_stub': True}` 구성 직후.
- 변경: `rec2['source'] = 'RESUME_CHAIN'` 한 줄 추가.

2. InstrumentedScheduler에 source 전파
- 위치: `main.py: InstrumentedScheduler._emit_op_events`.
- 변경: `_OpRow` 생성 시 `source=(None if rec.get('source') in (None, 'None') else str(rec.get('source')))`.

3. exporter에서 필터 적용
- 위치: `main.py: export_operation_sequence` 진입 초반.
- 변경: `rows0 = rows; flag = bool(((cfg.get('pattern_export', {}) or {}).get('skip_resume_chained_ops', True))); rows = [r for r in rows0 if (not flag) or (str(r.get('source')) != 'RESUME_CHAIN')]`.
- 주의: 그룹화 전에 필터하여 해당 uid 전체를 자연스럽게 제외.

4. 플래그 정의 추가(선택적 주석 포함)
- 위치: `config.yaml` 하단 `pattern_export` 섹션(존재 시 확장, 없으면 새 섹션).
- 추가: `skip_resume_chained_ops: true` 및 주석(“RESUME 체인 잔여 작업을 operation_sequence에서 제외”).

5. 문서 반영
- 위치: `docs/PRD_v2.md` Exporter/RESUME 규칙 근처(366행 인접).
- 내용: 구현 플래그/전파 필드(`source`) 간단 표기.

# 테스트 계획

1) 단일 축 ERASE 시나리오
- 순서: ERASE → (중간) ERASE_SUSPEND → ERASE_RESUME → ERASE 잔여 체인(commit됨)
- 기대: `operation_sequence_*.csv`에 ERASE(초반)만 존재, 잔여 ERASE(RESUME_CHAIN)는 제외.
- 부속: `operation_timeline_*`/`op_state_timeline_*`는 기존과 동일(진실 반영).

2) PROGRAM 축 시나리오
- 순서: PROGRAM_* → PROGRAM_SUSPEND → PROGRAM_RESUME → PROGRAM 잔여 체인
- 기대: sequence에서 잔여 체인 제외.

3) 플래그 off 회귀
- `pattern_export.skip_resume_chained_ops=false`로 실행 → 기존과 동일하게 잔여 체인도 포함.

4) 체인 비활성 경로(후속)
- `features.suspend_resume_chain_enabled=false`에서의 재개 경로 확인 후 동일 태깅 지점 적용(별 PR/작업).

# 완료 기준(DoD)

- flag on: RESUME 체인 잔여 작업이 `operation_sequence_*.csv`에서 제외됨(샘플 run에서 diff 확인).
- flag off: 기존 CSV와 완전 동일.
- 다른 CSV(op_state_timeline/operation_timeline) 및 메트릭 변화 없음.
- 코드 변경 범위 최소화, 가독성/명시성 확보, 주석/문서 반영.

# 파일 변경(예상)

- scheduler.py: 체인 stub 태깅(±1 LOC)
- main.py: `_emit_op_events` source 보존(±2 LOC), `export_operation_sequence` 필터(±5 LOC)
- config.yaml: `pattern_export.skip_resume_chained_ops: true` 추가(주석 포함)
- docs/PRD_v2.md: Exporter 섹션에 플래그 언급(선택)

# 참고

- 연구: `research/2025-09-15_00-04-28_skip_resume_ops_in_operation_sequence.md`
- 스펙: `docs/PRD_v2.md:366`
- 구현 포인트: `main.py:103`, `main.py:539`, `scheduler.py`(체인 stub), `resourcemgr.py`(suspended_ops_* 참조)

