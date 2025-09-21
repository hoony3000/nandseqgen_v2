---
date: 2025-09-22T01:07:35.793699+09:00
researcher: Codex
git_commit: f352a740151d2d58d11967905f1bfb2144c06f4b
branch: main
repository: nandseqgen_v2
topic: "Suspend 재중단 시 remaining_us 및 double-free 회귀 테스트 전략"
tags: [research, testing, suspend-resume, resource-manager, scheduler]
status: complete
last_updated: 2025-09-22
last_updated_by: Codex
last_updated_note: "미해결 질문 답변 추가"
---

# 연구: Suspend 중인 작업이 재차 suspend 될 때 remaining_us 누락이나 double-free 를 방지하기 위한 회귀 테스트 커버리지는 어떻게 구성해야 하는가?

**Date**: 2025-09-22T01:07:35.793699+09:00  
**Researcher**: Codex  
**Git Commit**: f352a740151d2d58d11967905f1bfb2144c06f4b  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
Suspend 중인 작업이 재차 suspend 될 때 remaining_us 누락이나 double-free 를 방지하기 위한 회귀 테스트 커버리지는 어떻게 구성해야 하는가?

## 요약
- `ResourceManager.move_to_suspended_axis` 가 `remaining_us` 를 계산하지만 이후 `resume_from_suspended_axis` 가 `start_us/end_us` 를 갱신하지 않아 반복 suspend 시 잔여 시간이 0 또는 음수로 붕괴할 수 있다 (`resourcemgr.py:628`, `resourcemgr.py:1094`).
- Scheduler 의 resume 체인 로직은 동일 `op_uid` 에 대해 OP_END 이벤트를 다시 push 하며 dedupe 가 없어 double-free(중복 완료 처리)가 발생할 여지를 남긴다 (`scheduler.py:533`, `scheduler.py:575`, `event_queue.py:18`).
- `tests/` 디렉터리에 suspend/resume 관련 검증이 전혀 없어 회귀를 포착할 장치가 없다.
- 회귀 테스트는 (1) ResourceManager 단위에서 remaining_us 재계산과 스택 무결성을 검증하고, (2) Scheduler-EventQueue 경로에서 OP_END 중복 방지와 remaining_us 유지 여부를 통합 시험하며, (3) 필요 시 시뮬레이션 로그를 분석하는 엔드투엔드 검증을 포함하는 3단 구성으로 설계한다.

## 상세 발견

### ResourceManager 상태 흐름
- Suspend 커밋 시 `ResourceManager.commit` 이 `move_to_suspended_axis` 를 호출해 최신 ongoing 메타를 pop 하고 `remaining_us = meta.end_us - now` 로 계산한다 (`resourcemgr.py:628`, `resourcemgr.py:1094`).
- `resume_from_suspended_axis` 는 스택에서 meta 를 꺼내 `_ongoing_ops` 로 되돌릴 뿐 잔여 시간에 맞춰 `start_us/end_us` 를 재설정하거나 `remaining_us` 를 초기화하지 않는다 (`resourcemgr.py:1135`, `resourcemgr.py:1150`).
- 동일 메타가 다시 suspend 되면 `meta.end_us` 가 초기값에 머물러 `remaining_us` 가 0 또는 음수로 계산되어 잔여 구간이 소실되는 사례가 기존 연구 로그에서 확인되었다 (`research/2025-09-18_12-51-24_resume_stub_remaining_us_meta.md`).

### Scheduler 체인 동작
- `_propose_and_schedule` 는 `PROGRAM_RESUME` 성공 직후 마지막 suspended 메타를 읽어 `_build_core_busy_stub` 으로 동일 대상의 잔여 CORE_BUSY 작업을 재등록한다 (`scheduler.py:520`, `scheduler.py:533`).
- 체인 스텁도 `_emit_op_events` 를 호출하여 OP_START/OP_END 이벤트를 다시 push 하고, EventQueue 는 단순 정렬만 수행해 중복 이벤트를 유지한다 (`scheduler.py:575`, `event_queue.py:18`).
- 체인 커밋 후 `resume_from_suspended_axis` 로 meta 를 복귀시키지만, meta 의 `end_us/remaining_us` 는 그대로여서 다음 suspend 시 값들이 누락된다 (`scheduler.py:648`, `resourcemgr.py:1150`).

