---
date: 2025-09-24T07:56:26.266825+00:00
researcher: Codex
git_commit: 36f1a384eac46638a4b1f7739f00fa2bc568098d
branch: main
repository: nandseqgen_v2
topic: "Suspend/Resume reservation realignment"
tags: [research, resourcemgr, scheduler, suspend-resume]
status: complete
last_updated: 2025-09-24
last_updated_by: Codex
last_updated_note: "미해결 질문 후속 연구 추가"
---
# 연구: Suspend/Resume reservation realignment

**Date**: 2025-09-24T07:56:26.266825+00:00  
**Researcher**: Codex  
**Git Commit**: 36f1a384eac46638a4b1f7739f00fa2bc568098d  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
SUSPEND/RESUME 리팩토링으로 "RESUME 된 ERASE/PROGRAM 동작이 끝나기도 전에 새로운 ERASE/PROGRAM target 이 비정상 예약"되는 문제를 해결하면서 ResourceManager 의 reserve/commit 경로를 재사용할 방법을 조사한다.

## 요약
- 최초 예약 때만 plane/bus/latch 창과 타임라인이 채워지고, suspend 이후 resume 에서는 어떤 자원도 재예약하지 않아 원래 end_us 가 지나면 동일 plane 이 즉시 가용하게 보인다 (`resourcemgr.py:543`, `resourcemgr.py:611-612`, `resourcemgr.py:1227-1259`).
- Scheduler 의 resume 핸들러는 suspended 메타를 되돌려 받고 OP_END 이벤트만 재삽입하며, reserve/commit 계층을 완전히 우회한다 (`scheduler.py:400-481`).
- Spec (`docs/SUSPEND_RESUME_RULES.md`) 은 suspend 시 종료 이벤트를 동결하고 remaining 시간으로 재개할 것을 요구하지만, 현재 구현은 remaining_us 만 업데이트하고 실질 자원 상태는 동기화하지 않는다.
- 자연스러운 리팩토링 경로는 "resume 도 새로운 예약"으로 취급해 동일한 ResourceManager 헬퍼를 재사용하는 것이며, 이를 위해 잔여 state/bus 정보를 메타에 보존하도록 확장해야 한다.

## 상세 발견

### ResourceManager
- `reserve` 는 최초 커밋 시 plane/bus 창과 `_st` 타임라인을 구성하며, 이 정보는 `_plane_resv`/`_bus_resv` 리스트에 영구 저장된다 (`resourcemgr.py:543-612`).
- `commit` 중 `PROGRAM_SUSPEND`/`ERASE_SUSPEND` 분기는 CORE_BUSY 구간을 잘라내고 메타를 suspended 스택으로 이동시키지만, 기존 plane 창은 그대로 유지되고 `remaining_us` 만 계산된다 (`resourcemgr.py:702-741`, `resourcemgr.py:1180-1199`).
- `PROGRAM_RESUME`/`ERASE_RESUME` 커밋은 axis state 를 닫을 뿐이고, `resume_from_suspended_axis` 는 meta.start_us/end_us 를 재설정한 뒤 `_ongoing_ops` 로 push 하는 것 외에 plane/bus/latch/timeline 을 전혀 갱신하지 않는다 (`resourcemgr.py:745-755`, `resourcemgr.py:1227-1259`).

### Scheduler
- `_propose_and_schedule` 는 최초 커밋에 대해서만 ResourceManager 트랜잭션을 열어 reserve/commit 을 호출하고, tracking axis 가 있으면 `register_ongoing` 으로 메타를 저장한다 (`scheduler.py:543-756`).
- `_handle_resume_commit` 은 suspended 메타를 복원한 뒤 동일 `op_uid` 로 OP_END 이벤트만 다시 push 하며, remaining CORE_BUSY 를 위한 예약이나 plane 라인업 재생성은 수행하지 않는다 (`scheduler.py:400-481`).
- 따라서 원래 end_us 이후에는 `_plane_resv` 가 더 이상 overlap 을 차단하지 못해, resume 구간이 아직 진행 중이어도 새 PROGRAM/ERASE 가 통과한다.

### Config & Spec
- `exclusions_by_suspend_state` 는 suspend 동안만 PROGRAM/ERASE 를 차단하고, resume 시 axis state 가 닫히면 더 이상 block 하지 않는다 (`config.yaml:2317-2321`, `resourcemgr.py:745-755`).
- `docs/SUSPEND_RESUME_RULES.md` 는 suspend 시 종료 이벤트를 "무기한 연장"하고 remaining 시간으로 재개해야 한다고 명시하지만, 현재 구조에서는 remaining_us 만 남고 실 예약은 복원되지 않아 규칙과 괴리가 발생한다 (`docs/SUSPEND_RESUME_RULES.md:1`, `resourcemgr.py:1189-1199`).

