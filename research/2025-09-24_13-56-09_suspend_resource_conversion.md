---
date: 2025-09-24T13:56:09+09:00
researcher: Codex
git_commit: 36f1a384eac46638a4b1f7739f00fa2bc568098d
branch: main
repository: nandseqgen_v2
topic: "SUSPEND 시 자원 예약 전환 분석"
tags: [research, resourcemgr, scheduler, suspend-resume]
status: complete
last_updated: 2025-09-24
last_updated_by: Codex
---

# 연구: SUSPEND 시 자원 예약 전환 분석

**Date**: 2025-09-24T13:56:09+09:00
**Researcher**: Codex
**Git Commit**: 36f1a384eac46638a4b1f7739f00fa2bc568098d
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
SUSPEND 동작 시 기존에 예약됐던 ERASE/PROGRAM 동작이 ResourceManager.commit / ResourceManager.reserve 를 통해 가진 resource 예약과 상태 변경(예: bus, latch_state)이 어떤 부분에서 SUSPEND 동작으로 바뀌고, 어떤 부분은 그대로 유지되는가?

## 요약
- `ERASE_SUSPEND`/`PROGRAM_SUSPEND` 는 `instant_resv` 기반이라 reserve 단계에서 bus·state 로그만 추가하고 plane/die 독점 창은 새로 만들지 않는다 (`config.yaml:556-594`, `resourcemgr.py:543-587`).
- commit 단계에서는 SUSPEND 자체의 상태 타임라인을 기록하면서 die 축 suspend state 를 열고, 최신 ongoing meta 를 axis 별 suspended 스택으로 이동시키며 CORE_BUSY 구간을 suspend 시각 이후로 잘라낸다 (`resourcemgr.py:668-744`, `resourcemgr.py:1180-1199`).
- 기존 ERASE/PROGRAM 이 가진 plane 예약과 latch 버킷은 commit 시 수정되지 않아 독점 창과 잠금은 원래 값이 유지되고, bus 창은 SUSPEND 용으로 한 구간이 추가된다 (`resourcemgr.py:611-655`, `resourcemgr.py:562-563`).
- Scheduler 는 tracking axis 가 있는 ERASE/PROGRAM 커밋에서만 ongoing 메타를 등록하므로 SUSPEND 자체는 별도 리소스 트래킹을 만들지 않지만, commit 중 `move_to_suspended_axis` 가 기존 메타를 꺼내 남은 시간을 저장한다 (`scheduler.py:235-242`, `scheduler.py:724-742`, `resourcemgr.py:1180-1193`).

## 상세 발견

### ResourceManager reserve 경로
- `ERASE_SUSPEND`/`PROGRAM_SUSPEND` 는 설정상 즉시 예약이며 (`config.yaml:556-594`), `reserve` 가 instant 분기에서 bus 구간만 추가하고 plane/die 창·배타 토큰·latch 를 건드리지 않는다 (`resourcemgr.py:548-585`).
- 이때 `txn.st_ops` 에도 SUSPEND 상태 시퀀스가 push 되어 이후 commit 에서 타임라인에 반영된다 (`resourcemgr.py:583-585`).

### Suspend commit 처리
- commit 루프는 affect_state=true 인 경우에만 타임라인을 갱신하므로, SUSPEND states(ISSUE, CORE_BUSY)가 그대로 기록된다 (`resourcemgr.py:668-672`).
- 동일 블록에서 die 축 suspend state 를 열고(`resourcemgr.py:702-708`), 중복 처리를 guard 한 뒤 `move_to_suspended_axis` 로 최신 ongoing 메타를 꺼내 남은 시간을 quantize 해 저장한다 (`resourcemgr.py:710-741`, `resourcemgr.py:1180-1193`).
- suspend 시점 이후의 원본 ERASE/PROGRAM CORE_BUSY 구간은 `_st.truncate_after` 로 절단되어 phase-key 조회 시 즉시 suspend 이후 상태로 전환된다 (`resourcemgr.py:734-741`).

