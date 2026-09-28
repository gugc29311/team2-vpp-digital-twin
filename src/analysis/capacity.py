# -*- coding: utf-8 -*-
"""
프린터 유효 처리용량 측정 [1번 정의 B 의 분모].

포화 부하(주문이 처리능력보다 훨씬 많이 들어오는 상태)에서 프린터까지만 시뮬레이션해
'주당 최대 출력시간'을 잰다. 무인운전·고장/PM·근무시간 투입 제약이 모두 반영된 값.
설정(프린터 대수, 캘린더, 고장 파라미터 등)을 바꾸면 다시 재서
parameters.py 의 PRINTER_EFFECTIVE_CAPACITY_H_PER_WEEK 를 갱신해야 부하율 ρ 가 맞게 나온다.

실행:  python -m src.analysis.capacity
"""
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation


def measure_printer_capacity(cfg=None, weeks=40, saturation_rate=2000.0, seed=None):
    """
    포화 시 주당 최대 출력시간 [h/주].
    saturation_rate 는 프린터 처리능력(현재 약 820건/주)보다 충분히 크면 됨 — 너무 크면 대기 배치가 쌓여 느려짐.
    JA·Build Preparation·후공정은 건너뜀 (프린터만의 용량).
    """
    cfg = cfg or SimConfig.from_parameters()
    week = 168 if cfg.USE_WORK_CALENDAR else 40
    c = cfg.replace(ORDER_SOURCE="random", ORDER_ARRIVAL_RATE_PER_WEEK=saturation_rate,
                    SIMULATION_TIME=cfg.WARMUP_TIME + weeks * week,
                    RANDOM_SEED=cfg.RANDOM_SEED if seed is None else seed)
    res = VPPSimulation(c, keep_events=False, printer_only=True).run()
    t0, t1 = c.WARMUP_TIME, res.end_time
    busy = sum(max(0.0, min(e, t1) - max(s, t0)) for r, s, e, _ in res.log.busy if r == "vpp_printers")
    return busy / weeks


if __name__ == "__main__":
    cfg = SimConfig.from_parameters()
    vals = [measure_printer_capacity(cfg, seed=s) for s in (42, 7, 123)]
    print(f"포화 시 주당 최대 출력시간 (seed 42/7/123): {', '.join(f'{v:.2f}' for v in vals)} h/주")
    print(f"평균 {sum(vals) / len(vals):.2f} h/주  (현재 parameters.py: {cfg.PRINTER_EFFECTIVE_CAPACITY_H_PER_WEEK})")
