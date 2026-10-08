# -*- coding: utf-8 -*-
"""
공장 자원 (SimPy).

설비 (대기열 + 설비 객체 풀 구조)
  vpp_printers / printer_pool
      대기열(PriorityResource)이 FCFS/SPT/EDD 로 '누가 먼저' 를 정하고,
      순서를 받은 배치가 풀(Store)에서 '지금 쓸 수 있는' 프린터 객체를 꺼낸다.
      고장·PM·세척액 교체 중인 설비는 풀에 없으므로 자연히 사용 불가 -> 대당 상태 추적 가능.
  washing_machines / washing_pool, uv_curing_machines / uv_pool : 같은 구조 (로드는 FIFO)
인력 (대기열 + 개인 ID 목록)
  job_assignment_workers(JA1..), post_process_workers(PP1..), quality_inspectors(QI1..),
  packers(PK1.., 0 이면 검사원 겸직), printer_operators(PO1.., 0 이면 없음 — 현재 모델에서 작업 없음)
  대기열(simpy.Resource, FIFO)이 '누가 먼저' 를 정하고, 자리를 받은 순간 빈 사람 중 번호가 가장 작은 사람을 배정.
  배정은 yield 없이 즉시 (Store.get 을 쓰면 같은 시각 이벤트가 하나 더 생겨 동시각 처리 순서가 바뀔 수 있음)
  -> 대기 순서·타이밍은 역할 단위 Resource 와 완전히 같고, 누가 했는지만 추가로 기록된다 [명세서 1·10절].

capacity = 동시에 일할 수 있는 대수/인원. 로드 용량(한 번에 넣는 부품 수)은 공정 로직(로드 분할)에서 쓴다.
"""
import heapq
import re

import simpy

from src.utils.random_utils import sample


class EquipmentUnit:
    """
    설비 1대의 고장·PM 상태 [공통6].
      cum_op    : 누적 가동시간 [h]  (고장은 가동시간 기준)
      next_fail : 다음 고장이 나는 누적 가동시간
      last_pm   : 마지막 PM 완료 시각 (PM 은 달력 주기)
      log       : [(종류 'fail'/'pm'/'clean', 시작, 종료)]
    """

    def __init__(self, name, kind, params, rng, pm_offset=0.0):
        self.name, self.kind, self.p, self.rng = name, kind, params, rng
        self.cum_op = 0.0
        self.loads = 0                               # 세척기: 세척액 교체 카운터
        self.last_pm = -pm_offset
        self.next_fail = rng.exponential(params["mtbf"]) if params else float("inf")
        self.log = []
        self.state, self.state_since = "Idle", 0.0      # 장비 상태 6종 [명세서 11절] — simulation._phase 가 갱신

    def due(self, now):
        if not self.p:
            return False
        return self.cum_op >= self.next_fail or now - self.last_pm >= self.p["pm_every"]

    def service(self, env, cal, work_hours_only, on_event=None):
        """
        도래한 정비 수행: 고장 수리 -> PM. 비선점(다음 투입 직전 호출). 근무시간에만 진행 옵션.
        on_event(kind, edge): 기록용 콜백 ('fail'/'pm', 'start'/'end') — 동작·난수에 영향 없음.
        """
        wait = (lambda h: cal.delay(env, h)) if work_hours_only else (lambda h: _timeout(env, h))
        note = on_event or (lambda kind, edge: None)
        if not self.p:
            return
        if self.cum_op >= self.next_fail:
            s = env.now
            note("fail", "start")
            yield from wait(sample(self.p["repair"], self.rng))
            self.log.append(("fail", s, env.now))
            note("fail", "end")
            self.next_fail = self.cum_op + self.rng.exponential(self.p["mtbf"])
        if env.now - self.last_pm >= self.p["pm_every"]:
            s = env.now
            note("pm", "start")
            yield from wait(sample(self.p["pm"], self.rng))
            self.log.append(("pm", s, env.now))
            note("pm", "end")
            self.last_pm = env.now

    def clean(self, env, cal, hours):
        """세척액 교체 [공통3]: 근무시간 hours 동안 설비 사용 불가."""
        s = env.now
        yield from cal.delay(env, hours)
        self.log.append(("clean", s, env.now))


def _timeout(env, h):
    if h > 0:
        yield env.timeout(h)


WORKER_PREFIX = {"job_assignment_workers": "JA", "post_process_workers": "PP", "quality_inspectors": "QI",
                 "packers": "PK", "printer_operators": "PO"}
_PREFIX_ROLE = {v: k for k, v in WORKER_PREFIX.items()}


def role_of(resource_id):
    """개인 ID(예: PP2) -> 역할 이름(post_process_workers). 사람 ID 가 아니면 None (설비 P1, WASH1 등)."""
    m = re.fullmatch(r"([A-Z]+)\d+", resource_id or "")
    return _PREFIX_ROLE.get(m.group(1)) if m else None


