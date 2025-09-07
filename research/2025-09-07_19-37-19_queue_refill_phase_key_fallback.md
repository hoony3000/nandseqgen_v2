---
date: 2025-09-07T19:37:19+09:00
researcher: Codex
git_commit: 6b8638740683e8462da97574fee57becff2f3d64
branch: main
repository: nandseqgen_v2
topic: "QUEUE_REFILL propose 시 phase_key가 DEFAULT로 폴백되는 문제 분석"
tags: [research, scheduler, proposer, hooks, phase_key, rm]
status: complete
last_updated: 2025-09-07
last_updated_by: Codex
last_updated_note: "_phase_key RM 기반 폴백(phase_key_at) 후속 연구 추가"
---

# 연구: QUEUE_REFILL propose 시 phase_key가 DEFAULT로 폴백되는 문제

**Date**: 2025-09-07T19:37:19+09:00
**Researcher**: Codex
**Git Commit**: 6b8638740683e8462da97574fee57becff2f3d64
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
QUEUE_REFILL 훅을 통해 operation이 제안될 때, 첫 번째 케이스만 기대한 phase_key를 참조하고 이후 반복 선택에서는 `DEFAULT`로 폴백되는 원인과 해결 방안은 무엇인가?

## 요약
- 원인: `proposer._phase_key()`가 우선 `ResourceManager.op_state(die, plane, now)`를 조회하고, 커버링 세그먼트가 없을 때 훅의 `label`을 `BASE.STATE` 형식으로 파싱한다. QUEUE_REFILL에서는 `label`이 "DEFAULT"이므로, 세그먼트 외 시간대에는 `DEFAULT`로 폴백된다.
- 1차 제안(정상 참조)은 PHASE_HOOK 경로에서 발생하며, 이때 훅 `label`이 `BASE.STATE`로 제공되어 정확한 key가 사용된다. 이후 주기적 QUEUE_REFILL에서는 같은 타겟이 반복 선택되더라도 해당 시각이 세그먼트 밖이면 `DEFAULT`가 된다.
- 해결: (A) proposer에서 RM의 `phase_key_at(...)`을 폴백으로 사용해 `<LAST_BASE>.END` 등 가상 키를 일관 적용하거나, (B) Scheduler의 QUEUE_REFILL 훅에 `label=rm.phase_key_at(d,p,now)`를 주입하여 proposer의 기존 로직을 유지한 채 컨텍스트를 보강한다.

## 상세 발견

- QUEUE_REFILL 훅 생성
  - `scheduler.py:180` `_next_refill_hook()`는 `{"label":"DEFAULT", "die": rr_die, "plane": rr_plane}`를 반환한다.
  - 이후 `tick()`에서 `QUEUE_REFILL` 처리 시 이 훅으로 proposer를 호출한다: `scheduler.py:142`.

- phase_key 계산 경로
  - `proposer.py:799` `_phase_key(hook, res, now)`는 `res.op_state(die, plane, now)`를 먼저 본다. 값이 없으면 훅 `label`이 `BASE.STATE` 형식일 때만 사용하고, 아니면 `DEFAULT`로 떨어진다.
  - `proposer.py:830` `_phase_dist(cfg, key)`는 위에서 얻은 key로 `cfg['phase_conditional']`의 분포를 찾고, 없으면 `DEFAULT`를 사용한 뒤 `phase_conditional_overrides`를 멱등 적용한다.

- RM의 가상 END 키 유틸(분석/집계용)
  - `resourcemgr.py:546` `ResourceManager.phase_key_at(die, plane, t, ...)`은 세그먼트 밖이면 직전 세그먼트의 `<BASE>.END`를 유도한다. 현재 proposer는 이를 사용하지 않는다.

- 구성과 분포
  - 실행 시 `op_state_probs.yaml`이 로드되어 `cfg['phase_conditional']`가 채워진다(`main.py:716` 경로). 여기에는 `ERASE.END` 등 END 키가 다수 정의되어 있어, END 시점에 맞는 분포가 존재한다.

### 왜 첫 번째만 정상이고, 이후는 DEFAULT로 폴백되는가
- 첫 번째 정상 참조는 PHASE_HOOK 경로(상태 전환 전후 pre/post 타이밍)에서 발생하며, 훅 label이 `BASE.STATE`로 명시되어 `_phase_key`가 정확히 해당 key를 사용한다.
- 이후 반복 제안은 QUEUE_REFILL 경로에서 발생하고, 훅 label이 `DEFAULT`이므로 같은 die/plane이라도 now 시각이 세그먼트 밖인 경우 `res.op_state(...)`가 `None`을 반환 → `_phase_key`는 label 파싱 실패로 `DEFAULT` 폴백.

