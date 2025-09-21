---
date: 2025-09-22T01:06:36.194499+09:00
researcher: Codex
git_commit: f352a740151d2d58d11967905f1bfb2144c06f4b
branch: main
repository: nandseqgen_v2
topic: "op_uid propagation locus"
tags: [research, scheduler, resourcemgr, proposer, suspend-resume]
status: complete
last_updated: 2025-09-22
last_updated_by: Codex
last_updated_note: "미해결 질문 답변 반영"
---

# 연구: op_uid propagation locus

**Date**: 2025-09-22T01:06:36.194499+09:00  
**Researcher**: Codex  
**Git Commit**: f352a740151d2d58d11967905f1bfb2144c06f4b  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
`op_uid` 생성·전파를 proposer, scheduler, resourcemgr 사이에서 일관되게 유지하는 최적의 위치는 어디인가?

## 요약
- `op_uid` 를 Scheduler 계층에서 단일 생성·할당하고 ResourceManager 메타에 저장하도록 하면 suspend/resume 를 포함한 전체 수명주기에서 동일 식별자를 보장할 수 있다.
- ResourceManager 는 기존 `op_id` 필드를 `op_uid` 로 치환하여 ongoing/suspended 메타와 공개 API 모두에서 동일 식별자를 돌려주고, Scheduler 는 해당 값을 이벤트 payload 와 재예약 경로에 재사용한다.
- Proposer 는 stateless 하므로 UID 생성 책임을 두기 어렵고, InstrumentedScheduler 는 Scheduler 가 부여한 UID 를 활용하도록 변경하는 것이 로그 일관성을 유지하는 가장 단순한 경로다.

## 상세 발견

### Proposer
- `ProposedOp`/`ProposedBatch` DTO 는 UID 필드를 갖지 않고 순수 데이터만 다룸 (`proposer.py:12`).
- Proposer 는 suspend/resume 로 재생성되는 작업을 관찰하지 못해 UID 를 안정적으로 재사용할 수 없다.

### Scheduler
- `_propose_and_schedule` 는 commit 된 작업 메타를 `resv_records` 로 수집하지만 UID 를 부여하지 않는다 (`scheduler.py:500`).
- `register_ongoing` 호출이 항상 `op_id=None` 을 전달해 ResourceManager 메타에 식별자가 남지 않는다 (`scheduler.py:588`).
- suspend 이후 체인 스텁 구성 시 `_suspended_ops_*` 에서 되돌려받은 `op_id` 를 `op_uid` 처럼 재사용하려 하지만 값이 항상 `None` 이라 효력이 없다 (`scheduler.py:533-541`).
- 이벤트 payload (`_emit_op_events`) 에 `op_uid` 가 포함되지 않아 OP_END dedupe 혹은 로그 상관 분석에 활용할 수 없다 (`scheduler.py:620-638`).

### ResourceManager
- `_OpMeta` 의 식별자 필드는 `op_id` 로 명명되어 있으나 Scheduler 가 값을 넣지 않아 비어있다 (`resourcemgr.py:121-130`).
- `register_ongoing`, `move_to_suspended_axis`, `resume_from_suspended_axis` 등 suspend/resume 경로는 `op_id` 로 대상을 찾도록 설계되어 있어 UID 를 저장하면 즉시 재사용 가능하다 (`resourcemgr.py:1038-1135`).
- `suspended_ops_*`/`ongoing_ops` 공개 API 역시 `op_id` 필드를 그대로 내보내므로 UID 전파 지점으로 활용할 수 있다 (`resourcemgr.py:934-1007`).

### InstrumentedScheduler / Exporters
- InstrumentedScheduler 는 자체 `_next_uid` 카운터를 사용해 로그 전용 UID 를 생성하여 core 의 UID 와 불일치가 발생한다 (`main.py:97-119`).
- `export_operation_sequence` 등 출력물은 `op_uid` 를 primary key 로 사용하므로 core 에서 부여한 UID 를 그대로 소비하는 편이 suspend/resume 을 추적하기 쉽다 (`main.py:548-611`).

## 코드 참조
- `proposer.py:12` – ProposedOp 구조에 UID 부재.
- `scheduler.py:500` – commit 대상 메타 생성 지점.
- `scheduler.py:588` – ResourceManager.register_ongoing 호출 시 `op_id=None` 전달.
- `scheduler.py:533-541` – suspend 메타에서 가져온 `op_id` 를 반복 사용하려는 로직.
- `scheduler.py:620-638` – `_emit_op_events` 가 UID 없이 이벤트를 push.
- `resourcemgr.py:121-130` – `_OpMeta` 정의와 `op_id` 필드.
- `resourcemgr.py:1038-1135` – ongoing/suspended 전환 경로에서 식별자 사용.
- `main.py:97-119` – InstrumentedScheduler 의 독립 UID 생성.
- `main.py:548-611` – `op_uid` 를 기반으로 패턴 export 를 그룹화.

## 아키텍처 인사이트
- Scheduler 는 commit 성공 여부와 suspend/resume 트리거를 모두 관장하므로 UID 생성을 중앙집중화하기 가장 적합하다.
- ResourceManager 는 메타를 스택 형태로 보관하므로 UID 를 저장하면 resume 후 동일 ID 를 자연스럽게 되돌려줄 수 있다.
- Proposer/Exporter 는 UID 소비자 역할에 집중하도록 두고, core 계층에서 UID 를 한 번만 생성·전파하면 중복 로직을 줄일 수 있다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-18_01-57-32_suspend_resume_apply_pgm_repeat.md` – suspend/resume 체인에서 동일 작업을 식별하기 위한 UID 필요성을 최초로 제기.
- `research/2025-09-22_00-38-35_resume_stub_alt_c_rule_mapping.md` – Alt C 접근의 전제 조건으로 `op_uid` 재사용을 명시.

## 관련 연구
- `research/2025-09-22_00-22-10_resume_stub_rework.md` – 체인 스텁 제거 대안 A/B/C 비교.
- `research/2025-09-18_12-51-24_resume_stub_remaining_us_meta.md` – remaining_us 메타 동기화 문제 분석.

## 후속 연구 2025-09-22T01:24:07.810786+09:00
- Scheduler 전역 monotonic 카운터로 commit 순서대로 `op_uid` 를 부여하고, suspend/resume 로 복귀하는 작업은 동일 UID 를 재사용하도록 결정했다. 이 정책은 멀티-오퍼레이션 배치에서도 재현 가능한 정렬 순서를 제공한다.
- READ 등 경량 작업도 Scheduler 가 동일 방식으로 UID 를 부여하되 ResourceManager 에 메타를 남기지 않고 이벤트/로그 계층에서만 소비하도록 해 일관된 추적성을 유지하기로 했다.

## 미해결 질문
- 없음
