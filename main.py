"""Punto de entrada CLI del Sistema Inteligente de Horarios ITIID."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kit.instances import build_instance_i1, build_instance_i2, build_instance_i3, materialize_all_kit_files
from kit.validator import validate_schedule
from src.evaluation.benchmark import run_full_benchmark
from src.evaluation.charts import generate_benchmark_charts
from src.llm_client import LLMConfig, detect_backend
from src.r1_data.excel_io import export_schedule_excels, import_instance_from_excel
from src.r1_data.pdf_export import export_schedule_pdfs
from src.r2_cpsat.solver import solve_cpsat
from src.r3_genetic.solver import solve_genetic
from src.r4_local_search.solver import solve_local_search
from src.r5_llm_extractor.extractor import evaluate_extractor
from src.r6_llm_explainer.explainer import explain_validation_report


PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Sistema Inteligente de Horarios ITIID")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # benchmark
    p_bm = sub.add_parser("benchmark", help="Corre la comparacion oficial (I1-I4, R2-R6, 10 corridas AG) y genera graficas")
    p_bm.add_argument("--time-limit", type=float, default=4.0, help="Limite de tiempo (s) por metodo/corrida")
    p_bm.add_argument("--ga-runs", type=int, default=10, help="Corridas independientes del Algoritmo Genetico")
    p_bm.add_argument("--provider", type=str, default="auto", choices=["auto", "ollama", "gemini", "openai", "offline"])

    # solve
    p_sol = sub.add_parser("solve", help="Resuelve una instancia (I1, I2, I3 o archivo Excel) y exporta Excel/PDF")
    p_sol.add_argument("--instance", type=str, default="I3", help="I1, I2, I3 o ruta a .xlsx / .json")
    p_sol.add_argument("--method", type=str, default="cpsat", choices=["cpsat", "ga", "local"])
    p_sol.add_argument("--scenario", type=str, default="C", choices=["A", "B", "C"])
    p_sol.add_argument("--time-limit", type=float, default=10.0)
    p_sol.add_argument("--export-dir", type=str, default="results/cli_export")

    # eval-r5
    p_r5 = sub.add_parser("eval-r5", help="Evalua R5 (extractor de preferencias) con Precision, Recall y F1")
    p_r5.add_argument("--provider", type=str, default="auto", choices=["auto", "ollama", "gemini", "openai", "offline"])

    # init-kit
    sub.add_parser("init-kit", help="Genera los archivos del kit comun (JSON, .ctt, plantillas Excel)")

    args = parser.parse_args()

    if args.cmd == "init-kit":
        paths = materialize_all_kit_files(PROJECT_ROOT / "data")
        print("Kit comun materializado:")
        for k, v in paths.items():
            print(f"  - {k}: {v}")
        return

    if args.cmd == "benchmark":
        cfg = LLMConfig(provider=args.provider)
        prov, model = detect_backend(cfg)
        print(f"Backend LLM detectado: {prov} ({model})")
        results = run_full_benchmark(
            PROJECT_ROOT,
            time_limit_sec=args.time_limit,
            ga_runs=args.ga_runs,
            llm_cfg=cfg,
        )
        charts = generate_benchmark_charts(results, PROJECT_ROOT / "results" / "charts")
        print(f"Benchmark completado. Graficas generadas: {len(charts)}")
        print(f"Tabla de posiciones: {PROJECT_ROOT / 'results' / 'leaderboard.csv'}")
        return

    if args.cmd == "eval-r5":
        cfg = LLMConfig(provider=args.provider)
        res = evaluate_extractor(llm_cfg=cfg)
        print(json.dumps({k: v for k, v in res.items() if k != "details"}, ensure_ascii=False, indent=2))
        return

    if args.cmd == "solve":
        if args.instance == "I1":
            inst = build_instance_i1(PROJECT_ROOT / "data")
        elif args.instance == "I2":
            inst = build_instance_i2()
        elif args.instance == "I3":
            inst = build_instance_i3()
        elif args.instance.endswith(".xlsx"):
            inst, warns = import_instance_from_excel(args.instance)
            for w in warns:
                print(f"[WARN] {w}")
        else:
            from kit.schema import Instance
            inst = Instance.from_json_file(args.instance)

        if args.method == "cpsat":
            sch = solve_cpsat(inst, scenario=args.scenario, time_limit_sec=args.time_limit)
        elif args.method == "ga":
            sch = solve_genetic(inst, scenario=args.scenario, time_limit_sec=args.time_limit)
        else:
            sch = solve_local_search(inst, scenario=args.scenario, time_limit_sec=args.time_limit)

        rep = validate_schedule(inst, sch, scenario=args.scenario)
        exp, _ = explain_validation_report(inst, rep, llm_cfg=LLMConfig(provider="offline"))
        print(exp)

        out = Path(args.export_dir)
        export_schedule_excels(inst, sch, out / "excel", report=rep)
        export_schedule_pdfs(inst, sch, out / "pdf", report=rep)
        sch.to_json_file(out / "schedule.json")
        print(f"\nArchivos exportados en: {out.resolve()}")


if __name__ == "__main__":
    main()
