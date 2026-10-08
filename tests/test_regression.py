# -*- coding: utf-8 -*-
"""
회귀 테스트 (느림, 기본 실행에서 제외): 가정값(parameters.py)으로 돌린 결과가
가정값 로그의 최종 검증값 범위 안에 있는지 확인. 가정값을 바꾸면 여기 기대 범위도 함께 갱신할 것.

실행:  python -m pytest -m slow
기준:  가정값 로그 '최종 하류 인력 재검증' 30회 반복 결과 (단일 run 변동을 감안해 ±2~3%p 여유)
"""
import pytest

from src.analysis.kpi import kpis
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation

# KPI: (하한, 상한) — Normal, seed=42 단일 run
EXPECTED_NORMAL = {
    "printer_rho": (0.77, 0.83),
    "util_job_assignment_workers": (0.55, 0.61),
    # 2026-10-07: 이동을 '거리 ÷ 속도 + AMR 운반'으로 바꾸며 운반 1회가 평균 2.3분 -> 수십 초로 줄어 46% 대로 낮아짐
    #             (기존 Tri(1,2,4)분 사람 운반 기준 범위는 0.48~0.53). seed 42: AMR 0.463 / 사람 운반 0.455
    "util_post_process_workers": (0.44, 0.49),
    "util_quality_inspectors": (0.53, 0.59),
    "mean_batch_size": (28.0, 29.0),
    "mean_build_time_h": (3.65, 3.75),
    "lead_work_h_mean": (8.3, 9.4),
    "rework_share_printed": (0.03, 0.05),
}


@pytest.mark.slow
def test_normal_scenario_matches_validated_values():
    cfg = SimConfig.from_parameters().replace(ORDER_SOURCE="random", SCENARIO="Normal", RANDOM_SEED=42)
    k = kpis(VPPSimulation(cfg, keep_events=False).run())
    bad = {key: (round(k[key], 4), rng) for key, rng in EXPECTED_NORMAL.items() if not rng[0] <= k[key] <= rng[1]}
    assert not bad, f"검증값 범위를 벗어남: {bad}"
