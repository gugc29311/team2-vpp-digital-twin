# Changelog

## v0.0.2 — 2026.09.28
### Added
- 가정값 로그 기반 VPP 공정 시뮬레이션 모델 (`src/model/simulation.py`)
  - Order Reception → Job Assignment → 배치 형성 → VPP Build → Part Removal → Washing → UV Curing → Support Removal → Surface Treatment → Inspection → Packaging
  - 배치 형성: 빌드플레이트 면적 70% 도달 또는 1근무일 대기 시 확정, 출력시간은 배치 내 최대 높이로 계산
  - 불량 4% 재출력, 설비 고장·계획예방정비(PM), 공정 간 이동시간, 근무 캘린더(주 40h) + 프린터 무인운전
- 주문·부품·배치 엔티티 (`src/entities/`), 설비·인력 자원 (`src/resources/`)
- 가정값 파라미터 (`config/parameters.py`) — 시나리오 Normal / High Demand / Stress
- KPI 계산(가동률, 프린터 부하율, 리드타임, 납기 준수, 레진 소모) 및 결과 CSV 저장
- 30회 독립 반복 실행(95% 신뢰구간), 프린터 유효 처리용량 측정
- 실행 진입점 `main.py`, 샘플 주문 `data/sample_orders.csv` (10건)
- 테스트 50개 (`tests/`)

### Changed
- README: 실행 방법 작성
- requirements: numpy, pytest 추가

### Notes
- 가정값 로그 대비 조정: 배치 최장 대기 1근무일(8h), 불량 재출력은 같은 형상, Build Preparation 시간 0, Stress = 프린터 부하율 97% (λ 764.5건/주)
- 검증: 30회 반복 결과가 가정값 로그 최종값과 일치 (Normal 기준 프린터 부하율 79.9%, 인력 가동률 JA 57.7% · 후공정 50.4% · 검사 55.6%)
- 모든 값은 가정값 — 기업 인터뷰 후 갱신 예정

## v0.0.1 — 2026.09.17
### Added
- Repository 초기 구조 생성 (src, dashboard, replay, tests, docs 등)
- README 작성
