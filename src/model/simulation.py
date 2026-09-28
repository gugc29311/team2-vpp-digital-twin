# -*- coding: utf-8 -*-
"""
VPP 공정 시뮬레이션 (SimPy).

흐름
  주문 도착 -> Job Assignment(JA 담당) -> 부품 단위로 배치 형성(면적 S / 대기 T, 같은 재료)
  -> [배치] (Build Preparation) -> 프린터 대기열(FCFS/SPT/EDD) -> 프린터 풀에서 사용 가능한 프린터 확보
     (고장·PM 도래 시 그 프린터는 정비로 보내고 다른 프린터 대기) -> VPP Build (무인운전: 밤에도 진행)
  -> ① 이동 + Part Removal(후공정 담당, 한 번의 점유)
  -> 세척: 로드 분할, 로드마다 ② 이동 -> 세척기 확보(정비 점검) -> 적재 -> 세척 -> 인출 (-> 세척액 교체)
  -> UV:   로드 분할, 로드마다 ③ 이동 -> UV 확보(정비 점검) -> 적재 -> 경화 -> 인출
  -> ④ 이동(UV 로드 수만큼) -> Support Removal(부품별) -> Surface Treatment(부품별)
  -> ⑤ 이동(검사원) -> Inspection(부품별) -> 합격: Packaging(검사원 겸직 / 포장 담당) / 불합격: 재출력 또는 폐기

근무 캘린더 사용 시: 사람 작업·세척·UV·정비는 근무시간만 소비, 프린터 투입(적재)은 근무시간에만,
출력 자체는 PRINTER_UNATTENDED 이면 달력시간으로 연속 진행.

실행 (프로젝트 최상위 폴더):  python main.py   |   python -m src.model.simulation
"""
import math
from dataclasses import dataclass

import simpy

from src.analysis.event_log import EventLog
from src.entities.batch import Batch
from src.entities.order import make_parts
from src.model.config import SimConfig
from src.model.order_source import check_orders_for_config, load_orders, random_order
from src.resources.resources import FactoryResources
from src.utils.calendar import make_calendar
from src.utils.random_utils import make_streams, sample


@dataclass
class SimulationResult:
    cfg: SimConfig
    orders: list
    batches: list
    log: EventLog
    end_time: float
    calendar: object
    capacities: dict
    units: list            # 설비 객체 (고장·PM·세척액 교체 기록)
    resin_log: list        # (출력 시작 시각, 레진 mm³, 재출력 여부)
    printed_parts: list    # (출력 시작 시각, 재출력 여부)


