---
title: "구현 계획(B안/일반화): inherit 힌트 메타데이터 전파(전 연쇄)"
date: 2025-09-08
based_on:
  - research/2025-09-08_17-05-45_dout_same_celltype_inherit_across_intervening_ops.md
  - plan/2025-09-08_dout_same_celltype_inherit_across_intervening_ops_altB_impl_plan.md
status: draft
owners: ["Codex"]
---

# Problem 1‑Pager

- 배경: PRD v2 §3.1 Operation Sequence에서 일부 오퍼레이션의 payload는 `celltype`을 요구한다. 현재 구현은 DOUT/DOUT4K에 한정해 직전 op만 확인하거나(Exporter), 또는 힌트를 전파하지 않아 SR/Delay 개입 시 `celltype`이 누락될 수 있다.
- 문제: 상속 규칙(`sequence.inherit`)이 있는 모든 후속 오퍼레이션(예: DOUT, DOUT4K, CACHE_READ_END, PLANE_CACHE_READ_END 등)에서 `same_celltype` 의미가 안정적으로 반영되지 않는다. DOUT 한정 해결은 부분 해법이다.
- 목표(G):
  - G1: Proposer→Scheduler→Exporter 경로에 일반화된 `inherit_hints`(특히 `celltype`)를 전파하여, 상속 규칙이 있는 모든 후속 오퍼레이션에서 올바른 `celltype`을 유지한다.
  - G2: CSV/PRD 스키마는 불변으로 유지하고, 내부 row 메타필드만 확장한다.
  - G3: 힌트가 없거나 무효일 때는 안전한 폴백(기존/일반화된 역참조)으로 동작한다.
- 비목표(NG):
  - Exporter의 전면적 역탐색(A안)으로만 해결하는 접근.
  - `same_reg` 등 비‑celltype 파라미터를 Exporter 출력에 즉시 반영(로그/메타 보존은 허용).
- 제약(C):
  - 함수 ≤ 50 LOC, 파일 ≤ 300 LOC, 순환복잡도 ≤ 10 유지.
  - 후방호환: 힌트 부재 시 기존 동작과 동일해야 함.

# 현재 상태 분석

- Exporter `export_operation_sequence`:
  - `needs_cell`일 때 DOUT/DOUT4K에 한해 직전 op만 확인해 `same_celltype` 상속 시도(개입 op가 끼면 실패).
  - 다른 상속 대상(CACHE_READ_END/PLANE_CACHE_READ_END 등)에는 미적용.
- Proposer:
  - `inherit` 규칙을 해석하여 타겟/후속 op 선택은 수행하나, `celltype` 힌트를 구조적으로 보존하지 않음.
- Scheduler/InstrumentedScheduler:
  - 제안시점 컨텍스트(phase hooks)는 전파하나, 상속 힌트는 없음.

# 목표 상태

- 상속 규칙에 `same_celltype`이 포함된 모든 후속 오퍼레이션에서, Exporter가 먼저 제안시점 `celltype` 힌트를 사용한다.
- 힌트가 없으면 일반화된 역참조(같은 (die,plane) 타임라인에서 현재 base의 `same_celltype` 상속을 제공하는 가장 가까운 과거 op)를 적용한다.
- DOUT/DOUT4K뿐 아니라 `CACHE_READ_END`, `PLANE_CACHE_READ_END` 등에도 동일하게 적용한다.
- CSV/타임라인 등 산출물 스키마는 변함없고, 값만 교정된다.

## 수용 기준(AC)
- AC1: READ(TLC) → SR → Delay → DOUT, payload.celltype == "TLC".
- AC2: READ4K(SLC) → SR_ADD → DOUT4K, payload.celltype == "SLC".
- AC3: CACHE_READ → (SR/Delay) → CACHE_READ_END, payload.celltype이 READ와 동일.
- AC4: PLANE_CACHE_READ → ... → PLANE_CACHE_READ_END, 각 plane에 대해 올바른 celltype.
- AC5: CFG에 `same_celltype` 상속 규칙이 없거나 제약 위반이면 기존 값 유지(None/미출력).