### Refactor Options
1. **Scheduler-driven resume reservation**  
   - Suspend 시 `_OpMeta` 에 scope, 원본 state sequence, bus segments 를 저장하도록 `register_ongoing` 을 확장한다.  
   - Resume 시 Scheduler 가 meta의 `remaining_states` 로 트리밍된 가상 `Op` 를 조립해 `rm.reserve()`/`rm.commit()` 을 다시 호출한다. 실패 시 rollback 으로 처리될 수 있어 기존 오류 경로와 일치한다.  
   - Plane/bus/latch/timeline 갱신이 기존 헬퍼를 그대로 타면서 drift 가 제거된다.  
   - 추가 과제: state trimming 로직과 다중 plane scope 복원을 위한 helper 필요, tests/test_suspend_resume.py 를 반복 suspend 케이스로 확장.

2. **ResourceManager 내부 reinstate Txn**  
   - Suspend 시 `_OpMeta` 에 원본 `st_list`, `scope`, `bus_segments` 를 보관하고, 커밋에서 `_txn` 객체를 serialization 형태로 snapshot 한다.  
   - `resume_from_suspended_axis` 가 호출되면 meta 와 snapshot 으로 새 내부 트랜잭션을 만들어 `_reserve()` + `_commit()` 헬퍼를 재사용해 잔여 기간을 재등록한다.  
   - Scheduler 는 기존처럼 OP_END 재삽입만 담당하지만, ResourceManager 가 동일 경로를 재실행하므로 확장성이 높다.  
   - 고려 사항: 실패 시 메타를 원래 스택으로 되돌리고 axis state 를 reopen 해야 하며, internal txn 이 기존 `_Txn` 구조와 호환되는지 검토 필요.

3. **Axis gating extension (보조/조합 전략)**  
   - Resume 이후에도 `_pgm_susp`/`_erase_susp` 플래그를 완료 시점까지 유지하거나 `RESUME_ACTIVE` 상태를 도입해 `state_forbid_suspend` 룰이 새 PROGRAM/ERASE 예약을 차단하도록 한다.  
   - 기존 예약 경로를 건드리지 않아 구현이 단순하지만, resume 작업 자신도 rules 예외로 허용해야 하고, config 상 많은 base 가 차단되어 병렬도가 크게 감소할 위험이 있다.  
   - 실 보장은 plane 차단이 아니라 rules 기반이므로, 나머지 두 옵션과 조합해 보조 safety guard 로 사용하는 편이 적합하다.

## 코드 참조
- `resourcemgr.py:543-612` – 최초 reserve 가 plane/bus 창과 state 타임라인을 구성.
- `resourcemgr.py:702-755` – SUSPEND/RESUME 커밋 분기에서 axis 상태를 열고 닫지만 plane 창은 갱신하지 않음.
- `resourcemgr.py:1180-1259` – suspended 메타로 remaining_us 를 기록하고 resume 시 `_ongoing_ops` 에만 재등록.
- `scheduler.py:543-756` – 최초 커밋에서만 ResourceManager 트랜잭션을 수행하고 resume 시에는 우회.
- `scheduler.py:400-481` – `_handle_resume_commit` 이 OP_END 재삽입만 수행.
- `docs/SUSPEND_RESUME_RULES.md:1` – suspend/resume 시 종료 이벤트 동결과 remaining 시간 재개 요구사항.
- `tests/test_suspend_resume.py:66-102` – remaining_us 업데이트 반복 suspend 케이스 (plane 재예약 검증은 부재).

## 아키텍처 인사이트
- Resume 구간을 일반 op 과 동일하게 모델링하려면 `_OpMeta` 가 scope/state/bus 정보를 보유해야 하며, 이는 register_ongoing 호출부(`scheduler.py:734-754`)에 해당 정보를 전달하도록 확장하면 된다.
- ResourceManager 가 internal txn 으로 잔여 구간을 재적용하면 scheduler 와의 책임 경계가 명확해지고, 향후 Validator 나 addr policy 평가도 동일하게 재사용할 수 있다.
- Axis gating 을 resume 완료 시점까지 유지하면 실패 방지용 세이프티 넷 이 되나, 본질적인 plane/bus 재예약 없이는 concurrency 제약이 커져 spec 과 충돌할 수 있다.

