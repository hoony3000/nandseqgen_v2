---
date: 2025-09-03T00:15:02+09:00
researcher: Codex
git_commit: 013916e2bf4c7c33f0cf2b0f45b7ada4ec14edb9
branch: main
repository: nandseqgen_v2
topic: "AddressManager-ResourceManager 인터페이스 및 독립 테스트 전략"
tags: [research, codebase, AddressManager, ResourceManager, testing, decoupling, transactions]
status: complete
last_updated: 2025-09-03
last_updated_by: Codex
last_updated_note: "nandsim_demo.py 기반 ResourceManager 자원 조사/분류 추가"
---

# 연구: AddressManager-ResourceManager 인터페이스 및 독립 테스트 전략

**Date**: 2025-09-03T00:15:02+09:00
**Researcher**: Codex
**Git Commit**: 013916e2bf4c7c33f0cf2b0f45b7ada4ec14edb9
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
AddressManager 와 ResourceManager 의 인터페이스를 구상하고, 상호작용 검증은 나중에 하되 우선 각 요소를 독립적으로 테스트하는 방법을 마련한다. 핵심은 둘 간의 복잡성이 전파되지 않도록 경계를 명확히 하는 것이다.

## 요약
- 최소 인터페이스 원칙: AddressManager는 주소 상태/모드와 샘플링만, ResourceManager는 타임라인·배제·락만 책임. 서로의 내부 구조에 의존하지 않음.
- 순수(Sampling)와 적용(Commit) 분리: AddressManager는 `sample_*` 계열은 순수 함수, `apply_*`는 명시적 Txn 핸들에만 기록. ResourceManager는 `precheck → reserve → commit/rollback`로 트랜잭션 경계 유지.
- 결정성 확보: RNG/시간/구성(CFG) 주입으로 테스트에서 시드 고정, 모의 시간 사용. 스냅샷/복구로 회귀 테스트 강화.
- 독립 테스트: 각 매니저는 Fake/Stub을 통해 상대방 없이 검증. 계약(Contract) 테스트로 인터페이스 수준의 호환성 보장.
- 상호작용 검증은 얇은 계약 기반 통합 테스트로 제한하여 복잡성 전파를 차단.

## 상세 발견

### AddressManager 인터페이스(샘플링/상태)
- 책임: op_base(ERASE/PGM/READ)와 cell_mode를 받아 제약을 만족하는 주소 후보 제안 및(선택적으로) 상태 적용.
- 제안 인터페이스
  - `from_topology(topology, init, offset, badlist) -> AddressManager`
  - 순수 샘플링: `sample_erase(size, mode) -> ndarray`, `sample_pgm(size, mode, sequential:bool) -> ndarray`, `sample_read(size, mode, offset:int, sequential:bool) -> ndarray`
  - 적용(선택): `apply_erase(txn, addrs, mode)`, `apply_pgm(txn, addrs, mode)`, `apply_read(txn, addrs)`
  - 스냅샷: `snapshot() -> dict|paths`, `restore(snapshot)`
  - 프로퍼티: `num_dies, num_planes, num_blocks, pagesize, offset`
- 테스트 포인트(독립)
  - 샘플링 불변식: ERASE는 비-Erase 블록만 선택, PGM은 ERASE 이후 페이지 증가, READ는 `offset` 이상/동일 `mode`만 선택.
  - 순차 샘플링: `sequential=True` 시 블록 내 연속 페이지 보장.
  - 적용 결과: `apply_*` 후 내부 배열 변화가 기대와 일치(페이지 증가/ERASE -> ERASE state 등).
  - 스냅샷 라운드트립: `snapshot→restore` 후 동일 토폴로지에서 동일 결과.
- 코드 근거
  - `addrman.py:65` AddressManager 클래스
  - `addrman.py:342` `from_topology` 초기화
  - `addrman.py:405` `random_erase` 경로
  - `addrman.py:490` `random_pgm` 경로
  - 주: 현 구현은 샘플+적용이 결합되어 있어(`random_*`) 테스트/경계 분리를 위해 `sample_*`/`apply_*`로 쪼개는 리팩터링을 제안

### ResourceManager 인터페이스(타임라인/배제/락)
- 책임: 각종 타임라인, 배제 윈도우, 래치/ODT/서스펜드, IO 버스 상태의 권위 있는 소스. 스케줄 검증·예약·커밋.
- 제안 인터페이스
  - 시간/트랜잭션: `begin(now) -> Txn`, `reserve(txn, op, targets, scope, duration) -> Reservation`, `commit(txn)`, `rollback(txn)`
  - 질의: `feasible_at(op, targets, start_hint) -> time|None`, `op_state(die, plane, at)`, `has_overlap(scope, interval)`, `latch_state(die,plane)`, `exclusions(scope)`
  - 스냅샷: `snapshot()`, `restore(snapshot)`
  - 정책 적용: CFG 기반 배제 그룹/락 전이/ODT/서스펜드 처리
