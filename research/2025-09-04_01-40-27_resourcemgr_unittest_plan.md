---
date: 2025-09-04T01:40:27+00:00
researcher: codex
git_commit: 013916e2bf4c7c33f0cf2b0f45b7ada4ec14edb9
branch: main
repository: nandseqgen_v2
topic: "ResourceManager 유닛테스트 플랜 (경계시간 포함)"
tags: [research, codebase, resourcemgr, timing, latch, bus, exclusion]
status: complete
last_updated: 2025-09-04
last_updated_by: codex
---

# 연구: ResourceManager 유닛테스트 플랜 (경계시간 포함)

**Date**: 2025-09-04T01:40:27+00:00
**Researcher**: codex
**Git Commit**: 013916e2bf4c7c33f0cf2b0f45b7ada4ec14edb9
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
`resourcemgr.py`의 유닛테스트 플랜을 수립하고, operation의 경계시간 조건에서 발생 가능한 오동작(레이스/잠금/배타창/버스/타임라인 경계)까지 포괄적으로 검증한다.

## 요약
- 시간 해상도는 `SIM_RES_US`에 의해 양자화되며 모든 비교는 좌폐우개([start, end))로 동작한다. 경계에서는 비중첩이 의도다.
- Plane/bus/exclusion/latch는 모두 경계에서 허용, 내측 겹침은 거부한다. 단, latch는 이전 op `end` 시각부터 활성(True)이며 해제 API 호출 전까지 유지된다.
- 테스트는: 양자화 경계, 인접 예약 무충돌, 미세 겹침 충돌, latch의 활성·해제 타이밍, 배타창 토큰, 타임라인 질의(`state_at`), 스냅샷/복원까지 포함한다.

## 상세 발견

### 시간 양자화와 경계
- `resourcemgr.py:6` `quantize(t)`는 `round(t / SIM_RES_US) * SIM_RES_US`를 사용. 반올림(은행가 반올림)과 부동소수 오차에 주의.
- 타임라인 포함성: `resourcemgr.py:38` `start_us <= t < end_us` (좌폐우개).
- 중첩 판정: `resourcemgr.py:47` `seg.start<end and start<seg.end` (경계 접촉은 비중첩).
- plane/bus/exclusion도 동일 형태 비교: `resourcemgr.py:100-101`, `resourcemgr.py:108-109`, `resourcemgr.py:168-172`.

### Latch 동작
- latch 활성 판정: `resourcemgr.py:112-120`. `t0 < start_us`면 비활성, `end_us is None`이면 무기한 활성, 그 외 `t0 < end_us`.
- latch 설정 시점: 예약 성공 후 `end`에서 시작. `READ*`는 plane별, `ONESHOT_PROGRAM_{LSB,CSB,MSB}`는 die-wide로 각 plane에 적용: `resourcemgr.py:218-227`.
- 해제: READ계열은 `release_on_dout_end(targets, now)`로 대상 plane에서 제거, 프로그램계열은 `release_on_exec_msb_end(die, now)`로 die의 모든 plane 제거: `resourcemgr.py:252-260`.
- 라치-그룹 매핑: `exclusions_by_latch_state` → `exclusion_groups` 목록 포함 여부로 차단: `resourcemgr.py:122-157`, `config.yaml:1900-1940` 근방.

### Exclusion 윈도우
- 기본 `constraints.exclusions` 규칙에서 배타창 생성 지원(현재 기본 config에 미정의라 빈 목록): `resourcemgr.py:311-326`.
- 별도 수동 윈도우 삽입 시 토큰 일치(`ANY` 또는 `BASE:NAME`)에만 차단: `resourcemgr.py:160-173`.

### Bus/Plane 예약
- bus 세그먼트는 op 시작 오프셋 기준으로 양자화된 구간으로 예약: `resourcemgr.py:104-110`.
- plane scope 가용 시각은 대상 plane 집합의 `max(avail)`로 결정: `resourcemgr.py:87-95`.

## 코드 참조
- `resourcemgr.py:6` - 시간 양자화 함수.
- `resourcemgr.py:38` - 상태 질의의 포함 규칙([start,end)).
- `resourcemgr.py:96-110` - plane/bus 중첩 판정 로직.
- `resourcemgr.py:112-120` - latch 활성 판정.
- `resourcemgr.py:122-157` - latch 기반 exclusion 평가.
- `resourcemgr.py:175-231` - 예약 절차와 latch/타임라인 등록.
- `resourcemgr.py:252-260` - latch 해제 API.
- `resourcemgr.py:264-277` - 타임라인 중첩 쿼리 API.
- `resourcemgr.py:289-309` - 스냅샷/복원.
- `resourcemgr.py:311-326` - cfg 기반 배타창 파생.

## 유닛테스트 플랜

- 기본 픽스처: `ResourceManager(cfg, dies=1, planes=2)`와 단순 `Op`/`State` 스텁.
  - `State(name, dur_us, bus=False)`
  - `Op(base, states=[...])` (`base`는 문자열)
  - `Address(die=0, plane=P, block=0)`

