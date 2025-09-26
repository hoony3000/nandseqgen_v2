# TODO / Bug & Experiment Tracker

목표: 버그와 개선 작업을 일관된 포맷으로 관리하고, 실험과 평가까지 한 흐름으로 기록합니다.

- 작성 원칙: 간결하게, 재현 가능한 정보 우선, 정량적 평가 권장
- 관련 문서: `docs/PRD_v2.md`, `docs/viz_required_outputs.md`
- 사용 방법: 아래 템플릿을 복사해 항목을 추가하세요(우선순위 섹션 권장). 완료되면 checkbox 빈칸을 x 로 채우세요.

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

- [ ] 3. operation_timeline `source` 필드 미기록 (스펙 미준수)
  - 문제 상황: PRD §3.3에 `source` 필드가 명시되었으나 현재 `main.py:115`에서 항상 `source=None`으로 기록됨. 생성 경로 추적 불가.
  - 개선 방향과 실험: `proposer.propose()` 단계에서 후보 생성 출처를 태깅하고 `InstrumentedScheduler` → `export_operation_timeline`으로 전파. 3개의 시나리오(bootstrap on/off, pc_demo 옵션 2종)에서 CSV 값 확인.
  - 평가 결과: (작성 예정)

- [ ] 4. op_state_timeline의 `op_name` 의미 혼동
  - 문제 상황: PRD §3.4의 `op_name`은 오퍼레이션 이름 의미이지만 현재 `main.py:199`에서 base 문자열을 기록함(사실상 `op_base`).
  - 개선 방향과 실험: 필드 의미를 명확화(옵션 A: `op_name`→실제 이름, 옵션 B: 컬럼명을 `op_base`로 변경). 시각화(`viz_required_outputs.py state`)와 다운스트림 소비자 정상 동작 확인.
  - 평가 결과: (작성 예정)

- [ ] 5. viz Gantt 축 처리 버그 가능성(`matplotlib.hlines`에 문자열 y 사용)
  - 문제 상황: `viz_required_outputs.py:84,134`에서 `hlines(y=r["lane"])`로 문자열 y값 사용. 환경에 따라 범주형 축 미해석 오류 가능. `yidx` 계산(`:73,:123`) 후 미사용.
  - 개선 방향과 실험: y좌표를 `yidx`(정수)로 변경하고 yticks/labels에 `lane` 매핑. 샘플 CSV로 before/after 렌더링 비교 및 예외 발생 여부 확인.
  - 평가 결과: (작성 예정)

- [ ] 6. PRD §3 CSV 스키마 자동 검증 도구 추가
  - 문제 상황: 수동 점검은 누락/회귀 위험. 현재 YAML 전용 검사(`tools/check_op_specs.py`)만 존재, CSV 스키마 검증 부재.
  - 개선 방향과 실험: `tools/validate_required_outputs.py`(신규)로 각 CSV 필수 컬럼 존재/타입/범위(input_time∈[0,1]) 검사. `out/` 샘플에 대해 통과/실패 케이스 유닛 테스트 추가.
  - 평가 결과: (작성 예정)
  
- [ ] 7. operation_timeline op_state 오류
  - 문제 상황: operation_timeline*.csv 결과물에 op_state 값이 실제 phase_conditional 에 쓰였던 key 값이 아닌 operation 예약이 끝난 후의 값이 저장됨
  - 개선 방향과 실험:
    - 원인 분석: `main.py:153`의 `export_operation_timeline`에서 `rm.op_state(die, plane, start)`(참고: `main.py:160`)로 조회하여, 해당 오퍼레이션의 첫 상태로 덮인 값이 기록됨. 실제 `phase_conditional` 키는 제안 시점(now) 기준 상태이므로 불일치가 발생.
    - 안 A(최소 변경, 권장): 시작 직전 시점에서 상태를 조회해 제안 시점 키와 정합성을 맞춘다.
      - `t_pre = quantize(start - max(SIM_RES_US, 1e-3))`를 계산하고, `rm.op_state(die, plane, t_pre)` 결과를 우선 사용한다. 없으면 기존 로직(`start` 시점)으로 폴백, 최종 None이면 "NONE".
      - 근거: 상태 타임라인은 예약 커밋 시점에 `start`부터 새 세그먼트를 추가(`resourcemgr.py`의 `_StateTimeline.reserve_op`). `start-ε` 시점 조회는 직전 상태를 안정적으로 반환한다. `SIM_RES_US`는 `resourcemgr.py:5`에 정의.
    - 안 B(보다 정확, 인터페이스 변경): 제안 시 사용된 phase 키를 기록/전파한다.
      - `proposer.propose`가 산출한 키를 배치/레코드에 포함 → `Scheduler._propose_and_schedule`의 `resv_records`에 보존 → `InstrumentedScheduler._emit_op_events`에서 row에 `phase_key`로 기록 → export에서 해당 값을 우선 사용.
      - 장점: 제안 시점과 시작 시점 차이가 큰 경우에도 정확. 단점: 모듈 간 인터페이스 변경 범위가 큼.
    - 검증 실험 설계:
      - 설정: `--pc-demo mix`, `--seed 42`, `--run-until 20000`, `--num-runs 1` 고정. 동일 설정으로 안 A 적용 전/후 실행.
      - 기준: `operation_timeline_*.csv`의 `op_state`가 `proposer_debug_*.log`에 기록된 `phase_conditional` 키(제안 시 사용)와 95% 이상 일치. 불일치 사례는 모두 `start`와 `now` 차이가 큰 예약에서 발생해야 함.
      - 회귀 확인: `op_state_timeline_*.csv`(참고: `main.py:185` 이후)와 기타 CSV 스키마/열 순서 불변. `viz_required_outputs.py` 렌더링 정상.
  - 구현 상태: 안 B 적용 완료 (`scheduler.py:203,236`, `main.py:86,103,156`).
  - 초기 평가 결과: 단일 샘플 실행에서 `operation_timeline_*.csv`의 `op_state`가 `proposer_debug_*.log`의 `phase_key`와 일치 확인(DEFAULT). 추가 샘플에서 통계적 검증 필요.
  
