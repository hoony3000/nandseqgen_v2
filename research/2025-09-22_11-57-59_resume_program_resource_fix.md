---
date: 2025-09-22T11:57:59.117444+09:00
researcher: Codex
git_commit: 63127c53461d485275ba33e6b4e4a4617100106d
branch: main
repository: nandseqgen_v2
topic: "PROGRAM resume resource reallocation"
tags: [research, codebase, scheduler, resourcemgr, suspend-resume]
status: complete
last_updated: 2025-09-22
last_updated_by: Codex
last_updated_note: "후속 연구: resume tail 재예약 정책과 quantize 판단 보강"
---

# 연구: PROGRAM resume resource reallocation

**Date**: 2025-09-22T11:57:59.117444+09:00  
**Researcher**: Codex  
**Git Commit**: 63127c53461d485275ba33e6b4e4a4617100106d  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
SUSPEND, RESUME 반복 시나리오에서 PROGRAM target(die, block) 내 page address 가 0→1→2 순서로 증가하지 않고 RESUME 된 PROGRAM 이 끝나기도 전에 다음 PROGRAM 이 예약되는 문제를 해결하기 위해 필요한 개선 방안을 조사한다.

## 요약
- `ResourceManager.move_to_suspended_axis` 는 원본 PROGRAM 예약의 plane/배타 window 를 그대로 두고 `remaining_us` 만 계산해서, SUSPEND 이후에도 `_plane_resv` 와 `_excl_die` 가 원래 종료 시각을 유지한다 (`resourcemgr.py:1087`).
- `resume_from_suspended_axis` 는 meta 를 `_ongoing_ops` 로 되돌리지만 plane/die 예약을 재적용하지 않아 RESUME 이후 남은 구간이 리소스 점유에 반영되지 않는다 (`resourcemgr.py:1153`).
- `Scheduler._handle_resume_commit` 는 RESUME 커밋을 감지해 OP_END 재큐잉만 수행하므로, ResourceManager 차원에서 재예약이 이뤄지지 않은 상태로 다음 PROGRAM 예약을 통과시킨다 (`scheduler.py:392`).
- 실제 로그에서도 RESUME 직후 다른 PROGRAM OP_START 가 선행하여 page address 샘플이 비정상 증가하는 것이 확인된다 (`out/op_event_resume.csv:17`, `out/op_event_resume.csv:18`).

## 상세 발견

### ResourceManager suspend/resume 흐름
- `move_to_suspended_axis` 는 ongoing 메타를 pop 하면서 `remaining_us` 를 계산하나, `_plane_resv[(die, plane)]` 와 `_excl_die[die]` 안의 기존 window 는 업데이트하지 않는다. SUSPEND 시각 이후 리소스가 해제되지 않아 "원래 종료 시각"이 그대로 남는다. (`resourcemgr.py:1087`)
- `resume_from_suspended_axis` 는 meta.start_us/end_us 를 RESUME 시각 + remaining 으로 재설정하지만, `_plane_resv`·`_excl_die`·`_avail` 등에 새 window 를 추가하지 않는다. 이 때문에 RESUME 된 작업의 tail(예: 6→12us)이 ResourceManager 가 추적하는 점유 정보에 반영되지 않는다. (`resourcemgr.py:1153`)
- 단위 테스트도 remaining_us 만 검증하고 plane/die 예약 일관성은 확인하지 않아 이러한 문제를 놓친다. (`tests/test_suspend_resume.py:65`)

### Scheduler resume 처리
- `_handle_resume_commit` 는 RESUME 커밋 record 에서 meta 를 꺼내 `_eq.push(end, "OP_END")` 만 수행한다. ResourceManager 를 통한 재예약이나 점유 복원 루틴이 없다. (`scheduler.py:392`)
- 현재 구성에서 `suspend_resume_chain_enabled` 플래그가 활성화되어 있어 RESUME 체인을 즉시 시도하지만, ResourceManager 측 제한이 없으므로 새로운 PROGRAM 예약이 조기 통과한다. (`config.yaml:30`)

### 관측 로그
- `Page_Program_SLC` 4번 RESUME 의 `OP_END`(15260us) 전에 5번 `OP_START` 가 15200us 에 등장하여 동일 die/plane 을 겹친다. 동일 패턴이 여러 구간에서 반복되어 page address 가 건너뛰는 로그가 누적된다. (`out/op_event_resume.csv:17`, `out/op_event_resume.csv:18`)

