---
date: 2025-09-26T13:02:33+0900
researcher: Codex
git_commit: 82bceb7cc7334dca08a6c48c438549d4f17c9f77
branch: main
repository: nandseqgen_v2
topic: "Shift suspend_state updates to SUSPEND/RESUME OP_END handling"
tags: [research, codebase, scheduler, resourcemgr, suspend-resume]
status: complete
last_updated: 2025-09-26
last_updated_by: Codex
---

# 연구: Shift suspend_state updates to SUSPEND/RESUME OP_END handling

**Date**: 2025-09-26T13:02:33+0900
**Researcher**: Codex
**Git Commit**: 82bceb7cc7334dca08a6c48c438549d4f17c9f77
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
scheduler 내 SUSPEND/RESUME 처리 루틴에서 PROGRAM_SUSPEND 종료 전에 PROGRAM_RESUME 이 예약되는 현상을 막기 위해, ResourceManager 의 suspend_state 업데이트 시점을 SUSPEND/RESUME OP_END 이벤트 처리 시점으로 옮길 때 변경 범위와 리스크는 무엇인가?

## 요약
- Scheduler 는 한 틱에서 OP_END → PHASE_HOOK → QUEUE_REFILL 순으로 이벤트를 처리하므로(OP_END 가 항상 선행) OP_END 시점에 suspend_state 를 갱신하면 이후 proposal 이 즉시 새로운 상태를 관측한다(scheduler.py:150-183).
- 현재 suspend_state 토글은 commit 경로에서 이루어지며(resourcemgr.py:804-856), 동시에 `move_to_suspended_axis` 가 실행돼 meta 이동·타임라인 절단이 발생한다. OP_END 로 옮기면 이 블록의 책임을 재배치해야 한다.
- suspend_state 는 `_rule_forbid_on_suspend` 를 통해 proposal/예약 차단으로 직접 연결된다(resourcemgr.py:2184-2200). 타이밍을 늦추면 PROGRAM_RESUME 제약뿐 아니라 suspend 기간 동안 금지돼야 할 base 들 전체가 영향을 받는다.
- PROGRAM_RESUME commit 시점에 `resume_from_suspended_axis` 가 즉시 원래 PROGRAM 을 재예약하는데(resourcemgr.py:1584-1644, scheduler.py:400-447), state 가 여전히 PROGRAM_SUSPENDED 로 남아 있으면 동일 규칙이 재예약을 차단하여 실패한다는 리스크가 있다.

## 상세 발견

### Scheduler OP_END Workflow
- Tick 루프에서 OP_END 를 가장 먼저 처리하며, 이후 proposal 단계가 이어진다(scheduler.py:150-183). 따라서 suspend_state 갱신을 OP_END 에 두면 같은 배치 내 후속 PHASE_HOOK/QUEUE_REFILL proposal 이 새로운 상태를 즉시 활용한다.
- `_handle_op_end` 는 현재 latch 해제와 AddressManager 동기화만 수행하고 있으며(scheduler.py:269-310), suspend_state 변경 로직이 없다. 여기서 ResourceManager 의 새로운 OP_END 훅을 호출하도록 확장해야 한다.

### ResourceManager Suspend Handling Today
- commit 시 SUSPEND/RESUME 분기에서 axis state 를 열고/닫으며(meta 이동 포함) suspend state 를 토글한다(resourcemgr.py:804-856).
- `move_to_suspended_axis` 는 commit 시점에 호출되어 ongoing meta 를 suspend 스택으로 옮기고 잔여 시간, plane/die 윈도우, 타임라인을 즉시 잘라낸다(resourcemgr.py:1420-1556). 이 동작을 OP_END 로 미루면 원래 PROGRAM 의 OP_END 이벤트가 도착했을 때 `is_op_suspended` 가 false 로 남아 오퍼레이션이 완결되는 문제가 발생할 수 있으므로 meta 이동 자체는 commit 단계에 남겨둬야 한다.

