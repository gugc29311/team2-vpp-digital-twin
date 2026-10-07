# -*- coding: utf-8 -*-
"""
대시보드용 시뮬레이션 실행 · 표 가공 (UI 없음) [명세서 11절 Dashboard].

  build_config(scenario, weeks, seed, rule) -> SimConfig   (main.py --weeks / --preset 과 같은 규칙)
  run(scenario, weeks, seed, rule)          -> SimulationResult (keep_events=True)
  summarize(res)                            -> dict (KPI · 자원표 · 병목 · 일별표 · 상태시간표 · 상태 구간표)
  replay_times_all / replay_states          -> 2D Factory Replay 용 시각별 방 단위 상태 [명세서 13절]
"""
import pandas as pd

from src.analysis.kpi import (MACHINE_STATES, bottleneck, daily_table, kpis, machine_state_hours,
                              utilization, worker_utilization, zone)
from src.experiments.scenarios import PRESETS, apply_preset
from src.logger.state import format_time, order_trace, state_at
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation

BASE_SCENARIOS = ("Normal", "High Demand", "Stress")
SCENARIO_OPTIONS = BASE_SCENARIOS + tuple(PRESETS)

RESOURCE_LABELS = {
    "vpp_printers": "프린터",
    "washing_machines": "세척기",
    "uv_curing_machines": "UV기",
    "job_assignment_workers": "작업 배정(JA)",
    "post_process_workers": "후공정 작업자",
    "quality_inspectors": "품질 검사원",
    "packers": "포장 작업자",
    "printer_operators": "프린터 오퍼레이터",
}
# 자원 이름 -> kpis() 의 대기 KPI 접두어
_WAIT_PREFIX = {"vpp_printers": "printer", "washing_machines": "washing", "uv_curing_machines": "uv"}


def build_config(scenario, weeks, seed, rule):
    """시나리오(또는 프리셋) · 기간(주) · 시드 · 프린터 순서 규칙 -> SimConfig. N주 실행은 워밍업 0."""
    base = SimConfig.from_parameters()
    overrides = {"ORDER_SOURCE": "random", "RANDOM_SEED": int(seed), "DEFAULT_SCHEDULING_RULE": rule,
                 "WARMUP_TIME": 0, "SIMULATION_TIME": int(weeks) * 168}
    if scenario in PRESETS:
        base = apply_preset(base, scenario)
    else:
        overrides["SCENARIO"] = scenario
    return base.replace(**overrides)


def run(scenario, weeks, seed, rule):
    return VPPSimulation(build_config(scenario, weeks, seed, rule), verbose=False, keep_events=True).run()


def resource_table(res, k):
    """자원별 용량 · 가동률 · 병목 비교값(프린터는 부하율 ρ) · 평균 대기 · 평균 대기열."""
    rows = []
    for name, u in utilization(res).items():
        prefix = _WAIT_PREFIX.get(name, name)
        compare = k["printer_rho"] if name == "vpp_printers" else u["utilization"]
        rows.append({
            "resource": name,
            "자원": RESOURCE_LABELS.get(name, name),
            "용량": u["capacity"],
            "가동률": u["utilization"],
            "병목 비교값": compare,
            "판정": zone(compare),
            "평균 대기(h)": k.get(f"{prefix}_wait_h_mean", float("nan")),
            "평균 대기열": k.get(f"{prefix}_queue_mean", float("nan")),
        })
    return pd.DataFrame(rows)


def worker_table(res):
    role_of = {w: role for role, ids in res.workers.items() for w in ids}
    return pd.DataFrame([{"작업자": w, "역할": RESOURCE_LABELS.get(role_of.get(w), role_of.get(w)), "가동률": v}
                         for w, v in worker_utilization(res).items()])


def state_hours_table(res):
    """설비 x 상태 6종 누적 시간 [h] (행 = 설비)."""
    df = pd.DataFrame.from_dict(machine_state_hours(res), orient="index")[list(MACHINE_STATES)]
    df["합계"] = df.sum(axis=1)
    df.index.name = "설비"
    return df


def phases_table(res):
    """machine_phases -> 간트 차트용 표 (길이 0 구간 제외)."""
    df = pd.DataFrame(res.machine_phases, columns=["unit", "state", "start", "end"])
    df = df[df["end"] > df["start"]].copy()
    df["duration"] = df["end"] - df["start"]
    df["start_label"] = df["start"].map(format_time)
    df["end_label"] = df["end"].map(format_time)
    return df


