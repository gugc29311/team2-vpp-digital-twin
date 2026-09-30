# -*- coding: utf-8 -*-
"""
KPI 계산 · 요약 출력 · CSV 저장.

측정 구간 = [measure_start, 종료] (random 모드 = WARMUP_TIME 이후, csv 모드 = 처음부터).
주문 KPI 는 측정 구간에 '도착'해 완료된 주문 기준.

  order_table  : 주문별 리드타임(달력/근무시간), 납기 준수, 상태
  batch_table  : 배치별 부품 수·트리거·출력시간
  utilization  : 자원별 가동률 = 점유 근무시간 / (용량 x 가용시간)
                 가용시간: 사람·세척·UV = 근무시간, 프린터 = 무인운전이면 달력시간
  kpis         : 반복실험·회귀검증용 스칼라 KPI 모음 (프린터 부하율 ρ = 정의 B 포함)
"""
import csv
import os

import numpy as np

def _level_series(intervals):
    """[(시작, 끝 또는 None), ...] -> 동시 개수 계단함수 [(t, 개수)]. 끝이 None이면 진행 중."""
    ev = sorted([(s, 1) for s, e in intervals] + [(e, -1) for s, e in intervals if e is not None])
    lvl, out = 0, []
    for t, d in ev:
        lvl += d
        out.append((t, lvl))
    return out


def _step_stats(series, t0, t1):
    """계단함수의 [t0, t1] 시간평균·최대. 구간 길이 0 (예: 주문 0건 csv) 이면 NaN."""
    if t1 <= t0:
        return float("nan"), float("nan")
    area, last_t, w, peak = 0.0, t0, 0, 0
    for t, nw in series:
        if t <= t0:
            w = nw
            continue
        if t >= t1:
            break
        area += w * (t - last_t)
        peak = max(peak, w)
        last_t, w = t, nw
    peak = max(peak, w)
    area += w * (t1 - last_t)
    return area / (t1 - t0), peak

def measure_start(res):
    """측정 시작 시각: random 모드 = WARMUP_TIME, csv 모드 = 0 (검증용 CSV 는 워밍업 없이 전체 집계)."""
    return res.cfg.WARMUP_TIME if res.cfg.ORDER_SOURCE == "random" else 0.0


def _weeks(res):
    """측정 구간 길이 [주] — 근무시간 기준 (24시간 연속 시계면 40h = 1주, 1번 정의와 동일)."""
    cal = res.calendar
    return cal.work_hours(measure_start(res), res.end_time) / cal.hours_per_week


def order_table(res):
    cal, warm = res.calendar, measure_start(res)
    rows = []
    for o in res.orders:
        if o.arrival_time < warm:
            continue
        done = o.completed_time is not None
        rows.append({
            "order_id": o.order_id, "priority": o.priority, "material": o.material, "quantity": o.quantity,
            "arrival_time": round(o.arrival_time, 4), "due_date": round(o.due_date, 4),
            "completed_time": round(o.completed_time, 4) if done else "",
            "lead_time_h": round(o.completed_time - o.arrival_time, 4) if done else "",
            "lead_time_work_h": round(cal.work_hours(o.arrival_time, o.completed_time), 4) if done else "",
            "on_time": (o.completed_time <= o.due_date + 1e-9) if done else "",
            "reworks": o.reworks, "status": o.status,
            "parts_good": o.parts_good, "parts_scrapped": o.parts_scrapped,
        })
    return rows


def batch_table(res):
    return [{
        "batch_id": b.batch_id, "material": b.material, "n_parts": b.n_parts,
        "parts": " ".join(p.part_id for p in b.parts), "trigger": b.trigger or "OPEN",
        "created": round(b.created_time, 4), "closed": "" if b.closed_time is None else round(b.closed_time, 4),
        "print_start": "" if b.print_start is None else round(b.print_start, 4),
        "print_end": "" if b.print_end is None else round(b.print_end, 4),
        "build_time_h": "" if b.build_time is None else round(b.build_time, 4),
        "total_area_mm2": round(b.total_area, 1),
    } for b in res.batches]