- [x] 8. op_state_name_input_time_count op_state 오류
  - 문제 상황: op_state_name_input_time_count*.csv 결과물에 op_state 값이 실제 phase_conditional 에 쓰였던 key 값이 아닌 operation 예약이 끝난 후의 값이 저장됨
  - 개선 방향과 실험:
    - 원인 분석: `main.py:253`의 `export_op_state_name_input_time_count`가 `rows[*].start_us` 시점의 RM 세그먼트에서 `base.state`를 도출하여 사용. 이는 예약 커밋 이후의 상태이며, 제안 시점 phase 키와 불일치 가능.
    - 해결(안 B 정합): `_OpRow.phase_key`를 사용해 `op_state` 값을 기록하고, 세그먼트 인덱스는 그대로 유지하여 `input_time`은 해당 세그먼트 상대 진행도로 계산(제안 시각 미보존 상황에서의 합리적 근사).
      - 구현 포인트: `main.py:279` 근처에서 `op_state_fk = r.get("phase_key") or f"{base}.{state}"` 로 키 선정 후 집계 키로 사용.
      - 대안(확장): 제안 시각(`propose_now_us`)을 함께 전파하여 `input_time`도 제안 시점 기준으로 계산. 인터페이스 변경이 커서 추후 과제로 분리.
    - 검증: 옵션/시드 고정 후 before/after 비교. `op_state_name_input_time_count_*.csv`의 `op_state`가 proposer `phase_key`와 고빈도 일치하는지 확인.
  - 평가 결과:
    - 재현: `python main.py -t 200 -n 1 --seed 42 --out-dir out`
    - 확인: `out/op_state_name_input_time_count_*.csv`의 `op_state`가 proposer 로그(`out/proposer_debug_*.log`의 `phase_key`)와 일치함(`DEFAULT` 케이스 확인). 추가 샘플에서 고빈도 일치 예상.
    - 결론: Option B 반영으로 op_state 필드가 제안 시점 phase 키를 반영하도록 수정됨. 분포 집계의 기준축 일관성 확보.
- [x] 9. op_uid 로 output csv 파일 정렬 필요
  - 문제 상황: op_state_timeline*.csv 과 operation_timeline*.csv 파일을 op_uid 를 기준으로 정렬 필요
  - 개선 방향과 실험:
    - operation_timeline: `op_uid` 1차, `start` 2차 정렬로 변경(`main.py:185` 인근).
    - op_state_timeline: `_OpRow`를 이용해 세그먼트 시작 시점을 포함하는 `op_uid`를 유도 정렬키로 사용(출력 컬럼 추가 없이 내부 정렬만 변경). 행 충돌 시 `start`/lane 순 안정 정렬.
    - 검증: 동일 실행에서 두 CSV가 op_uid 증가 순으로 정렬되는지 확인. 사양 컬럼/순서/의미 불변.
  - 평가 결과: (작성 예정)

- [x] 12. suspend_state 에 suspend 상태가 아닐때 RESUME 제한하기.
  - 문제 상황: SUSPEND operation 이 수행되기 전에는 RESUME operation 이 금지되어야 함에도 propose 되어 스케쥴에 등록되는 상황 발생
  - 개선 방향과 실험: 현재는 ERASE_SUSPEND 와 PROGRAM_SUSPEND 를 동일한 state 의 값에다 관리하는데, 따로 state 를 분리하고, exclusions_by_suspend_state 의 key 값에 NOT_ERASE_SUSPENDED, NOT_PROGRAM_SUSPENDED 를 등록해서 SUSPEND 수행되기 전 상태에서 RESUME operation 제한. NESTED_SUSPEND 삭제
  - 평가 결과:
  
