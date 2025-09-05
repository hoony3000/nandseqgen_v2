# TODO / Bug & Experiment Tracker

목표: 버그와 개선 작업을 일관된 포맷으로 관리하고, 실험과 평가까지 한 흐름으로 기록합니다.

- 작성 원칙: 간결하게, 재현 가능한 정보 우선, 정량적 평가 권장
- 관련 문서: `docs/PRD_v2.md`, `docs/viz_required_outputs.md`
- 사용 방법: 아래 템플릿을 복사해 항목을 추가하세요(우선순위 섹션 권장).

## 템플릿

```
- [ ] <짧은 제목>
  - 문제 상황: <현상 요약 + 재현 절차/조건>
  - 개선 방향과 실험: <가설, 변경안, 실험 설계(데이터/파라미터/절차)>
  - 평가 결과: <정량 지표/로그/스크린샷 경로 + 결론>
```

---

## High Priority

<!-- 여기에 가장 시급한 이슈들을 추가하세요 -->


- [x] 생성되는 operation 이 하나밖에 없음
  - 문제 상황: main.py 를 실행시켰을 때 operation_sequence*.csv 파일에 "Block_Erase_SLC" 밖에 없음.
  - 개선 방향과 실험: 기본 CFG에 PROGRAM/READ의 최소 스펙을 추가(`main.py:_ensure_min_cfg`에 `op_bases[PROGRAM_SLC|READ4K]`와 `op_names[All_WL_Dummy_Program|4KB_Page_Read_confirm_LSB]` 정의). `python3 main.py -n 1 -t 1000 --out-dir out` 실행 후 CSV 확인.
  - 평가 결과: 완료 — `operation_sequence_*.csv`에 `All_WL_Dummy_Program` 포함, `committed_by_base`에 `PROGRAM_SLC` 집계. `address_touch_count_*.csv`에도 PROGRAM 집계 1건 확인.

- [ ] phase_conditional runtime 으로 생성 시 .ISSUE state 제외 필요

- [ ] op_state_probs.yaml 파일 미생성
  - 문제 상황: `PRD_v2.md:168-179` 문서에 따르면 op_state_probs.yaml 파일을 생성하게 돼있으나 생성되지 않음
  - 개선 방향과 실험: CFG runtime 초기화가 잘됐는지 점검

 - [ ] operation_timeline `source` 필드 미기록 (스펙 미준수)
  - 문제 상황: PRD §3.3에 `source` 필드가 명시되었으나 현재 `main.py:115`에서 항상 `source=None`으로 기록됨. 생성 경로 추적 불가.
  - 개선 방향과 실험: `proposer.propose()` 단계에서 후보 생성 출처를 태깅하고 `InstrumentedScheduler` → `export_operation_timeline`으로 전파. 3개의 시나리오(bootstrap on/off, pc_demo 옵션 2종)에서 CSV 값 확인.
  - 평가 결과: (작성 예정)

 - [ ] op_state_timeline의 `op_name` 의미 혼동
  - 문제 상황: PRD §3.4의 `op_name`은 오퍼레이션 이름 의미이지만 현재 `main.py:199`에서 base 문자열을 기록함(사실상 `op_base`).
  - 개선 방향과 실험: 필드 의미를 명확화(옵션 A: `op_name`→실제 이름, 옵션 B: 컬럼명을 `op_base`로 변경). 시각화(`viz_required_outputs.py state`)와 다운스트림 소비자 정상 동작 확인.
  - 평가 결과: (작성 예정)

 - [ ] viz Gantt 축 처리 버그 가능성(`matplotlib.hlines`에 문자열 y 사용)
  - 문제 상황: `viz_required_outputs.py:84,134`에서 `hlines(y=r["lane"])`로 문자열 y값 사용. 환경에 따라 범주형 축 미해석 오류 가능. `yidx` 계산(`:73,:123`) 후 미사용.
  - 개선 방향과 실험: y좌표를 `yidx`(정수)로 변경하고 yticks/labels에 `lane` 매핑. 샘플 CSV로 before/after 렌더링 비교 및 예외 발생 여부 확인.
  - 평가 결과: (작성 예정)

 - [ ] PRD §3 CSV 스키마 자동 검증 도구 추가
  - 문제 상황: 수동 점검은 누락/회귀 위험. 현재 YAML 전용 검사(`tools/check_op_specs.py`)만 존재, CSV 스키마 검증 부재.
  - 개선 방향과 실험: `tools/validate_required_outputs.py`(신규)로 각 CSV 필수 컬럼 존재/타입/범위(input_time∈[0,1]) 검사. `out/` 샘플에 대해 통과/실패 케이스 유닛 테스트 추가.
  - 평가 결과: (작성 예정)

## Medium Priority

<!-- 중요하지만 즉시 긴급하지는 않은 작업들 -->

- [ ] address_touch_count 가 집계되지 않음
  - main.py 를 실행시켰을 때 address_touch_count*csv 에 아무것도 집계되지 않음
  - 개선 방향과 실험: address_touch_count 관련 log 함수 점검

 - [ ] RNG 결정성 일원화(NumPy/Random 동기화)
  - 문제 상황: `main.py`에서 Scheduler는 `random.Random(seed)`를, AddressManager는 `numpy.default_rng(1234)` 고정 사용. 동일 seed라도 경로 차이 발생.
  - 개선 방향과 실험: CLI `--seed`를 AddressManager에도 전파. 동일 seed 반복 실행 시 모든 CSV 해시 일치 여부 검증.
  - 평가 결과: (작성 예정)

 - [ ] Bootstrap 단계 전이 회귀 테스트
  - 문제 상황: `bootstrap.py`의 stage 전이 로직(erase→program→read)이 회귀 위험.
  - 개선 방향과 실험: 최소 토폴로지로 각 임계값 직전/직후 케이스 골든 스냅샷 비교 테스트.
  - 평가 결과: (작성 예정)

 - [ ] op_state×op_name×input_time 분포 품질 지표 정의
  - 문제 상황: PRD §4 "고른 분포" 기준 미정량화.
  - 개선 방향과 실험: 구간별 엔트로피/지니계수 등 지표 정의하고 실패 기준 임계값 설정. E2E 실험 스크립트 추가.
  - 평가 결과: (작성 예정)

## Low Priority / Backlog

<!-- 아이디어, 개선 제안, 장기 과제 등 -->

---

## 작업 기록 팁

- 재현 정보: 입력 데이터 경로, 시드/난수 고정, 실행 명령(옵션 포함)
- 실험 설계: 단일 변수 변경 원칙(가능하면), 통제군/실험군 구분
- 평가 기준: 통일된 지표 이름과 단위 사용, 허용 오차 명시
- 아티팩트: 결과물 경로를 남기고, 큰 파일은 저장소 외부(예: `out/`)에 두기
