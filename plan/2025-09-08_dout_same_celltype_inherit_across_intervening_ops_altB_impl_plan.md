---
title: "구현 계획(B안): DOUT same_celltype 힌트 메타데이터 전파"
date: 2025-09-08
based_on: research/2025-09-08_17-05-45_dout_same_celltype_inherit_across_intervening_ops.md
status: draft
owners: ["Codex"]
---

# Problem 1‑Pager

- 배경: PRD v2 §3.1에 따라 DOUT/DOUT4K payload에는 `celltype`이 포함된다. 현재 exporter는 같은 (die,plane)에서 "직전 op 하나"만 보고 READ 계열의 `same_celltype` 상속을 복원한다(`main.py:~486-540`).
- 문제: READ → (SR/Delay 등) → DOUT 구간에서 중간 비상속성 op가 끼면 직전 op가 READ가 아니므로 상속 실패 → `celltype`이 None/"NONE"으로 출력된다.
- 목표(G):
  - G1: Proposer→Scheduler→Exporter 경로에 `celltype_hint`를 구조적으로 전파하여 개입 op 유무와 무관하게 DOUT이 올바른 celltype을 갖도록 한다.
  - G2: PRD 스키마/출력은 변경하지 않는다(내부 row 메타필드만 추가, CSV 포맷 불변).
  - G3: 다른 소비자(타임라인/카운트 등)와의 호환성을 유지한다(회귀 방지).
- 비목표(NG):
  - Exporter의 역탐색(대안 A)만으로 해결하는 접근(이 계획은 B안 전파형 설계에 한정).
  - DOUT을 celltype별 op_name으로 쪼개는 CFG 세분화.
- 제약(C):
  - 함수 ≤ 50 LOC, 파일 ≤ 300 LOC, 순환복잡도 ≤ 10 유지(핵심 로직은 헬퍼/가드로 분리).
  - 입력/출력 정규화 및 실패 시 안전한 폴백(기존 동작 유지).

# 현재 상태 분석

- Exporter: `export_operation_sequence`가 DOUT/DOUT4K일 때 직전 op만 살펴 상속(`same_celltype`) 복원 시도. 중간에 `SR`/`Delay(NOP)` 끼면 실패.
  - 참조: `main.py:~486`(기본 celltype), `main.py:~520`(`_prev_of`), `main.py:~540`(inherit 판정)
- Scheduler: `_emit_op_events`는 rows에 제안 시점의 phase_key/훅 컨텍스트는 담지만 celltype 힌트는 없음.
  - 참조: `scheduler.py:~300-520`(PHASE_HOOK/row 로깅 경로), `InstrumentedScheduler._emit_op_events`
- Proposer: 시퀀스 전개 시 `same_celltype` 조건을 해석해 후속 op_name 선택에는 반영하나, 메타데이터로 보존하진 않음.
  - 참조: `proposer.py:~920-1120`(`_expand_sequence_seq`), `proposer.py:~800-920`(단일 단계 전개), `proposer.py:~1160-1200`(preflight 계획화)
- CFG: READ/READ4K/PLANE_READ/COPYBACK_READ 등이 `inherit: DOUT/DOUT4K -> ['same_celltype']`를 명시.
  - 참조: `config.yaml:~300`(READ4K), `config.yaml:~322`(PLANE_READ), `config.yaml:~260`(READ), `config.yaml:~404`(COPYBACK_READ)

핵심 발견:
- 힌트가 제안 시점에 이미 결정 가능(READ op_name의 `celltype`)하므로, 후속 DOUT 생성 시 `celltype_hint`를 함께 싣는 것이 가장 정확하고 단순하다.
- 내부 row 스키마(_OpRow)에 옵셔널 필드로 추가하면 PRD 출력과 분리되어 안전하다.

# 목표 상태