# ------------------------------------------------------------------ 2D Factory Replay [명세서 13절]
# 방 배치(좌표)는 모델에 없음 -> 화면용 임의 배치 ⚠️ 가정값 (실제 공장 배치는 인터뷰 확인 필요)
# 방 -> (x0, y0, x1, y1). 위쪽 = 프린터실(온라인 주문 접수·작업 배정 포함)·후공정, 가운데 = 복도(이동),
# 아래쪽 = 포장·검사·UV·세척. 주문은 온라인 접수라 주문 데스크는 두지 않음
ROOM_LAYOUT = {
    "Print Room": (0.0, 4.6, 5.9, 8.0),
    "Post-processing Room": (6.1, 4.6, 12.0, 8.0),
    "Corridor": (0.0, 3.5, 12.0, 4.3),
    "Packing Room": (0.0, 0.0, 2.85, 3.2),
    "Inspection Room": (3.05, 0.0, 5.9, 3.2),
    "UV Room": (6.1, 0.0, 8.95, 3.2),
    "Wash Room": (9.15, 0.0, 12.0, 3.2),
}
ROOM_LABELS = {"Print Room": "프린터실 (작업 배정·출력)", "Post-processing Room": "후공정실",
               "Corridor": "복도 (이동)", "Packing Room": "포장실", "Inspection Room": "검사실",
               "UV Room": "UV실", "Wash Room": "세척실"}
# 설비 종류(이름 접두어) -> 공정 / 대기열 키
_UNIT_PROCESS = (("WASH", "Washing", "washing"), ("UV", "UV Curing", "uv"), ("P", "VPP Build", "printer"))
# 작업자 역할 -> 대기 중일 때 머무는 공정
_ROLE_HOME = {"job_assignment_workers": "Job Assignment", "post_process_workers": "Support Removal",
              "quality_inspectors": "Inspection", "packers": "Packaging", "printer_operators": "VPP Build"}


def _unit_process(name):
    for prefix, process, queue in _UNIT_PROCESS:
        if name.startswith(prefix):
            return process, queue
    return None, None


def replay_times_all(step_min, end_time):
    """시뮬레이션 전체 구간 [0, 종료] 를 step_min 분 간격으로 (날짜 구분 없이 연속). t=0 = Day 1 09:00."""
    step = step_min / 60.0
    n = int(end_time / step + 1e-9)
    return [round(i * step, 6) for i in range(n + 1)]


def replay_states(res, times):
    """시각별 2D 화면용 상태 (state_at 을 방 단위로 묶음). 이벤트 로그 필요 (run 은 keep_events=True)."""
    loc = res.cfg.LOCATIONS
    role_of = {w: role for role, ids in res.workers.items() for w in ids}
    # 작업자 작업 문자열("LOADING B00001-W3") -> 방. 같은 작업 이름이 세척·UV 에 다 있어 대상 ID 까지 키로 씀
    task_room = {}
    for e in res.log.events:
        if e.resource in role_of and e.event.endswith("_START"):
            task_room[f"{e.event.rsplit('_', 1)[0]} {e.entity_id}"] = loc.get(e.process, "Corridor")
    out = []
    for t in times:
        st = state_at(res, t)
        rooms = {r: {"waiting": 0, "processing": 0, "by_process": {}, "ids": []} for r in ROOM_LAYOUT}
        for oid, o in st["orders"].items():
            if o["state"] == "Done":
                continue
            r = rooms[loc.get(o["process"], "Corridor")]
            r["processing" if o["state"] == "Processing" else "waiting"] += 1
            key = f"{o['process']} ({o['state']})"
            r["by_process"][key] = r["by_process"].get(key, 0) + 1
            r["ids"].append(oid)
        workers = {}
        for w, task in st["workers"].items():
            home = loc.get(_ROLE_HOME.get(role_of[w], ""), "Corridor")
            workers[w] = {"room": task_room.get(task, home) if task else home, "task": task}
        out.append({
            "t": t, "label": st["label"], "wip": st["wip"], "completed": st["completed"],
            "late": len(st["late_orders"]), "resin_L": st["resin_L"],
            "machines": {u: {"state": s, "room": loc.get(_unit_process(u)[0], "Corridor")}
                         for u, s in st["machines"].items()},
            "workers": workers, "queues": st["queues"], "rooms": rooms,
        })
    return out


def order_trace_table(res, order_id):
    return pd.DataFrame(order_trace(res, order_id))


def summarize(res):
    k = kpis(res)
    return {
        "kpis": k,
        "resources": resource_table(res, k),
        "workers": worker_table(res),
        "bottleneck": bottleneck(res),
        "daily": pd.DataFrame(daily_table(res)),
        "state_hours": state_hours_table(res),
        "phases": phases_table(res),
        "units": [u.name for u in res.units],
        "end_time": res.end_time,
        "n_orders": len(res.orders),
        "n_batches": len(res.batches),
        "arrival_rate": res.cfg.arrival_rate_per_week,
        "printer_capacity_h": res.cfg.PRINTER_EFFECTIVE_CAPACITY_H_PER_WEEK,
    }