## 코드 참조
- `scheduler.py:142` — QUEUE_REFILL 훅 처리에서 proposer 호출
- `scheduler.py:180` — `_next_refill_hook()`의 훅 내용(`label="DEFAULT"`, die/plane RR)
- `proposer.py:799` — `_phase_key(...)`: RM 상태 우선, 없으면 label 파싱, 실패 시 DEFAULT
- `proposer.py:830` — `_phase_dist(...)`: key→분포 조회 후 overrides 적용
- `resourcemgr.py:546` — `phase_key_at(...)`: 세그먼트 밖에서 `<BASE>.END` 유도
- `op_state_probs.yaml:1` — `phase_conditional`에 `*.END` 키 다수 정의

## 아키텍처 인사이트
- PHASE_HOOK은 풍부한 컨텍스트(label=BASE.STATE)를 제공하므로 phase-key 정합성이 높다.
- QUEUE_REFILL은 컨텍스트가 빈약하여(기본 label=DEFAULT), 같은 타겟 반복 선택이라도 시각(now) 기준 상태가 없으면 `DEFAULT`로 귀결된다.
- 이미 RM에 가상 END 유도기가 있으므로, 제안 시점의 key를 일관되게 잡을 수 있는 안전한 확장 지점이 존재한다.

## 해결 방안(대안 비교)

- A. proposer 폴백 개선(권장)
  - 내용: `_phase_key()`에서 `op_state(...)`가 없고 훅 label이 `BASE.STATE`가 아닐 때 `res.phase_key_at(die, plane, now)`를 호출해 가상 키(예: `ERASE.END`)를 사용.
  - 장점: Scheduler 변경 없이 일관된 key 적용, PHASE_HOOK/QUEUE_REFILL 모두 개선, END 분포 활용 가능.
  - 단점: proposer의 키 유도 정책이 바뀌므로 미세한 분포 변화 가능.
  - 위험: 낮음. 이미 `op_state_probs.yaml`에 END 키가 준비되어 있고, 미적용 시에도 DEFAULT로 폴백.

- B. Scheduler 훅 라벨 보강(대안, 최소 침습)
  - 내용: `_next_refill_hook()`에서 `rm.phase_key_at(rr_die, rr_plane, now)`를 계산하여 `hook['label']`에 넣어 proposer에 전달.
  - 장점: proposer 로직 불변, QUEUE_REFILL 경로에서만 컨텍스트 향상, 구현 국소화.
  - 단점: 훅 생성 시 RM 접근이 추가됨. 라벨이 시각 경계에 있을 때 END/STATE 해석은 RM 쪽 정책에 의존.
  - 위험: 매우 낮음.

- C. 분포/오버라이드로 DEFAULT 케이스 제어(보조 수단)
  - 내용: `config.yaml`의 `phase_conditional_overrides.global`/특정 키에 DEFAULT 상황을 보정하도록 명시적 가중치 설정.
  - 장점: 코드 변경 없이 즉시 적용 가능.
  - 단점: 근본 원인(키 유실) 해결 아님. 상황별 세밀 제어 어려움.
  - 위험: 중간.

## 관련 연구
- `research/2025-09-07_18-56-10_queue_refill_rr_die_plane.md` — QUEUE_REFILL 훅에 die/plane RR 주입 설계와 구현 배경
- `plan/2025-09-06_op_state_virtual_end_aggregation.md` — 가상 END 키 유도기(`phase_key_at`) 도입 배경과 분석 파이프라인

## 미해결 질문
- proposer 쪽 폴백 변경(A안)과 스케줄러 라벨 보강(B안) 중 어느 것을 기본으로 둘지 정책 결정 필요.
- 경계 시각 해석(세그먼트 시작점에서 END 선호 여부)에 대한 일관성 정책을 PRD 주석으로 명시할지 여부.

## 후속 연구 2025-09-07T19:37:19+09:00 — _phase_key RM 기반으로 변경 시 영향/리스크

