# -*- coding: utf-8 -*-
"""
대시보드용 시뮬레이션 실행 · 표 가공 (UI 없음) [명세서 11절 Dashboard].

  build_config(scenario, weeks, seed, rule) -> SimConfig   (main.py --weeks / --preset 과 같은 규칙)
  run(scenario, weeks, seed, rule)          -> SimulationResult (keep_events=True)
  summarize(res)                            -> dict (KPI · 자원표 · 병목 · 일별표 · 상태시간표 · 상태 구간표)
  replay_times_all / replay_times_window / replay_states -> Factory Replay 용
  replay_tracks(res, t0, t1)                -> 실시간 Replay 용 이벤트 기반 궤적 (작업자 이동·설비 상태·순간이동)
  unit_frame / stage_frame / checks_frame   -> 개발자용 화면 표 (설비 대별 · 주문 단계 · 교차 검증) 시각별 방 단위 상태 [명세서 13절]
"""
import pandas as pd

from src.analysis.kpi import (MACHINE_STATES, ORDER_STAGES, bottleneck, daily_table, kpis, machine_state_hours,
                              order_stage_table, unit_table, utilization, worker_utilization, zone)
from src.experiments.scenarios import PRESETS, apply_preset
from src.logger.state import format_time, order_trace, state_at
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation
from src.validation.crosscheck import cross_checks
from src.validation.locations import DOOR, amr_tasks, move_endpoints, order_jumps, worker_jumps, worker_tasks

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
    "amrs": "AMR (운반 로봇)",
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
M_PER_UNIT = 2.5         # 화면 1칸 = 2.5 m (parameters.ROOM_DOOR_X_M 과 같은 배치)
ROOM_LABELS = {"Print Room": "프린터실 (작업 배정·출력)", "Post-processing Room": "후공정실",
               "Corridor": "복도 (이동)", "Packing Room": "포장실", "Inspection Room": "검사실",
               "UV Room": "UV실", "Wash Room": "세척실"}
# 설비 종류(이름 접두어) -> 공정 / 대기열 키
_UNIT_PROCESS = (("WASH", "Washing", "washing"), ("UV", "UV Curing", "uv"), ("P", "VPP Build", "printer"))
# 작업자 역할 -> 대기 중일 때 머무는 공정
_ROLE_HOME = {"job_assignment_workers": "Job Assignment", "post_process_workers": "Support Removal",
              "quality_inspectors": "Inspection", "packers": "Packaging", "printer_operators": "VPP Build"}


# 실물이 없는 단계 (전산 처리): 주문 접수·작업 배정, 배치 구성(출력 파일 묶기), 출력 대기(준비 포함).
# 부품은 출력 시작부터 실물 -> Replay 에서는 방이 아니라 '실물 없음' 으로 따로 셈
VIRTUAL_GROUPS = ("접수·작업 배정", "배치 구성", "출력 대기")
_PRE_PRINT_EVENTS = ("PRINTER_QUEUE_ENTER", "BUILD_PREPARATION_START", "BUILD_PREPARATION_END")


def virtual_group(o):
    """state_at 의 주문 위치 -> 실물 없음 그룹 이름 (실물이면 None)."""
    if o["process"] in ("Order Reception", "Job Assignment"):
        return VIRTUAL_GROUPS[0]
    if o["process"] == "Batch Formation":
        return VIRTUAL_GROUPS[1]
    if o["event"] in _PRE_PRINT_EVENTS:
        return VIRTUAL_GROUPS[2]
    return None


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


def replay_times_window(start_h, dur_h, step_min, end_time):
    """실시간 Replay 용: [start_h, start_h + dur_h] 구간 (종료 시각에서 자름) 을 step_min 분 간격으로."""
    step = step_min / 60.0
    stop = min(start_h + dur_h, end_time)
    n = int((stop - start_h) / step + 1e-9)
    return [round(start_h + i * step, 6) for i in range(max(n, 0) + 1)]


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
        rooms = {r: {"waiting": 0, "processing": 0, "by_process": {}, "ids": [], "shelf": 0} for r in ROOM_LAYOUT}
        virtual = dict.fromkeys(VIRTUAL_GROUPS, 0)
        for oid, o in st["orders"].items():
            if o["state"] == "Done":
                continue
            g = virtual_group(o)
            if g:
                virtual[g] += 1
                continue
            room = loc.get(o["process"], "Corridor")
            ends = move_endpoints(res.cfg, o["event"])
            if ends and o["event"].endswith("_END"):                 # 이동 도착 = 도착 위치에서 다음 작업 대기
                if ends[1].endswith(DOOR):                            # 문 앞 선반 (AMR 모드)
                    shelf_room = ends[1][:-len(DOOR)]
                    if shelf_room in rooms:
                        rooms[shelf_room]["shelf"] += 1
                        continue
                room = ends[1]
            r = rooms[room if room in rooms else "Corridor"]
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
            "workers": workers, "queues": st["queues"], "rooms": rooms, "virtual": virtual,
        })
    return out