- DOUT/DOUT4K가 exporter 단계에서 `celltype_hint`를 최우선으로 사용(있으면 그대로, 없으면 기존 역참조 폴백) → SR/Delay 개입과 무관하게 올바른 `celltype` 출력.
- 기존 CSV 스키마 불변: `seq,time,op_id,op_name,op_uid,payload` 그대로. payload JSON의 `celltype` 값만 교정.
- 회귀 방지: 힌트가 누락되거나 비정상일 때는 현재 로직(직전 op 기반)으로 폴백.

### 수용 기준(AC)
- AC1: READ(TLC) → SR → Delay → DOUT에서 payload.celltype == "TLC".
- AC2: READ4K(SLC) → SR_ADD → DOUT4K에서 payload.celltype == "SLC".
- AC3: 상속 규칙이 CFG에 없거나 페이지 제약 위반 시 기존 값 유지(None/미출력)한다.
- AC4: Operation Timeline/State/Counts 등 다른 산출물은 스키마/값 회귀가 없다.

# 설계(대안 B — 메타데이터 전파)

요지: ProposedOp에 `meta.celltype_hint`를 추가하고, Scheduler가 이를 row로 전파, Exporter가 우선 사용.

API/스키마 변경(내부):
- `proposer.ProposedOp`에 `meta: Optional[Dict[str, Any]]` 필드 추가(불변 dataclass 그대로, 생성 시 주입).
- `scheduler._propose_and_schedule`에서 `rec["celltype_hint"] = p.meta.get("celltype_hint")` 보존.
- `InstrumentedScheduler._OpRow`에 `celltype_hint: Optional[str] = None` 추가 및 emit 시 채움.
- `export_operation_sequence`에서 DOUT/DOUT4K 처리 시 `row.get("celltype_hint")`가 유효하면 최우선 사용, 미존재/무효면 기존 역참조 로직 폴백.

메타 값 결정 시점:
- Proposer 시퀀스 전개 시 `inherit` 규칙에 `same_celltype`이 포함된 후속 스텝(예: DOUT/DOUT4K/CACHE_READ_END/PLANE_CACHE_READ_END 등)에 한해,
  - `cell = _op_celltype(cfg, first_name)` 혹은 `first_cell`을 `celltype_hint`로 설정.
  - 멀티‑플레인 분할(`split_dout_per_plane`) 이후에도 각 ProposedOp에 동일 힌트가 실리므로 안전.

# 구현 단계

1) ProposedOp 확장
- 파일: `proposer.py`
- 변경: `@dataclass(frozen=True) class ProposedOp`에 `meta: Optional[Dict[str, Any]] = None` 추가.
- 생성자 호출처 업데이트: `_preflight_schedule`에서 Planned 리스트 구성 시 `meta` 전달(초기는 None).

2) 시퀀스 전개 시 힌트 채우기
- 파일: `proposer.py`
- 위치: `_expand_sequence_chain`, `_expand_sequence_seq`
- 변경: 전개 결과를 (name, targets, meta) 형태로 보유하도록 내부 표현을 확장하거나, 별도 병렬 리스트로 `celltype_hint`를 유지.
  - 간단안: 전개 함수 반환은 그대로 두고, `_preflight_schedule`에 힌트 파생 로직 삽입.
    - 규칙: 첫 op_name의 celltype을 `first_cell`로 구함.
    - 후속 각 (base_i, rules_i)에서 `same_celltype ∈ rules_i`면 해당 스텝 `meta={"celltype_hint": first_cell}`로 설정.
- 결과: `planned.append(ProposedOp(..., meta=meta_i))`로 힌트 주입.

3) Scheduler 전파
- 파일: `scheduler.py`
- 위치: `_propose_and_schedule` 내 `rec` 구성부
- 변경: `celltype_hint = p.meta.get("celltype_hint") if p.meta else None` 추출 후 `rec["celltype_hint"] = (None if v in (None, "None", "NONE") else str(v))`로 정규화 저장.

