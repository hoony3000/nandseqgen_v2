---
date: 2025-09-22T00:38:35.141443+09:00
researcher: Codex
git_commit: f352a740151d2d58d11967905f1bfb2144c06f4b
branch: main
repository: nandseqgen_v2
topic: "Resume stub alternative C rule mapping"
tags: [research, scheduler, resourcemgr, suspend-resume]
status: complete
last_updated: 2025-09-22
last_updated_by: Codex
---

# 연구: Resume stub alternative C rule mapping

**Date**: 2025-09-22T00:38:35.141443+09:00  
**Researcher**: Codex  
**Git Commit**: f352a740151d2d58d11967905f1bfb2144c06f4b  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
2025-09-22_00-22-10_resume_stub_rework.md 의 대안 C 를 기준으로 SUSPEND_RESUME_RULES.md 의 각 조항을 어떤 주체(Scheduler, ResourceManager 등)가 맡아 구현해야 하는지 조사하고, 규칙을 반영하기 위한 구체적 훅/데이터를 식별한다.

## 요약
- 체인 스텁을 제거하려면 OP_END 이벤트를 재사용할 수 있도록 각 PROGRAM/ERASE 작업에 고유 식별자를 부여하고, Scheduler 가 EventQueue 에서 해당 OP_END 를 일시 중단·재예약해야 한다(scheduler.py:580, scheduler.py:679, event_queue.py:7).
- ResourceManager 는 suspend 시점에 메타를 정확히 동결하고 `remaining_us` 를 유지할 뿐 아니라, 축적된 `end_us` 를 재설정하고 axis 상태를 통해 같은 die 의 재예약을 막아야 한다(resourcemgr.py:628, resourcemgr.py:1070).
- Resume 는 ResourceManager 가 남은 시간으로 메타를 복원하고 Scheduler 가 동일 OP_END 를 새 시각으로 재스케줄하는 이중 단계로 구동되어야 하며, AddressManager 는 단 한 번만 apply 가 실행되도록 보장된다(addrman.py:607).
- 규칙 중 "종료 이벤트 무기한 연장"은 EventQueue 가 특정 OP_END 를 식별자 기반으로 동결/해제할 수 있어야 충족되며, 이는 현재 리스트 정렬 구조에 대한 보완을 요구한다(event_queue.py:7).

## 상세 발견

### ERASE 규칙 대응
- **예약 시 ongoing 등록**: Scheduler 가 최초 ERASE commit 때 `register_ongoing` 을 호출해 메타를 보관하고, Alt C 에서는 여기서 생성한 `op_uid` 와 OP_END 이벤트 핸들을 ResourceManager 에 전달해야 한다(scheduler.py:580, resourcemgr.py:1038, docs/SUSPEND_RESUME_RULES.md:2).
- **CORE_BUSY 상태에서만 suspend 허용**: ResourceManager 는 suspend 커밋 전 `self._st` 타임라인을 조회해 해당 die/plane 이 CORE_BUSY 인지 검증하고, 실패 시 Scheduler 에게 reserve 실패를 반환해야 한다(resourcemgr.py:628, resourcemgr.py:770, docs/SUSPEND_RESUME_RULES.md:3).
- **Suspend 시 남은 시간 저장 및 종료 이벤트 연장**: `move_to_suspended_axis` 가 pop 한 메타의 `end_us` 를 즉시 `now_us` 로 잘라내고 `remaining_us` 를 고정하며, Scheduler 는 대응하는 OP_END 이벤트를 EventQueue 에서 제거하거나 `suspended=True` 로 플래그 하여 재등장하지 않도록 처리한다(resourcemgr.py:1070, scheduler.py:239, event_queue.py:7, docs/SUSPEND_RESUME_RULES.md:4).
- **동일 die ERASE 금지**: suspend axis 가 열려 있을 때 `reserve` 는 동일 die 의 ERASE 예약을 거부하여 규칙 위반을 방지한다(resourcemgr.py:575, docs/SUSPEND_RESUME_RULES.md:5).
- **Resume 허용 조건**: `reserve`/`commit` 흐름은 `ERASE_RESUME` 요청 시 axis 상태를 검사해 suspend 중일 때만 허용하고, 성공 시 axis 플래그를 닫는다(resourcemgr.py:671, resourcemgr.py:1200, docs/SUSPEND_RESUME_RULES.md:6).
- **남은 시간으로 재개**: Resume 커밋 이후 ResourceManager 가 `resume_from_suspended_axis`를 통해 메타를 ongoing 으로 복귀시키며 새 `start_us=now` 와 `end_us=now+remaining_us` 를 설정하고, Scheduler 는 기존 OP_END 이벤트를 같은 `op_uid` 로 새로운 시각에 재삽입한다(resourcemgr.py:1135, scheduler.py:679, docs/SUSPEND_RESUME_RULES.md:6).
- **종료 시 반복 중단**: OP_END 처리 시 AddressManager 적용과 함께 해당 `op_uid` 를 폐기해 더 이상 suspend/resume 대상이 되지 않도록 하고, `remaining_us` 를 None 으로 비워 재진입을 막는다(scheduler.py:239, addrman.py:607, resourcemgr.py:1070, docs/SUSPEND_RESUME_RULES.md:7).