### Resume Path Dependencies
- PROGRAM_RESUME commit 직후 Scheduler 는 `resume_from_suspended_axis` 를 호출하여 중단된 PROGRAM 을 재예약한다(scheduler.py:400-447).
- 재예약은 `reserve` 경로를 그대로 사용하는데, 이때 `_rule_forbid_on_suspend` 가 suspend_state 에 따라 PROGRAM_SLC 등 base 를 차단한다(resourcemgr.py:2184-2200).
- suspend_state 해제가 OP_END 로 지연되면 재예약 시점에도 PROGRAM_SUSPENDED 로 남아 reserve 가 실패할 위험이 있다. 이를 피하려면 (a) resume 재예약 전에 상태를 미리 바꾸거나, (b) resume 경로에서 suspend 규칙을 우회하는 별도 플래그가 필요하다.

### Config-Driven Blocking
- `exclusions_by_suspend_state` 는 PROGRAM_RESUME 을 `NOT_PROGRAM_SUSPENDED` 그룹으로 묶고, PROGRAM 계열 명령 다수를 `PROGRAM_SUSPENDED` 그룹에 묶어 suspend 기간 동안 금지한다(config.yaml:2317-2321, config.yaml:2168-2199).
- `PROGRAM_SUSPEND` 자체는 `instant_resv: true` 로 정의돼 있어(config.yaml:556-579) commit 직후 plane/die 윈도우가 제거되더라도 기본 PROGRAM 예약이 확보돼 suspend 전후 윈도우 일관성을 유지한다.

## 코드 참조
- `scheduler.py:150-183` – Tick 순서(OP_END 우선)와 proposal 연계
- `scheduler.py:269-310` – `_handle_op_end` 에서 현재 수행 중인 처리
- `scheduler.py:400-447` – PROGRAM_RESUME commit 시 재예약 흐름
- `resourcemgr.py:804-856` – SUSPEND/RESUME commit 분기 및 suspend_state 토글
- `resourcemgr.py:1420-1556` – `move_to_suspended_axis` 가 meta/윈도우/타임라인을 조정하는 방식
- `resourcemgr.py:1584-1644` – `resume_from_suspended_axis` 재예약 로직과 reserve 호출
- `resourcemgr.py:2184-2200` – suspend_state 기반 차단 규칙
- `config.yaml:556-579` – PROGRAM_SUSPEND/RESUME 속성 정의
- `config.yaml:2317-2321` – suspend_state 와 exclusion 그룹 매핑

## 아키텍처 인사이트
- suspend_state 는 proposal 차단 규칙의 핵심 입력값으로, commit 단계에서 즉시 토글하는 현재 설계는 resume 재예약 패스와 강하게 결합돼 있다.
- meta 이동과 state 토글이 분리되지 않은 상태에서 타이밍을 바꾸면 plane/die 윈도우 정합성과 `is_op_suspended` 체크가 깨지기 쉽다. 단계적 분리가 선행돼야 한다.
- PROGRAM_RESUME 재예약은 일반 reserve 경로에 의존하므로, state 토글 시점 변경 시 예외 플래그나 두 단계 상태(예: `RESUME_PENDING`) 도입이 필요할 수 있다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-24_13-56-09_suspend_resource_conversion.md` – suspend 자원 관리 구조 변경 연구 기록.
- `research/2025-09-24_07-56-26_suspend_resume_reservation_alignment.md` – register_ongoing 확장 및 suspend 잔여 시간 계산 과정 정리.

## 관련 연구
- `research/2025-09-25_07-56-18_suspend_resume_refactor_best_practice.md`
- `research/2025-09-22_01-07-35_repeat_suspend_remaining_us_regression.md`
- `research/2025-09-18_08-13-53_suspend_resume_addr_state.md`

## 미해결 질문
- PROGRAM_RESUME 재예약이 suspend 규칙에 막히지 않도록 어떤 예외 경로를 둘 것인가?
- suspend_state 갱신을 미루면서도 original PROGRAM OP_END 이벤트를 안전하게 무시하도록 meta 이동/윈도우 트리밍을 어떤 시점에 수행해야 하는가?
- suspend_state 토글을 OP_END 로 옮길 경우, suspend 직후 제안되어야 하는 RECOVERY_RD 등 후속 시퀀스가 얼마나 지연되는지에 대한 성능 영향 평가는 되었는가?
