title: 계획 — PROGRAM rejected after ERASE(sample_none) 개선
date: 2025-09-07
owner: Codex CLI
status: proposed
related:
  - research/2025-09-07_04-42-50_program_rejected_after_erase_sample_none.md
  - docs/VALIDATOR_INTEGRATION_GUIDE.md
  - docs/PRD_v2.md
---

## Problem 1‑Pager

- 배경: `phase_conditional_overrides`로 SLC ERASE/PROGRAM/READ만 활성화한 환경에서, 첫 ERASE 이후 PROGRAM이 proposer에서 반복적으로 `sample_none`으로 거절됨. ERASE tBUSY가 길어 `window_exceed`도 빈발.
- 문제: Scheduler가 커밋된 ERASE/PROGRAM 효과를 AddressManager(AM) 상태에 반영하지 않아, 다음 제안에서 PROGRAM 샘플링 조건(ERASE 완료 블록 필요)이 충족되지 않음.
- 목표: OP_END 시점에 ERASE/PROGRAM의 주소 상태를 AM에 동기화하고, 필요 시 EPR(주소 의존 규칙)과 런타임 구성으로 실패/경고를 줄여 정상적인 PROGRAM 제안을 가능하게 한다.
- 비목표: RM 타임라인(예약·자원) 동작 및 proposer의 샘플링 구조 자체를 근본 변경하지 않음. 외부 I/O 추가 없음.
- 제약: 함수 ≤ 50 LOC, 파일 ≤ 300 LOC, 순환복잡도 ≤ 10. 민감 데이터 로깅 금지. 변경은 기본적으로 추가적/보수적이어야 함.

## 영향도 및 호출 경로

- proposer 샘플링: `proposer.py:520` `_sample_targets_for_op` → `addrman.sample_erase/sample_pgm/sample_read`
- AM PROGRAM 조건: `addrman.py:606` `apply_pgm`, `addrman.py:492` `sample_pgm`에서 ERASE 완료·모드 일치 필요
- 스케줄러 이벤트: `scheduler.py:114` `tick()` → `OP_END` 발생 시 `_handle_op_end`
- RM 규칙(EPR): `resourcemgr.py:957` `_eval_rules`에서 주소 의존 콜백 호출(현재 `op_celltype=None` 전달)

## 대안 비교

1) OP_END 시 AM 동기화(선택)
   - 장점: 간단·국소 변경, 샘플링 원천 진실(AM) 보장, 회귀 위험 낮음
   - 단점: OP_END에 대상 판별/변환 로직 필요
   - 위험: 잘못된 베이스 매칭 시 과잉/과소 반영 가능 → 엄격한 매칭·가드

2) RM overlay를 proposer 샘플링에 직접 반영(침습적)
   - 장점: 동일 트랜잭션 일관성 강화, 즉시 효과
   - 단점: 모듈 경계 침범, 샘플러 API 변경 필요, 복잡도↑
   - 위험: 순서/동시성 경계 오판 → 보수적 금지 과도 가능

3) `phase_conditional_overrides`로 CORE_BUSY 중 PROGRAM 가중치 0(보완)
   - 장점: `sample_none`/`window_exceed` 시도 감소, 간단
   - 단점: 근본 원인 미해결(AM 미동기화)
   - 위험: 설정 의존; 커버리지 저하 가능

→ 선택: 1) OP_END 동기화 + 3) 보완 구성. 2)는 후속 연구 항목으로 유지.

## 구현 계획

