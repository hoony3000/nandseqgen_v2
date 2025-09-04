---
title: "Implementation Plan — Validator Integration in ResourceManager (EPR + State Forbid Rules)"
author: codex
date: 2025-09-04
status: draft
owners: [resourcemgr, addrman]
reviewers: [proposer, validator, scheduler]
adr_ref: research/2025-09-04_22-04-48_validator_in_resourcemgr.md
---

## Problem 1‑Pager

- 배경: ResourceManager(RM)은 plane/bus/타임라인/배타/래치 검증을 내부적으로 수행하며 `feasible_at`/`reserve`에서 실패 사유를 반환한다. 누락된 검증은 상태 기반 금지(ODT/SUSPEND/CACHE)와 주소 의존 규칙(EPR)이며, 이는 `config.yaml`의 매핑과 AddressManager(AM)의 상태에 의존한다.
- 문제: Validator를 별도 파일로 두지 않고 RM 내부에서 일관되게 확장 가능한 검증 구조를 제공해야 한다. EPR(주소 의존 규칙)은 AM에 위임하되, RM의 단일 진입점에서 결과를 수집/요약해야 한다.
- 목표: 규칙 레지스트리 기반의 경량 Validator를 RM 내부에 도입하여
  - 상태 금지 규칙(ODT/SUSPEND/CACHE)과 EPR을 평가하고,
  - 구성 가능(enabled/severity)하며,
  - 기존 검증 순서와 충돌 없이 빠른 실패 경로에 통합한다.
- 비목표: 대규모 RM 리팩터링, 전면적인 예외/로깅 체계 개편, 성능 인덱싱 구조(B-Tree 등) 도입.
- 제약: 
  - 기존 `Reservation` 시그니처를 깨지 않는다.
  - 규칙 구현은 50 LOC 이하 함수로 분할한다.
  - 테스트는 결정적·독립적으로 작성하며 외부 시스템은 가짜 콜백으로 대체한다.
  - 파일 크기 확대를 최소화한다(필요 시 경량 래퍼만 RM에 두고 무거운 로직은 AM 콜백에 둔다).

## 접근 대안

1) RM 내부 경량 규칙 레지스트리(권장)
- 장점: 단일 진입점, 기존 흐름과 쉽게 합류, 콜백으로 EPR 분리(결합도↓)
- 단점: RM 파일 크기 증가, 규칙 정의가 분산될 수 있음
- 위험: 규칙 순서/우선순위 오류 시 오동작 가능

2) 별도 Validator 모듈(파일) + RM 훅
- 장점: 관심사 분리, 파일 크기/복잡도 관리 용이
- 단점: 요구사항(분리 금지) 불일치, 호출/데이터 흐름 복잡화
- 위험: 상태 동기화 비용/표준화 실패

결정: 1) 채택. 다만 EPR의 실제 판정 로직은 AM 콜백(순수 함수)으로 위임해 RM 변경량/위험을 최소화.

## 아키텍처/설계

- 규칙 시그니처: `rule(ctx) -> (ok: bool, code: str|None, msg: str|None)`
- 컨텍스트 `ctx`: `{ op, base, op_name, targets, die, plane_set, scope, start, end, cfg, rm, pending_overlay?, addr_policy? }`
- 카테고리: `timeline`, `die_excl`, `logic_overlap`, `state_forbid`, `addr_dep`, `custom`.
- 레지스트리: `self._rules: List[(name, category, fn, enabled, severity)]`
- 순서(빠른 실패 우선): timeline(plane→bus) → die_excl → logic_overlap → state_forbid(latch→suspend→odt→cache) → addr_dep(EPR) → custom.
- 활성화: `cfg['constraints']['enabled_rules']` 또는 카테고리별 on/off. EPR는 `cfg['constraints']['enable_epr']`로 별도 게이트.

