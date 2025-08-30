# AddressManager Sampling Benchmark

이 문서는 `tools/bench_addrman.py` 스크립트로 AddressManager의 직접 샘플링(fast path, `random_*`) 성능을 측정하는 방법을 설명합니다. 기존 후보 전개 기반(legacy) API는 제거되었습니다.

## 준비
- NumPy 필요: `pip install numpy`
- (선택) CSV 저장 시 파일 경로 지정

## 실행 예시
```bash
python tools/bench_addrman.py \
  --planes 4 \
  --blocks 4096,16384 \
  --pages 256,2048 \
  --iters 100 \
  --erase-size 64 \
  --pgm-size 64 \
  --read-size 128 \
  --offset 0 \
  --seed 12345 \
  --mp-size 2 \
  --csv-out bench_results.csv
```

## 출력
- 표준 출력: 요약 테이블(CSV 헤더 포함)
  - 주요 컬럼
    - 공통: `planes,blocks,pages,iters,mp_size`
    - 단일(non-seq/seq): `erase_ms,pgm_ms,read_ms,pgm_seq_ms,read_seq_ms`
    - 멀티(non-seq/seq): `mp_erase_ms,mp_pgm_ms,mp_pgm_seq_ms,mp_read_ms,mp_read_seq_ms`
- `--csv-out` 지정 시: 상세 필드 포함한 CSV 저장(동일 디렉토리)

## 벤치 설계 노트
- 경로: `random_*` — 후보 전개 없이 직접 샘플/적용합니다.
- 상태 유지를 위해 각 섹션(ERASE/PGM/READ) 전에는 초기 상태로 리셋합니다.
- 초기 상태는 일부 블록을 ERASE로 두고, 그 절반 정도를 소량 PGM 하여 READ/PGM 후보가 충분히 존재하도록 구성합니다.

## 결과 해석 가이드
- READ는 페이지 가중치 기반 샘플링을 사용하므로 큰 `pagesize`에서 비용이 커질 수 있습니다. `iters`를 높여 통계적 분산을 줄이되, 필요 시 `read_size`를 조절하세요.
- `--mp-size`로 멀티‑플레인 측정을 포함할 수 있습니다(예: 2면 2‑plane 동시 후보).

## 주의
- 결과는 하드웨어/파이썬 버전에 따라 달라질 수 있습니다.
- 측정 자체의 랜덤성 영향을 줄이려면 `--iters`를 충분히 키우고, `--seed`를 고정하세요.