### 유지되는 자원 상태
- `_plane_resv` 와 `_avail` 은 commit 중 새 창만 append 할 뿐 suspend 특례를 두지 않아 기존 ERASE/PROGRAM 이 예약해 둔 창이 그대로 남는다 (`resourcemgr.py:611-655`).
- latch 는 `_latch_kind_for_base` 가 SUSPEND 에 대응 값을 반환하지 않아 추가/해제 모두 일어나지 않는다 (`resourcemgr.py:564-575`, `resourcemgr.py:1555-1565`).
- bus 창은 SUSPEND 본연의 ISSUE 구간이 추가될 뿐 기존 ERASE/PROGRAM ISSUE 구간은 이미 종료된 상태로 유지된다 (`resourcemgr.py:562-563`).

### Scheduler 및 메타 이동
- Scheduler 는 base 명에 `SUSPEND` 가 포함되면 tracking axis 를 부여하지 않으므로 commit 이후 `register_ongoing` 대상에서 제외된다 (`scheduler.py:235-242`, `scheduler.py:724-742`).
- 그 대신 commit 내부에서 호출된 `move_to_suspended_axis` 가 ongoing 목록에서 meta 를 제거하고 남은 시간을 계산해 axis 별 suspended 스택으로 옮긴다 (`resourcemgr.py:1180-1199`). 이 동작은 테스트에서도 남은 시간이 누적 보존되는지 검증된다 (`tests/test_suspend_resume.py:66-102`).

## 코드 참조
- `config.yaml:556` – ERASE/PROGRAM_SUSPEND 가 `instant_resv: true` 로 정의됨.
- `resourcemgr.py:543` – instant reserve 분기에서 bus/state 만 예약하고 plane/die 창을 건너뜀.
- `resourcemgr.py:702` – commit 시 suspend axis 상태 열고 timeline truncate 실행.
- `resourcemgr.py:1180` – `move_to_suspended_axis` 가 remaining_us 계산 후 suspended 스택으로 이동.
- `resourcemgr.py:611` – plane 예약 창은 commit 시 append 되는 구조라 suspend 전 예약이 그대로 남음.
- `scheduler.py:235` – tracking axis 로 SUSPEND 를 제외.
- `scheduler.py:724` – tracking axis 존재할 때만 `register_ongoing` 수행.
- `tests/test_suspend_resume.py:66` – suspend 반복 시 remaining_us 갱신 테스트.
- `docs/SUSPEND_RESUME_RULES.md:1` – suspend/resume 동작 규칙 요약.

## 아키텍처 인사이트
- suspend 는 즉시 예약으로 설계되어 die/plane 독점 해제를 ResourceManager 가 직접 수행하지 않는다; 대신 suspend 축 상태와 timeline 절단으로 "논리적" 중단을 표현한다.
- plane 예약이 그대로 유지되므로 실제 자원 공유(unblocking)는 별도 개선이 필요하며, 기존 연구에서도 resume 중 중첩 예약 문제가 확인되었다 (`research/2025-09-22_11-32-44_resume_program_overlap.md`).
- latch 는 suspend 경로에서 전혀 개입하지 않으므로, latch 기반 상호 배제는 ERASE/PROGRAM 원본 수행 시점에만 영향을 준다.

## 역사적 맥락(thoughts/ 기반)
- `docs/SUSPEND_RESUME_RULES.md:1` 은 suspend 시 ongoing 메타를 suspended 목록으로 옮기고 remaining 시간을 저장해야 함을 명시한다. 현재 구현은 해당 규칙을 충족하지만 plane 창 유지 여부에 대한 추가 규정은 없다.

## 관련 연구
- `research/2025-09-22_11-32-44_resume_program_overlap.md` – resume 기간 중 plane 예약이 복원되지 않아 중첩이 허용되는 문제 분석.

## 미해결 질문
- suspend 시점에 plane 예약을 즉시 축소하거나 resume 시 재예약해야 하는지 여부가 남아 있으며, 중첩 허용 범위에 대한 사양 검토가 필요하다.
