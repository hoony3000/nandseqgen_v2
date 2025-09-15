# RCA: 35639.1µs 이후 operation 미예약 문제 — ONESHOT_CACHE_PROGRAM 상태 종료 누락

## Problem 1‑Pager
- 배경: `python main.py --refresh-op-state-probs -t 200000` 실행 시 PRD §3 산출물(오퍼레이션/상태 타임라인 등)을 생성해 스케줄러 동작을 검증한다.
- 문제: 오퍼레이션 타임라인이 `35639.1` 이후 더 이상 예약되지 않음. 이후 시간대에서는 proposer가 후보를 지속적으로 탐색하지만 대부분 `state_block`으로 거절됨.
- 목표: 재현 → 원인 규명 → 가장 단순하고 안전한 수정으로 스케줄링을 정상화.
- 비목표: 제약 정책(Constraints) 의미 변경, 광범위한 리팩터링, 임시적으로 규칙을 비활성화하여 문제를 “우회”하는 것.
- 제약: PRD v2 의미 준수, 변경은 작고 국소적으로, 코드/문서 반영 일치.

## 재현 및 관찰
- 커맨드: `python main.py --refresh-op-state-probs -t 200000`
- 결과 파일: 
  - `out/operation_timeline_YYMMDD_0000001.csv` (오퍼레이션 타임라인)
  - `out/proposer_debug_YYMMDD_0000001.log` (proposer 디버그)
  - `out/snapshots/state_snapshot_YYYYMMDD_HHMMSS_0000001.json` (RM 스냅샷)
- 증상:
  - 타임라인 말미 예시: `... 35639.1,35668.92,...,One_Shot_PGM_MSB_23h,...` 이후 추가 예약 없음.
  - proposer 로그 후반부: 다수 후보가 `-> state_block`으로 필터링됨, `One_Shot_PGM_LSB`는 종종 `-> preflight_fail` (체인 내 CSB/MSB가 차단됨).
  - 스냅샷: `cache_program` 엔트리에 `ON_ONESHOT_CACHE_PROGRAM`가 `end_us=null`로 계속 활성 상태.
  - ODT/suspend 상태는 비활성(`odt_disabled=false`, `suspend_states=null`).

## 구성/정책 상 맥락
- `config.yaml`의 `exclusions_by_cache_state`에 따라, `ON_ONESHOT_CACHE_PROGRAM` 활성 시 `after_oneshot_cache_program` 그룹의 베이스들이 차단됨.
- 해당 그룹에는 `READ`, `DOUT` 뿐 아니라 체인의 `ONESHOT_PROGRAM_CSB/MSB`도 포함되어 있어, LSB 이후 체인 확장에서 preflight 시 차단이 발생.

## 원인 분석 (Root Cause)
- ResourceManager가 `ONESHOT_CACHE_PROGRAM` 시작을 기록하지만, oneshot program 완료 시점(예: `ONESHOT_PROGRAM_MSB_23H` 또는 `ONESHOT_PROGRAM_EXEC_MSB`)에
  die‑level cache‑program 상태를 종료(end_us 세팅)하지 않음.
- 그 결과 cache‑program 상태가 무기한 지속되어 대부분의 후보가 캐시 상태 기반 exclusion에 의해 차단됨.
- 구현 세부: `commit()` 단계에서 베이스 문자열을 대문자로 비교함. 따라서 종료 조건 매칭도 대문자 키를 사용해야 함.
  - 참조: `resourcemgr.py:497` (commit 루프 시작), `resourcemgr.py:516` (대문자 변환), `resourcemgr.py:529` 이후 캐시 관련 처리.

## 수정 (Minimal, Safe Change)
- 의도: oneshot program이 MSB/EXEC 단계 완료 시 die‑level `ON_ONESHOT_CACHE_PROGRAM`를 종료한다.
- 구현: `resourcemgr.py`의 `commit()` 내 캐시 프로그램 처리에 종료 분기 추가.
  - 파일/라인: `resourcemgr.py:539`
  - 변경점: `elif b in ("ONESHOT_PROGRAM_MSB_23H", "ONESHOT_PROGRAM_EXEC_MSB"):` 분기에서 활성 캐시‑프로그램 엔트리의 `end_us`를 설정.
  - 주의: 비교 키는 대문자(`23H`)여야 함. (이 함수는 `b = str(base).upper()`로 처리)

## 대안 비교 (결정 전 최소 2가지)
- (A) 설정 변경: `after_oneshot_cache_program` 그룹에서 `ONESHOT_PROGRAM_CSB/MSB`/`READ`/`DOUT` 일부를 허용
  - 장점: 코드 변경 없음
  - 단점: PRD상 캐시 프로그램 기간의 동시성 제약을 약화, 시간적 의미 누수 위험
- (B) proposer 레벨 특례: 캐시 상태여도 특정 체인(LSB→CSB→MSB)을 통과시키는 예외 처리
  - 장점: RM 수정 없이도 진행 가능
  - 단점: RM의 일관된 상태/제약 검증과 괴리. preflight/feasible_at 단계에서 결국 RM이 막을 수 있음
- (C) RM에서 oneshot program 완료 시 캐시 프로그램 종료 (채택)
  - 장점: 상태 수명주기를 올바로 마무리, 정책 의미 보존, 변경 범위 최소
  - 단점: oneshot 흐름에 대한 의미가 PRD와 불일치할 경우 재검토 필요

## 검증 결과 (Before → After)
- Before: `hooks≈602, ops_committed≈84`, 타임라인 종료 `~35668.92µs`, 스냅샷의 `cache_program.end_us=null` 지속.
- After: `hooks≈1731, ops_committed≈566`, 타임라인이 200ms까지 진행, 스냅샷 `cache_program`의 `end_us`가 정상적으로 채워짐.
- 부수효과: ODT/서스펜드/래치 관련 회귀 징후 없음(샘플 스냅샷 확인).

## 리스크/영향
- 캐시 프로그램 종료 시점을 MSB/EXEC 완료로 본다는 가정이 맞는지 PRD와 재확인 필요.
- oneshot이 아닌 일반 `CACHE_PROGRAM_SLC → PROGRAM_SLC` 경로는 기존 종료 로직을 그대로 유지.

## 후속 작업 (TODO)
- 단위/회귀 테스트 추가: MSB/EXEC 완료 후 `cache_state(die, plane, at_us)`가 None으로 돌아오는지 확인.
- 명명 규칙 점검: 베이스 키 비교는 대문자 기준임을 개발자 가이드에 명시.
- 설정 검증: `exclusion_groups.after_oneshot_cache_program` 구성이 PRD 기대와 일치하는지 리뷰.

## 파일 참조
- `resourcemgr.py:497`
- `resourcemgr.py:539`

## 재현 커맨드
- `python main.py --refresh-op-state-probs -t 200000`

