"""Evaluacion real con LLM local en Ollama (ej. qwen2.5:7b) para P1, P2 y P4.

Ejecuta llamadas reales al modelo local de Ollama y actualiza
results/benchmark_results.json y results/leaderboard.csv manteniendo intactos
los resultados de los motores de optimizacion (CP-SAT, AG, Busqueda Local).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from kit.instances import build_instance_i1, build_instance_i2, build_instance_i3, build_instance_i4
from kit.schema import Schedule
from kit.validator import compute_stability, validate_schedule
from src.llm_client import LLMConfig
from src.r5_llm_extractor.extractor import evaluate_extractor
from src.r6_llm_explainer.explainer import (
    explain_validation_report,
    generate_direct_llm_schedule,
    parse_change_request,
)


def run_ollama_eval(project_root: Path, model_name: str = "qwen2.5:7b") -> None:
    results_dir = project_root / "results"
    bm_path = results_dir / "benchmark_results.json"
    bm = json.loads(bm_path.read_text(encoding="utf-8"))

    cfg = LLMConfig(provider="ollama", model=model_name, timeout_sec=120.0, temperature=0.1)

    # 1. P2: R5 Extractor sobre las 25 preferencias anotadas con Ollama real
    print(f"[Ollama:{model_name}] Evaluando P2 (R5 — 25 preferencias en espanol)...")
    r5_real = evaluate_extractor(llm_cfg=cfg)
    bm["r5_extraction_p2_offline_baseline"] = bm.get("r5_extraction_p2")
    bm["r5_extraction_p2"] = {
        "backend": r5_real["backend"],
        "num_examples": r5_real["num_examples"],
        "exact_match_rate": r5_real["exact_match_rate"],
        "overall": r5_real["overall"],
        "by_type": r5_real["by_type"],
    }
    print(
        f"  -> P2 ({r5_real['backend']}): P={r5_real['overall']['precision']:.3f}, "
        f"R={r5_real['overall']['recall']:.3f}, F1_micro={r5_real['overall']['f1_micro']:.3f}, "
        f"Exact={r5_real['exact_match_rate']*100:.1f}%"
    )

    # 2. P1: Horario directo con Ollama real (sin y con 1 ronda de feedback del validador)
    instances = [
        build_instance_i1(project_root / "data"),
        build_instance_i2(),
        build_instance_i3(),
    ]
    new_p1 = []
    # Quitar filas antiguas de LLM directo del leaderboard y reinsertar las reales
    lb_clean = [r for r in bm["leaderboard"] if not r["method"].startswith("LLM directo")]

    for inst in instances:
        print(f"[Ollama:{model_name}] Evaluando P1 (horario directo) en {inst.id} ({inst.total_sessions()} ses.)...")
        raw_sch = generate_direct_llm_schedule(
            inst, with_validator_feedback=False, llm_cfg=cfg
        )
        rep_raw = validate_schedule(inst, raw_sch, scenario="C")
        print(
            f"  -> {inst.id} sin validador: {rep_raw.hard_violations_total} duras "
            f"(asignaciones devueltas: {len(raw_sch.assignments)}/{inst.total_sessions()}, t={rep_raw.solve_time_sec}s)"
        )

        fb_sch = generate_direct_llm_schedule(
            inst, with_validator_feedback=True, feedback_rounds=1, llm_cfg=cfg
        )
        rep_fb = validate_schedule(inst, fb_sch, scenario="C")
        print(
            f"  -> {inst.id} + feedback validador: {rep_fb.hard_violations_total} duras "
            f"(asignaciones devueltas: {len(fb_sch.assignments)}/{inst.total_sessions()}, t={rep_fb.solve_time_sec}s)"
        )

        new_p1.append(
            {
                "instance_id": inst.id,
                "sessions": inst.total_sessions(),
                "llm_raw_returned": len(raw_sch.assignments),
                "llm_raw_hard": rep_raw.hard_violations_total,
                "llm_raw_by_type": rep_raw.hard_violations_by_type,
                "llm_raw_soft": rep_raw.soft_penalty_total,
                "llm_raw_time_sec": rep_raw.solve_time_sec,
                "llm_fb_returned": len(fb_sch.assignments),
                "llm_fb_hard": rep_fb.hard_violations_total,
                "llm_fb_by_type": rep_fb.hard_violations_by_type,
                "llm_fb_soft": rep_fb.soft_penalty_total,
                "llm_fb_time_sec": rep_fb.solve_time_sec,
                "backend": raw_sch.metadata.get("backend", f"ollama:{model_name}"),
            }
        )

        for rep in (rep_raw, rep_fb):
            lb_clean.append(
                {
                    "instance_id": inst.id,
                    "rank": 0,
                    "method": f"{rep.method} [{model_name}]",
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

    # Reordenar ranks por instancia
    final_lb = []
    for iid in ("I1", "I2", "I3"):
        sub = [r for r in lb_clean if r["instance_id"] == iid]
        sub.sort(key=lambda r: (r["hard_violations"], r["soft_penalty"], r["solve_time_sec"]))
        for idx, row in enumerate(sub, start=1):
            row["rank"] = idx
            final_lb.append(row)

    bm["leaderboard"] = final_lb
    bm["llm_direct_p1"] = new_p1

    # 3. P4: Explicaciones reales en espanol con Ollama para I3 y I4 + parseo real de cambio I4
    print(f"[Ollama:{model_name}] Generando explicaciones reales R6 para I3 e I4...")
    i3 = build_instance_i3()
    i4 = build_instance_i4(i3, teacher_id="DOC_01", blocked_day=2)
    sch_i3 = Schedule.from_json_file(results_dir / "exports" / "I3_best_schedule.json")
    sch_i4 = Schedule.from_json_file(results_dir / "exports" / "I4_reprogrammed_schedule.json")
    rep_i3 = validate_schedule(i3, sch_i3, scenario="C")
    rep_i4 = validate_schedule(i4, sch_i4, scenario="C")
    stab_i4 = compute_stability(sch_i3, sch_i4)

    ch_real = parse_change_request(bm["i4_reprogramming"]["change_text"], i3, llm_cfg=cfg)
    bm["i4_reprogramming"]["parsed_change"] = ch_real.model_dump()

    exp_i3, bk_i3 = explain_validation_report(i3, rep_i3, llm_cfg=cfg)
    exp_i4, bk_i4 = explain_validation_report(i4, rep_i4, stability=stab_i4, llm_cfg=cfg)
    bm["i3_explanation_p4"] = {"backend": bk_i3, "text": exp_i3}
    bm["i4_reprogramming"]["explanation"] = exp_i4
    bm["i4_reprogramming"]["explainer_backend"] = bk_i4

    bm_path.write_text(json.dumps(bm, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(final_lb).to_csv(results_dir / "leaderboard.csv", index=False)
    print(f"[Ollama:{model_name}] Evaluacion real guardada en {bm_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen2.5:7b")
    args = parser.parse_args()
    run_ollama_eval(Path(__file__).resolve().parents[2], model_name=args.model)
