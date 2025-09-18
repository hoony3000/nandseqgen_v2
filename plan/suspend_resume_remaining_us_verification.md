# SUSPEND->RESUME remaining_us 검증 계획

## Problem 1-Pager
- **배경**: `ResourceManager.move_to_suspended_axis` 는 SUSPEND 시점에서 ongoing 메타의 종료 예정 시각(`end_us`)과 현재 시각 차이를 `remaining_us` 로 계산해 `suspended_ops_*` 스택에 보관한다. Scheduler 는 `PROGRAM_RESUME` 후 이 값을 사용해 CORE_BUSY stub 을 재예약한다.
- **문제**: 실제 재개 시 stub 이 예약되는 시각(`start_at`)과 재예약 후 종료 시각(`end_us'`) 사이가 원래 계산된 `remaining_us` 와 다를 수 있다. 반복 SUSPEND/RESUME 환경에서는 큐 지연, quantize 재적용, 추가 훅 처리로 인해 잔여 시간이 누적 오차를 발생시킬 가능성이 있다.
- **목표**: 실제 실행된 CORE_BUSY stub 의 지속 시간과 원래 `remaining_us`/`meta.end_us` 차이가 얼마나 일치하는지 데이터를 수집해 검증한다. 오차가 발생한다면 패턴(양/음, 누적 여부)을 정량화한다.
- **비목표**: 아직 해결책을 구현하거나 stub 예약 방식을 변경하지 않는다. ERASE 축 점검은 후속 연구로 남긴다.
- **제약**:
  - 코드 변경은 300 LOC 이하, 함수 50 LOC 이하 유지.
  - 가상환경(.venv) 내 실행, 외부 네트워크 사용 금지.
  - 기존 validation 플래그(`validation.suspend_resume_op_end`)와 호환 가능한 계측을 우선 고려.

## 접근 대안 비교 (결정 전 >=2개)
1. **내장 계측(Instrumentation) 추가**
   - *방법*: `validation.suspend_resume_op_end.strategy2` 영역을 확장해 SUSPEND/RESUME 체인의 `meta.end_us`, `remaining_us`, stub 예약 시각(`start_us`, `end_us`)을 JSONL 로 기록.
   - *장점*: 실시간 ground truth 확보, 동일 실행 내에서 기대치 vs 실제치를 바로 비교 가능.
   - *단점*: 핵심 모듈(scheduler/resourcemgr)에 코드 추가가 필요, 계측 플래그가 꺼진 경우엔 정보 수집 불가.
   - *위험*: 계측 코드가 본 로직에 영향을 줄 수 있음(예: 예외 미처리, 성능 저하). 방지 위해 try/except 및 flag gating 필수.
2. **외부 로그 후처리**
   - *방법*: 기존 이벤트 로그(예: `strategy1_events.jsonl`)와 operation_sequence CSV를 조합해 SUSPEND/RESUME 타임라인을 복원하고 stub 지속 시간을 계산.
   - *장점*: 핵심 코드 수정 없이 검증 가능, 로그 형식이 안정적이면 재사용 용이.
   - *단점*: 현재 로그에는 `remaining_us` 가 기록되지 않아 복원이 어렵고, 동일 `op_uid` 를 통해 최초 end 시각을 알아내는 과정이 복잡.
   - *위험*: 데이터 해상도가 부족하면 오차 원인을 잘못 추정할 수 있음.
3. **EventQueue 스냅샷 기반 분석**
   - *방법*: resume 직전/직후 `EventQueue` 내용을 스냅샷 해 stub 이벤트의 시간차를 직접 측정.
   - *장점*: 큐 상태를 그대로 확인하여 예약 지연 여부를 명확히 알 수 있음.
   - *단점*: snapshot 로깅이 이미 strategy3에 있으나 stub 관련 필드가 부족하며, 중첩 suspend 사례에서는 스냅샷 시점 선택이 어렵다.
   - *위험*: 큐 스냅샷 빈도 증가가 성능에 영향, 데이터 해석이 복잡.

