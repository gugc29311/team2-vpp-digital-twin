# -*- coding: utf-8 -*-
"""
Level 5 극한 조건 테스트: 입력을 극단값으로 놓았을 때 결과가 상식적으로 예측되는 값·방향인지.
(고정 설정 LOGIC_CFG — 24시간 시계, 고정 처리시간, 한 배치 최대 4부품, 배치 대기 24h)

  0 수요     : 주문 없음 -> 오류 없이 종료, 배치·점유 0
  경쟁 없음   : 주문 1건 -> 리드타임 = 처리시간 합 (손계산 47h, 이동 ①~⑥ 포함)
  자원 무한   : 사람·설비 대기 0
  과부하     : JA 부하 150% -> 시스템 내 주문이 계속 쌓임, 완료율 < 1
  불량 최대   : 불량 99% + 재출력 없음 -> 거의 전부 SHORT / 재출력 있음 -> 결국 전부 완료
  프린터 0   : 허용 -> 완료 0, WIP 누적, KPI 오류 없음 (명세서 14절 Level 5)
  자원 0     : 프린터 외 자원은 설정 단계에서 거부
"""
import math
import re

import pytest

from src.analysis.kpi import kpis, print_summary, utilization
from src.entities.order import Order
from src.model.simulation import VPPSimulation
from src.validation.invariants import check


def _order(oid="O1", t=0.0):
    return Order(oid, "P1", t, 1, "Resin_A", 999.0, "NORMAL", 900.0, 40.0)


def test_zero_demand(logic_cfg, capsys):
    res = VPPSimulation(logic_cfg, orders=[]).run()
    assert res.batches == [] and res.log.busy == []
    k = kpis(res)                                                   # 0 으로 나누기 없이 NaN
    assert math.isnan(k["throughput_per_week"]) and math.isnan(k["wip_mean"])
    assert all(v["utilization"] == 0 for v in utilization(res).values())
    print_summary(res)
    assert check(res) == []


def test_zero_demand_random_mode(logic_cfg):
    res = VPPSimulation(logic_cfg.replace(ORDER_SOURCE="random", ORDER_ARRIVAL_RATE_PER_WEEK=1e-9,
                                          SIMULATION_TIME=200)).run()
    assert res.orders == [] and res.end_time == pytest.approx(200)
    k = kpis(res)
    assert k["throughput_per_week"] == 0 and k["wip_mean"] == 0


def test_single_order_lead_time_is_sum_of_process_times(logic_cfg):
    """
    JA 1 -> 배치 대기 24 (부품 1개라 시간 조건) -> 준비 2 -> 출력 5 -> ① 0.5 + 제거 1
    -> ② 0.5 + 세척 2 -> ③ 0.5 + UV 3 -> ④ 0.5 -> 서포트 2 -> 표면 2 -> ⑤ 0.5 -> 검사 1 -> ⑥ 0.5 -> 포장 1 = 47h
    """
    res = VPPSimulation(logic_cfg, orders=[_order()]).run()
    assert res.orders[0].completed_time == pytest.approx(47)


def _resource_waits(res):
    waits = []
    for e in res.log.events:
        m = re.search(r"wait=([\d.]+)h", e.detail)
        if e.event.endswith("_START") and m and float(m.group(1)) > 1e-9:
            waits.append((e.entity_id, e.event, float(m.group(1))))
    return waits


def test_ample_resources_mean_no_waiting(run_logic):
    big = dict(JOB_ASSIGNMENT_WORKER_COUNT=50, POST_PROCESS_WORKER_COUNT=50, QUALITY_INSPECTOR_COUNT=50,
               VPP_PRINTER_COUNT=20, WASHING_MACHINE_COUNT=20, UV_CURING_MACHINE_COUNT=20)
    assert _resource_waits(run_logic(**big)) == []
    assert _resource_waits(run_logic()) != []                     # 기본 인원에서는 대기가 생김 (대조군)