핵심 포인트(소스 위치 참조)
- `feasible_at` 진입점: `resourcemgr.py:324` — 기존 plane/bus/single×multi/legacy excl/latch 이후 또는 일부를 규칙화.
- `reserve` 진입점: `resourcemgr.py:345` — 동일 순서로 평가하되, txn의 pending 윈도우/오버레이를 포함.
- 래치 검증: `_latch_ok` 유지(`resourcemgr.py:268`), 규칙 래핑만 추가.
- 상태 질의: `odt_state`, `cache_state`, `suspend_states` 활용(`resourcemgr.py:490+`).

EPR 콜백(AM) 표준
- 위치/시그니처: `AddressManager.check_epr(base, targets, *, op_name=None, op_celltype=None, as_of_us=None, pending=None, offset_guard=None) -> EprResult` (research 문서 표준 준수)
- 결과: `EprResult.ok`, `failures[{code,message,evidence}]`, `warnings`, `checked_rules`.
- 표준 코드(초안):
  - `epr_reserved_at_past_addr_state`, `epr_program_before_erase`, `epr_read_before_program_with_offset_guard`, `epr_programs_on_same_page`, `epr_different_celltypes_on_same_block`.

Pending Overlay(동일 시퀀스 내 선행 예약 반영)
- RM에서 구축: `reserve()` 시 현재 op가 PROGRAM/ERASE면 overlay에 (die,block)[,page] 효과를 기록.
  - ERASE: `addr_state=-1`, `mode_erase=<celltype>`, `mode_pgm=TBD`.
  - PROGRAM: `addr_state=targets의 마지막 page`, `mode_pgm=<celltype>`.
- READ: 상태 변화 없음.
- 오버레이 형태(권장): `Dict[(die,block)] -> {'addr_state': int|None, 'mode_erase': str|None, 'mode_pgm': str|None}` (research 준수).

구성(config)
- `constraints.enabled_rules`: [문자열] — 이름 또는 카테고리.
- `constraints.enable_epr`: bool (기본 false)
- `constraints.epr.offset_guard`: int (기본 AddressManager.offset)
- 상태 금지 매핑(이미 존재): `exclusions_by_suspend_state`, `exclusions_by_odt_state`, `exclusions_by_cache_state` + `exclusion_groups`.

Reservation.reason 코드 매핑(요약)
- `planescope`, `bus`, `exclusion_multi`, `exclusion`, `latch` (기존 유지)
- 신설(요약용): `state_forbid_suspend`, `state_forbid_odt`, `state_forbid_cache`, `epr_dep`, `logic_overlap`
- 세부 서브코드(EPR)는 내부 디버그 저장소(`self._last_validation`)에 저장하고 API 변경은 보류.

## 변경 사항(스코프/작업)

1) 규칙 레지스트리/평가기 추가 — RM 내부
- `ResourceManager.__init__`: 규칙 테이블 초기화, `self.addr_policy: Optional[Callable] = None`, `self._last_validation: Optional[dict] = None` 추가.
- `ResourceManager.register_addr_policy(fn)`: EPR 콜백 주입용 헬퍼.
- `ResourceManager._eval_rules(ctx)` 구현: enabled rules만 순서대로 평가, 최초 실패 시 즉시 반환.
- 상태 금지 규칙 3종 구현: suspend/odt/cache. 각 30~40 LOC 내에서 cfg 매핑→금지 판정. 래치는 기존 `_latch_ok` 래핑.

2) `feasible_at`/`reserve`에 규칙 호이스팅
- `feasible_at`(`resourcemgr.py:324`): 기존 통과 후 `state_forbid`→`addr_dep`(EPR) 평가. 실패 시 `None` 반환.
- `reserve`(`resourcemgr.py:345`): 동일 순서로 평가하고 실패 시 `Reservation(False, reason, ...)` 반환.
- `reserve`에서 pending overlay 구축 및 EPR 호출 시 전달.

3) AddressManager 확장(콜백)
- `AddressManager.check_epr(...) -> EprResult` 추가(순수 함수, 상태 조회/불변).
- 기본 구현은 표준 규칙 4~5개(연구 문서)와 증거 반환을 포함.
- 퍼포먼스: numpy 벡터화로 O(#targets) 유지.

4) 구성/게이트
- `config.yaml`에 `constraints.enable_epr`, `constraints.enabled_rules`, `constraints.epr.offset_guard` 키 추가/문서화(키 존재 시 사용, 미존재 시 안전한 기본값 유지).

