---
date: 2025-09-15T00:04:28+09:00
researcher: codex
git_commit: f375d82
branch: main
repository: nandseqgen_v2
topic: "Exclude RESUME-resumed suspended_ops_* from operation_sequence CSV"
tags: [research, codebase, exporter, scheduler, suspended_ops]
status: complete
last_updated: 2025-09-15
last_updated_by: codex
---

# 연구: RESUME 재개된 suspended_ops_erase/program의 operation_sequence 비노출

Date: 2025-09-15T00:04:28+09:00
Researcher: codex
Git Commit: f375d82
Branch: main
Repository: nandseqgen_v2

## 연구 질문
RESUME 동작으로 재개된 `suspended_ops_erase`, `suspended_ops_program`는 `operation_sequence_*.csv`에 노출되지 않아야 한다. 이를 반영하기 위한 개선 방법은?

## 요약
- 현재 `export_operation_sequence`는 스케줄러가 기록한 모든 `op_uid` 그룹을 CSV로 내보낸다. RESUME 직후 체인(stub)으로 재개된 ERASE/PROGRAM 잔여 작업도 동일하게 포함되어 요구사항과 상충한다.
- 가장 단순·안전한 개선은 “체인으로 재개된 작업”에 명시적 태그(`source='RESUME_CHAIN'`)를 달아 Export 단계에서 필터링하는 방식이다.
- 대안으로는 시간/다이 상 인접성 휴리스틱으로 판정하는 방법이 있으나 오탐/누락 위험이 있다. ResourceManager에 판정 API를 추가하는 방법은 변경 범위가 넓다.
- 권장: 스케줄러 체인 경로에 `source` 태깅 + InstrumentedScheduler가 이를 row에 보존 + `export_operation_sequence`에서 feature‑flag로 필터.

## 상세 발견

### PRD 스펙 근거
- docs/PRD_v2.md:366 — “RESUME 으로 다시 추가된 operation 은 `operation_sequence_yymmdd_0000001.csv` 에 추가하지 않는다.”

### 현재 동작
- 체인 재개: `*_RESUME` 직후 잔여 CORE_BUSY를 즉시 이어서 예약하는 체인 로직 존재(기능 플래그)
  - scheduler.py:463 — `suspend_resume_chain_enabled` 활성 시 체인 준비
  - scheduler.py:573 — 체인 stub 레코드(`_chain_stub: True`)로 예약·커밋 및 이벤트 방출
- 체인 메타 출처
  - resourcemgr.py:999 — `suspended_ops_erase(die)`
  - resourcemgr.py:1019 — `suspended_ops_program(die)`
- 타임라인 기록
  - resourcemgr.py:633 — SUSPEND 시 대상 family의 CORE_BUSY 구간을 절단 후 축적
  - resourcemgr.py:677/682 — `ERASE_RESUME`/`PROGRAM_RESUME`에서 축 정리(ISSUE 제외 기록 등)
- Export (시퀀스)
  - main.py:539 — `export_operation_sequence(...)`는 모든 `op_uid` 그룹을 `operation_sequence_*.csv`로 출력. 체인 stub을 식별·제외하는 로직 없음
  - main.py:103 — `InstrumentedScheduler._emit_op_events`가 row를 수집하나 현재 `rec['source']`를 보존하지 않음

## 코드 참조
- `docs/PRD_v2.md:366` — RESUME 재개 작업 CSV 비노출 규칙
- `main.py:539` — `export_operation_sequence` 진입점
- `main.py:103` — `InstrumentedScheduler._emit_op_events` (source 누락)
- `scheduler.py:463` — RESUME 직후 체인 기능 활성 분기
- `scheduler.py:573` — 체인 stub 레코드 구성(`_chain_stub`)
- `resourcemgr.py:633` — SUSPEND 시 타임라인 절단 및 meta 이동
- `resourcemgr.py:999` — `suspended_ops_erase` 공개 API
- `resourcemgr.py:1019` — `suspended_ops_program` 공개 API

