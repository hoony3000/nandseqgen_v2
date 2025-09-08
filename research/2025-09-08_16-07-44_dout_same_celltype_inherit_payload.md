---
date: 2025-09-08T16:07:44+0900
researcher: codex
git_commit: fc526a6c073c9f58d495be1975a8e4a99e213d6e
branch: main
repository: nandseqgen_v2
topic: "DOUT same_celltype inheritance reflected in payload"
tags: [research, codebase, proposer, exporter, payload]
status: complete
last_updated: 2025-09-08
last_updated_by: codex
---

# 연구: DOUT same_celltype 상속 payload 출력

**Date**: 2025-09-08T16:07:44+0900
**Researcher**: codex
**Git Commit**: fc526a6c073c9f58d495be1975a8e4a99e213d6e
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
propose 단계에서 operation sequence 를 만들 때, 후속 동작이 DOUT 일 때, inherit 조건이 ‘same_celltype’ 이면, 이전 operation 의 celltype 을 상속받아서 payload 에도 출력하도록 변경하는 방법은?

## 요약
- 현재 DOUT/DOUT4K의 payload는 `config.yaml[payload_by_op_base]`에 `celltype` 필드를 포함하고 있으나, `export_operation_sequence`는 `cfg.op_names[op_name].celltype`만 참조하여 채웁니다. DOUT 계열의 op_name들은 `celltype: NONE`이므로 상속된 celltype이 payload에 반영되지 않습니다.
- 구현 대안 2가지:
  1) Exporter 보강(권장, 최소침습): `export_operation_sequence`에서 DOUT/DOUT4K일 때 같은 (die,plane)의 직전 READ 계열 op의 `celltype`을 찾아 상속하고, 해당 READ base의 sequence.inherit에 `DOUT: [..., same_celltype]`이 명시된 경우에만 적용.
  2) Proposer 메타데이터 전파(구조적 개선): `_expand_sequence_chain`/`_expand_sequence_seq`에서 `same_celltype` 규칙을 감지해 `celltype_hint`를 ProposedOp로 전달 → Scheduler → Instrumented rows → exporter가 우선 사용.
- 대안 1은 코드 변경 범위가 `main.py` 내 exporter로 한정되어 리스크가 낮고, 요구사항(최종 payload 출력 반영)에 충분합니다.

## 상세 발견

### 구성요소: 설정(config)
- `payload_by_op_base`에 DOUT 계열이 `celltype`을 요구: `config.yaml:758`, `config.yaml:759`.
- READ/PLANE_READ/READ4K 등에서 sequence 후속 동작으로 `DOUT`/`DOUT4K`를 지정하고 `same_celltype`을 inherit: `config.yaml:255`, `config.yaml:276`, `config.yaml:296`, `config.yaml:341`, `config.yaml:361`, `config.yaml:407`.

### 구성요소: proposer (sequence 전개와 same_celltype 처리)
- `same_celltype` 규칙은 이름 선택 시 celltype 힌트로만 사용됨: `proposer.py:923`, `proposer.py:1090`.
- `.SEQ` 확장에서도 per-step로 `cell_hint`가 계산되나 외부로 전파되지는 않음: `proposer.py:1010`.

### 구성요소: scheduler/event emission
- Scheduler는 ProposedOp를 예약·커밋하고 이벤트를 발행하나, OP payload에 celltype을 싣지는 않음. READ 계열 PHASE_HOOK에 한해 hook.targets에 op의 celltype을 포함(분석용): `scheduler.py:448-462`.

### 구성요소: exporter (operation_sequence CSV)
- `export_operation_sequence`는 uid별 그룹에서 `cfg.payload_by_op_base[base]`를 따라 payload를 구성. `celltype`이 필요한 경우 `cfg.op_names[name].celltype`만 사용: `main.py:413`, `main.py:431-447`.

## 코드 참조
- `config.yaml:726` — `payload_by_op_base` 루트
- `config.yaml:758` — `DOUT: [die,plane,block,page,celltype]`
- `config.yaml:759` — `DOUT4K: [die,plane,block,page,celltype]`
- `config.yaml:255` — READ → `DOUT` inherit에 `same_celltype` 포함
- `config.yaml:296` — READ4K → `DOUT4K` inherit에 `same_celltype` 포함
- `config.yaml:341` — PLANE_READ → `DOUT` inherit에 `same_celltype` 포함
- `proposer.py:923` — non-SEQ 후속에서 `same_celltype` 시 cell 힌트 사용
- `proposer.py:1010` — `.SEQ` 확장에서 per-step `cell_hint` 계산
- `proposer.py:1090` — non-SEQ 분기 또다시 `same_celltype` 반영
- `scheduler.py:448-462` — READ-family PHASE_HOOK payload에 cell 포함(참고)
- `main.py:413` — `export_operation_sequence` 시작부
- `main.py:431-447` — payload 구성부(현재 celltype 치환 지점)