def utilization(res):
    cfg, cal = res.cfg, res.calendar
    t0, t1 = measure_start(res), res.end_time
    busy_by = {}
    for r, s, e, _ in res.log.busy:
        busy_by.setdefault(r, []).append((s, e))
    out = {}
    for name, cap in res.capacities.items():
        if cap == 0:
            continue
        calendar_time = name == "vpp_printers" and cfg.USE_WORK_CALENDAR and cfg.PRINTER_UNATTENDED
        avail = (t1 - t0) if calendar_time else cal.work_hours(t0, t1)
        busy = 0.0
        for s, e in busy_by.get(name, []):
            if e <= t0 or s >= t1:
                continue
            a, b = max(s, t0), min(e, t1)
            busy += (b - a) if calendar_time else cal.work_hours(a, b)
        out[name] = {"capacity": cap, "busy_h": busy, "available_h": avail,
                     "utilization": busy / (cap * avail) if avail > 0 else 0.0}
    return out


def _downtime(res, prefix):
    """설비 종류별 측정 구간 고장·PM·세척액 교체 횟수와 다운 시간."""
    t0, t1 = measure_start(res), res.end_time
    out = {"fail": 0, "pm": 0, "clean": 0, "down_h": 0.0, "units": 0}
    for u in res.units:
        if not u.name.startswith(prefix):
            continue
        out["units"] += 1
        for kind, s, e in u.log:
            if t0 <= s < t1:
                out[kind] += 1
            out["down_h"] += max(0.0, min(e, t1) - max(s, t0))
    return out


def kpis(res):
    """반복실험·회귀검증용 KPI (스칼라). 없는 값은 NaN."""
    cfg, cal = res.cfg, res.calendar
    t0, t1 = measure_start(res), res.end_time
    weeks = _weeks(res)
    u = utilization(res)
    nan = float("nan")
    k = {f"util_{name}": v["utilization"] for name, v in u.items()}

    # 프린터: 주당 출력시간, 부하율 ρ (정의 B = 주당 출력시간 / 유효 처리용량)
    pbusy = sum(max(0.0, min(e, t1) - max(s, t0)) for r, s, e, _ in res.log.busy if r == "vpp_printers")
    k["printer_busy_h_per_week"] = pbusy / weeks if weeks else nan
    capw = cfg.PRINTER_EFFECTIVE_CAPACITY_H_PER_WEEK
    k["printer_rho"] = k["printer_busy_h_per_week"] / capw if capw else nan

    # 배치
    bs = [b for b in res.batches if b.closed_time is not None and b.closed_time >= t0]
    k["batches_per_week"] = len(bs) / weeks if weeks else nan
    k["mean_batch_size"] = float(np.mean([b.n_parts for b in bs])) if bs else nan
    k["mean_build_time_h"] = float(np.mean([b.build_time for b in bs if b.build_time])) if bs else nan
    k["batch_trigger_area_share"] = float(np.mean([b.trigger == "AREA" for b in bs])) if bs else nan

    # 주문 리드타임 · 납기
    done = [o for o in res.orders if o.arrival_time >= t0 and o.completed_time is not None]
    arrived = [o for o in res.orders if o.arrival_time >= t0]
    k["orders_per_week"] = len(arrived) / weeks if weeks else nan
    k["completion_ratio"] = len(done) / len(arrived) if arrived else nan

    def stats(os_, prefix):
        if not os_:
            k[prefix + "_mean"] = k[prefix + "_p95"] = nan
            return
        lw = np.array([cal.work_hours(o.arrival_time, o.completed_time) for o in os_])
        k[prefix + "_mean"], k[prefix + "_p95"] = float(lw.mean()), float(np.percentile(lw, 95))

    stats(done, "lead_work_h")
    stats([o for o in done if o.reworks > 0], "lead_work_rework_h")
    lc = np.array([o.completed_time - o.arrival_time for o in done]) if done else np.array([nan])
    k["lead_calendar_h_mean"], k["lead_calendar_h_p95"] = float(lc.mean()), float(np.percentile(lc, 95))

    def on(os_all):
        """납기 준수율. 미완료인데 납기가 지난 주문은 지연으로 센다."""
        fin = [o for o in os_all if o.completed_time is not None]
        late_open = [o for o in os_all if o.completed_time is None and o.due_date < t1]
        n = len(fin) + len(late_open)
        return sum(o.completed_time <= o.due_date + 1e-9 for o in fin) / n if n else nan
    k["on_time_all"] = on(arrived)
    k["on_time_urgent"] = on([o for o in arrived if o.is_urgent])
    k["on_time_normal"] = on([o for o in arrived if not o.is_urgent])

    # 처리량 · Tardiness (근무시간)
    # 끝 시각 포함: csv 모드는 마지막 주문 완료 시각 = 종료 시각
    k["throughput_per_week"] = (sum(1 for o in res.orders if o.completed_time is not None
                                    and t0 <= o.completed_time <= t1) / weeks) if weeks else nan
    late = [cal.work_hours(o.due_date, o.completed_time) if o.completed_time > o.due_date + 1e-9 else 0.0
            for o in done]
    k["tardiness_work_h_mean"] = float(np.mean(late)) if late else nan
    k["tardiness_work_h_total"] = float(np.sum(late)) if late else nan

    # WIP (시간평균) + Little 법칙 교차검증, 프린터 대기열·대기시간
    wip = _level_series([(o.arrival_time, o.completed_time) for o in res.orders])
    k["wip_mean"], k["wip_max"] = _step_stats(wip, t0, t1)
    k["wip_little"] = len(arrived) / (t1 - t0) * k["lead_calendar_h_mean"] if t1 > t0 else nan  # λ(달력) x W(달력)
    q = _level_series([(b.closed_time, b.print_start) for b in res.batches if b.closed_time is not None])
    k["printer_queue_mean"], k["printer_queue_max"] = _step_stats(q, t0, t1)
    pw = [b.print_start - b.closed_time for b in bs if b.print_start is not None]
    k["printer_wait_h_mean"] = float(np.mean(pw)) if pw else nan
    k["printer_wait_h_p95"] = float(np.percentile(pw, 95)) if pw else nan

    # 재출력 · 레진
    pp = [rw for t, rw in res.printed_parts if t0 <= t < t1]
    k["rework_share_printed"] = float(np.mean(pp)) if pp else nan
    resin = sum(v for t, v, _ in res.resin_log if t0 <= t < t1)
    k["resin_L_per_week"] = resin / 1e6 / weeks if weeks else nan

    # 고장 · PM · 세척액 교체
    for prefix, name in (("P", "printer"), ("WASH", "washing"), ("UV", "uv")):
        d = _downtime(res, prefix)
        per_unit = max(1, d["units"])
        k[f"{name}_failures_per_unit"] = d["fail"] / per_unit
        k[f"{name}_pm_per_unit"] = d["pm"] / per_unit
        k[f"{name}_down_h_per_week"] = d["down_h"] / weeks if weeks else nan
        if name == "washing":
            k["washing_liquid_changes_per_week"] = d["clean"] / weeks if weeks else nan
    return k

