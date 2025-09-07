---
date: 2025-09-07T18:56:10+09:00
researcher: Codex
git_commit: 306b495154519224c6511bd6f5f3c7b7cc546347
branch: main
repository: nandseqgen_v2
topic: "QUEUE_REFILL: hook.die/plane round-robin rotation"
tags: [research, codebase, scheduler, proposer, hooks]
status: complete
last_updated: 2025-09-07
last_updated_by: Codex
---

# 연구: QUEUE_REFILL 시 hook.die/plane 라운드로빈 순회

**Date**: 2025-09-07T18:56:10+09:00
**Researcher**: Codex
**Git Commit**: 306b495154519224c6511bd6f5f3c7b7cc546347
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
QUEUE_REFILL 시 `hook.die`, `hook.plane`을 번갈아 순회(라운드로빈)하게 바꾸려면 어디를 어떻게 개선해야 하는가? 현재는 `die=0`, `plane=0` 고정처럼 동작한다.

## 요약
- 현행 Scheduler는 QUEUE_REFILL 처리 시 proposer를 `hook={"label":"DEFAULT"}`로 호출한다. `die/plane` 미지정으로 인해 proposer는 기본값 `0,0`을 사용한다.
- proposer는 `hook.die/plane`을 다음에서 사용한다:
  - `phase_key` 계산 시 RM의 `op_state(die,plane,now)` 조회에 사용(미제공 시 0,0)
  - Non‑E/P/R 계열의 기본 타겟 fallback(`Address(die,plane,...)`)에 사용
  - E/P/R 샘플러에 `sel_die` 힌트로 전달되어, 해당 die에서만 주소 샘플링 수행
- 개선안: Scheduler가 QUEUE_REFILL용 `hook`에 `(die,plane)`을 라운드로빈으로 주입해 proposer 호출. 가장 단순한 방법은 Scheduler 내부에 `(die,plane)` 인덱스를 유지하며 plane‑major(또는 die‑major)로 순회하는 것이다.

## 상세 발견

### 이벤트 처리 순서와 QUEUE_REFILL 훅
- `event_queue.py:6` — 우선순위: `OP_END(0) → PHASE_HOOK(1) → QUEUE_REFILL(2) → OP_START(3)`
- `scheduler.py:92` — 초기 시드: 생성자에서 첫 `QUEUE_REFILL` 이벤트 push
- `scheduler.py:127` — 동일 시각 배치 처리 루프 내 순서 고정
- `scheduler.py:138` — QUEUE_REFILL 처리: `self._propose_and_schedule(self.now_us, {"label":"DEFAULT"})`로 호출(die/plane 미지정)

### proposer가 hook.die/plane을 사용하는 지점
- `proposer.py:799` — `_phase_key`: `die=int(hook.get("die",0))`, `plane=int(hook.get("plane",0))`로 기본값 0,0 사용. `res.op_state(die,plane,now)`로 phase_key 유도
- `proposer.py:986` — `key = _phase_key(hook, ...)`로 분포 선택
- `proposer.py:1049` — E/P/R 주소 샘플링 시 `sel_die = hook.get("die")`를 `_sample_targets_for_op(..., sel_die=...)`에 전달 → 해당 die에서만 샘플링
- `proposer.py:1061` — Non‑E/P/R Fallback: hook에 `targets`가 없으면 `hook.die/plane`으로 단일 타겟 구성(없으면 0,0)

### 왜 0,0으로 고정처럼 보이는가
- QUEUE_REFILL 경로에서는 hook에 die/plane이 없음 → `_phase_key`와 non‑EPR fallback 모두 기본값 0,0을 사용 → die 0, plane 0 기준의 상태/타겟으로 일관된 제안이 발생.

## 개선안 제시

- A. Scheduler 라운드로빈 주입(권장, 최소 변경)
  - 아이디어: Scheduler가 내부에 `(rr_die, rr_plane)` 상태를 유지하고, QUEUE_REFILL 처리 시 `hook={"label":"DEFAULT", "die": rr_die, "plane": rr_plane}`로 proposer를 호출. 호출 후 `(rr_plane += 1)`, wrap 시 `(rr_plane=0, rr_die += 1)`, 최종 wrap 시 `rr_die=0`.
  - 장점: 변경 범위가 Scheduler로 국한, 결정성 유지, 공정한 die/plane 커버리지 향상(E/P/R 샘플링은 die 단위로, non‑EPR fallback은 die/plane 단위로 분산).
  - 단점: hook payload가 PRD의 "QUEUE_REFILL 데이터: None" 설명과 약간 달라짐(실전 런타임에서는 합리적 확장으로 보임).
  - 위험: 거의 없음. 기존 훅/틱 순서나 트랜잭션 원자성 불변.

