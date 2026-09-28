# -*- coding: utf-8 -*-
"""
VPP Digital Twin 실행 진입점. 반드시 프로젝트 최상위 폴더에서 실행:

  python main.py                                   # data/sample_orders.csv 로 공정 통과 확인 (추적 출력)
  python main.py --quiet                           # 요약만
  python main.py --mode random                     # Normal 시나리오 1회 (105주, 수십 초)
  python main.py --mode random --scenario "High Demand" --seed 7
  python main.py --mode random --reps 30           # 30회 독립 반복 + 95% CI (병렬)
"""
import argparse

from src.analysis.kpi import print_summary, save_outputs
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation

KEY_KPIS = ["printer_rho", "util_job_assignment_workers", "util_post_process_workers", "util_quality_inspectors",
            "util_washing_machines", "util_uv_curing_machines", "mean_batch_size", "mean_build_time_h",
            "lead_work_h_mean", "lead_work_h_p95", "lead_calendar_h_mean", "lead_work_rework_h_mean",
            "on_time_normal", "on_time_urgent", "rework_share_printed", "resin_L_per_week",
            "printer_failures_per_unit", "washing_liquid_changes_per_week"]


def main():
    ap = argparse.ArgumentParser(description="VPP Digital Twin simulation")
    ap.add_argument("--mode", choices=["csv", "random"], help="주문 입력 방식 (기본: parameters.py)")
    ap.add_argument("--orders", help="주문 CSV 경로 (csv 모드)")
    ap.add_argument("--scenario", help="random 모드 시나리오 (Normal / High Demand / Stress)")
    ap.add_argument("--seed", type=int, help="난수 시드 (단일 실행)")
    ap.add_argument("--reps", type=int, help="독립 반복 횟수 (지정 시 반복실험 모드)")
    ap.add_argument("--jobs", type=int, help="반복실험 병렬 프로세스 수 (기본: CPU-1)")
    ap.add_argument("--quiet", action="store_true", help="공정 추적 출력 끄기")
    ap.add_argument("--out", default="outputs", help="결과 CSV 저장 폴더")
    args = ap.parse_args()

    overrides = {}
    if args.mode:
        overrides["ORDER_SOURCE"] = args.mode
    if args.orders:
        overrides["ORDER_CSV_PATH"] = args.orders
    if args.scenario:
        overrides["SCENARIO"] = args.scenario
    if args.seed is not None:
        overrides["RANDOM_SEED"] = args.seed
    cfg = SimConfig.from_parameters().replace(**overrides)

    if args.reps:
        from src.experiments.replications import print_summary_table, run_replications, summarize
        rows = run_replications(cfg, n=args.reps, jobs=args.jobs)
        print_summary_table(summarize(rows), KEY_KPIS,
                            title=f"[{cfg.SCENARIO}] {args.reps}회 독립 반복 (λ={cfg.arrival_rate_per_week}건/주)")
        return

    verbose = not args.quiet and cfg.ORDER_SOURCE == "csv"
    res = VPPSimulation(cfg, verbose=verbose, keep_events=cfg.ORDER_SOURCE == "csv").run()
    print_summary(res)
    print(f"결과 저장: {save_outputs(res, args.out)}/")


if __name__ == "__main__":
    main()
