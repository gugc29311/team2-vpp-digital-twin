# -*- coding: utf-8 -*-
"""
VPP Digital Twin - 시뮬레이션 기본 파라미터 (기본값 모음)

규칙
  1. 여기에는 '공장·공정 설정값'만 둔다. 주문별 정보(order_id, due_date 등)는 data/*.csv 에 둔다.
  2. 시간은 '분포 정의' 튜플로 적고, 마지막 원소에 단위를 쓴다.
       ("const", 값, 단위) | ("tri", 최소, 최빈, 최대, 단위) | ("uniform", 최소, 최대, 단위) | ("exp", 평균, 단위)
     단위: "h" | "min" | "s"  (내부 시간 단위는 항상 hour). 단위 없는 튜플은 시간이 아닌 값(면적 등).
  3. 실험(시나리오·반복)에서는 이 파일을 고치지 말고
     SimConfig.from_parameters().replace(...) 로 값을 바꿔서 실행한다.

값의 출처
  - [n번] / [공통n] = 가정값 로그(vpp-assumptions-log.md)의 해당 절. 근거·검증 결과는 로그 참고.
  - [권장안] = 로그 검토 후 바꾼 값 (T=8h, 같은 형상 재출력, Build Prep=0) — 로그에 반영 필요.
  - ⚠️ 표시는 로그에서 '인터뷰 확인 필요'로 남은 값.

시간 기준 (중요)
  - USE_WORK_CALENDAR = True: 시계는 달력시간 (t=0 = 월요일 09:00).
    사람 작업·세척·UV·정비는 '근무시간'만 소비하고, 프린터 출력만 밤·주말에도 진행(무인운전).
  - '근무시간 h' 라고 적힌 값은 근무시간 기준 (예: T=8h = 1근무일).
"""

# =========================================================
# Simulation  [공통5]
# =========================================================
TIME_UNIT = "hour"
WEEK_H = 168                         # 달력 1주
WARMUP_TIME = 5 * WEEK_H             # [h] 5주 워밍업 (KPI 에서 제외)
SIMULATION_TIME = WARMUP_TIME + 100 * WEEK_H   # [h] 워밍업 + 100주 측정
RANDOM_SEED = 42
REPLICATIONS = 30                    # 독립 반복 횟수 (명세서 Level 8)
REPLICATION_ROOT_SEED = 20260927     # 반복별 시드 생성용 루트

# =========================================================
# Order generation  [1번 · 공통7]
# =========================================================
ORDER_SOURCE = "csv"                 # "csv" = data 파일 재생 (공정 통과 확인용) | "random" = 확률적 생성 (실험용)
ORDER_CSV_PATH = "data/sample_orders.csv"

# 시나리오별 주문 도착률 λ [건/주, 주 40 근무시간 기준]  [1번 최종 재캘리브레이션]
#   Normal / High Demand = 프린터 유효 처리용량 대비 부하율 80% / 92% (정의 B)
#   Stress = 부하율 97% [권장안: 구 λ 기준 '2 x Normal', 'λ=600' 을 부하율 기준으로 재정의,
#            이 틀로 탐색 후 30회 반복 평균 ρ = 97.0% [96.9, 97.2] 확인]
SCENARIO = "Normal"
SCENARIO_ARRIVAL_RATES = {"Normal": 629.7, "High Demand": 723.4, "Stress": 764.5}
ORDER_ARRIVAL_RATE_PER_WEEK = None   # None = SCENARIO 값 사용. 숫자를 넣으면 그 값으로 덮어씀
# 도착간격 = Exp(평균 = 주당 근무시간 / λ) — 근무시간에만 도착 (random 모드)

ORDER_QUANTITY = ("const", 1)                      # [공통7] 주문 1건 = 부품 1개 ⚠️
ORDER_AREA_MM2 = ("uniform", 400, 1600)            # [1번] 부품 바닥 면적
ORDER_HEIGHT_MM = ("tri", 20, 40, 80)              # [4번] 부품 높이
ORDER_MATERIALS = {"Resin_A": 1.0}                 # [공통7] 단일 레진 가정 ⚠️
URGENT_PROBABILITY = 0.10                          # [공통7] 긴급 10% ⚠️
DUE_DATE_SLACK_NORMAL = ("tri", 16, 24, 40, "h")   # [공통7] 근무시간 = Tri(2,3,5) 근무일
DUE_DATE_SLACK_URGENT = ("tri", 4, 8, 16, "h")     # [공통7] 근무시간 = Tri(0.5,1,2) 근무일 ⚠️