5) 디버그/관찰성
- `ResourceManager.last_validation()` 추가: 최근 평가 결과 반환(사유 코드, 서브코드, evidence 스냅샷 포함). 로그에 민감정보 금지.

## 상세 구현 단계(스프린트 단위)

Phase 0 — 가드레일/스켈레톤 (완료)
- RM: `__init__`에 필드 추가(`addr_policy`, `_last_validation`), `register_addr_policy`, `_rules_cfg`, `_eval_rules`(no‑op) 추가.
- 통합 지점: `feasible_at`/`reserve`에 규칙 평가 훅 삽입(기능 플래그 off 기본, 동작 불변).
- 코드 참조: `resourcemgr.py:97-118`(필드), `resourcemgr.py:336-349`(feasible_at 훅), `resourcemgr.py:362-371`(reserve 훅), `resourcemgr.py:812-866`(스켈레톤 메서드들; 라인은 로컬 차이 가능).

Phase 1 — 상태 금지 규칙(State Forbid) (완료)
- 규칙 3종 구현: `forbid_on_suspend`, `forbid_on_odt`, `forbid_on_cache`.
- cfg 매핑: `exclusions_by_*_state` → `exclusion_groups` → base 포함 시 차단.
- 코드 참조: resourcemgr.py: `_rule_forbid_on_suspend`, `_rule_forbid_on_odt`, `_rule_forbid_on_cache`, `_blocked_by_groups`, `_eval_rules` 분기.
- 단위 테스트: 별도 커밋에서 추가 예정(현재 기본 off로 회귀 영향 없음).

Phase 2 — EPR 콜백 연동 (구현 완료, 테스트 보류)
- AM: `check_epr` + 데이터클래스 `EprResult`, `EprFailure` 구현(`addrman.py`).
- RM: `addr_dep.epr` 통합 — `addr_policy` 등록 시 호출, 실패 시 `epr_dep` 요약 및 `epr_failures` 서브코드 저장.
- 오버레이: `_Txn.addr_overlay` 추가 및 `reserve()` 성공 시 `_update_overlay_for_reserved`로 갱신. EPR 호출 시 전달.
- 게이팅: `constraints.enable_epr=true` 그리고 `constraints.enabled_rules`에 `"addr_dep"` 포함 시 활성화.
- 단위 테스트: 오프셋 가드 경계, program_before_erase, programs_on_same_page, overlay 반영(동일 txn 재프로그램 차단) — 추후 한 번에 추가.

Phase 3 — 순서/성능/회귀 (완료)
- 규칙 순서: plane/bus → die_excl → legacy excl → latch → state_forbid → addr_dep(EPR)로 고정. 빠른 실패 우선 만족.
- 기본 게이트: rules 비활성 시 no-op로 동작(회귀 안전).
- 성능: EPR는 단일 배치 호출(타깃 리스트 전달). 추가 최적화 필요 시 후속 진행.
- 회귀: 기존 테스트군 영향 없음(게이트 off 기본). 신규 테스트로 활성 시나리오 검증.

Phase 4 — 문서/롤아웃 (완료)
- 추가 문서: `docs/VALIDATOR_INTEGRATION_GUIDE.md` — 게이팅, 정책 주입, 사유 코드 요약.
- 기본 설정: `enable_epr=false`(보수 롤아웃). 필요 시 canary 환경에서 `addr_dep`/`state_forbid` 별도 온.

## 코드 삽입 포인트(참조)
- `resourcemgr.py:324` `feasible_at`: 규칙 평가 호출 추가 위치.
- `resourcemgr.py:345` `reserve`: 규칙 평가 호출 + overlay 구성 위치.
- `resourcemgr.py:268` `_latch_ok`: 래치 규칙 래핑 시 참조.
- `resourcemgr.py:497` `cache_state`: 상태 금지 규칙 구현 시 참조.

## 테스트 전략

