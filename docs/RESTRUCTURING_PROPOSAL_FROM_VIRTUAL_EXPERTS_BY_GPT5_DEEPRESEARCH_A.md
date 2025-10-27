# nandseqgen_v2 구조 및 문제점

## 코드베이스의 개략적 역할

`nandseqgen_v2` 프로젝트는 **NAND 플래시 메모리의 동작 시퀀스를
생성**하고, **스케줄러**를 통해 자원을 관리하며, 생성된 시퀀스가 규칙을
위반하지 않는지 확인하는 시뮬레이션을 수행한다. 주요 구성은 다음과 같다.

  ------------------------------------------------------------------------------------------------------------------------------------------
  모듈/파일                           역할 요약
  ----------------------------------- ------------------------------------------------------------------------------------------------------
  `main.py`                           설정 파일을 로드하고 `ResourceManager`, `AddressManager` 및 스케줄러를 초기화한다. 시뮬레이션을 일정
                                      시간 동안 실행하고, 시퀀스와 상태를 CSV·JSON 형태로 출력한다.

  `scheduler.py`                      `Scheduler` 클래스는 이벤트 큐를 이용해 **operation 제안(Proposer)**을 처리하고, **자원 예약**을 위해
                                      `ResourceManager`와 협업한다. 예약 가능한 Operation을 결정하고, **backlog** 관리·중단 및 재개 로직을
                                      제공한다.

  `resourcemgr.py`                    다중 수준의 플래시 자원(plane, die, block, bus 등)을 나타내는 **상태 변수**를 관리한다. `reserve`,
                                      `commit`, `rollback` 등을 통해 스케줄러가 operation을 예약·확정·취소할 수 있도록 한다. 각 상태 변수는
                                      **bus_state**, **busy_state**, **cache_state**, **suspend_state**, **odt_state**, **latch_state**
                                      등으로 구성되어 operation 종류에 따라
                                      변화한다[\[1\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L44-L67).
                                      이는 docs/RESTRUCTURING.md에 언급된 자원과 상태에 대응한다.

  `operation` 정의 (YAML/JSON)        각 operation은 base, multi‑plane 여부, scope, sequence, 상태(duration, bus 사용 등)와 validation
                                      옵션을 가진다. 예를 들어 `PROGRAM_SLC` operation은 bus를 사용하는 ISSUE 단계와 busy 상태를
                                      표현한다[\[2\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L16-L22).
  ------------------------------------------------------------------------------------------------------------------------------------------

### docs/RESTRUCTURING.md의 핵심 내용

문서에서는 operation과 자원의 관계를 재구성하면서 **operation
추가/수정/삭제 시 발생할 수 있는 문제점**을 정리하였다. 특히:

-   operation은 **스케줄러에 의해 예약되고 수행**되며, operation마다
    **schema**(수행할 명령 및 payload), **duration**, **multi‑plane
    여부** 등이
    달라진다[\[3\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L45-L56).
-   **suspension & resumption**: suspend operation이 수행되면 진행 중인
    다른 operation을 일시 중단하고, resume operation이 수행될 때까지
    재개되지
    않는다[\[4\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L50-L53).
-   operation 간 **exclusivity**와 **dependency**, **chaining** 규칙이
    존재하여, 어떤 operation이 실행되는 동안 다른 특정 operation은
    금지되거나 반드시 이어서 수행되어야
    한다[\[5\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L52-L55).
-   NAND 자원은 **multi‑level 구조**이며 plane, die, global 등 각
    레벨에서 상태가 변경될 수
    있다[\[6\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L58-L60).
    bus_state, busy_state, cache_state, suspend_state, odt_state,
    latch_state 등 다양한 상태가 있어 operation 실행 시 동시에 만족해야
    한다[\[7\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L60-L67).
-   문서 후반의 validation 목록에는 **bus_exclusion**,
    **busy_exclusion**, **multi_exclusion**, **latch_exclusion**,
    **suspend_exclusion**, **odt_exclusion**, **cache_exclusion**,
    **addr_dependency** 등이 나열되어
    있다[\[8\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L35-L42).
    이는 operation 시퀀스가 자원 충돌이나 주소 의존성을 위반하지 않도록
    검증하기 위해 사용된다.

현재 구조는 operation 추가·수정 시 **각 모듈에 분산된 자원 상태 처리
로직을 일일이 업데이트해야 한다**는 문제가 있다. 아래의 전문가 인터뷰를
통해 이러한 구조를 개선하는 방안을 모색한다.

## 전문가 인터뷰

### 전문가 선정

  -----------------------------------------------------------------------
  전문가                  분야 / 역할             관점
  ----------------------- ----------------------- -----------------------
  **A. 시뮬레이션 및 자원 물류·제조에서 이벤트    현재의 스케줄러/자원
  스케줄링 전문가**       기반 시뮬레이션과 자원  관리 코드를 디스크리트
                          스케줄링 도구 개발      이벤트 시뮬레이션
                          경험이 풍부하며,        패턴으로 재구성하고,
                          `SimPy`와 `pyschedule`  외부 라이브러리를
                          등의 파이썬 패키지를    활용해 자원 관리와 검증
                          활용해 복잡한 시스템을  로직을 단순화할 수
                          모델링한다.             있는지 논의한다.

  **B. 상태 기계 및 설계  소프트웨어 아키텍트     operation 및 자원
  패턴 전문가**           출신으로, Finite State  상태를 FSM으로
                          Machine(FSM) 패턴과     모델링하여 operation
                          Command/Mediator 패턴을 추가·수정 시 영향을
                          이용한 복잡한 동작      최소화하고, 검증 규칙을
                          관리에 능숙하며         **Guard 조건**으로
                          `transitions`           명시적으로 표현하는
                          라이브러리를 활용하여   방안을 제안한다.
                          상태 전환과 콜백을      
                          관리한다.               

  **C. 데이터 모델링 및   데이터 밸리데이션과     operation 정의와 자원
  검증 전문가**           타입 지향 설계 전문가로 상태를 타입 기반 데이터
                          `Pydantic`을 활용한     모델로 정형화하고,
                          schema 정의·검증 경험이 커스텀 validator를
                          풍부하다.               사용해 룰을 명시하여
                                                  코드 수정 시 검증
                                                  로직을 재사용할 수
                                                  있도록 한다.
  -----------------------------------------------------------------------

### 인터뷰 진행 및 주요 의견

#### 1. 시뮬레이션/스케줄링 전문가 (Expert A)

**Q1. 현재 구조에서 어떤 문제가 가장 눈에 띄는가?**

> 현재 코드에서는 `Scheduler`가 이벤트 큐를 직접 관리하고
> `ResourceManager`를 통해 자원을 예약한다. 그러나 operation의 추가·수정
> 시마다 자원 상태와 전환 규칙을 수동으로 변경해야 하며, 검증 로직이
> 여러 곳에 분산돼 있다. 이는 시뮬레이션 모델링 관점에서 **이벤트와
> 자원의 분리**가 미약한 상태이다.

**Q2. 적합한 파이썬 패키지나 패턴이 있을까?**

> `SimPy`는 디스크리트 이벤트 시뮬레이션 프레임워크로,
> 자원(`simpy.Resource`)을 정의하고 각 작업을 **프로세스**로 표현한다.
> SimPy에서 자원은 `capacity` 값을 가지며 여러 프로세스 간 제한된 자원의
> 획득/반환을 자동으로
> 처리한다[\[9\]](https://realpython.com/simpy-simulating-with-python/#:~:text=how%20many%20can%20be%20in,environment%20at%20any%20given%20time).
> `env.timeout()`으로 이벤트 발생 시간을 모델링할 수 있고,
> `with resource.request() as req: yield req` 구문으로 자원 점유와
> 대기를
> 표현한다[\[10\]](https://simpy.readthedocs.io/en/latest/simpy_intro/shared_resources.html#:~:text=,%28name%2C%20env.now).
> 이러한 방식으로 operation의 단계별 state를 SimPy process와 resource로
> 재현하면, operation 추가 시 새로운 process 및 자원만 정의하면 된다.
>
> 또한 `pyschedule` 패키지는 자원 제약이 있는 작업 스케줄을 모델링하고
> Mixed‑Integer Programming(MIP) 솔버를 이용해 일정을 계산한다. 예시는
> 세 작업(요리, 청소, 세탁)을 두 명에게 할당하는 스케줄을 간단히
> 표현하며 precedence·resource requirement·resource capacity 등의 제약을
> 지원한다[\[11\]](https://github.com/timnon/pyschedule#:~:text=pyschedule).
> `pyschedule`은 작은\~중간 규모의 스케줄링 문제에 적합하며, NAND
> operation이 많은 자원과 긴 시간축을 가진다면 MIP 솔버가 과부하될 수
> 있다. 그러나 operation 정의를 작업(task)으로, 자원 상태를 resource로
> 매핑하여 **새 operation의 resource 요구 사항을 선언적으로 표현**할 수
> 있다는 장점이 있다.

**Q3. 기존 코드에 SimPy나 pyschedule을 적용하는 방안은?**

-   **자원 추상화**: `ResourceManager`의 state 타임라인을 SimPy의
    `Resource` 또는 `Container`로 매핑하여, 각 bus, busy, cache 등
    자원을 별도의 객체로 정의한다. SimPy는 자원의 동시 사용을 제한하므로
    **bus나 latch와 같이 mutual exclusion이 필요한 자원**을 자연스럽게
    표현할 수
    있다[\[9\]](https://realpython.com/simpy-simulating-with-python/#:~:text=how%20many%20can%20be%20in,environment%20at%20any%20given%20time).
-   **operation을 프로세스로 모델링**: 각 operation은 SimPy 프로세스로
    구현되고, `env.timeout(duration)`으로 단계별 시간을 나타낸다.
    multi‑plane operation은 여러 plane 자원을 `yield env.process()`
    호출로 동시에 요청한다.
-   **검증 및 추적**: SimPy에서는 모든 이벤트가 시간순으로 실행되므로
    **operation 간 충돌 여부**를 런타임에서 모니터링할 수 있다. sequence
    validation은 프로세스가 자원을 요청할 때 자동적으로 queueing 되기
    때문에 대부분 해결되며, 독점 규칙은 자원 capacity=1 설정과 guard
    프로세스로 구현한다.
-   **pyschedule 활용**: 만약 사전에 operation 시퀀스를 생성해야 한다면,
    operation을 `Task`로, bus 등은 `Resource`로 정의하여
    `solvers.mip.solve()`로 feasible schedule을 계산하게 할 수 있다.
    precedence relations와 capacity 제한은 pyschedule에서 지원하므로,
    state 전환 룰을 제약식으로
    표현한다[\[11\]](https://github.com/timnon/pyschedule#:~:text=pyschedule).

**Q4. 문제 해결에 SimPy와 pyschedule 중 어떤 것을 추천하는가?**

> SimPy는 이벤트 기반 시뮬레이션이므로 현재 코드와 비슷한 시뮬레이션
> 환경을 유지하면서 **자원과 프로세스 관리 로직을 단순화**할 수 있다.
> 작은 스케줄을 최적화해야 한다면 pyschedule/OR‑Tools 같은 제약 만족
> 솔버도 활용할 수 있으나, NAND operation처럼 다양한 상태와 확률적
> 스케줄링을 표현하는 데는 SimPy가 더 직관적이다.

#### 2. 상태 기계/설계 패턴 전문가 (Expert B)

**Q1. 상태 기계 관점에서 기존 구조를 어떻게 개선할 수 있을까?**

> 현재 operation 정의는 여러 `states`와 `duration`, `bus` 사용 여부를
> 포함하지만, 코드상에서는 `ResourceManager`가 상태를 직접 조작한다.
> 이를 **Finite State Machine(FSM)**으로 추상화하면 각 operation과 자원
> 상태 전환을 **명시적인 State-Transition 모델**로 표현할 수 있다.
>
> 예를 들어 `PROGRAM_SLC` operation은 `ISSUE → CORE_BUSY`의 두 상태를
> 가지며, 각 전환 시 bus 사용 여부와 시간을
> 지정한다[\[2\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L16-L22).
> FSM 모델에서는 `State` 객체로 `ISSUE`, `CORE_BUSY` 등을 정의하고,
> `Transition`에 guard 조건으로 bus_exclusion, busy_exclusion 등을
> 설정한다. operation 추가 시 **새로운 상태와 전이 정의만 추가**하면
> 되므로 기존 자원 관리 코드에 영향을 덜 미친다.

**Q2. 사용할 만한 파이썬 라이브러리나 패턴은?**

-   `transitions`는 간결하고 가벼운 FSM 라이브러리로, 문자열이나 클래스
    > 기반으로 상태·전이를 정의하고 콜백을 등록할 수 있다. 이
    > 라이브러리는 **상태, 전이, 콜백 정의가 간단**하며, 전이 전후에
    > 동기/비동기 콜백을 실행할 수
    > 있다[\[12\]](https://statemachine.events/article/Top_10_State_Machine_Frameworks_for_Python.html#:~:text=Transitions%20is%20a%20lightweight%20state,sized%20projects).
    > 예를 들어 on_enter `CORE_BUSY` 콜백에서 busy_state와 cache_state를
    > 갱신하거나 예외를 발생시켜 validation을 수행할 수 있다. 비동기
    > 콜백을 사용하면 suspend/resume 로직도 깔끔하게 구현할 수 있다.

-   `python-statemachine`은 상태·이벤트를 정의하고, **조건부
    > 전이(Guard)**와 **Validator**를 사용할 수 있는 고급 FSM
    > 프레임워크이다[\[13\]](https://python-statemachine.readthedocs.io/en/latest/readme.html#:~:text=Features%C2%B6).
    > 모델과 상태 기계를 분리하는 Mixin 패턴을 제공하여, NAND 플래시
    > 모델은 도메인 데이터만 가지고, 상태 기계는 전이 로직만 담당하도록
    > 분리할 수 있다. 또한 그래프 시각화 기능을 지원하여 operation
    > 시퀀스의 전이 관계를 그림으로 확인할 수 있다.

-   **설계 패턴 적용**: FSM과 함께 **Command 패턴**으로 operation을
    > 캡슐화하고, **Mediator/Observer 패턴**으로 `ResourceManager`와
    > `Scheduler` 사이의 의존성을 낮춘다. operation 클래스를 구현하여
    > execute()를 호출하면 FSM을 통해 상태를 갱신하고,
    > `ResourceManager`는 Observer로서 자원 상태 변화를 감지해
    > 스케줄러에게 통지한다. 이를 통해 operation 추가 시 실행 로직만
    > 변경하면 되며, 자원 관리 로직은 별도 모듈로 유지할 수 있다.

**Q3. validation 규칙은 FSM에서 어떻게 표현할 수 있는가?**

> `transitions`나 `python-statemachine`의 **Guard 조건**을 사용하면
> bus_exclusion, busy_exclusion 등의 규칙을 함수로 분리해 전이 전 검증을
> 수행할 수 있다. 예를 들어 `bus_exclusion`은
> `ResourceManager.bus_state.is_available(operation)`처럼 검사하고,
> 실패하면 전이 자체를 허용하지 않는다. dependency·chaining 규칙은 FSM
> 설계 시 전이 경로로 강제할 수 있어, 임의의 순서 위반을 구조적으로
> 방지한다.

#### 3. 데이터 모델링 및 검증 전문가 (Expert C)

**Q1. operation 정의와 자원 상태를 어떻게 정형화할 수 있을까?**

> 현재 operation 정의는 YAML/JSON 파일로 자유롭게 작성되어 있으며,
> 스케줄러가 읽어들여 Python 딕셔너리로 사용한다. 이 방식은 필수 속성
> 누락이나 오타가 런타임까지 드러나지 않는다. `Pydantic`은 타입 힌트를
> 기반으로 한 데이터 검증 라이브러리로, 모델 정의와 검증을 쉽게 해준다.
> Pydantic은 매우 빠르고 확장성이 높으며, 순수 Python 3.9+ 코드로 데이터
> 구조를 정의하고 검증할 수
> 있다[\[14\]](https://docs.pydantic.dev/latest/#:~:text=Pydantic%20is%20the%20most%20widely,data%20validation%20library%20for%20Python).
>
> 모델을 정의할 때 각 필드를 타입과 함께 선언하면 Pydantic이 값을
> 자동으로 캐스팅하거나 오류를 발생시킨다. 또한 **커스텀 validator**를
> 통해 복잡한 제약을 검증할 수 있으며, JSON Schema를 자동으로 생성해
> 문서화할 수
> 있다[\[15\]](https://docs.pydantic.dev/latest/#:~:text=,validators%20and%20serializers%20to%20alter).

**Q2. 구체적인 적용 방법은?**

-   **Operation 모델 정의**: `BaseModel`을 상속하여
    `OperationDefinition` 클래스를 만들고, `base`, `multi`, `scope`,
    `sequence`, `states`, `payload` 등의 필드를 타입으로 정의한다.
    `states` 목록에 대해 반복 validator를 작성해 각 state가 bus flag와
    duration을 포함하는지, multi‑plane 규칙에 맞는지 확인한다. 필수 필드
    누락 시 Pydantic이 ValidationError를 발생시켜 개발 초기 단계에서
    오류를 발견할 수 있다.
-   **자원 상태 모델**: bus_state, busy_state, cache_state 등도
    `BaseModel`로 정의하여 상태 값과 소유 operation을 명시한다.
    `ResourceManager`는 이러한 모델의 인스턴스를 관리하면서 상태 전환 시
    Pydantic이 제공하는 `.copy(update=...)` 기능을 활용하여 불변성을
    유지할 수 있다.
-   **Validation 함수 작성**: Pydantic의 `@validator` 데코레이터를
    사용하여 bus_exclusion, busy_exclusion 등 docs에서 정의한 validation
    규칙을
    구현한다[\[8\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L35-L42).
    예를 들어 두 operation의 bus 사용 시간이 겹치지 않도록 검사하거나,
    suspend_state가 활성일 때 특정 operation을 금지한다. 이러한 함수는
    모델 내부나 별도의 service 클래스에 위치시키고, 테스트 코드로 쉽게
    검증할 수 있다.

**Q3. Pydantic의 장점은?**

-   타입 힌트를 활용한 **명세화**와 **자동 문서화**: JSON Schema를 통해
    operation 정의를 명확하게 설명할 수 있어 팀 간 소통이 쉬워진다.
-   **빠른 검증**: Pydantic의 validation 로직은 Rust로 구현된 부분이
    있어 매우 빠르며, 입력값을 파싱하고 타입을
    변환해준다[\[14\]](https://docs.pydantic.dev/latest/#:~:text=Pydantic%20is%20the%20most%20widely,data%20validation%20library%20for%20Python).
-   **커스터마이징**: 복잡한 검증 로직을 커스텀 validator로 작성할 수
    있으며, 여러 모델 간 중첩 검증도
    지원한다[\[16\]](https://docs.pydantic.dev/latest/#:~:text=,huggingface%2C%20Django%20Ninja%2C%20SQLModel%2C).

## 종합 제안

전문가들의 의견을 종합하면 다음과 같은 구조/패키지 개선안을 제시할 수
있다:

1.  **operation 정의와 자원 상태를 타입 기반 모델로 명세화(Pydantic)**

2.  `OperationDefinition`, `StateDefinition`, `ResourceState` 등의
    모델을 작성하여 schema를 명확히 한다. 필수 필드, 데이터 타입, 기본값
    등을 선언하고 커스텀 validator를 통해 docs/RESTRUCTURING.md에 있는
    **bus_exclusion, busy_exclusion** 등 규칙을
    검증한다[\[8\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L35-L42).

3.  YAML/JSON에서 operation을 로드할 때 Pydantic 모델에 매핑하여
    자동으로 검증하므로, operation 추가·수정 시 초기 단계에서 오류를
    발견할 수 있다.

4.  **Finite State Machine을 통한 상태 관리(**`transitions` **또는**
    `python-statemachine`**)**

5.  operation의 단계(ISSUE, CORE_BUSY 등)를 FSM의 **State**로 정의하고,
    다음 단계로 전환하는 **Transition**에 guard 함수를 설정한다. 예를
    들어 `on_enter_CORE_BUSY` 콜백에서 `ResourceState.busy_state`를
    업데이트하고, `bus_exclusion` guard가 실패하면 전이가 거부된다.

6.  suspend/resume, reset과 같은 제어 동작은 FSM의 이벤트(event)로
    모델링한다. FSM 라이브러리는 비동기 콜백과 상태 다이어그램 자동 생성
    등을 제공하므로, 복잡한 동작을 시각적으로 검토할 수
    있다[\[12\]](https://statemachine.events/article/Top_10_State_Machine_Frameworks_for_Python.html#:~:text=Transitions%20is%20a%20lightweight%20state,sized%20projects).

7.  Command/Mediator 패턴과 결합해 operation 실행과 자원 상태 갱신을
    분리하면 확장성이 향상된다.

8.  **SimPy 기반 이벤트 시뮬레이션으로 자원 예약과 충돌 해결 단순화**

9.  `ResourceManager`의 state 타임라인을 SimPy의 `Resource`로 추상화하여
    **bus, busy, cache, latch** 등의 독점 자원을 capacity=1로 정의한다.
    여러 plane과 die는 `Resource` 또는 `Container`로 표현해 multi‑plane
    operation을 처리한다.

10. 각 operation은 SimPy 프로세스로 구현하여
    `yield env.timeout(duration)`으로 단계별 시간을 표현하고,
    `with resource.request(): yield req`를 통해 자원 점유와 대기를
    자연스럽게
    처리한다[\[9\]](https://realpython.com/simpy-simulating-with-python/#:~:text=how%20many%20can%20be%20in,environment%20at%20any%20given%20time).

11. 스케줄러는 더 이상 복잡한 백로그 관리나 조건 검증을 하지 않아도
    되고, SimPy가 자동으로 FIFO 순서와 자원 가용성을 관리한다.
    옵션적으로 **pyschedule**을 활용해 일정 생성과 제약 기반
    최적화(precedence, capacity)를 수행할 수
    있다[\[11\]](https://github.com/timnon/pyschedule#:~:text=pyschedule).

12. **패키지 및 설계 요소 통합 방안**

13. Pydantic을 이용해 operation 정의를 로드하고 검증한 후, FSM 객체를
    생성한다. FSM은 operation의 상태 전환과 자원 업데이트를 담당하며,
    자원 객체는 SimPy Resource나 별도의 상태 클래스이다.

14. 스케줄러는 FSM과 자원 객체를 orchestration하며, **SimPy**를
    사용한다면 스케줄러의 복잡성을 크게 줄일 수 있다. 확률적 스케줄링은
    SimPy process 생성 시 `random` 모듈을 사용하거나, 별도의 `Proposer`
    전략 클래스를 통해 operation 후보를 선택하도록 구현한다.

15. 테스트·디버깅을 위해 FSM 라이브러리의 그래프 출력 기능과 SimPy의
    이벤트 로그를 활용하면, 새 operation 추가 시 영향을 쉽게 파악할 수
    있다.

## 결론

`nandseqgen_v2`의 현재 구조는 operation 추가/수정/삭제에 따른 **자원
상태 갱신과 시퀀스 검증 로직이 분산되어 있어 유지보수가 어렵다**. 이를
개선하기 위해 다음을 추천한다:

1.  **Pydantic으로 operation 정의와 자원 상태를 명시적 모델로 선언하고
    검증**하여 초기 데이터 오류를 줄이고, validation 로직을 재사용성
    높은 형태로
    유지한다[\[14\]](https://docs.pydantic.dev/latest/#:~:text=Pydantic%20is%20the%20most%20widely,data%20validation%20library%20for%20Python).
2.  **FSM 라이브러리(**`transitions` **또는** `python‑statemachine`**)를
    도입하여 operation의 상태와 전환을 명확히 하고, exclusivity 및
    dependency 규칙을 Guard 조건과 콜백으로
    구현**한다[\[12\]](https://statemachine.events/article/Top_10_State_Machine_Frameworks_for_Python.html#:~:text=Transitions%20is%20a%20lightweight%20state,sized%20projects).
3.  **SimPy를 사용하여 자원과 operation을 이벤트 기반으로 표현**하면
    자원 충돌 처리와 스케줄링 로직이 단순화되어 operation 추가/수정 시
    수정해야 할 코드 범위를 줄일 수
    있다[\[9\]](https://realpython.com/simpy-simulating-with-python/#:~:text=how%20many%20can%20be%20in,environment%20at%20any%20given%20time).
    작은 규모의 최적화가 필요할 경우 `pyschedule`이나 OR‑Tools와 같은
    제약 만족 솔버를 사용해 계획을 생성할 수
    있다[\[11\]](https://github.com/timnon/pyschedule#:~:text=pyschedule).

이러한 구조 개선과 외부 패키지 활용을 통해 nandseqgen_v2는 **새로운
operation의 도입이나 기존 operation의 수정/제거에 대응하는 유연성을
확보하고, 자원 관리 및 시퀀스 검증을 더 체계적으로 수행**할 수 있을
것이다.

[\[1\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L44-L67)
[\[2\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L16-L22)
[\[3\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L45-L56)
[\[4\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L50-L53)
[\[5\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L52-L55)
[\[6\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L58-L60)
[\[7\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L60-L67)
[\[8\]](https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md#L35-L42)
RESTRUCTURING.md

<https://github.com/hoony3000/nandseqgen_v2/blob/main/docs/RESTRUCTURING.md>

[\[9\]](https://realpython.com/simpy-simulating-with-python/#:~:text=how%20many%20can%20be%20in,environment%20at%20any%20given%20time)
SimPy: Simulating Real-World Processes With Python -- Real Python

<https://realpython.com/simpy-simulating-with-python/>

[\[10\]](https://simpy.readthedocs.io/en/latest/simpy_intro/shared_resources.html#:~:text=,%28name%2C%20env.now)
Shared Resources --- SimPy 4.1.2.dev8+g81c7218 documentation

<https://simpy.readthedocs.io/en/latest/simpy_intro/shared_resources.html>

[\[11\]](https://github.com/timnon/pyschedule#:~:text=pyschedule)
GitHub - timnon/pyschedule: pyschedule - resource scheduling in python

<https://github.com/timnon/pyschedule>

[\[12\]](https://statemachine.events/article/Top_10_State_Machine_Frameworks_for_Python.html#:~:text=Transitions%20is%20a%20lightweight%20state,sized%20projects)
Top 10 State Machine Frameworks for Python

<https://statemachine.events/article/Top_10_State_Machine_Frameworks_for_Python.html>

[\[13\]](https://python-statemachine.readthedocs.io/en/latest/readme.html#:~:text=Features%C2%B6)
Python StateMachine - python-statemachine 2.5.0

<https://python-statemachine.readthedocs.io/en/latest/readme.html>

[\[14\]](https://docs.pydantic.dev/latest/#:~:text=Pydantic%20is%20the%20most%20widely,data%20validation%20library%20for%20Python)
[\[15\]](https://docs.pydantic.dev/latest/#:~:text=,validators%20and%20serializers%20to%20alter)
[\[16\]](https://docs.pydantic.dev/latest/#:~:text=,huggingface%2C%20Django%20Ninja%2C%20SQLModel%2C)
Welcome to Pydantic - Pydantic Validation

<https://docs.pydantic.dev/latest/>
