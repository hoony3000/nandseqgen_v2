---
date: 2025-09-07T21:57:06+09:00
researcher: Codex
git_commit: 6b8638740683e8462da97574fee57becff2f3d64
branch: main
repository: nandseqgen_v2
topic: "QUEUE_REFILL 제안 시 과거 phase_key 사용 문제 분석 및 개선안"
tags: [research, scheduler, proposer, hooks, phase_key, rm]
status: complete
last_updated: 2025-09-07
last_updated_by: Codex
---

# 연구: QUEUE_REFILL 제안 시 과거 phase_key 사용 문제

**Date**: 2025-09-07T21:57:06+09:00
**Researcher**: Codex
**Git Commit**: 6b8638740683e8462da97574fee57becff2f3d64
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
QUEUE_REFILL 훅으로 생성된 이벤트에서 만들어진 오퍼레이션이 `propose` 단계에서 “현재(now) 시각”이 아닌 과거 시점의 phase_key를 계속 사용하는 오류가 발생한다. 원인과 개선 방안을 무엇인가?

## 요약
- 원인 1(경계 시점): now가 직전 세그먼트의 종료 시각(경계)에 위치할 때 `ResourceManager.op_state(d,p,now)`는 `None`을 반환한다. 이때 `proposer._phase_key()`가 훅 `label`을 그대로 신뢰하면 과거 상태(예: `ERASE.CORE_BUSY`)가 유지된다.
- 원인 2(QUEUE_REFILL 컨텍스트 빈약): REFILL 훅은 기본적으로 `label="DEFAULT"`이므로, 과거 상태를 보정할 정보가 부족하다.
- 개선(권장 조합):
  - A. Proposer에서 RM의 가상 키(`phase_key_at`)를 폴백으로 사용해 경계/세그먼트 외부에서도 일관된 키(예: `<BASE>.END`)를 선택한다.
  - B. Scheduler의 REFILL 훅에 `label=rm.phase_key_at(d,p,now)`를 주입해, 라벨 자체를 now 기준 가상 키로 보강한다.

## 상세 발견

### 생성/처리 경로
- `scheduler.py:142` — QUEUE_REFILL 처리에서 proposer 호출: `_propose_and_schedule(self.now_us, self._next_refill_hook())`
- `scheduler.py:180` — `_next_refill_hook()`은 `{label: "DEFAULT", die, plane}`을 반환(라운드로빈 대상만 포함)
- `proposer.py:823` — `_phase_key(cfg, hook, res, now_us)` 흐름:
  - 1) `res.op_state(die,plane,now)` 우선
  - 2) 없으면 `hook.label`을 `BASE.STATE`로 파싱
  - 3) `label`이 `DEFAULT`인 경우, `features.phase_key_rm_fallback`가 켜져 있으면 `res.phase_key_at(d,p,now)`로 폴백

### 왜 과거 키가 남는가(시나리오)
- PHASE_HOOK post_t 경로: 후속 제안을 촉발하는 훅의 `label`이 바로 직전 상태(예: `READ.DATA_OUT`)로 들어온다. 이때 now가 경계(세그먼트 종료 시각)라 `op_state(...)`는 None → `label` 파싱 결과를 그대로 사용하면 과거 키가 유지된다. 현재 구현은 `label != DEFAULT`이면 RM 폴백을 시도하지 않는다.
- REFILL 경로: 훅 라벨이 `DEFAULT`이고, 과거 상태 세그먼트 외라 `op_state(...)`가 None → RM 폴백을 통해 `<BASE>.END`로 잡을 수 있으나, 폴백이 꺼져 있거나(설정) 훅 라벨을 먼저 고정 신뢰하는 구현이라면 `DEFAULT` 등 비의미 키가 사용될 수 있다.

### 증거(출력 관찰)
- `out/operation_timeline_*.csv`에서 `phase_key_used`와 `phase_key_virtual`의 불일치 사례가 경계 시점에 집중됨. 예: `7000.11` 근방에서 `phase_key_used=ERASE.CORE_BUSY`, `phase_key_virtual=ERASE.END`.
  - `main.py:170` `export_operation_timeline`은 제안 시 보존된 `row.phase_key`를 우선 사용하고, 가상 키는 `rm.phase_key_at(d,p,t_ctx or start)`로 별도 산출하여 `phase_key_virtual`로 기록한다(라인 `194, 209`).

