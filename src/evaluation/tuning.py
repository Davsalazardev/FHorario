"""Experimento sistematico de ajuste de hiperparametros para R3 (Algoritmo Genetico).

Responde al objetivo #3 del PDF: 'Implementar y ajustar un algoritmo genetico y una busqueda local'.
Compara configuraciones sobre I2 e I3 (3 semillas por configuracion) y guarda resultados en JSON.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from kit.instances import build_instance_i2, build_instance_i3
from kit.validator import validate_schedule
from src.r3_genetic.solver import GAConfig, solve_genetic


TUNING_GRID: list[tuple[str, GAConfig]] = [
    (
        "AG-Base (sin pulido memetico)",
        GAConfig(pop_size=28, crossover_rate=0.85, mutation_rate=0.35, local_polish_prob=0.0, local_polish_steps=0),
    ),
    (
        "AG-PobGrande (pop=32, polish=8)",
        GAConfig(pop_size=32, crossover_rate=0.85, mutation_rate=0.30, local_polish_prob=0.35, local_polish_steps=8),
    ),
    (
        "AG-Balanceado (pop=18, polish=18)",
        GAConfig(pop_size=18, crossover_rate=0.85, mutation_rate=0.40, local_polish_prob=0.55, local_polish_steps=18),
    ),
    (
        "AG-Intensivo (pop=14, polish=28)",
        GAConfig(pop_size=14, crossover_rate=0.80, mutation_rate=0.45, local_polish_prob=0.65, local_polish_steps=28),
    ),
]


def run_ga_tuning(output_path: str | Path, time_limit_sec: float = 2.5, seeds: tuple[int, ...] = (10, 20, 30)) -> dict[str, Any]:
    """Ejecuta el barrido de hiperparametros sobre I2 e I3 y retorna la mejor configuracion."""
    instances = [build_instance_i2(), build_instance_i3()]
    rows: list[dict[str, Any]] = []

    for inst in instances:
        for label, cfg in TUNING_GRID:
            softs: list[int] = []
            hards: list[int] = []
            gens: list[int] = []
            for sd in seeds:
                sch = solve_genetic(inst, scenario="C", time_limit_sec=time_limit_sec, seed=sd, config=cfg)
                rep = validate_schedule(inst, sch, scenario="C")
                softs.append(rep.soft_penalty_total)
                hards.append(rep.hard_violations_total)
                gens.append(int(sch.metadata.get("generations", 0)))
            rows.append(
                {
                    "instance_id": inst.id,
                    "config_name": label,
                    "pop_size": cfg.pop_size,
                    "crossover_rate": cfg.crossover_rate,
                    "mutation_rate": cfg.mutation_rate,
                    "local_polish_prob": cfg.local_polish_prob,
                    "local_polish_steps": cfg.local_polish_steps,
                    "feasible_runs": sum(1 for h in hards if h == 0),
                    "total_runs": len(seeds),
                    "soft_best": min(softs),
                    "soft_mean": round(statistics.mean(softs), 2),
                    "soft_std": round(statistics.stdev(softs), 2) if len(softs) > 1 else 0.0,
                    "avg_generations": round(statistics.mean(gens), 1),
                }
            )

    # Elegir ganador segun media en I3 (instancia completa)
    i3_rows = [r for r in rows if r["instance_id"] == "I3"]
    best_row = min(i3_rows, key=lambda r: (r["total_runs"] - r["feasible_runs"], r["soft_mean"]))

    payload = {
        "time_limit_sec": time_limit_sec,
        "seeds": list(seeds),
        "results": rows,
        "recommended_config": best_row["config_name"],
    }
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