## 아키텍처 인사이트
- 체인 재개의 핵심 컨트랙트는 ResourceManager가 축적한 `suspended_ops_*` 메타를 읽어 스케줄러가 이어붙이는 것이다. 최종 CSV 제외 정책은 “데이터 생성 단계(스케줄러)”에서 출처를 태깅하고 “소비 단계(Exporter)”에서 정책적으로 필터하는 2계층 접근이 가장 견고하다.
- 현재 InstrumentedScheduler row에는 `_chain_stub`이 보존되지 않으므로 Exporter만으로 안전한 식별이 어렵다(휴리스틱 의존 위험).

## 개선안 비교

1) Source 태깅 + Export 필터(권장)
  - 변경: 
    - scheduler.py 체인 경로에서 `rec2['source'] = 'RESUME_CHAIN'` 설정
    - main.py:103 `InstrumentedScheduler._emit_op_events`에서 `source=rec.get('source')` 보존
    - main.py:539 `export_operation_sequence`에서 feature‑flag `pattern_export.skip_resume_chained_ops`가 true면 `row.source == 'RESUME_CHAIN'` 그룹 제거
  - 장점: 명시적·결정적 식별, 변경 범위 작음, 부작용 최소
  - 단점: 경로 간 `source` 전달 코드 추가 필요
  - 위험: 낮음(기존 소비자가 `source`를 사용하지 않아 호환성 영향 미미)

2) 휴리스틱 필터(RESUME 직후 동일 die, 동일 시각 시작)
  - 변경: Exporter에서 행 정렬 후, `*_RESUME`의 `end_us == ERASE/PROGRAM류 start_us`(동일 die) 패턴 탐지 시 제외
  - 장점: 태깅 없이 구현 가능
  - 단점: 오탐/누락 가능, 유지보수 취약, 타이밍 정밀도/정렬 의존
  - 위험: 중간 리팩터링 시 깨질 가능성 큼

3) RM 기반 판단 API 추가
  - 변경: ResourceManager가 “이 uid/행이 RESUME 체인에서 유래했는가?”를 답하는 API 제공 → Exporter에서 호출해 제외
  - 장점: 명시적·일관적
  - 단점: RM 내부 상태 추적 확장 필요(체인 시점의 매핑 저장 등), 변경 범위 큼
  - 위험: 코어 컴포넌트 변경에 따른 회귀 가능

## 권장 방안 (구체 단계)
- Feature flag: `config.yaml.pattern_export.skip_resume_chained_ops: true` (기본 true)
- 구현
  1) scheduler.py 체인 stub 생성부에 태그 추가
     - `rec2['source'] = 'RESUME_CHAIN'`
  2) main.py:103 `_emit_op_events`에서 `source` 보존
     - `source = rec.get('source') if rec.get('source') not in (None, 'None') else None`
  3) main.py:539 `export_operation_sequence`에서 필터
     - `rows = [r for r in rows if r.get('source') != 'RESUME_CHAIN']` (flag가 true일 때)
  4) 단위 검증
     - 재현 시나리오: ERASE → ERASE_SUSPEND → ERASE_RESUME → ERASE 잔여 체인
     - 기대: `operation_sequence_*.csv`에는 ERASE(초반)만 존재, 체인된 ERASE 잔여는 제외. `operation_timeline_*.csv`와 `op_state_timeline_*.csv`는 계속 진실을 반영(이미 Option C로 효과 창 분리 가능)

## 역사적 맥락(thoughts/ 기반)
- N/A

## 관련 연구
- research/2025-09-14_13-48-44_rm_validity_pending_bus_plane_windows.md — 동일시각 공존 정합성 관련
- plan/2025-09-14_operation_timeline_effective_optionC_impl_plan.md — 타임라인 효과창 분리(Option C)

## 미해결 질문
- 체인 기능 비활성(`features.suspend_resume_chain_enabled=false`) 시 재개 경로가 다른 경우에도 동일 정책을 적용할 필요가 있음. 해당 경로에서의 ‘출처’ 태깅 포인트 확인 필요.
