---
date: 2025-09-04T22:04:48+0900
researcher: codex
git_commit: 9e840e6b69dbbbf01f40b0248cdf627695263c48
branch: main
repository: nandseqgen_v2
topic: "Validator를 ResourceManager에 통합하고 validation 항목을 관리하기 위한 구조"
tags: [research, codebase, resourcemgr, validator, rules, config]
status: complete
last_updated: 2025-09-04
last_updated_by: codex
last_updated_note: "EPR 콜백 표준 인터페이스 설계 추가"
---

# 연구: Validator를 ResourceManager에 통합하고 validation 항목을 관리하기 위한 구조

**Date**: 2025-09-04T22:04:48+0900
**Researcher**: codex
**Git Commit**: 9e840e6b69dbbbf01f40b0248cdf627695263c48
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
Validator를 별도 파일로 두지 않고 ResourceManager에 통합하되, 다양한 validation 항목을 확장 가능하고 일관되게 관리할 수 있는 구조는 무엇인가?

## 요약
- 현재 `ResourceManager`는 이미 다수의 검증을 내장한다: plane/bus 중첩, die‑level single×multi 배제, latch 기반 금지, 타임라인 질의 등. `reserve/feasible_at`에서 reason 코드를 반환한다.
- 누락/보강 대상은 상태 기반 금지(ODT/SUSPEND/CACHE)와 주소 의존 규칙(EPR) 연결부다. 이는 `config.yaml`의 `exclusions_by_*`/`exclusion_groups`와 `AddressManager` 상태를 조합해 평가해야 한다.
- 제안: ResourceManager 내부에 소형 규칙 엔진(규칙 레지스트리)을 두고, 규칙을 카테고리화하여 순차 평가한다. 규칙은 구성(활성화/심각도)으로 제어하고, 주소 의존 규칙은 콜백을 통해 `AddressManager`에 위임한다.

## 상세 발견

### 현 구조와 접점
- Plane/bus/타임라인: `resourcemgr.py:90-110`, `resourcemgr.py:264-305` — plane/bus 중첩, 타임라인 질의/삽입.
- Die‑level 배제(single×multi 등): `resourcemgr.py:200-246`, `resourcemgr.py:318-338` — 허용 베이스 집합 포함.
- Latch 금지: `resourcemgr.py:268-305`, `config.yaml:2006`(exclusions_by_latch_state), `config.yaml:633`(exclusion_groups).
- 스냅샷/복원 및 상태: ODT/CACHE/SUSPEND 보관·질의 제공 — `resourcemgr.py:488-573`, `resourcemgr.py:676-746`.
- 현재 `feasible_at/reserve`는 latch/배타/버스/plane을 검사하지만, `suspend/odt/cache` 기반 금지 및 주소 규칙(EPR)은 일관된 진입점에 없음.

### PRD §5.7 Validator 정합성 체크리스트
- 참조: `docs/PRD_v2.md:350`
- epr_dependencies: 본 문서의 EPR 콜백으로 커버(아래 후속 연구 섹션).
- IO_bus_overlap: RM의 버스 세그먼트 충돌 검사로 커버(`resourcemgr.py:96-110`).
- exclusion_window_violation: RM의 cfg 기반 배타창 파생/적용으로 커버(`resourcemgr.py:311-326` + 커스텀 규칙 카테고리).
- forbidden_operations_on_latch_lock: RM의 `_latch_ok`로 커버(`resourcemgr.py:268-305`).
- logic_state_overlap: RM의 타임라인 중첩 질의로 커버(`resourcemgr.py:264-305`의 `has_overlap` 경유). PRD 문구는 AddressManager로 표기되었으나 실제 구현은 RM에 존재.
- forbidden_operations_on_suspend_state: 상태 금지 규칙으로 커버(`config.yaml:2012`).
- operation_on_odt_disable: 상태 금지 규칙으로 커버(`config.yaml:2017`).
- (추가) forbidden_operations_on_cache_state: PRD 테스트 섹션에서 요구(`docs/PRD_v2.md:413`), 상태 금지 규칙으로 커버(`config.yaml:2020`).

### 관리 가능한 통합 구조 제안

