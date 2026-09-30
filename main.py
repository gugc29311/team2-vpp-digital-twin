# -*- coding: utf-8 -*-
"""
VPP Digital Twin 실행 진입점. 반드시 프로젝트 최상위 폴더에서 실행:

  python main.py                                   # data/sample_orders.csv 로 공정 통과 확인 (추적 출력)
  python main.py --quiet                           # 요약만
  python main.py --mode random                     # Normal 시나리오 1회 (105주, 수십 초)
  python main.py --mode random --scenario "High Demand" --seed 7
  python main.py --mode random --reps 30           # 30회 독립 반복 + 95% CI (병렬)
  python main.py --mode random --weeks 2 --keep-events   # 2주만 실행 + 이벤트 로그 저장 (OME 조회·Replay용)
  python main.py --preset "Rush Order" --reps 30   # 실험 프리셋 (src/experiments/scenarios.py, random 모드)
"""
import argparse
import csv
import os

from src.analysis.kpi import print_summary, save_outputs
from src.experiments.scenarios import PRESETS, apply_preset
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation

KEY_KPIS = ["printer_rho", "util_job_assignment_workers", "util_post_process_workers", "util_quality_inspectors",
            "util_washing_machines", "util_uv_curing_machines", "mean_batch_size", "mean_build_time_h",
            "lead_work_h_mean", "lead_work_h_p95", "lead_calendar_h_mean", "lead_work_rework_h_mean",
            "on_time_normal", "on_time_urgent", "rework_share_printed", "resin_L_per_week",
            "printer_failures_per_unit", "washing_liquid_changes_per_week","throughput_per_week", "wip_mean", 
            "printer_queue_mean", "printer_wait_h_mean", "tardiness_work_h_mean"]


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
    args = ap.parse_args()
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


if __name__ == "__main__":
    main()