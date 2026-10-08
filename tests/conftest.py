# -*- coding: utf-8 -*-
"""
테스트 공통 설정.

LOGIC_CFG: parameters.py 와 무관한 '로직 검증용 고정 설정' (팀원 1 초안의 임시값 기반, 고정 시간).
  -> 가정값(parameters.py)을 바꿔도 로직 테스트는 깨지지 않고, 손계산으로 결과를 확인할 수 있다.
"""
import pytest

from src.model.config import SimConfig
from src.model.simulation import VPPSimulation

LOGIC_OVERRIDES = dict(
    ORDER_SOURCE="csv", ORDER_CSV_PATH="data/sample_orders.csv", WARMUP_TIME=0, SIMULATION_TIME=10_000,
    USE_WORK_CALENDAR=False, BREAKDOWN_ENABLED=False, INSPECTION_FAILURE_RATE=0.0, PRINT_FAILURE_RATE=0.0,
    REWORK_ENABLED=False, CLEANING_LIQUID_CHANGE_EVERY_LOADS=None,
    BUILD_PLATE_MAX_PARTS=4, BUILD_PLATE_AREA_MM2=None, BATCH_FILL_RATIO=None,
    BATCH_MAX_WAIT_TIME=("const", 24, "h"),
    WASHING_LOAD_CAPACITY=2, UV_CURING_LOAD_CAPACITY=2,
    JOB_ASSIGNMENT_TIME=("const", 1, "h"), BUILD_PREPARATION_TIME=("const", 2, "h"),
    VPP_BUILD_TIME_MODE="fixed", VPP_BUILD_TIME=("const", 5, "h"),
    PART_REMOVAL_TIME=("const", 1, "h"), PART_REMOVAL_TIME_PER_PART=("const", 0, "h"),
    WASHING_TIME=("const", 2, "h"), UV_CURING_TIME=("const", 3, "h"), LOAD_HANDLING_TIME=("const", 0, "h"),
    SUPPORT_REMOVAL_TIME=("const", 2, "h"), SURFACE_TREATMENT_TIME=("const", 2, "h"),
    INSPECTION_TIME=("const", 1, "h"), PACKAGING_TIME=("const", 1, "h"),
    JOB_ASSIGNMENT_WORKER_COUNT=1, POST_PROCESS_WORKER_COUNT=2, QUALITY_INSPECTOR_COUNT=1, PACKER_COUNT=0,
    TRANSPORT_ENABLED=True, DEFAULT_TRANSPORT_TIME=("const", 0.5, "h"),
    TRANSPORT_MODE="worker", MOVE_TIME_MODE="fixed",           # 기존 방식: 사람 운반 0.5h 고정, 빈손 이동 없음
    DEFAULT_SCHEDULING_RULE="FCFS",
)


@pytest.fixture
def logic_cfg():
    return SimConfig.from_parameters().replace(**LOGIC_OVERRIDES)


@pytest.fixture
def run_logic(logic_cfg):
    def _run(**overrides):
        return VPPSimulation(logic_cfg.replace(**overrides)).run()
    return _run
