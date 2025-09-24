---
date: 2025-09-23T18:08:59.391967+09:00
researcher: Codex
git_commit: 1805c6c27e339c3c571f79966f9dc53d3940cec6
branch: main
repository: nandseqgen_v2
topic: "READ/PROGRAM latch overlap risk"
tags: [research, codebase, resourcemgr, scheduler, proposer]
status: complete
last_updated: 2025-09-23
last_updated_by: Codex
---

# 연구: READ/PROGRAM latch overlap risk

**Date**: 2025-09-23T18:08:59.391967+09:00
**Researcher**: Codex
**Git Commit**: 1805c6c27e339c3c571f79966f9dc53d3940cec6
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
ResourceManager 의 latch 가 (die, plane) 단일 항목으로만 관리될 때 READ 계열과 ONESHOT PROGRAM 계열 latch 가 동시에 필요할 수 있는가? 만약 가능하다면 PROGRAM/READ 를 별도 구조로 나누는 접근은 얼마나 효과적인가?

## 요약
- READ 예약 직후 `_latch` 는 plane 단위로 `LATCH_ON_READ` 항목을 등록하지만 release 경로가 도달하기 전이라도 PROGRAM 계열이 같은 키를 덮어쓴다(`resourcemgr.py:569`, `resourcemgr.py:601`).
- `after_read` 그룹이 비어 있어 `_latch_ok` 가 READ latch 를 차단에 활용하지 못하므로 ONESHOT PROGRAM 은 READ latch 가 유효한 동안에도 예약/커밋 가능하다(`config.yaml:1093`, `resourcemgr.py:409`).
- DOUT OP_END 가 발생하면 release 는 latch 종류를 구분하지 않고 동일 키를 pop 하므로, PROGRAM latch 가 READ 해제 과정에서 조기에 사라질 수 있다(`scheduler.py:484`, `resourcemgr.py:695`).
- Proposer 는 동일 배치 내 후속 연산을 직전 종료 시각으로 정렬하므로 READ 종료와 DOUT/PROGRAM 시작 시간이 겹쳐 latch 덮어쓰기-즉시해제 시나리오가 현실적으로 발생한다(`proposer.py:1184`).
- PROGRAM/READ 별도 저장소로 분리하거나 다중 latch 컨테이너를 도입하면 조기 해제와 덮어쓰기를 모두 막을 수 있으나, Snapshot/restore API 와 규칙 평가 경로도 함께 조정해야 한다.

## 상세 발견

### ResourceManager latch lifecycle
- `_latch` 는 plane 키 하나에 `_Latch(kind,start,end)` 만 저장하므로 동시 다중 latch 표현이 불가능하다(`resourcemgr.py:143`).
- READ/PROGRAM 예약 시점에 각각 `txn.latch_locks[(die, plane)]` 를 갱신하며 commit 에서 기존 값을 덮어쓴다(`resourcemgr.py:569`, `resourcemgr.py:601`).
- Release 경로는 latch 종류를 검사하지 않고 단순히 pop 하여 해제 시점 충돌에 취약하다(`resourcemgr.py:695`, `resourcemgr.py:699`).

### Scheduler release hooks
- OP_END 핸들러가 READ 후속 DOUT, CACHE_READ_END 종료 시 plane 키를 그대로 pop 해 버린다(`scheduler.py:484`).
- ONESHOT PROGRAM 완료 시점 해제를 위해서는 EXEC MSB 종료를 기다리지만, 그 전에 동일 plane 에 대한 DOUT 이 완료되면 latch 가 이미 제거된다(`scheduler.py:488`).

### Sequencing behavior
- Proposer 는 동일 배치 내 연산을 직전 종료 시각에 맞춰 연쇄 배치하므로 READ 종료와 DOUT, 온샷 PROGRAM 이 같은 시각에 시작된다(`proposer.py:1184`).
- `sequence_gap` 이 0 이라면 latch start 시각과 후속 연산 start 시각이 동일해 덮어쓰기 → 즉시 release 흐름이 빈번해질 수 있다(`proposer.py:1190`).

