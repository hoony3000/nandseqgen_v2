---
date: 2025-09-06T08:26:46Z
researcher: codex
git_commit: 9424de5ee928bfc1bc7b25e724a3d053589c2e78
branch: main
repository: nandseqgen_v2
topic: "Align proposer target sampling to PRD_v2 step 6 (ERASE/PROGRAM/READ only)"
tags: [research, codebase, proposer, sampling, AddressManager, resourcemgr]
status: complete
last_updated: 2025-09-06
last_updated_by: codex
last_updated_note: "DOUT plane_set restoration follow-up added"
---

# 연구: proposer 샘플링을 PRD의 단계 6에 정렬

**Date**: 2025-09-06T08:26:46Z
**Researcher**: codex
**Git Commit**: 9424de5ee928bfc1bc7b25e724a3d053589c2e78
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
[proposer.py](proposer.py) 의 `propose -> _sample_targets_for_op` 호출을 PRD에 맞게 제한해야 한다. [docs/PRD_v2.md:307](docs/PRD_v2.md:307) 에 따르면 erase/program/read 에 국한해야 하나, 현재는 모든 operation 후보에 대해 시도되어 `sample_none`으로 끝나며 `maxtry_candidate` 만큼 빈 시도가 발생한다. 샘플링 단계 워크플로를 재확인하고 정렬 방안을 제시하라.

## 요약
- 현재 구현은 후보 op_name 마다 무조건 `_sample_targets_for_op`를 호출한다([proposer.py:793](proposer.py:793)).
- `_sample_targets_for_op`는 내부적으로 ERASE/PROGRAM/READ 계열만 지원하고, 그 외 base는 빈 리스트를 반환한다([proposer.py:333](proposer.py:333)). 이로 인해 DOUT/SR/RESET 등 비(직접)주소 샘플링 대상 후보에서 `sample_none` 시도가 누적된다([proposer.py:794](proposer.py:794)).
- PRD의 샘플링 단계 6은 “erase/program/read가 후보라면 AddressManager로 target address 샘플링”이라고 규정한다([docs/PRD_v2.md:307](docs/PRD_v2.md:307)). 즉, E/P/R에 한정해 AddressManager를 호출하고, 나머지는 시퀀스 상속(7단계) 또는 훅 컨텍스트(die/plane) 기반으로 처리해야 한다.
- 정렬 방안: `propose` 내에서 base에 따라 샘플링을 조건부로 수행(E/P/R 계열만). 그 외 base는 (a) 훅의 `die/plane`로 최소 타겟을 구성하거나(스코프 NONE/DIE_WIDE용), (b) 컨텍스트가 없으면 해당 후보를 건너뛴다. 이렇게 하면 불필요한 `sample_none` 시도를 제거하고 PRD 의도와 부합한다.

## 상세 발견

### 현재 proposer 샘플링 흐름
- 후보 선별 후 모든 이름에 대해 타겟 샘플링 호출:
  - `targets = _sample_targets_for_op(cfg, addr_sampler, name, sel_die=...)` → 실패 시 `sample_none` 기록 후 다음 후보([proposer.py:791](proposer.py:791), [proposer.py:793](proposer.py:793), [proposer.py:794](proposer.py:794)).
- `_sample_targets_for_op` 지원 범위:
  - ERASE / PROGRAM* / READ* 만 처리. 그 외 base는 “Unsupported bases: skip”으로 빈 리스트 반환([proposer.py:333](proposer.py:333), [proposer.py:361](proposer.py:361), [proposer.py:381](proposer.py:381), [proposer.py:415](proposer.py:415)).
- 시퀀스 1스텝 확장:
  - 첫 op의 타겟을 바탕으로 후속 op를 선택/상속(페이지 상속, same_celltype, multi 등)([proposer.py:494](proposer.py:494), [proposer.py:520](proposer.py:520), [proposer.py:527](proposer.py:527)).

