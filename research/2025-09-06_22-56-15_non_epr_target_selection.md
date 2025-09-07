---
date: 2025-09-06T22:56:15+0900
researcher: Codex CLI
git_commit: 9424de5ee928bfc1bc7b25e724a3d053589c2e78
branch: main
repository: nandseqgen_v2
topic: "PRD 5.4 Proposer sampling vs non‑EPR target selection"
tags: [research, codebase, proposer, scheduler, non-epr, target-selection]
status: complete
last_updated: 2025-09-06
last_updated_by: Codex CLI
last_updated_note: "후속 연구: hook_targets 설정과 훅 보강 베이스 인터페이스 분석"
---

# 연구: PRD 5.4 Proposer→Workflow(샘플링)와 non‑EPR 타겟 선택 불일치

**Date**: 2025-09-06T22:56:15+0900
**Researcher**: Codex CLI
**Git Commit**: 9424de5ee928bfc1bc7b25e724a3d053589c2e78
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
PRD_v2.md의 5.4 Proposer→Workflow(샘플링 단계) 대비, 구현상 erase/program/read(E/P/R) 이외의 operation에서 target이 정의되지 않아 후보가 빨리 소진되는 문제. 현재 non‑EPR에 대한 target 결정 방식은 무엇이며, 없다면 개선 제안은?

## 요약
- 현재 `proposer.propose`는 E/P/R 계열만 AddressManager로 target을 샘플링하고, 그 외(non‑EPR)는 오직 두 경로만 지원한다: (1) 직전 READ 계열 훅에 의해 스케줄러가 제공한 `hook.targets`를 사용, (2) sequence의 2번째 op로서 상속 규칙으로 target을 전달.
- `scheduler._emit_op_events`는 READ 패밀리일 때만 PHASE_HOOK에 `targets/plane_set`를 실어 보낸다. 따라서 READ가 아닌 컨텍스트에서는 non‑EPR 후보가 `no_context_for_non_epr`로 소진된다.
- 로그에서도 non‑EPR 후보가 반복적으로 `no_context_for_non_epr`로 거절됨을 확인했다.
- 개선 방향: (A) Proposer에서 non‑EPR fallback 타겟 유도(hook의 die/plane 또는 RM 상태 기반), (B) Scheduler 훅 보강 대상을 READ 외(PROGRAM/ERASE 등)로 확대(설정화), (C) phase 확률 튜닝/제한으로 문맥 없는 non‑EPR 시도를 억제.

## 상세 발견

### Proposer의 non‑EPR 타겟 처리
- E/P/R 판별 및 샘플링 베이스:
  - `proposer.py:590` `_is_addr_sampling_base`는 ERASE/READ/PROGRAM 패밀리만 AddressManager 대상이라고 명시.
- non‑EPR 경로의 타겟 결정:
  - `proposer.py:870` 이후 분기에서 non‑EPR은 `hook.targets`가 없으면 거절.
  - 거절 사유: `no_context_for_non_epr`.
  - 시퀀스 2단계 상속은 별도로 처리되며, 이 경우 `_targets_with_inherit`로 타겟을 유지/변형.
- 코드 레퍼런스:
  - `proposer.py:851` E/P/R이면 `_sample_targets_for_op` 사용, 실패 시 `sample_none`.
  - `proposer.py:870` 비‑EPR이면 `hook.targets`를 파싱(`_targets_from_hook`), 없으면 `no_context_for_non_epr`.
  - `proposer.py:560` `_expand_sequence_once`와 `proposer.py:582` `_targets_with_inherit`로 시퀀스 상속 처리.

### Scheduler의 PHASE_HOOK 타겟 전달(READ 한정)
- 스케줄러는 READ 패밀리에서만 훅에 `plane_set`/`targets`를 실어 보냄:
  - 조건: `hook_targets_enabled`가 true이고 base가 READ 계열.
  - 구현: `scheduler.py:296`에서 READ 계열만 `hook_targets_payload` 구성 후 훅에 첨부.
  - 훅은 모든 non‑ISSUE 상태 구간의 pre/post 경계 시각에 plane별로 생성됨(`scheduler.py:321`, `scheduler.py:332`).
- 코드 레퍼런스:
  - `scheduler.py:285` 주석 및 정책, `scheduler.py:296` 분기, `scheduler.py:301` payload 구성, `scheduler.py:326`/`scheduler.py:334` 훅 push.

### 관찰된 런타임 현상(로그)
- `out/proposer_debug_250906_0000001.log`에 다수의 non‑EPR 후보가 `no_context_for_non_epr(base=SR/TRAINING/PROGRAM_RESUME ...)`로 거절됨.
  - 예시: `out/proposer_debug_250906_0000001.log:1` 이후 첫 후보군 처리에서 SR/Training 등이 연속 거절.

