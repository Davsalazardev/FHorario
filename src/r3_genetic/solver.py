"""R3: Algoritmo Genetico (AG) para generacion de horarios ITIID.

Caracteristicas:
- Representacion por cromosoma de sesiones [(day, slot, room_idx), ...].
- Inicializacion hibrida (MRV aleatorizado + reparacion + diversidad aleatoria).
- Seleccion por torneo + elitismo.
- Cruce consciente de grupos (hereda bloques por grupo para preservar estructura) + reparacion dirigida.
- Mutacion guiada (reubicacion a franjas libres y swaps intra-grupo).
- Soporte oficial de 10 corridas independientes por instancia (requisito del PDF).
"""

from __future__ import annotations

import random
import statistics
import time
from dataclasses import dataclass
from typing import Any

from kit.schema import Instance, Schedule
from kit.validator import ValidationReport, validate_schedule
from src.fast_eval import FastEvaluator
from src.r4_local_search.solver import mrv_backtracking_construct, repair_conflicts


@dataclass
class GAConfig:
    pop_size: int = 32
    crossover_rate: float = 0.85
    mutation_rate: float = 0.35
    tournament_k: int = 3
    elitism: int = 2
    repair_steps: int = 120
    local_polish_prob: float = 0.25


def _fitness_key(hard: int, soft: int) -> int:
    """Costo a minimizar: violaciones duras dominan lexicograficamente."""
    return hard * 100_000 + soft


def _group_crossover(
    ev: FastEvaluator,
    p1: list[tuple[int, int, int]],
    p2: list[tuple[int, int, int]],
    rng: random.Random,
) -> list[tuple[int, int, int]]:
    """Cruce por bloques de grupo: hereda asignaciones completas de cada grupo de P1 o P2."""
    group_choice = {gi: rng.random() < 0.5 for gi in range(ev.num_groups)}
    if all(group_choice.values()) or not any(group_choice.values()):
        # Garantizar mezcla real cuando hay pocos grupos
        child = [p1[i] if rng.random() < 0.5 else p2[i] for i in range(ev.num_sessions)]
    else:
        child = [
            p1[i] if group_choice[ev.sessions[i].group_idx] else p2[i]
            for i in range(ev.num_sessions)
        ]
    return child


def _guided_mutation(
    ev: FastEvaluator,
    genes: list[tuple[int, int, int]],
    rng: random.Random,
) -> list[tuple[int, int, int]]:
    """Mutacion guiada: mueve 1-2 sesiones a un slot valido con mejora o perturbacion controlada."""
    child = list(genes)
    n = ev.num_sessions

    # 1. Si hay conflictos, mutar prioritariamente una sesion en conflicto
    bad = ev.conflicting_sessions(child)
    if bad:
        idx = rng.choice(bad)
        sp = ev.sessions[idx]
        child[idx] = rng.choice(sp.valid_domain)
        return child

    # 2. Si es factible, intentar mutacion de mejora o exploracion en 1 o 2 genes
    t_used = {(ev.sessions[i].teacher_idx, d, s) for i, (d, s, _) in enumerate(child)}
    g_used = {(ev.sessions[i].group_idx, d, s) for i, (d, s, _) in enumerate(child)}
    r_used = {(ri, d, s) for (d, s, ri) in child}

    n_muts = 1 if rng.random() < 0.7 else 2
    for _ in range(n_muts):
        idx = rng.randrange(n)
        sp = ev.sessions[idx]
        old = child[idx]
        t_used.discard((sp.teacher_idx, old[0], old[1]))
        g_used.discard((sp.group_idx, old[0], old[1]))
        r_used.discard((old[2], old[0], old[1]))

        free = [
            c
            for c in sp.valid_domain
            if c != old
            and (sp.teacher_idx, c[0], c[1]) not in t_used
            and (sp.group_idx, c[0], c[1]) not in g_used
            and (c[2], c[0], c[1]) not in r_used
        ]
        if free:
            # Elegir el mejor entre una muestra pequena (mutacion memetica ligera)
            sample = free if len(free) <= 8 else rng.sample(free, 8)
            best_c = old
            best_sc = ev.evaluate(child)
            for cand in sample:
                child[idx] = cand
                sc = ev.evaluate(child)
                if sc < best_sc:
                    best_sc = sc
                    best_c = cand
            # A veces aceptar uno aleatorio libre para mantener diversidad genetica
            chosen = best_c if best_c != old else rng.choice(sample)
            child[idx] = chosen
        else:
            child[idx] = old

        cur = child[idx]
        t_used.add((sp.teacher_idx, cur[0], cur[1]))
        g_used.add((sp.group_idx, cur[0], cur[1]))
        r_used.add((cur[2], cur[0], cur[1]))

    return child


