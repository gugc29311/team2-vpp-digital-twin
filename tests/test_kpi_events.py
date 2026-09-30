# -*- coding: utf-8 -*-
"""
v0.1.0 에서 추가한 KPI · 이벤트 로그 · main.py 옵션 검사 (고정 설정 LOGIC_CFG 사용 — parameters.py 값과 무관).
실행: 프로젝트 최상위 폴더에서  python -m pytest
"""
import csv
import math
import numbers
import subprocess
import sys

import pytest

from src.analysis.kpi import _level_series, _step_stats, bottleneck, daily_table, kpis, save_outputs, utilization
from src.entities.order import Order
from src.model.simulation import VPPSimulation

HUMANS = ("job_assignment_workers", "post_process_workers", "quality_inspectors", "packers")


def one_order(due, arrival=0.0):
    return [Order("O1", "P1", arrival, 1, "Resin_A", due, "NORMAL", 900.0, 40.0)]


# ---------------------------------------------------------------- 계단함수 도구
def test_step_stats_hand_calculation():
    s = _level_series([(0, 4), (2, None)])                 # 0~2: 1개, 2~4: 2개, 4~: 1개(진행 중)
    assert s == [(0, 1), (2, 2), (4, 1)]
    assert _step_stats(s, 0, 6) == pytest.approx((8 / 6, 2))   # (1x2 + 2x2 + 1x2) / 6
    assert _step_stats(s, 1, 3) == pytest.approx((1.5, 2))     # (1x1 + 2x1) / 2


# ---------------------------------------------------------------- kpis() 형식
def test_kpis_are_all_numeric(run_logic):
    """문자열 KPI 가 섞이면 replications.summarize 가 깨짐 — bottleneck 은 kpis() 밖에 둔다."""
    k = kpis(run_logic())
    bad = {key: v for key, v in k.items() if not isinstance(v, numbers.Real) or isinstance(v, bool)}
    assert not bad
    for key in ("lead_work_h_mean", "lead_work_h_p95", "lead_calendar_h_mean", "lead_calendar_h_p95",
                "throughput_per_week", "tardiness_work_h_mean", "wip_mean", "wip_little",
                "printer_queue_mean", "printer_wait_h_mean"):
        assert key in k, key


# ---------------------------------------------------------------- 처리량 · WIP · Little
def test_throughput_and_little_law_when_all_orders_finish(run_logic):
    """csv 샘플은 전부 완료 후 종료 -> WIP 면적 = 리드타임 합 이므로 wip_mean == wip_little (정확히)."""
    res = run_logic()
    k = kpis(res)
    assert k["completion_ratio"] == 1.0
    assert k["throughput_per_week"] == pytest.approx(k["orders_per_week"])
    assert k["wip_mean"] == pytest.approx(k["wip_little"])
    assert k["wip_mean"] == pytest.approx(sum(o.completed_time - o.arrival_time for o in res.orders) / res.end_time)
    assert 1 <= k["wip_max"] <= len(res.orders)


def test_little_law_in_random_mode(logic_cfg):
    cfg = logic_cfg.replace(ORDER_SOURCE="random", SIMULATION_TIME=2000, WARMUP_TIME=200,
                            ORDER_ARRIVAL_RATE_PER_WEEK=10, RANDOM_SEED=3)   # JA 부하 25% (40이면 100%, 정상상태 없음)
    k = kpis(VPPSimulation(cfg, keep_events=False).run())
    assert k["wip_mean"] == pytest.approx(k["wip_little"], rel=0.05)
    assert k["throughput_per_week"] == pytest.approx(k["orders_per_week"] * k["completion_ratio"], rel=0.05)


# ---------------------------------------------------------------- 납기 · Tardiness
def test_unfinished_overdue_order_counts_as_late(logic_cfg):
    """종료 시 미완료 + 납기 경과 -> 지연 (예전에는 분모에서 빠져 NaN 이었음)."""
    res = VPPSimulation(logic_cfg.replace(SIMULATION_TIME=5), orders=one_order(due=1.0)).run()
    assert res.orders[0].completed_time is None
    assert kpis(res)["on_time_all"] == 0.0


def test_unfinished_order_not_yet_due_is_excluded(logic_cfg):
    res = VPPSimulation(logic_cfg.replace(SIMULATION_TIME=5), orders=one_order(due=999.0)).run()
    assert math.isnan(kpis(res)["on_time_all"])


def test_tardiness_hand_calculation(logic_cfg):
    res = VPPSimulation(logic_cfg, orders=one_order(due=1.0)).run()     # 24h 시계, 한참 늦게 완료
    o = res.orders[0]
    k = kpis(res)
    assert k["on_time_all"] == 0.0
    assert k["tardiness_work_h_total"] == pytest.approx(o.completed_time - 1.0)
    assert k["tardiness_work_h_mean"] == pytest.approx(o.completed_time - 1.0)


def test_tardiness_is_zero_when_on_time(run_logic):
    k = kpis(run_logic(SIMULATION_TIME=10_000))
    assert k["tardiness_work_h_total"] >= 0
    if k["on_time_all"] == 1.0:
        assert k["tardiness_work_h_total"] == 0.0


