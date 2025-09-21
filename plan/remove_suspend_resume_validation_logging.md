# Suspend/Resume Validation Logging Removal

## Problem 1-Pager
- **배경**: Scheduler/ResourceManager에는 `validation.suspend_resume_op_end` 플래그를 기준으로 SUSPEND/RESUME 흐름을 JSONL 로 기록하는 계측 코드가 포함되어 있다. 연구 목적으로 도입되었으며 기본 구동 시점에는 꺼져 있지만, 코드가 남아 있어 유지 보수 복잡도가 높다.
- **문제**: suspend/resume 실행 중 validation 데이터를 출력하는 로직을 완전히 제거해 달라는 요구가 들어왔으며, 현재 구현은 환경 변수/설정에 따라 재활성화될 가능성이 있다. 또한 불필요한 파일 입출력과 메트릭 분기 코드가 계속해서 경로를 오염시킨다.
- **목표**: suspend/resume 관련 validation 데이터(이벤트, remaining_us 로그 등)를 기록하거나 노출하는 코드를 Scheduler/ResourceManager에서 제거해 더 이상 파일이 생성되지 않도록 한다.
- **비목표**: suspend/resume 자체 동작, core-busy stub 체인 로직, 다른 validation/diagnostic 경로(예: 일반 이벤트 로깅, metrics) 변경은 다루지 않는다.
- **제약**: 기존 기능을 깨뜨리지 않아야 하고, 함수·파일 길이 제한을 준수하며 테스트(있다면) 정상 동작을 보장해야 한다. config 대형 파일은 가능하면 수정 없이 유지한다.

## 대안 비교
1. **구성/환경 플래그만 끄기 유지**  
   - 장점: 코드 변경이 거의 없이 요구를 충족하는 것처럼 보인다.  
   - 단점: 계측 코드가 그대로 남아 복잡도가 유지되고, ENV/CFG 로 쉽게 다시 켜질 수 있다.  
   - 위험: 미래 배포나 스크립트가 실수로 플래그를 켜면 로그가 재등장한다.
2. **관련 계측 코드 완전 제거** *(선택)*  
   - 장점: 로그 경로가 사라져 요구를 확실히 충족하고 유지 보수가 쉬워진다.  
   - 단점: 제거 범위가 넓어 레거시 테스트/도구가 숨은 의존성을 갖고 있으면 수정 필요.  
   - 위험: metrics 키나 함수 제거로 인해 다른 코드가 AttributeError 를 낼 수 있으므로 영향 분석 필요.

> **선택 사유**: 요구 사항이 “출력을 모두 제거”하는 것이므로 대안 2를 택한다. 제거 전에 global 검색으로 의존성을 확인하고, 필요 시 대체 경로를 마련한다.

## 구현 계획
1. **Scheduler 정리**: suspend_resume validation 설정/메트릭 초기화, `_validation_*` 헬퍼, `_op_uid_seq` 사용 지점, 관련 호출을 제거하고 필요 시 단순화한다.
2. **ResourceManager 정리**: `_SuspendResumeRemainingLogger` 클래스와 `_suspend_resume_logger` 필드 사용(`move_to_suspended_axis`, `record_resume_stub*`)을 삭제하고 호출부를 정리한다.
3. **사용 흔적 점검**: 남은 코드/문서/테스트에서 삭제된 메트릭이나 클래스에 의존하는 부분이 있는지 확인하고 필요 시 수정 또는 제거한다.
4. **검증**: 가능한 단위 테스트/스모크(예: 기존 시뮬레이션 진입 스크립트가 있다면) 실행, 없으면 수동 코드 점검으로 회귀 위험을 평가한다.