def _home_room(res, role):
    room = res.cfg.LOCATIONS.get(_ROLE_HOME.get(role, ""), "Corridor")
    return room if room in ROOM_LAYOUT else "Corridor"


def _split_loc(loc, rooms):
    """'Wash Room:door' -> (방 번호, 문 앞 여부)."""
    room = loc.split(":")[0]
    return (rooms.index(room) if room in rooms else rooms.index("Corridor")), int(loc.endswith(DOOR))


def replay_tracks(res, t0, t1):
    """
    실시간 Replay 용 이벤트 기반 궤적 [회의 피드백: 설정된 이동 시간·경로와 화면 이동이 일치하는지 확인].
      workers : 작업자별 {room0, door0: 구간 시작 위치, slot·lane: 방 안 자리, segs: [[시작, 끝, 이동?, 출발 방, 출발 문?,
                도착 방, 도착 문?, 작업]]}. 이동(WALK·HANDOFF·RECEIVE·사람 운반)만 움직이고 작업 중·대기 중에는 제자리
      amrs    : AMR 별 {x0: 시작 문 위치(화면 x), segs: [[시작, 끝, 출발 x, 도착 x, 적재?, 적재·하역 h, 작업]]}
      jumps   : 이동 이벤트 없이 위치가 바뀐 경우 [[작업자 번호, 시각, 출발 방, 도착 방, 작업]] (distance 모드면 0건)
      machines: 설비별 상태 구간 [[시작, 끝, 상태 번호]] (machine_phases 그대로)
    방은 ROOM_LAYOUT 순번, 상태는 MACHINE_STATES 순번, 시각은 달력 h.
    """
    rooms = list(ROOM_LAYOUT)
    cfg = res.cfg
    order = [w for ids in res.workers.values() for w in ids]
    slot = {w: ids.index(w) for ids in res.workers.values() for w in ids}
    lane = {w: k for k, ids in enumerate(res.workers.values()) for w in ids}
    tasks = worker_tasks(res)
    home = getattr(res, "worker_home", None) or {}
    role_of = {w: role for role, ids in res.workers.items() for w in ids}
    loc0 = {w: home.get(w, _home_room(res, role_of[w])) for w in order}
    segs = {w: [] for w in order}
    for w, s, e, task, ent, a, b, n in tasks:
        if s < t0 and (e is not None and e <= t0):
            loc0[w] = b                                         # 구간 시작 전에 끝난 작업 -> 그 위치에 있음
            continue
        if s >= t1:
            continue
        label = f"{task} {ent}" + (f" ({n}건)" if a != b and n else "")
        segs[w].append([round(s, 6), round(t1 if e is None else e, 6), int(a != b),
                        *_split_loc(a, rooms), *_split_loc(b, rooms), label])
    for w in order:                                             # 구간 시작에 진행 중인 작업이 있으면 그 출발 위치
        if segs[w] and segs[w][0][0] < t0:
            g = segs[w][0]
            loc0[w] = rooms[g[3]] + (DOOR if g[4] else "")
    jumps = [[order.index(j["worker"]), round(j["t"], 6), _split_loc(j["from"], rooms)[0], _split_loc(j["to"], rooms)[0],
              j["task"]] for j in worker_jumps(res, tasks) if t0 <= j["t"] <= t1]

    door_x = lambda loc: cfg.ROOM_DOOR_X_M[loc.split(":")[0]] / M_PER_UNIT
    amrs = {}
    if cfg.TRANSPORT_ENABLED and cfg.TRANSPORT_MODE == "amr":
        ids = [f"AMR{i + 1}" for i in range(cfg.AMR_COUNT)]
        x0 = {a: door_x(cfg.AMR_HOME) for a in ids}
        amrs = {a: [] for a in ids}
        for amr, s, e, task, ent, a, b, n, xfer in amr_tasks(res):
            if s < t0 and (e is not None and e <= t0):
                x0[amr] = door_x(b)
                continue
            if s >= t1 or amr not in amrs:
                continue
            label = f"{task} {ent}" + (f" ({n}건)" if n else "")
            amrs[amr].append([round(s, 6), round(t1 if e is None else e, 6), round(door_x(a), 4), round(door_x(b), 4),
                              int(task != "AMR_MOVE"), round(xfer, 9), label])
        amrs = [{"id": a, "x0": round(x0[a], 4), "segs": amrs[a]} for a in ids]

    units = [u.name for u in res.units]
    mph = {u: [] for u in units}
    for name, state, s, e in res.machine_phases:
        if e > t0 and s < t1 and name in mph:
            mph[name].append([round(s, 6), round(e, 6), MACHINE_STATES.index(state)])
    room_geo = []
    for r in rooms:
        x = cfg.ROOM_DOOR_X_M.get(r)
        y0, y1 = ROOM_LAYOUT[r][1], ROOM_LAYOUT[r][3]
        room_geo.append({"doorX": None if x is None else round(x / M_PER_UNIT, 4),
                         "side": 1 if y0 >= ROOM_LAYOUT["Corridor"][3] else -1})
    return {
        "t0": t0, "t1": t1, "depth": cfg.ROOM_DEPTH_M / M_PER_UNIT, "roomGeo": room_geo,
        "workers": [{"id": w, "room0": _split_loc(loc0[w], rooms)[0], "door0": _split_loc(loc0[w], rooms)[1],
                     "slot": slot[w], "lane": lane[w], "segs": segs[w]} for w in order],
        "amrs": amrs or [],
        "jumps": jumps,
        "machines": [mph[u] for u in units],
    }


