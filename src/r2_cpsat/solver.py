"""R2: Extension del modelo CP-SAT base del kit comun.

Agrega:
- Penalizaciones blandas exactas (linealizadas) identicas al validador oficial.
- Escenarios A (estudiantes), B (docentes) y C (equilibrio).
- Modo de reprogramacion (I4): fija lo no afectado y minimiza sesiones movidas.
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict

from ortools.sat.python import cp_model

from kit.cpsat_base import BaseCPSATContext, build_base_cpsat_model, extract_schedule_from_solver
from kit.schema import Instance, Schedule, get_scenario_weights


def _add_gap_variables(
    model: cp_model.CpModel,
    entity_occ: dict[tuple[str, int, int], cp_model.IntVar],
    entity_ids: list[str],
    num_days: int,
    valid_slots: list[int],
    prefix: str,
) -> dict[tuple[str, int, int], cp_model.IntVar]:
    """Crea variables BoolVar de huecos exactos (gap=1 si hay clase antes y despues, y slot libre).

    Como los huecos siempre tienen peso positivo en minimizacion,
    gap >= pref_any + suff_any - occ - 1 es exacto.
    """
    gaps: dict[tuple[str, int, int], cp_model.IntVar] = {}
    k = len(valid_slots)
    if k < 3:
        return gaps

    for eid in entity_ids:
        for d in range(num_days):
            occs = [entity_occ[(eid, d, s)] for s in valid_slots]

            pref: list[cp_model.IntVar] = [model.NewConstant(0)]
            for i in range(1, k):
                p = model.NewBoolVar(f"{prefix}_pref_{eid}_{d}_{i}")
                if i == 1:
                    model.Add(p == occs[0])
                else:
                    model.AddMaxEquality(p, [pref[i - 1], occs[i - 1]])
                pref.append(p)

            suff: list[cp_model.IntVar] = [model.NewConstant(0)] * k
            for i in range(k - 2, -1, -1):
                sf = model.NewBoolVar(f"{prefix}_suff_{eid}_{d}_{i}")
                if i == k - 2:
                    model.Add(sf == occs[k - 1])
                else:
                    model.AddMaxEquality(sf, [suff[i + 1], occs[i + 1]])
                suff[i] = sf

            for i in range(1, k - 1):
                s = valid_slots[i]
                g = model.NewBoolVar(f"{prefix}_gap_{eid}_{d}_{s}")
                model.Add(g >= pref[i] + suff[i] - occs[i] - 1)
                gaps[(eid, d, s)] = g

    return gaps


def _add_soft_objective(
    ctx: BaseCPSATContext,
    scenario: str = "C",
) -> list[cp_model.LinearExpr]:
    """Construye todos los terminos blandos exactos segun el validador oficial."""
    model = ctx.model
    inst = ctx.instance
    weights = get_scenario_weights(scenario)
    valid_slots = inst.valid_slots()
    terms: list[cp_model.LinearExpr] = []

    # 1. Horas muertas de estudiantes (student_gaps)
    group_ids = [g.id for g in inst.groups]
    g_gaps = _add_gap_variables(model, ctx.group_occ, group_ids, inst.num_days, valid_slots, "g")
    if g_gaps and weights.student_gaps > 0:
        terms.append(weights.student_gaps * sum(g_gaps.values()))

    # 2. Jornadas excesivas de estudiantes (student_excessive_day)
    if weights.student_excessive_day > 0:
        for g in inst.groups:
            soft_cap = min(g.max_daily_hours, 5)
            for d in range(inst.num_days):
                day_sum = sum(ctx.group_occ[(g.id, d, s)] for s in valid_slots)
                ex1 = model.NewIntVar(0, len(valid_slots), f"gex1_{g.id}_{d}")
                model.Add(ex1 >= day_sum - soft_cap)
                terms.append(weights.student_excessive_day * ex1)

                ex2 = model.NewIntVar(0, len(valid_slots), f"gex2_{g.id}_{d}")
                model.Add(ex2 >= day_sum - g.max_daily_hours)
                terms.append((2 * weights.student_excessive_day) * ex2)

    # 3. Reparto semanal de asignaturas (course_spread)
    if weights.course_spread > 0:
        for c in inst.courses:
            for d in range(inst.num_days):
                c_day_vars = [ctx.y[(c.id, d, s)] for s in valid_slots if (c.id, d, s) in ctx.y]
                if not c_day_vars:
                    continue
                c_sum = sum(c_day_vars)
                sp1 = model.NewIntVar(0, len(c_day_vars), f"csp1_{c.id}_{d}")
                model.Add(sp1 >= c_sum - 1)
                terms.append(weights.course_spread * sp1)

                sp2 = model.NewIntVar(0, len(c_day_vars), f"csp2_{c.id}_{d}")
                model.Add(sp2 >= c_sum - c.max_sessions_per_day)
                terms.append((3 * weights.course_spread) * sp2)

    # 4. Huecos de docentes (teacher_gaps)
    teacher_ids = [t.id for t in inst.teachers]
    t_gaps = _add_gap_variables(model, ctx.teacher_occ, teacher_ids, inst.num_days, valid_slots, "t")
    if t_gaps and weights.teacher_gaps > 0:
        terms.append(weights.teacher_gaps * sum(t_gaps.values()))

    # 5. Equilibrio de carga docente (teacher_balance)
    if weights.teacher_balance > 0:
        teacher_req = Counter()
        for c in inst.courses:
            teacher_req[c.teacher_id] += c.sessions_per_week
        for t in inst.teachers:
            ideal_cap = max(1, math.ceil(teacher_req[t.id] / max(1, inst.num_days)))
            for d in range(inst.num_days):
                t_sum = sum(ctx.teacher_occ[(t.id, d, s)] for s in valid_slots)
                tb1 = model.NewIntVar(0, len(valid_slots), f"tbal1_{t.id}_{d}")
                model.Add(tb1 >= t_sum - ideal_cap)
                terms.append(weights.teacher_balance * tb1)

                tb2 = model.NewIntVar(0, len(valid_slots), f"tbal2_{t.id}_{d}")
                model.Add(tb2 >= t_sum - t.max_daily_hours)
                terms.append((2 * weights.teacher_balance) * tb2)

    # 6. Buen uso de aulas y laboratorios (room_utilization)
    if weights.room_utilization > 0:
        courses = inst.course_map()
        groups = inst.group_map()
        rooms = inst.room_map()
        for (cid, d, s, rid), var in ctx.x.items():
            c = courses[cid]
            r = rooms[rid]
            g = groups[c.group_id]
            pen = 0
            if c.room_type == "aula" and r.room_type == "laboratorio":
                pen += 2
            if r.capacity - g.size > 15:
                pen += 1
            if pen > 0:
                terms.append((pen * weights.room_utilization) * var)

    # 7. Preferencias docentes blandas aprobadas
    if weights.preferences > 0:
        courses = inst.course_map()
        rooms = inst.room_map()
        teacher_x_vars: dict[str, list[tuple[tuple[str, int, int, str], cp_model.IntVar]]] = defaultdict(list)
        for key, var in ctx.x.items():
            cid = key[0]
            teacher_x_vars[courses[cid].teacher_id].append((key, var))

        for p in inst.approved_preferences():
            if p.hard:
                continue
            w = weights.preferences * p.weight
            tid = p.teacher_id
            ptype = p.pref_type
            params = p.params

            if ptype == "avoid_day":
                td = int(params["day"])
                vs = [ctx.teacher_occ[(tid, td, s)] for s in valid_slots if (tid, td, s) in ctx.teacher_occ]
                if vs:
                    terms.append(w * sum(vs))

            elif ptype == "avoid_slot":
                ts = int(params["slot"])
                td = params.get("day")
                if td is not None:
                    k = (tid, int(td), ts)
                    if k in ctx.teacher_occ:
                        terms.append(w * ctx.teacher_occ[k])
                else:
                    vs = [
                        ctx.teacher_occ[(tid, d, ts)]
                        for d in range(inst.num_days)
                        if (tid, d, ts) in ctx.teacher_occ
                    ]
                    if vs:
                        terms.append(w * sum(vs))

            elif ptype == "prefer_morning":
                cutoff = int(params.get("cutoff", inst.morning_cutoff_slot))
                vs = [
                    ctx.teacher_occ[(tid, d, s)]
                    for d in range(inst.num_days)
                    for s in valid_slots
                    if s >= cutoff and (tid, d, s) in ctx.teacher_occ
                ]
                if vs:
                    terms.append(w * sum(vs))

            elif ptype == "prefer_afternoon":
                cutoff = int(params.get("cutoff", inst.morning_cutoff_slot))
                vs = [
                    ctx.teacher_occ[(tid, d, s)]
                    for d in range(inst.num_days)
                    for s in valid_slots
                    if s < cutoff and (tid, d, s) in ctx.teacher_occ
                ]
                if vs:
                    terms.append(w * sum(vs))

            elif ptype == "max_daily_hours":
                limit = int(params["max_hours"])
                for d in range(inst.num_days):
                    t_sum = sum(ctx.teacher_occ[(tid, d, s)] for s in valid_slots)
                    ex = model.NewIntVar(0, len(valid_slots), f"pref_maxh_{p.id}_{d}")
                    model.Add(ex >= t_sum - limit)
                    terms.append(w * ex)

            elif ptype == "compact_schedule":
                vs = [
                    t_gaps[(tid, d, s)]
                    for d in range(inst.num_days)
                    for s in valid_slots
                    if (tid, d, s) in t_gaps
                ]
                if vs:
                    terms.append(w * sum(vs))

            elif ptype == "prefer_room_type":
                wanted = str(params["room_type"])
                vs = [
                    var
                    for (_, _, _, rid), var in teacher_x_vars.get(tid, [])
                    if rooms[rid].room_type != wanted
                ]
                if vs:
                    terms.append(w * sum(vs))

    return terms


def solve_cpsat(
    instance: Instance,
    scenario: str = "C",
    time_limit_sec: float = 15.0,
    num_workers: int = 4,
    seed: int = 42,
) -> Schedule:
    """Resuelve el problema completo con CP-SAT (duras + blandas para escenario A, B o C)."""
    t0 = time.perf_counter()
    scen = scenario.upper()
    ctx = build_base_cpsat_model(instance)
    soft_terms = _add_soft_objective(ctx, scenario=scen)
    if soft_terms:
        ctx.model.Minimize(sum(soft_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_sec
    solver.parameters.num_search_workers = num_workers
    solver.parameters.random_seed = seed
    status = solver.Solve(ctx.model)
    elapsed = time.perf_counter() - t0
    status_name = solver.StatusName(status)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return extract_schedule_from_solver(
            ctx,
            solver,
            method=f"CP-SAT (Escenario {scen})",
            scenario=scen,
            solve_time_sec=elapsed,
            metadata={
                "status": status_name,
                "objective_value": solver.ObjectiveValue(),
                "best_bound": solver.BestObjectiveBound(),
            },
        )
    return Schedule(
        instance_id=instance.id,
        method=f"CP-SAT (Escenario {scen})",
        scenario=scen,  # type: ignore[arg-type]
        assignments=[],
        solve_time_sec=elapsed,
        metadata={"status": status_name},
    )


def solve_cpsat_reprogram(
    updated_instance: Instance,
    base_schedule: Schedule,
    scenario: str = "C",
    time_limit_sec: float = 15.0,
    move_penalty: int = 120,
    lock_unaffected_teachers: set[str] | None = None,
    num_workers: int = 4,
    seed: int = 42,
) -> Schedule:
    """Modo reprogramacion (I4): ante un cambio, fija lo no afectado y mueve lo menos posible.

    - Penaliza fuertemente cada sesion del horario base que cambie de (dia, franja, aula).
    - Permite opcionalmente fijar con restriccion dura docentes/grupos que no deben tocarse.
    - Usa hints del horario base para acelerar la busqueda.
    """
    t0 = time.perf_counter()
    scen = scenario.upper()
    ctx = build_base_cpsat_model(updated_instance)
    soft_terms = _add_soft_objective(ctx, scenario=scen)

    courses = updated_instance.course_map()
    lock_set = lock_unaffected_teachers or set()

    # Contar sesiones base en cada tupla (cid, d, s, rid) y agregar hints + penalizacion de movimiento
    base_keys: set[tuple[str, int, int, str]] = set()
    for a in base_schedule.assignments:
        key = (a.course_id, a.day, a.slot, a.room_id)
        base_keys.add(key)
        if key in ctx.x:
            ctx.model.AddHint(ctx.x[key], 1)
            c = courses.get(a.course_id)
            if c and c.teacher_id in lock_set:
                ctx.model.Add(ctx.x[key] == 1)
            else:
                # Si la sesion NO se mantiene en su lugar original (x == 0), paga move_penalty
                soft_terms.append(move_penalty * (1 - ctx.x[key]))

    if soft_terms:
        ctx.model.Minimize(sum(soft_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_sec
    solver.parameters.num_search_workers = num_workers
    solver.parameters.random_seed = seed
    status = solver.Solve(ctx.model)
    elapsed = time.perf_counter() - t0
    status_name = solver.StatusName(status)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return extract_schedule_from_solver(
            ctx,
            solver,
            method=f"CP-SAT Reprogramacion (Escenario {scen})",
            scenario=scen,
            solve_time_sec=elapsed,
            metadata={
                "status": status_name,
                "move_penalty": move_penalty,
                "objective_value": solver.ObjectiveValue(),
                "best_bound": solver.BestObjectiveBound(),
            },
        )
    return Schedule(
        instance_id=updated_instance.id,
        method=f"CP-SAT Reprogramacion (Escenario {scen})",
        scenario=scen,  # type: ignore[arg-type]
        assignments=[],
        solve_time_sec=elapsed,
        metadata={"status": status_name},
    )
