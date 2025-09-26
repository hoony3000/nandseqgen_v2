---
date: 2025-09-25T07:56:18.014458+00:00
researcher: Codex
git_commit: 36f1a384eac46638a4b1f7739f00fa2bc568098d
branch: main
repository: nandseqgen_v2
topic: "Suspend/resume refactor option best practice assessment"
tags: [research, resourcemgr, scheduler, suspend-resume, refactor]
status: complete
last_updated: 2025-09-25
last_updated_by: Codex
last_updated_note: "후속 연구: _OpMeta 메타데이터와 회귀 테스트 범위 정리"
---

# 연구: Suspend/resume refactor option best practice assessment

**Date**: 2025-09-25T07:56:18.014458+00:00  
**Researcher**: Codex  
**Git Commit**: 36f1a384eac46638a4b1f7739f00fa2bc568098d  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
2025-09-24_07-56-26_suspend_resume_reservation_alignment.md 연구에서 Refactor Options 1,2,3 중 어떤 것이 best practice 에 가까운지 판단하기 위한 정보를 research 해줘

## 요약
- ResourceManager 는 예약 시점에 plane/bus/state 정보를 이미 수집하므로 내부 트랜잭션을 재구성해 resume 잔여 구간을 재예약하는 Option 2 가 구조상 가장 자연스럽다.
- Scheduler 는 proposal/commit 파이프라인을 담당하며 resume 시에는 메타만 받아 OP_END 를 재주입하고 있어 Option 1 처럼 잔여 state 를 조립하면 층간 책임이 흐려진다.
- Option 3 의 상태 기반 차단은 config 가 제공하는 거친 배타 규칙(`exclusions_by_suspend_state`)을 장시간 유지해 병렬도를 떨어뜨리는 완화책일 뿐, plane/bus 타임라인을 복원하지 못해 근본 문제를 해결하지 못한다.

## 상세 발견

### ResourceManager state instrumentation
- `reserve` 는 op states, bus segments, plane 창을 `_Txn` 구조에 누적하고 commit 에서 `_plane_resv`/`_bus_resv`/timeline 으로 반영한다(`resourcemgr.py:543`).
- 각 target plane 에 대한 state 시퀀스는 `txn.st_ops` 로 저장되어 suspend 시 잔여 state 계산에 필요한 데이터가 이미 존재한다(`resourcemgr.py:583`).

### Suspend handling gap
- Suspend commit 은 timeline 을 잘라내고 meta 를 suspended 스택으로 옮기지만 plane/bus 창을 갱신하지 않아 resume 중에도 새 PROGRAM/ERASE 예약이 통과한다(`resourcemgr.py:702`).
- Resume 복원은 meta 의 start/end 를 조정해 `_ongoing_ops` 로만 되돌리므로 자원 상태는 그대로 비어 있는 것으로 간주된다(`resourcemgr.py:1227`).

### Scheduler layering constraints
- Scheduler 는 `_propose_and_schedule` 에서 proposer 가 만든 op 객체를 RM 에 전달하고, commit 후에는 tracking axis 가 있는 op 만 `register_ongoing` 으로 위임한다(`scheduler.py:605`).
- Resume commit 시에는 suspended meta 를 되살려 OP_END 이벤트만 재삽입하며 reserve/commit 을 호출하지 않는다(`scheduler.py:400`).

### Option 1: Scheduler-driven re-reservation
- Scheduler 가 잔여 state 를 조립하려면 `_proposer._build_op` 의 state 정의를 복제하거나 새로운 helper 를 만들어야 하고, 다중 plane scope/quantize 로직도 직접 다뤄야 한다는 점에서 기존 책임 범위를 벗어난다(`scheduler.py:605`).
- 잔여 timeline 을 scheduler 가 직접 복원하면 ResourceManager 의 `_bus_segments`/latch 정책 업데이트와 테스트 커버리지도 이원화되어 drift 위험이 높다.

