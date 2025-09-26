---
date: 2025-09-26T15:18:07+09:00
researcher: Codex
git_commit: 82bceb7cc7334dca08a6c48c438549d4f17c9f77
branch: main
repository: nandseqgen_v2
topic: "ResourceManager DIE_WIDE op_state timeline scope"
tags: [research, codebase, resourcemgr, timeline]
status: complete
last_updated: 2025-09-26
last_updated_by: Codex
last_updated_note: "미해결 질문 후속 연구 추가"
---

# 연구: ResourceManager DIE_WIDE op_state timeline scope

**Date**: 2025-09-26T15:18:07+09:00
**Researcher**: Codex
**Git Commit**: 82bceb7cc7334dca08a6c48c438549d4f17c9f77
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
ResourceManager.commit 에서 op_state 를 timeline 에 등록할 때 CFG[op_bases][op_base][scope] 의 값이 DIE_WIDE 라면 target die 내 모든 plane 에 대해서 등록하는 것이 설계 의도이다. 이것을 변경하기 위해 필요한 범위와 risk 를 research 해줘.

## 요약
- ResourceManager.commit 은 txn.st_ops 항목의 plane 값만 사용해 상태 타임라인을 기록하며, 현재는 DIE_WIDE 선언을 무시한다.
- scope 정보는 cfg.op_bases 에 존재하고 proposer 가 Scope.DIE_WIDE 로 전달하지만 st_ops 구조에는 반영되지 않아 commit 단계에서 알 수 없다.
- 타임라인을 전체 plane에 복제하려면 commit 로직 보강과 동시에 중복 방지, suspend/resume 경로, exporter 의존성에 대한 영향 분석이 필요하다.

## 상세 발견

### ResourceManager.commit op_state 기록
- `resourcemgr.py:748` 루프는 `(die, plane, base, st_list, start)` 튜플을 그대로 사용하며 `_affects_state(base)` 가 true일 때만 `_st.reserve_op` 을 호출한다. scope 판단이 없어 DIE_WIDE라도 단일 plane만 기록된다.
- 동일 블록에서 ODT/CACHE/SUSPEND 상태도 plane별로 처리하므로 plane 확장 시 중복 업데이트 가능성이 있다.

### Reservation 경로와 st_ops 생성
- `resourcemgr.py:104` 의 `_Txn.st_ops` 튜플 정의는 scope 없이 plane 단위 정보를 보관한다.
- `resourcemgr.py:741` 및 instant 경로 모두 target마다 `txn.st_ops.append((t.die, t.plane, base, st_list, start))` 를 호출해 diesel wide op라도 target plane 개수만큼 엔트리가 생성된다. 그대로 확장하면 DIE_WIDE 복제 시 plane×target 중복이 발생한다.

### Config DIE_WIDE 선언 및 전달
- `config.yaml:545` 등 다수의 op_base 가 `scope: "DIE_WIDE"` 로 선언되어 있으며 `affect_state: true` 인 경우 타임라인 반영이 기대된다.
- `proposer.py:147` 의 `_base_scope` 가 cfg를 읽어 `Scope.DIE_WIDE` 를 반환하고, scheduler 는 이를 ResourceManager.reserve 로 넘긴다. 즉 reserve/commit 사이에서 scope 정보가 손실된다.

### Suspend/Resume 경로 영향
- `resourcemgr.py:1420` 의 `move_to_suspended_axis` 는 `meta.targets` 에 포함된 plane 집합만 사용해 plane 예약과 타임라인을 절단한다. DIE_WIDE 확장으로 타 plane에도 segment 를 추가하면, meta.targets 가 전체 plane를 포함하지 않는 한 잔여 segment 가 남아 suspend 이후 상태가 끊기지 않는 리스크가 있다.

### Exporter 및 분석 의존성
- `main.py:369` 의 `export_op_state_timeline` 는 snapshot의 `(die, plane)` 키별 segment를 그대로 CSV로 내보낸다. plane 전체 복제 시 출력 행 수가 plane 수 만큼 증가한다.
- `main.py:272` `_build_effective_rows` 는 `(die, plane)` 키로 CORE_BUSY segment 를 찾는다. 동일 op 가 여러 plane로 복제되면 plane 기반 index 는 동작하지만 op_uid 매핑이 plane 수만큼 필요해진다.

## 코드 참조
- `resourcemgr.py:748` - commit이 st_ops plane만 사용해 `_st.reserve_op` 을 호출함.
- `resourcemgr.py:741` - st_ops 생성이 대상 plane별로 append 되어 die-wide 시 중복 가능성이 있음.
- `config.yaml:545` - op_bases에서 scope를 DIE_WIDE 로 선언해 설계 의도를 명시함.
- `proposer.py:147` - cfg scope 값을 Scope enum으로 변환해 ResourceManager.reserve 에 전달함.
- `resourcemgr.py:1420` - suspend 처리 로직이 meta.targets 기반 plane 목록만 절단함.
- `main.py:369` - op_state_timeline exporter가 모든 plane segment 를 그대로 출력함.