def solve_genetic(
    instance: Instance,
    scenario: str = "C",
    time_limit_sec: float = 10.0,
    seed: int = 42,
    config: GAConfig | None = None,
) -> Schedule:
    """Ejecuta una corrida del Algoritmo Genetico dentro del limite de tiempo dado."""
    cfg = config or GAConfig()
    t0 = time.perf_counter()
    deadline = t0 + time_limit_sec
    rng = random.Random(seed)
    ev = FastEvaluator(instance, scenario=scenario)

    # 1. Poblacion inicial diversa
    population: list[tuple[int, int, int, list[tuple[int, int, int]]]] = []
    # Cada individuo: (cost, hard, soft, genes)
    for k in range(cfg.pop_size):
        if time.perf_counter() >= deadline and population:
            break
        if k < int(cfg.pop_size * 0.7):
            genes = mrv_backtracking_construct(ev, rng, max_nodes=800, randomize=True)
            genes, h, s = repair_conflicts(ev, genes, rng, max_steps=cfg.repair_steps)
        else:
            genes = [rng.choice(sp.valid_domain) for sp in ev.sessions]
            genes, h, s = repair_conflicts(ev, genes, rng, max_steps=cfg.repair_steps * 2)
        population.append((_fitness_key(h, s), h, s, genes))

    population.sort(key=lambda x: x[0])
    best = population[0]
    history: list[dict[str, Any]] = [
        {"gen": 0, "best_hard": best[1], "best_soft": best[2], "t": round(time.perf_counter() - t0, 3)}
    ]

    gen = 0
    while time.perf_counter() < deadline:
        gen += 1
        new_pop: list[tuple[int, int, int, list[tuple[int, int, int]]]] = population[: cfg.elitism]

        while len(new_pop) < cfg.pop_size and time.perf_counter() < deadline:
            # Seleccion por torneo
            k_t = min(cfg.tournament_k, len(population))
            p1 = min(rng.sample(population, k_t), key=lambda x: x[0])[3]
            p2 = min(rng.sample(population, k_t), key=lambda x: x[0])[3]

            if rng.random() < cfg.crossover_rate:
                child = _group_crossover(ev, p1, p2, rng)
            else:
                child = list(p1)

            # Reparar si el cruce introdujo choques
            h, s = ev.evaluate(child)
            if h > 0:
                child, h, s = repair_conflicts(ev, child, rng, max_steps=cfg.repair_steps)

            # Mutacion guiada
            if rng.random() < cfg.mutation_rate:
                child = _guided_mutation(ev, child, rng)
                h, s = ev.evaluate(child)
                if h > 0:
                    child, h, s = repair_conflicts(ev, child, rng, max_steps=cfg.repair_steps)

            new_pop.append((_fitness_key(h, s), h, s, child))

        new_pop.sort(key=lambda x: x[0])
        population = new_pop
        if population[0][0] < best[0]:
            best = population[0]
        if gen % 5 == 0 or population[0][0] == best[0]:
            history.append(
                {
                    "gen": gen,
                    "best_hard": best[1],
                    "best_soft": best[2],
                    "t": round(time.perf_counter() - t0, 3),
                }
            )

    elapsed = time.perf_counter() - t0
    return ev.to_schedule(
        best[3],
        method=f"Algoritmo Genetico (Esc. {scenario.upper()})",
        solve_time_sec=elapsed,
        seed=seed,
        metadata={
            "generations": gen,
            "pop_size": cfg.pop_size,
            "crossover_rate": cfg.crossover_rate,
            "mutation_rate": cfg.mutation_rate,
            "convergence_history": history[-30:],
        },
    )


def run_ga_multirun(
    instance: Instance,
    scenario: str = "C",
    time_limit_sec: float = 5.0,
    runs: int = 10,
    base_seed: int = 100,
    config: GAConfig | None = None,
) -> dict[str, Any]:
    """Ejecuta las 10 corridas independientes requeridas por instancia y valida todas con el arbitro oficial."""
    schedules: list[Schedule] = []
    reports: list[ValidationReport] = []

    for r in range(runs):
        seed = base_seed + r
        sch = solve_genetic(
            instance,
            scenario=scenario,
            time_limit_sec=time_limit_sec,
            seed=seed,
            config=config,
        )
        rep = validate_schedule(instance, sch, scenario=scenario)
        schedules.append(sch)
        reports.append(rep)

    ranked_idx = sorted(range(runs), key=lambda i: reports[i].ranking_key())
    best_i = ranked_idx[0]
    softs = [rep.soft_penalty_total for rep in reports]
    hards = [rep.hard_violations_total for rep in reports]
    times = [rep.solve_time_sec for rep in reports]
    feasible_count = sum(1 for rep in reports if rep.feasible)

    return {
        "runs": runs,
        "best_schedule": schedules[best_i],
        "best_report": reports[best_i],
        "all_reports": reports,
        "summary": {
            "feasible_runs": feasible_count,
            "feasible_rate_pct": round(100.0 * feasible_count / max(1, runs), 1),
            "hard_min": min(hards),
            "hard_mean": round(statistics.mean(hards), 2),
            "soft_best": reports[best_i].soft_penalty_total,
            "soft_mean": round(statistics.mean(softs), 2),
            "soft_median": round(statistics.median(softs), 2),
            "soft_std": round(statistics.stdev(softs), 2) if runs > 1 else 0.0,
            "time_mean_sec": round(statistics.mean(times), 3),
        },
    }