### 테스트 공백
- `tests/` 경로가 비어 있어 suspend/resume 경로에 대한 단위 및 통합 검증이 전무하다 (`tests`).
- 기존 instrumentation 연구는 수동 스크립트(`tools/analyze_resume_remaining_us.py`)에 의존하며 자동화된 회귀 검증으로 연결되지 않았다.

## 테스트 커버리지 제안
- **ResourceManager 단위 테스트 (pytest)**
  - 시나리오: `register_ongoing` → 첫 suspend (`move_to_suspended_axis`) → resume (`resume_from_suspended_axis`) → 부분 실행 이후 두 번째 suspend.
  - 검증: 두 번째 suspend 시 `remaining_us` 가 첫 suspend 대비 감소하지만 `> 0` 을 유지하고, `suspend_time_us` 가 최신 시각으로 갱신되며 `_suspended_ops_program` 길이가 1을 유지한다 (`resourcemgr.py:1038`, `resourcemgr.py:1094`, `resourcemgr.py:1150`).
  - 예상 실패(회귀 감시): meta.end_us 가 업데이트되지 않으면 `remaining_us` 가 0으로 떨어져 테스트가 실패한다.
- **스택 무결성 테스트**
  - 시나리오: 동일 작업에 대해 suspend 를 연속 두 번 호출하거나 resume 을 중복 호출.
  - 검증: 두 번째 suspend 는 noop 이며 스택 길이가 증가하지 않고, resume 중복 호출은 `_ongoing_ops` 에 메타를 중복 삽입하지 않는다 (`resourcemgr.py:1095`, `resourcemgr.py:1150`).
  - Double-free 방지: pop 연산 카운트가 push 보다 많아지지 않는지 `len(ongoing_ops)`/`len(suspended_ops)` 차이로 확인.
- **Scheduler-EventQueue 통합 테스트**
  - 구성: 경량 proposer 더블(mock)로 `PROGRAM` → `PROGRAM_SUSPEND` → `PROGRAM_RESUME` → `PROGRAM_SUSPEND` 흐름을 주입.
  - 관찰: `EventQueue._q` 를 스파이하여 동일 `op_uid`+`kind="OP_END"` 조합이 1회만 남는지, `ResourceManager.suspended_ops_program` 의 `remaining_us` 가 반복 suspend 때 양수를 유지하는지 확인 (`scheduler.py:533`, `scheduler.py:575`, `event_queue.py:18`).
  - 필요 모킹: proposer stub, deterministic config(단일 die/plane)로 quantize 영향 최소화.
- **엔드투엔드 로그 검증 (선택)**
  - `main.py` 실행을 `SR_REMAINING_US_ENABLE` 등 instrumentation 플래그와 결합해 JSONL 로그를 수집하고 pytest 내에서 분석.
  - 검증 지표: 동일 `op_uid` 의 suspend event 가 2회 이상 등장할 때 `remaining_us` 가 감소-양수 패턴을 유지하고, OP_END 로그가 중복되지 않는지 확인 (`tools/analyze_resume_remaining_us.py`).

## 접근 방식 비교
1. **ResourceManager 단위 테스트 중심**
   - 장점: 빠르고 결정적이며 외부 의존성이 없다.
   - 단점: Scheduler 와 EventQueue 의 실사용 경로를 직접 검증하지 못한다.
   - 위험: 실제 체인 로직이 변할 경우 mock 시나리오가 현실과 어긋날 수 있다.
2. **Scheduler 통합 테스트**
   - 장점: suspend/resume 체인의 실제 상호작용(OP_END 재큐잉, meta 갱신)을 포괄한다.
   - 단점: proposer/validator 모킹이 필요하고 유지 보수 부담이 크다.
   - 위험: 시간 정렬(quantize)이나 랜덤 훅이 불안정하면 테스트가 플래키해질 수 있다.
