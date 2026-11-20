"""R4: Busqueda local y heuristica.

Implementa:
1. Backtracking con heuristica de menor dominio (MRV - Minimum Remaining Values)
   y desempate por grado (Degree) + seleccion guiada de valores.
2. Reparacion de conflictos (Min-Conflicts) para garantizar factibilidad o reparar horarios.
3. Hill Climbing con reinicios aleatorios (vecindarios de reubicacion e intercambio 2-opt).
"""

from __future__ import annotations

import random
import time

from kit.schema import Instance, Schedule
from src.fast_eval import FastEvaluator


def _build_occupancy(
    ev: FastEvaluator, genes: list[tuple[int, int, int]]
) -> tuple[dict[tuple[int, int, int], int], dict[tuple[int, int, int], int], dict[tuple[int, int, int], int]]:
    t_occ: dict[tuple[int, int, int], int] = {}
    g_occ: dict[tuple[int, int, int], int] = {}
    r_occ: dict[tuple[int, int, int], int] = {}
    for spec, (d, s, ri) in zip(ev.sessions, genes):
        tk = (spec.teacher_idx, d, s)
        gk = (spec.group_idx, d, s)
        rk = (ri, d, s)
        t_occ[tk] = t_occ.get(tk, 0) + 1
        g_occ[gk] = g_occ.get(gk, 0) + 1
        r_occ[rk] = r_occ.get(rk, 0) + 1
    return t_occ, g_occ, r_occ


def mrv_backtracking_construct(
    ev: FastEvaluator,
    rng: random.Random,
    max_nodes: int = 3000,
    randomize: bool = True,
) -> list[tuple[int, int, int]]:
    """Constructivo por Backtracking con heuristica MRV (menor dominio disponible).

    Si se agota el presupuesto de nodos, completa greedy y delega a reparacion.
    """
    n = ev.num_sessions
    assigned: list[tuple[int, int, int] | None] = [None] * n
    unassigned = set(range(n))

    t_used: set[tuple[int, int, int]] = set()
    g_used: set[tuple[int, int, int]] = set()
    r_used: set[tuple[int, int, int]] = set()
    course_days: list[list[int]] = [[0] * ev.num_days for _ in range(ev.num_courses)]
    group_days: list[list[int]] = [[0] * ev.num_days for _ in range(ev.num_groups)]

    # Grado estatico por sesion (cuantas sesiones comparten docente o grupo)
    t_deg = [0] * ev.num_teachers
    g_deg = [0] * ev.num_groups
    for sp in ev.sessions:
        t_deg[sp.teacher_idx] += 1
        g_deg[sp.group_idx] += 1
    session_deg = [t_deg[sp.teacher_idx] + g_deg[sp.group_idx] for sp in ev.sessions]

    nodes = 0

    def valid_choices(idx: int) -> list[tuple[int, int, int]]:
        sp = ev.sessions[idx]
        ti, gi = sp.teacher_idx, sp.group_idx
        return [
            (d, s, ri)
            for (d, s, ri) in sp.valid_domain
            if (ti, d, s) not in t_used and (gi, d, s) not in g_used and (ri, d, s) not in r_used
        ]

    def backtrack() -> bool:
        nonlocal nodes
        if not unassigned:
            return True
        if nodes >= max_nodes:
            return False
        nodes += 1

        # Seleccion MRV: sesion con menor numero de opciones libres (desempate por grado)
        best_idx = -1
        best_choices: list[tuple[int, int, int]] = []
        best_key = (10**9, 0, 0.0)

        for idx in unassigned:
            ch = valid_choices(idx)
            if not ch:
                return False  # Poda inmediata (forward checking)
            key = (len(ch), -session_deg[idx], rng.random() if randomize else 0.0)
            if key < best_key:
                best_key = key
                best_idx = idx
                best_choices = ch
                if len(ch) == 1:
                    break

        sp = ev.sessions[best_idx]
        # Ordenar valores candidatos (favorecer dias sin esta materia, menor carga del grupo y aula adecuada)
        c_days = course_days[sp.course_idx]
        g_days = group_days[sp.group_idx]
        r_pen = ev.room_pen[sp.course_idx]

        def val_score(v: tuple[int, int, int]) -> tuple[int, int, int, float]:
            d, s, ri = v
            return (
                c_days[d],
                r_pen[ri],
                g_days[d],
                rng.random() if randomize else float(s),
            )

        best_choices.sort(key=val_score)
        # Limitar ramificacion por nodo para no estancarse en dominios grandes
        branch_limit = min(len(best_choices), 12)

        unassigned.remove(best_idx)
        for d, s, ri in best_choices[:branch_limit]:
            assigned[best_idx] = (d, s, ri)
            t_used.add((sp.teacher_idx, d, s))
            g_used.add((sp.group_idx, d, s))
            r_used.add((ri, d, s))
            c_days[d] += 1
            g_days[d] += 1

            if backtrack():
                return True

            c_days[d] -= 1
            g_days[d] -= 1
            t_used.remove((sp.teacher_idx, d, s))
            g_used.remove((sp.group_idx, d, s))
            r_used.remove((ri, d, s))
            assigned[best_idx] = None

        unassigned.add(best_idx)
        return False

    solved = backtrack()
    if solved:
        return [g for g in assigned if g is not None]

    # Respaldo voraz para cualquier sesion pendiente
    genes: list[tuple[int, int, int]] = []
    for idx in range(n):
        if assigned[idx] is not None:
            genes.append(assigned[idx])  # type: ignore[arg-type]
        else:
            sp = ev.sessions[idx]
            ch = valid_choices(idx)
            pick = rng.choice(ch) if ch else rng.choice(sp.valid_domain)
            genes.append(pick)
            t_used.add((sp.teacher_idx, pick[0], pick[1]))
            g_used.add((sp.group_idx, pick[0], pick[1]))
            r_used.add((pick[2], pick[0], pick[1]))
    return genes