def bottleneck(res):
    """부하가 가장 큰 자원 (이름, 값). 프린터는 달력시간 가동률이 아니라 부하율 ρ로 비교."""
    k, u = kpis(res), utilization(res)
    cand = {n: v["utilization"] for n, v in u.items() if n != "vpp_printers"}
    if k["printer_rho"] == k["printer_rho"]:            # NaN 아님
        cand["vpp_printers"] = k["printer_rho"]
    return max(cand.items(), key=lambda x: x[1]) if cand else ("", float("nan"))

def zone(u):
    """Hopp & Spearman 판정: <50% 여유 · 50~70% 주의 · ≥70% 위험."""
    return "위험" if u >= 0.70 else ("주의" if u >= 0.50 else "여유")


def print_summary(res, show_batches=None):
    orders = order_table(res)
    done = [r for r in orders if r["completed_time"] != ""]
    k = kpis(res)
    print()
    print("=" * 78)
    print(f"시나리오 {res.cfg.SCENARIO if res.cfg.ORDER_SOURCE == 'random' else 'CSV'} | "
          f"종료 {res.end_time:.1f}h | 주문 {len(orders)}건 중 완료 {len(done)}건 | 배치 {len(res.batches)}개")
    if done:
        print(f"리드타임(도착→완료) 근무시간 평균 {k['lead_work_h_mean']:.2f}h / P95 {k['lead_work_h_p95']:.2f}h | "
              f"달력 평균 {k['lead_calendar_h_mean']:.2f}h")
        print(f"납기 준수: 전체 {k['on_time_all']:.1%} | 일반 {k['on_time_normal']:.1%} | 긴급 {k['on_time_urgent']:.1%}")
    if res.cfg.ORDER_SOURCE == "random":
        print(f"프린터 부하율 ρ(정의 B) {k['printer_rho']:.1%} | 평균 배치 {k['mean_batch_size']:.1f}건 · "
              f"출력 {k['mean_build_time_h']:.2f}h | 재출력 비중 {k['rework_share_printed']:.2%} | "
              f"레진 {k['resin_L_per_week']:.2f} L/주")
    short = [r["order_id"] for r in orders if r["status"] == "SHORT"]
    if short:
        print(f"일부 폐기(SHORT) 주문: {short}")
    if show_batches is None:
        show_batches = len(res.batches) <= 20
    if show_batches:
        print("-" * 78)
        print(f"{'batch':<7} {'material':<8} {'parts':>5} {'trigger':<9} {'closed':>7} {'print(h)':>15}  부품")
        f2 = lambda x: "" if x == "" else f"{x:.2f}"
        for b in batch_table(res):
            pr = f"{f2(b['print_start'])}→{f2(b['print_end'])}" if b["print_start"] != "" else "-"
            print(f"{b['batch_id']:<7} {b['material']:<8} {b['n_parts']:>5} {b['trigger']:<9} {f2(b['closed']):>7} "
                  f"{pr:>15}  {b['parts']}")
    print("-" * 78)
    print(f"{'자원':<24} {'용량':>4} {'점유(h)':>10} {'가용(h)':>10} {'가동률':>7} 판정")
    for name, u in utilization(res).items():
        print(f"{name:<24} {u['capacity']:>4} {u['busy_h']:>10.1f} {u['available_h']:>10.1f} "
              f"{u['utilization']:>7.1%} {zone(u['utilization'])}")
    if res.cfg.BREAKDOWN_ENABLED:
        print(f"고장/PM(대당): 프린터 {k['printer_failures_per_unit']:.0f}/{k['printer_pm_per_unit']:.0f}회, "
              f"세척 {k['washing_failures_per_unit']:.0f}/{k['washing_pm_per_unit']:.0f}회, "
              f"UV {k['uv_failures_per_unit']:.0f}/{k['uv_pm_per_unit']:.0f}회")
    print("=" * 78)


