# -*- coding: utf-8 -*-
"""
명세서 14절 Level 4 (Simple Test Case) · Level 2 (Event Trace Verification) — 손계산과 SimPy 결과 비교.
고정 설정 LOGIC_CFG 기반 (24시간 시계, 고장·불량 없음).
"""
import pytest

from src.analysis.kpi import kpis
from src.entities.order import Order
from src.logger.state import order_trace
from src.model.simulation import VPPSimulation

ZERO = ("const", 0, "h")


def _orders(n, t=0.0):
    return [Order(f"O{i + 1}", "P1", t, 1, "Resin_A", 999.0, "NORMAL", 900.0, 40.0) for i in range(n)]


def _level4_cfg(logic_cfg, printers):
    """명세서 예시: 프린터 printers 대, 출력 각 5h, Failure 없음, 이동시간 없음, 그 밖의 처리시간 0, 주문 1건 = 배치 1개."""
    return logic_cfg.replace(
        VPP_PRINTER_COUNT=printers, BUILD_PLATE_MAX_PARTS=1, VPP_BUILD_TIME=("const", 5, "h"),
        TRANSPORT_ENABLED=False, JOB_ASSIGNMENT_TIME=ZERO, BUILD_PREPARATION_TIME=ZERO, PART_REMOVAL_TIME=ZERO,
        WASHING_TIME=ZERO, UV_CURING_TIME=ZERO, SUPPORT_REMOVAL_TIME=ZERO, SURFACE_TREATMENT_TIME=ZERO,
        INSPECTION_TIME=ZERO, PACKAGING_TIME=ZERO)


# ---------------------------------------------------------------- Level 4
def test_level4_one_printer_two_orders(logic_cfg):
    """프린터 1대, 주문 2개(t=0), 각 5h -> 1번째 [0,5], 2번째 [5,10] -> 완료 5h, 10h (FCFS)."""
    res = VPPSimulation(_level4_cfg(logic_cfg, 1), orders=_orders(2)).run()
    done = {o.order_id: o.completed_time for o in res.orders}
    assert done == pytest.approx({"O1": 5.0, "O2": 10.0})
    assert [(b.print_start, b.print_end) for b in res.batches] == [(0.0, 5.0), (5.0, 10.0)]
    k = kpis(res)
    assert k["util_vpp_printers"] == pytest.approx(1.0)                    # 0~10h 내내 출력
    assert k["lead_calendar_h_mean"] == pytest.approx(7.5)                 # (5 + 10) / 2


def test_level4_two_printers_run_in_parallel(logic_cfg):
    res = VPPSimulation(_level4_cfg(logic_cfg, 2), orders=_orders(2)).run()
    assert [o.completed_time for o in res.orders] == pytest.approx([5.0, 5.0])


def test_level4_worker_queue_hand_calculation(logic_cfg):
    """JA 1명 x 1h, 주문 3건 동시 도착 -> JA 대기 0, 1, 2h (평균 1h), 대기 인원 [0,1] 2명·[1,2] 1명 (면적 3)."""
    res = VPPSimulation(logic_cfg.replace(BUILD_PREPARATION_TIME=ZERO), orders=_orders(3)).run()
    k = kpis(res)
    assert k["job_assignment_workers_wait_h_mean"] == pytest.approx(1.0)
    assert k["job_assignment_workers_wait_h_p95"] == pytest.approx(1.9)
    assert k["job_assignment_workers_queue_max"] == 2
    assert k["job_assignment_workers_queue_mean"] == pytest.approx(3.0 / res.end_time)
    details = sorted(e.detail for e in res.log.events if e.event == "JOB_ASSIGNMENT_START")
    assert details == ["", "wait=1.00h", "wait=2.00h"]                     # 이벤트 로그의 wait 와 같은 정의


def test_worker_wait_kpis_without_event_log(logic_cfg):
    cfg = logic_cfg.replace(BUILD_PREPARATION_TIME=ZERO)
    a = kpis(VPPSimulation(cfg, orders=_orders(3)).run())
    b = kpis(VPPSimulation(cfg, orders=_orders(3), keep_events=False).run())
    for role in ("job_assignment_workers", "post_process_workers", "quality_inspectors"):
        assert b[f"{role}_wait_h_mean"] == pytest.approx(a[f"{role}_wait_h_mean"], nan_ok=True)