## 아키텍처 인사이트
- scope 정의는 proposer→scheduler→ResourceManager.reserve 까지는 유지되나 commit 경로에서 사라진다. state timeline, plane 예약, suspend bookkeeping, exporter가 동일 소스 데이터(_st) 를 공유하기 때문에 한 지점의 변경이 전체 파이프라인에 영향을 준다.
- DIE_WIDE 확장 시 plane 수 배 증가한 segment 로 인해 CSV/메모리 비용이 커지고, state 기반 파생 지표(phase key, effective timeline) 의 정합성 검토가 필요하다.

## 역사적 맥락(thoughts/ 기반)
- 관련 thoughts 문서를 확인하지 못했다.

## 관련 연구
- `research/2025-09-26_14-11-29_suspend_resume_plane_scope.md` - suspend/resume plane 스코프 정합성 조사 결과가 있어 함께 참고 필요.

## 미해결 질문
- st_ops를 scope-aware 구조로 바꿀지, commit에서 cfg를 재조회할지 결정 필요.
- DIE_WIDE multi-plane op에서 plane×target 중복을 어떻게 제거할지 설계 필요.
- suspend/resume, cache, exporter 등 파생 로직이 추가 plane segment 와 호환되는지 회귀 테스트가 요구됨.

## 후속 연구 2025-09-26T15:26:34+09:00

### 1. st_ops scope-aware 설계 대안
- `resourcemgr.py:741` 는 DIE_WIDE 작업이라도 대상 plane 수만큼 `txn.st_ops` 항목을 추가해, commit 시 `range(self.planes)` 확장을 적용하면 중복 삽입 위험이 있다.
- st_ops 수준에서 scope 정보를 유지하도록 `_Txn.st_ops` 형태를 `(die, plane_or_none, scope, base, states, start)` 로 확장하거나, 최소한 DIE_WIDE 에서는 첫 target 에 대해서만 항목을 남기는 가드가 필요하다.
- commit 단계에서 cfg 기반 scope 재조회(`resourcemgr.py:239`) 또는 st_ops 확장 필드를 읽는 방식으로 die-wide 여부를 판정한 뒤, `(die, start, base)` 키로 dedup 집합을 관리하면 multi-plane target 에서도 중복 없이 `_st.reserve_op` 을 각 plane 에 한 번씩만 호출할 수 있다.
- scope 재조회를 선택할 경우, cfg 누락 시 안전 기본값(plane_set) 으로 회귀하도록 방어 로직을 추가해 backward compatibility 를 유지해야 한다.

### 2. suspend/resume·overlay 경로 정합성
- 현재 `register_ongoing` 은 scheduler 가 전달한 실제 target 목록만 저장하기 때문에(`scheduler.py:750-761`), DIE_WIDE 작업은 주로 첫 plane 주소만 meta.targets 에 남는다.
- `move_to_suspended_axis` 와 `resume_from_suspended_axis` 는 `meta.targets` 기반으로 plane 집합을 계산, 타임라인 절단 및 재예약을 수행한다(`resourcemgr.py:1420-1480`, `resourcemgr.py:1584-1636`). die-wide segment 를 모든 plane 에 기록하면, meta.targets 에 포함되지 않은 plane 은 잘리지 않아 CORE_BUSY 잔류가 발생한다.
- 해결책으로는 (a) register_ongoing 시 DIE_WIDE scope 를 감지해 모든 plane 에 대한 synthetic Address 를 주입하거나, (b) suspend/resume 로직이 meta.scope 가 DIE_WIDE 일 때 `range(self.planes)` 를 직접 조회하도록 수정하는 방법이 있다.
- plane 예약 해제 및 overlay 업데이트(`resourcemgr.py:1471-1480`, `resourcemgr.py:1525-1533`)도 같은 plane 집합을 참조하므로, 위 수정과 연동해 전체 plane 에 대한 윈도우 정리가 동작하는지 재검증이 필요하다.

### 3. 회귀 테스트 및 산출물 영향
- 기존 단위 테스트는 단일 plane 구성에 의존하거나(`tests/test_suspend_resume.py:69-114`) die-wide 작업을 plane 0 주소로만 등록한다(`tests/test_resourcemgr_multi_latch.py:95-110`), 따라서 다중 plane 타임라인 복제를 적용하면 예상 결과가 달라질 수 있다.
- 새로운 동작을 검증하려면: (a) 다중 plane topology 에서 DIE_WIDE base 예약 후 모든 plane 의 `_st.by_plane` 에 segment 가 생성되는지 확인하는 RM 단위 테스트, (b) suspend → resume 시 전체 plane segment 가 잘리고 복원되는지 확인하는 회귀 테스트가 필요하다.
- Exporter/분석 경로도 영향을 받는다. `main.py:272-320` 의 effective window 계산과 `main.py:369-420` 의 CSV 정렬 로직이 plane 수 배로 늘어난 segment 를 처리하므로, 샘플 실행에 대한 golden 출력을 갱신하거나 동적 assertion 으로 전환해야 한다.
- phase key 소비자는 `_build_effective_rows` 재분할 이후 op_uid 매핑이 plane 당 한 번씩 존재한다는 가정(`main.py:339-360`)을 유지해야 하므로, 다중 plane 샘플 데이터로 phase key/UID 일관성을 검증하는 통합 테스트 추가도 권장된다.

