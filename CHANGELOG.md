# Changelog

## v0.1.1 - 2026.10.07

### Added
- Streamlit 대시보드 `dashboard/app.py` [명세서 11절 Dashboard]: `python -m streamlit run dashboard/app.py`
  - 사이드바: 시나리오(프리셋 포함) · 기간(1~4주, 워밍업 0) · 시드 · 프린터 순서 규칙 -> [실행]. 같은 설정은 캐시 재사용
  - 탭 5개: KPI 요약 / 시간 추이 / 장비 상태 타임라인 / 2D 공장 Replay / 3D 공장 Replay
  - 주문 추적: 주문 ID 입력 -> 전체 이벤트 표 [명세서 14절 Level 2]
- `dashboard/data.py`: 대시보드용 실행 · 표 가공, Replay 시각별 상태 재구성 `replay_states` (`state_at` 을 방 단위로 묶음),
  화면용 방 배치 `ROOM_LAYOUT` # 임의 배치
- 2D 공장 Replay `dashboard/replay.py` [명세서 13절]: Event Log -> State Reconstruction -> plotly 애니메이션.
  설비(상태 6종 색) · 작업자(작업 중/대기) · 방별 주문 수(작업 · 대기) · 설비 대기열, 마우스 올리면 공정별 주문 수 · 주문 ID
- 3D 공장 Replay `dashboard/replay3d.py` [명세서 13절]: 2D Replay 와 같은 시각별 상태를 3D 로 재생.
  설비 상자(상태 6종 색, 가동 중 초록 불 · 고장 빨간 불) · 작업자(방 이동은 복도 경유) · 주문 상자(작업 중/대기) · 방별 주문 수와 대기열.
  Play / Pause / ×1 ×2 ×5 ×10 / 타임라인 슬라이더, 드래그 회전 · 휠 확대 · 오른쪽 드래그 이동 · 시점 버튼(기본 / 위에서 / 자동 회전),
  마우스 올리면 상태 표시. 외부 3D 라이브러리 없이 Canvas 로 그림 (오프라인 동작, 추가 패키지 없음)
- requirements: `streamlit>=1.50`, `plotly>=5.18`

### Changed
- 2D · 3D Replay: 날짜 선택 없이 시뮬레이션 전체 기간(Day 1 09:00 ~ 종료)을 연속 재생.
  슬라이더에 날짜 눈금 `D1(월)`, `D2(화)` …, 시간 간격 15/30/60/120분(기본 1주 = 30분, 2주 이상 = 60분),
  프레임 1000개 초과 시 경고, 2D 재생 속도에 "매우 빠르게" 추가
- 주문은 온라인 접수 -> `Order Desk` 방 제거. `LOCATIONS` 의 Order Reception · Job Assignment · Batch Formation 을
  `Print Room` 으로 변경 (KPI 영향 없음, 이벤트 로그 location 열만 변경)
- Replay 방 배치: 윗줄을 "프린터실 (작업 배정·출력)" · 후공정실 2개로 재배치, 작업 배정 작업자(JA)는 프린터실에 표시

### Documentation
- README: 대시보드 실행 명령, `## 대시보드` 섹션(탭 구성 · Replay 사용법 · 3D 조작법), `dashboard/` 폴더 구조,
  이벤트 로그 location 설명(주문 데스크 없음), 공정 흐름에 온라인 접수 표기

### Notes
- 대시보드 · Replay 는 화면 코드만 추가 · 변경. 시뮬레이션 로직 · 가정값 · KPI 계산은 변경 없음
- 3D Replay 는 테스트용 상태 데이터로 브라우저 동작(재생 · 회전 · 시점 전환 · 툴팁) 확인. 실제 시뮬레이션 결과로 화면 확인 필요
- `outputs/event_log.csv` 는 이전 실행 결과라 location 열에 `Order Desk` 가 남아 있음 -> 다시 실행하면 갱신


## v0.1.0 - 2026.10.01

### Added
- 주문 속성 [명세서 4절]
  - `Order.required_process`: 필요한 후공정. 기본 = 전체, 서포트 제거·표면처리만 생략 가능. CSV 선택 열 `required_process`
  - `Order.estimated_build_time`: 부품 높이로 계산한 단독 출력 예상시간
  - `Order.current_process`, `Order.current_state`: 현재 공정·상태
  - `order_summary.csv`에 위 항목과 `product_id` 열 추가
