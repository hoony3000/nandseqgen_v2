# TODO / Bug & Experiment Tracker

목표: 버그와 개선 작업을 일관된 포맷으로 관리하고, 실험과 평가까지 한 흐름으로 기록합니다.

- 작성 원칙: 간결하게, 재현 가능한 정보 우선, 정량적 평가 권장
- 사용 방법: 아래 템플릿을 복사해 항목을 추가하세요(우선순위 섹션 권장). 완료되면 checkbox 빈칸을 x 로 채우세요.

## 템플릿

```
- [ ] <Ticket Name>
  - 문제 상황: <현상 요약 + 재현 절차/조건>
  - 개선 방향과 실험: <가설, 변경안, 실험 설계(데이터/파라미터/절차)>
  - 평가 결과: <정량 지표/로그/스크린샷 경로 + 결론>
```

---

## Items

<!-- 여기에 가장 시급한 이슈들을 추가하세요 -->

- [ ] B01
  - 문제 상황: `viz_required_outputs.py:84,134`에서 `hlines(y=r["lane"])`로 문자열 y값 사용. 환경에 따라 범주형 축 미해석 오류 가능. `yidx` 계산(`:73,:123`) 후 미사용.
  - 개선 방향과 실험: y좌표를 `yidx`(정수)로 변경하고 yticks/labels에 `lane` 매핑. 샘플 CSV로 before/after 렌더링 비교 및 예외 발생 여부 확인.
  - 평가 결과: (작성 예정)

- [ ] B02
  - 문제 상황: RESET/RSET_ADD 에 대한 state 값 reset
  - 개선 방향과 실험:
  - 평가 결과:
  
- [ ] B03
  - 문제 상황: operation chain 의 후속 operation 만들 시, 이전 operation 의 multi 조건을 제대로 상속받지 못함
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] B04
  - 문제 상황: multi-plane 동작 예약 시 PHASE_HOOK 이 targets 갯수만큼 만드는 문제.
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] B05
  - 문제 상황: cache read 경우 program 안 된 상태에서 read 동작 실행하고 dout 도 page 가 하나 증가 안되는 현상 발생
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] B06
  - 문제 상황: 생성된 sequence 가 epr dependency rule 에 맞지 않는 항목 전재함
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] B07
  - 문제 상황: SR/SR_ADD 의 payload 에 suspend 상태 값 넘기기
  - 개선 방향과 실험:
  - 평가 결과:

- [x] B08
  - 문제 상황: SUSPEND, RESUME 반복 시나리오에서 PROGRAM target(die, block) 내 page address 가 0→1→2 순서로 증가하지 않고 RESUME 된 PROGRAM 이 끝나기도 전에 다음 PROGRAM 이 예약되는 문제가 있다.
  - 개선 방향과 실험:
  - 평가 결과:

- [x] B10
  - 문제 상황: suspend 이후 suspended_ops 의 remaining_us 가 계산이 잘됐는지, resume 시에 올바르게 스케쥴에 반영되는지. 즉, resume 동작 busy 가 끝난 후, suspended_ops 가 이어서 예약되는지 확인.
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] B11
  - 문제 상황: ONESHOT_PROGRAM_MSB_23H SUSPEND->RESUME 반복 시나리오에서, SUSPEND 가 항상 ONESHOT_PROGRAM_MSB_23H OP_END 직후에 예약되는 현상
  - 개선 방향과 실험:
  - 평가 결과:

- [ ] B12
  - 문제 상황: OP_START, OP_END 에 exclusions_by_latch 적용 하기
  - 개선 방향과 실험:
  - 평가 결과:

## 작업 기록 팁

- 재현 정보: 입력 데이터 경로, 시드/난수 고정, 실행 명령(옵션 포함)
- 실험 설계: 단일 변수 변경 원칙(가능하면), 통제군/실험군 구분
- 평가 기준: 통일된 지표 이름과 단위 사용, 허용 오차 명시
- 아티팩트: 결과물 경로를 남기고, 큰 파일은 저장소 외부(예: `out/`)에 두기