### Config-driven gating
- LATCH_ON_READ 가 연결된 `after_read` 그룹은 비어 있어 READ latch 가 어떤 base 도 차단하지 못한다(`config.yaml:2311`, `config.yaml:1093`).
- 반면 PROGRAM 계열 latch 는 `after_oneshot_program_lsb` 등 그룹을 통해 READ 계열을 제외하고 다양한 base 를 차단하도록 설계되어 있어 latch 유지가 더 중요하다(`config.yaml:1716`).

## 코드 참조
- `resourcemgr.py:569` – READ/PROGRAM 예약에서 latch_locks 덮어쓰기.
- `resourcemgr.py:601` – commit 시 `_latch` 업데이트.
- `resourcemgr.py:695` – `release_on_dout_end` 가 latch 종류 구분 없이 pop.
- `scheduler.py:484` – DOUT OP_END 시 release 호출.
- `proposer.py:1184` – 후속 연산을 직전 종료 시각에 배치.
- `config.yaml:1093` – `after_read` 그룹이 빈 배열로 정의됨.
- `config.yaml:1716` – `after_oneshot_program_lsb` 가 여러 base 를 차단.

## 아키텍처 인사이트
- latch 는 plane/시간 기반 리소스 모델에서 규칙 기반 차단과 결합되어 있으므로, 단일 맵 구조는 latch 종류가 늘어날수록 동시 표현 부족 문제를 일으킨다.
- Release 경로는 latch 종류 식별을 전혀 하지 않아, 호출 순서가 조금만 변해도 의도치 않은 해제가 발생한다.
- Config 그룹이 READ latch 를 사실상 비활성화하고 있어, latch 구조 개선과 함께 정책 정의를 재검토해야 한다.

## Alternatives Considered
- **Option 1 – Separate maps for READ vs PROGRAM latch**
  - 장점: release 경로를 분기하여 조기 해제를 방지하고 기존 `_Latch` 구조를 재사용 가능.
  - 단점: `_latch_ok`, snapshot/restore, metrics 등 latch 사용 지점 전반에 조건 분기 추가 필요.
  - 위험: 다른 latch 종류(향후 suspend 등)를 추가할 때 또다시 구조 조정이 필요할 수 있음.
- **Option 2 – Multi-latch container per (die, plane) keyed by kind**
  - 장점: READ/PROGRAM 뿐 아니라 향후 latch 종류 증가에도 대응 가능.
  - 단점: 기존 코드 전반이 단일 `_Latch` 전제에 맞춰져 있어 비교적 큰 리팩터링 요구.
  - 위험: release 시 특정 kind 미삭제 또는 중복 삭제 같은 신규 버그가 생길 수 있어 회귀 테스트 필요.

## 역사적 맥락
- `research/2025-09-23_17-18-10_suspend_resource_effects.md:49` 는 suspend 처리 시 latch 해제가 외부 호출에만 의존한다는 점을 기록했으며, 이번 분석은 해당 취약점이 latch 종류별 충돌로 이어질 수 있음을 확장한다.

## 관련 연구
- `research/2025-09-23_17-18-10_suspend_resource_effects.md`

## 미해결 질문
- READ latch 를 다시 활성화할 계획이 있는지, `after_read` 그룹 정의가 의도된 것인지 사양 확인 필요. -> (검토완료) 'after_read' 그룹이 비어있는 것은 되도된 것이나, 추후 변경 필요하므로 활성화 가능.
- release 경로에서 latch 종류를 구분하지 않는 이유와 하드웨어 동작 대비 차이를 검증할 필요가 있다. -> (검토완료) 구현 단계에서 고려하지 못한 부분. latch 는 분리하는 게 맞음
- multi-latch 설계 채택 시 snapshot/restore 및 exporter 영향 범위를 어떻게 검증할지 테스트 전략이 요구된다. -> (TODO) research 필요.
