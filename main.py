# -*- coding: utf-8 -*-
"""
VPP Digital Twin 실행 진입점. 어느 폴더에서 실행해도 프로젝트 최상위 폴더 기준으로 동작
(data/ 를 읽고 outputs/ 에 저장. VS Code ▶ 실행 버튼도 가능):

  python main.py                                   # data/sample_orders.csv 로 공정 통과 확인 (추적 출력)
  python main.py --quiet                           # 요약만
  python main.py --mode random                     # Normal 시나리오 1회 (105주, 수십 초)
  python main.py --mode random --scenario "High Demand" --seed 7
  python main.py --mode random --reps 30           # 30회 독립 반복 + 95% CI (병렬)
  python main.py --mode random --weeks 2 --keep-events   # 2주만 실행 + 이벤트 로그 저장 (OME 조회·Replay용)
  python main.py --preset "Rush Order" --reps 30   # 실험 프리셋 (src/experiments/scenarios.py, random 모드)
  python main.py --quiet --at "Day 2 10:00"        # 시각 조회 (OME Time Query): 그 시각의 주문·설비·작업자·대기열
"""
import argparse
import csv
import os

from src.analysis.kpi import print_summary, save_outputs
from src.experiments.scenarios import PRESETS, apply_preset
from src.logger.state import format_time, order_trace, parse_time, print_state, print_trace, state_at
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation

KEY_KPIS = ["printer_rho", "util_job_assignment_workers", "util_post_process_workers", "util_quality_inspectors",
            "util_washing_machines", "util_uv_curing_machines", "mean_batch_size", "mean_build_time_h",
            "lead_work_h_mean", "lead_work_h_p95", "lead_calendar_h_mean", "lead_work_rework_h_mean",
            "on_time_normal", "on_time_urgent", "rework_share_printed", "resin_L_per_week",
            "printer_failures_per_unit", "washing_liquid_changes_per_week","throughput_per_week", "wip_mean", 
            "printer_queue_mean", "printer_wait_h_mean", "tardiness_work_h_mean",
            "washing_queue_mean", "washing_wait_h_mean", "uv_queue_mean", "uv_wait_h_mean",
            "job_assignment_workers_wait_h_mean", "post_process_workers_wait_h_mean",
            "quality_inspectors_wait_h_mean"]