def amr_frame(res):
    """AMR 별 운반 횟수 · 빈 차 이동 · 운행 시간 (개발자용)."""
    rows = {}
    for amr, s, e, task, ent, a, b, n, xfer in amr_tasks(res):
        if e is None:
            continue
        r = rows.setdefault(amr, {"AMR": amr, "운반 횟수": 0, "운반 부품": 0, "빈 차 이동": 0, "운행 시간 (h)": 0.0})
        if task == "AMR_MOVE":
            r["빈 차 이동"] += 1
        else:
            r["운반 횟수"] += 1
            r["운반 부품"] += n
        r["운행 시간 (h)"] += e - s
    for r in rows.values():
        r["운행 비율 (달력)"] = r["운행 시간 (h)"] / res.end_time if res.end_time else float("nan")
    return pd.DataFrame(sorted(rows.values(), key=lambda r: r["AMR"]))


# ------------------------------------------------------------------ 개발자용 표
UNIT_COLUMNS = {"unit": "설비", "kind": "종류", "utilization": "가동률 (Running+Setup)", "jobs": "처리 건수",
                "parts": "처리 부품", "parts_per_week": "부품/주", "failures": "고장 횟수",
                **{f"{s.lower()}_h": f"{s} (h)" for s in MACHINE_STATES}}


def unit_frame(res):
    return pd.DataFrame(unit_table(res)).rename(columns=UNIT_COLUMNS)


def stage_frame(res):
    return pd.DataFrame(order_stage_table(res))


def checks_frame(res):
    return pd.DataFrame(cross_checks(res))


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
        "unit_table": unit_frame(res),
        "amr_table": amr_frame(res),
        "transport_mode": res.cfg.TRANSPORT_MODE if res.cfg.TRANSPORT_ENABLED else "off",
        "stages": stage_frame(res),
        "checks": checks_frame(res),
        "order_jumps": order_jumps(res),
        "worker_jumps": pd.DataFrame(worker_jumps(res), columns=["worker", "t", "from", "to", "task", "entity"]),
        "end_time": res.end_time,
        "n_orders": len(res.orders),
        "n_batches": len(res.batches),
        "arrival_rate": res.cfg.arrival_rate_per_week,
        "printer_capacity_h": res.cfg.PRINTER_EFFECTIVE_CAPACITY_H_PER_WEEK,
    }
