# -*- coding: utf-8 -*-
"""실험 프리셋(src/experiments/scenarios.py) 테스트."""
import subprocess
import sys

import pytest

from src.analysis.kpi import kpis
from src.experiments.scenarios import PRESETS, apply_preset
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation
from src.validation.invariants import check


@pytest.mark.parametrize("name", list(PRESETS))
def test_every_preset_is_a_valid_config(name):
    cfg = apply_preset(SimConfig.from_parameters(), name)       # 없는 키·잘못된 값이면 여기서 오류
    assert cfg.ORDER_SOURCE == "random"


def test_unknown_preset_is_rejected():
    with pytest.raises(KeyError, match="Rush"):
        apply_preset(SimConfig.from_parameters(), "Rush")


def test_rush_order_keeps_normal_demand():
    base, rush = SimConfig.from_parameters(), apply_preset(SimConfig.from_parameters(), "Rush Order")
    assert rush.arrival_rate_per_week == base.replace(SCENARIO="Normal").arrival_rate_per_week
    assert rush.URGENT_PROBABILITY > base.URGENT_PROBABILITY


def _short_run(logic_cfg, urgent):
    cfg = apply_preset(logic_cfg, "Rush Order").replace(URGENT_PROBABILITY=urgent, SIMULATION_TIME=600,
                                                        ORDER_ARRIVAL_RATE_PER_WEEK=15, RANDOM_SEED=4)
    return VPPSimulation(cfg).run()


def test_urgent_share_changes_but_arrivals_do_not(logic_cfg):
    """긴급 비율만 바꾸면 도착 시각·면적·높이는 같다 (같은 시드 = 공정한 비교)."""
    low, high = _short_run(logic_cfg, 0.10), _short_run(logic_cfg, PRESETS["Rush Order"]["URGENT_PROBABILITY"])
    geo = lambda r: [(o.arrival_time, o.area_mm2, o.height_mm, o.quantity) for o in r.orders]
    assert geo(low) == geo(high)
    share = lambda r: sum(o.is_urgent for o in r.orders) / len(r.orders)
    assert share(high) > share(low)
    assert share(high) == pytest.approx(0.30, abs=0.1)
    assert check(high) == []
    assert kpis(high)["on_time_urgent"] <= 1.0


@pytest.mark.parametrize("argv", [["--preset", "Rush Order", "--mode", "csv"],
                                  ["--preset", "Rush Order", "--scenario", "Stress"],
                                  ["--preset", "Nope"]])
def test_main_rejects_bad_preset_usage(argv):
    r = subprocess.run([sys.executable, "main.py", *argv], capture_output=True)
    assert r.returncode == 2, r.stderr.decode(errors="replace")
