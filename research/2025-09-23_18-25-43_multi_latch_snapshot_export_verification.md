---
date: 2025-09-23T18:25:43+09:00
researcher: Codex
git_commit: 1805c6c27e339c3c571f79966f9dc53d3940cec6
branch: main
repository: nandseqgen_v2
topic: "Multi-latch snapshot/export verification"
tags: [research, resourcemgr, snapshot, exporters, latch]
status: complete
last_updated: 2025-09-23
last_updated_by: Codex
---

# 연구: Multi-latch snapshot/export verification

**Date**: 2025-09-23T18:25:43+09:00
**Researcher**: Codex
**Git Commit**: 1805c6c27e339c3c571f79966f9dc53d3940cec6
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
multi-latch 설계를 도입했을 때 ResourceManager snapshot/restore 및 CSV/JSON exporter들이 영향을 받는 범위를 어떻게 검증할 것인가?

## 요약
- `_latch` 는 단일 `_Latch` 인스턴스를 plane 키에 매핑하고 있어 snapshot/restore/save_snapshot 모두 단일 항목을 가정한다(`resourcemgr.py:1217`, `resourcemgr.py:1321`, `main.py:905`).
- CSV exporter 들은 latch 구조 자체를 직접 소모하지 않지만 `rm.snapshot()`과 `phase_key_at`을 통해 파생 데이터를 읽으므로, multi-latch 도입 시에도 snapshot 반환 포맷과 JSON 직렬화가 후방 호환되어야 테스트로 검증해야 한다(`main.py:861`, `main.py:503`, `main.py:400`).
- 현재 단일 exporter 단위 테스트만 존재하며(`tests/test_address_touch_count.py:10`), snapshot/restore round-trip이나 snapshot JSON 스키마를 커버하는 회귀 테스트가 부재해 multi-latch 전환 전에 테스트 전략을 보강해야 한다.

## 상세 발견

### ResourceManager snapshot/restore 경로
- `_latch` 딕셔너리는 plane 키 하나당 `_Latch` 하나를 저장하며 snapshot 에서 그대로 복제된다(`resourcemgr.py:1217-1241`).
- `restore` 는 snapshot의 `latch` 엔트리가 단일 `_Latch` 객체라고 가정하고 다시 딕셔너리로 역직렬화한다(`resourcemgr.py:1321`).
- multi-latch 컨테이너를 도입하면 snapshot 출력 형식과 restore 역직렬화 로직을 모두 수정해야 하므로, round-trip 테스트로 기존 상태와 동일한 결과가 나오는지 확인할 필요가 있다.

### save_snapshot JSON 직렬화
- `save_snapshot` 은 snapshot의 `latch` 딕셔너리를 `[ {die, plane, start_us, end_us, kind} ]` 리스트로 평탄화한다(`main.py:861-909`).
- 다중 latch 구조에서는 plane 당 여러 레코드를 내보내야 하며 JSON 스키마 변경이 필연적이므로, 스키마 변경 여부와 역직렬화 도우미(향후 restore 입력)까지 검증하는 테스트가 필요하다.

### CSV Exporters의 snapshot 의존성
- `export_operation_timeline` 은 `rm.phase_key_at`을 통해 상태 키를 계산하고, 필요시 snapshot timeline을 참조하는 effective row 계산을 수행한다(`main.py:361`, `main.py:274`).
- `export_op_state_timeline` 및 `export_op_state_name_input_time_count` 는 `rm.snapshot()`의 `timeline` 필드를 직접 소비한다(`main.py:400`, `main.py:503`). latch 데이터 구조 변경이 timeline에 직접 반영되지는 않지만, snapshot 포맷이 깨질 경우 exporter 호출이 실패할 수 있어 실행형 회귀가 필요하다.
- `export_operation_sequence` 는 run 결과에서 전달된 `phase_key_time` 등을 사용하며 latch 구조와 직접적 연결은 없지만, multi-latch 기능 검증을 위한 통합 테스트 시 함께 실행해 CSV 출력 성공 여부를 확인하는 것이 안전하다(`main.py:612`).