### PROGRAM 규칙 대응
- **ERASE suspend 와 병행 허용**: Scheduler 는 프로포저가 다른 target 의 PROGRAM 을 계속 제안할 수 있게 유지하되, ResourceManager 가 die 단위 axis 상태를 분리 추적하여 상호 간섭을 방지한다(resourcemgr.py:628, resourcemgr.py:1199, docs/SUSPEND_RESUME_RULES.md:9).
- **PROGRAM resume 선행**: Alt C 에서는 PROGRAM axis 메타가 원 작업의 `op_uid` 를 보존하므로, Scheduler 가 ERASE_RESUME 를 시도하기 전에 PROGRAM axis 가 비어있는지 확인하고, 필요 시 reserve 단계에서 실패 이유를 반환하도록 ResourceManager 규칙을 확장한다(resourcemgr.py:671, resourcemgr.py:1153, docs/SUSPEND_RESUME_RULES.md:9).
- **PROGRAM 전용 suspend/resume 흐름**: ERASE 와 동일한 이벤트 재예약 전략을 적용하되, PROGRAM 의 OP_END 만 AddressManager 에 반영되도록 `_am_apply_on_end` 의 commit 화이트리스트를 유지한다(scheduler.py:324, scheduler.py:679, addrman.py:607, docs/SUSPEND_RESUME_RULES.md:8).

### EventQueue 보완 포인트
- Alt C 요구사항을 충족하려면 EventQueue 가 `(op_uid, kind)` 로 항목을 검색·갱신하는 API 를 제공해야 하며, 현재 append/sort 기반 구조만으로는 suspend 시점에 기존 OP_END 를 조용히 무력화할 수 없다(event_queue.py:7).

### AddressManager 영향
- OP_END 가 한 번만 발생하면 `apply_pgm` 의 중복 호출 위험이 제거되어 programmed page 증가가 안정된다(addrman.py:607).

## 코드 참조
- `scheduler.py:239` – `_handle_op_end` 가 모든 OP_END 를 처리하며 AddressManager 에 상태를 반영.
- `scheduler.py:580` – commit 시 `register_ongoing` 호출로 메타를 등록.
- `scheduler.py:679` – `_emit_op_events` 가 OP_START/OP_END 를 push.
- `resourcemgr.py:628` – suspend 커밋 시 axis 상태와 meta 이동 처리.
- `resourcemgr.py:1070` – `move_to_suspended_axis` 가 remaining_us 를 기록.
- `resourcemgr.py:1135` – `resume_from_suspended_axis` 가 메타를 복귀.
- `event_queue.py:7` – EventQueue 가 단순 리스트 정렬로 이벤트를 관리.
- `addrman.py:607` – `apply_pgm` 이 블록 등장 횟수만큼 page 수를 증가.
- `docs/SUSPEND_RESUME_RULES.md:1` – ERASE/PROGRAM suspend-resume 규칙 정의.

## 아키텍처 인사이트
- Suspend/Resume 을 완전히 RM 메타 중심으로 재설계하면 Scheduler 는 이벤트 재스케줄만 담당하고, chain stub 특수 로직을 제거할 수 있다.
- EventQueue 가 식별자 기반 업데이트를 지원하면 suspend/resume 뿐 아니라 기타 장기 실행 작업의 재타이밍에도 활용 가능하다.
- Axis 별 상태(_erase_susp, _pgm_susp) 와 `_ongoing_ops` 메타에 `op_uid` 를 저장하면 다중 suspend 재개에서도 추적이 단순해진다.

## 역사적 맥락
- `research/2025-09-22_00-22-10_resume_stub_rework.md` – 현행 체인 스텁 대안 비교.
- `research/2025-09-18_12-51-24_resume_stub_remaining_us_meta.md` – remaining_us 소진 문제와 메타 동기화 실험.

## 관련 연구
- `research/2025-09-18_01-57-32_suspend_resume_apply_pgm_repeat.md`
- `research/2025-09-18_08-13-53_suspend_resume_addr_state.md`

## 미해결 질문
- EventQueue 에 식별자 기반 remove/reschedule 를 도입할 때, 정렬 비용과 우선 순위 충돌을 어떻게 최소화할 것인가? -> (검토완료) remove 대신 OP_END 이벤트 처리 단계에서 suspend 대상이 된 operation 은 skip
- `op_uid` 생성·전파를 proposer, scheduler, resourcemgr 사이에서 일관되게 유지하는 최적의 위치는 어디인가? -> (검토완료) `2025-09-22_01-06-36_op_uid_generation_strategy.md` 문서 참고
- Suspend 중인 작업이 재차 suspend 될 때 remaining_us 누락이나 double-free 를 방지하기 위한 회귀 테스트 커버리지는 어떻게 구성해야 하는가? -> (검토완료) `2025-09-22_01-07-35_repeat_suspend_remaining_us_regression.md` 문서 참고