# 설계(일반화된 B안 — inherit_hints 전파)

- 핵심 아이디어: Proposer 단계에서 각 스텝이 의존하는 상속 규칙을 보고, 필요한 속성(우선 `celltype`)을 `inherit_hints`에 기록해 Scheduler→Exporter로 전파한다. Exporter는 힌트가 있으면 우선 사용하고, 없을 때만 일반화된 역참조로 보완한다.

## 데이터 모델(내부)
- proposer.ProposedOp
  - meta: Optional[Dict[str, Any]]
    - inherit_hints: Dict[str, Any] — e.g., {"celltype": "TLC"}
    - inherit_from: Optional[str] — 힌트 출처 op_name(관찰성)
    - inherit_rules: Optional[List[str]] — 적용된 상속 규칙(관찰성)
- scheduler._propose_and_schedule(rec)
  - rec["inherit_hints"] = p.meta.get("inherit_hints")
- main.InstrumentedScheduler._OpRow
  - celltype_hint: Optional[str] — Exporter 최적화용 얕은 필드
  - (선택) inherit_hints: Optional[Dict[str, Any]] — 분석/디버그용 보존

## 힌트 생성 규칙(제안 시점)
- 대상: `inherit`에 `same_celltype`이 포함된 모든 후속 스텝(예: DOUT, DOUT4K, CACHE_READ_END, PLANE_CACHE_READ_END, COPYBACK_PROGRAM_SLC.SEQ의 일부 단계 등)
- 값: 직전 단계의 op_name에서 `cfg.op_names[op_name].celltype`을 조회해 `inherit_hints['celltype']`로 설정
  - 멀티‑스텝 SEQ에서는 해당 스텝의 직전 단계 기준(일반 규칙)으로 설정
  - 불분명/누락 시 생략(None 처리 금지)
- 참고: `same_page`/`inc_page`/`prev_page`/`pgm_same_page`/`same_page_from_program_suspend` 등은 주소 생성에 이미 반영되므로 Exporter에는 별도 힌트가 불필요하나, `inherit_rules` 메타로 기록 가능(가시성).
- (선택) `same_reg`는 `inherit_hints['reg']`로 보존하되 Exporter는 사용하지 않음(향후 확장 대비).

## Exporter 일반화
- 기준: `needs_cell = ('celltype' ∈ payload_by_op_base[base])`
- 결정 순서:
  1) row.celltype_hint가 존재하고 유효하면 사용
  2) op_name 정의의 def_cell(존재 시) 사용
  3) 일반화된 역참조: 같은 (die,plane) 타임라인에서 현재 base의 `same_celltype` 상속을 제공하는 가장 가까운 과거 op를 찾아 그 op_name의 celltype 사용
     - 구현: 기존 `_prev_of` 전용 분기를 제거하고, `inherit_map(prev_base).get(cur_base)`에 `same_celltype` 포함 여부를 확인하는 공통 로직으로 교체
- 적용 대상: DOUT, DOUT4K, CACHE_READ_END, PLANE_CACHE_READ_END 등 `needs_cell`이 true이고 op_name에 고정 celltype이 없는 base 전부
- 예외/가드: def_cell이 명시된 경우 힌트/역참조로 override하지 않음(명시 우선)

## Feature Flag
- `features.inherit_hint_propagation: true`(기본) — 힌트 우선 사용을 제어
- (선택) `features.inherit_backref_fallback: true` — 역참조 폴백 활성화 토글(기본 on)

# 구현 단계(Tasks)

1) ProposedOp 메타 확장
- 파일: `proposer.py`
- 변경: `@dataclass(frozen=True) class ProposedOp`에 `meta: Optional[Dict[str, Any]] = None` 유지하되, 내부에서 `inherit_hints` 딕셔너리 키 사용 표준화