def test_overload_accumulates_wip(logic_cfg):
    """λ = 60건/주, 캘린더 끔 -> 주 = 40h 기준이라 1.5건/h. JA 1명 x 1h/건 -> 부하 150%."""
    T = 400
    res = VPPSimulation(logic_cfg.replace(ORDER_SOURCE="random", ORDER_ARRIVAL_RATE_PER_WEEK=60,
                                          SIMULATION_TIME=T, RANDOM_SEED=1), keep_events=False).run()
    in_system = lambda t: sum(o.arrival_time <= t and (o.completed_time is None or o.completed_time > t)
                              for o in res.orders)
    assert in_system(T) > in_system(T / 2) + 50                   # 시간에 비례해 계속 쌓임
    k = kpis(res)
    assert k["completion_ratio"] < 0.8
    assert utilization(res)["job_assignment_workers"]["utilization"] > 0.99
    assert check(res) == []


def test_max_defect_without_rework_scraps_almost_everything(run_logic):
    res = run_logic(INSPECTION_FAILURE_RATE=0.99, REWORK_ENABLED=False, RANDOM_SEED=1)
    assert all(o.is_done for o in res.orders)
    assert sum(o.parts_good for o in res.orders) <= 2
    assert sum(o.status == "SHORT" for o in res.orders) >= 8
    assert check(res) == []


def test_high_defect_with_rework_eventually_completes(run_logic):
    res = run_logic(INSPECTION_FAILURE_RATE=0.5, REWORK_ENABLED=True, RANDOM_SEED=2)
    assert all(o.status == "COMPLETED" for o in res.orders)
    assert sum(o.reworks for o in res.orders) >= 5
    assert check(res) == []


@pytest.mark.parametrize("key", ["POST_PROCESS_WORKER_COUNT", "WASHING_LOAD_CAPACITY"])
def test_zero_capacity_is_rejected(logic_cfg, key):
    with pytest.raises(ValueError, match=key):
        logic_cfg.replace(**{key: 0})


def test_negative_printer_count_is_rejected(logic_cfg):
    with pytest.raises(ValueError, match="VPP_PRINTER_COUNT"):
        logic_cfg.replace(VPP_PRINTER_COUNT=-1)


def test_zero_printers_means_zero_output(logic_cfg):
    """명세서 14절 Level 5: Printer = 0 -> 생산량 0. 배치는 프린터 대기열에서 계속 기다리고 WIP 는 쌓이기만 함."""
    T = 400
    res = VPPSimulation(logic_cfg.replace(VPP_PRINTER_COUNT=0, ORDER_SOURCE="random", SIMULATION_TIME=T,
                                          ORDER_ARRIVAL_RATE_PER_WEEK=10, RANDOM_SEED=1)).run()
    assert res.capacities["vpp_printers"] == 0
    assert res.orders and all(o.completed_time is None for o in res.orders)
    assert all(b.print_start is None for b in res.batches)
    in_system = lambda t: sum(o.arrival_time <= t for o in res.orders)       # 완료가 없으므로 도착 누적 = WIP
    assert in_system(T) > in_system(T / 2) > 0
    k = kpis(res)                                                             # 오류 없이 계산
    assert k["throughput_per_week"] == 0
    assert k["printer_rho"] == 0 and k["printer_busy_h_per_week"] == 0
    assert "util_vpp_printers" not in k                                       # 용량 0 자원은 가동률 없음 (packers 0 과 같은 규칙)
    assert k["completion_ratio"] == 0
    assert k["wip_max"] == len(res.orders)
    assert k["printer_queue_max"] == sum(b.closed for b in res.batches)
    print_summary(res)
    assert check(res) == []


def test_zero_printers_csv_mode_ends_at_time_limit(logic_cfg):
    """csv 모드는 '전부 완료' 로 끝나는데 완료가 없으므로 SIMULATION_TIME 에서 종료."""
    res = VPPSimulation(logic_cfg.replace(VPP_PRINTER_COUNT=0, SIMULATION_TIME=500)).run()
    assert res.end_time == pytest.approx(500)
    assert sum(o.completed_time is not None for o in res.orders) == 0
    assert all(math.isnan(v) or v >= 0 for v in kpis(res).values())


@pytest.mark.parametrize("key", ["PRINT_FAILURE_RATE", "INSPECTION_FAILURE_RATE"])
def test_defect_rate_of_one_is_rejected(logic_cfg, key):
    """불량률 100% + 재출력이면 끝나지 않으므로 설정 단계에서 막음."""
    with pytest.raises(ValueError, match=key):
        logic_cfg.replace(**{key: 1.0})
