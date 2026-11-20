"""Generacion de graficas comparativas de alta resolucion para el informe de evaluacion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def _style_ax(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.35)


def generate_benchmark_charts(results: dict[str, Any], output_dir: str | Path) -> list[Path]:
    """Crea 4 graficas limpias a partir de results/benchmark_results.json."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    # 1. P1: Violaciones duras de LLM directo vs motores formales por instancia
    p1_data = results["llm_direct_p1"]
    instances = [d["instance_id"] for d in p1_data]
    raw_hard = [d["llm_raw_hard"] for d in p1_data]
    fb_hard = [d["llm_fb_hard"] for d in p1_data]

    # Buscar violaciones duras de CP-SAT, AG y BL en el leaderboard
    lb = results["leaderboard"]
    cp_hard = [
        next((r["hard_violations"] for r in lb if r["instance_id"] == iid and "CP-SAT" in r["method"]), 0)
        for iid in instances
    ]
    ag_hard = [
        next((r["hard_violations"] for r in lb if r["instance_id"] == iid and "Genetico" in r["method"]), 0)
        for iid in instances
    ]

    x = np.arange(len(instances))
    w = 0.2
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    b1 = ax.bar(x - 1.5 * w, raw_hard, w, label="LLM directo (sin validador)", color="#E53E3E")
    b2 = ax.bar(x - 0.5 * w, fb_hard, w, label="LLM + feedback validador", color="#DD6B20")
    b3 = ax.bar(x + 0.5 * w, ag_hard, w, label="Algoritmo Genetico (R3)", color="#3182CE")
    b4 = ax.bar(x + 1.5 * w, cp_hard, w, label="CP-SAT (R2)", color="#2F855A")
    for bars in (b1, b2, b3, b4):
        ax.bar_label(bars, padding=2, fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{d['instance_id']}\n({d['sessions']} ses.)" for d in p1_data])
    ax.set_ylabel("Violaciones duras (validador oficial)")
    ax.set_title("P1: Factibilidad por instancia — LLM autonomo vs Motores de optimizacion")
    ax.legend(frameon=False, fontsize=8.5)
    _style_ax(ax)
    fig.tight_layout()
    p_p1 = out / "01_p1_violaciones_duras.png"
    fig.savefig(p_p1, dpi=180)
    plt.close(fig)
    generated.append(p_p1)

    # 2. P3: Comparativa de penalizacion blanda entre metodos validos en I2 e I3
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 4.6))
    for ax, iid in zip(axes, ("I2", "I3")):
        rows = [r for r in lb if r["instance_id"] == iid and r["feasible"]]
        rows.sort(key=lambda r: r["soft_penalty"])
        methods = [
            r["method"]
            .replace(" (Esc. C)", "")
            .replace(" (Escenario C)", "")
            .replace("Busqueda Local (MRV + Hill Climbing, Esc. C)", "Busqueda Local (MRV + HC)")
            .replace("Horario manual (coordinacion)", "Horario manual")
            for r in rows
        ]
        penalties = [r["soft_penalty"] for r in rows]
        colors_bar = [
            "#2F855A" if "CP-SAT" in m else "#3182CE" if "Genetico" in m else "#805AD5" if "Local" in m else "#718096"
            for m in methods
        ]
        bars = ax.barh(methods, penalties, color=colors_bar)
        ax.bar_label(bars, padding=4, fontsize=8.5)
        ax.set_xlim(0, max(penalties) * 1.18 if penalties else 10)
        ax.invert_yaxis()
        ax.set_xlabel("Penalizacion blanda total (menor es mejor)")
        ax.set_title(f"Instancia {iid} — Horarios validos (0 duras)")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="x", linestyle="--", alpha=0.35)
    fig.suptitle("P3: Comparacion de calidad entre metodos (Escenario C)", fontsize=12, fontweight="bold")
    fig.tight_layout()
    p_p3 = out / "02_p3_comparacion_metodos.png"
    fig.savefig(p_p3, dpi=180, bbox_inches="tight")
    plt.close(fig)
    generated.append(p_p3)

    # 3. Escenarios A, B y C sobre I3 (tradeoff estudiantes vs docentes)
    sc_data = results["scenario_comparison_i3"]
    sc_names = [f"Escenario {d['scenario']}" for d in sc_data]
    st_gaps = [d["student_dead_hours"] for d in sc_data]
    tc_gaps = [d["teacher_gaps"] for d in sc_data]
    pref_pct = [d["preferences_pct"] for d in sc_data]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.4))
    xs = np.arange(len(sc_names))
    bw = 0.34
    g1 = ax1.bar(xs - bw / 2, st_gaps, bw, label="Horas muertas estudiantes", color="#3182CE")
    g2 = ax1.bar(xs + bw / 2, tc_gaps, bw, label="Huecos docentes", color="#D69E2E")
    ax1.bar_label(g1, padding=2, fontsize=8.5)
    ax1.bar_label(g2, padding=2, fontsize=8.5)
    ax1.set_xticks(xs)
    ax1.set_xticklabels(sc_names)
    ax1.set_ylabel("Cantidad de huecos / horas muertas")
    ax1.set_title("Huecos estudiantes vs docentes (I3)")
    ax1.legend(frameon=False, fontsize=8.5)
    _style_ax(ax1)

    b_pref = ax2.bar(sc_names, pref_pct, width=0.5, color=["#3182CE", "#2F855A", "#805AD5"])
    ax2.bar_label(b_pref, fmt="%.1f%%", padding=2, fontsize=8.5)
    ax2.set_ylim(0, 115)
    ax2.set_ylabel("% Preferencias docentes cumplidas")
    ax2.set_title("Cumplimiento de preferencias (I3)")
    _style_ax(ax2)

    fig.suptitle("Tradeoff de Escenarios A (Estudiantes), B (Docentes) y C (Equilibrio)", fontsize=11.5, fontweight="bold")
    fig.tight_layout()
    p_sc = out / "03_escenarios_A_B_C.png"
    fig.savefig(p_sc, dpi=180)
    plt.close(fig)
    generated.append(p_sc)

    # 4. P2 (F1 por tipo en R5) + Estabilidad I4
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))
    by_type = results["r5_extraction_p2"]["by_type"]
    types = list(by_type.keys())
    f1s = [by_type[t]["f1"] * 100.0 for t in types]
    bars1 = ax1.barh(types, f1s, color="#2B6CB0")
    ax1.bar_label(bars1, fmt="%.1f%%", padding=3, fontsize=8)
    ax1.set_xlim(0, 115)
    ax1.set_xlabel("F1-Score (%)")
    ax1.set_title("P2: R5 Extractor — F1 por tipo de restriccion")
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)
    ax1.grid(axis="x", linestyle="--", alpha=0.35)

    i4 = results["i4_reprogramming"]
    modes = ["Reprogramacion\n(R2 estabilidad)", "Desde cero\n(sin fijar base)"]
    moved = [i4["reprogram_mode"]["moved_sessions"], i4["from_scratch_mode"]["moved_sessions"]]
    bars2 = ax2.bar(modes, moved, width=0.45, color=["#2F855A", "#E53E3E"])
    ax2.bar_label(bars2, padding=3, fontsize=9)
    ax2.set_ylabel("Sesiones movidas (menor = mayor estabilidad)")
    ax2.set_title("I4: Estabilidad ante cambio de docente")
    _style_ax(ax2)

    fig.tight_layout()
    p_p2i4 = out / "04_p2_f1_y_i4_estabilidad.png"
    fig.savefig(p_p2i4, dpi=180)
    plt.close(fig)
    generated.append(p_p2i4)

    return generated
