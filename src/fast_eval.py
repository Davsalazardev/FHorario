"""Evaluador rapido en memoria para metaheuristicas (R3 Algoritmo Genetico y R4 Busqueda Local).

Mantiene correspondencia matematica 1-a-1 con kit.validator.validate_schedule,
pero opera sobre enteros compactos para evaluar miles de candidatos por segundo.
Todo horario final aun pasa obligatoriamente por el validador oficial kit.validator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from kit.schema import Assignment, Instance, Preference, Schedule, get_scenario_weights


@dataclass(frozen=True)
class SessionSpec:
    idx: int
    course_idx: int
    course_id: str
    session_idx: int
    group_idx: int
    all_group_idxs: tuple[int, ...]
    teacher_idx: int
    room_type: str
    group_size: int
    allowed_rooms: tuple[int, ...]
    # Dominio de combinaciones validas (day, slot, room_idx) que respetan disponibilidad e idoneidad
    valid_domain: tuple[tuple[int, int, int], ...]


class FastEvaluator:
    """Evaluador compacto sincronizado con el validador oficial del kit comun."""

    def __init__(self, instance: Instance, scenario: str = "C") -> None:
        self.instance = instance
        self.scenario = scenario.upper()
        self.weights = get_scenario_weights(self.scenario)

        self.num_days = instance.num_days
        self.slots_per_day = instance.slots_per_day
        self.valid_slots = tuple(instance.valid_slots())
        self.recess_set = frozenset(instance.recess_slots)

        self.teachers = instance.teachers
        self.groups = instance.groups
        self.rooms = instance.rooms
        self.courses = instance.courses

        self.teacher_idx = {t.id: i for i, t in enumerate(self.teachers)}
        self.group_idx = {g.id: i for i, g in enumerate(self.groups)}
        self.room_idx = {r.id: i for i, r in enumerate(self.rooms)}
        self.course_idx = {c.id: i for i, c in enumerate(self.courses)}

        self.num_teachers = len(self.teachers)
        self.num_groups = len(self.groups)
        self.num_rooms = len(self.rooms)
        self.num_courses = len(self.courses)

        # Bloqueos de docentes y aulas
        self.teacher_blocked: list[frozenset[tuple[int, int]]] = [
            frozenset(instance.teacher_blocked_slots(t.id)) for t in self.teachers
        ]
        self.room_blocked: list[frozenset[tuple[int, int]]] = [
            frozenset(r.blocked_slots()) for r in self.rooms
        ]

        # Parametros precalculados para blandas
        self.group_max_daily = [g.max_daily_hours for g in self.groups]
        self.group_soft_cap = [min(g.max_daily_hours, 5) for g in self.groups]
        self.course_max_per_day = [c.max_sessions_per_day for c in self.courses]
        self.teacher_max_daily = [t.max_daily_hours for t in self.teachers]

        t_req = [0] * self.num_teachers
        for c in self.courses:
            t_req[self.teacher_idx[c.teacher_id]] += c.sessions_per_week
        self.teacher_ideal_cap = [
            max(1, math.ceil(req / max(1, self.num_days))) for req in t_req
        ]

        # Penalizacion de aula precalculada por (course_idx, room_idx)
        self.room_pen: list[list[int]] = []
        for c in self.courses:
            req_size = max(
                (self.groups[self.group_idx[gid]].size for gid in c.all_group_ids() if gid in self.group_idx),
                default=0,
            )
            row = []
            for r in self.rooms:
                p = 0
                if c.room_type == "aula" and r.room_type == "laboratorio":
                    p += 2
                if r.capacity - req_size > 15:
                    p += 1
                row.append(p)
            self.room_pen.append(row)

        # Preferencias blandas aprobadas compiladas
        self.soft_prefs: list[tuple[int, Preference]] = []
        for p in instance.approved_preferences():
            if not p.hard and p.teacher_id in self.teacher_idx:
                self.soft_prefs.append((self.teacher_idx[p.teacher_id], p))

        # Lista de sesiones con sus dominios validos (respetan disponibilidad + idoneidad)
        self.sessions: list[SessionSpec] = []
        s_idx = 0
        for ci, c in enumerate(self.courses):
            gi = self.group_idx[c.group_id]
            all_gis = tuple(self.group_idx[gid] for gid in c.all_group_ids() if gid in self.group_idx)
            ti = self.teacher_idx[c.teacher_id]
            comp = tuple(self.room_idx[r.id] for r in instance.compatible_rooms(c.id))
            t_blk = self.teacher_blocked[ti]
            domain = []
            for d in range(self.num_days):
                for s in self.valid_slots:
                    if (d, s) in t_blk:
                        continue
                    for ri in comp:
                        if (d, s) not in self.room_blocked[ri]:
                            domain.append((d, s, ri))
            dom_tuple = tuple(domain)
            for k in range(c.sessions_per_week):
                self.sessions.append(
                    SessionSpec(
                        idx=s_idx,
                        course_idx=ci,
                        course_id=c.id,
                        session_idx=k,
                        group_idx=gi,
                        all_group_idxs=all_gis,
                        teacher_idx=ti,
                        room_type=c.room_type,
                        group_size=max(self.groups[g_i].size for g_i in all_gis),
                        allowed_rooms=comp,
                        valid_domain=dom_tuple,
                    )
                )
                s_idx += 1

        self.num_sessions = len(self.sessions)

    def evaluate(self, genes: list[tuple[int, int, int]]) -> tuple[int, int]:
        """Retorna (hard_violations, soft_penalty) para una lista de genes (day, slot, room_idx).

        Asume que cada gen proviene de valid_domain (cobertura exacta + disponibilidad + idoneidad).
        Si algun gen viola disponibilidad/idoneidad tambien lo cuenta como violacion dura.
        """
        D = self.num_days
        S = self.slots_per_day
        DS = D * S

        t_cnt: dict[int, int] = {}
        g_cnt: dict[int, int] = {}
        r_cnt: dict[int, int] = {}

        # Listas de slots por (entity, day) usando bitmasks de slots para conteo de huecos ultrarapido
        g_mask = [0] * (self.num_groups * D)
        g_day_n = [0] * (self.num_groups * D)
        t_mask = [0] * (self.num_teachers * D)
        t_day_n = [0] * (self.num_teachers * D)
        c_day_n = [0] * (self.num_courses * D)

        hard = 0
        room_util = 0

        # Para preferencias
        t_assignments: list[list[tuple[int, int, int]]] = [[] for _ in range(self.num_teachers)]

        for spec, (d, s, ri) in zip(self.sessions, genes):
            ds = d * S + s
            tk = spec.teacher_idx * DS + ds
            rk = ri * DS + ds

            tc = t_cnt.get(tk, 0)
            if tc > 0:
                hard += 1
            t_cnt[tk] = tc + 1

            bit = 1 << s
            for g_i in spec.all_group_idxs:
                gk = g_i * DS + ds
                gc = g_cnt.get(gk, 0)
                if gc > 0:
                    hard += 1
                g_cnt[gk] = gc + 1
                gd = g_i * D + d
                g_mask[gd] |= bit
                g_day_n[gd] += 1

            rc = r_cnt.get(rk, 0)
            if rc > 0:
                hard += 1
            r_cnt[rk] = rc + 1

            td = spec.teacher_idx * D + d
            t_mask[td] |= bit
            t_day_n[td] += 1

            c_day_n[spec.course_idx * D + d] += 1
            room_util += self.room_pen[spec.course_idx][ri]
            t_assignments[spec.teacher_idx].append((d, s, ri))

        w = self.weights

        # 1 & 2. Estudiantes: huecos + jornadas excesivas
        student_gaps = 0
        student_excess = 0
        recess_mask = 0
        for rs in self.recess_set:
            recess_mask |= 1 << rs

        for gi in range(self.num_groups):
            soft_cap = self.group_soft_cap[gi]
            hard_cap = self.group_max_daily[gi]
            base = gi * D
            for d in range(D):
                m = g_mask[base + d]
                n = g_day_n[base + d]
                if n > soft_cap:
                    student_excess += n - soft_cap
                if n > hard_cap:
                    student_excess += 2 * (n - hard_cap)
                if m and (m & (m - 1)):  # al menos 2 bits
                    low = (m & -m).bit_length() - 1
                    high = m.bit_length() - 1
                    span = ((1 << (high + 1)) - 1) ^ ((1 << low) - 1)
                    empty = span & ~m & ~recess_mask
                    student_gaps += empty.bit_count()

        # 3. Reparto de asignaturas
        course_spread = 0
        for ci in range(self.num_courses):
            max_d = self.course_max_per_day[ci]
            base = ci * D
            for d in range(D):
                n = c_day_n[base + d]
                if n > 1:
                    course_spread += n - 1
                if n > max_d:
                    course_spread += 3 * (n - max_d)

        # 4 & 5. Docentes: huecos + equilibrio
        teacher_gaps_by_t = [0] * self.num_teachers
        teacher_gaps = 0
        teacher_balance = 0
        for ti in range(self.num_teachers):
            ideal = self.teacher_ideal_cap[ti]
            hard_cap = self.teacher_max_daily[ti]
            base = ti * D
            tg = 0
            for d in range(D):
                m = t_mask[base + d]
                n = t_day_n[base + d]
                if n > ideal:
                    teacher_balance += n - ideal
                if n > hard_cap:
                    teacher_balance += 2 * (n - hard_cap)
                if m and (m & (m - 1)):
                    low = (m & -m).bit_length() - 1
                    high = m.bit_length() - 1
                    span = ((1 << (high + 1)) - 1) ^ ((1 << low) - 1)
                    empty = span & ~m & ~recess_mask
                    tg += empty.bit_count()
            teacher_gaps_by_t[ti] = tg
            teacher_gaps += tg

        # 6. Preferencias blandas
        pref_units = 0
        cutoff_default = self.instance.morning_cutoff_slot
        rooms = self.rooms
        for ti, p in self.soft_prefs:
            ptype = p.pref_type
            params = p.params
            t_list = t_assignments[ti]
            u = 0
            if ptype == "avoid_day":
                td = int(params["day"])
                u = sum(1 for d, _, _ in t_list if d == td)
            elif ptype == "avoid_slot":
                ts = int(params["slot"])
                td = params.get("day")
                if td is not None:
                    td_i = int(td)
                    u = sum(1 for d, s, _ in t_list if d == td_i and s == ts)
                else:
                    u = sum(1 for _, s, _ in t_list if s == ts)
            elif ptype == "prefer_morning":
                co = int(params.get("cutoff", cutoff_default))
                u = sum(1 for _, s, _ in t_list if s >= co)
            elif ptype == "prefer_afternoon":
                co = int(params.get("cutoff", cutoff_default))
                u = sum(1 for _, s, _ in t_list if s < co)
            elif ptype == "max_daily_hours":
                lim = int(params["max_hours"])
                base = ti * D
                for d in range(D):
                    n = t_day_n[base + d]
                    if n > lim:
                        u += n - lim
            elif ptype == "compact_schedule":
                u = teacher_gaps_by_t[ti]
            elif ptype == "prefer_room_type":
                wanted = str(params["room_type"])
                u = sum(1 for _, _, ri in t_list if rooms[ri].room_type != wanted)
            pref_units += u * p.weight

        soft = (
            student_gaps * w.student_gaps
            + student_excess * w.student_excessive_day
            + course_spread * w.course_spread
            + teacher_gaps * w.teacher_gaps
            + teacher_balance * w.teacher_balance
            + room_util * w.room_utilization
            + pref_units * w.preferences
        )
        return hard, soft

    def conflicting_sessions(self, genes: list[tuple[int, int, int]]) -> list[int]:
        """Devuelve indices de sesiones que participan en al menos un conflicto duro."""
        S = self.slots_per_day
        DS = self.num_days * S
        t_map: dict[int, list[int]] = {}
        g_map: dict[int, list[int]] = {}
        r_map: dict[int, list[int]] = {}

        for i, (spec, (d, s, ri)) in enumerate(zip(self.sessions, genes)):
            ds = d * S + s
            t_map.setdefault(spec.teacher_idx * DS + ds, []).append(i)
            g_map.setdefault(spec.group_idx * DS + ds, []).append(i)
            r_map.setdefault(ri * DS + ds, []).append(i)

        bad: set[int] = set()
        for m in (t_map, g_map, r_map):
            for lst in m.values():
                if len(lst) > 1:
                    bad.update(lst)
        return list(bad)

    def to_schedule(
        self,
        genes: list[tuple[int, int, int]],
        method: str,
        solve_time_sec: float = 0.0,
        seed: int | None = None,
        metadata: dict | None = None,
    ) -> Schedule:
        assignments = [
            Assignment(
                course_id=spec.course_id,
                session_idx=spec.session_idx,
                day=d,
                slot=s,
                room_id=self.rooms[ri].id,
            )
            for spec, (d, s, ri) in zip(self.sessions, genes)
        ]
        return Schedule(
            instance_id=self.instance.id,
            method=method,
            scenario=self.scenario,  # type: ignore[arg-type]
            assignments=assignments,
            solve_time_sec=solve_time_sec,
            seed=seed,
            metadata=metadata or {},
        )