- 테스트 포인트(독립)
  - 시간 중첩: 같은 die/plane 범위에서 윈도우 겹치면 거절.
  - 배제 그룹: single×multi, multi×multi 금지 시나리오 검증.
  - 래치 전이: READ 후 cache_latch 기간 중 금지된 오퍼레이션 거절.
  - 서스펜드/리줌: suspend→resume 후 타임라인 복원 및 재스케줄 규칙.
  - 스냅샷 라운드트립: timeline/locks/windows round-trip 무결성.
- 문서 근거
  - `docs/PRD_v2.md:300`–`docs/PRD_v2.md:325` ResourceManager 상태/스냅샷
  - `docs/PRD_v2.md:211` Scheduler가 ResourceManager를 통해 state update

### 경계 설계: 복잡성 전파 차단
- 데이터 계약(DTO): 주소는 `(die, plane, block, page)`의 불변 튜플/ndarray로만 교환. 내부 배열 포인터/뷰 노출 금지.
- 트랜잭션 핸들만 공유: AddressManager는 `apply_*` 시 ResourceManager의 `Txn` 타입만 의존(구현체 세부를 몰라도 됨).
- 읽기/쓰기 인터페이스 분리: AddressManager는 `Reader`로도 생성 가능(읽기 전용), ResourceManager도 `Reader`/`Writer` 역할 별도 타입 분리.
- 결정성 주입: `rng: np.random.Generator`, `clock: Callable[[], float]`를 외부에서 주입하여 테스트 고정.

## 코드 참조
- `docs/PRD_v2.md:327` - AddressManager 범위/속성/스냅샷 요구
- `docs/PRD_v2.md:211` - Scheduler를 통한 ResourceManager state update
- `addrman.py:65` - AddressManager 클래스 정의
- `addrman.py:342` - `from_topology` 초기화 유틸
- `addrman.py:405` - `random_erase` 샘플링/적용 경로
- `addrman.py:490` - `random_pgm` 샘플링/적용 경로
- `tools/bench_addrman.py:26` - `from_topology` 사용 패턴, RNG 주입 예시

## 아키텍처 인사이트
- 패턴: Unit of Work(트랜잭션), Ports & Adapters(인터페이스 분리), Pure Functions(샘플링).
- 경계: AddressManager는 주소 상태/샘플링, ResourceManager는 타임라인/락. 상호 호출 없음. 상호작용은 Scheduler/Validator에서만 조정.
- 스냅샷 우선: 대형 배열은 사이드카 `.npy`(PRD 3.6)로, 인덱스 JSON은 마지막에 기록하는 원자성 유지.

## 독립 테스트 전략(구체안)
- AddressManager 단위 테스트
  - 토폴로지 미니멀: `dies=1, planes=2, blocks=16, pages=8` 고정 RNG로 케이스 생성.
  - 속성 기반: `sample_read`는 `page>=offset`/`mode` 일치. `sample_pgm(seq=False)`는 서로 다른 블록/페이지. `seq=True`는 연속 페이지.
  - 적용 검증: `apply_erase/pgm` 후 상태 배열 불변식 유지(페이지 상한, ERASE→0…), 스냅샷 라운드트립.
- ResourceManager 단위 테스트
  - 시간 격자: 단순화된 us 단위 타임라인에서 scope별 충돌 케이스 테이블테스트.
  - 배제 그룹/래치: PRD 정의 그룹을 작은 CFG로 구성해 금지 케이스를 빠짐없이 검증.
  - 서스펜드/리줌: 예약→suspend→resume 후 윈도우/상태 일관성 확인.
- 계약(Contract) 테스트
  - 추상 베이스 테스트 스위트: `IAddressSampler`, `IResourceTimeline`에 대한 공통 테스트를 정의. 실제/페이크 구현 모두 통과해야 함.
  - FakeResourceManager: 메모리 내 간단 구현으로 AddressManager의 `apply_*` 연동만 검증.
- 결정성/시드
  - 전역 RNG/시간을 테스트에서 고정(PRD 7.1), 시스템 시간 사용 금지.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-02_23-58-12_interfaces.md` - 초기 인터페이스 구상 및 샘플링/적용 분리 아이디어가 정리되어 있음.

## 관련 연구
- `tools/bench_addrman.py` 벤치 하네스는 현재 RNG 주입/상태 스냅샷 접근을 보여주는 유용한 예시로, 테스트 픽스처에 재사용 가능.

## 미해결 질문
- 트랜잭션 경계: Address/Resource의 커밋 타이밍을 단일 커밋으로 묶을지(원자성) 또는 단계화할지.
- `sample_*`와 `apply_*`의 API 모양: ndarray vs 도메인 객체(타입 안정성/가독성 트레이드오프).
- 다중 플레인/다중 다이에서의 스코프 규칙(plane-set, die-wide)의 표준화 이름/표현.
- 성능 vs 결정성: 매우 큰 배열에서의 oversampling/weighted 선택의 비용과 테스트 속도 균형.

