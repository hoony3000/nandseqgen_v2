# NANDSEQGEN Codebase Restructuring for Extensibility and Validation

## 프로젝트 목표와 현 상태

`nandseqgen_v2`는 NAND 플래시의 프로그램/읽기/삭제 작업에 대한 현실적인
시퀀스(trace)를 생성하고 자원 충돌과 타이밍 제약을 반영한 스케줄링, 일시
중단/재개 등을 관리하는 툴입니다. `docs/RESTRUCTURING.md`를 보면
operation은 **base, multi, scope, affect_state, instant_resv, celltype**
등의 속성을 가지고 있으며, 각 operation마다 **state 변화 시퀀스**와
**payload** 정의가
포함됩니다[\[1\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L15-L23).
또한 문서는 operation 수행 중에 **스케줄링, 자원 점유, schema, payload,
execution & duration, suspend/resume, reset, exclusivity, dependency,
chaining, multi‑plane, probabilistic scheduling** 등 많은 동작 특성이
있음을
강조합니다[\[2\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L44-L56).
자원은 bus, block, celltype, busy, cache, suspend, odt, latch 상태 등을
포함하며, **multi-level 구조**와 각 상태의 제한 사항이 설명되어
있습니다[\[3\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L58-L67).

현재 구조는 operation 정의와 resource 변화를 하드코딩한 파이썬
클래스/딕셔너리 구조에 묶어 두었고, operation 추가/수정/삭제 시 resource
관리 로직과 validator를 손으로 업데이트해야 합니다. 이는 기능
추가·테스트가 어려워지는 원인이며, 구조적 개선이 필요합니다.

## 역할이 다른 가상의 전문가 선정

  -----------------------------------------------------------------------
  전문가                  역할/관점               주요 질문
  ----------------------- ----------------------- -----------------------
  **1. NAND 펌웨어        NAND 동작과 자원 모델,  operation 속성과 state
  엔지니어 (현 구조       스케줄링 정책 전문가.   간의 매핑, 동기·비동기
  분석)**                                         동작,
                                                  suspend/resume/ODT 등
                                                  특성의 파급효과는? 어떤
                                                  정보가 확장·추상화되면
                                                  검증이 쉬워지는가?

  **2. 소프트웨어         모듈성·확장성을         operation 정의를
  아키텍트 (플러그인      중시하는 설계 전문가.   플러그인 형태로
  아키텍처)**                                     분리하는 방법,
                                                  스케줄러와 데이터
                                                  모델을 decouple 하는
                                                  패턴? Python 생태계에서
                                                  추천되는
                                                  플러그인·스케줄링
                                                  패키지는?

  **3. 데이터 모델/검증   Pydantic 등으로         operation 정의와 생성된
  전문가 (스키마와        JSON/YAML 스키마        sequence의 검증을
  검증)**                 정의·검증하는 데 능숙.  자동화할 수 있는
                                                  라이브러리와 접근법?
                                                  JSON-Schema vs
                                                  Pydantic? state 검증
                                                  규칙을 어떻게
                                                  모델링하면
                                                  유연해지는가?
  -----------------------------------------------------------------------

## 전문가 인터뷰 요약

### 1. NAND 펌웨어 엔지니어

-   **Q: Operation과 state 관리의 핵심 문제점은 무엇인가?**\
    *A:* 현 구조에선 각 operation의 state 점유/해제 정보가 코드 곳곳에
    퍼져 있어 추가/수정 때마다 여러 파일을 수정해야 합니다. 예컨대
    `suspend_state`가 바뀌면 latch/busy/cache 상태와의 상호작용을 모두
    고려해야
    합니다[\[4\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L65-L67).
    operation 간 확률적 선택과 chaining, dependency 등도 규칙이 여러
    위치에 흩어져 있어 테스트가
    어렵습니다[\[2\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L44-L56).

-   **Q: 운영 관점에서 개선해야 할 구조는?**\
    *A:* 각 operation을 **자체 모듈/설명자**로 분리해, `base`, `scope`,
    `states`, `payload`, `dependencies` 등을 데이터로 표현해야 합니다.
    자원 상태 갱신(예: busy_state 변경, block_pgm_state 업데이트)은
    스케줄러에서 operation 정의에 따라 자동 처리하고,
    suspend/resume/reset 등의 공통 동작은 제어 흐름으로 분리해야 합니다.

-   **Q: validation 측면에서 필요한 요소는?**\
    *A:* generated sequence가 bus, busy, cache 등 **exclusion 규칙**을
    만족하는지, block celltype과 address inheritance가 올바른지 확인해야
    합니다[\[5\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L35-L42).
    또한 operation 실행 후 state-duration 스케줄이 겹치지 않는지,
    suspend/resume 시 tail이 잘려서 resource가 leak되지 않는지 검증하는
    유닛 테스트가 필요합니다.

### 2. 소프트웨어 아키텍트

-   **Q: Operation 정의를 플러그인 형태로 관리하는 방법은?**\
    *A:* **플러그인 아키텍처**는 코어 애플리케이션과 독립적으로 기능을
    추가·제거할 수 있게 하며, 각 플러그인이 특정 기능을 캡슐화합니다.
    블로그에서는 플러그인 아키텍처를 *별도의 컴포넌트(plugins)*로
    로드하여 **모듈화, 재사용성, 확장성**을 확보한다고
    설명합니다[\[6\]](https://binarycoders.wordpress.com/2023/07/22/plugin-architecture-for-python/#:~:text=,application%20to%20their%20specific%20needs).
    Python에서는 표준 라이브러리 `importlib`와 `pkgutil`을 사용해 특정
    폴더의 모듈을 동적으로 로딩하거나, \[Python Packaging User
    Guide\]에서 제안하는 **네임스페이스 패키지** 또는 **entry points**를
    활용해 플러그인을 자동으로 발견할 수
    있습니다[\[7\]](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/#:~:text=Creating%20and%20discovering%20plugins%C2%B6).
    예를 들어 `myapp.plugins` 네임스페이스 아래에 `program_slc.py`,
    `erase_block.py` 같은 모듈을 배치하면 `pkgutil.iter_modules()` 로
    플러그인을 발견하고 로딩할 수
    있습니다[\[8\]](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/#:~:text=Namespace%20packages%20%20can%20be,packages%20installed%20under%20that%20namespace).

-   **Q: 스케줄러와 자원 관리 구조를 개선하려면?**\
    *A:* 현재의 스케줄러 로직을 **프레임워크화**하여 operation executor,
    resource manager, validation engine 등으로 계층화해야 합니다.
    시뮬레이션과 자원 충돌 관리에는 **SimPy** 같은 파이썬 기반 **이산
    이벤트 시뮬레이션 프레임워크**가 유용합니다. SimPy는
    프로세스(제너레이터)와 **공유 자원**을 통해 제한된 용량의 서버와
    큐잉 모델을 선언할 수
    있으며[\[9\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=Shared%20resources%20are%20another%20way,in%20order%20to%20use%20them),
    `PriorityResource` 와 `PreemptiveResource`를 통해 우선순위와 선점
    등을 모델링할 수
    있습니다[\[10\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=SimPy%20implements%20three%20resource%20types%3A).
    즉, 각 operation을 SimPy 프로세스로 구현하고 resource를 SimPy의
    `Resource`/`PriorityResource` 로 모델링하면 타이밍과 충돌을 자동으로
    처리하고 suspend/resume 를 `interrupt` 기능으로 표현할 수
    있습니다[\[11\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=Resources%20and%20interrupts%EF%83%81).

-   **Q: 이용할 수 있는 다른 패턴과 패키지?**\
    *A:*

-   **플러그인 관리 패키지:** `pluggy`(pytest에서 사용)나 `yapsy`는
    플러그인 로딩과 디스커버리를 제공한다.

-   **스케줄러/워크플로 엔진:** `apscheduler`는 실제 시간 기반 작업
    예약에 적합하지만, 이산 이벤트 시뮬레이션에는 SimPy가 더 알맞다.

-   **상태 머신:** `transitions` 패키지는 operation 실행 상태를
    명시적으로 모델링하는 유한 상태 머신을 제공하여 suspend/resume/reset
    등의 상태 전이가 명확하게 된다.

### 3. 데이터 모델/검증 전문가

-   **Q: Operation 스키마와 sequence 검증을 어떻게 모델링하면 좋은가?**\
    *A:* 하드코딩된 딕셔너리 대신 **정형 데이터 모델**을 사용하면 확장과
    검증이 용이하다. `Pydantic` 은 타입 힌트를 활용해 데이터 스키마를
    정의하고 자동으로 파싱·검증하는 라이브러리로, 정수 확인부터 깊이
    중첩된 dict 구조 검증까지 적은 코드로 수행할 수
    있습니다[\[12\]](https://realpython.com/python-pydantic/#:~:text=Pydantic%20is%20a%20powerful%20data,scenario%20with%20minimal%20boilerplate%20code).
    또한 Pydantic은 JSON 직렬화/역직렬화 및 빠른 속도를
    제공하며[\[13\]](https://realpython.com/python-pydantic/#:~:text=,serialize%20nearly%20any%20Python%20object),
    유효성 검사 실패 시 명확한 오류 메시지를 생성한다. operation 정의를
    Pydantic `BaseModel`로 표현하고, state 변경과 address 의존성,
    exclusion 규칙 등은 **커스텀 validator**에서 구현할 수 있습니다.
    Pydantic 모델은 JSON Schema를 자동으로 생성할 수 있어 문서화에도
    도움이 됩니다.

-   **Q: sequence 검증을 자동화하려면?**\
    *A:* 생성된 sequence를 리스트로 받아 `Validator` 클래스에서 각
    스텝을 순회하면서 자원 상태 머신을 시뮬레이션한다. `transitions`
    패키지의 상태 머신과 함께 사용하면 상태 전이가 유효한지 자동으로
    확인할 수 있다. 또한 Pydantic의 `@validate_call` 데코레이터를
    사용하면 함수 호출 시 인자 검증을 실시할 수 있어, 스케줄러와 리소스
    매니저에 잘못된 데이터가 유입되는 것을 방지할 수
    있습니다[\[14\]](https://realpython.com/python-pydantic/#:~:text=,settings).

## 종합 권장사항

### 1. Operation 정의의 데이터화 및 플러그인 아키텍처 도입

1.  **Operation 정의를 YAML/JSON 등 외부 데이터로 추출**하고,
    `BaseOperation` 추상 클래스와 Pydantic 모델로 표현합니다. 예를 들어
    operation 속성(`base`, `scope`, `states`, `payload`, `dependencies`,
    `probabilities`)을 `OperationModel(BaseModel)`로 정의하면 타입과
    필수 항목을 자동으로 검사할 수 있습니다.
2.  각 operation을 **플러그인**으로 등록합니다. 추천 방법은
    `myapp.plugins` 네임스페이스를 만들어 각 operation 모듈을 배치하고,
    `pkgutil.iter_modules()` 또는 `importlib.metadata.entry_points()`를
    통해 플러그인을 자동으로 발견·등록하는
    것입니다[\[8\]](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/#:~:text=Namespace%20packages%20%20can%20be,packages%20installed%20under%20that%20namespace).
    operation 추가/수정 시 플러그인 모듈만 작성하면 되므로 유지 관리가
    수월해집니다.
3.  operation 모듈 안에서는 Pydantic 모델과 실행 로직을 함께 정의할 수
    있습니다. 이를테면 `ProgramSLCPlugin`은 `OperationModel`
    서브클래스로 속성을 정의하고, 실행 시 resource manager에 필요한
    예약을 요청합니다.

### 2. 자원 및 스케줄링 프레임워크 개선

1.  **자원(state) 관리**를 독립된 클래스(`ResourceManager`)로
    분리합니다. 각 상태(busy, block_pgm, latch 등)는 객체로 모델링하고
    상태 간 exclusion/dependency 규칙을 테이블이나 함수로 표현합니다.
    `transitions` 패키지로 유한 상태 머신을 정의하면
    suspend/resume/reset 등의 전이가 명확해집니다.
2.  **SimPy 기반의 이산 이벤트 시뮬레이터**를 도입합니다. 각 operation은
    SimPy 프로세스로 구현하고, bus/die/plane 등 자원을 SimPy `Resource`
    또는 `PriorityResource`로 모델링하면 자원 점유와 대기열 관리가
    자연스럽게
    됩니다[\[9\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=Shared%20resources%20are%20another%20way,in%20order%20to%20use%20them)[\[10\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=SimPy%20implements%20three%20resource%20types%3A).
    suspend/resume 는 `interrupt` 메커니즘으로 처리할 수
    있습니다[\[11\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=Resources%20and%20interrupts%EF%83%81).
    이 방식은 추후 timing 모델을 바꾸거나 새로운 operation을 추가할 때
    유연합니다.
3.  **스케줄러를 정책 기반으로 분리**합니다. priority queue, FIFO,
    weighted random 등 스케줄링 정책을 전략 패턴으로 구현하여 operation
    플러그인이 스케줄러에 등록할 수 있도록 합니다. 확률적 예약 규칙은
    operation 정의에서 가중치로 지정하고, 스케줄러는 현재 자원 상태에
    따라 가능한 operation 리스트를 만들고, 확률적으로 선택하도록
    구현합니다.

### 3. 시퀀스 검증 자동화

1.  **SequenceValidator** 클래스를 만들고, operation 실행 결과와 자원
    상태의 타임라인을 입력받아 여러 검증 규칙을 적용합니다. 검증
    규칙으로는 `bus_exclusion`, `busy_exclusion`, `multi_exclusion`,
    `latch_exclusion`, `suspend_exclusion`, `odt_exclusion`,
    `cache_exclusion`, `addr_dependency` 등이
    있으며[\[5\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L35-L42),
    각 규칙을 함수로 분리해 가독성을 높입니다.
2.  validator 내부에서 Pydantic 모델을 활용해 입력 데이터 타입을
    검증하고, `transitions` 패키지와 resource manager를 이용해
    sequence를 시뮬레이션합니다. 검증 실패 시 상세한 오류 메시지를
    제공하여 테스트와 디버깅을 쉽게 합니다.
3.  시퀀스 검증 로직을 unit test와 integration test로 분리하여,
    operation 개발자들이 새 operation을 만들 때 자동으로 검증할 수 있게
    합니다.

### 4. 추천 Python 패키지 요약

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  패키지/프레임워크      역할                                                                                                                                                                                                                                                                                                      참고/장점
  ---------------------- --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- --------------
  **Pydantic**           Operation 정의와 sequence 입력을 **타입 안전한 모델**로 표현하고 검증. 커스텀 validator를 통해 state dependency 및 제약을 검사할 수                                                                                                                                                                       빠른 성능과
                         있음[\[12\]](https://realpython.com/python-pydantic/#:~:text=Pydantic%20is%20a%20powerful%20data,scenario%20with%20minimal%20boilerplate%20code)[\[13\]](https://realpython.com/python-pydantic/#:~:text=,serialize%20nearly%20any%20Python%20object).                                                    JSON Schema
                                                                                                                                                                                                                                                                                                                                   지원, 널리
                                                                                                                                                                                                                                                                                                                                   사용됨.

  **Pluggy /             플러그인 로딩 및 entry point 관리. operation 모듈을 플러그인으로 등록하고 자동으로 탐색·로드.                                                                                                                                                                                                             모듈식 확장성
  importlib.metadata**                                                                                                                                                                                                                                                                                                             및 동적 로딩을
                                                                                                                                                                                                                                                                                                                                   제공.

  **SimPy**              이산 이벤트 시뮬레이션으로 스케줄링과 자원 점유를 모델링. `Resource`, `PriorityResource`, `PreemptiveResource` 등으로 제한된 자원과 선점/우선순위 처리를                                                                                                                                                  직관적이고
                         지원[\[9\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=Shared%20resources%20are%20another%20way,in%20order%20to%20use%20them)[\[10\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=SimPy%20implements%20three%20resource%20types%3A).   성능이 좋으며
                         `interrupt`를 사용해 suspend/resume를 구현할 수 있음[\[11\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=Resources%20and%20interrupts%EF%83%81).                                                                                                                         자원 충돌
                                                                                                                                                                                                                                                                                                                                   테스트에 적합.

  **transitions**        FSM(유한 상태 머신) 구현으로 operation 실행 상태(suspended/busy/ready 등)와 resource state 변화를 명확하게 표현.                                                                                                                                                                                          상태 전이를
                                                                                                                                                                                                                                                                                                                                   코드로 선언해
                                                                                                                                                                                                                                                                                                                                   가독성과
                                                                                                                                                                                                                                                                                                                                   검증성 향상.

  **PyYAML / JSON5**     Operation 정의를 YAML/JSON 파일에 저장하고 읽기.                                                                                                                                                                                                                                                          사람이 읽기
                                                                                                                                                                                                                                                                                                                                   쉬운 구문,
                                                                                                                                                                                                                                                                                                                                   버전 관리
                                                                                                                                                                                                                                                                                                                                   용이.

  **pytest +             단위 테스트와 property 기반 테스트로 operation 규칙과 sequence 검증 로직을 자동화.                                                                                                                                                                                                                        랜덤
  hypothesis**                                                                                                                                                                                                                                                                                                                     시나리오에서
                                                                                                                                                                                                                                                                                                                                   제약 위반을
                                                                                                                                                                                                                                                                                                                                   찾아내기 좋음.
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

## 결론

`nandseqgen_v2`의 재구조화를 통해 operation 추가·수정·제거에 따른 자원
관리와 sequence 검증을 용이하게 할 수 있습니다. 핵심은 **operation
정의를 데이터 모델과 플러그인 모듈로 분리**하고, **SimPy 기반의 이산
이벤트 시뮬레이션과 상태 머신**으로 자원 점유와 타임라인을 모델링하며,
**Pydantic**과 **transitions**를 통해 검증 로직을 강화하는 것입니다.
이러한 구조는 유지 관리와 확장성을 크게 향상시키며, 새로운 operation을
추가할 때 기존 스케줄러나 자원 관리 코드를 거의 수정하지 않아도 됩니다.
향후엔 GPU/모바일 NAND 특성 등 새로운 리소스 타입이 추가되더라도 해당
리소스 클래스와 플러그인만 작성하면 시스템 전체가 자동으로 동작하도록
설계할 수 있습니다.

[\[1\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L15-L23)
[\[2\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L44-L56)
[\[3\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L58-L67)
[\[4\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L65-L67)
[\[5\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L35-L42)
RESTRUCTURING.md

<https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md>

[\[6\]](https://binarycoders.wordpress.com/2023/07/22/plugin-architecture-for-python/#:~:text=,application%20to%20their%20specific%20needs)
Plugin Architecture for Python -- Binary Coders

<https://binarycoders.wordpress.com/2023/07/22/plugin-architecture-for-python/>

[\[7\]](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/#:~:text=Creating%20and%20discovering%20plugins%C2%B6)
[\[8\]](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/#:~:text=Namespace%20packages%20%20can%20be,packages%20installed%20under%20that%20namespace)
Creating and discovering plugins - Python Packaging User Guide

<https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/>

[\[9\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=Shared%20resources%20are%20another%20way,in%20order%20to%20use%20them)
[\[10\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=SimPy%20implements%20three%20resource%20types%3A)
[\[11\]](https://simpy.readthedocs.io/en/latest/topical_guides/resources.html#:~:text=Resources%20and%20interrupts%EF%83%81)
Shared Resources --- SimPy 4.1.2.dev8+g81c7218 documentation

<https://simpy.readthedocs.io/en/latest/topical_guides/resources.html>

[\[12\]](https://realpython.com/python-pydantic/#:~:text=Pydantic%20is%20a%20powerful%20data,scenario%20with%20minimal%20boilerplate%20code)
[\[13\]](https://realpython.com/python-pydantic/#:~:text=,serialize%20nearly%20any%20Python%20object)
[\[14\]](https://realpython.com/python-pydantic/#:~:text=,settings)
Pydantic: Simplifying Data Validation in Python -- Real Python

<https://realpython.com/python-pydantic/>