### 기존 테스트 커버리지 공백
- 현재 repo에는 exporter 관련 단일 단위 테스트(`tests/test_address_touch_count.py:10`)만 존재하며, snapshot/restore나 JSON 스냅샷을 검증하는 테스트가 없다.
- multi-latch를 적용하면 snapshot과 exporter 모두 구조 변경 가능성이 있어, unit/integration 테스트 레이어를 추가하지 않으면 회귀를 탐지하기 어렵다.

### Verification Alternatives
- **Alternative 1 – Focused unit round-trip tests**
  - 장점: ResourceManager 인스턴스를 직접 구성해 multi-latch 상태를 주입하고 `snapshot() -> restore()` 및 `save_snapshot()` JSON을 비교하는 테스트 작성이 간단하다.
  - 단점: scheduler/proposer 흐름을 거치지 않아 실제 시나리오에서 latch 조작이 누락될 수 있다.
  - 위험: 수동으로 구성한 테스트 상태가 실제 시뮬레이션과 달라 구조적 버그를 놓칠 수 있다.
- **Alternative 2 – Mini run integration harness**
  - 장점: `run_once` 를 사용해 READ/PROGRAM 충돌 시나리오를 재현하고 모든 exporter와 `save_snapshot`을 실행해 multi-latch 포맷과 CSV 출력이 동시에 유효한지 확인할 수 있다.
  - 단점: 테스트 실행 시간이 길고 fixture 구성이 복잡하다(프로포저 후보 구성 필요).
  - 위험: 통합 테스트가 실패 시 원인 파악이 어려울 수 있어, 로그/아티팩트 비교 체계가 필요하다.
- **Alternative 3 – Property-style randomized replay**
  - 장점: 여러 연산 시퀀스를 무작위로 실행해 snapshot/restore 후 상태 동일성을 확인하면 구조 변경에 강건한 회귀 방어선을 제공한다.
  - 단점: 구현 복잡도가 높고 재현성 제어를 위한 시드 관리가 필요하다.
  - 위험: 비교 로직이 복잡해지면 false positive 가능성이 높아지고 유지보수 부담이 커질 수 있다.

## 코드 참조
- `resourcemgr.py:1217` – snapshot이 `_latch` 구조를 단일 `_Latch` 로 직렬화.
- `resourcemgr.py:1321` – restore가 snapshot의 latch 엔트리를 단일 `_Latch` 로 역직렬화.
- `main.py:861` – `save_snapshot` 이 latch를 JSON-friendly 리스트로 변환.
- `main.py:361` – `export_operation_timeline` 가 RM phase key 및 snapshot timeline에 의존.
- `main.py:503` – `export_op_state_name_input_time_count` 가 snapshot timeline을 소비.
- `tests/test_address_touch_count.py:10` – 현재 존재하는 exporter 단위 테스트 예시.

## 아키텍처 인사이트
- snapshot/restore/save_snapshot은 `_latch` 구조에 강하게 결합되어 있어 자료구조 변경 시 세 곳 모두를 동시에 업데이트해야 한다.
- exporter 들은 snapshot의 다른 필드(timeline 등)에 의존하지만 latch 구조 변경으로 snapshot 포맷이 깨지면 연쇄적으로 실패할 수 있어 계약 기반 테스트가 필요하다.
- multi-latch 설계는 plane-키 딕셔너리를 종류별 컨테이너로 확장해야 하므로 JSON/CSV 계약 정의를 명시적으로 문서화하는 것이 안전하다.

## 역사적 맥락
- `research/2025-09-23_18-08-59_program_read_latch.md` – 단일 `_latch` 구조가 READ/PROGRAM 덮어쓰기 문제를 야기한다는 분석.

## 관련 연구
- `research/2025-09-23_17-18-10_suspend_resource_effects.md`

## 미해결 질문
- multi-latch 구조를 JSON으로 내보낼 때 기존 소비자(내부 툴)가 요구하는 스키마를 어떻게 유지하거나 마이그레이션할지 명세 필요.
- integration 테스트에서 사용할 최소 구성(프로포저 후보, config 샘플)을 어디에 두고 재사용할지 결정해야 함.