1) 양자화/경계 동작 ✓
- quantize 반올림: 0.005 → 0.00, 0.015 → 0.02 검증 (`SIM_RES_US=0.01`, 파이썬 은행가 반올림 적용).
- `state_at` 경계: 구간 시작 t=start 포함, t=end 제외 확인.
- `has_overlap` 경계: [0.0,1.0)와 [2.0,3.0) 비중첩, [0.0,1.0)와 [0.99,1.5) 중첩.

2) plane 예약 경계 ✓
- 동일 plane에서 opA [0,5), opB [5,10) 허용(인접 경계). `reserve`→`commit` 후 `_plane_resv` 길이 확인.
- 미세 겹침: opB 시작을 4.99로 힌트 → `feasible_at`가 5.0으로 정렬(양자화 포함).

3) bus 예약 경계 ✓
- opA에 bus 세그먼트 [0,1), opB bus 세그먼트 [1,2) 허용.
- 겹침: opB 세그먼트 [0.99,1.5) → `reserve`/`feasible_at`에서 bus 충돌로 거부.

4) exclusion 윈도우 경계(수동 삽입) ✓
- `ExclWindow(start=0.0,end=5.0,scope='GLOBAL',tokens={'ANY'})` 추가 후 [0,5) op 거부, [5,6) 허용.
- `tokens={'BASE:READ'}`로 기반별 차단 확인.

5) latch: READ 경계 ✓
- READ opA [0,5) 예약/커밋 → latch는 t=5.0부터 활성. `latch_state(plane,4.99)=False`, `=5.0=True`.
- latch 활성 중 `exclusions_by_latch_state.LATCH_ON_READ -> group 'after_read'`에 READ 포함 시 차단 확인.
- `release_on_dout_end(target)` 전후로 `feasible_at` 허용 변화 확인.

6) latch: ONESHOT_PROGRAM_* 경계 ✓
- LSB/CSB/MSB 각각 [0,5) 또는 가용시각에 맞춰 예약 후, 종료 시각부터 die-wide 모든 plane latch 활성 확인.
- `release_on_exec_msb_end(die)` 전후 허용 변화 확인.

7) 타임라인/상태 ✓
- 예약/커밋 후 `op_state(die,plane,t)`가 각 상태 구간의 경계에서 기대대로 동작(시작 포함, 끝 제외).
- `has_overlap` 프레디킷으로 특정 `op_base`만 탐지.

8) 스냅샷/복원 ✓
- 연속 예약 후 `snapshot`→새 매니저에 `restore`→ `latch_state`/타임라인/배타창 동일성 확인.

9) 다중 plane scope ✓
- `Scope.DIE_WIDE`로 예약 시 대상 die 내 plane들의 `max(avail)`로 시작시각이 결정되는지 확인.

10) 에지 케이스 ✓
- 빈 `states` op(기간 0) 예약/커밋 가능, 가용시각 비증가 확인.
- 매우 짧은 dur(예: 0.005us) → 0으로 양자화되어 경계/중첩 판단이 일관적으로 비중첩 처리되는지 확인.

11) single×single 허용 베이스 동시성 ✓
- `PLANE_READ` vs `PLANE_READ4K`를 서로 다른 plane에서 동시 실행(시간 겹침) 허용 확인.
- 버스 세그먼트 없이 구성하여 bus 간섭 제거.

## 테스트 구현 노트
- 최소 cfg: `exclusion_groups`와 `exclusions_by_latch_state`에 READ/ONESHOT_PROGRAM_* 포함하도록 축소 샘플 사용(`config.yaml` 정의와 동일 그룹명 사용).
- 시간 비교는 모두 `quantize` 적용치를 검증(직접 부동소수 비교 금지).
- 경계 판정은 [start,end) 불변을 기준으로 어서션을 작성.
- 내부 구조 접근이 필요한 경우(`_excl_global` 등)는 스냅샷/복원 또는 공개 API 조합으로 우선 시도, 불가 시 테스트에서 직접 필드 조작.

## 테스트 구현 현황
- 테스트 파일: `tests/test_resourcemgr_timing_latch.py`
  - 포함 항목: 1, 2, 3, 4, 5, 6(LSB/CSB/MSB), 7, 8, 9, 10, 11
  - 미포함 항목: (없음)
  - 메모: `quantize(0.005)`는 0.00으로 양자화됨(파이썬 round 은행가 반올림). 경계 검증 시 질의 시간도 양자화됨에 유의.

## 아키텍처 인사이트
- 모든 시간 비교가 양자화된 값으로 수행되어 경계/동시성 논리가 단순해짐. 경계 인접 시 무충돌, 내부 겹침 시 충돌이라는 일관 규칙.
- latch는 ‘작업 종료 시각부터’ 활성이라는 설계로, 데이터 출력 완료/EXEC_MSB 완료 이벤트로만 해제되는 비대칭 구조. 경계시각(t=end)에서 즉시 차단됨을 테스트로 보장해야 함.

## 관련 연구
- `docs/PRD_v2.md`의 latch/배타 정책 서술과 일치 여부 교차 검증 필요.

## 미해결 질문
- `constraints.exclusions` 규칙이 실제 배포 config에 추가될 경우, 파생 윈도우의 범위가 상태 경계와 정확히 일치해야 하는지(양자화 전/후 기준) 명세 필요.
- latch 해제 이벤트와 타임라인의 상호작용(예: `DOUT_END` 상태 경계) 구체 시점 정의 확인 필요.
