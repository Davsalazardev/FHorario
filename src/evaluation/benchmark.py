"""Suite de evaluacion comparativa oficial sobre las mismas instancias y con el mismo validador.

Compara:
- Horario manual de la coordinacion (I2 e I3)
- LLM directo, sin y con correcciones del validador (R6)
- Busqueda local MRV + Hill Climbing con reinicios (R4)
- Algoritmo genetico con 10 corridas independientes por instancia (R3)
- CP-SAT extendido en Escenarios A, B y C (R2)
- Escenario de reprogramacion I4 con estabilidad (R2 + R6)
- Evaluacion de extraccion de preferencias P/R/F1 por tipo (R5)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from kit.instances import (
    build_instance_i1,
    build_instance_i2,
    build_instance_i3,
    build_instance_i4,
    build_manual_reference_schedule,
    materialize_all_kit_files,
)
from kit.schema import Instance, Schedule
from kit.validator import compute_stability, validate_schedule
from src.llm_client import LLMConfig
from src.r1_data.excel_io import export_instance_template_excel, export_schedule_excels
from src.r1_data.pdf_export import export_schedule_pdfs
from src.r2_cpsat.solver import solve_cpsat, solve_cpsat_reprogram
from src.r3_genetic.solver import GAConfig, run_ga_multirun
from src.r4_local_search.solver import solve_local_search
from src.r5_llm_extractor.extractor import evaluate_extractor
from src.r6_llm_explainer.explainer import (
    apply_change_and_reprogram,
    explain_validation_report,
    generate_direct_llm_schedule,
    parse_change_request,
)


def run_full_benchmark(
    project_root: str | Path,
    time_limit_sec: float = 4.0,
    ga_runs: int = 10,
    llm_cfg: LLMConfig | None = None,
) -> dict[str, Any]:
    """Ejecuta todos los experimentos del PDF y guarda resultados, Excel, PDF y tabla de posiciones."""
    root = Path(project_root)
    data_dir = root / "data"
    results_dir = root / "results"
    exports_dir = results_dir / "exports"
    results_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)

    # 1. Materializar kit comun y plantillas Excel
    materialize_all_kit_files(data_dir)
    i1 = build_instance_i1(data_dir)
    i2 = build_instance_i2()
    i3 = build_instance_i3()
    i4 = build_instance_i4(i3, teacher_id="DOC_01", blocked_day=2)

    templates_dir = data_dir / "templates"
    export_instance_template_excel(i2, templates_dir / "plantilla_I2_itiid_7q.xlsx")
    export_instance_template_excel(i3, templates_dir / "plantilla_I3_itiid_completo.xlsx")

    manual_schedules: dict[str, Schedule] = {
        "I2": build_manual_reference_schedule(i2, seed=2026),
        "I3": build_manual_reference_schedule(i3, seed=2026),
    }

    instances_to_eval: list[Instance] = [i1, i2, i3]
    leaderboard_rows: list[dict[str, Any]] = []
    scenario_comparison_i3: list[dict[str, Any]] = []
    ga_multirun_stats: dict[str, Any] = {}
    best_schedules: dict[str, Schedule] = {}
    llm_direct_comparison: list[dict[str, Any]] = []

    ga_cfg = GAConfig(pop_size=28, crossover_rate=0.85, mutation_rate=0.35, tournament_k=3, elitism=2)

    for inst in instances_to_eval:
        print(f"[Benchmark] Evaluando {inst.id} ({inst.total_sessions()} sesiones)...")
        candidates: list[tuple[Schedule, dict[str, Any]]] = []

        # a) Manual (solo I2 e I3)
        if inst.id in manual_schedules:
            m_sch = manual_schedules[inst.id]
            m_rep = validate_schedule(inst, m_sch, scenario="C")
            candidates.append((m_sch, {"report": m_rep, "extra": {"source": "manual"}}))

        # b) LLM directo (sin validador) y LLM directo + feedback validador (R6 / P1)
        llm_raw = generate_direct_llm_schedule(
            inst, with_validator_feedback=False, llm_cfg=llm_cfg, seed=77
        )
        rep_llm_raw = validate_schedule(inst, llm_raw, scenario="C")
        candidates.append((llm_raw, {"report": rep_llm_raw, "extra": llm_raw.metadata}))

        llm_fb = generate_direct_llm_schedule(
            inst, with_validator_feedback=True, feedback_rounds=3, llm_cfg=llm_cfg, seed=77
        )
        rep_llm_fb = validate_schedule(inst, llm_fb, scenario="C")
        candidates.append((llm_fb, {"report": rep_llm_fb, "extra": llm_fb.metadata}))

        llm_direct_comparison.append(
            {
                "instance_id": inst.id,
                "sessions": inst.total_sessions(),
                "llm_raw_hard": rep_llm_raw.hard_violations_total,
                "llm_raw_by_type": rep_llm_raw.hard_violations_by_type,
                "llm_raw_soft": rep_llm_raw.soft_penalty_total,
                "llm_fb_hard": rep_llm_fb.hard_violations_total,
                "llm_fb_by_type": rep_llm_fb.hard_violations_by_type,
                "llm_fb_soft": rep_llm_fb.soft_penalty_total,
                "backend": llm_raw.metadata.get("backend", "unknown"),
            }
        )

        # c) Busqueda Local (R4)
        ls_sch = solve_local_search(inst, scenario="C", time_limit_sec=time_limit_sec, seed=42)
        rep_ls = validate_schedule(inst, ls_sch, scenario="C")
        candidates.append((ls_sch, {"report": rep_ls, "extra": ls_sch.metadata}))

        # d) Algoritmo Genetico (R3) - 10 corridas por instancia (mismo time_limit_sec por corrida)
        ga_res = run_ga_multirun(
            inst,
            scenario="C",
            time_limit_sec=time_limit_sec,
            runs=ga_runs,
            base_seed=100,
            config=ga_cfg,
        )
        ga_multirun_stats[inst.id] = ga_res["summary"]
        candidates.append((ga_res["best_schedule"], {"report": ga_res["best_report"], "extra": ga_res["summary"]}))

        # e) CP-SAT (R2) - Escenario C para comparacion justa en tabla de posiciones
        cp_sch = solve_cpsat(inst, scenario="C", time_limit_sec=time_limit_sec, seed=42)
        rep_cp = validate_schedule(inst, cp_sch, scenario="C")
        candidates.append((cp_sch, {"report": rep_cp, "extra": cp_sch.metadata}))
        best_schedules[inst.id] = cp_sch

        # Registrar en tabla de posiciones ordenada por ranking_key()
        candidates.sort(key=lambda pair: pair[1]["report"].ranking_key())
        for rank, (sch, info) in enumerate(candidates, start=1):
            rep = info["report"]
            leaderboard_rows.append(
                {
                    "instance_id": inst.id,
                    "rank": rank,
                    "method": rep.method,
                    "scenario": rep.scenario,
                    "feasible": rep.feasible,
                    "hard_violations": rep.hard_violations_total,
                    "soft_penalty": rep.soft_penalty_total,
                    "student_dead_hours": rep.student_dead_hours,
                    "teacher_gaps": rep.teacher_gaps,
                    "preferences_pct": rep.preferences_pct,
                    "solve_time_sec": rep.solve_time_sec,
                }
            )

    # 2. Comparacion de Escenarios A, B y C sobre I3 con CP-SAT (R2)
    print("[Benchmark] Comparando Escenarios A, B y C sobre I3...")
    for scen in ("A", "B", "C"):
        sch_s = solve_cpsat(i3, scenario=scen, time_limit_sec=max(time_limit_sec, 6.0), seed=42)
        # Evaluar bajo sus propios pesos Y tambien bajo C para ver el tradeoff
        rep_own = validate_schedule(i3, sch_s, scenario=scen)
        rep_c = validate_schedule(i3, sch_s, scenario="C")
        scenario_comparison_i3.append(
            {
                "scenario": scen,
                "feasible": rep_own.feasible,
                "hard_violations": rep_own.hard_violations_total,
                "soft_own_weights": rep_own.soft_penalty_total,
                "soft_eval_C": rep_c.soft_penalty_total,
                "student_dead_hours": rep_own.student_dead_hours,
                "student_excess_units": rep_own.soft_raw_units["student_excessive_day"],
                "course_spread_units": rep_own.soft_raw_units["course_spread"],
                "teacher_gaps": rep_own.teacher_gaps,
                "teacher_balance_units": rep_own.soft_raw_units["teacher_balance"],
                "room_util_units": rep_own.soft_raw_units["room_utilization"],
                "preferences_pct": rep_own.preferences_pct,
                "solve_time_sec": rep_own.solve_time_sec,
            }
        )
        if scen == "C":
            best_schedules["I3"] = sch_s

    # 3. Escenario de cambio I4: Reprogramacion y estabilidad (R2 + R6)
    print("[Benchmark] Evaluando escenario de cambio I4 (reprogramacion)...")
    base_i3_schedule = best_schedules["I3"]
    change_text = "El docente DOC_01 ya no puede los miercoles por comision academica."
    change_req = parse_change_request(change_text, i3, llm_cfg=llm_cfg)
    reprog_result = apply_change_and_reprogram(
        instance=i3,
        base_schedule=base_i3_schedule,
        change=change_req,
        scenario="C",
        time_limit_sec=max(time_limit_sec, 6.0),
        llm_cfg=llm_cfg,
    )
    # Tambien comparar contra resolver I4 desde cero (sin penalizacion de movimiento)
    i4_scratch = solve_cpsat(i4, scenario="C", time_limit_sec=max(time_limit_sec, 6.0), seed=42)
    rep_i4_scratch = validate_schedule(i4, i4_scratch, scenario="C")
    stab_i4_scratch = compute_stability(base_i3_schedule, i4_scratch)

    rep_i4_reprog = reprog_result["validation_report"]
    stab_i4_reprog = reprog_result["stability_report"]

    i4_summary = {
        "change_text": change_text,
        "parsed_change": change_req.model_dump(),
        "reprogram_mode": {
            "feasible": rep_i4_reprog.feasible,
            "hard_violations": rep_i4_reprog.hard_violations_total,
            "soft_penalty": rep_i4_reprog.soft_penalty_total,
            "moved_sessions": stab_i4_reprog.moved_sessions,
            "unchanged_sessions": stab_i4_reprog.unchanged_sessions,
            "total_sessions": stab_i4_reprog.total_sessions,
            "stability_pct": stab_i4_reprog.stability_pct,
            "solve_time_sec": rep_i4_reprog.solve_time_sec,
        },
        "from_scratch_mode": {
            "feasible": rep_i4_scratch.feasible,
            "hard_violations": rep_i4_scratch.hard_violations_total,
            "soft_penalty": rep_i4_scratch.soft_penalty_total,
            "moved_sessions": stab_i4_scratch.moved_sessions,
            "unchanged_sessions": stab_i4_scratch.unchanged_sessions,
            "total_sessions": stab_i4_scratch.total_sessions,
            "stability_pct": stab_i4_scratch.stability_pct,
            "solve_time_sec": rep_i4_scratch.solve_time_sec,
        },
        "explanation": reprog_result["explanation"],
        "explainer_backend": reprog_result["explainer_backend"],
    }

    # 4. Evaluacion de R5 (Extractor de preferencias: Precision, Recall, F1 por tipo)
    print("[Benchmark] Evaluando R5 (Precision, Recall, F1)...")
    r5_eval = evaluate_extractor(llm_cfg=llm_cfg)

    # 5. Exportar horarios ganadores de I3 en Excel y PDF (R1: sabana, docente, grupo, aula)
    print("[Benchmark] Exportando horarios ganadores de I3 a Excel y PDF...")
    rep_i3_best = validate_schedule(i3, best_schedules["I3"], scenario="C")
    excel_paths = export_schedule_excels(i3, best_schedules["I3"], exports_dir / "I3_excel", report=rep_i3_best)
    pdf_paths = export_schedule_pdfs(i3, best_schedules["I3"], exports_dir / "I3_pdf", report=rep_i3_best)
    best_schedules["I3"].to_json_file(exports_dir / "I3_best_schedule.json")
    reprog_result["new_schedule"].to_json_file(exports_dir / "I4_reprogrammed_schedule.json")

    # Explicacion R6 para I3 ganador
    i3_explanation, i3_exp_backend = explain_validation_report(i3, rep_i3_best, llm_cfg=llm_cfg)

    # Guardar CSV de tabla de posiciones y JSON consolidado
    lb_df = pd.DataFrame(leaderboard_rows)
    lb_df.to_csv(results_dir / "leaderboard.csv", index=False)

    full_results = {
        "config": {"time_limit_sec": time_limit_sec, "ga_runs": ga_runs},
        "leaderboard": leaderboard_rows,
        "llm_direct_p1": llm_direct_comparison,
        "ga_multirun_stats": ga_multirun_stats,
        "scenario_comparison_i3": scenario_comparison_i3,
        "i4_reprogramming": i4_summary,
        "r5_extraction_p2": {
            "backend": r5_eval["backend"],
            "num_examples": r5_eval["num_examples"],
            "exact_match_rate": r5_eval["exact_match_rate"],
            "overall": r5_eval["overall"],
            "by_type": r5_eval["by_type"],
        },
        "i3_explanation_p4": {"backend": i3_exp_backend, "text": i3_explanation},
        "exported_files": {
            "excel": {k: str(v) for k, v in excel_paths.items()},
            "pdf": {k: str(v) for k, v in pdf_paths.items()},
        },
    }

    (results_dir / "benchmark_results.json").write_text(
        json.dumps(full_results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return full_results