2) 상속 힌트 생성
- 파일: `proposer.py`
- 위치: `_expand_sequence_seq` 또는 `_preflight_schedule`
- 로직: 각 후속 스텝 전개 시, 해당 스텝의 `inherit`에 `same_celltype` 포함 시 직전 스텝의 op_name에서 celltype을 조회하여 `inherit_hints['celltype'] = <value>` 설정; `inherit_from`, `inherit_rules` 보강
- 결과: `planned.append(ProposedOp(..., meta={'inherit_hints': {...}, 'inherit_from': prev_name, 'inherit_rules': rules}))`

3) Scheduler 전파
- 파일: `scheduler.py`
- 위치: `_propose_and_schedule`
- 변경: `rec['inherit_hints'] = p.meta.get('inherit_hints') if p.meta else None`

4) Row 스키마/로깅 확장
- 파일: `main.py`
- 위치: `_OpRow`, `_emit_op_events`
- 변경: `_OpRow`에 `celltype_hint: Optional[str] = None` 및 (선택) `inherit_hints: Optional[Dict[str, Any]] = None`
  - 채움: `celltype_hint=(rec.get('inherit_hints') or {}).get('celltype')`
  - (선택) `inherit_hints=rec.get('inherit_hints')`

5) Exporter 일반화
- 파일: `main.py`
- 위치: `export_operation_sequence`
- 변경:
  - 기존 `if needs_cell and base in ("DOUT","DOUT4K"):` 분기를 제거
  - 공통 결정 순서로 교체: (1) row.celltype_hint → (2) def_cell → (3) 일반화된 역참조(`inherit_map` 확인)
  - 역참조 로직은 현재 `_inherit_map_for` + `_prev_of`를 재사용하되, 대상 base 전체에 적용

6) (선택) 관련 대상 확장
- `CACHE_READ_END`, `PLANE_CACHE_READ_END` 외에도 `needs_cell`이 true이고 op_name에 celltype이 정의되지 않은 base가 있다면 동일 적용

7) 테스트
- 단위: Exporter 결정 순서 테스트(힌트 우선/def_cell 우선/역참조 폴백)
- 통합:
  - READ(TLC) → SR → Delay → DOUT → celltype == TLC
  - READ4K(SLC) → SR_ADD → DOUT4K → celltype == SLC
  - CACHE_READ → SR → CACHE_READ_END → celltype 동일
  - PLANE_CACHE_READ(멀티) → interleave → PLANE_CACHE_READ_END → 각 plane celltype 유지
  - 상속 규칙 없음/제약 위반 → 기존 값 유지

# 영향 범위 / 변경 파일
- `proposer.py` — 힌트 생성(meta.inherit_hints)
- `scheduler.py` — 힌트 전파(rec)
- `main.py` — `_OpRow` 필드 추가, `export_operation_sequence` 일반화
- (선택) `config.yaml` — `features.inherit_hint_propagation`, `features.inherit_backref_fallback` 플래그 추가

# 리스크와 완화
- 인터페이스 변경(내부 메타): Optional 기본값으로 후방호환 유지
- Exporter 오탐(과도 상속): `inherit_map(prev_base)[cur_base]`에 `same_celltype` 존재할 때만 적용 + def_cell 우선
- 성능: 역참조는 기존과 동일 수준; 힌트 전파는 상수 비용

# 성공 기준(체크리스트)
- [ ] 힌트가 존재할 때 모든 대상 base에서 올바른 celltype 출력
- [ ] 힌트 부재/무효 시 역참조로 복원 또는 기존 값 유지
- [ ] CSV/타임라인/카운트 산출물 스키마 회귀 없음

# 참고
- 연구: `research/2025-09-08_17-05-45_dout_same_celltype_inherit_across_intervening_ops.md`
- 기존 B안(DOUT 한정): `plan/2025-09-08_dout_same_celltype_inherit_across_intervening_ops_altB_impl_plan.md`
- CFG 상속 예: `config.yaml` READ/READ4K/PLANE_READ/CACHE_READ_END/PLANE_CACHE_READ_END 등

