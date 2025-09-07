---
date: 2025-09-07T23:41:59+0900
researcher: Codex
git_commit: 466c7452461bd6dfa5bad9cb3836462ed045c41d
branch: main
repository: nandseqgen_v2
topic: "Operation chain multi consistency — exclude mismatched subsequent ops"
tags: [research, codebase, proposer, scheduler, resourcemgr, sequence, multi]
status: complete
last_updated: 2025-09-07
last_updated_by: Codex
---

# 연구: Operation chain의 multi 일관성 — 선행 multi에 맞지 않는 후속 op 제외

**Date**: 2025-09-07T23:41:59+0900
**Researcher**: Codex
**Git Commit**: 466c7452461bd6dfa5bad9cb3836462ed045c41d
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
operation chain 으로 후속 operation 의 조건을 만들 때, 앞선 operation 의 multi=true/false 에 따라서 후속 operation 도 동일한 조건을 만족하지 않는 것을 제외하게끔 개선할 수 있는 방법은?

## 요약
- 후속 op의 이름 선택은 `_choose_op_name_for_base`의 힌트(`multi`, `same_celltype`)에 의존한다. 현재 힌트를 만족하는 이름이 없으면 느슨한 폴백이 작동해 multi 불일치가 발생할 수 있다.
- 사전 스케줄(prefight)과 `Scheduler`는 순차화로 다중 중첩을 줄이지만, multi 불일치 자체를 걸러내지는 않는다.
- 개선 방안: (1) 이름 선택 폴백을 강화하여 multi 힌트가 주어지면 불일치 이름을 선택하지 않도록 하고(None 반환), (2) 프리플라이트에서 선행 multi와 불일치하는 후속 op를 체인에서 제외하거나 계획 실패로 처리, (3) CFG 시퀀스 규칙에서 ‘multi’ 상속을 확실히 표기해 힌트가 항상 전달되게 한다.

## 상세 발견

### Proposer — 체인 전개와 이름 선택
- `_expand_sequence_chain`에서 비-SEQ/SEQ 모두 ‘inherit’ 규칙에 `multi`가 있으면 첫 타겟의 plane 수로 `multi_hint`를 전달하여 이름을 고른다.
  - `proposer.py:986` — 비-SEQ 분기에서 `multi = (len(first_targets) > 1) if ("multi" in rules) else None` 후 `_choose_op_name_for_base` 호출.
  - `proposer.py:915` — SEQ 전개에서도 `multi_hint` 동일 적용.
- `_choose_op_name_for_base`는 힌트가 주어지면 우선 필터하지만, 매치가 없을 때 폴백 경로가 존재해 multi가 깨질 수 있다.
  - `proposer.py:668` — `if multi is not None and bool(spec.get("multi", False)) != bool(multi): return False`
  - `proposer.py:678` — celltype 폴백 이후, 결국 `return names[0]` 폴백이 있어 multi 힌트가 무시될 수 있음.

### Proposer — 프리플라이트와 DOUT 분할
- `_preflight_schedule`는 첫 op가 multi-plane READ 계열이면 DOUT/CACHE_READ_END를 plane 단위로 분할하는 옵션을 제공한다(기본 on).
  - `proposer.py:1406` — split 대상 베이스 집합 정의와 분할 로직 진입.
  - 분할은 자원 충돌을 줄이지만, 이름의 multi 속성 불일치를 적극적으로 배제하지는 않음.

### Scheduler — 체인 순차화 보장
- `Scheduler`는 같은 트랜잭션 내 후속 op의 시작 시각을 직전 예약의 종료로 이동시켜 READ→DOUT 중첩을 방지한다.
  - `scheduler.py:378` — “READ -> DOUT strictly ordered” 주석과 함께 `txn.now_us` 갱신.

### ResourceManager — single×multi/multi×multi 배제
- RM은 die 단위로 단일/멀티 배제를 엄격히 적용한다. op의 multiplicity는 CFG 우선, 없으면 plane_set 크기로 유추한다.
  - `resourcemgr.py:247` — CFG 우선으로 multiplicity 파악.
  - `resourcemgr.py:279` — single×multi, multi×multi 충돌 금지.
  - `resourcemgr.py:456` — 예약 시에도 동일 규칙 적용(pending 포함).

### CFG/PRD — ‘multi’ 상속 규칙
- PRD는 READ multi-plane 후 DOUT을 plane별로 순차 생성하도록 요구한다.
  - `docs/PRD_v2.md:337` — multi-plane READ → plane 수만큼 DOUT 순차 예약.
- 현행 CFG는 READ/PLANE_READ/CACHE_READ_END 등에서 DOUT 계열로 ‘inherit: [same_page, multi, same_celltype]’를 명시한다.
  - `config.yaml:208`/`config.yaml:211` — READ → DOUT 상속에 `multi` 포함.

