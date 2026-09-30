# Changelog

## v0.0.3 — 2026.09.30
### Added
- KPI: `throughput_per_week`, `tardiness_work_h_mean/total`, `wip_mean/max/little`,
  `printer_queue_mean/max`, `printer_wait_h_mean/p95`
- 프린터 대기열 규칙 `URGENT_FIRST` (긴급 부품이 든 배치 먼저, 같은 등급은 FCFS), `Batch.has_urgent`.
  현재 가정값에서는 배치 대부분에 긴급 부품이 섞여 FCFS와 결과 동일 (Rush Order seed 42 확인)
- 설정 `URGENT_BATCH_MAX_WAIT_TIME` (기본값 `None` = 기존 혼합 배치, `config.py` `DEFAULTS`): 분포를 주면 긴급 부품을
  긴급 전용 배치로 모으고 이 대기 한도를 적용. `Batch.urgent`. **실험 결과 기본값 `None` 유지 권장** (GUIDE §6 참고)
- `src/experiments/scenarios.py` + `main.py --preset`: 실험 프리셋. `Rush Order` = Normal λ + 긴급 비율 30% (가정값)
- `src/validation/invariants.py`: `check(res)` 불변식 검사 (용량·근무시간·배치·주문 보존·정비 중 작업·이벤트 순서)
- `bottleneck(res)`, `daily_table(res)` + `daily_summary.csv` (random 모드)
- `main.py`: `--keep-events`, `--weeks`, `--reps` 반복별 결과 CSV(`replications_<시나리오>.csv`),
  옵션 조합 검사(종료코드 2)
- 이벤트: `PRINTER_QUEUE_ENTER`, `WASHING_QUEUE_ENTER`, `UV_CURING_QUEUE_ENTER`,
  `MAINTENANCE_START/END`, `CLEANING_START/END`
- `src/__init__.py`: `__version__ = "0.1.0"` (버전 표기)
- `--reps` 요약표(`main.py` `KEY_KPIS`)에 `throughput_per_week`, `wip_mean`, `printer_queue_mean`,
  `printer_wait_h_mean`, `tardiness_work_h_mean` 추가

### Changed
- 프린터 대기열 규칙을 `src/scheduler/dispatch.py`(`RULES`)로 분리 — `SimConfig`의 허용 규칙도 여기서 읽음
- `on_time_*`: 종료 시 미완료인데 납기가 지난 주문을 지연으로 셈
- 사람 작업 `*_START`(검사·포장 포함)를 근무시간 시작 시각에 기록 — KPI 수치는 변화 없음
  (csv 샘플 결과 이전과 동일 확인)
- 사람 작업 `*_START`의 `detail` `wait=`에 근무시간 대기까지 포함 (이전: 자원 대기만)
- `--weeks N` 지정 시 `WARMUP_TIME = 0`, `SIMULATION_TIME = N×168` — KPI에 초기 빈 공장 상태가 섞임 (조회·Replay용)
- `--preset` 사용 시 반복 결과 파일명·요약표 제목을 프리셋 이름으로 표기 (`replications_Rush_Order.csv`)
- `BATCH_OPENED` 이벤트 `detail`에 긴급 전용 배치면 `urgent` 표시
- `.gitignore`에 `data/private/` 추가

### Fixed
- `kpis()`에서 빠졌던 `lead_work_h_*`, `lead_work_rework_h_*`, `lead_calendar_h_*` 복원
- `kpi.py` 들여쓰기 오류, `simulation.py` 클래스 본문의 잘못된 코드 제거
- `throughput_per_week`, `daily_table`: 종료 시각에 완료된 주문 포함 (csv 모드에서 마지막 주문 1건이 빠지던 문제)
- 주문 0건(측정 구간 길이 0)일 때 `kpis()`의 `ZeroDivisionError` -> `wip_*`, `printer_queue_*` 는 NaN

### Documentation
- README: 현재 버전·CHANGELOG 링크 추가, 파라미터 표기를 "최종 확정값" -> **"가정값(인터뷰 확인 전)"**으로 정정,
  새 옵션(`--weeks`, `--keep-events`, `--preset`) 예시·결과 파일·옵션 조합 오류 안내, `--reps` 소요시간(8코어 약 1분 · 4코어 약 8분)
- GUIDE: `scheduler/dispatch.py`, `experiments/scenarios.py`, `validation/invariants.py` 설명, 새 KPI 표·출력 파일 표,
  이벤트 로그 규칙, 새 테스트 파일 표 추가
- GUIDE §6: 긴급 전용 배치 실험 결과표 (2026-09-28, Rush Order 긴급 30%, 워밍업 5주 + 측정 20주, 시드 3개 평균)
  — 혼합 배치(기본값 `None`)가 납기 준수율 86.6%로 가장 좋음. 긴급 준수율 한계(약 57%)는 긴급 납기 여유 < 평균 리드타임에서 옴
- GUIDE: `printer_rho` 79.9%를 캘리브레이션 확인값으로 표기

### Tests
- `tests/test_kpi_events.py`: 계단함수 손계산, KPI 숫자 형식, Little 법칙, 미완료 지연 판정, Tardiness,
  병목, 일별 표 합계, QUEUE_ENTER→START 순서, 정비·세척 START/END 짝, 사람 작업 START 근무시간, 옵션 조합 종료코드 2
- `tests/test_scheduling.py`: 프린터 1대에 배치 3개가 대기할 때 FCFS/SPT/EDD/URGENT_FIRST 출력 순서 = 손계산 정답
- `tests/test_invariants.py`: 설정 11종 + 가정값(csv·random 2주)에서 불변식 위반 0건, 조작한 결과 5종 검출
- `tests/test_urgent_batch.py`: 기본값 혼합 유지, 긴급·일반 분리, 긴급 대기 한도, 긴급 리드타임 단축(고정 설정), 재출력 부품도 긴급 배치
- `tests/test_scenarios.py`: 프리셋 설정 유효성, 긴급 비율만 바꾸면 도착 패턴 동일, `--preset` 옵션 오류 종료코드 2
- `tests/test_extreme.py` (Level 5 극한 조건): 수요 0, 주문 1건 리드타임 = 처리시간 합(46.5h), 자원 무한 -> 대기 0,
  과부하 150% -> WIP 누적, 불량 99% -> SHORT, 불량 50% + 재출력 -> 전부 완료, 용량 0·불량률 1 설정 거부

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
