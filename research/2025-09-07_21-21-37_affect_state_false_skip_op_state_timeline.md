---
date: 2025-09-07T21:21:37+09:00
researcher: Codex
git_commit: 6b8638740683e8462da97574fee57becff2f3d64
branch: main
repository: nandseqgen_v2
topic: "affect_state=false인 operation의 op_state_timeline 등록 방지"
tags: [research, codebase, resource-manager, scheduler, export, prd]
status: complete
last_updated: 2025-09-07
last_updated_by: Codex
---

# 연구: affect_state=false 인 operation 이 op_state timeline 에 state 가 등록되지 않게 하는 방법

**Date**: 2025-09-07T21:21:37+09:00
**Researcher**: Codex
**Git Commit**: 6b8638740683e8462da97574fee57becff2f3d64
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
affect_state=false 인 operation 이 op_state timeline 에 state 가 등록되지 않게 수정할 수 있는 방법을 research 해줘.

## 요약
- 타임라인 세그먼트는 `ResourceManager.commit()`에서 `self._st.reserve_op(...)` 호출로 등록된다(`resourcemgr.py:497`).
- PRD v2에 명시된 예외 규칙(affect_state=false → 타임라인 미등록, `docs/PRD_v2.md:355`)을 구현하려면 이 호출을 `affect_state` 설정으로 게이트하면 된다.
- 권장 변경: `ResourceManager.commit()`에서 `base`의 `affect_state`가 `false`일 때 `reserve_op` 호출만 생략한다. 나머지 ODT/CACHE/SUSPEND 등의 런타임 상태 갱신 로직은 그대로 유지된다.
- 이미 `Scheduler`는 `affect_state=false`에 대해 PHASE_HOOK 생성을 건너뛰고 있으므로(`scheduler.py:457`), RM 쪽만 보완하면 op_state 타임라인에서의 미등록 요건이 충족된다.

## 상세 발견

### 타임라인 등록 지점 (ResourceManager)
- `resourcemgr.py:496` 이후 루프에서 예약된 각 op에 대해 타임라인 세그먼트를 추가한다.
- 현재 구현:
  - `resourcemgr.py:496` — `for (die, plane, base, st_list, start) in txn.st_ops:`
  - `resourcemgr.py:497` — `self._st.reserve_op(die, plane, base, st_list, start)`
- 동일 루프에서 ODT/CACHE/SUSPEND 상태도 함께 갱신한다(`resourcemgr.py:501` 이하). 따라서 타임라인 미등록 처리를 하면서도 해당 상태 갱신은 유지되어야 한다.

제안 변경(핵심 아이디어):
- `reserve_op` 호출 전 `affect_state`를 조회하여 `false`면 호출을 생략한다.
- 의사코드:
  - `if affects_state(base): self._st.reserve_op(...)`
  - `affects_state(base)` 구현은 `cfg['op_bases'][base]['affect_state']`를 안전하게 조회하여 기본값 `True`를 반환.

### PHASE_HOOK 생성 게이트 (Scheduler)
- 이미 스케줄러에서 `affect_state=false`인 베이스에 대해 PHASE_HOOK을 생성하지 않도록 처리되어 있다.
  - `scheduler.py:457` — `if not _affects_state(self._deps.cfg, base): return`
  - `_affects_state`는 `cfg.op_bases[base].affect_state`를 기본 `True`로 조회(`scheduler.py:424-428`).

### PRD 요구사항 근거
- `docs/PRD_v2.md:355` — "예외적으로 affect_state=false 인 경우 op_state_timeline 에 등록하지 않는다."
- 또한 PHASE_HOOK 관련 주의사항에 동일 정책이 언급되어 있다(`docs/PRD_v2.md:278`).

### 구성 확인 (예시)
- `config.yaml:432` — `DOUT` 베이스: `affect_state: false`.
- 이 외에도 `DOUT4K`, `DATAIN`, `SR`, `TRAINING`, `READID`, `SETPARA/GETPARA`, `SETFEATURE/GETFEATURE`, `ODTDISABLE/ODTENABLE` 등 다수 베이스가 `affect_state: false`로 정의되어 있다.

## 코드 참조
- `resourcemgr.py:497` — 타임라인 세그먼트 등록 호출 위치
- `resourcemgr.py:501` — ODT/CACHE/SUSPEND 상태 갱신(타임라인 등록과 같은 루프에서 실행)
- `scheduler.py:457` — `affect_state=false` 시 PHASE_HOOK 미생성 게이트
- `docs/PRD_v2.md:355` — affect_state=false 타임라인 미등록 규정
- `docs/PRD_v2.md:278` — affect_state=false PHASE_HOOK 미생성 규정
- `config.yaml:432` — `DOUT`의 `affect_state: false` 정의 예시

## 아키텍처 인사이트
- 타임라인의 단일 진실원천은 RM의 `_StateTimeline`; 등록 게이트를 RM에 두면 분석/익스포트(`main.py`) 전 구간에서 일관성 있게 적용된다.
- ODT/CACHE/SUSPEND 등의 런타임 상태는 타임라인과 별개이며, 동일 커밋 루프에서 처리되므로 타임라인 미등록과 충돌하지 않는다. 즉, `reserve_op` 호출만 조건부로 생략하는 방식이 가장 안전하다.

## 대안 비교
- 옵션 A: `ResourceManager.commit()`에서 `reserve_op` 호출만 `affect_state`로 게이트 [권장]
  - 장점: 최소 변경, 모든 경로(instant/normal) 공통 처리, 부작용 적음
  - 단점: `txn.st_ops`는 그대로 유지되어 약간의 메모리·루프 오버헤드 존재
  - 위험: 낮음 — 기능적 행태 변화는 타임라인 축소뿐
- 옵션 B: `reserve()`에서부터 `txn.st_ops`에 비상태 오퍼레이션을 넣지 않음
  - 장점: 약간의 효율 개선
  - 단점: 커밋 루프에서 수행하는 ODT/CACHE/SUSPEND 갱신이 누락될 수 있어 추가 경로 분기가 필요
  - 위험: 중간 상태 갱신 누락으로 일관성 저하 위험
- 옵션 C: 익스포트 시(`main.py`)에만 필터링
  - 장점: 매우 국소적 변경
  - 단점: 런타임 질의(`rm.op_state`)와 분석 지표 간 불일치 발생
  - 위험: 제안·검증 로직 오동작 가능성

## 역사적 맥락
- 관련 연구: proposal 타이밍의 가상 END 키 폴백(`research/2025-09-07_19-37-19_queue_refill_phase_key_fallback.md`)에서 RM의 타임라인이 제안 키 파생에 활용됨을 분석. 본 변경은 그 경로에 영향을 주지 않으며, 비상태 오퍼레이션을 타임라인에서 제거해 해석을 더 명확히 한다.

## 관련 연구
- `research/2025-09-07_19-37-19_queue_refill_phase_key_fallback.md`

## 미해결 질문
- `affect_state=false` 중에서도 타임라인에 예외적으로 남겨야 하는 베이스가 있는가? (현재 PRD 기준 없음) -> (검토완료) 없음
- 운영 지표에서 `operation_timeline`의 `op_state` 필드가 `NONE`으로 증가하는 것이 의도와 부합하는지 점검 필요(사용자 기대와 일치하면 그대로 유지). -> (검토완료) 특정 operation 으로 국한되는 개선이기에 OK.