## 개선 방안

1) 이름 선택 폴백 강화(추천)
- 내용: `_choose_op_name_for_base`에서 `multi` 힌트가 주어진 경우, 매칭되는 이름이 하나도 없으면 폴백을 하지 말고 `None`을 반환한다. 호출부는 `None`이면 해당 스텝(또는 전체 체인) 생성을 건너뛰거나 계획 실패로 처리한다.
- 장점: 국소 변경으로 의도치 않은 multi 불일치를 사전에 차단. 규칙이 분명함.
- 단점: CFG가 아직 정리되지 않은 베이스에서는 체인이 더 자주 포기될 수 있음.
- 코드 지점: `proposer.py:659`(함수 시작), `proposer.py:678`(폴백), `proposer.py:736` 주석과 일치하도록 동작 설명 보강.

2) 프리플라이트 단계에서 강제 배제 가드 추가
- 내용: `_preflight_schedule`에서 첫 op의 multiplicity(= `len(first_targets) > 1`)를 기준으로, 후속 op의 CFG상 multiplicity와 불일치하면 해당 체인을 무효화한다. 최소한 DOUT/DOUT4K/CACHE_READ_END/PLANE_CACHE_READ_END에 적용.
- 장점: CFG 힌트 누락이나 이름 선택 폴백 실수를 보정. 단일 진입점에서 보정 가능.
- 단점: 사후 배제이므로 대체 이름 선택까지 자동화하려면 추가 탐색 로직이 필요.
- 코드 지점: `proposer.py:1406` 근방 — 분할 직전/직후에 multiplicity 검사 삽입.

3) CFG 규칙 엄격화(운영 규범)
- 내용: `generate_seq_rules`의 후속 단계들에 ‘multi’를 반드시 포함. 특히 READ/PLANE_READ → DOUT 계열, CACHE_READ_END → DOUT 계열 등.
- 장점: 코드 변경 없이 일관성 향상. 선언적 관리.
- 단점: CFG 누락 시 런타임에서 놓칠 수 있음(1,2로 보완 권장).
- 위치 예: `config.yaml:208-233`, `config.yaml:294-317`, `config.yaml:359-365`.

4) RM 규칙 추가(선택)
- 내용: RM의 규칙 평가(옵션 기능)에 “체인 상속된 multi와 불일치”를 거절 사유로 추가. 체인 컨텍스트 전달이 필요하므로 제안 단계에서 ProposedOp.meta 등에 ‘expected_multi’ 태그를 싣고, RM이 이를 검증.
- 장점: 중앙집중적 일관성 강제.
- 단점: 경계면 확장 필요(ProposedOp/meta), 현재 구조보다 침투적.

## 코드 참조
- `proposer.py:668` — `_choose_op_name_for_base`: multi 힌트 필터
- `proposer.py:678` — `_choose_op_name_for_base`: 폴백(강화 대상)
- `proposer.py:915` — `_expand_sequence_seq`: 단계별 name 선택에 multi_hint 적용
- `proposer.py:986` — `_expand_sequence_chain`: 비-SEQ 단계 name 선택에 multi 적용
- `proposer.py:1406` — `_preflight_schedule`: DOUT/END 분할(배치 전 멀티 가드 위치)
- `scheduler.py:378` — 트랜잭션 내 순차화로 READ→DOUT 중첩 방지
- `resourcemgr.py:247` — multiplicity 파생 우선순위
- `resourcemgr.py:456` — reservation 시 single×multi/multi×multi 배제
- `docs/PRD_v2.md:337` — READ multi-plane 후속 DOUT 순차 생성 요구
- `config.yaml:211` — READ → DOUT에 ‘inherit: [same_page, multi, same_celltype]’

## 아키텍처 인사이트
- ‘의도된 멀티성’을 이름 선택 레벨에서 보존하고, 프리플라이트에서 2차 방어선을 두면 실행·관측 양쪽에서 일관성이 높아진다.
- 스케줄러의 순차화는 충돌을 줄이지만 멀티성 불일치를 해결하지 않는다. 선택·검증 단계에서 명시적으로 막는 편이 안전하다.

## 관련 연구
- `research/2025-09-07_22-28-08_prd54_inherit_rules_impl.md` — inherit 규칙/SEQ 전개 맥락과 DOUT 분할 계획.

## 미해결 질문
- DOUT 외 다른 후속 베이스(예: COPYBACK_PROGRAM_* 체인)에도 동일 강제를 적용할지 여부. 적용 범위 기준(베이스 목록)을 명확히 해야 함.
- 이름 선택 단계에서 ‘대체 op_name’ 탐색(멀티 일치) 자동화를 어느 수준까지 할지 결정 필요.

