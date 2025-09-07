---
date: 2025-09-07T07:01:11Z
researcher: Codex
git_commit: 306b495154519224c6511bd6f5f3c7b7cc546347
branch: main
repository: nandseqgen_v2
topic: "READ not scheduled after ERASE->PROGRAM despite proposer ok"
tags: [research, codebase, scheduler, proposer, read, window]
status: complete
last_updated: 2025-09-07
last_updated_by: Codex
last_updated_note: "PHASE_HOOK 기반 분리 예약 대안 조사 추가"
---

# 연구: ERASE->PROGRAM 이후 READ가 proposer ok인데 예약되지 않음

**Date**: 2025-09-07T07:01:11Z
**Researcher**: Codex
**Git Commit**: 306b495154519224c6511bd6f5f3c7b7cc546347
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
ERASE->PROGRAM 수행 이후 READ 동작이 proposer 단계에서 ok 및 selected로 보이지만, 실제로 스케줄/예약(operation_timeline/sequence)에는 반영되지 않는 원인 분석.

## 요약
- 원인: Scheduler가 배치의 모든 연쇄 작업에 대해 admission window를 강제하여, READ 다음에 붙는 두 번째 연쇄 작업(DOUT 또는 CACHE_READ 시퀀스)이 창(Window) 밖으로 밀려 배치 전체가 롤백됨. Proposer는 "첫 번째 op만" 창 내 보장을 가정하지만, Scheduler는 모든 op에 대해 창 체크를 수행함으로써 불일치 발생.
- 증거: proposer 로그에는 Read_SLC가 여러 번 selected로 기록되지만(out/proposer_debug...), operation_timeline/operation_sequence에는 READ가 없음. Scheduler 코드가 배치 내 모든 p에 대해 window_exceed를 검사하는 로직을 확인.
- 해결 방향: (A) Scheduler에서 admission window 체크를 배치의 첫 op에만 적용하거나, (B) 연쇄의 두 번째 op(DOUT 등)를 instant_resv로 취급, (C) 정책(admission_window) 확대 등. A가 가장 의도에 부합.

## 상세 발견

### Proposer 동작과 로그
- Proposer는 phase-conditional 분포에서 후보를 뽑고, 첫 op의 창 내 feasibility만 보장한 뒤, 1-스텝 시퀀스를 확장해 배치(len_batch)로 반환함.
- `proposer.py:1145` 에서 "Whole-batch return (first op inside admission window already enforced)" 코멘트가 이 의도를 명시.
- 실제 로그 예시:
  - `out/proposer_debug_250907_0000001.log:3051` — "selected op=Read_SLC base=READ start_us=21310.150 len_batch=2"
  - 이 구간에서 다수의 "try name=Read_SLC -> ok"와 selected가 이어짐(예: `out/proposer_debug_250907_0000001.log:3048`, `3057`, `3066`, `3072`, ...).

### Scheduler의 창(window) 체크가 배치 전체에 적용됨
- Scheduler는 배치 내 모든 ProposedOp에 대해 admission window를 재검증함:
  - `scheduler.py:276` 이후 루프에서 각 `p in batch.ops`에 대해
  - `scheduler.py:279` — "if (not instant) and W > 0 and p.start_us >= (now + W): ... break"
- Proposer는 첫 op만 창 내 보장하지만, 두 번째 op(DOUT 또는 CACHE_READ.SEQ 등)는 READ의 실행 시간과 `sequence_gap`(기본 1.0us) 때문에 현재 now 기준 창(기본 W=0.5us)을 넘어감.
- 그 결과, 두 번째 op에서 window_exceed가 발생하여 ok_all=False로 배치 전체 롤백.

### 타임라인에 READ가 없는 증거
- 타임라인/시퀀스에는 해당 READ가 기록되지 않음:
  - `out/operation_timeline_250907_0000001.csv:5` — `Page_Program_SLC` (21000.33→21310.15) 다음, 바로 `Block_Erase_SLC`(21900.0→28900.11)로 진행, 21310~21900 사이 READ 없음.
  - `out/operation_sequence_250907_0000001.csv:1`~ — 초기 구간에 READ 관련 항목 없음.

### 정책/설정
- Admission window: `config.yaml:24` 근처 정책 섹션에서 `sequence_gap: 1.0`, `admission_window`는 proposer 로그에 `0.5`로 반영됨.
- READ 베이스 시퀀스: READ는 다음과 같은 1-스텝 연쇄를 가짐(두 번째 op는 DOUT 또는 CACHE_READ 관련):
  - `config.yaml:194` — READ base에 `sequence: probs: DOUT 0.9, CACHE_READ.SEQ 0.1`와 inherit 규칙 명시.

## 코드 참조
- `scheduler.py:279` - 배치의 각 op에 대해 admission window 체크 수행.
- `proposer.py:1145` - "first op inside admission window already enforced" 주석(설계 의도).
- `out/proposer_debug_250907_0000001.log:3051` - Read_SLC selected, len_batch=2.
- `out/operation_timeline_250907_0000001.csv:5` - 21310.15 이후 READ 미반영, 21900.0에 ERASE 시작.
- `config.yaml:194` - READ base의 시퀀스 정의(DOUT/CACHE_READ.SEQ 연쇄).

## 아키텍처 인사이트
- 현재 설계는 Proposer가 첫 op만 창 내에 들도록 하고, 시퀀스는 사전(preflight) feasibility만 확인 후 배치로 반환.
- Scheduler는 배치 각 op에 대해 동일한 창 제약을 재적용하여 Proposer의 가정과 불일치. 이로 인해 READ 배치가 반복적으로 롤백되고, 타임라인에 READ가 남지 않음.

## 역사적 맥락(thoughts/ 기반)
- thoughts/ 디렉터리 내 관련 설계 기록은 본 조사에서 확인되지 않음.

## 관련 연구
- 없음

## 미해결 질문
- 창(window) 정책의 의도: 연쇄 전체에 적용인가, 첫 op만인가? Proposer 주석상 첫 op만으로 보이나, 명확한 합의 필요. -> (검토완료) 첫 op 만 적용.
- DOUT/CACHE_READ 연쇄를 이벤트 기반(PHASE_HOOK)으로 분리 예약하는 대안 검토 가치. -> (검토완료) 검토 불필요

## 개선 제안(요약)
- **최종안**(A): `scheduler._propose_and_schedule`에서 admission window 검사를 배치의 첫 op에만 적용.
  - 구현 예시: `for idx, p in enumerate(batch.ops):` 사용, `if idx == 0 and ... window check ...` 형태.
- 대안(B): `op_bases.DOUT` 및 연쇄 후속 op를 `instant_resv: true`로 설정해 창 검사 우회(연쇄의 짧은 housekeeping 성격일 때).
- 대안(C): `policies.admission_window` 확대(부작용으로 스케줄러의 near-term 집중도가 낮아질 수 있음).