# =========================================================
# Equipment  [4 · 6 · 7번]
# =========================================================
VPP_PRINTER_COUNT = 2                # ⚠️ 인터뷰 최우선
WASHING_MACHINE_COUNT = 1
UV_CURING_MACHINE_COUNT = 1

# =========================================================
# Build plate / batch formation  [3번]
#   배치 확정: 면적 채움률(S) 또는 최장 대기(T) 중 먼저. 같은 재료끼리, 도착 순서대로 채움.
# =========================================================
BUILD_PLATE_MAX_PARTS = None         # 부품 수 상한 없음 (면적 기준)
BUILD_PLATE_AREA_MM2 = 40_000        # 200 x 200 mm
BATCH_FILL_RATIO = 0.70              # S = 28,000 mm²
BATCH_MAX_WAIT_TIME = ("const", 8, "h")   # T = 근무시간 8h = 1근무일 [권장안: 기존 24h(=3근무일)에서 변경]
BATCH_SAME_MATERIAL_ONLY = True

# =========================================================
# Washing / UV curing  [6 · 7번]
# =========================================================
WASHING_LOAD_CAPACITY = 10           # 부품/로드 ⚠️
UV_CURING_LOAD_CAPACITY = 15         # 부품/로드 ⚠️

# =========================================================
# Processing time   (단위) 주문당 / 배치당 / 로드당 / 부품당
# =========================================================
JOB_ASSIGNMENT_TIME = ("tri", 2, 5, 12, "min")      # [2번] 주문당 ⚠️
BUILD_PREPARATION_TIME = ("const", 0, "h")          # 배치당 [권장안: 로그에 별도 정의 없음 — 준비는 출력 셋업 0.5h 에 포함]

VPP_BUILD_TIME_MODE = "height"                      # [4번] 셋업 + ceil(최대높이/레이어) x 레이어당 시간
VPP_BUILD_TIME = ("const", 3.7, "h")                # fixed 모드일 때만 사용 (참고: 최종 평균 3.70h)
BUILD_SETUP_TIME = ("const", 0.5, "h")              # [4번] 준비·캘리브레이션·드레인
LAYER_THICKNESS_MM = 0.05                           # [4번]
TIME_PER_LAYER = ("const", 8, "s")                  # [4번] 경화 3초 + 리코팅 5초

PART_REMOVAL_TIME = ("tri", 1, 2, 4, "min")         # [5번] 배치당 (플레이트 탈착) ⚠️
PART_REMOVAL_TIME_PER_PART = ("tri", 10, 20, 40, "s")   # [5번] 부품당 ⚠️
WASHING_TIME = ("tri", 5, 10, 15, "min")            # [6번] 로드당 ⚠️
UV_CURING_TIME = ("tri", 10, 15, 25, "min")         # [7번] 로드당 ⚠️
LOAD_HANDLING_TIME = ("tri", 1, 2, 3, "min")        # [7번] 로드당 적재+인출 (반씩), 설비 점유에 포함
SUPPORT_REMOVAL_TIME = ("tri", 30, 60, 150, "s")    # [8번] 부품당 ⚠️
SURFACE_TREATMENT_TIME = ("tri", 60, 120, 300, "s") # [9번] 부품당, 모든 부품에 적용 ⚠️
INSPECTION_TIME = ("tri", 20, 40, 90, "s")          # [10번] 부품당 전수검사 ⚠️
PACKAGING_TIME = ("tri", 30, 60, 120, "s")          # [11번] 부품당 (합격품만) ⚠️

# =========================================================
# Human resources  [최종 하류 인력 재검증]
#   JOB_ASSIGNMENT_WORKER : Job Assignment (주문·재출력 주문), Build Preparation
#   POST_PROCESS_WORKER   : Part Removal, 세척·UV 적재/인출, Support Removal, Surface Treatment, 이동 ①~④
#   QUALITY_INSPECTOR     : Inspection + Packaging 겸직 (PACKER_COUNT = 0), 이동 ⑤
#   PRINTER_OPERATOR      : 로그에 작업 정의 없음 -> 0 (모델에서 사용 안 함) [권장안] ⚠️ 인터뷰 확인
# =========================================================
JOB_ASSIGNMENT_WORKER_COUNT = 3
POST_PROCESS_WORKER_COUNT = 3
QUALITY_INSPECTOR_COUNT = 1
PACKER_COUNT = 0
PRINTER_OPERATOR_COUNT = 0

# =========================================================
# Working calendar  [공통1]
# =========================================================
USE_WORK_CALENDAR = True
WORK_DAYS = 5                        # 월~금
WORK_WINDOWS = [(9.0, 12.0), (13.0, 18.0)]   # 점심 12~13 제외, 하루 8h
PRINTER_UNATTENDED = True            # 투입은 근무시간에만, 출력은 밤·주말에도 계속 ⚠️ 인터뷰 최우선

