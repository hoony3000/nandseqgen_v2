# RM: suspended_ops 축 분리(ERASE/PROGRAM) 구현 계획

- 근거 문서: research/2025-09-14_20-59-24_program_resume_reschedule.md:85
- 관련 코드 진입점:
  - scheduler.py:466 — 체인 전처리에서 `rm.suspended_ops(die0)` 사용(마지막 항목만 사용)
  - scheduler.py:580 — 체인 예약 성공 후 `rm.resume_from_suspended(die, op_id)` 호출
  - resourcemgr.py:600 — SUSPEND/RESUME 커밋 처리 분기
  - resourcemgr.py:1009 — `move_to_suspended`가 단일 `_suspended_ops`로 이동
  - main.py:873 — 스냅샷에 `suspended_ops` 포함

## Problem 1-Pager
- 배경: 현재 ResourceManager는 die별 suspended op을 단일 스택(`_suspended_ops`)으로 관리한다. Scheduler는 *_RESUME 직후 체인할 때 `sus[-1]`만 확인한다.
- 문제: ERASE/PROGRAM이 교차 suspend될 경우, PROGRAM_RESUME 시 마지막 항목이 ERASE라면 체인이 건너뛰어 재스케줄 누락이 발생한다. 또한 rem==0.0인 마지막 항목이 있어도 스킵된다.
- 목표: suspended 메타를 ERASE/PROGRAM 축으로 분리 저장·조회하여 패밀리 모호성을 제거하고, Scheduler가 정확한 축에서만 체인/복귀하도록 한다.
- 비목표: 상태 타임라인 계산/커밋 규칙 변경, admission 윈도우/락/배제 정책 변경, proposer 로직 변경.
- 제약: 하위 호환 유지(기존 `suspended_ops()`와 스냅샷 포맷 지원), 함수 복잡도/길이 제한 내 구현, 관찰 가능성(로그/메트릭) 유지.

## 대안 비교(결정 전 평가)
- A) 축 분리 저장/조회(ERASE/PROGRAM)
  - 장점: 모호성 제거, O(1) 접근, 단순한 정신모형. 위험: API/호출부 수정 필요. 단점: 스냅샷/복구 포맷 확장 필요.
- B) 단일 리스트 유지 + Scheduler가 역순 스캔해 패밀리 일치 항목 선택
  - 장점: RM 변경 최소화. 단점: 호출자마다 필터링 중복/누락 위험, 향후 소비자 증가 시 복잡도 전파. 위험: 숨은 사이드이펙트.
- C) 단일 리스트 유지 + 메타에 축 필드 추가하고 호출자 필터링
  - 장점: 부분적 구조 개선. 단점: B와 동일한 소비자 책임, 실수 여지. 위험: 기존 소비자와 혼재 시 재발.
- 선택: A(축 분리). 연구노트 권고와 일치하며 가장 단순·명확.

## 변경 사항 요약
- 내부 저장소: `_suspended_ops_program: Dict[int, List[_OpMeta]]`, `_suspended_ops_erase: Dict[int, List[_OpMeta]]` 추가. 기존 `_suspended_ops`는 제거 대신 호환 뷰로 유지.
- API(신규):
  - `def suspended_ops_program(self, die: Optional[int] = None) -> List[Dict[str, Any]]`
  - `def suspended_ops_erase(self, die: Optional[int] = None) -> List[Dict[str, Any]]`
  - `def resume_from_suspended_axis(self, die: int, op_id: Optional[int], axis: str) -> None`  # axis in {"ERASE","PROGRAM"}
- API(변경):
  - `move_to_suspended(die, op_id, now_us, axis)`로 서명 확장(axis 필수). 기존 시그니처는 deprecated 경로로 축을 추론(가능하면 경고 로그)하거나 내부에서 no-op 처리.
  - `suspended_ops(die)`는 호환 뷰: 두 축을 병합해 반환(정렬 기준: `start_us` 오름차순). 신규 소비자는 축별 API 사용.
  - `resume_from_suspended(die, op_id)`는 호환 경로: op_id가 주어지면 두 축을 역순 스캔해 가장 최근 일치 항목 복귀(모호성 존재). Scheduler는 축 지정 API를 사용.
- 커밋 경로 변경(resourcemgr.py):
  - `ERASE_SUSPEND`/`PROGRAM_SUSPEND`에서 축 결정 후 `move_to_suspended(..., axis=fam)` 호출.
  - 타임라인 절단 시 planes 결정은 방금 이동한 축 리스트의 마지막 메타를 참조.
  - `*_RESUME`는 축 상태 종료만 수행(현행과 동일).
- Scheduler 변경(scheduler.py):
  - 체인 전처리: `ERASE_RESUME`면 `rm.suspended_ops_erase(die)`, `PROGRAM_RESUME`면 `rm.suspended_ops_program(die)`에서만 메타 조회.
  - 체인 후 복귀: `rm.resume_from_suspended_axis(die, op_id, axis)` 사용(axis는 RESUME 패밀리로 결정).
- 스냅샷/복구:
  - snapshot에 `suspended_ops_erase`, `suspended_ops_program` 추가. 기존 `suspended_ops` 유지(호환).
  - restore는 신규 필드를 우선 복구; 구버전 스냅샷만 있을 경우 `base`에 "ERASE" 포함 여부로 축 라우팅 후 각 축 리스트 구성.
- 관찰성:
  - 체인 스킵 로그 메시지 유지하되 축/메타 요약 포함.

