# AddressManager Badlist Initialization 구현 계획

## 개요
`main.py` 실행 시 선택적 badlist CSV(`die,block` 헤더 포함)을 읽어 `AddressManager` 초기화 시 전달하여, 다중 실행 시나리오에서도 일관된 불량 블록 정보를 적용한다.

## 현재 상태 분석
- `main.py:32` `_mk_addrman`는 topology만 사용해 `AddressManager`를 만들고 badlist를 넘기지 않음.
- `main.py:1115`부터의 CLI 정의에는 badlist 파일을 지정할 인자가 없어 기본적으로 빈 badlist로 동작함.
- `addrman.py:91` 생성자는 `(die, block)` 쌍 배열을 받아 내부 상태를 BAD로 표기하며, 범위를 벗어나면 즉시 예외를 던짐 (`addrman.py:163`).
- `main.py:1182` 이후 multi-site 루프에서도 `_mk_addrman`를 호출하므로 재사용 가능한 badlist 전달 경로가 필요함.

## 목표 상태
- 사용자는 `main.py --badlist custom.csv`로 badlist 파일을 지정할 수 있고, 파일이 없으면 경고 없이 빈 badlist로 계속 진행한다.
- CSV 파싱 실패(데이터 누락, 정수 변환 실패, 범위 위반 등) 시 CLI는 명확한 오류 메시지와 함께 실패한다.
- 모든 실행 경로(기본, multi-run, multi-site)에서 동일한 badlist가 재사용되어 AddressManager의 BAD 상태가 일관되게 적용된다.

### 핵심 발견:
- `main.py:32` `_mk_addrman`가 badlist 인자를 받을 수 있도록 확장 필요.
- `addrman.py:181` `_normalize_badlist`가 입력 유효성을 담당하므로, CSV 로더는 `(die, block)` 정수쌍만 보장하면 된다.
- `main.py:1217` multi-site 루프에서도 동일한 AddressManager 생성 경로가 사용되어야 재사용된다.

## 범위에서 제외되는 항목
- CSV 외 다른 badlist 입력 형식(JSON 등) 지원.
- AddressManager 내부 로직 변경.
- badlist 관련 새로운 테스트 작성(필요 시 추후 별도 작업).

## 구현 접근
CSV를 한 번 파싱하여 `(die, block)` 리스트로 유지하고, `_mk_addrman` 호출마다 전달한다. fallback AddressManager 대체 경로에서는 badlist를 무시하고 경고 없이 진행한다.

## 1단계: CLI 옵션 추가 및 입력 경로 확정

### 개요
새로운 `--badlist` CLI 인자를 추가하고 기본 파일명(`badlist.csv`)을 현재 작업 디렉터리 기준으로 설정한다.

### 필요한 변경:

#### 1. CLI 정의
**File**: `main.py`
**Changes**: argparse에 `--badlist` 옵션 추가, help 텍스트 정의, 기본값 `badlist.csv` 지정.

```python
    p.add_argument(
        "--badlist",
        default="badlist.csv",
        help="CSV file with 'die,block' header; missing file disables badlist",
    )
```

### 성공 기준:

#### 자동 검증:
- [x] `python -m compileall main.py` 통과 (문법 검사 대용)

#### 수동 검증:
- [x] `python main.py --help` 실행 시 새 옵션이 표시됨

---

## 2단계: Badlist CSV 로더 구현

### 개요
헤더 포함 CSV를 읽어 `(die, block)` 정수쌍 리스트를 반환하고, 파일 미존재 시 빈 리스트를 반환하며 파싱 오류는 예외로 처리한다.

### 필요한 변경:

#### 1. 보조 함수 추가
**File**: `main.py`
**Changes**: `_load_badlist_csv(path: str, *, dies: int, blocks_per_die: int) -> Optional[List[Tuple[int, int]]]` 함수 추가. 파일이 없으면 `None` 반환, 존재 시 헤더 검사 후 정수 변환, 범위 확인, 실패 시 `ValueError` 발생.

