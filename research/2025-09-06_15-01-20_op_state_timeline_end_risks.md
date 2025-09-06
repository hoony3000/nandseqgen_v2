---
date: 2025-09-06T15:01:20+09:00
researcher: Codex CLI
git_commit: 8ba3afdde895a26bd8458f57b4f70967536afdfb
branch: main
repository: nandseqgen_v2
topic: "Risks of adding op_state_timeline op_name.END (PRD §5.5)"
tags: [research, codebase, ResourceManager, exporters, proposer, visualization, snapshot]
status: complete
last_updated: 2025-09-06
last_updated_by: Codex CLI
---

# 연구: Risks of adding op_state_timeline op_name.END (PRD §5.5)

**Date**: 2025-09-06T15:01:20+09:00
**Researcher**: Codex CLI
**Git Commit**: 8ba3afdde895a26bd8458f57b4f70967536afdfb
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
docs/TODO.md의 항목 11에 따라, op_state_timeline에 각 예약 오퍼레이션의 모든 logic_state 뒤에 `op_name.END`를 `end_time='inf'`(또는 open-ended) 세그먼트로 추가할 때, 현재 코드베이스에서 발생할 수 있는 위험은 무엇인가?

## 요약
- 설계 의도(END=무한 꼬리)와 현재 구현이 기대하는 “유한·비중첩 상태 세그먼트” 간 불일치가 큼. 그대로 도입 시 시각화, 정렬, 입력시간 집계, 스냅샷 JSON, 제안 분포(phase_conditional)까지 여러 경로에서 부작용이 생길 수 있다.
- 특히 `proposer`가 `rm.op_state()`를 사용해 phase key를 결정하므로, 빈 구간에서 이전 오퍼레이션의 `*.END`가 반환되면 제안 분포가 바뀌는 기능 변경 위험이 크다.
- CSV/시각화에서는 `end=inf`가 축 계산을 망가뜨리거나, END 세그먼트가 이후 모든 상태와 겹쳐 Gantt가 중첩돼 가독성이 크게 저하될 위험이 있다.
- 안전 채택을 위해서는 최소 (a) 내부 표현을 무한대 대신 “다음 상태 시작 전까지”로 제한하거나, (b) 조회·수출·시각화 시 `.END`를 명시적으로 필터링/특별처리해야 한다.

## 상세 발견

### 사양 근거
- `docs/PRD_v2.md:315` — 스케줄 시 모든 logic_state 등록 후 `op_name.END`를 end_time='inf'로 추가. 예외: ERASE_RESUME 등.

### ResourceManager / 상태 타임라인
- `_StateTimeline.reserve_op`는 전달된 상태 리스트만 순차 기록(END 없음).
  - `resourcemgr.py:27` — 세그먼트 삽입 구현
- 예약 커밋 시 상태 등록 위치:
  - `resourcemgr.py:429` — `self._st.reserve_op(...)` 호출
- 상태 조회/중첩 검사:
  - `resourcemgr.py:33` — `state_at`는 이분 탐색으로 “가장 최근 시작 세그먼트”가 시각 t를 포함하면 반환
  - `resourcemgr.py:39` — `overlaps_plane`는 [start,end)와 겹치는 모든 세그먼트 검사

위에 무한대 END를 추가하면:
- 중첩 급증: END(start=end_of_op, end=∞)가 이후 모든 세그먼트와 겹쳐 `overlaps_plane`이 항상 True가 됨. 현재 스케줄 경로는 이를 직접 사용하진 않지만(예약 결정은 plane_resv/버스/래치/배제 기준), 향후/테스트에서 중첩 확인 로직과 충돌 위험 있음.
- 타입 안정성: `_StateInterval.end_us`는 float로 사용됨. 내부 표현을 `None`으로 두면 비교 연산에서 TypeError 발생. 무한대(float('inf'))를 쓰면 연산은 되지만, 하위 단계에서 추가 이슈(아래) 발생.

### Exporters
- op_state 타임라인 내보내기:
  - `main.py:195` — `export_op_state_timeline()`는 스냅샷 `timeline`을 그대로 `start,end,duration`으로 CSV화. END의 `end=inf`이면 `duration=inf`가 기록됨.
  - 정렬 키 유도: `main.py:232` 근처 `_uid_for`는 세그먼트 시작이 포함되는 `op_uid`를 찾음. END는 무한대로 인해 fallback(겹치는 첫 op)을 선택하여, 의도와 다른 uid 연계/정렬이 발생할 수 있음.
- operation 타임라인 내보내기와의 상호작용:
  - `main.py:156` — `export_operation_timeline()`는 행의 시작 시점에서 `rm.op_state(die,plane,start)`을 조회. 새 오퍼레이션의 첫 상태가 정확히 `start`에 기록되므로 일반적으로 END가 선택되지는 않음(동일 시각 시작 세그먼트가 우선). 영향은 제한적.

### Proposer 상호작용(기능 변경 리스크)
- 제안 분포 key 결정:
  - `proposer.py:568` — `_phase_key()`가 `res.op_state(die,plane,now)`를 우선 사용. 현재는 빈 구간에서 None → DEFAULT로 떨어짐.
- END 도입 후에는 빈 구간에서 직전 오퍼의 `BASE.END`가 반환될 수 있어, `phase_conditional`에서 END 전용 분포를 사용하게 됨(또는 키가 없으면 DEFAULT). 이는 생성 시퀀스 특성이 바뀌는 기능 변경 위험.
- 참고: `cfg_autofill.py:52`는 state key 목록에 `END`를 추가해 분포 키 후보가 이미 포함될 수 있음. 실제 `op_state_probs.yaml`에 END 키가 존재한다면 영향이 현실화됨.