### Option 2: ResourceManager internal reinstate txn
- `_OpMeta` 에 scope, state 리스트, bus 세그먼트를 추가 저장하면 ResourceManager 가 자체적으로 `_Txn` 을 생성해 잔여 구간을 `reserve`/`commit` 으로 재적용할 수 있다(`resourcemgr.py:543`).
- Resume 시 내부 txn 을 돌리면 plane/bus/latch/timeline 업데이트가 기존 헬퍼를 그대로 사용하므로 spec 이 요구한 “resume = remaining 시간 재예약” 흐름과 일치한다(`docs/SUSPEND_RESUME_RULES.md:1`).
- Scheduler 는 기존대로 meta 이동과 이벤트 관리에 집중할 수 있어 레이어 간 책임 분리가 유지된다(`scheduler.py:400`).

### Option 3: Axis gating extension
- Suspend 플래그를 더 오래 유지해 배타 규칙으로 새 PROGRAM/ERASE 를 막는 방식은 config 기반 차단(`config.yaml:2317`)에 의존하며 plane/bus 타임라인이 복원되지 않아 resume 작업 자체도 동일 규칙을 우회시켜야 하는 잔여 과제가 남는다.
- 장시간 `PROGRAM_SUSPENDED`/`ERASE_SUSPENDED` 를 유지하면 다른 die/plane 조합도 함께 차단될 수 있어 사양이 기대하는 병렬성 손실 위험이 있다(`tests/test_suspend_resume.py:66`).

## 코드 참조
- `resourcemgr.py:543` – reserve 가 plane/bus/state 데이터를 `_Txn` 에 저장.
- `resourcemgr.py:702` – suspend commit 이 meta 를 이동하지만 자원 창은 손대지 않는다.
- `resourcemgr.py:1227` – resume 가 start/end 만 재설정하고 reservation 을 복원하지 않음.
- `scheduler.py:605` – Scheduler 가 proposer -> RM -> register_ongoing 흐름을 유지.
- `scheduler.py:400` – resume commit 처리에서 OP_END 만 재삽입.
- `docs/SUSPEND_RESUME_RULES.md:1` – resume 는 remaining 시간으로 재스케줄해야 함을 명시.
- `config.yaml:2317` – suspend state 기반 배타 규칙 설정.
- `tests/test_suspend_resume.py:66` – 현재 테스트가 remaining_us 만 검증해 timeline 복원은 다루지 않음.

