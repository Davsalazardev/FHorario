"""Modelo CP-SAT base del kit comun (unicamente restricciones duras).

R2 extiende este modelo agregando penalizaciones blandas, escenarios A/B/C
y modo de reprogramacion con estabilidad minima.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

from ortools.sat.python import cp_model

from kit.schema import Assignment, Instance, Schedule


@dataclass
class BaseCPSATContext:
    """Contenedor de variables y estructuras del modelo CP-SAT base."""

    model: cp_model.CpModel
    instance: Instance
    # x[(course_id, day, slot, room_id)] -> BoolVar: curso c se imparte en (d, s, r)
    x: dict[tuple[str, int, int, str], cp_model.IntVar]
    # y[(course_id, day, slot)] -> BoolVar: curso c se imparte en (d, s) en algun aula
    y: dict[tuple[str, int, int], cp_model.IntVar]
    # Variables agregadas utiles para extensiones blandas en R2
    group_occ: dict[tuple[str, int, int], cp_model.IntVar]
    teacher_occ: dict[tuple[str, int, int], cp_model.IntVar]


def build_base_cpsat_model(instance: Instance) -> BaseCPSATContext:
    """Construye el modelo CP-SAT base con todas las restricciones duras."""
    model = cp_model.CpModel()
    valid_slots = instance.valid_slots()

    x: dict[tuple[str, int, int, str], cp_model.IntVar] = {}
    y: dict[tuple[str, int, int], cp_model.IntVar] = {}

    teacher_slot_vars: dict[tuple[str, int, int], list[cp_model.IntVar]] = defaultdict(list)
    group_slot_vars: dict[tuple[str, int, int], list[cp_model.IntVar]] = defaultdict(list)
    room_slot_vars: dict[tuple[str, int, int], list[cp_model.IntVar]] = defaultdict(list)

    # 1. Crear variables solo en combinaciones validas (disponibilidad + idoneidad de aula)
    for c in instance.courses:
        comp_rooms = instance.compatible_rooms(c.id)
        t_blocked = instance.teacher_blocked_slots(c.teacher_id)
        course_all_vars: list[cp_model.IntVar] = []

        for d in range(instance.num_days):
            for s in valid_slots:
                if (d, s) in t_blocked:
                    continue

                slot_room_vars: list[cp_model.IntVar] = []
                for r in comp_rooms:
                    if (d, s) in r.blocked_slots():
                        continue
                    v = model.NewBoolVar(f"x_{c.id}_{d}_{s}_{r.id}")
                    x[(c.id, d, s, r.id)] = v
                    slot_room_vars.append(v)
                    room_slot_vars[(r.id, d, s)].append(v)

                if slot_room_vars:
                    yv = model.NewBoolVar(f"y_{c.id}_{d}_{s}")
                    # En una misma franja un curso usa a lo sumo 1 aula y yv == sum(slot_room_vars)
                    model.Add(sum(slot_room_vars) == yv)
                    y[(c.id, d, s)] = yv
                    course_all_vars.append(yv)
                    teacher_slot_vars[(c.teacher_id, d, s)].append(yv)
                    group_slot_vars[(c.group_id, d, s)].append(yv)

        # Restriccion dura: cobertura exacta de sesiones semanales por asignatura
        model.Add(sum(course_all_vars) == c.sessions_per_week)

    # 2. Restriccion dura: un docente no imparte dos clases a la vez
    teacher_occ: dict[tuple[str, int, int], cp_model.IntVar] = {}
    for t in instance.teachers:
        for d in range(instance.num_days):
            for s in valid_slots:
                vs = teacher_slot_vars.get((t.id, d, s), [])
                occ = model.NewBoolVar(f"tocc_{t.id}_{d}_{s}")
                if vs:
                    model.Add(sum(vs) <= 1)
                    model.Add(occ == sum(vs))
                else:
                    model.Add(occ == 0)
                teacher_occ[(t.id, d, s)] = occ

    # 3. Restriccion dura: un grupo no tiene dos asignaturas a la vez
    group_occ: dict[tuple[str, int, int], cp_model.IntVar] = {}
    for g in instance.groups:
        for d in range(instance.num_days):
            for s in valid_slots:
                vs = group_slot_vars.get((g.id, d, s), [])
                occ = model.NewBoolVar(f"gocc_{g.id}_{d}_{s}")
                if vs:
                    model.Add(sum(vs) <= 1)
                    model.Add(occ == sum(vs))
                else:
                    model.Add(occ == 0)
                group_occ[(g.id, d, s)] = occ

    # 4. Restriccion dura: un aula no aloja dos grupos a la vez
    for (rid, d, s), vs in room_slot_vars.items():
        if len(vs) > 1:
            model.Add(sum(vs) <= 1)

    return BaseCPSATContext(
        model=model,
        instance=instance,
        x=x,
        y=y,
        group_occ=group_occ,
        teacher_occ=teacher_occ,
    )


def extract_schedule_from_solver(
    ctx: BaseCPSATContext,
    solver: cp_model.CpSolver,
    method: str = "CP-SAT Base",
    scenario: str = "C",
    solve_time_sec: float = 0.0,
    metadata: dict | None = None,
) -> Schedule:
    """Extrae un objeto Schedule a partir de un solver CP-SAT con solucion factible."""
    assignments: list[Assignment] = []
    session_counter: dict[str, int] = defaultdict(int)

    for (cid, d, s, rid), var in sorted(ctx.x.items()):
        if solver.Value(var) == 1:
            idx = session_counter[cid]
            session_counter[cid] += 1
            assignments.append(
                Assignment(course_id=cid, session_idx=idx, day=d, slot=s, room_id=rid)
            )

    return Schedule(
        instance_id=ctx.instance.id,
        method=method,
        scenario=scenario,  # type: ignore[arg-type]
        assignments=assignments,
        solve_time_sec=solve_time_sec,
        metadata=metadata or {},
    )


def solve_base_cpsat(instance: Instance, time_limit_sec: float = 15.0) -> Schedule:
    """Resuelve unicamente factibilidad con el modelo base del kit comun."""
    t0 = time.perf_counter()
    ctx = build_base_cpsat_model(instance)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_sec
    solver.parameters.num_search_workers = 4
    status = solver.Solve(ctx.model)
    elapsed = time.perf_counter() - t0

    status_name = solver.StatusName(status)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return extract_schedule_from_solver(
            ctx,
            solver,
            method="CP-SAT Base",
            scenario="C",
            solve_time_sec=elapsed,
            metadata={"status": status_name},
        )
    return Schedule(
        instance_id=instance.id,
        method="CP-SAT Base",
        scenario="C",
        assignments=[],
        solve_time_sec=elapsed,
        metadata={"status": status_name},
    )
