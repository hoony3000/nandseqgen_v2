---
date: 2025-09-17T15:57:35.577471+09:00
researcher: Codex
git_commit: c9ccad1d70445ef62590f2e265450a3ef82958ce
branch: main
repository: nandseqgen_v2
topic: "Suspend-resume OP_END dedupe implementation plan"
tags: [research, scheduler, event-queue, suspend-resume, implementation-plan]
status: complete
last_updated: 2025-09-17
last_updated_by: Codex
last_updated_note: "CORE_BUSY 영향 평가 후속 연구 추가"
---

# 연구: Suspend-resume OP_END dedupe implementation plan

**Date**: 2025-09-17T15:57:35.577471+09:00  
**Researcher**: Codex  
**Git Commit**: c9ccad1d70445ef62590f2e265450a3ef82958ce  
**Branch**: main  
**Repository**: nandseqgen_v2

## 연구 질문
suspend→resume 체인에서 중복 OP_END 이벤트를 제거하고 실제 완료 시점만 한 번 적용하도록 구현할 방법을 정리하라.

## Problem 1-Pager
- **Background**: 기존 연구(`research/2025-09-17_02-14-45_suspend_resume_op_end_requeue.md`)는 suspend-resume 경로가 동일 작업에 대해 OP_END 이벤트를 두 번 큐잉해 `AddressManager.apply_pgm`이 중복 호출된다고 보고했다.
- **Problem**: 현재 스케줄러는 최초 예약과 resume 스텁 모두에서 `_emit_op_events`를 호출하며, EventQueue와 AddressManager는 중복을 식별하지 못한다.
- **Goal**: OP_END 이벤트를 단일 인스턴스로 유지하며 suspend-resume 체인을 안전하게 재스케줄할 수 있는 구현 대안을 도출한다.
- **Non-goals**: 실제 코드 수정이나 패치 적용, 시뮬레이션 실행.
- **Constraints**: 파일/함수 크기 제한, 기존 validation 전략과의 호환성, suspend 상태 메타데이터(`remaining_us`) 활용, Python 3.11 환경 및 ASCII.

## 요약
- 스케줄러는 commit 시점과 resume 체인 스텁에서 동일 `op_uid` 없이 `_emit_op_events`를 호출해 EventQueue에 중복 OP_END를 남긴다 (`scheduler.py:765`, `scheduler.py:827`, `scheduler.py:868`).
- ResourceManager 는 suspend 시 메타를 축적하지만 OP_END 시각을 갱신하지 않아, 원본 이벤트가 그대로 실행되고 나중 stub에서 재발행된 이벤트가 추가 실행된다 (`resourcemgr.py:574`, `resourcemgr.py:1062`).
- EventQueue 는 단순 정렬만 수행하며 중복 제거가 없고, AddressManager 는 이벤트 호출 횟수만큼 페이지 수를 증가시킨다 (`event_queue.py:17`, `addrman.py:607`).

## 상세 발견

### Scheduler
- commit 루프가 `_emit_op_events(rec)`를 호출해 최초 OP_END 이벤트를 스케줄한다 (`scheduler.py:765`).
- resume 체인 스텁 생성 시 `_emit_op_events(rec2)`가 동일 타겟으로 다시 OP_END 를 큐잉하며 `source="RESUME_CHAIN"`만 다르다 (`scheduler.py:827`).
- `_emit_op_events` 는 `op_uid` 가 존재할 때만 payload 에 포함시키며, validation 비활성 시 None 이라 dedupe에 사용할 키가 없다 (`scheduler.py:868`).

### ResourceManager
- `commit` 은 suspend된 작업을 `_suspended_ops_*` 로 이동시키지만 `end_us` 를 새 타임라인에 맞춰 갱신하지 않는다 (`resourcemgr.py:574`).
- `move_to_suspended_axis` 는 잔여 시간을 `remaining_us` 로 저장하나, resume 시 기존 OP_END 이벤트를 제거하거나 재예약하지 않는다 (`resourcemgr.py:1062`).
- resume 완료 후 `resume_from_suspended_axis` 는 메타를 ongoing 으로 되돌리지만 원본 `end_us` 그대로라 재차 suspend 될 때 또다시 중복 stub 를 만든다 (`resourcemgr.py:1125`).

### EventQueue & AddressManager
- EventQueue `push` 는 (time, priority, seq) 단순 정렬만 수행해 동일 `op_uid` 의 중복 엔트리를 허용한다 (`event_queue.py:17`).
- `AddressManager.apply_pgm` 은 등장 횟수만큼 page state 를 증가시키므로 중복 이벤트가 곧바로 누적 실행으로 이어진다 (`addrman.py:607`).