### 변경 요약
- 기존: `_phase_key(hook, res, now)`는 순서대로 (1) `res.op_state(die,plane,now)` → (2) 훅 `label`을 `BASE.STATE`로 파싱 → (3) 실패 시 `DEFAULT`를 반환.
- 제안 변경(A안 세부): (1)에서 상태가 없을 때, (2) 훅 라벨 대신 우선 `res.phase_key_at(die,plane,now)`로 가상 키(`<BASE>.END`)를 유도하고, 그것도 `DEFAULT`라면 마지막으로 훅 라벨 파싱을 시도.
  - 의사코드:
    - `st = res.op_state(die, plane, now)` → 있으면 `key = st`
    - 없으면: `if hasattr(res, 'phase_key_at'): key = res.phase_key_at(die, plane, now)`
    - `if key in (None, '', 'DEFAULT'): key = parse_label_or_default(hook)`

### 세부 동작 변화
- PHASE_HOOK(pre_t): 세그먼트 내부이므로 기존과 동일하게 `BASE.STATE` 유지.
- PHASE_HOOK(post_t): 세그먼트 경계(정확히 end)에서는 `op_state`가 없음 → 기존에는 훅 `label`의 `BASE.STATE`로 회귀했지만, 변경 후에는 `phase_key_at`이 `<BASE>.END`를 반환하여 END 분포를 사용.
- QUEUE_REFILL: 훅 라벨이 `DEFAULT`이므로, 기존에는 `DEFAULT`로 빈번히 폴백. 변경 후에는 `phase_key_at`이 직전 세그먼트의 `<BASE>.END`를 제공하여 END 분포 사용 비율이 크게 증가.

### 분포/정책 상의 영향
- `op_state_probs.yaml`에는 다수의 `*.END` 키가 정의되어 있어(예: `ERASE.END`, `READ.END`) 변경과 합치됨. 또한 `config.yaml`의 `phase_conditional_overrides`에 `READ*.END → DOUT: 1` 등의 규칙이 이미 있어 후속 행동(DOUT 유도)은 유지됨.
- `exclusions_by_op_state`는 `*.END` 키에 맞춘 그룹이 준비되어 있어 차단/허용 정책이 의도대로 적용됨.
- 결과적으로 PHASE_HOOK(post_t) 및 QUEUE_REFILL에서 `DEFAULT` 사용이 줄고, 의미 있는 END 키가 일관되게 사용됨.

### 리스크 평가
- 키 이동 리스크: PHASE_HOOK(post_t)이 `BASE.STATE`에서 `<BASE>.END`로 바뀌며 일부 분포가 달라질 수 있음. 그러나 END 키에 대한 오버라이드가 정해져 있어(예: `READ.END → DOUT:1`) 실제 선택은 동일할 가능성이 높음. 위험 낮음.
- 커버리지/분포 누락: 환경에서 특정 `*.END` 키가 `phase_conditional`에 없으면 `DEFAULT`로 다시 폴백. `op_state_probs.yaml`이 로드되는 표준 실행 경로에서는 리스크 낮음.
- 타입/호환성: `ResourceView` 프로토콜에 `phase_key_at`가 명시돼 있지 않음. 구현은 `hasattr(res, 'phase_key_at')`로 덕 타이핑 호출하고, 없으면 기존 훅 라벨 경로로 폴백하여 호환성 유지. 위험 매우 낮음.
- 결정성/성능: `phase_key_at`는 내부 이진 탐색(O(log N))으로 조회하며 결정성 보장. 성능 영향 미미.

### 테스트/검증 포인트
- proposer 로그(`out/proposer_debug_*.log`)에서 `[proposer] phase_key:` 라인을 비교.
  - 동일 시나리오에서 PHASE_HOOK(post_t)와 QUEUE_REFILL의 key가 `DEFAULT` → `<BASE>.END`로 전환되는지 확인.
- 결과 아티팩트:
  - `operation_timeline_*.csv`의 `phase_key_used`가 END 키로 증가하는지 확인.
  - 선택된 오퍼가 기존 오버라이드 정책과 일치(예: READ.END에서 DOUT 선택)하는지 확인.

### 대안 비교(요약)
- A안(본 변경): proposer에서 일괄 보정. PHASE_HOOK/QUEUE_REFILL 모두 혜택. 코드 변경은 proposer만.
- B안: Scheduler가 QUEUE_REFILL 훅 `label`을 `rm.phase_key_at(...)`으로 보강. proposer 불변이지만 PHASE_HOOK의 post_t에는 영향 없음.