### 시각화(viz_required_outputs.py)
- `viz_required_outputs.py:111` — op_state Gantt는 각 행의 `start,end`를 선으로 그림. `end=inf`가 들어오면 축 계산에서 무한대가 포함되어 Matplotlib가 축 한계를 잡지 못해 예외/빈 플롯 위험.
- END 세그먼트는 본질적으로 이후 모든 상태와 중첩되므로 중첩 막대가 그려져 가독성이 크게 저하.

### 스냅샷(JSON 직렬화)
- `main.py:612` 부근 `save_snapshot()`은 `float(s1)`로 복사해 JSON으로 저장. Python `json.dump`는 기본적으로 Infinity를 허용하지만, 파일의 비표준 값("Infinity")은 외부 도구/엄격 파서에서 실패 소지. 또한 재로드 후 `restore()` 경로는 `float('inf')`라도 재삽입을 허용하나, 이후 소비자(시각화/검증)에서 다시 같은 문제를 겪음.

### 스키마/의미 불일치(사양과 구현 간) -> (검토완료) PRD 수정. op_name.END -> op_base.END
- 현재 exporter는 `op_state_timeline`의 `op_name` 열에 base를 기록함:
  - `main.py:205` — `op_name`: `str(base)`로 기록. TODO 4와 충돌. END를 추가해도 `ERASE.END`처럼 base 기반 라벨이 출력되어 PRD 의도(“op_name.END”)와 불일치 지속.

## 코드 참조
- `docs/PRD_v2.md:315` — END 추가 요구사항
- `resourcemgr.py:27` — `_StateTimeline.reserve_op` (현재 END 미삽입)
- `resourcemgr.py:429` — 커밋 시 상태 타임라인 기록
- `resourcemgr.py:33` — 상태 조회 `state_at`
- `resourcemgr.py:39` — 중첩 검사 `overlaps_plane`
- `main.py:195` — `export_op_state_timeline`
- `main.py:156` — `export_operation_timeline`
- `proposer.py:568` — `_phase_key`의 RM 상태 조회 사용
- `viz_required_outputs.py:111` — op_state Gantt 그리기
- `cfg_autofill.py:52` — state key 목록에 `END` 포함

## 아키텍처 인사이트
- END의 “무한 꼬리”는 측정/집계를 위한 개념적 상태에 더 가깝고, 운영 타임라인(비중첩/유한) 모델과는 상충한다. 경계(layer)에서 표현을 분리해야 한다.
  - 내부 RM 타임라인: 항상 유한·비중첩(스케줄 검증 용이성 유지)
  - 집계/분포 계산: 필요 시 가상적으로 END를 생성해 사용(쿼리 시점에 파생), 또는 조회에서 END를 선택적으로 노출

## 미해결 질문
- END를 도입하는 1차 목적이 정확히 무엇인지(분포 다양성 확보 vs. 타임라인 시각화 강화). 분포 목적이라면 RM 내부 데이터 구조를 바꾸지 않고 조회/집계 단계에서 해결하는 편이 안전.  ->(TODO) 분포, 집계 모두 포함. 어떤 operation 이 어떤 op_state 에서 제안됐되는지 집계하여 다양성 정도를 보고자 하는 것. 구현에 대한 reserach 필요
- ERASE_RESUME 등 예외 처리의 상세 규칙: 어떤 BASE의 END를 언제 생성/제외할지. -> (검토 완료) PRD 에 봔영

## 권고(리스크 완화 대안)
- 접근 A(권장, 무중단): RM 내부는 그대로(유한 세그먼트 유지). 다음을 적용.
  - 조회 시 END 가상화: `ResourceManager.op_state()`에서 빈 구간이면 `BASE.END`를 반환하는 게 아니라, 별도 API(예: `phase_key_at(die,plane,now)`)를 추가해 proposer/집계에만 조건부 END를 노출. 기존 `op_state()` 의미는 유지(None 반환).
  - 집계 전용 END: `export_op_state_name_input_time_count()`에서 필요 시 “빈 구간 샘플”을 END로 파생 계산(현재는 start 시각만 보므로 효과 제한적임. 설계 재검토 필요).
  - 시각화에서는 `.END`는 기본 필터링(옵션으로 표시 가능), 또는 `end`를 `min(end, t_max)`로 잘라 그림.
- 접근 B(내부 타임라인 확장): END를 실제 세그먼트로 추가하되 엄격한 가드 적용.
  - 내부 표현: `end_us`를 무한대 대신 “다음 상태 시작 전까지”로 갱신(동일 (die,plane)의 다음 세그먼트 삽입 시 이전 END를 그 시점으로 truncate). 첫 END는 임시(열린)로 들고 있다가 다음 삽입 시 닫음.
  - 중첩 검사/조회 필터: `overlaps_plane`/스냅샷/수출/시각화에서 `.END`는 기본 제외 또는 별도 처리.
  - proposer 영향 차단: proposer의 phase key 경로는 기존 의미 유지(None→DEFAULT). END 노출은 명시적 스위치로 제어.

각 대안의 장단점/위험:
- A: 장점 — 영향 범위 최소, 회귀 위험 낮음. 단점 — END를 실제 타임라인으로 보긴 어려움(개념 분리 필요).
- B: 장점 — PRD §5.5에 근접한 모델. 단점 — 구현 복잡/회귀 위험 높음(중첩/시각화/스냅샷/제안 경로 동시 조정 필요).

