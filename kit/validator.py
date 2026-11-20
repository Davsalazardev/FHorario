"""Validador oficial del kit comun (arbitro de la tabla de posiciones).

Regla del proyecto:
- Ningun equipo modifica el validador.
- Todo horario, venga de donde venga (CP-SAT, AG, Busqueda Local, LLM, Manual),
  pasa por este validador antes de reportarse.
- Primero cuenta tener 0 violaciones duras; entre horarios validos,
  gana la menor penalizacion blanda.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel, Field

from kit.schema import Assignment, Instance, Preference, Schedule, get_scenario_weights


class HardViolation(BaseModel):
    violation_type: str
    message: str
    entities: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    instance_id: str
    method: str
    scenario: str
    feasible: bool
    hard_violations_total: int
    hard_violations_by_type: dict[str, int]
    hard_violation_details: list[HardViolation] = Field(default_factory=list)
    soft_penalty_total: int
    soft_raw_units: dict[str, int]
    soft_weighted_breakdown: dict[str, int]
    student_dead_hours: int
    teacher_gaps: int
    preferences_total: int
    preferences_fulfilled: int
    preferences_pct: float
    solve_time_sec: float = 0.0

    def ranking_key(self) -> tuple[int, int, float]:
        """Clave de ordenamiento oficial: (violaciones_duras, penalizacion_blanda, tiempo)."""
        return (self.hard_violations_total, self.soft_penalty_total, self.solve_time_sec)


class StabilityReport(BaseModel):
    total_sessions: int
    moved_sessions: int
    unchanged_sessions: int
    moved_time_only: int
    moved_room_only: int
    moved_both: int
    stability_pct: float
    changes: list[dict[str, Any]] = Field(default_factory=list)


def _count_gaps(occupied_slots: list[int], recess_set: set[int]) -> int:
    """Cuenta franjas vacias (no receso) estrictamente entre primera y ultima clase."""
    if len(occupied_slots) < 2:
        return 0
    s_sorted = sorted(set(occupied_slots))
    first, last = s_sorted[0], s_sorted[-1]
    occ = set(s_sorted)
    return sum(1 for s in range(first + 1, last) if s not in occ and s not in recess_set)


def evaluate_preference_units(
    instance: Instance,
    pref: Preference,
    teacher_assignments: list[Assignment],
    recess_set: set[int],
) -> int:
    """Retorna unidades de violacion de una preferencia blanda (0 = cumplida)."""
    ptype = pref.pref_type
    params = pref.params

    if ptype == "avoid_day":
        target_day = int(params["day"])
        return sum(1 for a in teacher_assignments if a.day == target_day)

    if ptype == "avoid_slot":
        target_slot = int(params["slot"])
        target_day = params.get("day")
        if target_day is not None:
            td = int(target_day)
            return sum(1 for a in teacher_assignments if a.day == td and a.slot == target_slot)
        return sum(1 for a in teacher_assignments if a.slot == target_slot)

    if ptype == "prefer_morning":
        cutoff = int(params.get("cutoff", instance.morning_cutoff_slot))
        return sum(1 for a in teacher_assignments if a.slot >= cutoff)

    if ptype == "prefer_afternoon":
        cutoff = int(params.get("cutoff", instance.morning_cutoff_slot))
        return sum(1 for a in teacher_assignments if a.slot < cutoff)

    if ptype == "max_daily_hours":
        limit = int(params["max_hours"])
        by_day: dict[int, int] = Counter(a.day for a in teacher_assignments)
        return sum(max(0, count - limit) for count in by_day.values())

    if ptype == "compact_schedule":
        by_day_slots: dict[int, list[int]] = defaultdict(list)
        for a in teacher_assignments:
            by_day_slots[a.day].append(a.slot)
        return sum(_count_gaps(slots, recess_set) for slots in by_day_slots.values())

    if ptype == "prefer_room_type":
        wanted = str(params["room_type"])
        rooms = instance.room_map()
        return sum(
            1
            for a in teacher_assignments
            if a.room_id in rooms and rooms[a.room_id].room_type != wanted
        )

    return 0


def validate_schedule(
    instance: Instance,
    schedule: Schedule,
    scenario: str | None = None,
) -> ValidationReport:
    """Valida un horario completo contra el kit comun y calcula todas las metricas."""
    scen = (scenario or schedule.scenario or "C").upper()
    weights = get_scenario_weights(scen)

    teachers = instance.teacher_map()
    groups = instance.group_map()
    rooms = instance.room_map()
    courses = instance.course_map()
    recess_set = set(instance.recess_slots)

    hard_counts: dict[str, int] = {
        "teacher_conflict": 0,
        "group_conflict": 0,
        "room_conflict": 0,
        "teacher_availability": 0,
        "room_availability": 0,
        "room_suitability": 0,
        "course_coverage": 0,
        "invalid_reference": 0,
    }
    details: list[HardViolation] = []

    def add_hard(vtype: str, msg: str, entities: dict[str, Any]) -> None:
        hard_counts[vtype] += 1
        details.append(HardViolation(violation_type=vtype, message=msg, entities=entities))

    # Indexadores para conflictos y metricas
    teacher_at: dict[tuple[str, int, int], list[Assignment]] = defaultdict(list)
    group_at: dict[tuple[str, int, int], list[Assignment]] = defaultdict(list)
    room_at: dict[tuple[str, int, int], list[Assignment]] = defaultdict(list)
    course_sessions: dict[str, list[Assignment]] = defaultdict(list)
    valid_assignments: list[Assignment] = []

    teacher_blocked_cache = {t.id: instance.teacher_blocked_slots(t.id) for t in instance.teachers}
    room_blocked_cache = {r.id: r.blocked_slots() for r in instance.rooms}

    for a in schedule.assignments:
        if a.course_id not in courses:
            add_hard(
                "invalid_reference",
                f"Asignatura inexistente: {a.course_id}",
                {"course_id": a.course_id},
            )
            continue
        if a.room_id not in rooms:
            add_hard(
                "invalid_reference",
                f"Aula inexistente: {a.room_id}",
                {"room_id": a.room_id, "course_id": a.course_id},
            )
            continue
        if not (0 <= a.day < instance.num_days) or not (0 <= a.slot < instance.slots_per_day):
            add_hard(
                "invalid_reference",
                f"Dia/franja fuera de rango ({a.day}, {a.slot}) para {a.course_id}",
                {"course_id": a.course_id, "day": a.day, "slot": a.slot},
            )
            continue

        course = courses[a.course_id]
        room = rooms[a.room_id]
        group = groups[course.group_id]

        valid_assignments.append(a)
        course_sessions[a.course_id].append(a)
        teacher_at[(course.teacher_id, a.day, a.slot)].append(a)
        group_at[(course.group_id, a.day, a.slot)].append(a)
        room_at[(a.room_id, a.day, a.slot)].append(a)

        # Disponibilidad de docente (incluye recursos compartidos y restricciones duras)
        if a.slot in recess_set or (a.day, a.slot) in teacher_blocked_cache[course.teacher_id]:
            add_hard(
                "teacher_availability",
                f"Docente {course.teacher_id} no disponible en dia {a.day} franja {a.slot} ({course.id})",
                {"teacher_id": course.teacher_id, "day": a.day, "slot": a.slot, "course_id": course.id},
            )

        # Disponibilidad de aula (incluye recursos compartidos y recesos)
        if a.slot in recess_set or (a.day, a.slot) in room_blocked_cache[room.id]:
            add_hard(
                "room_availability",
                f"Aula {room.id} no disponible en dia {a.day} franja {a.slot} ({course.id})",
                {"room_id": room.id, "day": a.day, "slot": a.slot, "course_id": course.id},
            )

        # Idoneidad del aula (capacidad, tipo y equipamiento)
        if not instance.is_room_suitable(course, room):
            add_hard(
                "room_suitability",
                (
                    f"Aula {room.id} ({room.room_type}, cap {room.capacity}) no apta para "
                    f"{course.id} (grupo {group.id} tam {group.size}, req {course.room_type})"
                ),
                {"room_id": room.id, "course_id": course.id, "group_id": group.id},
            )

    # Conflictos de docente
    for (tid, d, s), items in teacher_at.items():
        if len(items) > 1:
            extra = len(items) - 1
            for _ in range(extra):
                add_hard(
                    "teacher_conflict",
                    f"Docente {tid} tiene {len(items)} clases simultaneas en dia {d} franja {s}",
                    {"teacher_id": tid, "day": d, "slot": s, "courses": [x.course_id for x in items]},
                )

    # Conflictos de grupo
    for (gid, d, s), items in group_at.items():
        if len(items) > 1:
            extra = len(items) - 1
            for _ in range(extra):
                add_hard(
                    "group_conflict",
                    f"Grupo {gid} tiene {len(items)} clases simultaneas en dia {d} franja {s}",
                    {"group_id": gid, "day": d, "slot": s, "courses": [x.course_id for x in items]},
                )

    # Conflictos de aula
    for (rid, d, s), items in room_at.items():
        if len(items) > 1:
            extra = len(items) - 1
            for _ in range(extra):
                add_hard(
                    "room_conflict",
                    f"Aula {rid} aloja {len(items)} grupos a la vez en dia {d} franja {s}",
                    {"room_id": rid, "day": d, "slot": s, "courses": [x.course_id for x in items]},
                )

    # Cobertura de horas/sesiones por asignatura
    for c in instance.courses:
        assigned = len(course_sessions[c.id])
        diff = abs(assigned - c.sessions_per_week)
        if diff > 0:
            for _ in range(diff):
                add_hard(
                    "course_coverage",
                    f"Asignatura {c.id} requiere {c.sessions_per_week} sesiones y tiene {assigned}",
                    {"course_id": c.id, "required": c.sessions_per_week, "assigned": assigned},
                )

    # =========================================================================
    # METRICAS BLANDAS (CALIDAD)
    # =========================================================================
    group_day_slots: dict[tuple[str, int], list[int]] = defaultdict(list)
    teacher_day_slots: dict[tuple[str, int], list[int]] = defaultdict(list)
    teacher_all_assignments: dict[str, list[Assignment]] = defaultdict(list)
    course_day_counts: dict[tuple[str, int], int] = Counter()

    room_util_units = 0
    for a in valid_assignments:
        c = courses[a.course_id]
        r = rooms[a.room_id]
        g = groups[c.group_id]
        group_day_slots[(c.group_id, a.day)].append(a.slot)
        teacher_day_slots[(c.teacher_id, a.day)].append(a.slot)
        teacher_all_assignments[c.teacher_id].append(a)
        course_day_counts[(c.id, a.day)] += 1

        # Uso adecuado de aulas y laboratorios
        if c.room_type == "aula" and r.room_type == "laboratorio":
            room_util_units += 2
        if r.capacity - g.size > 15:
            room_util_units += 1

    # 1. Horas muertas y jornadas excesivas de estudiantes
    student_gaps_units = 0
    student_excess_units = 0
    for g in instance.groups:
        soft_daily_cap = min(g.max_daily_hours, 5)
        for d in range(instance.num_days):
            slots = group_day_slots.get((g.id, d), [])
            student_gaps_units += _count_gaps(slots, recess_set)
            hours = len(slots)
            if hours > soft_daily_cap:
                student_excess_units += hours - soft_daily_cap
            if hours > g.max_daily_hours:
                student_excess_units += 2 * (hours - g.max_daily_hours)

    # 2. Reparto semanal de asignaturas
    course_spread_units = 0
    for c in instance.courses:
        for d in range(instance.num_days):
            cnt = course_day_counts.get((c.id, d), 0)
            if cnt > 1:
                course_spread_units += cnt - 1
            if cnt > c.max_sessions_per_day:
                course_spread_units += 3 * (cnt - c.max_sessions_per_day)

    # 3. Huecos y equilibrio de carga docente
    teacher_gaps_units = 0
    teacher_balance_units = 0
    teacher_total_req: dict[str, int] = Counter()
    for c in instance.courses:
        teacher_total_req[c.teacher_id] += c.sessions_per_week

    for t in instance.teachers:
        ideal_cap = max(1, math.ceil(teacher_total_req[t.id] / max(1, instance.num_days)))
        for d in range(instance.num_days):
            slots = teacher_day_slots.get((t.id, d), [])
            teacher_gaps_units += _count_gaps(slots, recess_set)
            hours = len(slots)
            if hours > ideal_cap:
                teacher_balance_units += hours - ideal_cap
            if hours > t.max_daily_hours:
                teacher_balance_units += 2 * (hours - t.max_daily_hours)

    # 4. Preferencias docentes (blandas aprobadas)
    soft_prefs = [p for p in instance.approved_preferences() if not p.hard]
    pref_units = 0
    prefs_fulfilled = 0
    for p in soft_prefs:
        u = evaluate_preference_units(
            instance, p, teacher_all_assignments.get(p.teacher_id, []), recess_set
        )
        if u == 0:
            prefs_fulfilled += 1
        pref_units += u * p.weight

    prefs_total = len(soft_prefs)
    prefs_pct = (100.0 * prefs_fulfilled / prefs_total) if prefs_total > 0 else 100.0

    raw_units = {
        "student_gaps": student_gaps_units,
        "student_excessive_day": student_excess_units,
        "course_spread": course_spread_units,
        "teacher_gaps": teacher_gaps_units,
        "teacher_balance": teacher_balance_units,
        "room_utilization": room_util_units,
        "preferences": pref_units,
    }
    weighted = {
        "student_gaps": student_gaps_units * weights.student_gaps,
        "student_excessive_day": student_excess_units * weights.student_excessive_day,
        "course_spread": course_spread_units * weights.course_spread,
        "teacher_gaps": teacher_gaps_units * weights.teacher_gaps,
        "teacher_balance": teacher_balance_units * weights.teacher_balance,
        "room_utilization": room_util_units * weights.room_utilization,
        "preferences": pref_units * weights.preferences,
    }
    soft_total = sum(weighted.values())
    hard_total = sum(hard_counts.values())

    return ValidationReport(
        instance_id=instance.id,
        method=schedule.method,
        scenario=scen,
        feasible=(hard_total == 0),
        hard_violations_total=hard_total,
        hard_violations_by_type=hard_counts,
        hard_violation_details=details,
        soft_penalty_total=soft_total,
        soft_raw_units=raw_units,
        soft_weighted_breakdown=weighted,
        student_dead_hours=student_gaps_units,
        teacher_gaps=teacher_gaps_units,
        preferences_total=prefs_total,
        preferences_fulfilled=prefs_fulfilled,
        preferences_pct=round(prefs_pct, 2),
        solve_time_sec=round(schedule.solve_time_sec, 4),
    )


def compute_stability(original: Schedule, updated: Schedule) -> StabilityReport:
    """Mide estabilidad para I4: sesiones movidas respecto al horario original.

    Empareja las sesiones de cada asignatura maximizando coincidencias exactas
    (mismo dia, franja y aula) para no penalizar permutaciones del indice session_idx.
    """
    orig_by_course: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    new_by_course: dict[str, list[tuple[int, int, str]]] = defaultdict(list)

    for a in original.assignments:
        orig_by_course[a.course_id].append((a.day, a.slot, a.room_id))
    for a in updated.assignments:
        new_by_course[a.course_id].append((a.day, a.slot, a.room_id))

    total = len(original.assignments)
    unchanged = 0
    moved_time_only = 0
    moved_room_only = 0
    moved_both = 0
    changes: list[dict[str, Any]] = []

    all_courses = sorted(set(orig_by_course.keys()) | set(new_by_course.keys()))
    for cid in all_courses:
        orig_list = list(orig_by_course[cid])
        new_list = list(new_by_course[cid])

        # 1. Emparejar identicos exactos (day, slot, room)
        rem_orig = []
        for item in orig_list:
            if item in new_list:
                new_list.remove(item)
                unchanged += 1
            else:
                rem_orig.append(item)

        # 2. Emparejar mismo (day, slot) distinto aula -> moved_room_only
        rem_orig_2 = []
        for d, s, r in rem_orig:
            match_idx = next((i for i, x in enumerate(new_list) if x[0] == d and x[1] == s), None)
            if match_idx is not None:
                nd, ns, nr = new_list.pop(match_idx)
                moved_room_only += 1
                changes.append(
                    {"course_id": cid, "from": (d, s, r), "to": (nd, ns, nr), "type": "room_only"}
                )
            else:
                rem_orig_2.append((d, s, r))

        # 3. Emparejar misma aula distinto (day, slot) -> moved_time_only
        rem_orig_3 = []
        for d, s, r in rem_orig_2:
            match_idx = next((i for i, x in enumerate(new_list) if x[2] == r), None)
            if match_idx is not None:
                nd, ns, nr = new_list.pop(match_idx)
                moved_time_only += 1
                changes.append(
                    {"course_id": cid, "from": (d, s, r), "to": (nd, ns, nr), "type": "time_only"}
                )
            else:
                rem_orig_3.append((d, s, r))

        # 4. Restantes -> moved_both
        for (d, s, r), (nd, ns, nr) in zip(rem_orig_3, new_list):
            moved_both += 1
            changes.append(
                {"course_id": cid, "from": (d, s, r), "to": (nd, ns, nr), "type": "time_and_room"}
            )
        # Si difieren en conteo
        extra_diff = abs(len(rem_orig_3) - len(new_list))
        moved_both += extra_diff

    moved = moved_time_only + moved_room_only + moved_both
    stability_pct = (100.0 * unchanged / total) if total > 0 else 100.0

    return StabilityReport(
        total_sessions=total,
        moved_sessions=moved,
        unchanged_sessions=unchanged,
        moved_time_only=moved_time_only,
        moved_room_only=moved_room_only,
        moved_both=moved_both,
        stability_pct=round(stability_pct, 2),
        changes=changes,
    )