### PRD의 의도(샘플링 단계)
- 5.4 Proposer > Workflow:
  - 6) “erase/program/read가 후보라면 AddressManager로 target address 샘플링. `multi=true`면 plane_set 조합 생성 후 샘플링; 실패 시 plane_set을 2까지 축소; 그래도 없으면 남은 후보 중 `maxtry_candidate` 만큼 비복원 샘플링”([docs/PRD_v2.md:307](docs/PRD_v2.md:307)).
  - 7) 시퀀스가 정의된 경우 `sequence[probs]`로 후속 op를 선택하고, 상속 규칙(`inc_page`, `multi`, `same_page_from_program_suspend` 등)으로 타겟을 파생([docs/PRD_v2.md:308](docs/PRD_v2.md:308), [docs/PRD_v2.md:311](docs/PRD_v2.md:311), [docs/PRD_v2.md:315](docs/PRD_v2.md:315)).
- 관련 스코프와 주소 필요성
  - `READ`: `PLANE_SET` 스코프([config.yaml:194](config.yaml:194))
  - `ERASE`: `DIE_WIDE` 스코프([config.yaml:54](config.yaml:54))
  - `PROGRAM_SLC` 등 PROGRAM 계열: `DIE_WIDE` 스코프([config.yaml:76](config.yaml:76))
  - `DOUT`: `scope: "NONE"`로 정의됨([config.yaml:423](config.yaml:423)) — 직접 샘플링이 아니라 이전 READ 컨텍스트/시퀀스 상속 대상
  - `SR`: `scope: "NONE"`([config.yaml:447](config.yaml:447))

### 불일치/문제점
- 모든 후보에서 `_sample_targets_for_op`를 호출 → E/P/R 외 base는 빈 결과가 되며 `sample_none` 원인으로 `maxtry_candidate` 소진.
- DOUT/DATAIN/SR/RESET 등은 주소 샘플링이 아니라 컨텍스트 기반 또는 시퀀스 상속으로 타겟을 정해야 함에도, 현재는 동일 루틴을 거친다.

## 코드 참조
- `proposer.py:793` - 모든 후보에 대해 `_sample_targets_for_op` 호출.
- `proposer.py:333` - `_sample_targets_for_op` 정의 시작; E/P/R만 처리, 나머지는 빈 리스트 반환.
- `proposer.py:494` - 시퀀스 확장; 상속 규칙 적용.
- `docs/PRD_v2.md:307` - 샘플링 단계 6: E/P/R에 한정.
- `config.yaml:423` - `DOUT` base의 `scope: "NONE"`.
- `config.yaml:447` - `SR` base의 `scope: "NONE"`.

## 아키텍처 인사이트
- AddressManager 기반 주소 샘플링은 “리드/프로그램/이레이즈” 계열의 리딩 op에만 적용. 후속 op(DOUT 등)는 시퀀스 상속 규칙으로 타겟을 결정.
- 스코프가 `NONE`인 op는 리소스 레벨에서 plane/die 컨텍스트가 필요할 수 있으며, 훅(payload)의 `die/plane`을 사용해 최소 타겟을 구성하는 것이 합리적이다. ResourceManager는 `feasible_at`에서 `targets[0]`의 die/plane을 참조한다([resourcemgr.py:338](resourcemgr.py:338)).

## 정렬 방안 제안
1) 샘플링 대상 판별: `base` 포함 문자열로 ERASE/PROGRAM/READ 계열을 식별(READ4K/PLANE_READ/CACHE_READ 포함). 그 외 base는 AddressManager 샘플링을 수행하지 않는다.
2) 비샘플링 op 처리:
   - 훅에 `die/plane`가 있으면 이를 이용해 최소 타겟(`Address(die, plane, block=0, page=None)`)을 구성해 feasibility만 평가한다. 시퀀스 상속이 있다면 후속 op에서 타겟이 적절히 파생된다.
   - 훅 컨텍스트가 없으면(예: 일반 QUEUE_REFILL) 해당 후보는 스킵하고 다음 후보로 진행한다.
3) 로깅/메트릭: 샘플링을 건너뛴 이유(`skip_non_epr_sample`, `no_context_for_non_epr`)를 `attempts`에 기록해 디버깅 가능성 확보.
4) 영향:
   - 불필요한 `sample_none` 시도가 제거되어 `maxtry_candidate` 소모가 줄고, E/P/R 후보 평가에 더 많은 기회를 제공.
   - PRD의 단계 6/7 분리(직접 샘플링 vs 시퀀스 상속)가 코드에 반영됨.

