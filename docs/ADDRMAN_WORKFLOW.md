# AddressManager Target Address Workflow

본 문서는 `addrman.py`의 AddressManager가 ERASE, PGM(Program), READ 대상 주소를 어떻게 생성하는지 단계별 워크플로우를 정리합니다. 함수 이름만을 참조하며, 라인 번호 표기는 제거했습니다.

중요: v2에서 기존 후보 전개 기반 API(`get_*`, `sample_*`, `set_adds_*`)는 제거되었고, 빠른 경로 `random_*` API만을 제공합니다. 아래 다이어그램과 일부 섹션은 과거(legacy) 흐름을 설명하므로, 실제 사용 시에는 `random_erase`, `random_pgm`, `random_read`를 사용하세요.

## 다이어그램(요약)
```mermaid
flowchart TD
  subgraph Inputs[입력]
    P[sel_plane (단일/다중)]
    M[mode (SLC/FWSLC/TLC)]
    O[offset]
  end

  subgraph State[상태]
    S1[addrstates]
    S2e[addr_mode_erase]
    S2p[addr_mode_pgm]
  end

  subgraph ERASE[random_erase]
    F1[조건 필터: !=BAD, !=ERASE]
    Smp1[샘플링]
    A1[적용: addrstates=ERASE, addrmodes=mode]
  end

  subgraph PGM[random_pgm]
    F2[조건 필터: ERASE..pagesize-2, mode 일치]
    Smp2[샘플링(연속 옵션)]
    A2[적용: addrstates += 1 또는 +size]
  end

  subgraph READ[random_read]
    F3[조건 필터: addrstates>=offset, mode 일치]
    Smp3[샘플링(연속 옵션), 상태변화 없음]
  end

  P --> F1 & F2 & F3
  M --> F2 & F3
  O --> F3
  S1 --> F1 & F2 & F3
  S2e --> F2
  S2p --> F2 & F3
  F1 --> Smp1 --> A1 --> S1
  F2 --> Smp2 --> A2 --> S1
  F3 --> Smp3
```

## 데이터 모델
- 상태 배열 `addrstates`: 블록 단위 상태를 보관하는 길이 `num_blocks`의 배열 (`addrman.py:67`).
  - `BAD=-3`: 불량 블록
  - `GOOD=-2`: 정상(미소거)
  - `ERASE=-1`: 소거 완료 상태
  - `0..pagesize-1`: 마지막으로 프로그램된 페이지 인덱스(소거 후 첫 PGM → 0)
- 모드 배열 `addr_mode_erase`/`addr_mode_pgm`: 블록 단위 셀 모드(SLC/FWSLC/TLC 등)를 분리 보관.
  - ERASE 시 `addr_mode_erase`를 설정하고 `addr_mode_pgm`은 초기화(TBD)
  - PGM 시작 시점: (erase==mode) 또는 (erase==SLC and mode∈{A0SLC,ACSLC}) 만 허용
  - PGM 지속/READ: `addr_mode_pgm == mode`만 허용
- 주소 표현: 반환 배열은 `(block, page)` 쌍을 담습니다. plane은 `block % num_planes`로 유도합니다.
- 반환 배열 형태(Shape):
  - 단일 plane 선택: `(#, 1, 2)`
  - 다중 plane 선택: `(#, len(sel_plane), 2)`

## 공통 유틸/래퍼
- 빠른 경로: `random_erase`, `random_pgm`, `random_read` — 후보 전개 없이 직접 샘플/적용
- 원복: `undo_last`
- 내부 헬퍼: `_plane_index`, `_groups_for_planes` (멀티-plane 그룹 계산)

## Random API 핵심 동작
- random_erase
  - 조건: `addrstates != BAD` AND `addrstates != ERASE` (단일/멀티 모두 충족)
  - 효과: 선택 블록(들) `addrstates = ERASE(-1)`, `addr_mode_erase = mode`, `addr_mode_pgm = TBD`
- random_pgm
  - 조건: `ERASE(-1) <= addrstates < pagesize-1` AND (시작: `addr_mode_erase==mode` 또는 `addr_mode_erase==SLC and mode∈{A0SLC,ACSLC}`) AND (지속: `addr_mode_pgm==mode`)
  - 연속 옵션: 동일 블록(또는 그룹)에서 `size`만큼 연속 페이지 할당, 상태는 `+size`
  - 효과: 선택 블록(들) `addrstates += 1` 또는 `+= size`
- random_read
  - 조건: `addrstates >= offset` AND `addr_mode_pgm == mode`
  - 효과: 상태 변화 없음, 가중치 기반(읽기 가능한 페이지 수) 샘플링

## 상태 전이 요약
- ERASE 수행 시: `addrstates = ERASE(-1)`, `addr_mode_erase = mode`, `addr_mode_pgm = TBD`
- PGM 수행 시: `addrstates += 1` (연속 시 `+= size`), 모드는 유지
- READ 수행 시: 상태/모드 변화 없음

## Plane 선택 규칙 요약
- 단일 plane: `block % num_planes == sel_plane` 기준으로 내부 필터링
- 멀티 plane: 선택된 plane 집합을 행 단위 그룹으로 묶어 조건을 일괄 평가
  - ERASE: 모든 블록 `!= BAD` AND `!= ERASE`
  - PGM: 모든 블록의 `addrstates`가 동일하고 범위 유효, 시작은 `addr_mode_erase` 규칙, 지속은 `addr_mode_pgm == mode`
  - READ: 모든 블록이 `addrstates >= offset`이고 `> ERASE(-1)`, `addr_mode_pgm == mode`

## 상태/모드 갱신 규칙
- 최근 동작 원복: `undo_last()`로 마지막 ERASE/PGM 이전 상태 복원

## 무작위 실행 시나리오(편의 함수)
- `random_erase(...)` → 조건 필터링 → 샘플링 → 상태 적용
- `random_pgm(...)` → 조건 필터링(연속 옵션 가능) → 샘플링 → 상태 적용
- `random_read(...)` → 조건 필터링 → 샘플링만 수행(상태 변화 없음)

## 주의 사항
- `offset`을 크게 설정하면 READ 대상이 줄어듭니다. `offset < pagesize` 제약(`__init__`)이 있습니다.
- PGM은 `pagesize-1` 도달 시 더 이상 대상에 포함되지 않습니다.
- 멀티-plane PGM/READ는 모든 선택 plane에서 동기화된 페이지 인덱스를 보장하도록 보수적으로 계산됩니다(최솟값/동일성 제약).
- ERASE는 이미 ERASE 상태인 블록은 제외합니다(중복 소거 방지).

## 빠른 예시(의사 코드)
```python
# ERASE: 직접 샘플+적용
adds = am.random_erase(sel_plane=0, size=K, mode=TLC)

# PGM: 연속 페이지 샘플+적용
adds = am.random_pgm(size=N, mode=TLC, sequential=True)

# READ: 연속 페이지 샘플(상태 변화 없음)
adds = am.random_read(size=M, mode=TLC, offset=am.offset, sequential=True)
```

---
이 문서는 `addrman.py`의 실제 구현 흐름을 축약·구조화한 것입니다. 함수별 정확한 세부 조건은 각 파일 참조 라인을 확인하세요.