# ---------------------------------------------------------------- 프린터 대기열
def test_printer_wait_matches_batch_times(run_logic):
    res = run_logic()
    k = kpis(res)
    waits = [b.print_start - b.closed_time for b in res.batches]
    assert min(waits) >= 2 - 1e-9                                         # BUILD_PREPARATION 2h 포함
    assert k["printer_wait_h_mean"] == pytest.approx(sum(waits) / len(waits))
    assert k["printer_queue_mean"] >= 0 and k["printer_queue_max"] >= 1


# ---------------------------------------------------------------- 병목
def test_bottleneck_is_highest_load(run_logic):
    res = run_logic()
    name, value = bottleneck(res)
    k, u = kpis(res), utilization(res)
    loads = {n: v["utilization"] for n, v in u.items() if n != "vpp_printers"}
    loads["vpp_printers"] = k["printer_rho"]
    assert name in res.capacities
    assert value == pytest.approx(max(loads.values()))


# ---------------------------------------------------------------- 일별 표
def test_daily_table_sums_to_completed_orders(run_logic, tmp_path):
    res = run_logic()
    rows = daily_table(res)
    assert len(rows) == math.ceil(res.end_time / 24)
    assert sum(r["throughput"] for r in rows) == sum(o.completed_time is not None for o in res.orders)
    assert all(0 <= r["printer_util"] <= 1 for r in rows)
    save_outputs(res, str(tmp_path), daily=True)
    with open(tmp_path / "daily_summary.csv", encoding="utf-8-sig") as f:
        assert len(list(csv.DictReader(f))) == len(rows)


def test_daily_summary_not_written_by_default(run_logic, tmp_path):
    save_outputs(run_logic(), str(tmp_path))
    assert not (tmp_path / "daily_summary.csv").exists()


# ---------------------------------------------------------------- 이벤트 로그
def _events(res):
    return [(t, eid, ev) for t, _, eid, ev, _, _ in res.log.events]


def test_every_queue_enter_is_followed_by_start(run_logic):
    res = run_logic(CLEANING_LIQUID_CHANGE_EVERY_LOADS=2, CLEANING_LIQUID_CHANGE_TIME=("const", 0.25, "h"))
    ev = _events(res)
    queued = [(t, eid, e) for t, eid, e in ev if e.endswith("_QUEUE_ENTER")]
    assert {e for _, _, e in queued} == {"PRINTER_QUEUE_ENTER", "WASHING_QUEUE_ENTER", "UV_CURING_QUEUE_ENTER"}
    start_of = {"PRINTER_QUEUE_ENTER": "VPP_BUILD_START"}
    for t, eid, e in queued:
        want = start_of.get(e, e.replace("_QUEUE_ENTER", "_START"))
        assert any(x == eid and s == want and u >= t for u, x, s in ev), (eid, e)


@pytest.mark.parametrize("kind", ["MAINTENANCE", "CLEANING"])
def test_machine_start_end_events_are_paired(logic_cfg, kind):
    fail = {"printer": {"mtbf": 3.0, "repair": ("const", 2, "h"), "pm_every": 20.0, "pm": ("const", 1, "h"),
                        "pm_offsets": [0.0, 10.0]},
            "washing": {"mtbf": 2.0, "repair": ("const", 1, "h"), "pm_every": 15.0, "pm": ("const", 0.5, "h"),
                        "pm_offsets": [0.0]},
            "uv_curing": {"mtbf": 2.0, "repair": ("const", 1, "h"), "pm_every": 15.0, "pm": ("const", 0.5, "h"),
                          "pm_offsets": [0.0]}}
    res = VPPSimulation(logic_cfg.replace(BREAKDOWN_ENABLED=True, EQUIPMENT_FAILURE=fail,
                                          CLEANING_LIQUID_CHANGE_EVERY_LOADS=2,
                                          CLEANING_LIQUID_CHANGE_TIME=("const", 0.25, "h"))).run()
    open_ = {}
    n = 0
    for t, eid, e in _events(res):
        if e == f"{kind}_START":
            assert not open_.get(eid), (eid, t)                          # 끝나기 전에 다시 시작하지 않음
            open_[eid] = True
            n += 1
        elif e == f"{kind}_END":
            assert open_.get(eid), (eid, t)                              # START 없이 END 없음
            open_[eid] = False
    assert n > 0
    assert not any(open_.values())


@pytest.mark.parametrize("packers", [0, 1])
def test_human_starts_are_in_work_hours(run_logic, packers):
    res = run_logic(USE_WORK_CALENDAR=True, PRINTER_UNATTENDED=True, PACKER_COUNT=packers)
    starts = [(e[0], e[3]) for e in res.log.events if e[3].endswith("_START") and e[4] in HUMANS]
    assert starts
    off = [(t, ev) for t, ev in starts if not res.calendar.is_open(t)]
    assert not off, off[:5]


# ---------------------------------------------------------------- main.py 옵션 조합
@pytest.mark.parametrize("argv", [["--orders", "a.csv", "--mode", "random"], ["--jobs", "4"],
                                  ["--mode", "csv", "--weeks", "2"]])
def test_main_rejects_bad_option_combinations(argv):
    r = subprocess.run([sys.executable, "main.py", *argv], capture_output=True)
    assert r.returncode == 2, r.stderr.decode(errors="replace")