## 관련 연구
- `research/2025-09-06_15-01-20_op_state_timeline_end_risks.md` – phase_key/END 가상 분류 맥락(후속 제안 타이밍 이해에 도움)

## 미해결 질문
- DOUT가 리딩 후보로 선택된 경우(예: READ.DATA_OUT 훅) 멀티플레인 읽기의 plane_set 상속을 어떻게 완전하게 복원할지? 현재 제안은 최소 타겟 1개로 feasibility만 평가하지만, 이상적으로는 해당 훅에 plane_set 정보가 포함되거나 RM에서 조회 가능해야 한다.
- SR/RESET/FEATURE 계열 중 일부가 특정 die/plane 문맥을 강제하는지 여부와, 훅 컨텍스트 부재 시의 안전한 폴백 정책(스킵 vs 기본 die/plane 고정)이 필요.

## 후속 연구 2025-09-06T08:41:40Z

### 상황 구체화: READ.DATA_OUT 훅에서 DOUT가 선행 후보로 선택되는 케이스
- PHASE_HOOK 생성 방식: 스케줄러는 각 state 구간의 말미와 종료 직후에 대상 plane마다 훅을 발생시킨다.
  - 코드: `scheduler.py:301`, `scheduler.py:307` — `hook = {die, plane, label=f"{base}.{name}"}`로 per-plane 훅 생성
- 예시 시나리오
  1) 멀티플레인 READ(예: `PLANE_READ` with plane_set=[0,1])가 실행 중이며, `DATA_OUT` state의 끝 지점에 도달
  2) 스케줄러가 각 plane(0,1)에 대해 `PHASE_HOOK(die, plane, label="READ.DATA_OUT")` 발생
  3) 이 훅에서 `phase_conditional['READ.DATA_OUT']`에 `DOUT`가 양의 확률로 포함되어 있어, `proposer`가 DOUT를 “첫 op” 후보로 선택
  4) 이때 DOUT의 payload는 `die,plane,block,page,celltype`가 필요(config.yaml:701)하므로, 직전 READ의 (block,page,celltype)과 멀티 plane_set 상속이 모두 필요
  5) 현재 구현은 훅에 plane_set/주소 문맥이 없고 RM도 주소를 노출하지 않아, 멀티플레인 상속 및 (block,page) 복원이 불가 → 단일 임시 타겟이나 스킵으로 귀결

### 제약 분석
- DOUT는 주소 의존(op_base scope: `NONE`이나 payload는 주소 필요) — config.yaml:423, 701
- `_expand_sequence_once`는 “READ → DOUT” 경로에서 `inherit=['same_page','multi','same_celltype']`로 완전 상속을 의도(config.yaml:198-204). 하지만 “DOUT가 선행 후보”인 경우엔 시퀀스가 아니라 직접 후보라 상속 기회가 없음.
- RM의 타임라인 `_StateTimeline`은 (op_base/state/start/end)만 보관; (block,page) 없음 → 타임라인만으로는 주소 복구 불가.
- RM에 ongoing READ 메타가 저장되지 않음(메서드 골격은 존재: `register_ongoing`, `ongoing_ops`, resourcemgr.py:723, 663)

### 대안 비교
- 대안 A — DOUT를 “후속 전용”으로 강제
  - 설명: `phase_conditional`에서 DOUT를 READ.DATA_OUT 훅의 “첫 후보”에서 제외하거나, `proposer`가 비(E/P/R) 베이스를 첫 후보로 선택하지 않도록 강제. DOUT는 오직 시퀀스(READ의 후속)로만 생성.
  - 장점: 상속 문제 사라짐(항상 READ의 first_targets에서 파생). 논리 단순.
  - 단점: 분포 제어 유연성 저하(훅 단계에서 DOUT 확률 튜닝 불가). 기존 설정과의 호환성 이슈 가능.
  - 위험: 구성 변경 범위가 큼(phase_conditional/오토필 정책 영향).