def repair_conflicts(
    ev: FastEvaluator,
    genes: list[tuple[int, int, int]],
    rng: random.Random,
    max_steps: int = 800,
) -> tuple[list[tuple[int, int, int]], int, int]:
    """Reparacion Min-Conflicts orientada a eliminar violaciones duras rapido."""
    cur = list(genes)
    t_occ, g_occ, r_occ = _build_occupancy(ev, cur)

    for _ in range(max_steps):
        # Encontrar sesiones conflictivas
        bad = [
            i
            for i, (sp, (d, s, ri)) in enumerate(zip(ev.sessions, cur))
            if t_occ.get((sp.teacher_idx, d, s), 0) > 1
            or g_occ.get((sp.group_idx, d, s), 0) > 1
            or r_occ.get((ri, d, s), 0) > 1
        ]
        if not bad:
            break

        idx = rng.choice(bad)
        sp = ev.sessions[idx]
        old_d, old_s, old_ri = cur[idx]

        # Quitar sesion temporalmente
        t_occ[(sp.teacher_idx, old_d, old_s)] -= 1
        g_occ[(sp.group_idx, old_d, old_s)] -= 1
        r_occ[(old_ri, old_d, old_s)] -= 1

        # Buscar mejor valor en dominio con minimos conflictos
        dom = sp.valid_domain
        sample = dom if len(dom) <= 45 else rng.sample(dom, 45)
        best_val = (old_d, old_s, old_ri)
        best_conf = 10**9
        for cand in sample:
            cd, cs, cri = cand
            conf = (
                t_occ.get((sp.teacher_idx, cd, cs), 0)
                + g_occ.get((sp.group_idx, cd, cs), 0)
                + r_occ.get((cri, cd, cs), 0)
            )
            if conf < best_conf or (conf == best_conf and rng.random() < 0.2):
                best_conf = conf
                best_val = cand
                if conf == 0 and ev.room_pen[sp.course_idx][cri] == 0:
                    break

        nd, ns, nri = best_val
        cur[idx] = best_val
        t_occ[(sp.teacher_idx, nd, ns)] = t_occ.get((sp.teacher_idx, nd, ns), 0) + 1
        g_occ[(sp.group_idx, nd, ns)] = g_occ.get((sp.group_idx, nd, ns), 0) + 1
        r_occ[(nri, nd, ns)] = r_occ.get((nri, nd, ns), 0) + 1

    hard, soft = ev.evaluate(cur)
    return cur, hard, soft


