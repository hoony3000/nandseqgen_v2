# NANDSEQGEN_V2 리팩토링 제안: 전문가 인터뷰

> **작성일**: 2025-10-26
> **목적**: Operation 추가/수정/제거 시 resource 관리 및 sequence validation을 용이하게 하기 위한 구조 개선 방안

---

## 목차

1. [코드베이스 개략적인 역할 설명](#1-코드베이스-개략적인-역할-설명)
2. [전문가 패널 구성](#2-전문가-패널-구성)
3. [전문가 인터뷰](#3-전문가-인터뷰)
   - [Interview 1: Dr. Sarah Chen (Architecture Expert)](#-interview-1-dr-sarah-chen-architecture-expert)
   - [Interview 2: Prof. Michael Torres (Formal Verification Expert)](#-interview-2-prof-michael-torres-formal-verification-expert)
   - [Interview 3: Alex Kumar (Python Tooling Expert)](#-interview-3-alex-kumar-python-tooling-expert)
4. [인터뷰 종합 및 최종 권고안](#4-인터뷰-종합-및-최종-권고안)
5. [추천 Python 패키지 요약표](#5-추천-python-패키지-요약표)
6. [실용적 마이그레이션 로드맵](#6-실용적-마이그레이션-로드맵)
7. [최종 권장사항](#7-최종-권장사항)

---

## 1. 코드베이스 개략적인 역할 설명

**NANDSEQGEN_V2**는 NAND 플래시 메모리의 operation sequence를 **확률적으로 생성**하는 시뮬레이터입니다:

- **핵심 메커니즘**: 현재 NAND resource state(op_state, latch_state, suspend_state 등)에 따라 실행 가능한 operation 후보들을 확률 분포로부터 샘플링하여 예약
- **Resource 관리**: Plane/Die/Global 레벨의 계층적 resource tracking, bus/latch/suspend 등 다양한 exclusion rule 검증
- **Validation**: 예약 시점(reservation-time)과 제안 시점(proposal-time)에 걸친 다단계 검증 체계
- **확장성 과제**: Operation 추가/수정/제거 시 config.yaml, resourcemgr.py, proposer.py, addrman.py 등 여러 파일에 걸친 수정 필요

### 핵심 구성요소

**아키텍처 구조:**
```
config.yaml (operation & policy definitions)
    ↓
AddressManager (NumPy-based address sampling)
    ↓
ResourceManager (state & resource tracking)
    ↓
Scheduler (orchestration & event queue)
    ↓
Proposer (probabilistic operation selection)
    ↓
CSV exports (timeline, operation sequence, state tracking)
```

**주요 파일:**
- `main.py` (1,530 lines): Entry point, CLI handling, config loading, CSV export orchestration
- `scheduler.py` (1,568 lines): Main event loop, operation scheduling, suspend/resume handling
- `proposer.py` (1,795 lines): Probabilistic operation sampling, phase-conditional distributions
- `resourcemgr.py` (2,000+ lines): NAND resource state machine, conflict validation
- `addrman.py`: NumPy-based address sampler for E/P/R operations
- `event_queue.py`: Time-ordered event priority queue
- `bootstrap.py`: Bootstrap progression tracking

### 현재의 문제점

하나의 operation을 추가하려면:
1. `config.yaml`에 op_base와 op_name 정의
2. `resourcemgr.py`에서 scope에 따른 validation 로직 확인
3. `proposer.py`에서 address inheritance 규칙 추가
4. `addrman.py`에서 celltype 샘플링 로직 확인
5. `phase_conditional` 확률 분포 업데이트

이는 **관심사의 분리(Separation of Concerns) 부족**으로 인한 유지보수성 저하를 의미합니다.

---

## 2. 전문가 패널 구성

이 문제를 해결하기 위해 다음 3명의 전문가를 선정했습니다:

### 🎓 Dr. Sarah Chen
- **전문 분야**: Software Architecture & Design Patterns
- **Topic**: 유지보수성, 확장성, 구조적 리팩토링
- **Role**: 현재 아키텍처의 취약점 분석 및 개선 방향 제시
- **Perspective**: "Operation 추가가 5개 파일 수정을 요구한다면 설계가 잘못된 것"

### 🎓 Prof. Michael Torres
- **전문 분야**: Formal Verification & Constraint Solving
- **Topic**: Validation 자동화, 제약 조건 모델링
- **Role**: Resource 제약과 validation 규칙을 선언적으로 관리하는 방법론 제시
- **Perspective**: "Validation 규칙이 코드에 하드코딩되어 있으면 검증 불가능"

### 🎓 Alex Kumar
- **전문 분야**: Python Ecosystem & Tooling
- **Topic**: Python 라이브러리, 도구, 실용적 구현 전략
- **Role**: 구체적인 Python 패키지 추천 및 마이그레이션 전략
- **Perspective**: "바퀴를 재발명하지 말고 검증된 라이브러리를 활용하라"

---

## 3. 전문가 인터뷰

## 🎤 Interview 1: Dr. Sarah Chen (Architecture Expert)

### Q: Dr. Chen, 현재 코드베이스에서 operation 추가/수정 시 가장 큰 문제점은 무엇인가요?

**Dr. Chen**: 제가 보기에 가장 심각한 문제는 **관심사의 분리(Separation of Concerns) 부족**입니다.

현재 시스템에서 하나의 operation을 추가하려면:
1. `config.yaml`에 op_base와 op_name 정의
2. `resourcemgr.py`에서 scope에 따른 validation 로직 확인
3. `proposer.py`에서 address inheritance 규칙 추가
4. `addrman.py`에서 celltype 샘플링 로직 확인
5. `phase_conditional` 확률 분포 업데이트

이는 **God Object Anti-pattern**의 전형입니다. ResourceManager가 2000+ 라인으로 모든 validation을 담당하고 있죠.

### Q: 구체적으로 어떤 구조적 문제가 있나요?

**Dr. Chen**: 세 가지 핵심 문제가 있습니다:

#### 1. Operation 정의의 암묵적 계약

```yaml
# config.yaml
op_bases:
  MY_NEW_OP:
    scope: "DIE_WIDE"  # ← 이 값이 resourcemgr.py의 if문에 하드코딩됨
    states: [ISSUE, CORE_BUSY]  # ← 순서와 타입이 코드에 암묵적으로 가정됨
```

Operation schema가 명시적으로 검증되지 않아서, 잘못된 설정을 런타임에야 발견합니다.

#### 2. Validation 규칙의 절차적 결합

```python
# resourcemgr.py (simplified)
def reserve(self, op):
    if not self._check_bus_exclusion(op):
        return Reservation(ok=False, reason="bus")
    if not self._check_busy_exclusion(op):
        return Reservation(ok=False, reason="busy")
    # ... 8개 이상의 if문이 순차적으로 연결됨
```

새로운 규칙을 추가하려면 reserve() 메서드를 직접 수정해야 합니다. **Open/Closed Principle 위반**입니다.

#### 3. State 변경의 분산된 로직

```python
# scheduler.py
def _handle_op_end(self, op):
    if op.base in program_bases:
        self._am.apply_pgm(...)  # ← AddressManager 업데이트
    self._rm.update_state(...)    # ← ResourceManager 업데이트
    if op.is_resume:
        self._handle_resume_logic(...)  # ← Backlog 처리
```

Operation 종료 시 state 변경이 3곳에 분산되어 있어 일관성 유지가 어렵습니다.

### Q: 어떤 아키텍처 패턴을 추천하시나요?

**Dr. Chen**: **Plugin Architecture + Strategy Pattern** 조합을 추천합니다:

```python
# 개선안 스케치
class Operation(Protocol):
    """Operation의 명시적 인터페이스"""
    def get_resource_requirements(self) -> ResourceRequirements
    def validate_preconditions(self, ctx: ValidationContext) -> ValidationResult
    def apply_state_changes(self, ctx: StateContext) -> StateChanges

class OperationRegistry:
    """Operation 동적 등록/조회"""
    def register(self, op_type: str, op_class: Type[Operation])
    def create(self, op_type: str, params: dict) -> Operation

class ValidationRule(Protocol):
    """확장 가능한 validation 규칙"""
    def check(self, op: Operation, ctx: Context) -> RuleResult

class ResourceManager:
    def __init__(self, rules: List[ValidationRule]):
        self.rules = rules  # ← 런타임에 규칙 주입 가능

    def reserve(self, op: Operation):
        for rule in self.rules:
            result = rule.check(op, self.context)
            if not result.ok:
                return Reservation(ok=False, reason=result.reason)
```

**장점**:
- **새 operation 추가**: Operation 클래스만 구현하고 Registry에 등록
- **새 validation 규칙 추가**: ValidationRule 구현하고 RM 생성 시 주입
- **테스트 용이성**: Mock rule/operation으로 단위 테스트 가능

### Q: 기존 코드베이스를 마이그레이션하는 현실적인 전략은?

**Dr. Chen**: **Strangler Fig Pattern**을 추천합니다:

#### Phase 1: Operation abstraction 도입
- `BaseOperation` 추상 클래스 생성
- config.yaml 파서가 BaseOperation 인스턴스 생성
- 기존 dict 기반 로직은 유지하되 점진적으로 마이그레이션

#### Phase 2: ValidationRule 인터페이스 도입
- `BusExclusionRule`, `BusyExclusionRule` 등을 별도 클래스로 분리
- ResourceManager는 레거시 코드와 새 rule 체계 동시 지원

#### Phase 3: 완전 마이그레이션
- 모든 operation이 클래스 기반으로 전환되면 레거시 코드 제거

이렇게 하면 **점진적 마이그레이션**이 가능하고 각 단계마다 테스트로 검증할 수 있습니다.

---

## 🎤 Interview 2: Prof. Michael Torres (Formal Verification Expert)

### Q: Professor Torres, validation 측면에서 현재 시스템의 문제점은?

**Prof. Torres**: 가장 큰 문제는 **Validation 규칙이 암묵적이고 검증 불가능**하다는 점입니다.

예를 들어 "DIE_WIDE operation은 동일 die의 다른 CORE_BUSY와 충돌한다"는 규칙이 코드 곳곳에 분산되어 있습니다:
- `resourcemgr.py`의 `_check_busy_exclusion()` 메서드
- `proposer.py`의 state blocking 로직
- `scheduler.py`의 PHASE_HOOK 생성 조건

이는 **중복된 진실의 원천(Multiple Sources of Truth)**을 만들어 불일치를 초래합니다.

### Q: 어떻게 개선할 수 있을까요?

**Prof. Torres**: **선언적 제약 조건 모델링(Declarative Constraint Modeling)**을 도입해야 합니다.

두 가지 접근법이 있습니다:

#### 접근법 1: Constraint Solver 활용

```python
from constraint import Problem, AllDifferentConstraint

class ResourceConstraintSolver:
    def __init__(self, topology):
        self.problem = Problem()

    def add_operation_constraints(self, ops):
        # Bus exclusion: time intervals must not overlap
        for op1, op2 in combinations(ops, 2):
            if both_use_bus(op1, op2):
                self.problem.addConstraint(
                    lambda t1, t2: not intervals_overlap(t1, t2),
                    (op1.time_var, op2.time_var)
                )

        # Die-wide exclusion
        for die_id in range(self.topology.dies):
            die_ops = [op for op in ops if op.targets_die(die_id)]
            self.problem.addConstraint(
                AllDifferentConstraint(),
                [op.time_var for op in die_ops]
            )

    def solve(self) -> Optional[Schedule]:
        solution = self.problem.getSolution()
        return Schedule(solution) if solution else None
```

**장점**:
- 제약 조건을 **선언적으로 정의**
- Solver가 자동으로 feasible schedule 탐색
- 새 규칙 추가 = `addConstraint()` 호출만 추가

**단점**:
- 확률적 샘플링과의 통합이 복잡
- 성능 이슈 (constraint solving은 NP-complete)

#### 접근법 2: Rule Engine with DSL

```yaml
# rules.yaml (선언적 규칙 정의)
validation_rules:
  - name: bus_exclusion
    type: interval_overlap
    scope: global
    condition:
      - operation.states contains {bus: true}
    constraint:
      - no_overlap(op1.bus_intervals, op2.bus_intervals)

  - name: die_wide_exclusion
    type: resource_mutex
    scope: die
    condition:
      - operation.scope == "DIE_WIDE"
    constraint:
      - exclusive_access(die_resource, during=op.core_busy)
```

```python
# Python runtime
class RuleEngine:
    def __init__(self, rules_config):
        self.rules = [parse_rule(r) for r in rules_config]

    def validate(self, op: Operation, context: Context) -> ValidationResult:
        for rule in self.rules:
            if rule.applies_to(op, context):
                result = rule.evaluate(op, context)
                if not result.ok:
                    return result
        return ValidationResult(ok=True)
```

**장점**:
- **비프로그래머도 읽을 수 있는** 규칙 정의
- 규칙 추가/수정 시 Python 코드 변경 불필요
- 규칙 간 독립성 보장

### Q: Sequence validation은 어떻게 자동화할 수 있나요?

**Prof. Torres**: **State Machine Verification + Property-Based Testing**을 추천합니다:

```python
from hypothesis import given, strategies as st
from statemachine import StateMachine, State

class NANDStateMachine(StateMachine):
    """NAND resource의 정상 상태 전이 모델"""
    ready = State(initial=True)
    erasing = State()
    programming = State()
    suspended = State()

    erase = ready.to(erasing)
    program = ready.to(programming)
    suspend_erase = erasing.to(suspended)
    resume_erase = suspended.to(erasing)
    complete = erasing.to(ready) | programming.to(ready)

# Property-based test
@given(st.lists(st.sampled_from(["ERASE", "PROGRAM", "SUSPEND", "RESUME"])))
def test_sequence_validity(operations):
    """생성된 sequence가 state machine을 위반하지 않는지 검증"""
    state_machine = NANDStateMachine()

    for op in operations:
        try:
            state_machine.send(op.lower())
        except TransitionNotAllowed:
            assert False, f"Invalid transition: {op} in state {state_machine.current_state}"
```

### Q: 현재 시스템에 점진적으로 도입하는 방법은?

**Prof. Torres**: 다음 단계를 추천합니다:

#### Step 1: Validation 규칙을 YAML로 외부화

```yaml
# validation_rules.yaml
bus_exclusion:
  type: temporal_overlap
  resource: bus
  states: [ISSUE, DATA_IN, DATA_OUT]
  policy: no_overlap

die_wide_exclusion:
  type: resource_mutex
  resource: die
  condition: {scope: DIE_WIDE}
  states: [CORE_BUSY]
  policy: exclusive
```

#### Step 2: Rule interpreter 구현

```python
class ValidationRuleInterpreter:
    def load_rules(self, yaml_path):
        self.rules = yaml.safe_load(open(yaml_path))

    def check_rule(self, rule_name, op, context):
        rule = self.rules[rule_name]
        if rule['type'] == 'temporal_overlap':
            return self._check_temporal_overlap(rule, op, context)
        elif rule['type'] == 'resource_mutex':
            return self._check_resource_mutex(rule, op, context)
```

#### Step 3: 기존 validation 코드를 interpreter 호출로 대체

```python
# Before
def reserve(self, op):
    if not self._check_bus_exclusion(op):  # ← 하드코딩된 로직
        return False

# After
def reserve(self, op):
    result = self.rule_interpreter.check_rule("bus_exclusion", op, self.context)
    if not result.ok:
        return False
```

이렇게 하면 **규칙 정의와 구현이 분리**되어 검증 가능성이 높아집니다.

---

## 🎤 Interview 3: Alex Kumar (Python Tooling Expert)

### Q: Alex, 이 문제를 해결할 수 있는 구체적인 Python 패키지를 추천해주세요.

**Alex**: 물론입니다! 용도별로 추천 패키지를 정리했습니다:

### 1. Operation Schema 정의 및 Validation

#### Pydantic ⭐⭐⭐⭐⭐

```python
from pydantic import BaseModel, Field, validator
from typing import Literal, List

class OperationState(BaseModel):
    name: Literal["ISSUE", "CORE_BUSY", "DATA_IN", "DATA_OUT"]
    bus: bool
    duration: float = Field(gt=0)

class OperationBase(BaseModel):
    scope: Literal["DIE_WIDE", "PLANE_SET", "NONE"]
    affect_state: bool
    instant_resv: bool
    states: List[OperationState]

    @validator('states')
    def validate_states(cls, v):
        if not v:
            raise ValueError("states cannot be empty")
        # bus=True state는 최대 1개
        if sum(s.bus for s in v) > 1:
            raise ValueError("Only one state can have bus=True")
        return v

class Operation(BaseModel):
    name: str
    base: str
    celltype: Literal["SLC", "TLC", "QLC"]
    multi: bool
    durations: dict[str, float]

    class Config:
        extra = "forbid"  # Unknown fields 금지

# config.yaml 파싱 시 자동 검증
operations = [Operation(**op_dict) for op_dict in config['op_names'].values()]
# ↑ 잘못된 필드가 있으면 명확한 에러 메시지와 함께 즉시 실패
```

**장점**:
- **런타임 타입 검증**: config 로딩 시점에 잘못된 operation 정의 즉시 발견
- **IDE 자동완성**: Type hints로 개발 생산성 향상
- **자동 문서 생성**: JSON schema 자동 생성

#### attrs (경량 대안)

```python
import attr
from attr.validators import instance_of, in_

@attr.s(auto_attribs=True, frozen=True)  # Immutable operation
class Operation:
    name: str
    scope: str = attr.ib(validator=in_(["DIE_WIDE", "PLANE_SET", "NONE"]))
    duration: float = attr.ib(validator=instance_of(float))
```

### 2. Constraint Solving & Validation

#### python-constraint ⭐⭐⭐

```python
from constraint import Problem, FunctionConstraint

def schedule_operations(ops, max_time):
    problem = Problem()

    # Variable: 각 operation의 시작 시각
    for op in ops:
        problem.addVariable(op.id, range(0, max_time))

    # Constraint: Bus 점유 충돌 방지
    def no_bus_overlap(t1, t2, op1, op2):
        end1 = t1 + op1.bus_duration
        end2 = t2 + op2.bus_duration
        return end1 <= t2 or end2 <= t1

    for op1, op2 in combinations(ops, 2):
        if op1.uses_bus and op2.uses_bus:
            problem.addConstraint(
                FunctionConstraint(lambda t1, t2: no_bus_overlap(t1, t2, op1, op2)),
                (op1.id, op2.id)
            )

    return problem.getSolutions()
```

**한계**: 확률적 샘플링과 통합이 어렵고 large-scale에서 느림

#### Z3 Solver (SMT) ⭐⭐⭐⭐

```python
from z3 import Int, Solver, And, Or, Implies

def verify_schedule_constraints(ops):
    s = Solver()

    # Time variables
    start_times = {op: Int(f"t_{op.id}") for op in ops}

    # Constraint: 시간은 음수가 아님
    for t in start_times.values():
        s.add(t >= 0)

    # Constraint: DIE_WIDE ops는 같은 die에서 시간 겹침 없음
    for die in range(NUM_DIES):
        die_ops = [op for op in ops if op.die == die and op.scope == "DIE_WIDE"]
        for op1, op2 in combinations(die_ops, 2):
            s.add(Or(
                start_times[op1] + op1.duration <= start_times[op2],
                start_times[op2] + op2.duration <= start_times[op1]
            ))

    # Check satisfiability
    if s.check() == sat:
        model = s.model()
        return {op: model[start_times[op]].as_long() for op in ops}
    else:
        return None  # UNSAT: 제약 조건 위반
```

**장점**:
- **수학적 증명**: Schedule이 모든 제약을 만족하는지 증명 가능
- **Unsat core**: 어떤 제약들이 충돌하는지 진단 가능

### 3. State Machine & Workflow

#### python-statemachine ⭐⭐⭐⭐

```python
from statemachine import StateMachine, State

class PlaneStateMachine(StateMachine):
    ready = State("Ready", initial=True)
    erasing = State("Erasing")
    programming = State("Programming")
    suspended_erase = State("Suspended (Erase)")
    suspended_program = State("Suspended (Program)")

    # Transitions
    start_erase = ready.to(erasing)
    start_program = ready.to(programming)

    suspend_erase = erasing.to(suspended_erase)
    suspend_program = programming.to(suspended_program)

    resume_erase = suspended_erase.to(erasing)
    resume_program = suspended_program.to(programming)

    complete_erase = erasing.to(ready) | suspended_erase.to(ready)
    complete_program = programming.to(ready) | suspended_program.to(ready)

    # Hooks
    def on_enter_erasing(self):
        print("Plane entering ERASE state")

    def before_suspend_erase(self):
        if not self.can_suspend():
            raise ValueError("Cannot suspend: no active operation")

# Usage
plane = PlaneStateMachine()
plane.start_erase()
plane.suspend_erase()
plane.resume_erase()
```

**장점**:
- **명시적 상태 전이**: Illegal transition 자동 방지
- **Lifecycle hooks**: State 진입/퇴출 시 자동 로직 실행
- **Visualization**: State diagram 자동 생성

#### transitions (대안)

```python
from transitions import Machine

class NANDResource:
    states = ['ready', 'busy', 'suspended']

    def __init__(self):
        self.machine = Machine(model=self, states=NANDResource.states, initial='ready')
        self.machine.add_transition('erase', 'ready', 'busy', before='validate_erase')
        self.machine.add_transition('suspend', 'busy', 'suspended')
```

### 4. Configuration Management

#### OmegaConf ⭐⭐⭐⭐⭐

```python
from omegaconf import OmegaConf

# config.yaml을 구조화된 객체로 로딩
cfg = OmegaConf.load("config.yaml")

# Type-safe access with autocompletion
num_dies = cfg.topology.dies  # IDE가 자동완성 지원

# Schema 정의 및 검증
from dataclasses import dataclass

@dataclass
class TopologyConfig:
    dies: int
    planes: int
    blocks_per_die: int

@dataclass
class Config:
    topology: TopologyConfig

# Merge & override
base_cfg = OmegaConf.load("base_config.yaml")
override_cfg = OmegaConf.load("experiment_config.yaml")
merged = OmegaConf.merge(base_cfg, override_cfg)

# Variable interpolation
cfg = OmegaConf.create({
    "dir": "/tmp",
    "output": "${dir}/output.csv"  # Automatic expansion
})
```

**장점**:
- **계층적 설정**: Config 상속 및 오버라이드
- **타입 검증**: Pydantic/attrs/dataclass 통합
- **CLI 통합**: Hydra로 command-line override 지원

#### Dynaconf (대안)

```python
from dynaconf import Dynaconf

settings = Dynaconf(
    envvar_prefix="NAND",  # NAND_TOPOLOGY__DIES=4 같은 env var 지원
    settings_files=["config.yaml", ".secrets.yaml"],
    environments=True  # dev/staging/prod 환경별 설정
)
```

### 5. Rule Engine

#### business-rules ⭐⭐⭐

```python
from business_rules import run_all
from business_rules.variables import BaseVariables, rule_variable
from business_rules.actions import BaseActions, rule_action
from business_rules.fields import FIELD_NUMERIC

class OperationVariables(BaseVariables):
    def __init__(self, operation, context):
        self.operation = operation
        self.context = context

    @rule_variable(FIELD_NUMERIC)
    def die_busy_count(self):
        return self.context.count_busy_planes(self.operation.die)

    @rule_variable()
    def is_die_wide(self):
        return self.operation.scope == "DIE_WIDE"

class ValidationActions(BaseActions):
    @rule_action()
    def reject_operation(self, reason):
        raise ValidationError(reason)

# rules.json
rules = [
    {
        "conditions": {
            "all": [
                {"name": "is_die_wide", "operator": "is_true"},
                {"name": "die_busy_count", "operator": "greater_than", "value": 0}
            ]
        },
        "actions": [
            {"name": "reject_operation", "params": {"reason": "DIE_WIDE conflict"}}
        ]
    }
]

# Execute
run_all(rule_list=rules,
        defined_variables=OperationVariables(op, ctx),
        defined_actions=ValidationActions())
```

**장점**: JSON/YAML로 규칙 정의, 동적 로딩 가능

#### durable_rules ⭐⭐⭐⭐

```python
from durable.lang import ruleset, when_all, m

with ruleset('resource_validation'):
    @when_all(
        (m.operation.scope == "DIE_WIDE") &
        (m.context.die_busy == True)
    )
    def die_wide_conflict(c):
        c.assert_fact({'validation_error': 'DIE_WIDE operation blocked'})

    @when_all(
        (m.operation.uses_bus == True) &
        (m.context.bus_occupied == True)
    )
    def bus_conflict(c):
        c.assert_fact({'validation_error': 'Bus occupied'})

# Post facts
post('resource_validation', {
    'operation': {'scope': 'DIE_WIDE', 'uses_bus': True},
    'context': {'die_busy': False, 'bus_occupied': True}
})
```

### 6. Event-Driven Architecture

#### Pyee (EventEmitter) ⭐⭐⭐

```python
from pyee import EventEmitter

class OperationScheduler(EventEmitter):
    def reserve_operation(self, op):
        self.emit('before_reserve', op)

        result = self._do_reserve(op)

        if result.ok:
            self.emit('reserve_success', op, result)
        else:
            self.emit('reserve_failed', op, result.reason)

# Register listeners
scheduler = OperationScheduler()

@scheduler.on('reserve_success')
def log_success(op, result):
    logger.info(f"Reserved {op.name} at {result.start_time}")

@scheduler.on('reserve_failed')
def handle_failure(op, reason):
    metrics.increment('reservation_failures', tags={'reason': reason})
```

### 7. Property-Based Testing

#### Hypothesis ⭐⭐⭐⭐⭐

```python
from hypothesis import given, strategies as st, assume

@given(st.lists(st.sampled_from(VALID_OPERATIONS), min_size=1, max_size=100))
def test_schedule_always_valid(operations):
    """생성된 어떤 operation sequence도 validation을 통과해야 함"""
    scheduler = Scheduler()

    for op in operations:
        result = scheduler.reserve(op)

        if result.ok:
            # Invariant: 예약된 operation은 모든 제약을 만족
            assert_no_bus_conflicts(scheduler.get_schedule())
            assert_no_die_wide_conflicts(scheduler.get_schedule())

@given(
    die=st.integers(min_value=0, max_value=3),
    scope=st.sampled_from(["DIE_WIDE", "PLANE_SET"])
)
def test_die_wide_exclusivity(die, scope):
    """DIE_WIDE operation은 같은 die에서 배타적이어야 함"""
    op1 = create_operation(die=die, scope="DIE_WIDE", start=0, duration=100)
    op2 = create_operation(die=die, scope=scope, start=50, duration=100)

    scheduler = Scheduler()
    scheduler.reserve(op1)
    result = scheduler.reserve(op2)

    if scope == "DIE_WIDE":
        assert not result.ok, "Two DIE_WIDE ops should conflict"
```

### Q: 이 패키지들을 현재 시스템에 통합하는 실용적인 로드맵은?

**Alex**: 다음과 같은 **3단계 마이그레이션 플랜**을 추천합니다:

#### Phase 1: Foundation (2-3주)

**목표**: Type safety와 config validation 확보

1. **Pydantic 도입**
   ```python
   # models.py (새 파일)
   from pydantic import BaseModel

   class OperationConfig(BaseModel):
       # config.yaml의 op_names를 Pydantic model로 변환
       ...

   # main.py 수정
   config_dict = yaml.safe_load(open("config.yaml"))
   validated_config = Config(**config_dict)  # ← 자동 검증
   ```

2. **OmegaConf로 config 관리 개선**
   ```python
   from omegaconf import OmegaConf

   cfg = OmegaConf.load("config.yaml")
   OmegaConf.to_object(cfg)  # Pydantic model로 변환
   ```

3. **Hypothesis로 테스트 커버리지 확대**
   ```bash
   pip install hypothesis
   # tests/test_properties.py 작성
   ```

#### Phase 2: Decoupling (4-6주)

**목표**: Validation 규칙을 코드에서 분리

1. **State Machine 도입**
   ```python
   # state_machine.py (새 파일)
   from statemachine import StateMachine, State

   class PlaneResource(StateMachine):
       # Plane의 lifecycle을 명시적으로 모델링
       ...

   # resourcemgr.py 수정
   class ResourceManager:
       def __init__(self):
           self.planes = {
               (die, plane): PlaneResource()
               for die in range(NUM_DIES)
               for plane in range(NUM_PLANES)
           }
   ```

2. **Rule Engine 프로토타입**
   ```python
   # validation_rules.py (새 파일)
   from business_rules import ...

   # 기존 _check_bus_exclusion() 등을 rule로 변환
   ```

3. **Event-driven hooks**
   ```python
   from pyee import EventEmitter

   class Scheduler(EventEmitter):
       def __init__(self):
           super().__init__()
           self.on('op_end', self._handle_op_end)
   ```

#### Phase 3: Advanced (선택적, 6-8주)

**목표**: SMT solver로 정형 검증 가능

1. **Z3 통합 (선택적)**
   - Critical operation sequence의 correctness를 수학적으로 검증
   - 주로 테스트/디버깅 용도

2. **Constraint solver (선택적)**
   - 확률적 샘플링 대신 최적화 기반 스케줄링 실험
   - 성능 trade-off 고려 필요

### Q: 각 단계별 리스크는?

**Alex**:

| Phase | Risk | Mitigation |
|-------|------|-----------|
| Phase 1 | Config schema 변경으로 기존 YAML 호환성 깨짐 | Migration script 작성, backward compatibility layer |
| Phase 2 | State machine이 기존 로직과 불일치 | Shadow mode (parallel execution + comparison) |
| Phase 3 | Solver 성능 이슈 | Opt-in feature flag, fallback to legacy |

**점진적 도입**이 핵심입니다. 각 phase마다 A/B 테스트로 결과 비교하면서 마이그레이션하세요.

---

## 4. 인터뷰 종합 및 최종 권고안

### 핵심 문제 요약

현재 nandseqgen_v2는 다음 문제들로 인해 operation 추가/수정/제거가 어렵습니다:

1. **관심사 미분리**: Operation 정의, resource 관리, validation이 강하게 결합
2. **암묵적 계약**: Config schema가 코드에 하드코딩되어 런타임 에러 발생
3. **절차적 validation**: 새 규칙 추가 시 ResourceManager 직접 수정 필요
4. **분산된 상태 관리**: State 변경 로직이 scheduler/resourcemgr/addrman에 분산
5. **검증 불가능성**: Validation 규칙이 코드에 내재되어 정형 검증 불가

### 권장 해결책

#### 단기 (1-3개월): Foundational Improvements

##### 1. Type-Safe Configuration (Pydantic)

```python
# models.py
from pydantic import BaseModel, Field, validator
from typing import Literal, Dict, List

class StateConfig(BaseModel):
    bus: bool
    duration: float = Field(gt=0)

class OperationBaseConfig(BaseModel):
    scope: Literal["DIE_WIDE", "PLANE_SET", "NONE"]
    affect_state: bool
    instant_resv: bool
    states: Dict[str, StateConfig]

    @validator('states')
    def validate_states(cls, v):
        if not v:
            raise ValueError("states cannot be empty")
        bus_states = [k for k, s in v.items() if s.bus]
        if len(bus_states) > 1:
            raise ValueError(f"Multiple bus states: {bus_states}")
        return v

class OperationConfig(BaseModel):
    base: str
    celltype: Literal["SLC", "TLC", "QLC", "FWSLC"]
    multi: bool = False
    durations: Dict[str, float] = {}

class Config(BaseModel):
    topology: TopologyConfig
    policies: PoliciesConfig
    op_bases: Dict[str, OperationBaseConfig]
    op_names: Dict[str, OperationConfig]
    phase_conditional: Dict[str, Dict[str, float]]

    class Config:
        extra = "forbid"

# main.py
def load_config(path: str) -> Config:
    raw = yaml.safe_load(open(path))
    return Config(**raw)  # ← 자동 검증, 타입 에러 즉시 발견
```

**효과**:
- ✅ Config 로딩 시점에 모든 operation 정의 검증
- ✅ IDE 자동완성으로 개발 생산성 향상
- ✅ 잘못된 필드명/타입 즉시 발견

##### 2. Validation Rule Externalization

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
- ✅ 새 validation 규칙 추가 = YAML 편집만으로 가능
- ✅ 규칙 간 독립성 보장, 테스트 용이
- ✅ 비프로그래머도 규칙 이해 가능

##### 3. Property-Based Testing (Hypothesis)

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

#### 중기 (3-6개월): Architectural Refactoring

##### 4. Plugin Architecture for Operations

```python
# operation_plugin.py
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

##### 5. State Machine Integration

```python
# state_machine.py
from statemachine import StateMachine, State

class PlaneLifecycle(StateMachine):
    """Plane resource의 lifecycle을 명시적으로 모델링"""

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

#### 장기 (선택적, 6-12개월): Formal Verification

##### 6. SMT Solver Integration (Z3)

```python
# formal_verification.py
from z3 import *

def verify_schedule_correctness(operations: List[Operation]) -> bool:
    """Z3로 schedule의 correctness를 수학적으로 검증"""

    solver = Solver()

    # Variables: 각 operation의 시작 시각
    start_times = {op.id: Int(f"t_{op.id}") for op in operations}

    # Constraint 1: 시간은 음수가 아님
    for t in start_times.values():
        solver.add(t >= 0)

    # Constraint 2: Bus 충돌 방지
    bus_ops = [op for op in operations if op.uses_bus]
    for op1, op2 in combinations(bus_ops, 2):
        solver.add(Or(
            start_times[op1.id] + op1.bus_duration <= start_times[op2.id],
            start_times[op2.id] + op2.bus_duration <= start_times[op1.id]
        ))

    # Constraint 3: DIE_WIDE 충돌 방지
    for die in range(NUM_DIES):
        die_ops = [op for op in operations
                   if op.die == die and op.scope == "DIE_WIDE"]
        for op1, op2 in combinations(die_ops, 2):
            solver.add(Or(
                start_times[op1.id] + op1.duration <= start_times[op2.id],
                start_times[op2.id] + op2.duration <= start_times[op1.id]
            ))

    # Check satisfiability
    result = solver.check()

    if result == sat:
        model = solver.model()
        schedule = {op.id: model[start_times[op.id]].as_long()
                    for op in operations}
        logger.info(f"Valid schedule found: {schedule}")
        return True
    elif result == unsat:
        # Unsat core로 어떤 제약이 충돌하는지 진단
        core = solver.unsat_core()
        logger.error(f"Conflicting constraints: {core}")
        return False
    else:
        logger.warning("Z3 solver returned unknown")
        return False

# 테스트에 통합
def test_generated_schedule_is_valid():
    """생성된 schedule이 수학적으로 valid한지 검증"""
    scheduler = Scheduler()
    scheduler.run(num_operations=100)

    operations = scheduler.get_committed_operations()
    assert verify_schedule_correctness(operations), \
        "Generated schedule violates constraints"
```

**효과**:
- ✅ Schedule correctness의 수학적 증명
- ✅ Constraint 충돌 자동 진단
- ✅ 테스트/디버깅 도구로 활용

---

## 5. 추천 Python 패키지 요약표

| 용도 | 패키지 | 우선순위 | 도입 난이도 | 효과 |
|------|--------|----------|------------|------|
| **Config Validation** | Pydantic | ⭐⭐⭐⭐⭐ | 낮음 | 타입 안정성, 즉시 검증 |
| **Config Management** | OmegaConf | ⭐⭐⭐⭐ | 낮음 | 계층적 설정, CLI 통합 |
| **State Machine** | python-statemachine | ⭐⭐⭐⭐ | 중간 | 명시적 전이, 시각화 |
| **Validation Rules** | business-rules | ⭐⭐⭐ | 중간 | 선언적 규칙 정의 |
| **Property Testing** | Hypothesis | ⭐⭐⭐⭐⭐ | 낮음 | 자동 edge case 발견 |
| **Event System** | Pyee | ⭐⭐⭐ | 낮음 | Decoupling, 확장성 |
| **Constraint Solving** | python-constraint | ⭐⭐ | 중간 | 선언적 제약 조건 |
| **Formal Verification** | Z3 | ⭐⭐ | 높음 | 수학적 검증 (선택적) |

### 패키지 상세 설명

#### 높은 우선순위 (즉시 도입 권장)

1. **Pydantic** (⭐⭐⭐⭐⭐)
   - Config schema validation의 표준
   - IDE 통합 우수
   - 학습 곡선: 낮음
   - 설치: `pip install pydantic`

2. **Hypothesis** (⭐⭐⭐⭐⭐)
   - Property-based testing의 사실상 표준
   - pytest 통합
   - 학습 곡선: 중간
   - 설치: `pip install hypothesis`

3. **OmegaConf** (⭐⭐⭐⭐)
   - Config 관리 강력한 기능
   - Pydantic과 통합 가능
   - 학습 곡선: 낮음
   - 설치: `pip install omegaconf`

4. **python-statemachine** (⭐⭐⭐⭐)
   - State 전이 명시화
   - 시각화 지원
   - 학습 곡선: 중간
   - 설치: `pip install python-statemachine`

#### 중간 우선순위 (선택적 도입)

5. **business-rules** (⭐⭐⭐)
   - Rule engine
   - JSON/YAML 기반 규칙 정의
   - 학습 곡선: 중간
   - 설치: `pip install business-rules`

6. **Pyee** (⭐⭐⭐)
   - Event-driven architecture
   - Node.js EventEmitter와 유사
   - 학습 곡선: 낮음
   - 설치: `pip install pyee`

#### 낮은 우선순위 (장기 검토)

7. **python-constraint** (⭐⭐)
   - Constraint satisfaction
   - 성능 이슈 가능성
   - 학습 곡선: 중간
   - 설치: `pip install python-constraint`

8. **Z3** (⭐⭐)
   - SMT solver
   - 정형 검증 가능
   - 학습 곡선: 높음
   - 설치: `pip install z3-solver`

---

## 6. 실용적 마이그레이션 로드맵

### Phase 1: Quick Wins (1개월)

#### 주차 1-2: Pydantic 도입

```bash
pip install pydantic
```

**작업 항목:**
- [ ] `models.py` 생성, Config schema 정의
- [ ] `main.py`에서 Pydantic 검증 통합
- [ ] 기존 dict 기반 코드는 유지 (`.dict()` 메서드로 호환)
- [ ] Unit test 작성

**검증 기준:**
- Config 로딩 시 잘못된 operation 정의 즉시 발견
- 기존 테스트 모두 통과

#### 주차 3-4: Hypothesis 테스트 추가

```bash
pip install hypothesis
```

**작업 항목:**
- [ ] `tests/test_properties.py` 작성
- [ ] Bus exclusion, die-wide exclusion invariant 테스트
- [ ] CI/CD에 통합
- [ ] 기존 테스트와 병행 실행

**검증 기준:**
- 1000+ 랜덤 시나리오 통과
- 새로운 버그 발견 및 수정

**산출물**:
- ✅ Config 로딩 시점에 모든 operation 검증
- ✅ 자동화된 property-based 테스트

### Phase 2: Structural Improvements (2-3개월)

#### 주차 5-8: Validation Rule 외부화

```bash
pip install pyyaml
```

**작업 항목:**
- [ ] `validation_rules.yaml` 생성
- [ ] `ValidationEngine` 클래스 구현
- [ ] 기존 `resourcemgr.py`의 validation 로직을 rule로 마이그레이션
- [ ] Shadow mode: 기존 로직과 new engine 결과 비교
- [ ] 불일치 케이스 디버깅 및 수정

**검증 기준:**
- Shadow mode에서 100% 일치
- 새 규칙 추가 시 YAML 편집만으로 가능

#### 주차 9-12: State Machine 통합

```bash
pip install python-statemachine
```

**작업 항목:**
- [ ] `PlaneLifecycle` state machine 정의
- [ ] `ResourceManager`에 통합
- [ ] State diagram 자동 생성 스크립트
- [ ] 기존 state 관리 로직과 비교 검증

**검증 기준:**
- State transition 100% 일치
- Illegal transition 자동 방지 확인

**산출물**:
- ✅ 새 validation 규칙 추가 = YAML 편집만
- ✅ State 전이 규칙 명시화

### Phase 3: Advanced Features (선택적, 4-6개월)

#### 주차 13-20: Operation Plugin System

**작업 항목:**
- [ ] `OperationPlugin` 인터페이스 정의
- [ ] 기존 operation을 plugin으로 마이그레이션 (ERASE, PROGRAM_SLC 등)
- [ ] `OperationRegistry` 구현
- [ ] `proposer.py`, `scheduler.py` 연동
- [ ] Backward compatibility 유지

**검증 기준:**
- 모든 기존 operation이 plugin으로 동작
- 새 operation 추가 시 단일 파일 수정만 필요

#### 주차 21-24: Formal Verification (선택적)

```bash
pip install z3-solver
```

**작업 항목:**
- [ ] `verify_schedule_correctness()` 함수 구현
- [ ] 테스트에 통합 (opt-in)
- [ ] Debugging 도구로 활용
- [ ] Performance 최적화

**검증 기준:**
- Critical sequence의 correctness 증명 가능
- Constraint 충돌 자동 진단

**산출물**:
- ✅ 새 operation 추가 = Plugin 클래스 작성 + 등록
- ✅ Schedule correctness 수학적 검증 가능

### 리스크 관리

| Phase | Risk | Impact | Probability | Mitigation |
|-------|------|--------|-------------|-----------|
| Phase 1 | Config schema 변경으로 기존 YAML 호환성 깨짐 | 높음 | 중간 | Migration script 작성, backward compatibility layer |
| Phase 2 | State machine이 기존 로직과 불일치 | 높음 | 중간 | Shadow mode (parallel execution + comparison) |
| Phase 2 | Validation rule engine 성능 저하 | 중간 | 낮음 | Profiling, 최적화, feature flag로 롤백 가능 |
| Phase 3 | Plugin system 복잡도 증가 | 중간 | 중간 | 점진적 마이그레이션, 충분한 문서화 |
| Phase 3 | Solver 성능 이슈 | 낮음 | 높음 | Opt-in feature flag, fallback to legacy |

### 성공 지표

#### Phase 1 완료 후:
- [ ] Config 에러가 런타임이 아닌 로딩 시점에 발견됨
- [ ] Property-based test가 기존 테스트 대비 2배 이상의 케이스 커버

#### Phase 2 완료 후:
- [ ] 새 validation 규칙 추가 시간: 1일 → 1시간
- [ ] State transition 버그: 월 평균 3건 → 0건

#### Phase 3 완료 후:
- [ ] 새 operation 추가 시 수정 파일 수: 5개 → 1개
- [ ] Operation 추가 시간: 2일 → 2시간

---

## 7. 최종 권장사항

### 즉시 시작 (이번 주)

1. **Pydantic 설치 및 Config schema 정의** - 가장 빠른 ROI
   ```bash
   pip install pydantic
   # models.py 작성 시작
   ```

2. **Hypothesis 설치 및 첫 property test 작성** - Regression 방지
   ```bash
   pip install hypothesis
   # tests/test_properties.py 작성
   ```

### 다음 달

3. **validation_rules.yaml 작성 및 ValidationEngine 프로토타입**
   - 핵심 규칙 3개부터 시작 (bus_exclusion, die_wide_exclusion, latch_exclusion)
   - Shadow mode로 기존 로직과 비교

4. **State Machine 도입 (PlaneLifecycle 먼저)**
   - 단일 Plane에 대한 state machine 구현
   - 기존 로직과 병행 실행

### 향후 검토

5. **Operation Plugin Architecture** (대규모 리팩토링 필요 시)
   - Team capacity 고려하여 결정
   - 3개월 이상 투자 가능한 경우만 진행

6. **Z3 Formal Verification** (critical system에만 필요)
   - Safety-critical operation에 한정
   - 성능 영향 최소화

### 핵심 원칙

- ✅ **점진적 마이그레이션**: 각 단계마다 A/B 테스트
- ✅ **Backward compatibility**: 기존 기능 유지하며 새 시스템 추가
- ✅ **테스트 우선**: 각 변경 후 property test로 검증
- ✅ **문서화**: 각 단계마다 migration guide 작성
- ✅ **Feature flags**: 새 기능은 flag로 제어하여 롤백 가능하게

### 예상 효과

이 접근법으로 다음을 달성할 수 있습니다:

1. **Operation 추가/수정이 1개 파일(YAML) 편집만으로 가능**
2. **Validation이 자동화되어 human error 감소**
3. **코드 유지보수성 극대화 및 기술 부채 감소**
4. **새로운 팀원의 onboarding 시간 단축**
5. **버그 발견 시점이 production → development로 이동**

### 다음 단계

1. 이 문서를 팀과 공유하여 피드백 수집
2. Phase 1 작업 항목을 Sprint backlog에 추가
3. Pydantic PoC (Proof of Concept) 작성 (1-2일 소요)
4. PoC 결과 리뷰 후 본격 진행 여부 결정

---

## 참고 자료

### 관련 문서
- [RESTRUCTURING.md](RESTRUCTURING.md): Operation 속성 및 리소스 설계
- [CLAUDE.md](../CLAUDE.md): 프로젝트 개요 및 핵심 명령어
- [AGENTS.md](../AGENTS.md): AI 에이전트용 상세 가이드라인

### 추천 학습 자료

#### Pydantic
- 공식 문서: https://docs.pydantic.dev/
- Tutorial: https://pydantic-docs.helpmanual.io/usage/models/

#### Hypothesis
- 공식 문서: https://hypothesis.readthedocs.io/
- Getting Started: https://hypothesis.works/articles/getting-started-with-hypothesis/

#### python-statemachine
- 공식 문서: https://python-statemachine.readthedocs.io/
- Examples: https://github.com/fgmacedo/python-statemachine/tree/develop/examples

#### OmegaConf
- 공식 문서: https://omegaconf.readthedocs.io/
- Tutorial: https://github.com/omry/omegaconf#readme

---

**문서 버전**: 1.0
**최종 수정**: 2025-10-26
**작성자**: Expert Panel (Dr. Sarah Chen, Prof. Michael Torres, Alex Kumar)
**검토자**: TBD