유닛
- 상태 금지: ODT/SUSPEND/CACHE On/Off 각 2케이스 이상, 허용/거부 명확 경계.
- EPR: program_before_erase, read_before_program_with_offset_guard(offset_guard=0/1/N 경계), programs_on_same_page, different_celltypes_on_same_block, reserved_at_past_addr_state(overlay 사용).

통합/E2E
- 시퀀스: ERASE→PROGRAM→READ 성공 경로 + 실패 경로 각 1개 이상.
- 다중 plane/블록 대상: 부분 실패가 evidence에 수집되는지 확인.

결정성/독립성
- AM 콜백은 순수 함수로 작성, 테스트에서 고정 토폴로지/상태로 재현 가능. 외부 I/O 없음.

## 리스크 및 완화
- RM 파일 비대화: 규칙 본체는 콜백/작은 함수로 유지, 추후 내부 클래스로 캡슐화 리팩터링 제안.
- 예약/커밋 타이밍 불일치: overlay로 동일 txn 내 선행 예약 반영, 테스트로 회귀 방지.
- 성능: 규칙 순서 최적화(빠른 실패 먼저), EPR 벡터화/배치 호출.

## 수용 기준(AC)
- AC1: `enable_epr=false`에서 기존 동작과 결과/성능 동일.
- AC2: 상태 금지 규칙이 cfg에 따라 일관되게 차단/허용.
- AC3: `enable_epr=true`에서 표준 EPR 규칙 4종이 정확히 평가되고 실패 시 `Reservation(False, 'epr_dep', ...)` 요약.
- AC4: 테스트(단위/통합) 녹색, 실패/성공 경로 모두 포함.
- AC5: 문서/샘플 config가 최신 상태와 일치.

## 변경 영향도(요약)
- RM 내부에 경량 레지스트리·콜백 훅 추가(중간 난이도, 표면적 변경). 공용 API 파괴 없음.
- AM에 순수 `check_epr` 추가(독립적, 리스크 낮음). 성능 영향 미미.

## 다음 단계(합의 필요 사항)
- EPR 표준 코드/메시지 확정, evidence 스키마 동결.
- cfg 키 네이밍 최종 확정: `constraints.enabled_rules`, `constraints.enable_epr`, `constraints.epr.offset_guard`.
- Reservation에 세부 실패정보를 노출할지 여부(현 단계에서는 내부 디버그만 권장).

## 작업 목록(TODO)
- [x] RM: 규칙 레지스트리/스켈레톤 추가 + no‑op 통합
- [x] RM: 상태 금지 3종 구현
- [x] RM: 상태 금지 3종 테스트 추가
- [x] AM: `check_epr`/데이터클래스 구현
- [x] RM: EPR 연동(overlay 포함)
- [x] AM/RM: EPR 테스트(경계/실패/overlay)
- [x] 문서/구성/샘플 업데이트

## 테스트 케이스(구현됨)
- tests/test_resourcemgr_state_forbid_and_epr_integration.py
  - state_forbid_odt_blocks_read: ODT_DISABLE 활성 시 READ 차단, reason=state_forbid_odt
  - state_forbid_suspend_blocks_cache_read: ERASE_SUSPEND 활성 시 CACHE_READ 차단, reason=state_forbid_suspend
  - state_forbid_cache_blocks_read: ON_CACHE_PROGRAM 활성 시 READ 차단, reason=state_forbid_cache
  - epr_program_before_erase_blocks: 비‑ERASE 블록에 PROGRAM 시 EPR 차단, reason=epr_dep
  - epr_read_offset_guard_blocks: last_pgmed_page - guard 초과 READ 차단, reason=epr_dep

- tests/test_addrman_epr_rules.py
  - overlay_allows_program_after_erase_in_same_txn: pending overlay(ERASE)로 PROGRAM 허용
  - duplicate_programs_on_same_page_are_blocked: 동일 (die,block,page) 중복 PROGRAM 차단
  - celltype_mismatch_on_block_is_blocked: SLC ERASE 후 TLC PROGRAM 시 차단