### Validation Hooks
- Strategy2 패치는 `_handle_op_end` 앞뒤 컨텍스트를 로깅하지만, 중복을 차단하지는 못한다 (`tools/validation_hooks.py:25`, `tools/validation_hooks.py:92`).

## 개선 방향

### Approach 1 – Scheduler queue replacement by stable `op_uid`
- **Implementation**: `Scheduler` 가 항상 `op_uid` 를 할당하고(Validation 여부와 무관), `_emit_op_events` 진입 시 동일 `op_uid` 의 기존 OP_END 이벤트를 EventQueue에서 제거한 뒤 새 시각으로 재삽입한다. EventQueue 에는 `remove_where(predicate)` 헬퍼를 추가하거나 `_eq._q` 를 선형 필터링한다. 이 로직은 `rm.commit(...)` 직후 `_emit_op_events` 가 호출되는 "schedule/commit" 단계에서 실행되며, 초기 PROGRAM/ERASE 커밋(`scheduler.py:752-767`)과 RESUME 체인 스텁 커밋(`scheduler.py:801-834`) 모두에 적용된다. 반면 `PROGRAM_SUSPEND` 자체는 OP_END 를 발행하지 않으므로 propose 단계나 SUSPEND 커밋 순간에는 재스케줄이 발생하지 않는다. suspend 시 `remaining_us` 를 이용해 재예약된 OP_END 시간이 재계산된다.
- **Pros**: 단일 책임(큐 정합성)으로 수정 영역이 scheduler/equeue 에 국한; AddressManager 등 후속 핸들러 변경 불필요.
- **Cons**: EventQueue 내부 구조 의존도가 증가하고, `_eq._q` 직접 조작이 유지보수 부담으로 작용할 수 있음.
- **Risks**: `op_uid` 가 누락된 경우(테스트나 도구가 커스텀 rec 주입) 기존 동작을 깨뜨릴 수 있으므로 fallback 키 (예: `(base, targets)` 해시) 가 필요함. 큐 필터링이 O(n) 이라 OP_END 밀도가 높은 워크로드에서 비용 증가 가능.
- **Testing**: suspend-resume 시나리오 단위 테스트에서 `EventQueue` 상태를 검증하고, validation 전략 1~3 로그를 재생해 중복 OP_END 가 생성되지 않는지 확인.

### Approach 2 – Suspend-aware rescheduling at event dispatch
- **Implementation**: `_handle_op_end` 실행 시, 대상 `op_uid` 가 여전히 ResourceManager 의 suspend 스택에 존재하면 핸들러 본문을 건너뛰고 `now_us + remaining_us` 로 OP_END 를 재스케줄한다. resume 스텁에서는 OP_END 를 추가로 발행하지 않고 CORE_BUSY stub 만 유지한다.
- **Pros**: EventQueue 조작 없이도 중복 실행 방지, suspend 상태에 맞춰 자연스럽게 재시퀀싱된다.
- **Cons**: `_handle_op_end` 가 ResourceManager 내부 상태에 강하게 결합되며, suspend 스택 정합성 실패 시 OP_END 가 영구 소실될 수 있음.
- **Risks**: 복수 resume(재-suspend) 시 `remaining_us` 갱신 지연으로 잘못된 재스케줄 발생 가능; hook/metrics 가 OP_END 지연을 어떻게 해석할지 명확한 재검토 필요.
- **Testing**: suspend-resume 반복 루프에서 OP_END 재스케줄이 정확히 한 번 실행되는지 assert 하고, `remaining_us` 업데이트 경계를 다루는 회귀 테스트 추가.

### Cross-cutting tasks
- suspend 시점에 `op_uid` 와 `end_us` 를 함께 저장하고 resume 시 최신 `end_us` 로 갱신하도록 ResourceManager 메타 구조를 확장.
- validation 로그(`strategy1_events.jsonl`, `strategy2_apply_pgm.jsonl`)에 `op_uid` 추적을 유지해 회귀 판별을 자동화.

## 코드 참조
- `scheduler.py:765` – 최초 commit 루프가 `_emit_op_events(rec)` 호출.
- `scheduler.py:827` – resume 체인 스텁이 `_emit_op_events(rec2)` 로 OP_END 재발행.
- `scheduler.py:868` – `_emit_op_events` 가 `op_uid` 유무에 따라 payload 구성.
- `resourcemgr.py:574` – suspend 커밋 시 타임라인과 메타 상태 갱신.
- `resourcemgr.py:1062` – `move_to_suspended_axis` 가 `remaining_us` 만 저장.
- `event_queue.py:17` – EventQueue 가 단순 정렬만 수행.
- `addrman.py:607` – `apply_pgm` 이 중복 횟수만큼 state 누적.
- `tools/validation_hooks.py:25` – Strategy2 패치가 apply_pgm 실행을 로깅.