> **선택**: 대안 1(내장 계측) 채택. 오차가 발생하는지 판단하려면 `remaining_us` 와 stub 지속 시간을 한 자리에서 비교해야 하며, 최소 침습 계측으로 구현 가능하다. 필요시 대안 2를 후속 검증 보조로 사용할 수 있다.

## 실행 계획
1. **계측 설계 & 1차 구현**
   - `validation.suspend_resume_op_end` 설정을 확장해 `strategy4`(가칭) 또는 `strategy2` 내에 새로운 로그 스트림(`resume_drift.jsonl`) 추가.
   - `ResourceManager.move_to_suspended_axis` 에서 기록할 필드: `die`, `op_id/op_name/base`, `start_us`, `end_us`, `remaining_us`, `suspend_time`.
   - 체인 stub 예약 시점(`scheduler._propose_and_schedule` 내 `chain_jobs` 처리 및 stub commit 후)에 실제 예약 정보(`stub_start_us`, `stub_end_us`, `duration_used`)와 큐 재삽입 시간 기록.
   - 로그 구조 예: `{ "kind": "suspend", ... }`, `{ "kind": "resume_stub", ... }`를 동일 `op_uid` 로 연결.
2. **샘플 워크로드 준비**
   - 기존 연구 입력(`research/..._baseline_inputs.md`) 혹은 별도 스크립트로 SUSPEND/RESUME 를 연속 발생시키는 시나리오 구성.
   - 재현 런 명령을 스크립트/Make 타겟으로 정리 (예: `python main.py --config config.yaml --num-runs 1 --run-until 500 --seed 123 --out-dir out --enable-validation`).
3. **데이터 수집 & 분석 도구**
   - `tools/` 하위에 `analyze_resume_remaining_us.py` (<=150 LOC) 추가, JSONL 을 읽어 각 `op_uid`에 대해:
     - `expected_remaining_us` vs `stub_duration_us` 차이(delta)
     - multiple resume occurrences의 누적 오차, 부호, 이상치
   - 요약 리포트(MD/CSV) 생성.
4. **검증 실행**
   - 최소 2가지 config/seed 조합으로 실행하여 오차 유무 확인.
   - quantize(0.01us)로 인한 +-SIM_RES_US/2 오차는 허용 범위로 간주하고 임계값 설정.
5. **결과 정리**
   - 분석 결과를 `research/` 또는 `out/validation` 내 요약으로 남기고, 향후 수정 사항(예: stub duration 재계산 필요 여부) 판단.

## 완료 기준
- 계측 플래그 활성화 시 SUSPEND/RESUME 흐름마다 suspend & resume_stub 레코드가 한 쌍 이상 기록된다.
- 분석 스크립트가 delta 분포를 출력하고, 허용 오차 범위 및 이상 사례가 명확히 보고된다.
- 베이스라인 실행에서 delta가 +-SIM_RES_US 범위를 벗어난 케이스 존재 여부가 판별된다.

## 리스크 & 대응
- **계측이 본 로직에 영향**: try/except 보호, flag 체크로 비활성 시 완전 우회하도록 구현.
- **로그 파일 크기 증가**: 체인 빈도는 낮으므로 JSONL 로 충분; 필요 시 샘플링 옵션 추가.
- **분석 허위 양성**: quantize에 따른 소수점 오차를 별도 필드(`quantized=true`)로 명시해 필터링.

## 산출물
- 계측 코드(PR): scheduler/resourcemgr/validation 모듈 변경.
- 신규 분석 스크립트: `tools/analyze_resume_remaining_us.py`.
- 실행 가이드: plan/README 혹은 문서에 명령어 기록.
- 결과 보고: `research/<timestamp>_resume_remaining_us.md` (후속 연구 동기화).

## 인수 테스트 아이디어
- 단일 SUSPEND->RESUME: delta ~= 0 (±0.01us).
- 연속 3회 SUSPEND->RESUME: delta 누적 확인.
- stub 예약 실패 경로(r2.ok=false) 발생 시 로그가 어떻게 남는지 검증.
- PROGRAM이 아닌 ERASE SUSPEND는 현 스코프 밖이므로 regression 테스트에서 제외.