1) Rule 인터페이스(경량)
- 시그니처: `rule(ctx) -> (ok: bool, code: str | None, msg: str | None)`
- 컨텍스트 `ctx`: `{ op, base, op_name, targets, die, plane_set, scope, start, end, cfg, rm }`
- 카테고리: `timeline`(plane/bus), `die_excl`(single×multi), `state_forbid`(latch/suspend/odt/cache), `addr_dep`(EPR), `custom`.

2) 규칙 레지스트리와 순서
- `self._rules: List[(name, category, fn, enabled, severity)]`
- 기본 순서(빠른 실패 우선):
  - timeline: plane → bus
  - die_excl: single/multi 충돌
  - logic_overlap: op_state 타임라인 중첩(`has_overlap` 기반)
  - state_forbid: latch → suspend → odt → cache
  - addr_dep: AddressManager 콜백 평가
  - custom: cfg 기반 배타창 등
- 구성으로 on/off 가능: `cfg['constraints']['enabled_rules']`에 이름 또는 카테고리.

3) 상태 기반 금지(state_forbid)
- Latch: 현 `_latch_ok` 유지하되 rule로 래핑.
- Suspend/ODT/Cache: `config.yaml`의 매핑 사용. 예: `exclusions_by_suspend_state[state] -> groups -> bases` 금지.
  - 의사코드: 현재 상태(예: `rm.suspend_states(die)`/`rm.odt_state()`/`rm.cache_state(die,plane,start)`)를 조회 → 그룹으로 매핑 → base 포함 여부로 차단.

4) 주소 의존 규칙(addr_dep)
- `ResourceManager`는 주소 상태를 모름. 콜백 인터페이스를 주입: `self.addr_policy: Optional[Callable[[op, targets, start], Tuple[bool, str|None]]]`.
- 기본은 `None`(skip). 통합 배치는 `AddressManager`가 제공하는 `check_epr(op, targets, start)` 같은 함수를 연결.
  - 규칙 예: `program_before_erase`, `read_before_program_with_offset_guard`, `programs_on_same_page`, `celltype consistency` 등.

5) 결과 표준화
- 공통 결과 타입을 재사용: `Reservation`(ok/reason/start/end). `feasible_at`/`reserve`는 내부적으로 `validate(ctx)`를 호출해 최초 실패 코드를 수집하고, 성공 시 start/end만 반환.
- `explain=True` 옵션으로 모든 실패 원인을 배열로 반환 가능(디버그/로그용).

6) 구성 관리와 확장성
- 규칙 정의는 고정 코드 + cfg 매핑을 혼합. 새 상태/그룹은 전부 `config.yaml`을 통해 통제 가능.
- 규칙 on/off와 심각도(severity: error/warn)는 `cfg['constraints']['rules']`에 선언. warn은 스케줄은 허용하되 사유를 로깅.

### 대안 비교(간단)
- A) 규칙 레지스트리(상기 제안)
  - 장점: 확장 용이, on/off/우선순위 제어, 테스트 단위화 용이
  - 단점: 약간의 보일러플레이트, 성능 고려(하지만 규칙 수가 적어 영향 작음)
- B) 단일 `validate()`에 if-else 나열
  - 장점: 구현 단순, 현재 코드와 자연스럽게 통합
  - 단점: 스파게티화 위험, on/off/우선순위 제어 어려움
- C) 내부 중첩 클래스로 `Validator`(별도 파일 아님)
  - 장점: 관심사 분리, 테스트 용이
  - 단점: API 경계가 생겨 호출/상태 전달량 증가

권고: A → 가장 관리/확장에 유리. 초기에는 B로 시작해 A의 형태(리스트/등록자)로 리팩토링하는 하이브리드도 가능.

## 코드 참조
- `resourcemgr.py:96` - plane/bus/타임라인 기초 로직.
- `resourcemgr.py:200` - single×multi 판정 및 충돌 규칙.
- `resourcemgr.py:268` - latch 금지 평가(`exclusions_by_latch_state` → `exclusion_groups`).
- `resourcemgr.py:318` - 예약 경로에서 배제 창/라치/버스/plane 검사 순서.
- `resourcemgr.py:488` - ODT state 질의/제어.
- `resourcemgr.py:546` - suspend state 질의/제어.
- `resourcemgr.py:520` - cache state 질의.
- `config.yaml:633` - `exclusion_groups` 정의.
- `config.yaml:2006` - `exclusions_by_latch_state` 매핑.
- `config.yaml:2012` - `exclusions_by_suspend_state` 매핑.
- `config.yaml:2017` - `exclusions_by_odt_state` 매핑.
- `config.yaml:2020` - `exclusions_by_cache_state` 매핑.
- `docs/PRD_v2.md:5.7 Validator` - 항목 목록과 의도.

