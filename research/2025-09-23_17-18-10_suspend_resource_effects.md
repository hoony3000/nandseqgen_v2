---
date: 2025-09-23T17:18:10.287670+09:00
researcher: Codex
git_commit: ab6376983ce9df20c6b775e19b2cd28f8e800e07
branch: main
repository: nandseqgen_v2
topic: "SUSPEND 동작 리소스 변화 조사"
tags: [research, codebase, resourcemgr, suspend-resume]
status: complete
last_updated: 2025-09-23
last_updated_by: Codex
---

# 연구: SUSPEND 동작 리소스 변화 조사

**Date**: 2025-09-23T17:18:10.287670+09:00
**Researcher**: Codex
**Git Commit**: ab6376983ce9df20c6b775e19b2cd28f8e800e07
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
SUSPEND 동작 시 기존에 예약됐던 ERASE/PROGRAM 동작이 ResourceManager.commit / ResourceManager.reserve 를 통해 가진 resource 예약과 상태 변경(예: bus, latch_state)이 어떤 부분에서 SUSPEND 동작으로 바뀌고, 어떤 부분은 그대로 유지되는가?

## 요약
- SUSPEND 계열은 cfg 의 `instant_resv` 로 인해 `reserve` 에서 즉시 경로를 타며 bus 세그먼트와 상태 타임라인만 추가한다(`resourcemgr.py:503-537`, `config.yaml:556-595`).
- `commit` 는 축적된 plane/bus 예약을 변경하지 않으며, SUSPEND 시점에는 축소 없이 그대로 유지된다(`resourcemgr.py:590-637`).
- SUSPEND 처리 시 `_erase_susp`/`_pgm_susp` 축 활성화, ongoing 메타의 suspended 스택 이동, `_StateTimeline` CORE_BUSY 구간 truncate 만 수행된다(`resourcemgr.py:637-680`, `resourcemgr.py:1096-1134`).
- 기존 latch, plane exclusivity, bus 예약은 유지되며 SUSPEND 자체는 latch 를 new 로 세팅하지 않는다(`resourcemgr.py:525-533`, `resourcemgr.py:590-637`).
- Suspend 상태는 `exclusions_by_suspend_state` 매핑을 통해 규칙 차단에만 반영되고, 리소스 해제는 별도로 하지 않는다(`resourcemgr.py:1644-1674`, `config.yaml:2317-2321`).

## 상세 발견

### ResourceManager.reserve instant 경로
- cfg 에서 `ERASE_SUSPEND`/`PROGRAM_SUSPEND` 가 `instant_resv: true`, scope `DIE_WIDE` 로 정의되어 즉시 경로가 활성화된다(`config.yaml:556-595`).
- instant 경로는 bus 충돌과 latch 차단만 검사하고 plane 창, die exclusivity, multiplicity 창을 만들지 않는다. 대신 `txn.bus_resv` 와 `txn.st_ops` 만 업데이트한다(`resourcemgr.py:508-537`).
- `_latch_kind_for_base` 는 SUSPEND 계열에 대응 값을 돌려주지 않아 latch 상태는 변하지 않는다(`resourcemgr.py:521-533`, `resourcemgr.py:1458-1473`).

### ResourceManager.commit 의 SUSPEND 처리
- 커밋 시 기존 plane/bus/exclusion/latch 구조에 새로 추가만 수행하며 SUSPEND 예약은 plane 창이 없으므로 아무 것도 추가하지 않는다(`resourcemgr.py:590-607`).
- SUSPEND detection 후 축 상태를 열고 `_suspended_ops_*` 스택으로 메타를 옮기며 잔여 시간을 계산한다(`resourcemgr.py:637-666`, `resourcemgr.py:1096-1134`).
- `_StateTimeline.truncate_after` 로 대상 die/plane 의 ERASE/PROGRAM CORE_BUSY 구간을 suspend 시점에서 잘라낸다. 이후 새 SUSPEND 구간이 타임라인에 남는다(`resourcemgr.py:637-680`, `resourcemgr.py:45-74`).
- ERASE/PROGRAM 의 과거 plane/bus 예약 기록은 commit 경로에서 손대지 않으므로 `_plane_resv` 와 `_bus_resv` 항목은 원래 종료 시각까지 유지된다(`resourcemgr.py:590-607`).

### Suspend 상태와 규칙 평가
- Suspend 발동 후 `_erase_susp` 또는 `_pgm_susp` 가 활성 상태로 남아, 후속 예약 시 `_rule_forbid_on_suspend` 가 exclusion group 을 체크한다(`resourcemgr.py:1644-1674`, `config.yaml:2317-2321`).
- 이 규칙은 리소스를 직접 변형하지 않고 PRD 레벨의 정책만 반영한다. 따라서 plane/bus/latch 해제는 별도 경로가 필요하다.

### 기존 리소스가 유지되는 지점
- 기존 프로그램/erase 예약이 추가했던 plane windows 는 그대로 남아 새 예약을 차단한다는 것이 선행 연구에서 확인되었다(`research/2025-09-22_11-32-44_resume_program_overlap.md`).
- latch 해제는 `release_on_dout_end`/`release_on_exec_msb_end` 등의 외부 호출로만 일어나며 SUSPEND 처리는 latch 사전 `_latch` 를 수정하지 않는다(`resourcemgr.py:703-723`).
- `_bus_resv` 는 SUSPEND 의 ISSUE 구간(0.4us)만 새로 추가하며, 기존 PROGRAM/ERASE bus 창은 유지된다(`resourcemgr.py:522-563`).

## 코드 참조
- `resourcemgr.py:503` – `reserve` instant 경로가 plane 창을 건너뛰는 지점.
- `resourcemgr.py:637` – SUSPEND 커밋 시 축 상태 전환과 타임라인 truncate.
- `resourcemgr.py:1096` – `_ongoing_ops` 메타를 축별 suspended 스택으로 이동.
- `resourcemgr.py:1644` – suspend 상태 기반 rule 차단 로직.
- `config.yaml:556` – ERASE/PROGRAM_SUSPEND 의 instant_resv 설정.
- `config.yaml:2317` – `exclusions_by_suspend_state` 그룹 매핑.
- `research/2025-09-22_11-32-44_resume_program_overlap.md` – plane 예약이 복원되지 않는 선행 분석.

## 아키텍처 인사이트
- Suspend 는 리소스 해제가 아닌 "타임라인 단축 + 정책 플래그" 로 구현되어 있어, 실제 하드웨어의 plane/bus 해제 효과를 모사하지 못한다.
- instant 경로 도입으로 suspend 명령이 admission 큐를 우회하지만, 기존 예약 데이터가 남아 있어 follow-up 동작이 여전히 차단될 수 있다.
- 축 상태 기반 규칙과 리소스 데이터 구조가 분리되어 있으므로, suspend 이후 동작을 허용하려면 plane/bus/latch 레벨의 별도 정리가 필요하다.

## 역사적 맥락
- `research/2025-09-22_11-32-44_resume_program_overlap.md` 는 resume 시 plane 예약이 복원되지 않아 중첩 예약이 발생함을 기록하고 있으며, Suspend 에서도 유사한 리소스 보존이 문제로 이어질 수 있음을 시사한다.

## 관련 연구
- `research/2025-09-22_11-32-44_resume_program_overlap.md`

## 미해결 질문
- Suspend 직후 plane/bus 예약을 단축하거나 해제해야 하는지 사양 확인이 필요하다. 현재 구현은 유지 상태이므로 스케줄러 측 완화가 필요한지 결정되지 않았다.