1) `Scheduler._handle_op_end`에서 AM 동기화 추가
   - 파일: `scheduler.py:114`
   - 내용:
     - 헬퍼 추가: `_am_apply_on_end(base: str, targets: List[Address]) -> None`
       - ERASE 계열: `base == "ERASE"` 인 경우 → `addrman.apply_erase(addrs, mode=<celltype>)`
       - PROGRAM 계열: `base` 가 PROGRAM_SLC/CACHE_PROGRAM_SLC/ONESHOT_CACHE_PROGRAM/ONESHOT_PROGRAM_MSB_23h/ONESHOT_PROGRAM_EXEC_MSB/COPYBACK_PROGRAM_SLC/ONESHOT_COPYBACK_PROGRAM_EXEC_MSB/ALLWL_PROGRAM 중 하나일 경우 → `addrman.apply_pgm(addrs, mode=<celltype>)`
       - Address → AM 포맷 변환: `(die, block, page)` numpy 배열(shape: (#, 1, 3)); page가 None이면 ERASE는 0, PROGRAM은 주어진 page 사용
       - celltype 결정: `cfg.op_names[op_name].celltype`(SLC/A0SLC/ACSLC 등)
     - 기존 release 정책은 유지(`DOUT*_END`, `ONESHOT_PROGRAM_*_MSB` 처리 등)
   - 제약: 40 LOC 내외, 예외 안전(빈 타깃/누락 필드 무시), 중복 적용 방지(한 번만 실행)

2) EPR(주소 의존 규칙) 통합 강화
   - 파일: `main.py:1` (초기화 루틴)
     - 실행 시 AddressManager 인스턴스 생성 후 `rm.register_addr_policy(am.check_epr)` 호출
     - 구성: `config.yaml` → `constraints.enabled_rules: ["state_forbid", "addr_dep"]`, `enable_epr: true`(문서 반영)
   - 파일: `resourcemgr.py:957`
     - `_eval_rules(...)`에서 `op_celltype` 전달: `op_name = self._op_name(op)` 후 `cell = (self.cfg.get("op_names", {}) or {}).get(op_name, {}).get("celltype") or None`
     - `addr_policy(..., op_celltype=cell, ...)`로 호출
   - 동작: 프로그램 전/읽기 오프셋 가드/동일 페이지 중복/동일 블록 이종 celltype 등을 사전 차단(구성에 따라)

3) 문서 업데이트
   - 파일: `docs/VALIDATOR_INTEGRATION_GUIDE.md:1`
     - `enabled_rules`/`enable_epr` 예시와 런타임 바인딩(`rm.register_addr_policy(am.check_epr)`)을 “기본 예시”로 승격
   - 파일: `docs/PRD_v2.md:5`
     - “현재 시점 참조; 주소 상태는 OP_END에서 AM에 반영” 원칙 1줄 추가

## 수용 기준(AC)

- AC1: ERASE 완료(OP_END) 이후 최초 PROGRAM 제안에서 `addrman.sample_pgm`이 빈 결과가 아니며 `sample_none` 빈도가 현저히 감소
- AC2: 동일 시드·시간에서, ERASE 이전에는 PROGRAM 샘플 실패 가능하나 ERASE.END 이후에는 적어도 1개의 PROGRAM 성공 케이스가 로그에 존재
- AC3: `constraints.enabled_rules`에 `addr_dep`+`enable_epr: true` 설정 시, EPR 규칙 실패가 `reserve_fail:epr_dep`로 표준화되어 기록됨(`rm.last_validation()`로 확인 가능)
- AC4: 기존 CSV 스키마/정렬 변화 없음; 추가 필드/파일 없음

## 테스트 계획

- 시나리오 실행(단일 런)
  - 명령: `python main.py -t 20000 -n 1 --seed 42 --out-dir out`
  - 확인:
    - `out/proposer_debug_*.log`에서 초기 `Block_Erase_SLC -> ok` 이후 `Page_Program_SLC -> sample_none` 반복이 ERASE.END 이후에는 `ok`가 등장
    - `out/operation_timeline_*.csv`에서 ERASE 종료 시점 뒤에 PROGRAM row 존재

- EPR 유효성(옵션)
  - `config.yaml`에 `constraints: { enabled_rules: ["state_forbid", "addr_dep"], enable_epr: true, epr: { offset_guard: 0 } }`
  - PROGRAM 전 READ 페이지가 `last_programmed_page - offset_guard` 조건을 위반하면 `reserve_fail:epr_dep`로 거절

- 회귀 가드
  - ERASE/PROGRAM 외 동작은 기존과 동일
  - 빈 타깃 시 AM 적용이 no-op이며 예외 없이 진행

## 리스크 및 완화

- multi‑plane/sequence 케이스: 전달 `targets`가 `Address`로 정규화되어 있고, PROGRAM은 페이지 증가 누적(`apply_pgm`)이 설계대로 동작 → 변환 로직에서 None page 가드
- celltype 일관성: EPR 활성 시 `epr_different_celltypes_on_same_block`로 보수적으로 차단; 비활성 시에도 ERASE SLC→A0/AC 허용 로직을 AM이 유지

## 작업 분해 및 추정

- Scheduler AM 동기화: 1.0h — 변환 헬퍼 + 베이스 매칭 + 호출
- EPR 통합(메인 바인딩 + celltype 전달): 0.5h
- 구성/문서 정리(선택적 overrides 포함): 0.5h

## 롤백 계획

- `_am_apply_on_end` 호출만 제거하면 AM 동기화 비활성화(기존 동작 복원). `register_addr_policy(None)`로 EPR 비활성. 설정/문서 변경은 원복 가능.

## 변경할 파일(참고용 포인터)

- `scheduler.py:114` — `_handle_op_end` 내 AM 동기화 진입점 추가
- `main.py:1` — AM 생성 직후 `rm.register_addr_policy(am.check_epr)` 바인딩
- `resourcemgr.py:957` — `_eval_rules`에서 `op_celltype` 전달
- `docs/VALIDATOR_INTEGRATION_GUIDE.md:1` — 예시 구성 반영