## 아키텍처 인사이트
- 시간 판정은 전부 좌폐우개·양자화로 일관성 확보. 규칙 평가도 동일 기준 준수 필요.
- 상태 기반 금지(라치/서스펜드/ODT/캐시)는 전부 config 구동이므로, cfg 변경만으로 정책을 조정할 수 있게 매핑 기반으로 일반화한다.
- 주소 의존(EPR) 검증은 상태 소유권(AddressManager)을 존중하고 콜백으로 느슨 결합한다.
- 실패 사유 코드는 소수 정해진 명세로 유지(planescope/bus/exclusion_multi/exclusion/latch/suspend/odt/cache/epr_dep 등). UI/로그/통계에 그대로 사용.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-04_01-40-27_resourcemgr_unittest_plan.md` — 타이밍/락/배제의 경계 동작과 테스트 계획 정리.

## 관련 연구
- `docs/NAND_BASICS_N_RULES.md` — latch/suspend/odt/cache 및 주소 의존 규칙 서술.

## 미해결 질문
- `exclusions_by_op_state`와 `phase_conditional`의 상호작용: state 기반 금지와 샘플링 확률을 어떤 순서로 적용/정규화할지.
- EPR 콜백의 표준 인터페이스: 어떤 입력/출력 스키마로 합의할지(복수 실패 사유 반환 필요?).
- 규칙 평가 성능: 매우 큰 타임라인/창 개수에서의 O(n)→O(log n) 최적화 필요성(인덱싱/트리 구조 도입 시점).

## 후속 연구 2025-09-04T22:14:48+0900 — EPR 콜백 표준 인터페이스

### 목적
- PRD §5.7의 EPR(dependencies) 규칙을 AddressManager의 상태(addr_state/modes/offset)를 기준으로 결정론적으로 평가한다.
- ResourceManager 내부 Validator는 주소 규칙을 콜백으로 위임해 결합도를 낮추고, 결과를 표준 코드로 수집한다.

### 설계 원칙
- 순수 함수: 내부 상태 조회만 수행, 변이/IO 없음(테스트·재현성 보장).
- 결정론: 동일 입력/상태에서 항상 동일 결과.
- 다중 대상: single/multi-plane, sequence 제안에서 모든 타깃을 함께 평가.
- 증거 포함: 실패 시 (die, block[, page]) 단위 근거를 반환.

### 입력 모델(표준)
- 위치: `AddressManager.check_epr(...)` (권장) 또는 RM 주입 콜백.
- 시그니처(권장):
  - `check_epr(base: str, targets: List[Address], *, op_name: Optional[str] = None, op_celltype: Optional[str] = None, as_of_us: Optional[float] = None, pending: Optional[EprOverlay] = None, offset_guard: Optional[int] = None) -> EprResult`
- 매개변수 의미:
  - `base`: 오퍼레이션 베이스(예: `ERASE`, `PROGRAM_SLC`, `READ`, `PLANE_READ`, ...).
  - `targets`: `resourcemgr.Address(die, plane, block, page?)` 리스트. READ/PROGRAM은 page 필요, ERASE는 page=None 허용.
  - `op_name`: 세부 op_name(선택). 로깅/추적용.
  - `op_celltype`: PROGRAM/READ에서 기대 celltype(선택). 없으면 내부 모드 일관성 규칙만 적용.
  - `as_of_us`: 시뮬레이션 기준시각(선택). 순수 주소 규칙은 시간 비의존이나, 스냅샷 일관성 로깅에 활용.
  - `pending`: 동일 제안(txn/sequence) 내 선행 예약으로 예상되는 상태 델타 오버레이(선택). 아래 참조.
  - `offset_guard`: READ offset 가드. 미지정 시 `self.offset` 사용.

#### EprOverlay(권장 형태)
- 목적: “reserved_at_past_addr_state” 방지. 동일 제안 내 선행 동작의 효과를 반영해 평가.
- 형태:
  - `EprOverlay = Dict[Tuple[int,int], { 'addr_state': int | None, 'mode_erase': str | None, 'mode_pgm': str | None }]`
  - 키: `(die, block)`
  - 값: 덮어써야 할 예상 상태(예: ERASE 후 `addr_state=-1`, PROGRAM 후 `addr_state=last+1`, 모드 설정 등)

### 출력 모델(표준)
- 결과 타입:
  - `EprResult.ok: bool` — 모든 규칙 통과 여부
  - `EprResult.failures: List[EprFailure]` — 실패 목록(비어있음=통과)
  - `EprResult.warnings: List[EprFailure]` — 경고(스케줄 허용 가능)
  - `EprResult.checked_rules: List[str]` — 평가한 규칙 코드(선택)
- 실패 항목:
  - `EprFailure.code: str` — 안정된 규칙 코드(아래 표준 코드)
  - `EprFailure.message: str` — 간결 설명(영/한 병기 가능)
  - `EprFailure.evidence: Dict[str, Any]` — `die`, `blocks`, `planes`, `pages`, `expected`, `found` 등

#### 표준 코드 집합(초안)
- `epr_reserved_at_past_addr_state`
- `epr_program_before_erase`
- `epr_read_before_program_with_offset_guard`
- `epr_programs_on_same_page`
- `epr_different_celltypes_on_same_block`

### 규칙 정의(요지)
- program_before_erase: `(die,block).addr_state == ERASE(-1)`가 아닌 곳에 PROGRAM 금지.
- read_before_program_with_offset_guard: READ 대상 페이지 `p`는 `(last_pgmed_page - offset_guard)` 이하에서만 허용. 즉, `p > last_pgmed_page - offset_guard`이면 금지. `last_pgmed_page = addr_state`.
- programs_on_same_page: 동일 `(die,block,page)`에 2회 이상 PROGRAM 금지(overlay 포함).
- different_celltypes_on_same_block: 동일 `(die,block)` 내 program/read는 일관된 celltype을 유지. ERASE가 TLC/AESLC/FWSLC면 동일 유지, SLC면 SLC/A0SLC/ACSLC만 허용.
- reserved_at_past_addr_state: `pending`에 의해 이미 변화될 상태를 무시하고 과거 상태로 판정하는 사용 금지(overlay를 우선 적용).

### RM 통합 포인트(권장 흐름)
- RM이 `reserve/feasible_at`에서 시간·락·배타 검증 이후, `state_forbid` 이전/이후 어느 한 지점에서 EPR 콜백 호출.
- 결과 매핑: `ok=False`면 `Reservation(False, 'epr_dep', ...)`로 요약하고, 내부 디버그용으로 `subcodes=[f.code...]`, `evidence`를 부가 저장(옵션).
- 활성화 제어: `cfg['constraints']['enabled_rules']` 또는 `cfg['constraints']['enable_epr']=true`.

### 예시 시그니처(Python 타입 힌트)
```python
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