## 아키텍처 인사이트
- celltype 상속은 "선택 로직"(후속 op_name 선택 힌트)에만 사용되고, 실행/로그 경로에는 별도 전파가 없음. 결과적으로 최종 CSV payload는 상속된 celltype을 모름.
- Exporter 단계에서 시간 순서를 이용하면 상속 정보를 복원할 수 있음(같은 (die,plane)의 직전 READ 계열 op_name의 celltype).
- 더 견고한 구조는 proposer가 `celltype_hint`를 ProposedOp 수준에서 전파하는 것. 다만 변경 범위가 넓어짐.

## 제안 변화 (대안 비교)

- 대안 A — Exporter 보강(권장)
  - 내용: `export_operation_sequence`에서 base ∈ {DOUT, DOUT4K}이고 `payload_by_op_base`가 `celltype`을 요구하면, 같은 (die,plane)의 직전 op의 base를 조회 → 해당 base의 `op_bases[base].sequence.inherit`에 `DOUT`/`DOUT4K` 키가 있고 값에 `same_celltype`이 포함되면, 직전 op_name의 `celltype`으로 덮어씀.
  - 장점: 모듈 간 인터페이스 불변, 구현 범위 최소(main.py만). 즉시 요구 충족.
  - 단점: 상속 판단을 타임라인에서 유추(약한 결합). 다만 단일 plane 동시성 금지 가정 하에 안정적.
  - 위험: 특수 시나리오(비정상 시퀀스 삽입)에서 오탐 가능. 가드로 inherit 규칙 확인으로 완화.

- 대안 B — Proposer 메타데이터 전파
  - 내용: `ProposedOp`에 `celltype_hint: Optional[str]` 추가. `_expand_sequence_chain`/`_expand_sequence_seq`에서 규칙에 따라 설정. Scheduler가 rec/rows로 전파. Exporter는 힌트를 우선 사용.
  - 장점: 의미론적으로 정확, 다른 소비자도 활용 가능.
  - 단점: dataclass/함수 시그니처/호출부 다수 변경. 리스크와 작업량 증가.

- 대안 C — CFG에서 DOUT을 celltype별 op_name으로 분기
  - 내용: DOUT_TLC/DOUT_SLC 등 op_name을 추가하고 선택 시 직접 매핑.
  - 장점: Exporter 변경 불필요.
  - 단점: CFG 부풀림/유지보수 어려움. 비권장.

## 구현 스케치 (대안 A)
- main.py `export_operation_sequence` 내 celltype 설정 로직을 다음으로 치환:
  - 사전 준비: (die,plane)별 타임라인 인덱스를 start/end 기준으로 정렬해 구축.
  - 각 그룹(grp) 처리 시 base ∈ {DOUT, DOUT4K}이고 fields에 `celltype`이 포함되면,
    - 그룹의 기준 시간 `t0`를 정하고, 각 row r(die,plane)에 대해 r의 `t0` 이전의 직전 row_prev를 조회.
    - `prev_base = row_prev.op_base`, `prev_name = row_prev.op_name`.
    - CFG에서 `op_bases[prev_base].sequence.inherit`를 꺼내 `DOUT`/`DOUT4K` 키의 규칙에 `same_celltype` 포함 여부 확인.
    - 포함 시 `cell = cfg.op_names[prev_name].celltype`로 덮어써 payload `celltype`에 사용.
  - 그렇지 않으면 기존 로직(현 op_name의 celltype) 유지.

## 관련 연구
- 없음

## 미해결 질문
- DOUT 외 CACHE_READ_END/PLANE_CACHE_READ_END 등에 대해서도 유사한 상속 요구가 있는지 확인 필요. -> (검토완료) same_celltype 조건이 있는 경우 동일 적용.
- 멀티-플레인 READ에서 plane-split된 DOUT 순서가 비정상적으로 interleave될 경우 보호 로직(같은 uid 내 plane별 개별 직전 검색)이 충분한지 검토. -> (검토완료) uid 기준으로 직전 동작의 celltype 을 상속