- B. 이벤트 payload에 내장(대안)
  - 아이디어: QUEUE_REFILL 이벤트 push 시점에 이미 `(die,plane)`을 payload에 담아두고, 처리 시 그대로 proposer에 전달.
  - 장점: 이벤트 자체가 컨텍스트를 운반하므로 디버깅 용이(큐 상태를 보면 다음 훅 대상 확인 가능).
  - 단점: push 지점이 두 곳(생성자/주기 재등록)이라 양쪽에 동일 로직 필요. 코드 분산.

- C. 무작위 선택(비권장)
  - 아이디어: 매 틱마다 `rng`로 die/plane을 샘플링.
  - 장점: 구현 쉬움.
  - 단점: 커버리지 편차/군집화 가능. 라운드로빈 대비 공정성 낮음. 요구사항 "번갈아 순회"와 불일치.

## 구현 스케치(안 A 기준)

- `scheduler.py` 변경 포인트:
  - 필드 추가: `self._rr_die = 0`, `self._rr_plane = 0`
  - 토폴로지 조회: `dies = int(cfg["topology"]["dies"])`, `planes = int(cfg["topology"]["planes"])` (안전하게 dict 접근)
  - 헬퍼 추가:
    - `_next_refill_hook()` → `{"label":"DEFAULT", "die": rr_die, "plane": rr_plane}` 반환 후 내부 카운터 갱신(plane‑major 또는 die‑major 선택 가능)
  - 사용처 교체:
    - `scheduler.py:138` — `c, rb, rsn = self._propose_and_schedule(self.now_us, self._next_refill_hook())`
    - 큐 비었을 때 시드(`scheduler.py:114` 경로)와 주기 재등록은 그대로 두어도 무방(훅 주입은 처리 시점에서 일어남)

예시 코드 조각(요지):

```
# __init__
self._rr_die = 0
self._rr_plane = 0

def _topology(self) -> tuple[int,int]:
    topo = self._deps.cfg.get("topology", {}) or {}
    return int(topo.get("dies", 1)), int(topo.get("planes", 1))

def _next_refill_hook(self) -> dict:
    dies, planes = self._topology()
    hook = {"label": "DEFAULT", "die": self._rr_die, "plane": self._rr_plane}
    # plane‑major RR
    self._rr_plane += 1
    if self._rr_plane >= planes:
        self._rr_plane = 0
        self._rr_die = (self._rr_die + 1) % max(1, dies)
    return hook

# tick() QUEUE_REFILL 분기
c, rb, rsn = self._propose_and_schedule(self.now_us, self._next_refill_hook())
```

옵션: 정책으로 순회 순서 선택
- `CFG[policies][queue_refill_rr]` ∈ {`plane_major`(기본), `die_major`}
- 필요 시 `_next_refill_hook`에서 분기해 순서 변경

## 코드 참조
- `event_queue.py:6` — 이벤트 우선순위 맵
- `scheduler.py:92` — 초기 QUEUE_REFILL push
- `scheduler.py:127` — 틱 내 처리 순서 보장
- `scheduler.py:138` — 현재 QUEUE_REFILL 처리 경로(라벨만 전달)
- `proposer.py:799` — `_phase_key`에서 `hook.die/plane` 기본값 0 사용
- `proposer.py:986` — `phase_key` 계산 및 분포 선택
- `proposer.py:1049` — E/P/R 샘플러에 `sel_die` 힌트로 사용
- `proposer.py:1061` — Non‑EPR fallback 타겟에서 `hook.die/plane` 사용
- `docs/PRD_v2.md:269` — QUEUE_REFILL 설명(데이터: None)

## 아키텍처 인사이트
- QUEUE_REFILL은 PHASE_HOOK 대비 "컨텍스트 빈약"한 훅이므로, 최소한 die를 제공하면 E/P/R 샘플링이 die‑wide로 공정 분산된다. plane은 Non‑EPR 기본 타겟에만 영향.
- PHASE_HOOK은 이미 타겟별(die, plane) 컨텍스트로 풍부하므로, QUEUE_REFILL 라운드로빈은 주로 "빈 구간 보충"과 초기 구동 공정성에 기여한다.
- 이벤트 순서/결정성은 유지되며, 훅에 die/plane을 추가하는 것은 proposer와의 계약에 자연스럽게 부합한다.

## 관련 연구
- `research/2025-09-06_22-56-15_non_epr_target_selection.md` — Non‑EPR의 훅/타겟 선택 정책 정리(훅 컨텍스트 부재 시 die/plane fallback 0,0)

## 미해결 질문
- 정책 토글 필요성: `queue_refill_rr`(plane‑major vs die‑major)와 활성화 on/off 노출 여부. -> (검토완료) 불필요.
- 멀티플레인 공정성: E/P/R에서 plane은 block→plane 매핑(`block % planes`)으로 간접 결정. plane 균형 보장이 충분한지 검증 필요. -> (검토완료) 불필요.