## 코드 참조
- `proposer.py:851` - E/P/R 타겟 샘플링 실패 시 `sample_none` 기록
- `proposer.py:870` - non‑EPR은 `hook.targets`가 없으면 `no_context_for_non_epr`
- `proposer.py:560` - 시퀀스 확장(상속) 로직 진입점
- `scheduler.py:296` - READ 계열일 때만 훅에 `targets/plane_set`를 부여
- `scheduler.py:326` - PHASE_HOOK payload에 `targets`/`plane_set` 삽입

## 아키텍처 인사이트
- 현재 설계는 non‑EPR 타겟을 “직전 READ 문맥”에 강하게 결합했다. 그 결과 READ가 아닌 상태에서 샘플링된 non‑EPR은 컨텍스트 부재로 자연스럽게 탈락한다.
- PRD 5.4 기준, non‑EPR의 타겟은 케이스별로 다르며(예: SR은 die/plane 기준, ODT/RESET은 타겟 불필요, DOUT은 READ 이후 상속) — 이를 일관되게 처리하려면 최소한 “컨텍스트 유도 규칙”이 필요하다.
- ProposedOp가 항상 `targets`를 요구하는 현재 API에서는, 타겟 불필요/범용(die‑wide) op에도 dummy/유추 타겟이 필요해짐. 이 부분은 Proposer에서 안전한 기본값(예: hook die/plane)을 공급하면 해소 가능.

## 개선 제안(대안 비교)

1) Proposer Fallback(간단)
- 아이디어: non‑EPR이며 `hook.targets`가 없을 때, `hook.die/plane`로 최소 타겟을 구성(Address(die, plane, block=0, page=None)). scope가 DIE_WIDE인 op는 실질적으로 die만 사용.
- 장점: 변경이 작고 즉효. READ 문맥이 없어도 SR/Training/Reset 등 제안 가능.
- 단점: 일부 op가 block/page를 요구하는 payload 스키마와 정합성 검토 필요. 문맥 부적합 제안 위험.

2) Scheduler 훅 보강 대상 확대(설정화)
- 아이디어: READ 외 PROGRAM/ERASE/DSL_VTH_CHECK 등에도 `targets`/`plane_set`를 훅에 실어 보냄. 또는 `policies.hook_enrich_bases`로 설정 주도화.
- 장점: Proposer 변경 최소화, 문맥 전달 개선으로 non‑EPR 활성화.
- 단점: 훅 컨텍스트 범람/오적용 가능성. 어떤 base에 부여할지 정책 설계 필요.

3) RM 상태 기반 유도 함수 도입(보수적)
- 아이디어: Proposer에서 `_derive_non_epr_targets(cfg, hook, res_view, base)`를 추가하고, 다음 순서로 유도:
  - hook.die/plane가 있으면 그것을 사용
  - 해당 die의 활성 latch/read/cache/suspend 상태를 탐색해 가장 관련 plane을 선택
  - 최후에는 die=0/plane=0과 같은 보수적 기본값(설정으로 on/off)
- 장점: 실제 런타임 상태를 반영. 문맥 일관성 향상.
- 단점: 구현 복잡도 증가, 오판 가능성. 기본값 사용 여부를 config로 제어 필요.

권장 접근: 1단계로 (1) Proposer fallback을 추가하고, 동작 관찰 후 필요 시 (2) Scheduler 훅 보강을 설정화하여 READ 외 컨텍스트도 전달. 마지막으로 (3) RM 기반 유도는 추후 정밀도가 필요할 때 도입.

## 역사적 맥락(thoughts/ 기반)
- 해당 리포지토리에 `thoughts/` 디렉터리는 존재하지 않음. 관련 역사적 결정 레코드는 미발견.

## 관련 연구
- N/A

## 미해결 질문
- SR/SR_ADD의 payload.expected_value는 die/plane 상태에 의존. Proposer/Scheduler 어느 계층에서 계산·부여할지 범위 결정 필요. -> (검토완료) Proposer 단계에서 `PRD_v2.md:25-37` 3.1 Operation Sequence 에 따라 SR/SR_ADD 가 스케쥴 되는 시점이 아닌 **끝나는 시점**에서의 ResourceManager 의 target (die,plane) 혹은 target die 의 모든 plnae 의 op_state 를 조회하여 결정.
- non‑EPR 중 die‑wide 스코프 op의 “타겟 필요성”을 완화할지(빈 리스트 허용) vs 최소 타겟 제공을 유지할지 정책 확정 필요. -> (검토완료) phase_key 를 조회한 target 이 기본 적용. 원칙은 erase/program/read 계열이 아닌 operation 이 sample_none 으로 거절되는 것은 설계를 잘못한 것이라는 것. QUEUE_REFILL 도 target 이 있어야 함. 지금 없다면 target 정책을 정해야 함.
- `policies.hook_targets_enabled`의 기본값 유지/노출 및 훅 보강 base 목록의 설정 인터페이스 정의. -> (검토완료) hook_target_enabled 설정을 없애고, operation 에 상관없이 hook 에 targets 정보를 싣도록 변경.
