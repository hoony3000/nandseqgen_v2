# AGENTS.md

이 문서는 **AI 에이전트(Codex CLI 등)**가 이 프로젝트를 이해하고 작업할 때 따라야 할 규칙과 지침을 정의한다.

# Repository Overview
NAND operation 시퀀스를 랜덤하게 생성하기 위한 시스템으로, state 별 operation 확률 설정값과 NAND 고유 규칙을 기반으로 runtime 으로 operation 이 제안되는 시뮬레이션 코드이다.

# Repository Guidelines

## 프로젝트 구조 및 모듈 구성
- `main.py`는 NAND 시퀀스 생성기의 진입점으로 `scheduler.py`, `resourcemgr.py`, `proposer.py`를 묶어 실행합니다.
- 주소 관리, 이벤트 큐, 시각화 내보내기 등 공용 유틸리티는 저장소 루트에 배치되어 있습니다.
- 설정 값은 `config.yaml`과 확률 테이블(`op_state_probs.yaml` 등)에 정리합니다.
- 생성된 CSV와 페이로드 산출물은 `out/` 하위 디렉터리에 정리합니다.
- 설계 문서와 규칙은 `docs/`, 리그레션 테스트는 도메인별로 `tests/`에 둡니다.

## 빌드·테스트·개발 명령어
- 환경 구성: `python -m venv .venv && source .venv/bin/activate`
- 의존성 설치: `pip install -r requirements.txt`
- 기본 실행: `python main.py --config config.yaml --out-dir out`
- 전체 테스트: `python -m pytest`
- 선택적 단위 실행: `python -m pytest tests/test_suspend_resume.py -k resume`
- 대규모 몬테카를로 작업은 `out/2024-ops/`처럼 날짜 기반 디렉터리에 저장해 산출물을 추적합니다.

## 코딩 스타일 및 네이밍 규칙
- Python 코드는 Black 포맷(들여쓰기 4칸, 행 길이 88자)에 맞춥니다.
- 모듈·함수는 `snake_case`, 클래스는 `PascalCase`, 상수는 대문자 스네이크 케이스를 유지합니다.
- 구조화 데이터는 `dataclass`로 표현하고 타입 힌트를 포함합니다.
- NumPy 의존 경로는 `main.py` 예시처럼 선택적으로 임포트합니다.

## 테스트 가이드라인
- 모든 테스트는 `pytest`를 사용하며 새 파일은 `test_<feature>.py` 형식으로 추가합니다.
- 스냅샷 파일보다 커버리지를 높여주는 명시적 어서션을 선호합니다.

## 커밋 및 PR 가이드라인
- 기록은 영향을 받는 모듈을 요약한 짧은 제목을 사용합니다(예: `scheduler: adjust admission window`).
- 커밋 메시지는 명령형으로 작성하고 연관성이 낮은 변경은 분리합니다.
- PR에는 시나리오 설명, 다룬 에지 케이스, 관련 문서나 이슈 링크를 포함합니다.
- 검증에 사용한 명령(`pytest`, 샘플 `main.py` 실행 등)과 산출물이 위치한 `out/` 경로를 명시해 검토자가 빠르게 확인할 수 있도록 합니다.