# ---------------------------------------------------------------- Level 2
HAND_TRACE = [   # (이벤트, 시각 h) — 경쟁 없는 주문 1건, LOGIC_CFG 처리시간으로 손계산
    ("ORDER_RECEIVED", 0), ("JOB_ASSIGNMENT_START", 0), ("JOB_ASSIGNMENT_END", 1),          # JA 1h
    ("BATCH_OPENED", 1), ("ADDED_TO_BATCH", 1), ("BATCH_CLOSED", 25),                       # 부품 1개 -> T 조건 24h
    ("BUILD_PREPARATION_START", 25), ("BUILD_PREPARATION_END", 27),                         # 준비 2h
    ("PRINTER_QUEUE_ENTER", 27), ("VPP_BUILD_START", 27), ("VPP_BUILD_END", 32),            # 출력 5h
    ("TRANSPORT_1_PRINT_TO_REMOVAL_START", 32), ("TRANSPORT_1_PRINT_TO_REMOVAL_END", 32.5),
    ("PART_REMOVAL_START", 32.5), ("PART_REMOVAL_END", 33.5),                               # 탈거 1h
    ("TRANSPORT_2_TO_WASHING_START", 33.5), ("TRANSPORT_2_TO_WASHING_END", 34),
    ("WASHING_QUEUE_ENTER", 34), ("WASHING_START", 34), ("WASHING_END", 36),                # 세척 2h
    ("TRANSPORT_3_TO_UV_START", 36), ("TRANSPORT_3_TO_UV_END", 36.5),
    ("UV_CURING_QUEUE_ENTER", 36.5), ("UV_CURING_START", 36.5), ("UV_CURING_END", 39.5),    # UV 3h
    ("TRANSPORT_4_TO_SUPPORT_START", 39.5), ("TRANSPORT_4_TO_SUPPORT_END", 40),
    ("SUPPORT_REMOVAL_START", 40), ("SUPPORT_REMOVAL_END", 42),                             # 서포트 2h
    ("SURFACE_TREATMENT_START", 42), ("SURFACE_TREATMENT_END", 44),                         # 표면 2h
    ("TRANSPORT_5_TO_INSPECTION_START", 44), ("TRANSPORT_5_TO_INSPECTION_END", 44.5),
    ("INSPECTION_START", 44.5), ("INSPECTION_END", 45.5),                                   # 검사 1h
    ("PACKAGING_START", 45.5), ("PACKAGING_END", 46.5),                                     # 포장 1h
    ("PART_COMPLETED", 46.5), ("ORDER_COMPLETED", 46.5),
]


def test_level2_single_order_trace_matches_hand_calculation(logic_cfg):
    res = VPPSimulation(logic_cfg, orders=_orders(1)).run()
    trace = order_trace(res, "O1")
    assert [r["event"] for r in trace] == [ev for ev, _ in HAND_TRACE]
    assert [r["t"] for r in trace] == pytest.approx([t for _, t in HAND_TRACE])
    assert trace[0]["time"] == "Day 1 09:00 (월)" and trace[-1]["time"] == "Day 3 07:30 (수)"   # 09:00 + 46.5h


def test_trace_includes_batch_and_load_events_of_that_order(run_logic):
    """샘플 O001: 배치 B00001·세척 로드 W1·UV 로드 U1 이벤트까지 포함, 다른 주문만의 로드(W2 = O003)는 제외."""
    entities = {r["entity"] for r in order_trace(run_logic(), "O001")}
    assert {"ORDER O001", "PART O001-1", "BATCH B00001", "LOAD B00001-W1", "LOAD B00001-U1"} <= entities
    assert "LOAD B00001-W2" not in entities


def test_trace_unknown_order(run_logic):
    with pytest.raises(ValueError, match="O999"):
        order_trace(run_logic(), "O999")