def main():
    ap = argparse.ArgumentParser(description="VPP Digital Twin simulation")
    ap.add_argument("--mode", choices=["csv", "random"], help="주문 입력 방식 (기본: parameters.py)")
    ap.add_argument("--orders", help="주문 CSV 경로 (csv 모드)")
    ap.add_argument("--scenario", help="random 모드 시나리오 (Normal / High Demand / Stress)")
    ap.add_argument("--preset", choices=list(PRESETS), help="실험 프리셋 (random 모드, 시나리오 포함)")
    ap.add_argument("--seed", type=int, help="난수 시드 (단일 실행)")
    ap.add_argument("--reps", type=int, help="독립 반복 횟수 (지정 시 반복실험 모드)")
    ap.add_argument("--jobs", type=int, help="반복실험 병렬 프로세스 수 (기본: CPU-1)")
    ap.add_argument("--quiet", action="store_true", help="공정 추적 출력 끄기")
    ap.add_argument("--out", default="outputs", help="결과 CSV 저장 폴더")
    ap.add_argument("--keep-events", action="store_true", help="random 모드에서도 이벤트 로그 보관·저장")
    ap.add_argument("--weeks", type=int, help="random 모드 측정 기간(주), 지정 시 워밍업 0 (KPI는 초기 상태 포함)")
    ap.add_argument("--at", help='시각 조회 "Day 3 14:25" (Day 1 = 월요일 09:00 시작). csv 모드 또는 --keep-events 필요')
    ap.add_argument("--trace", help="주문 1건 전체 이벤트 추적 (예: O001). csv 모드 또는 --keep-events 필요")
    args = ap.parse_args()
    if args.orders:                                   # 사용자가 준 경로는 실행한 위치 기준 -> 절대경로로 고정
        args.orders = os.path.abspath(args.orders)
    if args.out != ap.get_default("out"):             # --out 도 실행한 위치 기준 (기본값 outputs 는 프로젝트 폴더)
        args.out = os.path.abspath(args.out)
    os.chdir(os.path.dirname(os.path.abspath(__file__)))   # data/·outputs/ 상대경로 기준 = 프로젝트 최상위 폴더
    if args.preset and args.mode == "csv":
        ap.error("--preset 는 random 모드 전용")
    if args.preset and args.scenario:
        ap.error("--preset 에 시나리오가 들어 있으므로 --scenario 와 함께 쓰지 않음")
    base_mode = "random" if args.preset else (args.mode or SimConfig.from_parameters().ORDER_SOURCE)
    if args.orders and base_mode == "random":
        ap.error("--orders 는 csv 모드에서만 사용")
    if args.jobs is not None and not args.reps:
        ap.error("--jobs 는 --reps 와 함께 사용")
    if args.weeks is not None and base_mode != "random":
        ap.error("--weeks 는 random 모드에서만 사용")
    for opt in ("at", "trace"):
        if getattr(args, opt) is not None:
            if args.reps:
                ap.error(f"--{opt} 은 단일 실행에서만 사용 (--reps 와 함께 쓰지 않음)")
            if base_mode != "csv" and not args.keep_events:
                ap.error(f"--{opt} 은 이벤트 로그가 필요: csv 모드 또는 --keep-events")
    at = None
    if args.at is not None:
        try:
            at = parse_time(args.at)
        except ValueError as e:
            ap.error(str(e))

    overrides = {}
    if args.mode:
        overrides["ORDER_SOURCE"] = args.mode
    if args.orders:
        overrides["ORDER_CSV_PATH"] = args.orders
    if args.scenario:
        overrides["SCENARIO"] = args.scenario
    if args.seed is not None:
        overrides["RANDOM_SEED"] = args.seed
    if args.weeks:
        overrides["WARMUP_TIME"] = 0
        overrides["SIMULATION_TIME"] = args.weeks * 168
    base = SimConfig.from_parameters()
    if args.preset:
        base = apply_preset(base, args.preset)
        print(f"[프리셋 {args.preset}] {PRESETS[args.preset]}")
    cfg = base.replace(**overrides)
    label = args.preset or cfg.SCENARIO

    if args.reps:
        from src.experiments.replications import print_summary_table, run_replications, summarize
        rows = run_replications(cfg, n=args.reps, jobs=args.jobs)
        # 반복별 결과 저장 (rows가 KPI dict 리스트일 때)
        os.makedirs(args.out, exist_ok=True)
        path = f"{args.out}/replications_{label.replace(' ', '_')}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:     # 엑셀에서 한글 안 깨짐
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print_summary_table(summarize(rows), KEY_KPIS,
                            title=f"[{label}] {args.reps}회 독립 반복 (λ={cfg.arrival_rate_per_week}건/주)")
        print(f"반복별 결과 저장: {path}")
        return

    verbose = not args.quiet and cfg.ORDER_SOURCE == "csv"
    res = VPPSimulation(cfg, verbose=verbose,
                        keep_events=(cfg.ORDER_SOURCE == "csv" or args.keep_events)).run()
    print_summary(res)
    print(f"결과 저장: {save_outputs(res, args.out, daily=cfg.ORDER_SOURCE == 'random')}/")
    if at is not None:
        if at > res.end_time:
            print(f"주의: 조회 시각 {format_time(at)} 이 시뮬레이션 종료({format_time(res.end_time)}) 이후 — 종료 시점 상태")
        print_state(state_at(res, at))
    if args.trace:
        print(f"[주문 추적] {args.trace}")
        print_trace(order_trace(res, args.trace))


if __name__ == "__main__":
    main()