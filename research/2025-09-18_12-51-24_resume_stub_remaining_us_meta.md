---
date: 2025-09-18T12:51:24Z
researcher: Codex
git_commit: 9887f587f18cc35b83a1bea32d8e46395933994c
branch: main
repository: nandseqgen_v2
topic: "PROGRAM_RESUME chain stub meta handling"
tags: [research, scheduler, resource-manager, suspend-resume, instrumentation]
status: draft
last_updated: 2025-09-18
last_updated_by: Codex
---

# 연구: PROGRAM_RESUME 체인 스텁 메타 재등록 영향 분석

**Date**: 2025-09-18T12:51:24Z  
**Researcher**: Codex  
**Git Commit**: 9887f587f18cc35b83a1bea32d8e46395933994c  
**Branch**: main  
**Repository**: nandseqgen_v2

## 배경/질문
`suspend_resume_chain_enabled=true` 환경에서 `PROGRAM_RESUME` 이후 생성되는 CORE_BUSY 체인 스텁은 `_chain_stub` 플래그만 붙은 채 실제 `register_ongoing` 을 호출하지 않는다. 이로 인해:
- 체인 스텁 실행 후 원래 meta의 `end_us` 가 갱신되지 않아 다음 `PROGRAM_SUSPEND` 에서 `remaining_us=0` 으로 계산되는지,  
- 스텁을 `ongoing_ops` 로 재등록하면 어떤 부수효과가 있는지를 확인하고 후속 연구 범위를 정의한다.

## 실험 구성
1. **Baseline** – 현행 코드 유지. 계측 로그를 `out/validation/resume_remaining_us_baseline.jsonl` 에 수집.  
   명령: `SR_REMAINING_US_ENABLE=1 .venv/bin/python main.py --config config.yaml --num-runs 2 --run-until 20000 --seed 7 --out-dir out/seed7`
2. **Temporary Patch** – 체인 스텁 커밋 직후 meta.end_us 를 스텁의 종료 시각으로 직접 갱신.  
   - 구현: `scheduler.py` 체인 루프에서 `resume_from_suspended_axis` 호출 뒤 `_ongoing_ops[die][-1].end_us = stub_end`. (환경 플래그 `SR_CHAIN_ADJUST_META` 로 게이트)
   - 계측 로그를 `out/validation/resume_remaining_us_fix.jsonl` 에 수집.  
   명령: `SR_REMAINING_US_ENABLE=1 SR_CHAIN_ADJUST_META=1 SR_REMAINING_US_LOG_PATH=out/validation/resume_remaining_us_fix.jsonl .venv/bin/python main.py --config config.yaml --num-runs 2 --run-until 20000 --seed 7 --out-dir out/seed7_fix`

## 관측 결과
- **Baseline** (`out/validation/resume_remaining_us_baseline.jsonl`)
  - `op_uid=3`의 두 번째 `PROGRAM_SUSPEND` 이벤트가 `remaining_us=0` 으로 기록되어 후속 CORE_BUSY 잔여 시간이 소멸. (`lines 1-3`)
  - `op_uid=1`도 동일하게 체인 후 `remaining_us=0`. (`lines 4-6`)
  - 분석 스크립트 결과: `pairs=4`, `unmatched suspend events=5` (모두 0 또는 미처리).
- **Temporary Patch** (`out/validation/resume_remaining_us_fix.jsonl`)
  - 동일 `op_uid=3`가 다시 suspend 될 때 `remaining_us≈2709.44µs` 로 갱신되어 잔여 시간이 유지. (`lines 1-4`)
  - 후속 반복 suspend에서도 `remaining_us` 가 순차적으로 감소하면서 유지되며, `delta_us` 는 ±3.6e-12 수준(quantize 노이즈)에 머무름. (`lines 5-18`)
  - 분석 스크립트 결과: `pairs=9`, `unmatched suspend events=1` (남은 1건은 아직 체인 스텁이 실행되지 않은 미완료 케이스).

## 해석
- 체인 스텁이 `ongoing_ops`/`suspended_ops` 흐름에 등록되지 않아 meta의 `end_us` 가 업데이트되지 않는 것이 `remaining_us=0`의 직접적 원인.
- Stub 실행이 끝난 뒤 meta.end_us 를 스텁 종료 시각으로 보정하면 문제 재현이 사라짐. 이는 stub 자체를 정식 등록하거나, meta를 명시적으로 갱신해야 함을 시사한다.

## 리스크 / 고려 사항
- 체인 스텁을 `register_ongoing` 으로 등록하면 `_emit_op_events` 가 기존 파이프라인에 따라 OP_START/OP_END 를 다시 큐잉할 수 있어 OP_END 중복 문제가 확대될 가능성.
- `operation_sequence` 등 PRD 산출물에 체인 스텁이 정식 op 으로 노출되어 다운스트림 소비자(CSV 파서, viz)가 영향을 받을 수 있음.
- `resume_from_suspended_axis` 호출 후 meta 객체를 직접 mutate 하는 방식은 thread-safe 하진 않지만 현 구조(단일 스레드)에서는 동작. 다만 `remaining_us` 계산 시점(quantize)과 race를 고려해야 함.

## 후속 연구 과제
1. 체인 스텁을 안전하게 `ongoing_ops` 에 재등록할 수 있는지 설계 (중복 이벤트 차단, addr_state 영향 분석 포함).  
2. 재등록/갱신 여부에 따른 `suspended_ops` stack consistency 및 rollback 경로 검증.  
3. instrumentation 확장: stub 자체 메타 분리 여부, `remaining_us` log 에서 0이 아닌 잔여만 집계하도록 필터.

## 참고 로그 & 스크립트
- Baseline 로그: `out/validation/resume_remaining_us_baseline.jsonl`
- Patch 로그: `out/validation/resume_remaining_us_fix.jsonl`
- 분석 스크립트: `tools/analyze_resume_remaining_us.py`

## Notes
- Temporary patch 코드는 현재 repo 에 반영돼 있지 않으며 실험 전용 브랜치/환경 플래그(`SR_CHAIN_ADJUST_META`) 로만 사용했다.