## 아키텍처 인사이트
- Resume 를 일반 예약처럼 다루려면 `_OpMeta` 확장을 통해 ResourceManager 가 잔여 state 를 직접 재예약하는 것이 레이어 책임과 사양에 부합한다.
- Scheduler 에 잔여 state 조립 책임을 두면 proposer ↔ resource manager 간 계약을 깨고, 향후 state 정의가 바뀔 때 두 곳을 동시에 유지해야 하는 위험이 커진다.
- 상태 기반 배타 규칙은 보조 safety 로는 유용하지만 기본 해결책으로 삼으면 병렬 스케줄링 설계 이점이 사라진다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-24_07-56-26_suspend_resume_reservation_alignment.md` – 세 옵션의 골격과 plane 재예약 부족 문제 정의.
- `research/2025-09-22_11-32-44_resume_program_overlap.md` – resume 중 plane overlap 버그를 실증하며 재예약 필요성을 강조.

## 관련 연구
- `research/2025-09-24_13-56-09_suspend_resource_conversion.md`
- `research/2025-09-22_00-22-10_resume_stub_rework.md`

## 미해결 질문
- 없음

## 후속 연구 2025-09-25T08:06:39.709671+00:00

### Q1. `_OpMeta` 메타데이터 확장 및 quantize 처리
- `register_ongoing` 호출 시 commit 레코드가 이미 `scope` 필드를 포함하므로(`scheduler.py:633`), 함수 시그니처를 확장해 Scope 값을 보존하면 suspend 시점에 다중 plane/die 범위를 재구성할 수 있다(`resourcemgr.py:1113`).
- 예약 단계에서 `_Txn.st_ops` 에 `(state_name, dur_us)` 리스트가 저장되므로(`resourcemgr.py:104`, `resourcemgr.py:583`), 이 구조를 `_OpMeta` 에 복사해두면 remaining_us 를 기준으로 state 를 부분 슬라이스하는 helper 를 구현할 수 있다.
- 버스 구간은 `_bus_segments` 가 state 의 `bus` 플래그를 이용해 오프셋을 계산하므로(`resourcemgr.py:284`), suspend 이전까지 소비된 누적 시간을 제외한 잔여 세그먼트를 meta 에 저장하거나 resume 시점에 재계산하도록 `(offset0, offset1, bus_flag)` 형태로 유지하는 것이 가능하다.
- 시간 정렬은 기존 `quantize` 유틸이 0.01µs 해상도로 rounding 하며(`resourcemgr.py:7`), `move_to_suspended_axis` 역시 `remaining_us` 를 quantize 하기 때문에(`resourcemgr.py:1189`), state 슬라이싱/버스 재생성에서도 동일 함수를 재사용하면 타임라인과의 정합성이 유지된다.
- suspend 시 `_st.truncate_after` 가 CORE_BUSY 잔여 구간을 잘라내므로(`resourcemgr.py:734`), 재예약을 위한 state 슬라이스는 timeline 과 meta 가 동일 기준을 공유하게 되며, 필요 시 `resume_from_suspended_axis` 에서 meta 의 잔여 state 를 `_Txn` 으로 재주입할 수 있다(`resourcemgr.py:1227`).
- Snapshot 복원 경로는 `_OpMeta` 필드를 그대로 직렬화/역직렬화하므로(`resourcemgr.py:1464`), 새로운 meta 필드 추가 시 snapshot/schema 업데이트도 병행해야 한다.

### Q2. Resume 재예약 회귀 테스트 범위
- ResourceManager 단위 테스트에서 suspend → resume 후 동일 die/plane 에 새 PROGRAM 을 조기 예약하려고 시도하면 `reserve` 가 `planescope` 충돌로 실패해야 한다는 어서션을 추가할 수 있으며(`resourcemgr.py:591`), `_plane_resv` 또는 `snapshot()["plane_resv"]` 로 잔여 창이 보존됐는지도 검증할 수 있다(`resourcemgr.py:1285`).
- 기존 `tests/test_suspend_resume.py` 는 remaining_us 와 OP_END 이벤트만 다루므로(`tests/test_suspend_resume.py:66`), 여기에 ResourceManager 인스턴스를 실제로 사용해 resume 이후 `reserve` 실패/성공 경계를 확인하고 `_bus_resv` 축적량도 비교하는 케이스를 추가하는 것이 적합하다.
- Latch/addrman 흐름은 Scheduler 테스트가 stub 기반으로 검증 중이므로(`tests/test_suspend_resume.py:128`), resume 재예약 로직 도입 후 OP_END 1회, `apply_pgm` 호출 1회가 유지되는지 반복 suspend 시나리오를 포함한 새 테스트를 늘려야 한다.
- Snapshot/CSV 기반 회귀는 `main.py` 의 `_write_op_event_resume_csv` 가 resume 이벤트를 시간순으로 정렬해 출력하므로(`main.py:182`), 통합 테스트에서 시뮬레이션 실행 뒤 `is_resumed=True` 행이 OP_START/OP_END 1:1 짝을 유지하는지 확인하면 시스템 수준 회귀를 빠르게 감지할 수 있다.
- 커스텀 state 를 갖는 stub op 클래스를 이미 `tests/test_resourcemgr_multi_latch.py` 에서 사용하고 있으므로(`tests/test_resourcemgr_multi_latch.py:18`), 동일 패턴으로 CORE_BUSY/버스 조합을 구성해 resume 슬라이스 helper 의 정밀도를 검증할 수 있다.