# =========================================================
# Transportation  [공통4]  받는 쪽 작업자가 소모, 설비는 점유하지 않음
#   ① 출력→탈거(배치) ② 탈거→세척(세척 로드) ③ 세척→UV(UV 로드) ④ UV→서포트(UV 로드) ⑤ 표면처리→검사(배치)
# =========================================================
TRANSPORT_ENABLED = True
DEFAULT_TRANSPORT_TIME = ("tri", 1, 2, 4, "min")    # ⚠️ 레이아웃 의존

# 공정(명세서 8.1절) -> 방 이름. 이벤트 로그 location 열에만 쓰임 (좌표·거리·이동시간과 무관)
# ⚠️ 가정값 — 실제 공장 배치는 인터뷰 확인 필요
LOCATIONS = {
    "Order Reception": "Order Desk",
    "Job Assignment": "Order Desk",
    "Batch Formation": "Order Desk",
    "VPP Build": "Print Room",
    "Part Removal": "Post-processing Room",
    "Washing": "Wash Room",
    "UV Curing": "UV Room",
    "Support Removal": "Post-processing Room",
    "Surface Treatment": "Post-processing Room",
    "Inspection": "Inspection Room",
    "Packaging": "Packing Room",
    "Transport": "Corridor",
}

# =========================================================
# Scheduling  [공통2 · 공통7]
# =========================================================
DEFAULT_SCHEDULING_RULE = "FCFS"

# =========================================================
# Failure / rework  [10번]
# =========================================================
PRINT_FAILURE_RATE = 0.0
INSPECTION_FAILURE_RATE = 0.04       # ⚠️ 인터뷰 최우선
REWORK_ENABLED = True                # 불량 -> 폐기 후 재출력 (Job Assignment 부터)
REWORK_SAME_GEOMETRY = True          # True = 같은 부품(면적·높이) 재출력 [권장안] / False = 분포에서 새로 추출(기존 검증 방식)

# =========================================================
# Breakdown / PM  [공통6]   비선점: 진행 중 작업은 끝까지, 다음 투입 직전에 점검
#   mtbf     : 고장 간 '가동시간' Exp 평균 [h]
#   repair   : 수리시간 분포
#   pm_every : PM 주기 [달력 h]
#   pm       : PM 소요 분포
#   pm_offsets: 대별 첫 PM 기준점 어긋남 [h] (프린터 2대 PM 동시 방지)
# =========================================================
BREAKDOWN_ENABLED = True
MAINTENANCE_IN_WORK_HOURS_ONLY = True     # 수리·PM 은 근무시간에만 진행 ⚠️ 인터뷰 확인
EQUIPMENT_FAILURE = {
    "printer": {"mtbf": 250.0, "repair": ("tri", 2, 4, 8, "h"),
                "pm_every": 4 * WEEK_H, "pm": ("tri", 1, 2, 3, "h"), "pm_offsets": [0.0, 2 * WEEK_H]},
    "washing": {"mtbf": 400.0, "repair": ("tri", 1, 2, 4, "h"),
                "pm_every": 8 * WEEK_H, "pm": ("tri", 0.5, 1, 1.5, "h"), "pm_offsets": [0.0]},
    "uv_curing": {"mtbf": 400.0, "repair": ("tri", 1, 2, 4, "h"),
                  "pm_every": 8 * WEEK_H, "pm": ("tri", 0.5, 1, 1.5, "h"), "pm_offsets": [0.0]},
}
# 프린터 유효 처리용량 [h/주]: 포화 시 주당 최대 출력시간 (1번 정의 B 의 분모)
#   None 이면 부하율(ρ) KPI 생략. 설정 변경 후 재측정: python -m src.analysis.capacity
PRINTER_EFFECTIVE_CAPACITY_H_PER_WEEK = 106.4

# =========================================================
# Consumables  [공통3]   재고 제약 없음 (소모량 추적만)
# =========================================================
RESIN_TRACKING = True
RESIN_FILL_RATIO = ("tri", 0.20, 0.30, 0.45)         # 부품 부피 = 면적 x 높이 x 충전률 ⚠️
RESIN_SUPPORT_RATIO = 0.15                           # 서포트 추가 소모 ⚠️
CLEANING_LIQUID_CHANGE_EVERY_LOADS = 50              # 세척 로드 N회마다 교체 (None = 교체 없음) ⚠️
CLEANING_LIQUID_CHANGE_TIME = ("tri", 10, 15, 25, "min")   # 교체 동안 세척기 사용 불가 ⚠️
