# NANDSEQGEN_V2 구조 개선안: 공통 문제점 및 해결방안

> **작성일**: 2025-10-27
> **목적**: 4개 전문가 제안서의 공통 문제점을 종합하고, 구현 가능한 솔루션을 매핑
> **참조 문서**:
> - RESTRUCTURING_PROPOSAL_FROM_VIRTUAL_EXPERTS.md
> - RESTRUCTURING_PROPOSAL_FROM_VIRTUAL_EXPERTS_BY_GPT5_DEEPRESEARCH_A.md
> - RESTRUCTURING_PROPOSAL_FROM_VIRTUAL_EXPERTS_BY_GPT5_DEEPRESEARCH_B.md
> - RESTRUCTURING_PROPOSAL_FROM_VIRTUAL_EXPERTS_BY_CLAUSE_DEEPRESEARCH.md

---

## 목차

1. [공통 구조적 문제점](#1-공통-구조적-문제점)
2. [문제-솔루션 매핑](#2-문제-솔루션-매핑)
3. [추천 Python 패키지 컨센서스](#3-추천-python-패키지-컨센서스)
4. [솔루션 간 상충점 및 통합 전략](#4-솔루션-간-상충점-및-통합-전략)
5. [구현 우선순위 및 로드맵](#5-구현-우선순위-및-로드맵)
6. [Draft 코드 작성 가이드](#6-draft-코드-작성-가이드)

---

## 1. 공통 구조적 문제점

4개 문서 모두에서 공통적으로 지적한 핵심 문제들:

### 1.1 관심사 분리 부족 (Separation of Concerns)

**문제 상세**:
- Operation 정의, resource 관리, validation 로직이 강하게 결합
- 하나의 operation 추가 시 5개 이상의 파일 수정 필요
  - `config.yaml`: op_base, op_name 정의
  - `resourcemgr.py`: validation 로직 추가
  - `proposer.py`: address inheritance 규칙
  - `addrman.py`: celltype 샘플링
  - `phase_conditional`: 확률 분포 업데이트

**영향**:
- 개발 생산성 저하 (새 operation 추가 시간: 평균 2일)
- Side effect 발생 위험 증가
- 테스트 복잡도 증가

**4개 문서의 합의**:
- 모든 문서가 이를 최우선 문제로 지적
- Plugin/Registry 패턴을 통한 분리 필요

---

### 1.2 암묵적 계약과 Runtime 검증 부족

**문제 상세**:
- Operation schema가 명시적으로 정의되지 않음
- Config 검증이 runtime까지 지연됨
- 잘못된 설정이 실행 중에야 발견됨

**예시**:
```yaml
# config.yaml
op_bases:
  MY_NEW_OP:
    scope: "DIE_WIDE"  # ← 이 값이 resourcemgr.py에 하드코딩됨
    states: [ISSUE, CORE_BUSY]  # ← 순서와 타입이 코드에 암묵적으로 가정됨
```

**영향**:
- 디버깅 시간 증가
- Production 환경에서의 예기치 않은 실패
- Config 문서화 어려움

**4개 문서의 합의**:
- Pydantic을 통한 compile-time 검증 필요
- Type-safe config loading 필수

---

### 1.3 절차적 Validation의 확장성 문제

**문제 상세**:
- Validation 규칙이 `resourcemgr.py`에 하드코딩
- 새 규칙 추가 시 reserve() 메서드 직접 수정 필요
- Open/Closed Principle 위반

**현재 코드 패턴**:
```python
# resourcemgr.py
def reserve(self, op):
    if not self._check_bus_exclusion(op):
        return Reservation(ok=False, reason="bus")
    if not self._check_busy_exclusion(op):
        return Reservation(ok=False, reason="busy")
    # ... 8개 이상의 if문이 순차적으로 연결됨
```

**영향**:
- 새 validation 규칙 추가가 어려움
- 규칙 간 우선순위 변경 불가
- 테스트 시 특정 규칙만 비활성화 불가

**4개 문서의 합의**:
- 선언적 규칙 정의 필요 (YAML/JSON)
- Rule Engine 또는 Strategy Pattern 도입
- 규칙을 독립적인 클래스/함수로 분리

---

### 1.4 State 관리의 분산과 일관성 문제

**문제 상세**:
- State 변경 로직이 scheduler, resourcemgr, addrman에 분산
- Operation 종료 시 3곳에서 state 업데이트 필요
- Suspend/resume 시 state 동기화 어려움

**현재 패턴**:
```python
# scheduler.py
def _handle_op_end(self, op):
    if op.base in program_bases:
        self._am.apply_pgm(...)  # ← AddressManager 업데이트
    self._rm.update_state(...)    # ← ResourceManager 업데이트
    if op.is_resume:
        self._handle_resume_logic(...)  # ← Backlog 처리
```

**영향**:
- State 불일치 버그 발생
- Debugging 어려움
- Transaction 관리 복잡

**4개 문서의 합의**:
- State Machine 패턴 도입 필요
- 명시적 state transition 정의
- Observer 패턴으로 state 변화 추적

---

### 1.5 확률적 스케줄링의 결합도

**문제 상세**:
- Phase_conditional 확률이 operation이 아닌 resource state에 바인딩되어야 함
- 그러나 현재 구조는 operation과 state가 강하게 결합
- State-dependent weight lookup이 proposer.py에 하드코딩

**요구사항 (RESTRUCTURING.md)**:
> "확률은 operation이 아닌 **resource state**에 바인딩됨"

**영향**:
- State 추가 시 proposer.py 전체 수정 필요
- 확률 분포 실험이 어려움
- Configuration 외부화 불가

**4개 문서의 합의**:
- State-based scheduler 필요
- Weight configuration 외부화
- NumPy 기반 확률적 샘플링 유지

---

### 1.6 Validation 규칙의 검증 불가능성

**문제 상세**:
- Validation 규칙이 코드에 내재되어 formal verification 불가
- 규칙 간 충돌 여부를 사전에 확인할 수 없음
- 새 규칙 추가 시 기존 규칙과의 상호작용 예측 어려움

**예시**:
- `bus_exclusion`과 `suspend_exclusion`이 동시에 적용될 때 우선순위?
- Multi-plane operation에서 latch_exclusion과 busy_exclusion의 상호작용?

**4개 문서의 합의**:
- 선언적 제약 조건 모델링 필요
- Constraint Solver (선택적)
- Property-based testing으로 불변식 검증

---

### 1.7 Multi-level Resource Hierarchy의 복잡도

**문제 상세**:
- Plane, Die, Global 레벨의 resource 관계가 암묵적
- Multi-plane operation의 sync point 관리 복잡
- Scope (DIE_WIDE, PLANE_SET) 처리가 if문으로 분산

**영향**:
- 새 resource 레벨 추가 어려움 (예: channel 레벨)
- Resource hierarchy 변경 시 광범위한 수정 필요

**4개 문서의 합의**:
- Hierarchical resource abstraction 필요
- SimPy Resource 또는 유사 패턴 활용
- Scope를 data로 표현

---

## 2. 문제-솔루션 매핑

각 문제에 대한 구체적 솔루션과 구현 접근법:

---

### 솔루션 1: Type-Safe Configuration (Pydantic)

**해결하는 문제**: 1.2 (암묵적 계약)

**구현 방법**:

```python
# models.py (새 파일)
from pydantic import BaseModel, Field, validator
from typing import Literal, Dict, List

class StateConfig(BaseModel):
    """Operation state 정의"""
    bus: bool
    duration: float = Field(gt=0)

class OperationBaseConfig(BaseModel):
    """op_bases 스키마"""
    scope: Literal["DIE_WIDE", "PLANE_SET", "NONE"]
    affect_state: bool
    instant_resv: bool
    states: Dict[str, StateConfig]

    @validator('states')
    def validate_states(cls, v):
        if not v:
            raise ValueError("states cannot be empty")
        # Bus state는 최대 1개
        bus_states = [k for k, s in v.items() if s.bus]
        if len(bus_states) > 1:
            raise ValueError(f"Multiple bus states: {bus_states}")
        return v

class OperationConfig(BaseModel):
    """op_names 스키마"""
    base: str
    celltype: Literal["SLC", "TLC", "QLC", "FWSLC"]
    multi: bool = False
    durations: Dict[str, float] = {}

class TopologyConfig(BaseModel):
    dies: int = Field(gt=0)
    planes: int = Field(gt=0)
    blocks_per_die: int = Field(gt=0)
    pages_per_block: int = Field(gt=0)

class Config(BaseModel):
    """전체 config.yaml 스키마"""
    topology: TopologyConfig
    op_bases: Dict[str, OperationBaseConfig]
    op_names: Dict[str, OperationConfig]
    phase_conditional: Dict[str, Dict[str, float]]

    class Config:
        extra = "forbid"  # Unknown fields 에러

# main.py 수정
def load_config(path: str) -> Config:
    raw = yaml.safe_load(open(path))
    return Config(**raw)  # ← 자동 검증, 타입 에러 즉시 발견
```

**효과**:
- ✅ Config 로딩 시점에 모든 검증 완료
- ✅ IDE 자동완성 지원
- ✅ JSON Schema 자동 생성 (문서화)
- ✅ 런타임 에러 → 컴파일 타임 에러 전환

**우선순위**: 최고 (모든 문서 합의)

**예상 작업 시간**: 1-2주

---

### 솔루션 2: Validation Rule Externalization

**해결하는 문제**: 1.3 (절차적 validation), 1.6 (검증 불가능성)

**구현 방법**:

```yaml
# validation_rules.yaml (새 파일)
rules:
  bus_exclusion:
    type: temporal_overlap
    description: "ISSUE/DATA_IN/DATA_OUT states cannot overlap globally"
    scope: global
    resource: bus
    constraint: no_overlap
    states: [ISSUE, DATA_IN, DATA_OUT]

  die_wide_exclusion:
    type: resource_mutex
    description: "DIE_WIDE operations are mutually exclusive per die"
    scope: die
    condition:
      operation.scope: DIE_WIDE
    states: [CORE_BUSY]
    constraint: exclusive

  latch_exclusion:
    type: state_conflict
    scope: plane
    resource: latch
    constraint: no_conflict_by_kind
    kinds: [READ, LSB, CSB, MSB]
```

```python
# validation_engine.py (새 파일)
from typing import Protocol, List
from dataclasses import dataclass

@dataclass
class ValidationResult:
    ok: bool
    rule_name: str = ""
    reason: str = ""

class ValidationRule(Protocol):
    """Validation rule 인터페이스"""
    def check(self, op: Operation, context: Context) -> ValidationResult:
        ...

class BusExclusionRule:
    def __init__(self, config: dict):
        self.states = config['states']

    def check(self, op: Operation, context: Context) -> ValidationResult:
        for state_name in self.states:
            if not op.has_state(state_name):
                continue
            interval = op.get_interval(state_name)
            if context.bus_occupied(interval):
                return ValidationResult(
                    ok=False,
                    rule_name="bus_exclusion",
                    reason=f"Bus occupied during {state_name}"
                )
        return ValidationResult(ok=True)

class ValidationEngine:
    def __init__(self, rules_config: dict):
        self.rules: List[ValidationRule] = []
        for rule_name, rule_cfg in rules_config['rules'].items():
            rule_cls = RULE_TYPES[rule_cfg['type']]
            self.rules.append(rule_cls(rule_cfg))

    def validate(self, op: Operation, context: Context) -> ValidationResult:
        for rule in self.rules:
            result = rule.check(op, context)
            if not result.ok:
                return result
        return ValidationResult(ok=True)

# resourcemgr.py 수정
class ResourceManager:
    def __init__(self, validation_engine: ValidationEngine):
        self.validation_engine = validation_engine

    def reserve(self, op: Operation):
        result = self.validation_engine.validate(op, self.get_context())
        if not result.ok:
            return Reservation(ok=False, reason=result.reason)
        # ... 실제 예약 로직
```

**효과**:
- ✅ 새 validation 규칙 추가 = YAML 편집만
- ✅ 규칙 간 독립성 보장
- ✅ 테스트 시 특정 규칙만 비활성화 가능
- ✅ 비프로그래머도 규칙 이해 가능

**우선순위**: 높음 (3/4 문서 강력 추천)

**예상 작업 시간**: 3-4주

---

### 솔루션 3: State Machine for Resource Lifecycle

**해결하는 문제**: 1.4 (state 분산), 1.7 (resource hierarchy)

**구현 방법**:

```python
# state_machine.py (새 파일)
from statemachine import StateMachine, State

class PlaneLifecycle(StateMachine):
    """Plane resource의 lifecycle을 명시적으로 모델링"""

    # States
    ready = State("Ready", initial=True)
    erasing = State("Erasing")
    programming = State("Programming")
    reading = State("Reading")
    suspended_erase = State("Suspended (Erase)")
    suspended_program = State("Suspended (Program)")

    # Transitions
    start_erase = ready.to(erasing)
    start_program = ready.to(programming)
    start_read = ready.to(reading)

    suspend_erase = erasing.to(suspended_erase)
    suspend_program = programming.to(suspended_program)

    resume_erase = suspended_erase.to(erasing)
    resume_program = suspended_program.to(programming)

    complete = (erasing.to(ready) |
                programming.to(ready) |
                reading.to(ready))

    # Guards (validation)
    def before_start_erase(self, event_data):
        if self.current_state != self.ready:
            raise InvalidStateTransition(
                f"Cannot erase from state {self.current_state}"
            )

    def before_suspend_erase(self, event_data):
        if self.current_state != self.erasing:
            raise InvalidStateTransition("No erase operation to suspend")

    # Callbacks
    def on_enter_erasing(self):
        logger.info(f"Plane {self.plane_id} entering ERASE state")
        self.metrics.increment("erase_count")

    def on_exit_suspended_erase(self):
        logger.info(f"Plane {self.plane_id} resuming from suspend")

# resourcemgr.py 수정
class ResourceManager:
    def __init__(self, topology):
        self.plane_state_machines = {
            (die, plane): PlaneLifecycle(plane_id=f"{die}.{plane}")
            for die in range(topology.dies)
            for plane in range(topology.planes)
        }

    def reserve_erase(self, die, plane):
        sm = self.plane_state_machines[(die, plane)]
        try:
            sm.start_erase()  # ← 자동으로 state validation 수행
        except InvalidStateTransition as e:
            return Reservation(ok=False, reason=str(e))
```

**효과**:
- ✅ State 전이 규칙이 명시적
- ✅ Illegal transition 자동 방지
- ✅ State diagram 시각화 가능
- ✅ Suspend/resume 로직 단순화

**우선순위**: 높음 (모든 문서 추천)

**예상 작업 시간**: 4-5주

---

### 솔루션 4: Operation Plugin Architecture

**해결하는 문제**: 1.1 (관심사 분리), 1.5 (확률적 스케줄링)

**구현 방법**:

```python
# operation_plugin.py (새 파일)
from abc import ABC, abstractmethod
from typing import List

class OperationPlugin(ABC):
    """Operation의 공통 인터페이스"""

    @abstractmethod
    def get_name(self) -> str:
        pass

    @abstractmethod
    def get_resource_requirements(self) -> ResourceRequirements:
        pass

    @abstractmethod
    def validate_preconditions(self, context: ValidationContext) -> bool:
        pass

    @abstractmethod
    def apply_state_changes(self, resource_mgr: ResourceManager):
        pass

class EraseOperation(OperationPlugin):
    def __init__(self, config: OperationConfig):
        self.config = config

    def get_name(self) -> str:
        return "ERASE"

    def get_resource_requirements(self) -> ResourceRequirements:
        return ResourceRequirements(
            scope=Scope.DIE_WIDE,
            states=[
                State("ISSUE", bus=True, duration=self.config.durations["ISSUE"]),
                State("CORE_BUSY", bus=False, duration=self.config.durations["CORE_BUSY"])
            ]
        )

    def validate_preconditions(self, context: ValidationContext) -> bool:
        # Erase-specific validation (e.g., EPR check)
        return context.address_manager.can_erase(self.address)

    def apply_state_changes(self, resource_mgr: ResourceManager):
        resource_mgr.apply_erase(self.address)

class OperationRegistry:
    def __init__(self):
        self._plugins: Dict[str, Type[OperationPlugin]] = {}

    def register(self, op_type: str, plugin_class: Type[OperationPlugin]):
        self._plugins[op_type] = plugin_class

    def create(self, op_type: str, config: dict) -> OperationPlugin:
        if op_type not in self._plugins:
            raise ValueError(f"Unknown operation type: {op_type}")
        return self._plugins[op_type](config)

# 사용
registry = OperationRegistry()
registry.register("ERASE", EraseOperation)
registry.register("PROGRAM_SLC", ProgramSLCOperation)
registry.register("READ", ReadOperation)

# 동적 생성
op = registry.create("ERASE", {"die": 0, "plane": 0, "block": 10})
```

**효과**:
- ✅ 새 operation 추가 = Plugin 클래스 구현 + 등록
- ✅ Operation 간 독립성 보장
- ✅ 단위 테스트 용이

**우선순위**: 중간 (대규모 리팩토링 필요)

**예상 작업 시간**: 6-8주

---

### 솔루션 5: State-Based Probabilistic Scheduler

**해결하는 문제**: 1.5 (확률적 스케줄링)

**구현 방법**:

```python
# state_based_scheduler.py (새 파일)
import numpy as np
from typing import Dict, List

class StateBasedScheduler:
    def __init__(self, weight_config: Dict[str, Dict[str, float]]):
        """
        weight_config 예시:
        {
            'idle_ready': {'read': 3.0, 'program': 2.0, 'erase': 1.0},
            'busy_programming': {'suspend': 0.5, 'read': 0.1},
            ...
        }
        """
        self.weights = weight_config

    def select_operation(self,
                         candidates: List[Operation],
                         resource_state: ResourceState) -> Operation:
        """
        현재 resource state에 따라 확률적으로 operation 선택
        """
        # 현재 state를 key로 변환
        state_key = self._make_state_key(resource_state)

        # 각 candidate의 weight 계산
        weights = []
        for op in candidates:
            weight = self.weights.get(state_key, {}).get(op.type, 1.0)
            weights.append(weight)

        # 확률적 선택
        if sum(weights) == 0:
            return None

        probs = np.array(weights) / sum(weights)
        return np.random.choice(candidates, p=probs)

    def _make_state_key(self, resource_state: ResourceState) -> str:
        """
        Resource state를 phase key로 변환
        예: busy=idle, cache=ready → "idle_ready"
        """
        return f"{resource_state.busy}_{resource_state.cache}"

# proposer.py 수정
class Proposer:
    def __init__(self, scheduler: StateBasedScheduler, ...):
        self.scheduler = scheduler

    def propose(self, context: ProposalContext) -> List[Operation]:
        # 현재 가능한 operation 후보 생성
        candidates = self._generate_candidates(context)

        # Resource state 조회
        resource_state = context.resource_manager.get_state(
            die=context.die,
            plane=context.plane
        )

        # State-based 확률적 선택
        selected = self.scheduler.select_operation(candidates, resource_state)

        return [selected] if selected else []
```

**Configuration 예시**:

```yaml
# phase_conditional.yaml (기존과 동일하지만 의미가 명확해짐)
state_based_weights:
  # State key: busy_state + cache_state
  idle_ready:
    READ: 3.0
    PROGRAM_SLC: 2.0
    ERASE: 1.0

  busy_programming:
    PROGRAM_SUSPEND: 0.5
    READ: 0.1  # 가능하지만 낮은 확률

  suspended_loaded:
    PROGRAM_RESUME: 5.0
    READ_CACHE: 2.0
```

**효과**:
- ✅ 확률이 operation이 아닌 state에 바인딩됨 (요구사항 충족)
- ✅ Configuration 외부화로 실험 용이
- ✅ State 추가 시 weight config만 수정

**우선순위**: 높음 (핵심 요구사항)

**예상 작업 시간**: 2-3주

---

### 솔루션 6: Property-Based Testing (Hypothesis)

**해결하는 문제**: 1.6 (검증 불가능성), 전반적 품질 보장

**구현 방법**:

```python
# tests/test_properties.py
from hypothesis import given, strategies as st, settings
from hypothesis.stateful import RuleBasedStateMachine, rule

# Strategy: 유효한 operation 생성
valid_operations = st.builds(
    Operation,
    name=st.sampled_from(["ERASE", "PROGRAM_SLC", "READ"]),
    die=st.integers(min_value=0, max_value=3),
    plane=st.integers(min_value=0, max_value=1),
    start_time=st.floats(min_value=0, max_value=10000),
    duration=st.floats(min_value=0.1, max_value=100)
)

@given(st.lists(valid_operations, min_size=10, max_size=100))
def test_schedule_invariants(operations):
    """생성된 모든 schedule은 기본 불변조건을 만족해야 함"""
    scheduler = Scheduler()

    for op in operations:
        result = scheduler.reserve(op)

        if result.ok:
            # Invariant 1: Bus 충돌 없음
            schedule = scheduler.get_committed_operations()
            assert no_bus_conflicts(schedule), "Bus conflict detected"

            # Invariant 2: Die-wide 충돌 없음
            assert no_die_wide_conflicts(schedule), "Die-wide conflict"

            # Invariant 3: Plane busy 중복 없음
            assert no_plane_busy_overlap(schedule), "Plane busy overlap"

class SchedulerStateMachine(RuleBasedStateMachine):
    """Stateful property-based testing"""

    def __init__(self):
        super().__init__()
        self.scheduler = Scheduler()
        self.committed_ops = []

    @rule(op=valid_operations)
    def reserve_operation(self, op):
        result = self.scheduler.reserve(op)
        if result.ok:
            self.scheduler.commit(result)
            self.committed_ops.append(op)

    @rule()
    def check_invariants(self):
        # 항상 성립해야 하는 조건 검증
        assert self.scheduler.is_consistent()

# 실행
TestScheduler = SchedulerStateMachine.TestCase
```

**효과**:
- ✅ 수천 개의 랜덤 시나리오 자동 생성 및 테스트
- ✅ Edge case 자동 발견
- ✅ Regression 방지
- ✅ Validation 규칙의 불변식 검증

**우선순위**: 최고 (모든 문서 강력 추천)

**예상 작업 시간**: 2-3주 (초기 setup), 지속적 확장

---

### 솔루션 7: SimPy for Event-Driven Simulation (선택적)

**해결하는 문제**: 1.7 (resource hierarchy), 전반적 스케줄링 단순화

**구현 방법**:

```python
# simpy_integration.py (선택적 대안 아키텍처)
import simpy

class NANDSimulator:
    def __init__(self, env: simpy.Environment, topology):
        self.env = env

        # Resource 정의
        self.bus = simpy.Resource(env, capacity=1)  # Global bus

        self.dies = {
            die_id: simpy.Resource(env, capacity=1)  # Die-wide resource
            for die_id in range(topology.dies)
        }

        self.planes = {
            (die, plane): simpy.Resource(env, capacity=1)
            for die in range(topology.dies)
            for plane in range(topology.planes)
        }

    def erase_operation(self, die, plane, block):
        """Erase operation as SimPy process"""
        # Phase 1: ISSUE (bus 필요)
        with self.bus.request() as bus_req:
            yield bus_req
            yield self.env.timeout(5)  # ISSUE duration

        # Phase 2: CORE_BUSY (die-wide 독점)
        with self.dies[die].request() as die_req:
            yield die_req
            with self.planes[(die, plane)].request() as plane_req:
                yield plane_req
                yield self.env.timeout(3000)  # CORE_BUSY duration

    def multi_plane_program(self, die, planes, data_list):
        """Multi-plane operation with AllOf"""
        # Phase 1: Setup (병렬)
        setup_events = [
            self.env.process(self._setup_plane(die, p, data_list[i]))
            for i, p in enumerate(planes)
        ]
        yield simpy.AllOf(self.env, setup_events)

        # Phase 2: Execute (동시)
        exec_events = [
            self.env.process(self._execute_plane(die, p))
            for p in planes
        ]
        yield simpy.AllOf(self.env, exec_events)

# 실행
env = simpy.Environment()
sim = NANDSimulator(env, topology)

# Operation 스케줄링
env.process(sim.erase_operation(die=0, plane=0, block=10))
env.process(sim.multi_plane_program(die=1, planes=[0, 1], data=[...]))

env.run(until=100000)  # 100ms 시뮬레이션
```

**효과**:
- ✅ Resource 충돌 자동 처리
- ✅ Multi-plane sync point 명시적
- ✅ Suspend/resume을 interrupt()로 구현
- ⚠️ 현재 아키텍처와의 통합 복잡

**우선순위**: 낮음 (대규모 재작성 필요)

**예상 작업 시간**: 12주+ (전체 재구현)

**Note**: 이는 장기 전략으로 고려, 현재는 기존 아키텍처 개선 우선

---

## 3. 추천 Python 패키지 컨센서스

4개 문서 모두에서 추천한 패키지와 우선순위:

| 패키지 | 용도 | 문서 A | 문서 B | 문서 C | 문서 D | 합의 우선순위 | 난이도 |
|--------|------|--------|--------|--------|--------|---------------|--------|
| **Pydantic** | Config validation | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | **최고** | 낮음 |
| **Hypothesis** | Property testing | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | **최고** | 중간 |
| **python-statemachine** | State machine | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | **높음** | 중간 |
| **transitions** | State machine (대안) | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | 높음 | 낮음 |
| **SimPy** | Event simulation | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | 중간 | 높음 |
| **NetworkX** | Dependency graph | ⭐⭐⭐⭐ | - | - | ⭐⭐⭐ | 중간 | 낮음 |
| **NumPy/SciPy** | Probabilistic | ⭐⭐⭐⭐ | - | ⭐⭐⭐ | - | 높음 | 낮음 |
| **OmegaConf** | Config management | - | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 높음 | 낮음 |
| **business-rules** | Rule engine | - | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | 중간 | 중간 |
| **Z3** | SMT solver | ⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ | 낮음 | 높음 |
| **python-constraint** | CSP solver | ⭐⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐⭐ | 낮음 | 중간 |

### 즉시 도입 권장 (Phase 1)

1. **Pydantic** - 만장일치 최고 우선순위
   ```bash
   pip install pydantic
   ```
   - Config validation의 표준
   - ROI가 가장 빠름
   - 학습 곡선 낮음

2. **Hypothesis** - 만장일치 최고 우선순위
   ```bash
   pip install hypothesis
   ```
   - Property-based testing의 표준
   - Regression 방지
   - Edge case 자동 발견

3. **NumPy** (이미 사용 중) - 확률적 스케줄링 유지
   - 기존 코드 베이스 활용
   - 추가 학습 불필요

### 단기 도입 권장 (Phase 2, 1-3개월)

4. **python-statemachine** 또는 **transitions**
   ```bash
   pip install python-statemachine
   # 또는
   pip install transitions
   ```
   - State machine 필수
   - 선택 기준:
     - `python-statemachine`: 더 현대적, 시각화 우수
     - `transitions`: 더 경량, 학습 곡선 낮음
   - **권장**: `python-statemachine` (3/4 문서 우선 추천)

5. **OmegaConf** (선택적)
   ```bash
   pip install omegaconf
   ```
   - Config 관리 향상
   - Pydantic과 통합 가능
   - 필수는 아니지만 권장

### 중기 검토 (Phase 3, 3-6개월)

6. **NetworkX** (선택적)
   ```bash
   pip install networkx
   ```
   - Operation dependency graph 관리
   - Topological sort, cycle detection
   - 현재 scheduler가 충분하다면 보류

7. **business-rules** (선택적)
   ```bash
   pip install business-rules
   ```
   - Rule engine
   - 선언적 규칙 정의
   - 단순 ValidationEngine으로 충분할 수 있음

### 장기 검토 (Phase 4+, 6개월 이상)

8. **SimPy** (대규모 재작성 필요)
   ```bash
   pip install simpy
   ```
   - Event-driven simulation 프레임워크
   - 전체 아키텍처 재설계 필요
   - 장기 전략으로 고려

9. **Z3** / **python-constraint** (선택적, 고급)
   ```bash
   pip install z3-solver
   # 또는
   pip install python-constraint
   ```
   - Formal verification
   - Critical system에만 필요
   - 성능 trade-off 주의

---

## 4. 솔루션 간 상충점 및 통합 전략

### 상충점 1: State Machine vs SimPy

**충돌**:
- State Machine (솔루션 3)은 resource lifecycle 관리
- SimPy (솔루션 7)는 전체 시뮬레이션 아키텍처 재설계

**문서별 입장**:
- 문서 A: SimPy 적극 추천
- 문서 B: State Machine 우선, SimPy 선택적
- 문서 C: State Machine 필수
- 문서 D: 둘 다 추천하지만 별도

**통합 전략**:

```
Phase 1-2: State Machine 도입 (현재 아키텍처 유지)
    ↓
Phase 3: State Machine 안정화
    ↓
Phase 4 (선택적): SimPy 통합 검토
    - State Machine을 SimPy process로 래핑
    - 점진적 마이그레이션
```

**권장 결정**: State Machine 우선, SimPy는 장기 옵션

---

### 상충점 2: Rule Engine (business-rules) vs Custom ValidationEngine

**충돌**:
- business-rules: 외부 라이브러리, JSON 기반 규칙
- Custom ValidationEngine: 직접 구현, YAML 기반

**문서별 입장**:
- 문서 A: Custom 선호
- 문서 B: business-rules 추천
- 문서 C: business-rules 추천
- 문서 D: Custom 추천

**통합 전략**:

```python
# Hybrid approach: 인터페이스는 동일하게, 구현은 선택 가능

# validation_engine.py
class ValidationEngine:
    def __init__(self, backend: Literal["custom", "business_rules"] = "custom"):
        if backend == "custom":
            self.engine = CustomValidationEngine()
        else:
            self.engine = BusinessRulesValidationEngine()

    def validate(self, op, context):
        return self.engine.validate(op, context)
```

**권장 결정**:
- Phase 1-2: Custom ValidationEngine (의존성 최소화)
- Phase 3: business-rules 통합 검토 (필요 시)

---

### 상충점 3: Plugin Architecture 범위

**충돌**:
- 문서 A, C: Operation만 plugin화
- 문서 B: Operation + Validation rule 모두 plugin화
- 문서 D: Operation + Scheduler도 plugin화

**통합 전략**:

```
Phase 1: Operation Plugin만 구현
Phase 2: Validation Rule 확장
Phase 3 (선택적): Scheduler strategy plugin
```

**권장 결정**: Operation Plugin 우선, 점진적 확장

---

### 상충점 4: Pydantic vs OmegaConf

**충돌**:
- Pydantic: Validation 강점
- OmegaConf: Config 관리 (계층, override) 강점

**문서별 입장**:
- 문서 A: Pydantic 우선
- 문서 B, C, D: 둘 다 추천

**통합 전략**:

```python
# 둘을 함께 사용 (상호 보완적)

from omegaconf import OmegaConf
from pydantic import BaseModel

def load_config(path: str) -> Config:
    # OmegaConf로 로딩 및 계층 관리
    cfg = OmegaConf.load(path)

    # Pydantic으로 검증
    return Config(**OmegaConf.to_container(cfg))
```

**권장 결정**:
- Phase 1: Pydantic만 (필수)
- Phase 2: OmegaConf 추가 (선택적, 계층적 설정 필요 시)

---

### 상충점 5: Constraint Solver 필요성

**충돌**:
- 문서 A, C: Constraint solver (Z3/python-constraint) 적극 추천
- 문서 B: 언급만, 우선순위 낮음
- 문서 D: 선택적, 성능 이슈 우려

**통합 전략**:

```python
# Opt-in feature flag

class Scheduler:
    def __init__(self, use_constraint_solver: bool = False):
        self.use_constraint_solver = use_constraint_solver
        if use_constraint_solver:
            self.solver = Z3Solver()

    def reserve(self, op):
        # 기본 validation
        result = self.validation_engine.validate(op, ...)

        # 선택적 formal verification
        if self.use_constraint_solver:
            verified = self.solver.verify(op, ...)
            if not verified:
                logger.warning("Constraint solver rejected operation")

        return result
```

**권장 결정**:
- Phase 1-3: Constraint solver 없이 진행
- Phase 4: Critical operation에만 opt-in 적용

---

## 5. 구현 우선순위 및 로드맵

### Phase 1: Quick Wins (1-2개월)

**목표**: Type safety와 테스트 커버리지 확보

#### 주 1-4: Pydantic 도입

**작업 항목**:
- [ ] `models.py` 생성
- [ ] Config schema 정의 (OperationConfig, TopologyConfig 등)
- [ ] `main.py`에서 Pydantic 검증 통합
- [ ] 기존 dict 기반 코드는 `.dict()` 메서드로 호환 유지
- [ ] Unit test 작성

**검증 기준**:
- Config 로딩 시 잘못된 operation 정의 즉시 발견
- 기존 테스트 모두 통과

**예상 리스크**: Config schema 변경으로 기존 YAML 호환성 깨질 수 있음
**완화 전략**: Backward compatibility layer, migration script

#### 주 5-8: Hypothesis 테스트 추가

**작업 항목**:
- [ ] `tests/test_properties.py` 작성
- [ ] Bus exclusion invariant 테스트
- [ ] Die-wide exclusion invariant 테스트
- [ ] Plane busy overlap 테스트
- [ ] CI/CD에 통합

**검증 기준**:
- 1000+ 랜덤 시나리오 통과
- 새로운 버그 발견 및 수정

**산출물**:
- ✅ Config 로딩 시점에 모든 operation 검증
- ✅ 자동화된 property-based 테스트
- ✅ Regression 방지 체계

---

### Phase 2: Structural Improvements (2-4개월)

**목표**: Validation과 State 관리 개선

#### 주 9-14: Validation Rule 외부화

**작업 항목**:
- [ ] `validation_rules.yaml` 생성
- [ ] `ValidationEngine` 클래스 구현
- [ ] 기존 `resourcemgr.py`의 validation 로직을 rule로 마이그레이션
- [ ] Shadow mode: 기존 로직과 new engine 결과 비교
- [ ] 불일치 케이스 디버깅 및 수정

**검증 기준**:
- Shadow mode에서 100% 일치
- 새 규칙 추가 시 YAML 편집만으로 가능

**예상 리스크**: ValidationEngine 성능 저하
**완화 전략**: Profiling, 최적화, feature flag로 롤백 가능

#### 주 15-20: State Machine 통합

**작업 항목**:
- [ ] `PlaneLifecycle` state machine 정의
- [ ] `ResourceManager`에 통합
- [ ] State diagram 자동 생성 스크립트
- [ ] 기존 state 관리 로직과 비교 검증
- [ ] Suspend/resume 로직 마이그레이션

**검증 기준**:
- State transition 100% 일치
- Illegal transition 자동 방지 확인
- Suspend/resume 시나리오 모두 통과

**예상 리스크**: State machine이 기존 로직과 불일치
**완화 전략**: Shadow mode (parallel execution + comparison)

#### 주 21-24: State-Based Probabilistic Scheduler

**작업 항목**:
- [ ] `StateBasedScheduler` 클래스 구현
- [ ] Weight configuration 외부화
- [ ] `proposer.py` 통합
- [ ] Phase key 생성 로직 검증
- [ ] 확률 분포 일치 테스트

**검증 기준**:
- 기존 phase_conditional과 동일한 확률 분포
- State 추가 시 weight config만 수정

**산출물**:
- ✅ 새 validation 규칙 추가 = YAML 편집만
- ✅ State 전이 규칙 명시화
- ✅ 확률이 resource state에 바인딩 (요구사항 충족)

---

### Phase 3: Advanced Features (4-6개월, 선택적)

**목표**: Plugin architecture와 고급 기능

#### 주 25-36: Operation Plugin System

**작업 항목**:
- [ ] `OperationPlugin` 인터페이스 정의
- [ ] 기존 operation을 plugin으로 마이그레이션 (ERASE, PROGRAM_SLC 등)
- [ ] `OperationRegistry` 구현
- [ ] `proposer.py`, `scheduler.py` 연동
- [ ] Backward compatibility 유지

**검증 기준**:
- 모든 기존 operation이 plugin으로 동작
- 새 operation 추가 시 단일 파일 수정만 필요

**예상 리스크**: Plugin system 복잡도 증가
**완화 전략**: 점진적 마이그레이션, 충분한 문서화

#### 주 37-40: OmegaConf 통합 (선택적)

**작업 항목**:
- [ ] OmegaConf 설치 및 통합
- [ ] 계층적 config 구조 설계
- [ ] Pydantic과 통합
- [ ] CLI override 지원

**검증 기준**:
- Config 계층 및 override 동작
- Backward compatibility 유지

**산출물**:
- ✅ 새 operation 추가 = Plugin 클래스 작성 + 등록
- ✅ Config 관리 향상 (선택적)

---

### Phase 4: Long-term Strategy (6개월 이상, 선택적)

**목표**: Formal verification 및 SimPy 통합 검토

#### SimPy 통합 (12주+, 선택적)

**작업 항목**:
- [ ] SimPy 기반 프로토타입 작성
- [ ] 기존 아키텍처와 성능 비교
- [ ] 마이그레이션 전략 수립
- [ ] 점진적 전환 (모듈별)

**검증 기준**:
- 동일한 시뮬레이션 결과
- 성능 개선 확인

**결정 기준**:
- 현재 아키텍처의 한계 도달 시
- 팀 capacity 충분 시
- ROI 명확 시

#### Z3 Formal Verification (선택적)

**작업 항목**:
- [ ] `verify_schedule_correctness()` 함수 구현
- [ ] Critical operation에 opt-in 적용
- [ ] Debugging 도구로 활용
- [ ] Performance 최적화

**검증 기준**:
- Critical sequence의 correctness 증명 가능
- Constraint 충돌 자동 진단

**결정 기준**:
- Safety-critical system에만 필요
- 성능 영향 최소화 확인

---

## 6. Draft 코드 작성 가이드

Phase별 draft 코드 작성 시 참고할 구체적 가이드:

### 6.1 Pydantic Models (Phase 1)

**파일 구조**:
```
nandseqgen_v2/
├── models.py          # 새 파일
├── main.py            # 수정
└── tests/
    └── test_models.py # 새 파일
```

**models.py 템플릿**:

```python
"""
Pydantic models for config validation
"""
from pydantic import BaseModel, Field, validator, root_validator
from typing import Literal, Dict, List, Optional
from enum import Enum

class Scope(str, Enum):
    """Operation scope"""
    DIE_WIDE = "DIE_WIDE"
    PLANE_SET = "PLANE_SET"
    NONE = "NONE"

class CellType(str, Enum):
    """NAND cell type"""
    SLC = "SLC"
    TLC = "TLC"
    QLC = "QLC"
    FWSLC = "FWSLC"

class StateConfig(BaseModel):
    """State definition in operation"""
    bus: bool = False
    duration: Optional[float] = None  # Can be None if defined in op_name

    class Config:
        extra = "forbid"

class OperationBaseConfig(BaseModel):
    """op_bases schema"""
    scope: Scope
    affect_state: bool
    instant_resv: bool
    states: Dict[str, StateConfig]
    multi: Optional[bool] = False

    @validator('states')
    def validate_states(cls, v):
        if not v:
            raise ValueError("states cannot be empty")

        # Bus state는 최대 1개
        bus_states = [k for k, s in v.items() if s.bus]
        if len(bus_states) > 1:
            raise ValueError(f"Multiple bus states: {bus_states}")

        return v

    class Config:
        extra = "forbid"

class OperationConfig(BaseModel):
    """op_names schema"""
    base: str
    celltype: CellType
    multi: bool = False
    durations: Dict[str, float] = {}

    @validator('durations')
    def validate_durations(cls, v):
        for state, duration in v.items():
            if duration <= 0:
                raise ValueError(f"Duration for {state} must be positive")
        return v

    class Config:
        extra = "forbid"

class TopologyConfig(BaseModel):
    """Topology configuration"""
    dies: int = Field(gt=0)
    planes: int = Field(gt=0)
    blocks_per_die: int = Field(gt=0)
    pages_per_block: int = Field(gt=0)

    class Config:
        extra = "forbid"

class PoliciesConfig(BaseModel):
    """Policies configuration"""
    admission_window: int = Field(ge=0)
    queue_refill_period_us: float = Field(gt=0)
    topN: int = Field(gt=0)

    class Config:
        extra = "forbid"

class FeaturesConfig(BaseModel):
    """Feature flags"""
    suspend_resume_chain_enabled: bool = True
    multi_plane_enabled: bool = True

    class Config:
        extra = "allow"  # Allow new flags

class Config(BaseModel):
    """Root config schema"""
    topology: TopologyConfig
    policies: PoliciesConfig
    features: FeaturesConfig
    op_bases: Dict[str, OperationBaseConfig]
    op_names: Dict[str, OperationConfig]
    phase_conditional: Dict[str, Dict[str, float]]

    @root_validator
    def validate_op_names_bases(cls, values):
        """Validate that op_names reference valid op_bases"""
        op_bases = values.get('op_bases', {})
        op_names = values.get('op_names', {})

        for op_name, op_config in op_names.items():
            if op_config.base not in op_bases:
                raise ValueError(
                    f"Operation '{op_name}' references unknown base '{op_config.base}'"
                )

        return values

    class Config:
        extra = "forbid"
```

**main.py 수정**:

```python
# Before
def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)

# After
from models import Config

def load_config(config_path: str) -> Config:
    """Load and validate config"""
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    try:
        return Config(**raw)
    except ValidationError as e:
        print(f"Config validation error:")
        print(e)
        sys.exit(1)

# Usage
config = load_config(args.config)
# config.topology.dies (type-safe access)
# config.op_bases["ERASE"].scope (validated)
```

**test_models.py**:

```python
import pytest
from pydantic import ValidationError
from models import Config, OperationBaseConfig, StateConfig

def test_valid_config():
    """Test valid config"""
    config_dict = {
        "topology": {"dies": 4, "planes": 2, "blocks_per_die": 1024, "pages_per_block": 256},
        "policies": {"admission_window": 10, "queue_refill_period_us": 100.0, "topN": 5},
        "features": {"suspend_resume_chain_enabled": True},
        "op_bases": {
            "ERASE": {
                "scope": "DIE_WIDE",
                "affect_state": True,
                "instant_resv": False,
                "states": {
                    "ISSUE": {"bus": True},
                    "CORE_BUSY": {"bus": False}
                }
            }
        },
        "op_names": {
            "ERASE_BLOCK": {
                "base": "ERASE",
                "celltype": "SLC",
                "durations": {"ISSUE": 5.0, "CORE_BUSY": 3000.0}
            }
        },
        "phase_conditional": {}
    }

    config = Config(**config_dict)
    assert config.topology.dies == 4
    assert config.op_bases["ERASE"].scope == "DIE_WIDE"

def test_invalid_topology():
    """Test invalid topology (negative dies)"""
    config_dict = {...}
    config_dict["topology"]["dies"] = -1

    with pytest.raises(ValidationError) as exc_info:
        Config(**config_dict)

    assert "dies" in str(exc_info.value)

def test_unknown_base_reference():
    """Test op_name referencing unknown base"""
    config_dict = {...}
    config_dict["op_names"]["INVALID_OP"] = {
        "base": "UNKNOWN_BASE",
        "celltype": "SLC"
    }

    with pytest.raises(ValidationError) as exc_info:
        Config(**config_dict)

    assert "unknown base" in str(exc_info.value).lower()

def test_multiple_bus_states():
    """Test rejection of multiple bus states"""
    base_config = {
        "scope": "DIE_WIDE",
        "affect_state": True,
        "instant_resv": False,
        "states": {
            "ISSUE": {"bus": True},
            "DATA_IN": {"bus": True}  # Invalid: 2 bus states
        }
    }

    with pytest.raises(ValidationError) as exc_info:
        OperationBaseConfig(**base_config)

    assert "Multiple bus states" in str(exc_info.value)
```

---

### 6.2 ValidationEngine (Phase 2)

**파일 구조**:
```
nandseqgen_v2/
├── validation_engine.py  # 새 파일
├── validation_rules.yaml # 새 파일
├── resourcemgr.py        # 수정
└── tests/
    └── test_validation.py # 새 파일
```

**validation_rules.yaml 템플릿**:

```yaml
rules:
  bus_exclusion:
    type: temporal_overlap
    description: "ISSUE/DATA_IN/DATA_OUT states cannot overlap globally"
    enabled: true
    scope: global
    resource: bus
    constraint: no_overlap
    states: [ISSUE, DATA_IN, DATA_OUT]

  die_wide_exclusion:
    type: resource_mutex
    description: "DIE_WIDE operations are mutually exclusive per die"
    enabled: true
    scope: die
    condition:
      operation.scope: DIE_WIDE
    states: [CORE_BUSY]
    constraint: exclusive

  busy_exclusion:
    type: state_conflict
    description: "CORE_BUSY cannot overlap on same plane"
    enabled: true
    scope: plane
    resource: busy
    constraint: no_overlap
    states: [CORE_BUSY]

  latch_exclusion:
    type: state_conflict
    description: "Latch operations cannot conflict by kind"
    enabled: true
    scope: plane
    resource: latch
    constraint: no_conflict_by_kind
    kinds: [READ, LSB, CSB, MSB]

  suspend_exclusion:
    type: precondition
    description: "Cannot start new operation while suspended"
    enabled: true
    scope: plane
    condition:
      resource_state.suspended: true
    constraint: reject
```

**validation_engine.py 템플릿**:

```python
"""
Validation engine with pluggable rules
"""
from typing import Protocol, List, Dict, Any
from dataclasses import dataclass
from enum import Enum
import yaml

@dataclass
class ValidationResult:
    """Result of validation"""
    ok: bool
    rule_name: str = ""
    reason: str = ""
    details: Dict[str, Any] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}

class ValidationRule(Protocol):
    """Protocol for validation rules"""

    def check(self, op: 'Operation', context: 'ValidationContext') -> ValidationResult:
        """Check if operation satisfies this rule"""
        ...

class TemporalOverlapRule:
    """Check for temporal overlap of states"""

    def __init__(self, config: dict):
        self.name = config.get('name', 'temporal_overlap')
        self.scope = config['scope']
        self.states = config['states']
        self.enabled = config.get('enabled', True)

    def check(self, op: 'Operation', context: 'ValidationContext') -> ValidationResult:
        if not self.enabled:
            return ValidationResult(ok=True)

        # Get affected resource based on scope
        if self.scope == 'global':
            resources = context.get_global_resources()
        elif self.scope == 'die':
            resources = context.get_die_resources(op.die)
        elif self.scope == 'plane':
            resources = context.get_plane_resources(op.die, op.plane)
        else:
            return ValidationResult(ok=False, reason=f"Unknown scope: {self.scope}")

        # Check each state in operation
        for state_name in self.states:
            if not op.has_state(state_name):
                continue

            interval = op.get_interval(state_name)

            # Check if any existing operation overlaps
            for existing_op in resources.get_active_operations():
                if not existing_op.has_state(state_name):
                    continue

                existing_interval = existing_op.get_interval(state_name)

                if self._intervals_overlap(interval, existing_interval):
                    return ValidationResult(
                        ok=False,
                        rule_name=self.name,
                        reason=f"{state_name} state overlaps with {existing_op.name}",
                        details={
                            'state': state_name,
                            'op_interval': interval,
                            'existing_interval': existing_interval,
                            'conflicting_op': existing_op.name
                        }
                    )

        return ValidationResult(ok=True)

    def _intervals_overlap(self, i1: tuple, i2: tuple) -> bool:
        """Check if two intervals (start, end) overlap"""
        start1, end1 = i1
        start2, end2 = i2
        return not (end1 <= start2 or end2 <= start1)

class ResourceMutexRule:
    """Check for resource mutex (exclusive access)"""

    def __init__(self, config: dict):
        self.name = config.get('name', 'resource_mutex')
        self.scope = config['scope']
        self.condition = config.get('condition', {})
        self.states = config['states']
        self.enabled = config.get('enabled', True)

    def check(self, op: 'Operation', context: 'ValidationContext') -> ValidationResult:
        if not self.enabled:
            return ValidationResult(ok=True)

        # Check if condition applies
        if not self._condition_matches(op, context):
            return ValidationResult(ok=True)

        # Get affected resource
        if self.scope == 'die':
            resources = context.get_die_resources(op.die)
        elif self.scope == 'plane':
            resources = context.get_plane_resources(op.die, op.plane)
        else:
            return ValidationResult(ok=False, reason=f"Unknown scope: {self.scope}")

        # Check for any conflicting operation
        for existing_op in resources.get_active_operations():
            if not self._condition_matches(existing_op, context):
                continue

            # Check state overlap
            for state_name in self.states:
                if op.has_state(state_name) and existing_op.has_state(state_name):
                    op_interval = op.get_interval(state_name)
                    existing_interval = existing_op.get_interval(state_name)

                    if self._intervals_overlap(op_interval, existing_interval):
                        return ValidationResult(
                            ok=False,
                            rule_name=self.name,
                            reason=f"Resource mutex violation with {existing_op.name}",
                            details={
                                'scope': self.scope,
                                'state': state_name,
                                'conflicting_op': existing_op.name
                            }
                        )

        return ValidationResult(ok=True)

    def _condition_matches(self, op: 'Operation', context: 'ValidationContext') -> bool:
        """Check if operation matches condition"""
        for key, value in self.condition.items():
            if '.' in key:
                obj_name, attr = key.split('.', 1)
                if obj_name == 'operation':
                    if not hasattr(op, attr) or getattr(op, attr) != value:
                        return False
        return True

    def _intervals_overlap(self, i1: tuple, i2: tuple) -> bool:
        start1, end1 = i1
        start2, end2 = i2
        return not (end1 <= start2 or end2 <= start1)

class PreconditionRule:
    """Check preconditions for operation"""

    def __init__(self, config: dict):
        self.name = config.get('name', 'precondition')
        self.scope = config['scope']
        self.condition = config.get('condition', {})
        self.constraint = config['constraint']
        self.enabled = config.get('enabled', True)

    def check(self, op: 'Operation', context: 'ValidationContext') -> ValidationResult:
        if not self.enabled:
            return ValidationResult(ok=True)

        # Get resource state
        if self.scope == 'plane':
            state = context.get_plane_state(op.die, op.plane)
        elif self.scope == 'die':
            state = context.get_die_state(op.die)
        else:
            return ValidationResult(ok=False, reason=f"Unknown scope: {self.scope}")

        # Check condition
        condition_met = self._check_condition(state)

        if condition_met and self.constraint == 'reject':
            return ValidationResult(
                ok=False,
                rule_name=self.name,
                reason=f"Precondition failed: {self.condition}",
                details={'condition': self.condition, 'state': state}
            )

        return ValidationResult(ok=True)

    def _check_condition(self, state: Any) -> bool:
        """Check if condition is met"""
        for key, value in self.condition.items():
            if '.' in key:
                obj_name, attr = key.split('.', 1)
                if obj_name == 'resource_state':
                    if not hasattr(state, attr) or getattr(state, attr) != value:
                        return False
        return True

# Rule registry
RULE_TYPES = {
    'temporal_overlap': TemporalOverlapRule,
    'resource_mutex': ResourceMutexRule,
    'precondition': PreconditionRule,
    'state_conflict': TemporalOverlapRule,  # Reuse temporal_overlap
}

class ValidationEngine:
    """Main validation engine"""

    def __init__(self, rules_config_path: str):
        """Load rules from YAML config"""
        with open(rules_config_path) as f:
            config = yaml.safe_load(f)

        self.rules: List[ValidationRule] = []

        for rule_name, rule_cfg in config['rules'].items():
            rule_cfg['name'] = rule_name
            rule_type = rule_cfg['type']

            if rule_type not in RULE_TYPES:
                raise ValueError(f"Unknown rule type: {rule_type}")

            rule_cls = RULE_TYPES[rule_type]
            self.rules.append(rule_cls(rule_cfg))

    def validate(self, op: 'Operation', context: 'ValidationContext') -> ValidationResult:
        """Run all validation rules"""
        for rule in self.rules:
            result = rule.check(op, context)
            if not result.ok:
                return result

        return ValidationResult(ok=True)

    def get_rule_names(self) -> List[str]:
        """Get all rule names"""
        return [getattr(rule, 'name', type(rule).__name__) for rule in self.rules]
```

**resourcemgr.py 수정**:

```python
# Before
class ResourceManager:
    def reserve(self, op):
        if not self._check_bus_exclusion(op):
            return Reservation(ok=False, reason="bus")
        if not self._check_busy_exclusion(op):
            return Reservation(ok=False, reason="busy")
        # ...

# After
from validation_engine import ValidationEngine, ValidationContext

class ResourceManager:
    def __init__(self, topology, validation_rules_path: str = "validation_rules.yaml"):
        self.validation_engine = ValidationEngine(validation_rules_path)
        # ... existing initialization

    def reserve(self, op):
        # Create validation context
        context = self._make_validation_context()

        # Run validation engine
        result = self.validation_engine.validate(op, context)

        if not result.ok:
            return Reservation(
                ok=False,
                reason=result.reason,
                rule_name=result.rule_name,
                details=result.details
            )

        # ... existing reservation logic

    def _make_validation_context(self) -> ValidationContext:
        """Create validation context from current state"""
        return ValidationContext(
            global_resources=self._get_global_resources(),
            die_resources={die: self._get_die_resources(die)
                          for die in range(self.topology.dies)},
            plane_states=self.plane_states,
            # ...
        )
```

---

### 6.3 State Machine (Phase 2)

**파일 구조**:
```
nandseqgen_v2/
├── state_machine.py     # 새 파일
├── resourcemgr.py       # 수정
└── tests/
    └── test_state_machine.py # 새 파일
```

**state_machine.py 템플릿**:

```python
"""
State machine for NAND resource lifecycle
"""
from statemachine import StateMachine, State
from typing import Optional
import logging

logger = logging.getLogger(__name__)

class PlaneLifecycle(StateMachine):
    """
    Plane resource lifecycle state machine

    States:
    - ready: Ready for new operation
    - erasing: Erase operation in progress
    - programming: Program operation in progress
    - reading: Read operation in progress
    - suspended_erase: Erase operation suspended
    - suspended_program: Program operation suspended
    """

    # States
    ready = State("Ready", initial=True)
    erasing = State("Erasing")
    programming = State("Programming")
    reading = State("Reading")
    suspended_erase = State("Suspended (Erase)")
    suspended_program = State("Suspended (Program)")

    # Transitions
    start_erase = ready.to(erasing)
    start_program = ready.to(programming)
    start_read = ready.to(reading)

    suspend_erase = erasing.to(suspended_erase)
    suspend_program = programming.to(suspended_program)

    resume_erase = suspended_erase.to(erasing)
    resume_program = suspended_program.to(programming)

    complete_erase = erasing.to(ready)
    complete_program = programming.to(ready)
    complete_read = reading.to(ready)

    complete_from_suspended_erase = suspended_erase.to(ready)
    complete_from_suspended_program = suspended_program.to(ready)

    # Guards (validation)
    def before_start_erase(self, event_data):
        """Validate before starting erase"""
        if self.current_state != self.ready:
            raise InvalidStateTransition(
                f"Cannot erase from state {self.current_state.name}"
            )

    def before_start_program(self, event_data):
        """Validate before starting program"""
        if self.current_state != self.ready:
            raise InvalidStateTransition(
                f"Cannot program from state {self.current_state.name}"
            )

    def before_suspend_erase(self, event_data):
        """Validate before suspending erase"""
        if self.current_state != self.erasing:
            raise InvalidStateTransition(
                "No erase operation to suspend"
            )

    def before_suspend_program(self, event_data):
        """Validate before suspending program"""
        if self.current_state != self.programming:
            raise InvalidStateTransition(
                "No program operation to suspend"
            )

    def before_resume_erase(self, event_data):
        """Validate before resuming erase"""
        if self.current_state != self.suspended_erase:
            raise InvalidStateTransition(
                "No suspended erase to resume"
            )

    def before_resume_program(self, event_data):
        """Validate before resuming program"""
        if self.current_state != self.suspended_program:
            raise InvalidStateTransition(
                "No suspended program to resume"
            )

    # Callbacks
    def on_enter_erasing(self, event_data):
        """Callback when entering erasing state"""
        logger.info(f"Plane {self.plane_id} entering ERASE state")
        if hasattr(self, 'metrics'):
            self.metrics.increment("erase_count")

    def on_enter_programming(self, event_data):
        """Callback when entering programming state"""
        logger.info(f"Plane {self.plane_id} entering PROGRAM state")
        if hasattr(self, 'metrics'):
            self.metrics.increment("program_count")

    def on_exit_suspended_erase(self, event_data):
        """Callback when exiting suspended_erase state"""
        logger.info(f"Plane {self.plane_id} resuming from erase suspend")

    def on_exit_suspended_program(self, event_data):
        """Callback when exiting suspended_program state"""
        logger.info(f"Plane {self.plane_id} resuming from program suspend")

    def on_enter_ready(self, event_data):
        """Callback when entering ready state"""
        logger.debug(f"Plane {self.plane_id} ready for new operation")

    def __init__(self, plane_id: str, metrics=None):
        """Initialize state machine"""
        self.plane_id = plane_id
        self.metrics = metrics
        self.current_operation: Optional['Operation'] = None
        super().__init__()

class InvalidStateTransition(Exception):
    """Exception for invalid state transition"""
    pass
```

**resourcemgr.py 수정**:

```python
from state_machine import PlaneLifecycle, InvalidStateTransition

class ResourceManager:
    def __init__(self, topology, ...):
        # ... existing initialization

        # Initialize state machines for all planes
        self.plane_state_machines = {
            (die, plane): PlaneLifecycle(
                plane_id=f"D{die}P{plane}",
                metrics=self.metrics if hasattr(self, 'metrics') else None
            )
            for die in range(topology.dies)
            for plane in range(topology.planes)
        }

    def reserve_erase(self, die: int, plane: int, op: 'Operation'):
        """Reserve erase operation with state machine validation"""
        sm = self.plane_state_machines[(die, plane)]

        try:
            sm.start_erase()  # ← Automatically validates state transition
        except InvalidStateTransition as e:
            return Reservation(ok=False, reason=str(e))

        # ... existing reservation logic
        sm.current_operation = op
        return Reservation(ok=True)

    def reserve_program(self, die: int, plane: int, op: 'Operation'):
        """Reserve program operation with state machine validation"""
        sm = self.plane_state_machines[(die, plane)]

        try:
            sm.start_program()
        except InvalidStateTransition as e:
            return Reservation(ok=False, reason=str(e))

        sm.current_operation = op
        return Reservation(ok=True)

    def suspend_operation(self, die: int, plane: int, op_type: str):
        """Suspend ongoing operation"""
        sm = self.plane_state_machines[(die, plane)]

        try:
            if op_type == "ERASE":
                sm.suspend_erase()
            elif op_type == "PROGRAM":
                sm.suspend_program()
            else:
                return Reservation(ok=False, reason=f"Unknown op type: {op_type}")
        except InvalidStateTransition as e:
            return Reservation(ok=False, reason=str(e))

        return Reservation(ok=True)

    def resume_operation(self, die: int, plane: int, op_type: str):
        """Resume suspended operation"""
        sm = self.plane_state_machines[(die, plane)]

        try:
            if op_type == "ERASE":
                sm.resume_erase()
            elif op_type == "PROGRAM":
                sm.resume_program()
            else:
                return Reservation(ok=False, reason=f"Unknown op type: {op_type}")
        except InvalidStateTransition as e:
            return Reservation(ok=False, reason=str(e))

        return Reservation(ok=True)

    def complete_operation(self, die: int, plane: int):
        """Complete current operation"""
        sm = self.plane_state_machines[(die, plane)]

        try:
            if sm.current_state == sm.erasing:
                sm.complete_erase()
            elif sm.current_state == sm.programming:
                sm.complete_program()
            elif sm.current_state == sm.reading:
                sm.complete_read()
            elif sm.current_state == sm.suspended_erase:
                sm.complete_from_suspended_erase()
            elif sm.current_state == sm.suspended_program:
                sm.complete_from_suspended_program()
            else:
                logger.warning(f"Unexpected state: {sm.current_state.name}")
        except InvalidStateTransition as e:
            logger.error(f"Failed to complete operation: {e}")

        sm.current_operation = None

    def get_plane_state(self, die: int, plane: int) -> str:
        """Get current state of plane"""
        sm = self.plane_state_machines[(die, plane)]
        return sm.current_state.name
```

**test_state_machine.py**:

```python
import pytest
from state_machine import PlaneLifecycle, InvalidStateTransition

def test_normal_erase_cycle():
    """Test normal erase lifecycle"""
    sm = PlaneLifecycle(plane_id="test")

    assert sm.current_state == sm.ready

    sm.start_erase()
    assert sm.current_state == sm.erasing

    sm.complete_erase()
    assert sm.current_state == sm.ready

def test_erase_suspend_resume():
    """Test erase suspend and resume"""
    sm = PlaneLifecycle(plane_id="test")

    sm.start_erase()
    sm.suspend_erase()
    assert sm.current_state == sm.suspended_erase

    sm.resume_erase()
    assert sm.current_state == sm.erasing

    sm.complete_erase()
    assert sm.current_state == sm.ready

def test_invalid_transition_erase_from_programming():
    """Test that erase from programming state is rejected"""
    sm = PlaneLifecycle(plane_id="test")

    sm.start_program()

    with pytest.raises(InvalidStateTransition):
        sm.start_erase()

def test_invalid_suspend_when_not_busy():
    """Test that suspend when ready is rejected"""
    sm = PlaneLifecycle(plane_id="test")

    with pytest.raises(InvalidStateTransition):
        sm.suspend_erase()

def test_program_cycle():
    """Test program lifecycle"""
    sm = PlaneLifecycle(plane_id="test")

    sm.start_program()
    assert sm.current_state == sm.programming

    sm.suspend_program()
    assert sm.current_state == sm.suspended_program

    sm.resume_program()
    assert sm.current_state == sm.programming

    sm.complete_program()
    assert sm.current_state == sm.ready
```

---

이 가이드는 Phase 1-2의 핵심 구현을 위한 draft 코드를 제공합니다. Phase 3-4는 필요 시 별도 문서로 확장할 수 있습니다.

---

## 마무리

이 문서는 4개의 전문가 제안서를 종합하여 다음을 제공합니다:

1. **공통 구조적 문제점**: 7개 핵심 문제 식별
2. **문제-솔루션 매핑**: 각 문제에 대한 구체적 솔루션과 구현 방법
3. **Python 패키지 컨센서스**: 우선순위별 패키지 추천
4. **상충점 및 통합 전략**: 솔루션 간 충돌 해결 방안
5. **구현 로드맵**: Phase별 구체적 작업 항목과 일정
6. **Draft 코드 가이드**: 즉시 사용 가능한 코드 템플릿

**다음 단계**:
1. 팀과 이 문서를 공유하여 피드백 수집
2. Phase 1 Pydantic PoC 작성 (1-2일 소요)
3. PoC 결과 리뷰 후 본격 진행 여부 결정
4. Phase 1 작업 항목을 Sprint backlog에 추가

**성공 지표**:
- Phase 1 완료 후: Config 에러가 런타임이 아닌 로딩 시점에 발견됨
- Phase 2 완료 후: 새 validation 규칙 추가 시간 1일 → 1시간
- Phase 3 완료 후: 새 operation 추가 시 수정 파일 수 5개 → 1개

이 문서는 실제 구현에 직접 활용할 수 있도록 설계되었습니다.