→ 결론: 최소 한 개의 RM 단위 테스트 + 한 개의 Scheduler 통합 테스트를 우선 도입하고, 로그 기반 엔드투엔드 검증은 장기 보강용으로 채택한다.

## 코드 참조
- `resourcemgr.py:628` – suspend 커밋 시 axis 상태 열고 meta 이동.
- `resourcemgr.py:1094` – `move_to_suspended_axis` 가 `remaining_us` 계산.
- `resourcemgr.py:1150` – `resume_from_suspended_axis` 가 meta 를 다시 ongoing 으로 push.
- `scheduler.py:533` – resume 체인이 잔여 CORE_BUSY 작업을 재등록.
- `scheduler.py:575` – 체인 스텁에서도 `_emit_op_events` 로 OP_END 재큐잉.
- `scheduler.py:648` – 체인 커밋 후 `resume_from_suspended_axis` 호출.
- `event_queue.py:18` – EventQueue 가 append/sort 만 수행해 OP_END 중복을 막지 못함.

## 아키텍처 인사이트
- remaining_us 정확성을 보장하려면 resume 시점에 meta 의 `start_us/end_us` 를 재설정하고 `remaining_us` 를 초기화하는 API 가 필요하다.
- EventQueue 레벨에서 `(op_uid, kind)` 기반 dedupe 또는 update API 가 없으면 double-free 방지는 Scheduler/GUARD 레이어 테스트로만 확보된다.
- 테스트 픽스처에 `Address(die, plane, block)` 와 단일 CORE_BUSY state stub 을 재사용하면 suspend/resume 흐름을 손쉽게 재현할 수 있다.

## 역사적 맥락
- `research/2025-09-22_00-22-10_resume_stub_rework.md` – 체인 스텁 재설계 필요성과 remaining_us 소실 사례.
- `research/2025-09-18_12-51-24_resume_stub_remaining_us_meta.md` – remaining_us=0 재현 로그와 임시 패치 실험.
- `research/2025-09-18_08-13-53_suspend_resume_addr_state.md` – OP_END 중복과 addr_state 이상 증가 분석.

## 관련 연구
- `research/2025-09-22_00-38-35_resume_stub_alt_c_rule_mapping.md`
- `research/2025-09-18_01-57-32_suspend_resume_apply_pgm_repeat.md`

## 미해결 질문
- Scheduler 통합 테스트에서 proposer mock 을 어떻게 구성하면 최소 노력으로 반복 suspend 시퀀스를 재현할 수 있을까? (답변은 `## 후속 연구 2025-09-22T01:10:00+09:00` 참고)
- EventQueue 측면 보강(핸들 기반 update/dedupe)을 구현할 경우 테스트 전략을 어떻게 조정해야 할지 추가 검토가 필요하다. (답변은 `## 후속 연구 2025-09-22T01:10:00+09:00` 참고)

## 후속 연구 2025-09-22T01:10:00+09:00
- **통합 테스트용 proposer mock**: 단일 die/plane, 고정 CORE_BUSY duration 을 갖는 `PROGRAM`/`PROGRAM_SUSPEND`/`PROGRAM_RESUME` 시퀀스를 반환하도록 `Batch` 더블을 구성하고, `hook.label` 을 기반으로 순차적으로 다음 op 를 내보내게 하면 된다. RNG 없이 deterministic 리스트를 소비하도록 해 `Scheduler.tick()` 두 번으로 `suspend→resume→재-suspend` 흐름이 재현된다.
- **EventQueue API 보강 대비 테스트 전략**: `(op_uid, kind)` 기반 remove/update 기능이 추가되면 통합 테스트에서 큐 직접 검사를 제거하고, 대신 새 API 호출 후 `ResourceManager` 메타와 이벤트 개수가 일치하는지를 검증하도록 어설션을 전환한다. 기존 회귀 테스트는 backward-compat 플래그로 유지해 구버전 경로도 검증한다.