class VPPSimulation:
    def __init__(self, cfg: SimConfig, orders=None, verbose=False, keep_events=True, printer_only=False):
        """
        verbose      : 공정 추적을 화면에 출력
        keep_events  : 이벤트 로그 보관 (반복실험에서는 False 로 메모리 절약 — KPI 는 그대로 계산됨)
        printer_only : 프린터까지만 시뮬레이션 (포화 처리용량 측정용, 후공정 생략)
        """
        self.cfg = cfg
        self.env = simpy.Environment()
        self.rng = make_streams(cfg.RANDOM_SEED)
        self.cal = make_calendar(cfg)
        self.res = FactoryResources(self.env, cfg, self.rng["failure"])
        self.log = EventLog(verbose, keep_events)
        self.printer_only = printer_only
        self.orders = list(orders) if orders is not None else None
        self.batches = []
        self.open_batches = {}            # 재료(또는 "ALL") -> 형성 중 배치
        self._batch_seq = 0
        self._request_seq = 0             # FCFS 우선순위 = 프린터 요청 순번
        self._n_done = 0
        self.resin_log = []
        self.printed_parts = []
        self.all_done = self.env.event()

    # =====================================================
    # 실행
    # =====================================================
    def run(self) -> SimulationResult:
        cfg, env = self.cfg, self.env
        if cfg.ORDER_SOURCE == "csv":
            if self.orders is None:
                self.orders = load_orders(cfg.ORDER_CSV_PATH)
            check_orders_for_config(self.orders, cfg)
            if not self.orders:
                self.all_done.succeed()
            env.process(self._csv_source())
            until = simpy.AnyOf(env, [self.all_done, env.timeout(cfg.SIMULATION_TIME)])
        else:
            self.orders = []
            env.process(self._random_source())
            until = cfg.SIMULATION_TIME
        env.run(until=until)
        caps = {name: self.res.capacity_of(name) for name, _ in FactoryResources.TRACKED}
        return SimulationResult(cfg, self.orders, self.batches, self.log, env.now, self.cal, caps,
                                self.res.all_units, self.resin_log, self.printed_parts)

    # =====================================================
    # 공통 도구
    # =====================================================
    def _t(self, spec, stream="process"):
        """분포에서 시간(hour) 추출."""
        return sample(spec, self.rng[stream])

    def _work(self, res_name, tasks, entity_type, entity_id):
        """
        인력 1명(또는 자원 1개)을 잡고 tasks=[(작업명, 근무시간 h), ...] 를 순서대로 수행 (한 번의 점유).
        대기·시작·종료를 로그에 남기고, 가동률 계산용 점유 구간을 작업별로 기록.
        """
        resource = getattr(self.res, res_name)
        t_req = self.env.now
        with resource.request() as req:
            yield req
            wait = self.env.now - t_req
            for i, (task, hours) in enumerate(tasks):
                start = self.env.now
                self.log.add(start, entity_type, entity_id, f"{task}_START", res_name,
                             f"wait={wait:.2f}h" if i == 0 and wait > 1e-9 else "")
                yield from self.cal.delay(self.env, hours)
                self.log.add(self.env.now, entity_type, entity_id, f"{task}_END", res_name)
                self.log.add_busy(res_name, start, self.env.now, hours)

    def _transport_time(self):
        return self._t(self.cfg.DEFAULT_TRANSPORT_TIME, "transport") if self.cfg.TRANSPORT_ENABLED else 0.0

    def _transport(self, res_name, entity_type, entity_id, segment):
        if self.cfg.TRANSPORT_ENABLED:
            yield from self._work(res_name, [(f"TRANSPORT_{segment}", self._transport_time())],
                                  entity_type, entity_id)

    def _get_unit(self, pool_name):
        """
        설비 풀에서 지금 쓸 수 있는 설비 객체를 꺼낸다.
          - 캘린더 사용 시 적재는 근무시간에만 (밤에 반납된 설비라도 근무 시작까지 대기)
          - 꺼낸 설비가 고장·PM 도래면 그 설비는 정비 프로세스로 보내고 다른 설비를 다시 기다림 (비선점)
        """
        pool = self.res.pools[pool_name]
        while True:
            unit = yield pool.get()
            if self.cfg.USE_WORK_CALENDAR:
                yield from self.cal.wait_open(self.env)
            if self.cfg.BREAKDOWN_ENABLED and unit.due(self.env.now):
                self.env.process(self._service_and_return(unit, pool))
                continue
            return unit

    def _service_and_return(self, unit, pool):
        yield from unit.service(self.env, self.cal, self.cfg.MAINTENANCE_IN_WORK_HOURS_ONLY)
        yield pool.put(unit)

    def _clean_and_return(self, unit, pool):
        yield from unit.clean(self.env, self.cal, self._t(self.cfg.CLEANING_LIQUID_CHANGE_TIME))
        yield pool.put(unit)

    # =====================================================
    # 주문 입력
    # =====================================================
    def _csv_source(self):
        for o in sorted(self.orders, key=lambda x: x.arrival_time):
            if o.arrival_time > self.env.now:
                yield self.env.timeout(o.arrival_time - self.env.now)
            self.env.process(self._order_flow(o))

    def _random_source(self):
        """도착간격 Exp(평균 = 주당 근무시간 / λ), 근무시간 기준으로 소비 (근무시간에만 도착)."""
        mean_iat = self.cal.hours_per_week / self.cfg.arrival_rate_per_week
        rng, idx = self.rng["orders"], 0
        while True:
            yield from self.cal.delay(self.env, rng.exponential(mean_iat))
            idx += 1
            o = random_order(idx, self.env.now, self.cfg, rng, self.cal)
            self.orders.append(o)
            self.env.process(self._order_flow(o))

    def _order_flow(self, o):
        o.received_time = self.env.now
        self.log.add(self.env.now, "ORDER", o.order_id, "ORDER_RECEIVED", "",
                     f"qty={o.quantity} material={o.material} due={o.due_date:.2f} priority={o.priority}")
        if not self.printer_only:     # 프린터 용량 측정 시에는 JA 를 건너뜀 (JA 가 병목이 되어 용량을 과소 측정하지 않도록)
            yield from self._work("job_assignment_workers",
                                  [("JOB_ASSIGNMENT", self._t(self.cfg.JOB_ASSIGNMENT_TIME))], "ORDER", o.order_id)
        for p in make_parts(o):
            self._add_to_batch(p)

    # =====================================================
    # 배치 형성 (도착 순서대로 채움)
    # =====================================================
    def _batch_key(self, part):
        return part.material if self.cfg.BATCH_SAME_MATERIAL_ONLY else "ALL"

    def _overflows(self, b, part):
        c = self.cfg
        if c.BUILD_PLATE_MAX_PARTS is not None and b.n_parts >= c.BUILD_PLATE_MAX_PARTS:
            return True
        if c.BUILD_PLATE_AREA_MM2 is not None and b.total_area + part.area_mm2 > c.BUILD_PLATE_AREA_MM2:
            return True
        return False

    def _full_trigger(self, b):
        c = self.cfg
        if c.BUILD_PLATE_MAX_PARTS is not None and b.n_parts >= c.BUILD_PLATE_MAX_PARTS:
            return "PARTS"
        if c.BATCH_FILL_RATIO is not None and b.total_area >= c.BATCH_FILL_RATIO * c.BUILD_PLATE_AREA_MM2:
            return "AREA"
        return None

    def _add_to_batch(self, part):
        key = self._batch_key(part)
        b = self.open_batches.get(key)
        if b is not None and b.n_parts and self._overflows(b, part):
            self._close_batch(b, "OVERFLOW")
            b = None
        if b is None:
            self._batch_seq += 1
            b = Batch(f"B{self._batch_seq:05d}", part.material if key != "ALL" else "MIXED", self.env.now)
            self.open_batches[key] = b
            self.batches.append(b)
            self.log.add(self.env.now, "BATCH", b.batch_id, "BATCH_OPENED", "", f"material={b.material}")
            if self.cfg.BATCH_MAX_WAIT_TIME is not None:
                self.env.process(self._batch_timer(b))
        b.parts.append(part)
        self.log.add(self.env.now, "PART", part.part_id, "ADDED_TO_BATCH", "", b.batch_id)
        trig = self._full_trigger(b)
        if trig:
            self._close_batch(b, trig)

    def _batch_timer(self, b):
        yield from self.cal.delay(self.env, self._t(self.cfg.BATCH_MAX_WAIT_TIME))
        if not b.closed:
            self._close_batch(b, "TIME")

    def _close_batch(self, b, trigger):
        b.closed_time, b.trigger = self.env.now, trigger
        for k, v in list(self.open_batches.items()):
            if v is b:
                del self.open_batches[k]
        self.log.add(self.env.now, "BATCH", b.batch_id, "BATCH_CLOSED", "",
                     f"trigger={trigger} parts={b.n_parts} area={b.total_area:.0f}")
        self.env.process(self._batch_flow(b))

    # =====================================================
    # 배치 공정
    # =====================================================
    def _build_time(self, b):
        c = self.cfg
        if c.VPP_BUILD_TIME_MODE == "fixed":
            return self._t(c.VPP_BUILD_TIME)
        layers = math.ceil(round(b.max_height / c.LAYER_THICKNESS_MM, 9))
        return self._t(c.BUILD_SETUP_TIME) + layers * self._t(c.TIME_PER_LAYER)

    def _priority(self, b):
        rule = self.cfg.DEFAULT_SCHEDULING_RULE
        if rule == "SPT":
            return b.build_time
        if rule == "EDD":
            return b.earliest_due
        return self._request_seq

    def _batch_flow(self, b):
        c, env = self.cfg, self.env
        prep = self._t(c.BUILD_PREPARATION_TIME)
        if prep > 0 and not self.printer_only:
            yield from self._work("job_assignment_workers", [("BUILD_PREPARATION", prep)], "BATCH", b.batch_id)

        # ---- VPP Build
        b.build_time = self._build_time(b)
        self._request_seq += 1
        t_req = env.now
        with self.res.vpp_printers.request(priority=self._priority(b)) as req:
            yield req
            unit = yield from self._get_unit("vpp_printers")
            b.print_start = env.now
            self.log.add(env.now, "BATCH", b.batch_id, "VPP_BUILD_START", unit.name,
                         f"build={b.build_time:.2f}h wait={env.now - t_req:.2f}h")
            self._record_print(b)
            if c.USE_WORK_CALENDAR and not c.PRINTER_UNATTENDED:
                yield from self.cal.delay(env, b.build_time)
            else:
                yield env.timeout(b.build_time)
            b.print_end = env.now
            unit.cum_op += b.build_time
            self.res.printer_pool.put(unit)
            self.log.add(env.now, "BATCH", b.batch_id, "VPP_BUILD_END", unit.name)
            self.log.add_busy("vpp_printers", b.print_start, b.print_end, b.build_time)
        if self.printer_only:
            return

        good = []
        for p in b.parts:
            if c.PRINT_FAILURE_RATE > 0 and self.rng["quality"].random() < c.PRINT_FAILURE_RATE:
                self._reject(p, "PRINT_FAILED")
            else:
                good.append(p)
        if not good:
            return

        # ---- ① 이동 + Part Removal (한 번의 점유)
        removal = self._t(c.PART_REMOVAL_TIME) + sum(self._t(c.PART_REMOVAL_TIME_PER_PART) for _ in good)
        tasks = ([("TRANSPORT_1_PRINT_TO_REMOVAL", self._transport_time())] if c.TRANSPORT_ENABLED else []) \
            + [("PART_REMOVAL", removal)]
        yield from self._work("post_process_workers", tasks, "BATCH", b.batch_id)

        # ---- 세척 / UV (로드 단위)
        yield from self._machine_stage(b, good, "washing_machines", c.WASHING_LOAD_CAPACITY,
                                       c.WASHING_TIME, "WASHING", "W", "2_TO_WASHING")
        yield from self._machine_stage(b, good, "uv_curing_machines", c.UV_CURING_LOAD_CAPACITY,
                                       c.UV_CURING_TIME, "UV_CURING", "U", "3_TO_UV")

        # ---- ④ 이동 + Support Removal + Surface Treatment
        for k in range(math.ceil(len(good) / c.UV_CURING_LOAD_CAPACITY)):
            yield from self._transport("post_process_workers", "LOAD", f"{b.batch_id}-U{k + 1}", "4_TO_SUPPORT")
        for p in good:
            yield from self._work("post_process_workers", [("SUPPORT_REMOVAL", self._t(c.SUPPORT_REMOVAL_TIME))],
                                  "PART", p.part_id)
        for p in good:
            yield from self._work("post_process_workers",
                                  [("SURFACE_TREATMENT", self._t(c.SURFACE_TREATMENT_TIME))], "PART", p.part_id)

        # ---- ⑤ 이동 + Inspection (+ Packaging)
        yield from self._transport("quality_inspectors", "BATCH", b.batch_id, "5_TO_INSPECTION")
        for p in good:
            yield from self._inspect(p)

    def _record_print(self, b):
        """출력 시작 시점 기록: 출력 부품(재출력 비중), 레진 소모 [공통3]."""
        c = self.cfg
        for p in b.parts:
            self.printed_parts.append((self.env.now, p.gen > 0))
            if c.RESIN_TRACKING and p.area_mm2 is not None and p.height_mm is not None:
                pure = p.area_mm2 * p.height_mm * sample(c.RESIN_FILL_RATIO, self.rng["resin"])
                p.resin_mm3 = pure * (1 + c.RESIN_SUPPORT_RATIO)
                self.resin_log.append((self.env.now, p.resin_mm3, p.gen > 0))

    def _machine_stage(self, b, parts, machine, load_cap, time_spec, task, tag, segment):
        loads = [parts[i:i + load_cap] for i in range(0, len(parts), load_cap)]
        procs = [self.env.process(self._machine_load(b, k, load, machine, time_spec, task, tag, segment))
                 for k, load in enumerate(loads)]
        yield self.env.all_of(procs)

    def _machine_load(self, b, k, load, machine, time_spec, task, tag, segment):
        """
        로드 1개: ②/③ 이동(작업자만) -> 설비 대기열 -> 설비 확보(정비 점검) -> 적재(작업자)
        -> 처리(근무시간) -> 인출(작업자) -> 설비 반납 (세척기는 N로드마다 세척액 교체 후 반납).
        설비 점유(가동률) = 적재~인출 [7번 정의].
        """
        c = self.cfg
        load_id = f"{b.batch_id}-{tag}{k + 1}"
        yield from self._transport("post_process_workers", "LOAD", load_id, segment)
        handling = self._t(c.LOAD_HANDLING_TIME)
        proc = self._t(time_spec)
        pool = self.res.pools[machine]
        with getattr(self.res, machine).request() as req:
            yield req
            unit = yield from self._get_unit(machine)
            start = self.env.now
            self.log.add(start, "LOAD", load_id, f"{task}_START", unit.name,
                         "parts=" + ",".join(p.part_id for p in load))
            if handling > 0:
                yield from self._work("post_process_workers", [("LOADING", handling / 2)], "LOAD", load_id)
            yield from self.cal.delay(self.env, proc)
            unit.cum_op += proc
            if handling > 0:
                yield from self._work("post_process_workers", [("UNLOADING", handling / 2)], "LOAD", load_id)
            self.log.add(self.env.now, "LOAD", load_id, f"{task}_END", unit.name)
            self.log.add_busy(machine, start, self.env.now, self.cal.work_hours(start, self.env.now))
            unit.loads += 1
            n = c.CLEANING_LIQUID_CHANGE_EVERY_LOADS
            if machine == "washing_machines" and n and unit.loads % n == 0:
                self.env.process(self._clean_and_return(unit, pool))
            else:
                pool.put(unit)

    def _inspect(self, p):
        c, env = self.cfg, self.env
        t_req = env.now
        with self.res.quality_inspectors.request() as req:
            yield req
            start = env.now
            self.log.add(start, "PART", p.part_id, "INSPECTION_START", "quality_inspectors",
                         f"wait={start - t_req:.2f}h" if start - t_req > 1e-9 else "")
            d = self._t(c.INSPECTION_TIME)
            yield from self.cal.delay(env, d)
            defect = c.INSPECTION_FAILURE_RATE > 0 and self.rng["quality"].random() < c.INSPECTION_FAILURE_RATE
            self.log.add(env.now, "PART", p.part_id, "INSPECTION_END", "quality_inspectors",
                         "FAIL" if defect else "PASS")
            self.log.add_busy("quality_inspectors", start, env.now, d)
            if not defect and self.res.packers is None:
                ps = env.now
                self.log.add(ps, "PART", p.part_id, "PACKAGING_START", "quality_inspectors")
                d = self._t(c.PACKAGING_TIME)
                yield from self.cal.delay(env, d)
                self.log.add(env.now, "PART", p.part_id, "PACKAGING_END", "quality_inspectors")
                self.log.add_busy("quality_inspectors", ps, env.now, d)
        if defect:
            self._reject(p, "INSPECTION_FAILED")
        elif self.res.packers is not None:
            self.env.process(self._pack(p))
        else:
            self._part_done(p)

    def _pack(self, p):
        yield from self._work("packers", [("PACKAGING", self._t(self.cfg.PACKAGING_TIME))], "PART", p.part_id)
        self._part_done(p)

    # =====================================================
    # 부품·주문 종료
    # =====================================================
    def _reject(self, p, reason):
        """
        실패/불량 부품: REWORK_ENABLED 면 재출력(Job Assignment 부터), 아니면 폐기.
        재출력 형상: REWORK_SAME_GEOMETRY True = 같은 부품, False = 분포에서 새로 추출(기존 검증 방식).
        """
        self.log.add(self.env.now, "PART", p.part_id, reason)
        if self.cfg.REWORK_ENABLED:
            p.order.reworks += 1
            if self.cfg.REWORK_SAME_GEOMETRY:
                area, height = p.area_mm2, p.height_mm
            else:
                area = sample(self.cfg.ORDER_AREA_MM2, self.rng["rework"])
                height = sample(self.cfg.ORDER_HEIGHT_MM, self.rng["rework"])
            self.env.process(self._rework(p.reprint(area, height)))
        else:
            p.order.parts_scrapped += 1
            self.log.add(self.env.now, "PART", p.part_id, "SCRAPPED")
            self._check_order(p.order)

    def _rework(self, q):
        yield from self._work("job_assignment_workers",
                              [("JOB_ASSIGNMENT_REWORK", self._t(self.cfg.JOB_ASSIGNMENT_TIME))], "PART", q.part_id)
        self._add_to_batch(q)

    def _part_done(self, p):
        p.order.parts_good += 1
        self.log.add(self.env.now, "PART", p.part_id, "PART_COMPLETED")
        self._check_order(p.order)

    def _check_order(self, o):
        if o.is_done and o.completed_time is None:
            o.completed_time = self.env.now
            self.log.add(self.env.now, "ORDER", o.order_id, "ORDER_COMPLETED", "", o.status)
            self._n_done += 1
            if self.cfg.ORDER_SOURCE == "csv" and self._n_done == len(self.orders) \
                    and not self.all_done.triggered:
                self.all_done.succeed()


def run_simulation(cfg=None, orders=None, verbose=True):
    """팀원 1 초안과 같은 이름의 진입점: 설정 -> 실행 -> 결과."""
    cfg = cfg or SimConfig.from_parameters()
    return VPPSimulation(cfg, orders=orders, verbose=verbose).run()


if __name__ == "__main__":
    from src.analysis.kpi import print_summary
    print_summary(run_simulation())
