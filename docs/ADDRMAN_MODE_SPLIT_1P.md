# Problem 1‑Pager — addr_mode 분리(erase vs program)

- 배경: AddressManager는 블록별 단일 `addr_mode`(celltype)로 erase/program/read 규칙을 동시에 표현하고 있었습니다. `NAND_BASICS_N_RULES.md:101-102`에 따르면, “erase가 특정 celltype으로 수행되면 program/read도 동일 celltype으로 동작해야 한다(단, SLC erase의 경우 A0SLC/ACSLC program 허용, read는 program과 동일 celltype)”. 단일 모드 필드로는 erase 시점의 celltype과 program/read 시점의 celltype을 구분하기 어려워 규칙 표현이 제한되었습니다.

- 문제:
  - erase 시 셋된 celltype과 program/read 시 사용 celltype의 의미 충돌
  - 초기 erase celltype에 따른 program 허용 예외(SLC→A0SLC/ACSLC) 표현 곤란
  - read는 program celltype과 일치해야 하나, 단일 모드라 혼동 가능

- 목표:
  - `addr_mode_erase`와 `addr_mode_pgm`을 분리하여 규칙을 명시적으로 표현
  - 샘플링 시 다음 제약 자동 적용
    - erase: BAD이 아닌 블록 대상, 결과로 `addr_mode_erase`를 설정하고 `addr_mode_pgm` 초기화
    - program: state==ERASE이면 (erase==mode) 또는 (erase==SLC and mode∈{A0SLC,ACSLC})에서만 시작, 이후에는 기존 `addr_mode_pgm`과 동일한 mode만 지속
    - read: `addr_mode_pgm`과 동일한 mode만 후보

- 비목표:
  - 외부 도구/벤치마크의 대규모 API 변경(호환성은 유지)
  - AddressManager의 대폭 구조 개편(필요 최소 변경만 수행)

- 제약:
  - 함수 시그니처 유지, 반환 shape 유지
  - numpy 의존(벤치 환경에 설치 필요)

- 대안 비교:
  1) 단일 모드 유지 + 조건 분기 강화
     - 장점: 변경 폭 작음
     - 단점: 의미 충돌 지속, 규칙 표현/검증 난해
     - 위험: 예외 규칙(SLC→A0SLC/ACSLC) 누락 위험
  2) 모드 필드 분리(선택)
     - 장점: 규칙을 데이터 모델로 직접 표현, 검증 용이
     - 단점: 필드/undo 확장, 일부 코드 수정 필요
     - 위험: 외부 코드 호환성 저하(별칭으로 완화)

- 결정: (2) 채택.
  - 내부 상태: `addrstates`, `addr_mode_erase`, `addr_mode_pgm` 3트랙 관리
  - 호환성: `self.addrmodes`와 `get_addrmodes()`는 program 모드(alias)로 유지
  - random_erase: erase모드 설정 + program모드 리셋(TBD)
  - random_pgm: erase→program 시작 규칙 및 program 지속 규칙 벡터화 적용
  - random_read: program 모드 기반 필터

- 리스크/완화:
  - 벤치/툴이 `addrmodes`에 접근: program 모드 alias 제공으로 호환성 유지
  - 멀티‑플레인: 그룹 내 상태/모드 일치 조건을 행 단위로 평가해 정확성 보존

- 다음 단계:
  - Validator에서 addr_mode 규칙 위반(erase↔program/read 불일치) 케이스 추가 점검
  - config.yaml의 토폴로지 키 매핑(pages_per_block→pagesize) 반영: `AddressManager.from_topology(topology)` 추가
