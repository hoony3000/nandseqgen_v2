---
date: 2025-09-15T22:13:14+09:00
researcher: Codex
git_commit: d9ebd587f760a77d9b0db2f3ef3b57dc0459c504
branch: main
repository: nandseqgen_v2
topic: "Propose 단계에서 Delay 선택 시 CSV 미기록 및 다음 event_hook으로 진행"
tags: [research, codebase, scheduler, proposer, exporter, NOP, Delay]
status: complete
last_updated: 2025-09-15
last_updated_by: Codex
---

# 연구: Propose 단계에서 Delay 선택 시 CSV 미기록 및 다음 event_hook으로 진행

**Date**: 2025-09-15T22:13:14+09:00
**Researcher**: Codex
**Git Commit**: d9ebd587f760a77d9b0db2f3ef3b57dc0459c504
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
propose 단계에서 Delay 가 선택됐을때 output csv 에 등록하지도 않고, 다음 event_hook 으로 넘어가게끔 하려면 어떻게 구현해야 하는지?

## 요약
- Delay는 `op_names.Delay`가 `base: NOP`로 정의되어 있고 상태에 영향을 주지 않음. 현재는 예약/커밋 후 InstrumentedScheduler가 CSV용 row를 기록한다.
- 요구사항을 만족하려면 “Delay를 실제로 예약·커밋하지 않고” 다음 hook으로 넘어가며, CSV 로우 생성도 건너뛰어야 한다.
- 최단·안전한 구현은 Scheduler의 `_propose_and_schedule(...)`에서 첫 선택이 `op_name == 'Delay'`인 경우를 감지하여 예약/커밋·이벤트·로깅을 모두 건너뛰고 `(0, False, 'skip_delay')`로 반환하는 전략이다. 필요 시 `features.skip_delay_in_proposal` 플래그로 가드한다.
- 대안으로 Export 단계에서 Delay를 필터링할 수 있으나, 이는 시간축 이벤트를 여전히 생성하여 “다음 event_hook으로 넘어간다”는 의도와 다르게 행동할 수 있다.

## 상세 발견

### Delay 정의와 동작 특성
- `config.yaml:3034` — `Delay`는 `base: NOP`, `durations.ISSUE: 0.02`로 정의됨. `NOP`는 `affect_state: false`, `instant_resv: true`.
- `op_state_probs.yaml:59` 및 `:254` — `ERASE.CORE_BUSY`/`PROGRAM_SLC.CORE_BUSY`에서 `Delay` 확률이 존재하여 제안될 수 있음.

### 현재 제안·스케줄·CSV 경로
- `proposer.py` — `propose(...)`가 후보를 선택해 `ProposedBatch` 반환.
- `scheduler.py:322` — `_propose_and_schedule(...)`가 배치를 순회하며 `rm.reserve(...)` → 성공 시 커밋 후 이벤트 발생.
- `scheduler.py:610` — `_emit_op_events(...)`는 모든 OP에 대해 `OP_START`/`OP_END`를 큐에 넣고, `affect_state=false`인 base는 PHASE_HOOK 생성을 스킵.
- `main.py:102` — `InstrumentedScheduler._emit_op_events`가 각 타깃에 대해 타임라인 row를 쌓음. 이 row들이 여러 CSV에 사용됨.

### 구현안 A: Scheduler에서 Delay 즉시 스킵 (권장)
- 위치: `scheduler.py:_propose_and_schedule`에서 `batch` 수신 직후.
- 로직: `first = batch.ops[0]`가 존재하고 `first.op_name == 'Delay'`이면 예약 루프에 들어가지 않고 즉시 `(0, False, 'skip_delay')` 반환.
- 효과:
  - RM 예약/커밋/이벤트 미발생 → OP_START/OP_END/PHASE_HOOK 미생성.
  - InstrumentedScheduler row 미생성 → 어떤 CSV에도 Delay 미출력.
  - tick 루프는 동일 타임슬라이스의 남은 이벤트 처리 후, 스케줄된 다음 `QUEUE_REFILL`로 자연 진행.
- 코드 기준점: `scheduler.py:300` 부근에서 `batch = _proposer.propose(...)` 직후 가드 추가.

예시 코드 스케치:
```
first = batch.ops[0] if getattr(batch, 'ops', None) else None
def _skip_delay_enabled(cfg):
    try: return bool(((cfg.get('features', {}) or {}).get('skip_delay_in_proposal', True)))
    except Exception: return True
if first and str(getattr(first, 'op_name', '')) == 'Delay' and _skip_delay_enabled(d.cfg):
    self.metrics['last_reason'] = 'skip_delay'
    return (0, False, 'skip_delay')
```

### 구현안 B: Proposer에서 Delay 후보를 배제
- 위치: `proposer.py: selection` 경로에서 후보 반복 시 `if name == 'Delay': continue`.
- 장점: Delay가 아예 선택되지 않음 → 제안·예약 경로 단순화.
- 단점: 분포 교란 및 로깅/분석 지표(phase_proposal_counts 등)에 영향. 경우에 따라 대체 후보 없음으로 `no_candidate` 빈발 가능.

### 구현안 C: Export 단계에서 Delay 행 필터링
- 위치: `main.py` 내 각 `export_*` 진입부에서 `if row['op_name']=='Delay': continue`.
- 장점: 스케줄러 변경 없이 빠른 적용.
- 단점: OP 이벤트와 스케줄 시간은 여전히 발생하여 “다음 event_hook으로 즉시 진행” 요구와 불일치.

## 코드 참조
- `scheduler.py:300` — `_propose_and_schedule(now, hook)` 진입, 배치 수신 직후 가드 추가 지점
- `main.py:102` — `InstrumentedScheduler._emit_op_events`가 CSV용 row 생성(Delay가 여기서 포함됨)
- `config.yaml:3034` — `Delay` → `base: NOP`, `durations.ISSUE: 0.02`
- `op_state_probs.yaml:59` — `ERASE.CORE_BUSY`에서 `Delay` 확률
- `op_state_probs.yaml:254` — `PROGRAM_SLC.CORE_BUSY`에서 `Delay` 확률

## 아키텍처 인사이트
- “무효 동작(Delay/NOP)”은 상태에 영향 없이 시간만 소비한다. 요구사항이 “hook 전진”이라면 생산 단계(스케줄러)에서 제거하는 편이 부작용이 가장 적다.
- 기능 플래그(`features.skip_delay_in_proposal`)로 가드하여 환경별로 토글 가능하게 두는 것이 운영 상 안전하다.

## 관련 연구
- 연구 문서 없음

## 미해결 질문
- 분포 상 Delay 비중이 큰 환경에서 Delay 스킵 시 후보 고갈이 발생할 수 있는데, 이때 프로포절 재시도/대체 후보 선택 정책을 둘지 여부. -> (검토완료) 현재단계에서 고려하지 않는다.