def hill_climb_improve(
    ev: FastEvaluator,
    genes: list[tuple[int, int, int]],
    rng: random.Random,
    deadline: float,
    max_no_improve: int = 250,
) -> tuple[list[tuple[int, int, int]], int, int, int]:
    """Hill Climbing con movimientos de reubicacion e intercambio (swap) de 2 sesiones."""
    cur = list(genes)
    cur_h, cur_s = ev.evaluate(cur)
    if cur_h > 0:
        cur, cur_h, cur_s = repair_conflicts(ev, cur, rng, max_steps=600)

    t_occ, g_occ, r_occ = _build_occupancy(ev, cur)
    steps = 0
    no_improve = 0
    n = ev.num_sessions

    # Agrupar sesiones por grupo para swaps que preservan estructura
    by_group: dict[int, list[int]] = {}
    for i, sp in enumerate(ev.sessions):
        by_group.setdefault(sp.group_idx, []).append(i)
    group_lists = [lst for lst in by_group.values() if len(lst) >= 2]

    while time.perf_counter() < deadline and no_improve < max_no_improve:
        steps += 1
        improved = False

        if rng.random() < 0.72 or not group_lists:
            # Vecindario 1: Reubicacion de una sesion a un hueco libre (0 conflictos)
            idx = rng.randrange(n)
            sp = ev.sessions[idx]
            old_d, old_s, old_ri = cur[idx]

            # Retirar temporalmente
            t_occ[(sp.teacher_idx, old_d, old_s)] -= 1
            g_occ[(sp.group_idx, old_d, old_s)] -= 1
            r_occ[(old_ri, old_d, old_s)] -= 1

            # Filtrar candidatos libres de conflictos
            free_cands = [
                cand
                for cand in sp.valid_domain
                if cand != (old_d, old_s, old_ri)
                and t_occ.get((sp.teacher_idx, cand[0], cand[1]), 0) == 0
                and g_occ.get((sp.group_idx, cand[0], cand[1]), 0) == 0
                and r_occ.get((cand[2], cand[0], cand[1]), 0) == 0
            ]
            if free_cands:
                sample = free_cands if len(free_cands) <= 18 else rng.sample(free_cands, 18)
                best_cand = None
                best_score = (cur_h, cur_s)
                for cand in sample:
                    cur[idx] = cand
                    h, s = ev.evaluate(cur)
                    if (h, s) < best_score:
                        best_score = (h, s)
                        best_cand = cand
                if best_cand is not None:
                    cur[idx] = best_cand
                    cur_h, cur_s = best_score
                    nd, ns, nri = best_cand
                    t_occ[(sp.teacher_idx, nd, ns)] = t_occ.get((sp.teacher_idx, nd, ns), 0) + 1
                    g_occ[(sp.group_idx, nd, ns)] = g_occ.get((sp.group_idx, nd, ns), 0) + 1
                    r_occ[(nri, nd, ns)] = r_occ.get((nri, nd, ns), 0) + 1
                    improved = True
                else:
                    cur[idx] = (old_d, old_s, old_ri)
                    t_occ[(sp.teacher_idx, old_d, old_s)] += 1
                    g_occ[(sp.group_idx, old_d, old_s)] += 1
                    r_occ[(old_ri, old_d, old_s)] += 1
            else:
                t_occ[(sp.teacher_idx, old_d, old_s)] += 1
                g_occ[(sp.group_idx, old_d, old_s)] += 1
                r_occ[(old_ri, old_d, old_s)] += 1
        else:
            # Vecindario 2: Intercambio (swap) de dia/franja entre 2 sesiones del mismo grupo
            glst = rng.choice(group_lists)
            i1, i2 = rng.sample(glst, 2)
            sp1, sp2 = ev.sessions[i1], ev.sessions[i2]
            d1, s1, r1 = cur[i1]
            d2, s2, r2 = cur[i2]
            if (d1, s1) != (d2, s2):
                # Buscar aulas compatibles para el swap
                cand1 = next(
                    (
                        (d2, s2, ri)
                        for ri in sp1.allowed_rooms
                        if (d2, s2, ri) in sp1.valid_domain
                        and (ri == r2 or r_occ.get((ri, d2, s2), 0) == 0)
                    ),
                    None,
                )
                cand2 = next(
                    (
                        (d1, s1, ri)
                        for ri in sp2.allowed_rooms
                        if (d1, s1, ri) in sp2.valid_domain
                        and (ri == r1 or r_occ.get((ri, d1, s1), 0) == 0)
                    ),
                    None,
                )
                if cand1 is not None and cand2 is not None:
                    # Comprobar disponibilidad de docentes sin choque
                    t1_ok = (sp1.teacher_idx == sp2.teacher_idx) or t_occ.get((sp1.teacher_idx, d2, s2), 0) == 0
                    t2_ok = (sp1.teacher_idx == sp2.teacher_idx) or t_occ.get((sp2.teacher_idx, d1, s1), 0) == 0
                    if t1_ok and t2_ok:
                        cur[i1], cur[i2] = cand1, cand2
                        h, s = ev.evaluate(cur)
                        if (h, s) < (cur_h, cur_s):
                            cur_h, cur_s = h, s
                            t_occ, g_occ, r_occ = _build_occupancy(ev, cur)
                            improved = True
                        else:
                            cur[i1], cur[i2] = (d1, s1, r1), (d2, s2, r2)

        if improved:
            no_improve = 0
        else:
            no_improve += 1

    return cur, cur_h, cur_s, steps


