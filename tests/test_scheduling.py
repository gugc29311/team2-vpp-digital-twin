# -*- coding: utf-8 -*-
"""
프린터 대기열 규칙(FCFS/SPT/EDD/URGENT_FIRST) 순서 테스트 — src/scheduler/dispatch.py.

프린터 1대. BLK 가 먼저 출력하는 동안 O1·O2·O3 배치가 대기열에 쌓이고,
BLK 이후의 출력 순서가 규칙대로인지 손계산 정답과 비교한다.

  주문  도착   높이(→출력시간)  납기            우선순위
  O1    0.5    40mm (가장 김)   300             NORMAL
  O2    0.6    10mm (가장 짧음) 200             URGENT
  O3    0.7    20mm             100 (가장 이름) NORMAL
"""
import pytest

from src.entities.order import Order
from src.model.simulation import VPPSimulation
from src.scheduler.dispatch import RULES, priority

ORDERS = [("BLK", 0.0, 10.0, 999.0, "NORMAL"), ("O1", 0.5, 40.0, 300.0, "NORMAL"),
          ("O2", 0.6, 10.0, 200.0, "URGENT"), ("O3", 0.7, 20.0, 100.0, "NORMAL")]

EXPECTED = {
    "FCFS": ["O1", "O2", "O3"],             # 요청 순서
    "SPT": ["O2", "O3", "O1"],              # 출력시간 짧은 순
    "EDD": ["O3", "O2", "O1"],              # 납기 빠른 순
    "URGENT_FIRST": ["O2", "O1", "O3"],     # 긴급 먼저, 나머지는 요청 순서
}


def _print_order(logic_cfg, rule):
    cfg = logic_cfg.replace(
        DEFAULT_SCHEDULING_RULE=rule, VPP_PRINTER_COUNT=1, BUILD_PLATE_MAX_PARTS=1,
        JOB_ASSIGNMENT_WORKER_COUNT=5, BUILD_PREPARATION_TIME=("const", 0, "h"),
        VPP_BUILD_TIME_MODE="height", BUILD_SETUP_TIME=("const", 1, "h"),
        LAYER_THICKNESS_MM=0.05, TIME_PER_LAYER=("const", 8, "s"))
    orders = [Order(oid, "P1", t, 1, "Resin_A", due, pr, 900.0, h) for oid, t, h, due, pr in ORDERS]
    res = VPPSimulation(cfg, orders=orders).run()
    batches = sorted(res.batches, key=lambda b: b.print_start)
    return [b.parts[0].order.order_id for b in batches], res


@pytest.mark.parametrize("rule", list(EXPECTED))
def test_printer_queue_order_follows_rule(logic_cfg, rule):
    seq, res = _print_order(logic_cfg, rule)
    assert seq[0] == "BLK"
    assert seq[1:] == EXPECTED[rule]
    assert all(o.status == "COMPLETED" for o in res.orders)


def test_queue_is_actually_contended(logic_cfg):
    """전제 확인: O1~O3 는 BLK 출력 중에 대기열에 들어옴 (아니면 순서 테스트가 무의미)."""
    _, res = _print_order(logic_cfg, "FCFS")
    blk = next(b for b in res.batches if b.parts[0].order.order_id == "BLK")
    others = [b for b in res.batches if b is not blk]
    assert all(blk.print_start < b.closed_time < blk.print_end for b in others)


def test_every_configurable_rule_has_expected_order():
    """RULES 에 규칙을 추가하면 이 파일의 EXPECTED 에도 정답 순서를 추가해야 함."""
    assert set(RULES) == set(EXPECTED)


def test_priority_values():
    class B:
        build_time, earliest_due, has_urgent = 3.5, 42.0, False
    assert priority("FCFS", B, 7) == 7
    assert priority("SPT", B, 7) == 3.5
    assert priority("EDD", B, 7) == 42.0
    assert priority("URGENT_FIRST", B, 7) == 7

    class U(B):
        has_urgent = True
    assert priority("URGENT_FIRST", U, 10 ** 6) < priority("URGENT_FIRST", B, 1)   # 늦게 온 긴급 < 먼저 온 일반


def test_unknown_rule_is_rejected(logic_cfg):
    with pytest.raises(ValueError, match="DEFAULT_SCHEDULING_RULE"):
        logic_cfg.replace(DEFAULT_SCHEDULING_RULE="LIFO")