- 대안 B — PHASE_HOOK에 plane_set+주소 문맥 포함(권장)
  - 설명: `scheduler._emit_op_events`에서 READ(및 READ 파생)의 `PHASE_HOOK` payload에 직전 op의 `targets` 전체(plane_set 및 (block,page,celltype))를 추가. 예: `hook = {die, plane, label, plane_set:[...], targets:[(die,plane,block,page,celltype),...]}`
  - `proposer`는 비(E/P/R) 베이스(예: DOUT)가 첫 후보로 선택되면, 훅의 `targets`에서 동일 `die`의 전체 plane_set을 복원해 DOUT 타겟을 구성(READ의 주소/페이지/셀타입 그대로 상속).
  - 장점: 구현 직관적, 프로포저에서 별도 조회 없이 즉시 활용. 상속 의도를 정확히 반영. 결정성 유지.
  - 단점: 훅 payload가 커짐(plane 당 훅에 전체 plane_set/타겟 중복 포함). 그러나 plane 수가 작다면 실용적으로 허용 가능.
  - 위험: 훅 스키마 변경에 따른 파급(분석/로그 소비자 업데이트 필요).

- 대안 C — RM에 ongoing READ 인덱스 제공(주소 포함)
  - 설명: `commit()` 시점에 READ/PLANE_READ/CACHE_READ 예약을 `_ongoing_ops`에 등록하고, `OP_END`에서 제거. 조회 API(예: `res_view.ongoing_ops(die)` 또는 `res_view.lookup_read_context(die, plane, t)`)로 (plane_set, targets) 반환.
  - 장점: 중앙 권위 저장소. 훅 스키마 변경 불필요. 다양한 후속 op에서 재사용 가능.
  - 단점: 구현량/복잡도 증가(삽입/삭제, 시간 일관성, 스냅샷 연동). 프로포저 인터페이스 확장 필요(duck-typing으로 완화 가능).
  - 위험: 상태 누수/메모리 증가 가능성. 경계 타이밍 버그 주의.

- 대안 D — 하이브리드: B 우선, C 백업
  - 설명: 우선 훅 payload(대안 B)를 사용하고, 없을 때 RM 조회(대안 C) 시도. 최후엔 시퀀스 기반으로만 생성(비(E/P/R) 첫 후보 스킵).
  - 장점: 견고성 향상. 이행 단계에서 유연.
  - 단점: 복잡도 증가.

### 권고안
- 1순위: 대안 B 적용
  - 변경 포인트: `scheduler.py:301, 307`에서 `PHASE_HOOK` payload에 `plane_set`과 `targets` 추가. 동일 훅으로 들어온 모든 plane이 동일 plane_set을 공유하게 된다.
  - 프로포저 처리: 비(E/P/R) 베이스가 첫 후보일 때, 훅의 `targets`가 있으면 이를 그대로 사용해 타겟 복원. 없으면 스킵(후술 폴백 규칙).
- 2순위: 대안 C 추가(필요시)
  - RM에 ongoing READ 인덱스 구현 후, `proposer`에서 `res_view.ongoing_ops(die)`를 조건부 호출해 (plane_set, targets) 획득.
- 폴백 규칙(안전 장치)
  - 훅에 문맥 없음 + RM 조회 불가인 경우: 비(E/P/R) 첫 후보는 스킵(`attempts.reason=no_context_for_non_epr`).
  - 멀티플레인 불일치 감지 시: 단일-plane만으로 제안하지 않고 스킵(상속 위배 방지).

### 근거 파일 참조
- `scheduler.py:301` — per-plane PHASE_HOOK 생성(훅 확장 지점)
- `config.yaml:198` — READ → DOUT 시퀀스/상속 규칙(‘same_page’, ‘multi’, ‘same_celltype’)
- `config.yaml:701` — `DOUT` payload 스키마(주소 필수)
- `resourcemgr.py:663` — `ongoing_ops` 조회 API 골격
- `resourcemgr.py:723` — `register_ongoing` 제공(인덱스 구축용)

### 다음 단계 제안
- 스팩/설정 선택: 대안 B 적용 여부 결정. 필요 시 대안 C 병행.
- 구현 순서(안)
  1) 스케줄러 훅 payload 확장(B) → 2) 프로포저에서 훅 기반 타겟 복원 → 3) 메트릭/로그로 검증
  4) 필요시 RM 인덱스(C) 구현 후 프로포저에 안전한 백업 경로 추가