@dataclass
class EprFailure:
    code: str
    message: str
    evidence: Dict[str, Any]

@dataclass
class EprResult:
    ok: bool
    failures: List[EprFailure]
    warnings: List[EprFailure]
    checked_rules: List[str]

EprOverlay = Dict[Tuple[int, int], Dict[str, Any]]  # (die, block) -> overrides

def check_epr(
    base: str,
    targets: List[Address],
    *,
    op_name: Optional[str] = None,
    op_celltype: Optional[str] = None,
    as_of_us: Optional[float] = None,
    pending: Optional[EprOverlay] = None,
    offset_guard: Optional[int] = None,
) -> EprResult: ...
```

### 테스트 가이드
- 동일 블록 연속 시퀀스(ERASE→PROGRAM→READ)에서 `pending` 오버레이 반영으로 오류가 사라지는지.
- 오프셋 가드: `offset_guard=0/1/N` 경계에서 허용/거부가 결정적으로 바뀌는지.
- 다중 plane/블록: 각각 독립 판정, 실패가 부분 집합 증거와 함께 수집되는지.

### 성능/확장
- AddressManager의 numpy 배열 상태를 벡터화 평가(가능 시)하여 O(#targets) 내 동작.
- 코드/메시지/증거 스키마는 고정. 규칙 추가 시 `code`만 확장.
