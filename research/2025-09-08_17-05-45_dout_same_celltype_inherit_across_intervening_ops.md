---
date: 2025-09-08T17:05:45+09:00
researcher: codex
git_commit: 6f378648d22736ee281b705b840222b471f5d940
branch: main
repository: nandseqgen_v2
topic: "DOUT celltype inheritance survives SR/Delay between ops"
tags: [research, exporter, DOUT, payload, inheritance]
status: complete
last_updated: 2025-09-08
last_updated_by: codex
---

# 연구: DOUT 상속이 SR/Delay 개입 시에도 유지되도록 개선

**Date**: 2025-09-08T17:05:45+09:00
**Researcher**: codex
**Git Commit**: 6f378648d22736ee281b705b840222b471f5d940
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
operation chain 생성 시 후속 DOUT이 직전 operation의 celltype을 상속하도록 변경했으나, 직전과 DOUT 사이에 SR, Delay(NOP) 등이 끼면 celltype 정보가 사라져 payload에 "NONE"으로 표시됩니다. 이를 견고하게 개선하는 방법은 무엇인가?

## 요약
- 현재 `export_operation_sequence`는 같은 (die, plane)에서 DOUT 직전의 "즉시 이전" op만 확인해 상속 여부를 판단합니다. 중간에 `SR`/`Delay(NOP)` 등 비상속성(intermediate) op가 끼면 READ 계열을 보지 못해 `celltype`이 None으로 남습니다.
- 개선 방안 A(권장): DOUT의 직전 op를 1개만 보지 말고, 같은 (die,plane) 타임라인에서 "역방향으로" 상속 자격이 있는 op(예: READ/PLANE_READ/READ4K/COPYBACK_READ 등)까지 건너뛰어 탐색하여 `same_celltype` 규칙이 명시된 경우에만 celltype을 상속합니다. 하드코딩된 스킵 목록 대신 CFG의 `sequence.inherit` 존재 여부로 판정하여 일반화합니다.
- 대안 B: Proposer→Scheduler→Exporter 경로에 `celltype_hint` 메타데이터를 명시적으로 전파. 구조적으로 가장 견고하나 변경 범위가 넓습니다.

## 상세 발견

### 현재 동작 및 한계
- `export_operation_sequence`는 (die,plane)별 타임라인을 만들고, "직전 하나"만 조회해 상속 판단을 수행합니다.
  - `main.py:424` — (die,plane) → `[(t, base, name)]` 인덱스 구축
  - `main.py:497` — base ∈ {`DOUT`,`DOUT4K`}일 때 즉시 이전 op를 `_prev_of(...)`로 조회
  - `main.py:504` — 이전 base의 `sequence.inherit`에 `DOUT` 키가 있고 `same_celltype` 포함 시 celltype 상속
- 문제: SR(`SR`, `SR_ADD`), NOP(`Delay`, `NOP`) 등은 READ 계열이 아니므로, 이런 op가 READ와 DOUT 사이에 끼면 즉시 이전은 상속 불가 base가 되어 상속 실패 → payload `celltype`이 None/"NONE"으로 출력.

### 관련 설정 및 규칙
- READ/READ4K/PLANE_READ/COPYBACK_READ 등은 `sequence.inherit`에 DOUT/DOUT4K로 `same_celltype`을 명시합니다.
  - `config.yaml:236`(READ) `inherit: - DOUT: ['same_page','multi','same_celltype']`
  - `config.yaml:300`(READ4K) `inherit: - DOUT4K: ['same_page','multi','same_celltype']`
  - `config.yaml:322`(PLANE_READ) `inherit: - DOUT: ['same_page','multi','same_celltype']`
  - `config.yaml:404`(COPYBACK_READ) `inherit: - DOUT: ['same_page','multi','same_celltype']`
- `SR`, `SR_ADD`는 `instant_resv: true`이고 상속 규칙은 없음: `config.yaml:420-447` 부근
- `Delay`는 `base: NOP`: `config.yaml:3028-3034`

### 증상 재현 포인트(논리)
- READ(TLC) → SR → Delay → DOUT: 현재 구현은 즉시 이전이 Delay(NOP)이므로 상속 규칙을 찾지 못하고 `cfg.op_names['DOUT'].celltype(None)` 사용 → CSV payload에 `NONE`.

## 코드 참조
- `main.py:424` — (die,plane) 인덱스 구축
- `main.py:486` — 기본 celltype 결정(`cfg.op_names[name].celltype`)
- `main.py:497` — DOUT/DOUT4K 처리 분기
- `main.py:499` — `_prev_of(d,p,t)` 즉시 이전 조회(현 병목)
- `main.py:502` — `inherit` 조건에 `same_celltype` 포함 시 상속 적용
- `scheduler.py:455` — READ-family PHASE_HOOK payload에 targets와 cell 포함(참고; exporter 경로에는 직접 전파되지 않음)
- `config.yaml:236,300,322,404` — READ 계열의 DOUT 상속 규칙
- `config.yaml:3028` — Delay는 NOP 기반