## 상세 설계 및 스펙
- 축 상수: `AXIS_ERASE = "ERASE"`, `AXIS_PROGRAM = "PROGRAM"`.
- 자료구조: `_suspended_ops_program[d]`와 `_suspended_ops_erase[d]`는 `_OpMeta` 리스트(append/pop 스택)로 동일.
- 정규화 함수: `_to_pub_meta(_OpMeta) -> Dict[str, Any]`를 재사용하여 중복 제거.
- `move_to_suspended(die, op_id, now_us, axis)`:
  - 입력 검증: axis ∈ {ERASE, PROGRAM} 아니면 return.
  - 선택 규칙: `op_id`가 지정되면 `_ongoing_ops[die]`를 역순 스캔해 일치 항목 pop; 없으면 마지막 pop.
  - rem 계산: `remaining_us = max(0, end_us - now_q)` 후 대상 축 리스트에 append.
- `resume_from_suspended_axis(die, op_id, axis)`:
  - 대상 축 리스트에서 역순 스캔해 pop 후 `_ongoing_ops[die]`에 append.
- `suspended_ops_{erase,program}(die)`:
  - die 미지정 시 모든 die 평탄화하여 공개 형태로 변환(List[Dict]).
- `suspended_ops(die)`:
  - 두 축 결과를 병합 후 `start_us`로 정렬하여 반환. 성능 요구 낮음.
- 커밋 변경(resourcemgr.py):
  - `ERASE_SUSPEND`/`PROGRAM_SUSPEND` 분기에서 `fam` 도출 후 `move_to_suspended(axis=fam)` 호출.
  - 절단 대상 plane 계산: 직전에 이동한 축 리스트의 마지막 메타를 참조. 메타 없으면 전체 planes.
- 스냅샷/복구(resourcemgr.py, main.py):
  - snapshot(): `suspended_ops_erase`/`suspended_ops_program` 필드 추가. 기존 `suspended_ops` 유지.
  - restore(): 신규 필드가 있으면 우선 채움. 없고 `suspended_ops`만 있으면 각 메타의 `base`를 검사해 축별 리스트로 나눔.

## 영향도(호출/참조 경로)
- scheduler.py:466 — `suspended_ops(die0)` → 축별 API로 분기 조회 필요.
- scheduler.py:580 — `resume_from_suspended(...)` → `resume_from_suspended_axis(..., axis)`로 변경.
- resourcemgr.py:600 — SUSPEND 처리 분기에서 신규 시그니처 사용 및 절단 참조 축 변경.
- resourcemgr.py:1009 — `_suspended_ops` append 경로 → 축별 append로 변경.
- main.py:873 — 스냅샷에 신규 축별 리스트 추가.

## 단계별 구현 순서(작게 쪼개기)
1) RM 내부 구조 확장: 축별 컨테이너와 신규 getter 추가(기존 기능 영향 없음).
2) RM snapshot/restore 확장: 새 필드 지원 + 구버전 호환 병행.
3) RM 이동/복귀 API 추가: `move_to_suspended(axis)`/`resume_from_suspended_axis(axis)` 도입, 기존 메서드는 호환 래퍼로 유지.
4) RM 커밋 경로 연결: SUSPEND 분기에서 축 지정 호출, 절단 plane 참조 축 변경.
5) Scheduler 연동: 축별 조회/복귀로 전환(RESUME 패밀리 기준 분기).
6) main 스냅샷 출력 보강: 축별 리스트 추가(기존 필드 유지).
7) 리팩터: 불필요한 `_suspended_ops` 직접 참조 제거 및 주석/문서 업데이트.

## 테스트 계획
- 단위: RM 축별 API 동작, snapshot/restore 역직렬화 호환, move/resume 축 정확성.
- 회귀: 교차 suspend 시나리오에서 PROGRAM_RESUME 체인 발생 확인(이전엔 미발생).
  - 재현 스크립트: ERASE 예약 → ERASE_SUSPEND → PROGRAM 예약 → PROGRAM_SUSPEND → PROGRAM_RESUME → rem 체인 검사.
- E2E: `features.suspend_resume_chain_enabled=true`에서 ERASE/PROGRAM 모두 성공/실패 경로 1개 이상 포함.

## 리스크 및 완화
- 축 추론 오류(구버전 스냅샷 복구 시): `base`의 포함 검사를 보수적으로 구현하고 실패 시 전체 planes 절단/호환 뷰로 폴백.
- 호환성: 기존 `suspended_ops()`에 의존하는 코드가 병합 순서에 기대하는 경우 → 정렬 기준을 문서화하고 호출부를 점진 전환.
- 영향 범위: Scheduler/ResourceManager/main만 변경. Proposer/부트스트랩에는 영향 없음.

## 롤백 계획
- 기능 플래그 없이도 안전: 축별 API가 도입되어도 기존 API는 유지. 문제 발생 시 Scheduler를 기존 API로 즉시 복귀 가능.

## 구현 체크리스트
- [ ] `_suspended_ops_{erase,program}` 추가 및 초기화
- [ ] `suspended_ops_{erase,program}()` 구현
- [ ] `suspended_ops()`를 축 병합 뷰로 변경(정렬 포함)
- [ ] `move_to_suspended(axis)`/`resume_from_suspended_axis(axis)` 구현 및 래거시 래퍼 유지
- [ ] 커밋 경로에서 축 지정 호출 + 절단 참조 축 변경
- [ ] scheduler 축별 조회/복귀로 전환(RESUME 패밀리 기준)
- [ ] snapshot/restore 포맷 확장 + 호환
- [ ] 로그/메트릭 점검 및 문서 주석
- [ ] 회귀 테스트 추가(교차 suspend 재현)