```python
def _load_badlist_csv(path: str, *, dies: int, blocks_per_die: int) -> Optional[List[Tuple[int, int]]]:
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or [c.strip().lower() for c in reader.fieldnames] != ["die", "block"]:
            raise ValueError(f"badlist CSV must have 'die,block' header: {path}")
        rows: List[Tuple[int, int]] = []
        for idx, row in enumerate(reader, start=2):
            try:
                die = int(row["die"])
                block = int(row["block"])
            except Exception as exc:
                raise ValueError(f"invalid die/block at line {idx}: {exc}") from exc
            if not (0 <= die < dies):
                raise ValueError(f"die {die} out of range [0,{dies}) at line {idx}")
            if not (0 <= block < blocks_per_die):
                raise ValueError(f"block {block} out of range [0,{blocks_per_die}) at line {idx}")
            rows.append((die, block))
    return rows
```

### 성공 기준:

#### 자동 검증:
- [x] `python -m compileall main.py`

#### 수동 검증:
- [x] 정상 CSV로 함수 호출 시 `(die, block)` 리스트를 반환함
- [x] 헤더 오류나 범위 초과 시 명확한 예외 메시지를 확인함

---

## 3단계: AddressManager 생성 경로에 badlist 전달

### 개요
파싱된 badlist 데이터를 `_mk_addrman`과 multi-site 경로로 전달하고, fallback AddressManager는 badlist를 무시하도록 처리한다.

### 필요한 변경:

#### 1. `_mk_addrman` 시그니처 업데이트
**File**: `main.py`
**Changes**: `_mk_addrman(cfg, *, badlist=None)` 형태로 변경, `AddressManager` 생성 시 `badlist` 인자 전달. fallback `_SimpleAM`은 인터페이스 유지.

```python
def _mk_addrman(cfg: Dict[str, Any], *, badlist: Optional[List[Tuple[int, int]]] = None):
    ...
    if AddressManager is not None:
        am = AddressManager(
            num_planes=planes,
            num_blocks=blocks,
            pagesize=pages,
            num_dies=dies,
            badlist=np.array(badlist, dtype=int) if badlist else None,
        )
```

#### 2. AddressManager 호출부 갱신
**File**: `main.py`
**Changes**: 기본 실행 경로 및 multi-site 루프에서 `_load_badlist_csv` 결과를 재사용해 `_mk_addrman`에 전달. badlist가 `None`이면 기존 동작 유지.

```python
    badlist_rows = _load_badlist_csv(args.badlist, dies=dies, blocks_per_die=int(topo.get("blocks_per_die", 0)))
    ...
    am = _mk_addrman(cfg_run, badlist=badlist_rows)
```

### 성공 기준:

#### 자동 검증:
- [x] `python -m compileall main.py`
- [x] (선택) `pytest tests/test_suspend_resume.py`

#### 수동 검증:
- [x] badlist CSV 제공 시 AddressManager가 예외 없이 BAD 블록을 적용함
- [x] 파일이 없으면 기존 실행이 동일하게 진행됨
- [x] 범위 오류 CSV로 실행 시 명확한 실패 메시지가 출력됨

---

## 테스트 전략

### 단위 테스트:
- 기존 `pytest tests/test_suspend_resume.py`로 회귀 확인.
- 시간 여건 시 badlist 로더를 겨냥한 신규 테스트 추가 고려(이번 범위에는 포함하지 않음).

### 통합 테스트:
- 샘플 badlist CSV와 함께 `python main.py --badlist sample.csv --num-runs 1` 실행해 AddressManager 초기 BAD 상태를 확인.

### 수동 테스트 단계:
1. 정상 CSV(`die,block` 헤더)로 실행 후 로그나 스냅샷에서 BAD 상태 적용 여부 확인.
2. 일부 행에 범위 위반 값을 넣어 실행하여 실패 메시지 확인.
3. CSV를 제거한 상태로 실행하여 경고 없이 진행되는지 확인.

## 성능 고려사항
- CSV는 실행 당 한 번만 파싱하므로 성능 영향은 무시 가능.
- 멀티 사이트 루프에서도 파싱 결과를 재사용해 I/O 중복을 방지한다.

## 마이그레이션 노트
- 기존 실행 스크립트는 CSV가 없으면 그대로 동작함.
- badlist CSV를 공급하는 사용자는 헤더와 범위 규칙을 준수해야 한다.

## 참고 자료
- 관련 연구: 없음
- 유사 구현: `addrman.py:181` (`_normalize_badlist` 값 검증 로직)