- [x] 13. output csv 에서 phase_key_used, phase_key_virtual filed 추가
  - 문제 상황: phase_proposal_counts*.csv 에서 사용한 phase_key_used, phase_key_virtual 필드를 op_state_name_input_time_count*.csv, operation_timeline*.csv 에도 노출 필요
  - 개선 방향과 실험:
    - 구현: `main.py` 수정
      - operation_timeline: 각 row에 `phase_key_used`(제안 시 사용 키), `phase_key_virtual`(RM의 가상 키) 추가. 제안 컨텍스트(`phase_hook_die/plane`, `phase_key_time`) 존재 시 이를 사용해 virtual 계산, 부재 시 (die,plane,start_us)로 대체. 정렬/기존 컬럼 불변.
      - op_state_name_input_time_count: 집계 키를 `(phase_key_used, phase_key_virtual, op_name, input_time)`로 확장해 두 키를 컬럼으로 추가. 하위호환을 위해 `op_state`는 `phase_key_used`와 동일 값으로 유지.
    - 확인: 동일 설정에서 CSV 생성 후 각 파일에 컬럼 존재 확인 및 값 샘플 검토. `phase_proposal_counts*.csv`와의 used/virtual 일관성 확인.
  - 평가 결과: (작성 예정)

- [ ] 14. phase_conditiona_overrides 에 입력한 값이 최종값이 되도록 강제하기
  - 문제 상황: `config.yaml`->phase_conditional_overrides->global 에서 입력한 값들이 최종값이 되지 않음. 현재는 phase_conditional_overrides->특정state 에 입력한 값은 최종값이 됨
  - 개선 방향과 실험: override 순서는 global->특정state 임. normalize 는 override 한 값들의 합(=sum_overrides)이 1이 되는지 검사한 후, 1 이상이면 override 한 값들끼리만 normalize 하고, override 대상이 아닌 나머지 operation 모두 제외. 1 보다 작으면, override 대상이 아닌 나머지 operation 의 확률값들의 합(=sum_others)이 1-sum_overrides 되게 normalize 를 한다.
  - 평가 결과:
  
- [ ] 15. op_state_timeline 의 op_name field 이름을 op_base 로 고치기
  - 문제 상황: op_state_timeline*.csv 파일에서 op_name 이라고 표기된 field 가 실제 의미적으로는 op_base 여서, 정합성을 맞출 필요가 있음.
  - 개선 방향과 실험: 기본 코드, csv 출력 field, 시각화 코드 포함해서 모두 고치기
  - 평가 결과:

- [ ] 16. RESET/RSET_ADD 에 대한 state 값 reset
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:
  
- [ ] 17. operation chain 의 후속 operation 만들 시, 이전 operation 의 multi 조건을 제대로 상속받지 못함
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] 18. multi-plane 동작 예약 시 PHASE_HOOK 이 targets 갯수만큼 만드는 문제.
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] 19. operation 생성이 중간에 멈추는 현상 개선 (state 문제. commit 할 때 무언가 빠진 것 같음)
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:

- [x] 20. num_runs 를 2 이상 할때 두번째 operation 부터 생성이 안되는 현상
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:
   
- [ ] 21. read page 랑 dout page 가 다른 현상
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:
   
- [ ] 22. cache read 경우 program 안 된 상태에서 read 동작 실행하고 dout 도 page 가 하나 증가 안되는 현상 발생
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] 23. operation sequence 사전 검사 항목 만들기
  - 문제 상황: 생성된 sequence 가 epr dependency rule 에 맞지 않는 항목 전재함
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] 24. suspend-resume OP_END 중복 이벤트 분석 확장
  - 문제 상황: CORE_BUSY 중 SUSPEND→RESUME 반복 시 동일 작업의 OP_END 이벤트가 누적 큐잉되어 AddressManager 페이지가 0→2로 증가하는 사례가 발생하며, 이벤트 payload에 안정적인 작업 식별자가 없어 범위 확인이 어렵다 (`research/2025-09-17_02-14-45_suspend_resume_op_end_requeue.md`).
  - 개선 방향과 실험: Scheduler 이벤트 payload에 `op_uid` 등 식별자 부여 방안 비교, ERASE 멀티플레인 시뮬레이션으로 중복 여부 재현, OP_END 핸들러에서 중복 무시 시 다른 훅/메트릭 영향 분석; 각 안에 대한 로깅·검증 절차 정의.
  - 평가 결과: (작성 예정)

- [ ] 25. SR/SR_ADD 의 payload 에 suspend 상태 값 넘기기
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] 26. SUSPEND, RESUME 반복 시나리오에서 PROGRAM target(die, block) 내 page address 가 0→1→2 순서로 증가하지 않고 RESUME 된 PROGRAM 이 끝나기도 전에 다음 PROGRAM 이 예약되는 문제가 있다.
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:

- [x] 27. _update_overlay_for_reserved 을 모든 PROGRAM 동작에 대해서 업데이트 하게 변경하기
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:


- [ ] 28. SUSPEND 시 예약돼있던 resource, state 전부 자르거나 해제하기
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] 29. operation 별 latch 구분하기.
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] 30. suspend, resume 반복 시나리오에서 op_state_probs.yaml 에서 정의되지 않은 copyback_read 의 뜬금없는 등장 원인 파악 및 개선
  - 문제 상황: 
  - 개선 방향과 실험:
  - 평가 결과:




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
