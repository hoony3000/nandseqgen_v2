---
date: 2025-09-24T14:45:00.993629+09:00
researcher: Codex
git_commit: 36f1a384eac46638a4b1f7739f00fa2bc568098d
branch: main
repository: nandseqgen_v2
topic: "scheduler resume state transitions"
tags: [research, codebase, scheduler, resourcemgr]
status: complete
last_updated: 2025-09-24
last_updated_by: Codex
---

# 연구: scheduler resume state transitions

**Date**: 2025-09-24T14:45:00.993629+09:00
**Researcher**: Codex
**Git Commit**: 36f1a384eac46638a4b1f7739f00fa2bc568098d
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
Scheduler._propose_and_schedule 에서 RESUME 동작으로 suspended_ops 가 재예약될 때 어떤 state와 자원이 변경되는지 정리한다.

## 요약
- Scheduler 는 `PROGRAM_RESUME`/`ERASE_RESUME` commit 이후 `_handle_resume_commit`에서 재개 대상 die 를 찾고, ResourceManager 재개 결과를 기반으로 이벤트 큐와 재개 추적 상태(`_resumed_op_uids`, `_resume_expected_targets`)를 갱신한다.
- ResourceManager 는 `resume_from_suspended_axis` 호출 시 축적해둔 `_suspended_ops_*` 스택에서 메타를 꺼내 재시작 시각과 종료 시각을 재산정하고 `_ongoing_ops`에 되돌려 자원 점유를 복구한다.
- Resume 커밋은 `_pgm_susp` / `_erase_susp` 축 상태를 닫아 die 가 더 이상 suspend 모드로 간주되지 않도록 하여 새로운 제안이 같은 축에서 차단되지 않는다.

## 상세 발견

### Scheduler resume handling
- `scheduler.py:400` 에서 base 가 RESUME 계열인지 확인 후 대상 die 를 `targets`, `phase_hook` 또는 ResourceManager 의 대기 스택을 조회해 결정한다.
- `scheduler.py:467` 는 복귀한 op 의 `op_uid` 를 `_resumed_op_uids`와 `_resume_expected_targets`에 저장해 추후 `OP_END` 이벤트에서 재개 여부와 타겟 일치를 검증한다.
- `scheduler.py:481` 은 재개된 실제 op 의 종료 시각(`meta.end_us`)으로 `OP_END` 이벤트를 다시 큐잉하여 이벤트 플로우와 자원 해제를 재연결한다.

### ResourceManager resume path
- `resourcemgr.py:1166` 에 정의된 `move_to_suspended_axis` 는 suspend 시 `_ongoing_ops`에서 메타를 꺼내 남은 시간과 축 정보를 `_suspended_ops_*` 스택에 저장해 resume 준비 상태를 만든다.
- `resourcemgr.py:1227` 는 resume 시 스택에서 메타를 꺼내 재개 기준 시각을 양자화하고 남은 시간을 더해 종료 시각을 재계산한 뒤 `_ongoing_ops`에 다시 적재한다.
- `resourcemgr.py:745` 는 `PROGRAM_RESUME`/`ERASE_RESUME` 커밋이 축 상태 `_pgm_susp`/`_erase_susp` 를 닫아 해당 die 의 suspend 플래그를 해제하도록 한다.

## 코드 참조
- `scheduler.py:400` - `_handle_resume_commit` 이 재개 대상 die 탐색 및 ResourceManager 호출을 수행.
- `scheduler.py:467` - 재개된 op 의 UID 와 기대 타겟을 추적하여 이벤트 검증에 사용.
- `scheduler.py:481` - 재개된 op 의 `OP_END` 이벤트를 큐에 푸시하여 실행을 마무리하도록 스케줄링.
- `resourcemgr.py:1166` - suspend 시 `_ongoing_ops` → `_suspended_ops_*` 이동과 남은 시간 계산.
- `resourcemgr.py:1227` - resume 시 스택 pop, 타이밍 재설정, `_ongoing_ops` 복원.
- `resourcemgr.py:745` - resume 커밋이 축 suspend 상태를 해제하여 die-level 상태를 정상화.

## 아키텍처 인사이트
- Scheduler 는 resume 명령 자체보다 underlying meta 의 이어달리기(event 재큐잉, UID 추적)에 집중하며, 실제 상태 복구는 ResourceManager 에 위임한다.
- ResourceManager 의 suspend/resume 스택과 `_ongoing_ops` 는 die 축별 비동기 재개를 지원하는 핵심 자료구조로, 양자화된 시간 업데이트를 통해 시뮬레이터의 일관된 시간축을 유지한다.
- 축 suspend 상태가 commit 경로에서 즉시 해제되기 때문에, 동일 die 에 대한 후속 제안이 resume 직후 재개된 작업과 충돌하지 않도록 상위 스케줄링이 정교하게 조정된다.

## 역사적 맥락(thoughts/ 기반)
- thoughts/ 디렉터리가 존재하지 않아 관련 역사적 메모를 확인하지 못했다.

## 관련 연구
- 없음.

## 미해결 질문
- resume 후 `_st` 타임라인이 추가적으로 확장되지 않는 설계가 의도된 것인지(재개 시 CORE_BUSY 세그먼트 재삽입 필요성) 검증이 필요하다.