def worker_ids(role, n):
    return [f"{WORKER_PREFIX[role]}{i + 1}" for i in range(n)]


class FactoryResources:
    def __init__(self, env, cfg, fail_rng):
        self.env = env
        fp = cfg.EQUIPMENT_FAILURE if cfg.BREAKDOWN_ENABLED else {}

        def units(kind, n, prefix):
            p = fp.get(kind)
            offs = (p or {}).get("pm_offsets", [0.0])
            return [EquipmentUnit(f"{prefix}{i + 1}", kind, p, fail_rng, offs[i % len(offs)]) for i in range(n)]

        def pool(unit_list):
            st = simpy.Store(env, capacity=max(1, len(unit_list)))     # simpy Store 는 용량 0 불가
            st.items.extend(unit_list)
            return st

        # Equipment
        # 프린터 0대 [명세서 14절 Level 5]: simpy 자원은 용량 0 불가 -> 대기열 자리 1 + 빈 풀.
        # 첫 배치는 빈 풀에서, 나머지는 대기열에서 영원히 기다림 (보고 용량은 capacity_of = 0).
        self.vpp_printers = simpy.PriorityResource(env, capacity=max(1, cfg.VPP_PRINTER_COUNT))
        self.printer_units = units("printer", cfg.VPP_PRINTER_COUNT, "P")
        self.printer_pool = pool(self.printer_units)
        self.washing_machines = simpy.Resource(env, capacity=cfg.WASHING_MACHINE_COUNT)
        self.washing_units = units("washing", cfg.WASHING_MACHINE_COUNT, "WASH")
        self.washing_pool = pool(self.washing_units)
        self.uv_curing_machines = simpy.Resource(env, capacity=cfg.UV_CURING_MACHINE_COUNT)
        self.uv_units = units("uv_curing", cfg.UV_CURING_MACHINE_COUNT, "UV")
        self.uv_pool = pool(self.uv_units)
        self.pools = {"vpp_printers": self.printer_pool, "washing_machines": self.washing_pool,
                      "uv_curing_machines": self.uv_pool}
        # Human resources
        self.job_assignment_workers = simpy.Resource(env, capacity=cfg.JOB_ASSIGNMENT_WORKER_COUNT)
        self.post_process_workers = simpy.Resource(env, capacity=cfg.POST_PROCESS_WORKER_COUNT)
        self.quality_inspectors = simpy.Resource(env, capacity=cfg.QUALITY_INSPECTOR_COUNT)
        self.packers = simpy.Resource(env, capacity=cfg.PACKER_COUNT) if cfg.PACKER_COUNT else None
        self.printer_operators = (simpy.Resource(env, capacity=cfg.PRINTER_OPERATOR_COUNT)
                                  if cfg.PRINTER_OPERATOR_COUNT else None)
        # AMR (운반 로봇): 대기열 + 개별 ID (AMR1, AMR2 …)
        use_amr = cfg.TRANSPORT_ENABLED and cfg.TRANSPORT_MODE == "amr"
        self.amrs = simpy.Resource(env, capacity=cfg.AMR_COUNT) if use_amr else None
        self.amr_ids = [f"AMR{i + 1}" for i in range(cfg.AMR_COUNT)] if use_amr else []
        self.amr_free = list(self.amr_ids)
        # 개인 ID: 역할 -> 전체 목록 / 지금 비어 있는 번호(최소 힙)
        self.workers = {role: worker_ids(role, self.capacity_of(role)) for role in WORKER_PREFIX
                        if self.capacity_of(role) > 0}
        self._free = {role: list(range(len(ids))) for role, ids in self.workers.items()}

    def take_worker(self, role, key=None):
        """
        대기열 자리를 받은 직후 호출 (yield 없음): 빈 사람 중 번호가 가장 작은 사람의 ID.
        key(작업자 ID) 를 주면 그 값(작업 장소까지 거리)이 가장 작은 사람, 같으면 번호가 작은 사람.
        """
        free = self._free[role]
        if key is None:
            return self.workers[role][heapq.heappop(free)]
        ids = self.workers[role]
        i = min(free, key=lambda j: (key(ids[j]), j))
        free.remove(i)
        heapq.heapify(free)
        return ids[i]

    def release_worker(self, role, worker_id):
        heapq.heappush(self._free[role], self.workers[role].index(worker_id))

    def capacity_of(self, name):
        if name == "vpp_printers":
            return len(self.printer_units)                 # 0대면 0 (대기열 자리 1 은 구현상 자리)
        res = getattr(self, name)
        return res.capacity if res is not None else 0

    @property
    def all_units(self):
        return self.printer_units + self.washing_units + self.uv_units

    # 가동률 계산 대상: (자원 이름, 사람이면 True)
    TRACKED = (("vpp_printers", False), ("washing_machines", False), ("uv_curing_machines", False),
               ("job_assignment_workers", True), ("post_process_workers", True),
               ("quality_inspectors", True), ("packers", True), ("printer_operators", True),
               ("amrs", False))