## 코드 참조
- `scheduler.py:142` — REFILL 훅 처리에서 proposer 호출
- `scheduler.py:180` — `_next_refill_hook()` 라벨 기본값 `DEFAULT`
- `proposer.py:823` — `_phase_key(...)` 구현과 폴백 경로
- `proposer.py:835` — `features.phase_key_rm_fallback` 사용해 RM 가상 키 폴백
- `resourcemgr.py:570` — `phase_key_at(...)`: 경계/세그먼트 외부에서 `<BASE>.END` 유도
- `main.py:170` — `export_operation_timeline`에서 `phase_key_used/virtual` 작성
- `config.yaml:32` — `features.phase_key_rm_fallback: true`(기본 켜짐)

## 아키텍처 인사이트
- PHASE_HOOK은 풍부한 컨텍스트(정확한 `BASE.STATE` 라벨)를 제공하지만, “경계 시각”에서는 라벨이 과거 상태를 가리킬 수 있다.
- REFILL은 컨텍스트가 빈약해(now 기준 상태를 키로 삼으려면) RM의 가상 키 유도가 필수적이다.
- 이미 RM에 `phase_key_at`이 있어 제안 시점 기준으로 안정적인 키(특히 `.END`)를 도출할 수 있다.

## 개선안(대안 비교)

- A. proposer 폴백 강화(현행 구현, 권장 유지)
  - 내용: `_phase_key()`에서 `op_state(...)`가 None이고 `label`이 의미 없을 때(`DEFAULT`) `res.phase_key_at(...)` 사용.
  - 장점: REFILL/PHASE_HOOK 모두에서 now 기준 일관성 확보(특히 `.END`).
  - 단점: `label`이 유효(`BASE.STATE`)인 경우엔 여전히 과거 라벨을 우선시함.
  - 위험: 낮음(기능 플래그 `features.phase_key_rm_fallback`로 가드).

- B. REFILL 훅 라벨 주입(소폭 확장, 권장)
  - 내용: `Scheduler._next_refill_hook()`에서 `label=self._deps.rm.phase_key_at(rr_die, rr_plane, self.now_us)`로 채워 proposer에 전달.
  - 장점: proposer 로직 변경 없이 REFILL 경로에서 즉시 now‑기준 키 사용.
  - 단점: PRD에서 REFILL "데이터 없음" 설명과 약간의 괴리(실전 런타임 확장으로 허용 가능).

- C. 경계 시 `label`보다 RM 우선(선택적 추가)
  - 내용: `_phase_key()`에서 `op_state(...)`가 None이고 now가 경계로 판단되면, `label`이 `BASE.STATE`라도 `phase_key_at` 결과(대개 `<BASE>.END`)를 우선.
  - 장점: PHASE_HOOK post_t에서도 과거 라벨 대신 now 기준 `.END` 사용.
  - 단점: 라벨 신뢰도를 낮추므로 호환성 영향 가능. 플래그(예: `features.phase_key_prefer_rm_at_boundary`)로 가드 권장.

## 제안하는 최소 변경 패치 방향(요지)

- REFILL 훅 보강(B):
  - `scheduler.py:180` `_next_refill_hook()` 내부에서 현재 커서 `(rr_die, rr_plane)`와 `self.now_us`를 사용해 `key = self._deps.rm.phase_key_at(rr_die, rr_plane, self.now_us)`를 계산하고, `{"label": key, "die": rr_die, "plane": rr_plane}` 반환.

- 경계 선호 개선(C, 선택):
  - `proposer.py:823` `_phase_key(...)`의 라벨 분기에서, `op_state(...) is None`이고 `label`이 `BASE.STATE`인 경우 `rm.phase_key_at(...)` 결과가 `.END`이면 이를 우선하도록 조건 추가(플래그 가드).

## 역사적 맥락
- `research/2025-09-07_19-37-19_queue_refill_phase_key_fallback.md` — REFILL 시 `DEFAULT` 폴백 문제와 해결(Option A/B) 정리.

## 관련 연구
- `research/2025-09-07_18-56-10_queue_refill_rr_die_plane.md` — REFILL의 `(die,plane)` 라운드로빈 주입 설계.

## 미해결 질문
- 경계 판정 휴리스틱: `prefer_end_on_boundary=True` 기준을 proposer 단계에도 동일하게 적용할지 여부(현재 RM에서만 적용). -> (검토완료) 현행 유지
- `operation_timeline.op_state` 컬럼을 무엇으로 표준화할지(제안 시점 vs 예약 시점 vs 가상 END) — 현재는 `phase_key_used`/`phase_key_virtual`을 함께 노출해 해소. -> (검토 완료) 예약 시점이 기준.