### 개선 대안 평가
- **Alt A – 예약 구간 트림 + 재예약**: Pros – plane/excl/bus 점유를 정확히 복원하여 RM 일관성 유지; Cons – per-op window 메타 저장과 트리밍 로직 추가 필요; Risks – 잘못된 window 식별 시 deadlock/허용되지 않은 겹침 발생.
- **Alt B – `_ongoing_ops` 기반 guard**: Pros – 비교적 단순하게 RESUME 중 신규 PROGRAM 을 차단; Cons – `_plane_resv` 등 관측 지표는 계속 틀려 있고 bus/excl 정책 반영 안 됨; Risks – guard 누락 시 회귀, guard 과도 시 legitimate 병렬성까지 막을 수 있음.
- 가장 단순하면서 사양에 부합하는 해법은 Alt A: SUSPEND 에서 잔여 예약을 잘라내고 RESUME 에서 일반 커밋 경로(또는 동등한 helper)로 재예약하도록 ResourceManager 를 확장한 뒤, Scheduler 는 helper 호출 후 OP_END 를 큐잉한다.

## 코드 참조
- `scheduler.py:392` – `_handle_resume_commit` 가 RESUME 커밋을 OP_END 재큐잉으로만 처리.
- `resourcemgr.py:1087` – `move_to_suspended_axis` 가 plane/excl window 를 해제하지 않고 meta 만 이동.
- `resourcemgr.py:1153` – `resume_from_suspended_axis` 가 예약 복원 없이 meta.end_us 만 갱신.
- `resourcemgr.py:494` – `reserve` 가 `_ongoing_ops` 정보를 고려하지 않아 RESUME tail 과 겹쳐도 통과.
- `tests/test_suspend_resume.py:65` – remaining_us 위주 테스트로 plane 예약 검증 부재 확인.
- `config.yaml:30` – `suspend_resume_chain_enabled` 활성화 환경.
- `out/op_event_resume.csv:17` – RESUME 종료 이전에 새 PROGRAM OP_START 확인.

## 아키텍처 인사이트
- PROGRAM 리소스 점유는 ResourceManager 의 plane/exclusion window 에 의해 결정되므로, RESUME 는 동일 윈도우를 재적용하는 "2차 커밋" 으로 취급해야 한다. 이를 위해 `_OpMeta` 에 per-plane 예약 핸들을 저장하거나, 최소한 (die, plane, start, end) 튜플을 보관해 SUSPEND 시점에 트림/RESUME 시점에 재생성할 필요가 있다.
- 재예약 시 `_avail[(die, plane)]`, `_plane_resv`, `_excl_die` 를 동기화해야 admission window와 die-level 멀티 정책이 정확하게 동작한다.
- Resume tail 은 CORE_BUSY 잔여 구간만 다루므로, 기존 bus/latch 정책을 그대로 적용하면 된다. ISSUE 단계는 최초 커밋에서 이미 소모됐고, bus/latch 적용은 `reserve` 가 states 기반으로 계산하므로 CORE_BUSY-only 재예약에서도 동일하게 작동한다 (`resourcemgr.py:513`, `resourcemgr.py:515`).
- Suspend/Resume 주기마다 `quantize` 를 호출해 잔여 시간과 재시작 시각을 재양자화하므로 추가적인 rounding 전략 없이도 누적 오차가 제한된다 (`resourcemgr.py:1114`, `resourcemgr.py:1178`).
- Scheduler 측 로직은 ResourceManager helper 를 호출해 재예약 성공을 확인한 후 OP_END 를 큐잉하도록 변경하면 책임이 명확하게 분리된다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-22_11-32-44_resume_program_overlap.md` – 동일 버그의 원인 규명 및 사양 위반 정리.
- `research/2025-09-18_08-13-53_suspend_resume_addr_state.md` – Resume 체인과 OP_END 재큐잉으로 인한 상태 오염 분석.

## 관련 연구
- `research/2025-09-18_12-51-24_resume_stub_remaining_us_meta.md`
- `research/2025-09-22_00-22-10_resume_stub_rework.md`

## 후속 연구 2025-09-22T12:38:04.687012+09:00
- **Resume tail bus/latch 정책**: CORE_BUSY 잔여 구간을 재예약할 때 ISSUE 단계는 이미 소진되었으므로 stub 은 CORE_BUSY state 만 포함하면 된다. `reserve` 는 state 리스트에서 bus segment 와 latch 적용을 계산하기 때문에 CORE_BUSY 전용 stub 도 기존 정책을 그대로 상속한다 (`resourcemgr.py:513`, `resourcemgr.py:515`).
- **Quantize 누적 오차**: Suspend 시 `move_to_suspended_axis` 가 remaining 시간을 quantize 하고 (`resourcemgr.py:1114`), resume 시 `resume_from_suspended_axis` 가 시작/종료를 다시 quantize 하므로 추가 전략 없이도 누적 오차가 제한된다 (`resourcemgr.py:1178`).
- **테스트 범위**: Alt A 경로에서 guard 를 별도로 두지 않기로 했으므로, 회귀 테스트는 plane/excl window 가 트림/재적용되는지를 중점적으로 검증한다.

## 미해결 질문
- plane/excl window 트리밍 시 다른 op 윈도우와 동일한 start/end 를 구분하는 안정적인 식별자를 어떻게 부여할 것인가?