def solve_local_search(
    instance: Instance,
    scenario: str = "C",
    time_limit_sec: float = 10.0,
    seed: int = 42,
) -> Schedule:
    """Ejecuta Busqueda Local: Backtracking MRV + Reparacion + Hill Climbing con reinicios."""
    t0 = time.perf_counter()
    deadline = t0 + time_limit_sec
    rng = random.Random(seed)
    ev = FastEvaluator(instance, scenario=scenario)

    best_genes: list[tuple[int, int, int]] | None = None
    best_score = (10**9, 10**9)
    restarts = 0
    total_steps = 0

    while time.perf_counter() < deadline:
        restarts += 1
        init = mrv_backtracking_construct(ev, rng, max_nodes=2500, randomize=(restarts > 1))
        init, h, s = repair_conflicts(ev, init, rng, max_steps=600)
        rem = deadline - time.perf_counter()
        if rem <= 0:
            cand_genes, cand_h, cand_s, st = init, h, s, 0
        else:
            # Presupuesto por reinicio para explorar multiples cuencas locales
            restart_slice = min(rem, max(0.8, time_limit_sec / 4.0))
            cand_genes, cand_h, cand_s, st = hill_climb_improve(
                ev,
                init,
                rng,
                deadline=min(deadline, time.perf_counter() + restart_slice),
                max_no_improve=220,
            )
        total_steps += st
        if (cand_h, cand_s) < best_score:
            best_score = (cand_h, cand_s)
            best_genes = list(cand_genes)

    elapsed = time.perf_counter() - t0
    if best_genes is None:
        best_genes = mrv_backtracking_construct(ev, rng, max_nodes=1500, randomize=False)

    return ev.to_schedule(
        best_genes,
        method=f"Busqueda Local (MRV + Hill Climbing, Esc. {scenario.upper()})",
        solve_time_sec=elapsed,
        seed=seed,
        metadata={
            "restarts": restarts,
            "hill_climb_steps": total_steps,
            "fast_hard": best_score[0],
            "fast_soft": best_score[1],
        },
    )