- KPI [명세서 12절]: 처리량, Tardiness, WIP(Little 법칙 포함), 프린터·세척·UV·인력별 대기시간·대기열 길이
- 작업자 개인 ID (`JA1..`, `PP1..`, `QI1..`, `PK1..`): 이벤트에 누가 했는지 기록, 개인별 가동률 [명세서 1·10절]
- 장비 상태 6종 (Idle / Setup / Running / Waiting / Down / Maintenance): 상태 전환 이벤트, 상태별 시간 KPI.
  프린터 출력 = 셋업 + 층 출력 분리, 고장(`DOWN`)과 PM(`MAINTENANCE`) 구분 [명세서 11절]
- 이벤트 로그 권장 형식: `sim_time, order_id, entity_type, entity_id, event, process, location, state`
  (+ `resource, detail`). `parameters.LOCATIONS`는 가정 방 이름 [명세서 8.1·10절]
- OME 시각 조회: `state_at(res, t)`, `main.py --at "Day 3 14:25"` (주문 위치·WIP·설비 상태·작업자·대기 수·레진) [명세서 1·15·18절]
- 주문 추적: `main.py --trace O001` [명세서 14절 Level 2]
- 불변식 검사 `check(res)`: 용량·근무시간·주문 보존·작업자 작업 겹침·공정 순서·이동 순서 [명세서 14절 Level 3]
- 프린터 0대 허용 (생산량 0, 오류 없음) [명세서 14절 Level 5]
- 프린터 대기열 규칙 `URGENT_FIRST` 추가, 규칙은 `src/scheduler/dispatch.py`로 분리 [명세서 6절]
- 긴급 전용 배치 설정 `URGENT_BATCH_MAX_WAIT_TIME` (기본값 `None` = 혼합 배치). 실험 결과 기본값 유지 권장 (GUIDE §6)
- 실험 프리셋 `main.py --preset` (`Rush Order` = Normal + 긴급 30%)
- `bottleneck(res)`, 일별 표 `daily_summary.csv`, 반복별 결과 `replications_<시나리오>.csv`
- `main.py` 옵션: `--keep-events`, `--weeks`, `--reps`

### Changed
- 인력 자원: 역할 단위 대기열 + 개인 ID. 빈 사람 중 번호가 가장 작은 사람에게 배정 (KPI 불변) [명세서 1·10절]
- 이벤트 로그 열 순서·이름을 권장 형식으로 변경 [명세서 10절]
- `on_time_*`: 종료 시 미완료인데 납기가 지난 주문을 지연으로 셈
- 장비 상태: 세척·UV 적재·인출이 점심·퇴근에 걸려 멈춘 시간을 Setup이 아니라 Waiting으로 집계 [명세서 11절]

### Fixed
- `kpis()`에서 빠졌던 리드타임 KPI(`lead_work_h_*`, `lead_calendar_h_*` 등) 복원
- 처리량·일별 표에서 종료 시각에 완료된 마지막 주문이 빠지던 문제
- 주문 0건일 때 `kpis()`의 `ZeroDivisionError`

### Tests
- `test_order_model.py`: 주문 속성 [명세서 4절]
- `test_simple_case.py`: 명세서 예시 손계산(프린터 1대·주문 2개), 주문 1건 이벤트 39개 = 손계산 [명세서 14절 Level 2·4]
- `test_invariants.py`: 설정 12종에서 위반 0건, 조작한 결과 검출 [Level 3]
- `test_kpi_events.py`: KPI 손계산, Little 법칙, 이벤트 형식 [명세서 10·12절]
- `test_workers.py`: 작업자 ID·배정·작업 겹침 검출 [Level 3]
- `test_machine_states.py`: 장비 상태 구간·고장·PM [명세서 11절]
- `test_state_query.py`: 시각 조회 결과를 시뮬레이션 기록과 대조 [Level 2, 18절]
- `test_scheduling.py`: FCFS/SPT/EDD/URGENT_FIRST 출력 순서 = 손계산
- `test_urgent_batch.py`, `test_scenarios.py`: 긴급 배치·프리셋
- `test_extreme.py`: 수요 0, 자원 무한 → 대기 0, 과부하 → WIP 누적, 프린터 0대 → 완료 0 [명세서 14절 Level 5]

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