## 아키텍처 인사이트
- suspend-resume 체인에서 scheduler 와 ResourceManager 는 메타 기반으로 협업하지만 EventQueue/AddressManager 는 동일 식별자를 관찰하지 못해 불일치가 발생한다.
- validation instrumentation 이 사실상 `op_uid` 공급원 역할을 하므로, 운영 경로에서도 동일 식별자를 표준화해야 한다.
- CORE_BUSY stub 은 실제 작업 재시작을 나타내지만, 상태 히스토리는 stub 과 원본 작업을 구분하지 않아 로그 해석이 어렵다.

## 역사적 맥락
- `research/2025-09-17_02-14-45_suspend_resume_op_end_requeue.md` – 중복 큐잉 현상과 instrumentation 전략 1~3을 정의.

## 관련 연구
- `research/2025-09-17_02-14-45_suspend_resume_op_end_requeue.md`

## 미해결 질문
- Validation 비활성 환경에서 `op_uid` 를 어떻게 보급할지(새 필드 vs 기존 `op_id` 재사용). -> (검토완료) 기존 op_uid 지사용
- EventQueue 조작 헬퍼를 도입할 경우 다른 이벤트 종류(OP_START/PHASE_HOOK)에도 동일한 요구가 발생하는지 평가 필요. -> (검토완료) 현재까지는 요구 없음
- suspend-resume 반복 중 stub 없이 OP_END 만 재예약했을 때 CORE_BUSY 로그/메트릭이 어떻게 변하는지 추가 검증 필요. -> (검토완료) 해당 우려는 OP_END-only 재예약(Approach 2 방향)에 한정되며, Approach 1 은 기존 CORE_BUSY 스텁을 유지한다. Stub 제거 시 자원 점유/타임라인/관측치가 모두 변형되므로 별도 완화와 회귀 검증이 필요함. 자세한 우려와 검증 방안은 "후속 연구" 섹션 참고. 

## 후속 연구 2025-09-17T16:11:48.852993+09:00

- **Stub 제거 시 자원 점유 상실**: 현재 체인 스텁은 `ResourceManager.reserve` 로 잔여 CORE_BUSY 시간을 다시 예약하며, 성공 시 plane/die 창을 잠가준다 (`scheduler.py:804`, `scheduler.py:805`). Stub 을 생략하고 OP_END 만 뒤로 미루면 이 재예약이 사라져 suspend 구간 이후 자원이 즉시 해제되고, 동시에 다른 PROGRAM/ERASE 가 같은 타겟을 점유할 수 있다. 이는 원래 목표였던 CORE_BUSY 지속을 깨뜨려 시뮬레이션 일관성이 무너진다.
- **상태 타임라인 공백**: suspend 시점에 CORE_BUSY 세그먼트가 잘려 나가며(`resourcemgr.py:658`, `resourcemgr.py:665`), 스텁 커밋이 없는 경우 `_st` 타임라인에 후속 CORE_BUSY 블록이 채워지지 않는다. 이후 `phase_key_at` 및 `operation_timeline` 은 재개된 기간을 공백으로 읽어 `PROGRAM.END` 상태를 반환하거나 DEFAULT 로 떨어진다 (`resourcemgr.py:695`, `main.py:291`). CORE_BUSY 기반 phase key/policy 가 활성화된 설정에서는 잘못된 스케줄링 결정을 초래할 수 있다.
- **관측 지표 손실**: 체인 스텁 커밋은 `chained_stubs` 메트릭과 총 시간을 축적해 재시도 비율을 추적한다 (`scheduler.py:832`, `scheduler.py:833`). Stub 을 제거하면 이 카운터가 0 으로 유지되어 모니터링이 단절된다. 또한 Strategy3 큐 스냅샷은 `_chain_stub` 플래그 기반으로 실행되므로 (`scheduler.py:952`), OP_END-only 접근 시 중복 검출용 로그가 더 이상 생성되지 않는다.
- **검증 제안**: ① 스텁을 비활성화한 실험 브랜치에서 `operation_timeline` CSV 와 RM 스냅샷을 비교해 CORE_BUSY 구간이 사라지는지 확인한다. ② 동일 설정에서 plane-level 경쟁 시나리오를 실행하여 suspend 직후 다른 PROGRAM 이 허용되는지 검증한다. ③ Strategy3 로그 유무와 `metrics.chained_stubs` 변화를 감시해 관측 손실을 계량화한다. 필요 시 Stub 삭제 대신 OP_END 재예약 + `_chain_stub` 이벤트 보존 같은 절충안을 고려한다.