## 아키텍처 인사이트
- 상속 결정은 의미적으로 "체인" 단위의 맥락이며, 단순한 "직전 op"만으로는 충분하지 않습니다.
- CFG의 `sequence.inherit`가 상속의 사실상의 계약을 제공하므로, exporter가 이 계약을 이용해 과도기를 건너뛰는 역탐색을 수행하면 안정적으로 복원 가능합니다.
- 보다 정석적인 해법은 proposer가 선택 시점의 `celltype_hint`를 구조적으로 전파하는 것입니다(메시지 전파형 설계).

## 개선 대안

- 대안 A — Exporter 역방향 상속 탐색(권장)
  - 내용: `_prev_of`를 일반화해 `_prev_match(d,p,t,target_base)`로 변경. 같은 (die,plane) 타임라인에서 `t` 이전으로 역탐색하며, "해당 prev_base의 `sequence.inherit`에 `target_base` 키가 존재하고, 그 조건 배열에 `same_celltype`이 포함되는" 첫 항목을 선택. `SR`/`NOP` 등은 자동으로 건너뜀.
  - 장점: 변경 범위가 `main.py` 내 exporter로 한정, SR/Delay 등 개입 시나리오를 포괄적으로 해결. CFG 기반이므로 하드코딩 스킵 목록 불필요.
  - 단점: 타임라인 유추 기반(약결합). 비정상 시퀀스가 삽입된 로그에도 적용될 수 있음.
  - 위험: 성능 저하 가능성. 완화: 바이너리 서치로 시작 인덱스 결정 후, 보통 수십 단계 이내 역순 선형 탐색으로 제한.

- 대안 B — 메타데이터 전파(구조적)
  - 내용: `ProposedOp`/스케줄 기록에 `celltype_hint`를 추가하고, DOUT/DOUT4K 생성 시 설정. Scheduler가 `InstrumentedScheduler._emit_op_events`로 rows에 싣고, exporter는 힌트가 있으면 최우선 사용.
  - 장점: 의미론적 정확성, 다른 소비자(시각화 등)도 재사용 가능.
  - 단점: dataclass/함수 시그니처/호출 경로 다수 변경. 테스트 영향 큼.
  - 위험: 릴리스 리스크와 작업량 증가.

- 대안 C — CFG 세분화(비권장)
  - 내용: DOUT을 celltype별 op_name으로 분기(DOUT_TLC/DOUT_SLC 등)하여 선택 시점에 직접 표현.
  - 장점: exporter 변경 불필요.
  - 단점: CFG 폭발, 유지보수 어려움.

## 권장 방안(설계 스케치)
- `main.py:export_operation_sequence` 내 DOUT/DOUT4K 분기에서 다음 로직으로 교체:
  - 타깃 base = 현재 base(`DOUT`/`DOUT4K`).
  - `by_dp[(d,p)]` 리스트를 바이너리 서치로 `t_ref=r.start_us` 직전 인덱스를 찾고, 역방향으로 순회.
  - 각 후보(prev_base, prev_name)에 대해 `cfg.op_bases[prev_base].sequence.inherit[target_base]`를 조회해 `same_celltype` 포함 여부를 확인.
  - 포함 시 `cfg.op_names[prev_name].celltype`을 채택하고 탐색 종료. 찾지 못하면 기본값 유지.
- 추가 가드:
  - `None`/"None"/"NONE" 정규화.
  - 예외 안전(fail‑safe): 조회 실패 시 기존 동작 유지.

## 테스트 제안
- 단위
  - READ(TLC) → SR → Delay → DOUT → payload.celltype == TLC
  - READ4K(SLC) → SR_ADD → DOUT4K → payload.celltype == SLC
  - 상속 규칙 미존재(prev_base가 상속 안함) → 기존 값 유지(None)
- 통합
  - 멀티‑플레인 READ → plane별 DOUT 분할 + 사이사이 SR interleave → 각 plane에 대해 올바른 상속
  - CSV/JSON 라운드 트립 무결성(PRD 3.1.1)

## 관련 연구
- `research/2025-09-08_16-07-44_dout_same_celltype_inherit_payload.md` — 초판 구현 제안(직전 op 기준). 본 문서는 개입 op를 건너뛰는 역탐색으로 확장.

## 미해결 질문
- DOUT 외 `CACHE_READ_END`/`PLANE_CACHE_READ_END` 등에도 동일 패턴 적용 필요 여부(구성에 따라 확장 가능). -> (검토완료) 모든 operation 에 적용 필요
- 상속 범위에 `same_page` 등 다른 조건이 함께 있을 때의 추가 제약(예: page 불일치 시 상속 보류) 적용 여부. -> (검토완료) 상속 보류