def daily_table(res, day_h=24.0):
    """달력 1일 단위 시계열: 처리량, 지연 완료 수, WIP, 프린터 대기열, 프린터 가동률."""
    t0, t1 = measure_start(res), res.end_time
    wip = _level_series([(o.arrival_time, o.completed_time) for o in res.orders])
    que = _level_series([(b.closed_time, b.print_start) for b in res.batches if b.closed_time is not None])
    pr = [(s, e) for r, s, e, _ in res.log.busy if r == "vpp_printers"]
    cap = res.capacities.get("vpp_printers", 0)
    rows, a = [], t0
    while a + 1e-9 < t1:
        b = min(a + day_h, t1)
        last = b >= t1                                   # 마지막 날은 종료 시각 포함 (throughput_per_week 와 같은 기준)
        finished = [o for o in res.orders if o.completed_time is not None
                    and a <= o.completed_time and (o.completed_time < b or last and o.completed_time <= b)]
        busy = sum(max(0.0, min(e, b) - max(s, a)) for s, e in pr)
        rows.append({
            "day": int((a - t0) // day_h) + 1,
            "throughput": len(finished),
            "late_completed": sum(o.completed_time > o.due_date + 1e-9 for o in finished),
            "wip_mean": round(_step_stats(wip, a, b)[0], 3),
            "printer_queue_mean": round(_step_stats(que, a, b)[0], 3),
            "printer_util": round(busy / (cap * (b - a)), 4) if cap else "",
        })
        a = b
    return rows


def save_outputs(res, out_dir="outputs", daily=False):
    os.makedirs(out_dir, exist_ok=True)
    if res.log.keep_events:
        res.log.to_csv(os.path.join(out_dir, "event_log.csv"))
    tables = [("order_summary.csv", order_table(res)), ("batch_summary.csv", batch_table(res))]
    if daily:
        tables.append(("daily_summary.csv", daily_table(res)))
    for name, rows in tables:
        if not rows:
            continue
        with open(os.path.join(out_dir, name), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return out_dir