## 역사적 맥락
- `research/2025-09-22_11-32-44_resume_program_overlap.md` – resume 중 plane overlap 이 이미 버그로 확인됐음을 문서화.
- `research/2025-09-22_00-22-10_resume_stub_rework.md` – chain stub 접근의 한계와 규칙 대비 문제점 분석.
- `research/2025-09-18_08-13-53_suspend_resume_addr_state.md` – remaining_us 와 OP_END 중복 실행 배경 조사.

## 관련 연구
- `research/2025-09-18_12-51-24_resume_stub_remaining_us_meta.md`
- `research/2025-09-24_13-56-09_suspend_resource_conversion.md`

## 미해결 질문
- 잔여 state 를 어떻게 trim/저장할지 (다중 state/plane 케이스 포함) 및 quantize 오차 처리 방법.
- Resume 재예약이 실패하면 axis state/메타를 어떻게 복원할지에 대한 에러 경로 설계. -> (검토완료) Resume 재예약 실패는 고려하지 않는다.
- AddressManager 및 이벤트 로그에 resume 를 단일 OP_START/OP_END 흐름으로 노출하기 위한 테스트/도구 보강 범위.

## 후속 연구 2025-09-24T08:11:40.589080+00:00

### Q1. 잔여 state 트리밍과 quantize 조정
- `register_ongoing` 시점에 `resourcemgr.py:543` 에서 준비했던 `st_list` 와 `scope` 정보를 `_OpMeta` 로 함께 저장하면, suspend 타이밍에 정확한 state 잔여량을 역산할 수 있다. 현재 `_Txn.st_ops` 에서 이미 `(state, dur)` 리스트를 구축해 `_st.reserve_op` 에 전달하므로(`resourcemgr.py:583-670`), 같은 자료구조를 meta 에 복사하면 된다.
- `move_to_suspended_axis` 단계에서 `self._st` 타임라인이 suspend 시각 이후를 절단하는 로직이 존재하므로(`resourcemgr.py:702-742`), 절단 전후 구간 차이로 plane 별 진행 시간을 계산하여 `remaining_states` 를 생성할 수 있다. 이때 plane 세트(`Scope.DIE_WIDE` vs `Scope.PLANE_SET`) 는 meta.targets 로 결정하고 per-plane 루프를 동일하게 취급한다.
- 새 helper `_slice_states(states, elapsed)` 는 누적 시간을 따라가며 남은 state 를 잘라내되, 경계에 걸친 state 는 `quantize`(`resourcemgr.py:6`) 를 통해 잔여 dur 를 0.01µs 해상도로 반올림한다. suspend 직전에 timeline 이 quantize 된 값만 저장하므로 동일 함수를 사용하면 누적 오차가 재발하지 않는다.
- 다중 plane 작업은 동일 state 순서를 공유하므로, per-plane 잔여 시간은 동일하게 유지된다. 단, 실제로 plane 별 state 차이가 있는 경우를 대비해 `_st.state_at` 결과를 비교하여 불일치 시 로그 경고를 남기고 가장 긴 잔여 시간을 기준으로 재예약하도록 가드할 수 있다(`resourcemgr.py:70-107`, `resourcemgr.py:770-820`).

### Q3. AddressManager/로그 통합 및 테스트 보강
- OP_END 가 단 한 번만 발생하는지 검증하기 위해 `_handle_op_end` 의 resumed 경로(`scheduler.py:350-377`) 에서 `self._resumed_op_uids` 집합을 소비한 뒤, 동일 UID 로 두 번째 OP_END 가 들어오면 경고 및 drop 하도록 가드한다. 이로써 AddressManager 적용이 1회로 한정된다(`scheduler.py:483-522`).
- `main.py:182-210` 에서 `op_event_resume.csv` 를 생성하므로, resume 재예약 로직을 적용한 뒤에도 동일 `op_uid` 가 한 번만 등장하는지 확인하는 통합 테스트를 추가한다. 예: 시뮬레이션 실행 후 CSV 를 읽어 `is_resumed=True` 행의 count 를 검증하고, OP_START/END 개수가 1:1 인지 확인한다.
- 단위 테스트 측면에서는 `tests/test_suspend_resume.py` 에 stub AddressManager 를 이용해 suspend→resume 시 `apply_pgm` 호출 횟수가 정확히 1인지 단언하는 케이스를 추가한다(기존 케이스는 remaining_us 만 검사한다). 또한 resume 실패 시 axis 상태가 유지되는지를 확인하는 테스트를 도입하여 새로운 복원 경로 회귀를 방지한다(`tests/test_suspend_resume.py:66-132`).