4) Row 스키마 확장 및 로깅
- 파일: `main.py`
- 위치: `InstrumentedScheduler._OpRow` 정의 및 `_emit_op_events`
- 변경: `_OpRow`에 `celltype_hint: Optional[str] = None` 필드 추가. `self._rows.append(...)` 시 `celltype_hint=rec.get("celltype_hint")` 포함.

5) Exporter 우선 사용
- 파일: `main.py`
- 위치: `export_operation_sequence`
- 변경: DOUT/DOUT4K 분기에서 `r.get("celltype_hint")`가 truthy면 그대로 사용. 아니면 기존 `_prev_of`+`inherit_map` 복원 로직 사용.
- 가드: `None/"None"/"NONE"` 정규화.

6) 옵션 확장(선택)
- 같은 패턴을 `CACHE_READ_END`/`PLANE_CACHE_READ_END`에도 적용 필요 시, 동일 힌트 전파/사용 로직을 재사용.

7) 피처 플래그(선택)
- `config.yaml.features.celltype_hint_propagation: true`(기본)로 토글 가능하게 설계. 코드에서는 존재/참 값일 때만 힌트 우선.

# 테스트 전략

단위 테스트(신규):
- T1: READ(TLC) → SR → Delay → DOUT → payload.celltype == "TLC".
- T2: READ4K(SLC) → SR_ADD → DOUT4K → payload.celltype == "SLC".
- T3: CFG 상속 규칙 미존재 → 힌트 없음 → 기존 로직 폴백으로 None/미출력 유지.
- T4: 힌트가 비정상("NONE") → 폴백 작동(가능 시 직전 READ 역복원) → 안전한 값.

통합 테스트:
- I1: 멀티‑플레인 READ 후 plane‑wise DOUT 분할 케이스에서 각 plane DOUT이 올바른 celltype을 가진다.
- I2: CSV/JSON 라운드트립(§3.1.1) 무결성 유지.
- I3: Operation Timeline/State/Counts 산출물 스키마/값 회귀 없음.

# 영향 범위 / 변경 파일

- `proposer.py` — ProposedOp 확장, `_preflight_schedule` 힌트 주입.
- `scheduler.py` — `_propose_and_schedule`에서 rec로 힌트 전파.
- `main.py` — `_OpRow`/`_emit_op_events` 확장, `export_operation_sequence` 힌트 우선 사용.
- (선택) `config.yaml` — `features.celltype_hint_propagation` 기본 true 추가.

# 리스크와 완화

- 인터페이스 변경 파급: ProposedOp 필드 추가 → 생성자 호출부 제한적으로 업데이트(동 파일 내).
  - 완화: 기본값 Optional로 후방호환 유지.
- 힌트 불일치(READ와 DOUT 간 페이지 제약 등):
  - 완화: 힌트는 celltype만 전파, 페이지/블록 제약은 기존 로직/CFG 준수. 필요 시 `same_page` 위반이면 힌트 무시하도록 확장 가능.
- 성능 영향: 없음(단순 필드 전파). Exporter 폴백은 기존과 동일.

# 성공 기준(체크리스트)

자동 검증:
- [ ] 단위/통합 테스트가 결정적으로 통과한다.
- [ ] DOUT/DOUT4K payload의 `celltype`이 개입 op 유무와 무관하게 올바르다.

수동 검증:
- [ ] 샘플 실행에서 Operation Sequence CSV를 눈으로 확인 시 DOUT `celltype`이 기대와 일치.
- [ ] 다른 산출물(Operation Timeline/State/Counts) 변동 없음.

# 참고(파일/라인)

- 연구: `research/2025-09-08_17-05-45_dout_same_celltype_inherit_across_intervening_ops.md:1`
- Proposer: `proposer.py:1`, `proposer.py:920`, `proposer.py:1084`, `proposer.py:1160`
- Scheduler: `scheduler.py:240`, `scheduler.py:300`, `scheduler.py:455`
- Main/Exporter: `main.py:240`, `main.py:486`, `main.py:520`, `main.py:540`
- CFG: `config.yaml:260`, `config.yaml:300`, `config.yaml:322`, `config.yaml:404`

