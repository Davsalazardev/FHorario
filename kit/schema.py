"""Esquema de datos comun (JSON) para el sistema de horarios de ITIID.

Regla del kit comun: todos los componentes (R1 a R6) consumen y producen
exactamente estas estructuras serializables a JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


RoomType = Literal["aula", "laboratorio"]
PrefType = Literal[
    "avoid_day",
    "avoid_slot",
    "prefer_morning",
    "prefer_afternoon",
    "max_daily_hours",
    "compact_schedule",
    "prefer_room_type",
]
PrefStatus = Literal["approved", "pending_review", "rejected"]
ScenarioId = Literal["A", "B", "C"]


class ScenarioWeights(BaseModel):
    """Pesos de penalizacion blanda para los escenarios A, B y C."""

    scenario: ScenarioId
    description: str
    student_gaps: int
    student_excessive_day: int
    course_spread: int
    teacher_gaps: int
    teacher_balance: int
    room_utilization: int
    preferences: int


SCENARIOS: dict[ScenarioId, ScenarioWeights] = {
    "A": ScenarioWeights(
        scenario="A",
        description="Prioriza a los estudiantes (menos horas muertas, jornadas equilibradas y buen reparto semanal)",
        student_gaps=10,
        student_excessive_day=12,
        course_spread=8,
        teacher_gaps=2,
        teacher_balance=2,
        room_utilization=3,
        preferences=3,
    ),
    "B": ScenarioWeights(
        scenario="B",
        description="Prioriza a los docentes (menos huecos, carga equilibrada y cumplimiento de preferencias)",
        student_gaps=3,
        student_excessive_day=4,
        course_spread=4,
        teacher_gaps=10,
        teacher_balance=10,
        room_utilization=3,
        preferences=10,
    ),
    "C": ScenarioWeights(
        scenario="C",
        description="Busca el equilibrio entre estudiantes, docentes y uso eficiente de aulas/laboratorios",
        student_gaps=6,
        student_excessive_day=8,
        course_spread=6,
        teacher_gaps=6,
        teacher_balance=6,
        room_utilization=4,
        preferences=6,
    ),
}


def get_scenario_weights(scenario: str) -> ScenarioWeights:
    key = scenario.upper()
    if key not in SCENARIOS:
        raise ValueError(f"Escenario desconocido: {scenario}. Use 'A', 'B' o 'C'.")
    return SCENARIOS[key]  # type: ignore[index]


class Teacher(BaseModel):
    id: str
    name: str
    department: str = "ITIID"
    max_daily_hours: int = 6
    unavailable_slots: list[tuple[int, int]] = Field(default_factory=list)
    shared_occupied_slots: list[tuple[int, int]] = Field(default_factory=list)
    shared_with: list[str] = Field(default_factory=list)

    def blocked_slots(self) -> set[tuple[int, int]]:
        return set(self.unavailable_slots) | set(self.shared_occupied_slots)


class Group(BaseModel):
    id: str
    name: str
    quarter: int = 7
    size: int = 30
    max_daily_hours: int = 6


class Room(BaseModel):
    id: str
    name: str
    capacity: int = 35
    room_type: RoomType = "aula"
    equipment: list[str] = Field(default_factory=list)
    unavailable_slots: list[tuple[int, int]] = Field(default_factory=list)
    shared_occupied_slots: list[tuple[int, int]] = Field(default_factory=list)
    shared_with: list[str] = Field(default_factory=list)

    def blocked_slots(self) -> set[tuple[int, int]]:
        return set(self.unavailable_slots) | set(self.shared_occupied_slots)


class Course(BaseModel):
    id: str
    name: str
    group_id: str
    extra_group_ids: list[str] = Field(default_factory=list)
    teacher_id: str
    sessions_per_week: int
    room_type: RoomType = "aula"
    required_equipment: list[str] = Field(default_factory=list)
    max_sessions_per_day: int = 2
    quarter: int = 7

    def all_group_ids(self) -> list[str]:
        """Devuelve todos los grupos/curricula asociados (soporte multi-curriculum ITC-2007)."""
        seen: list[str] = [self.group_id]
        for gid in self.extra_group_ids:
            if gid not in seen:
                seen.append(gid)
        return seen


class Preference(BaseModel):
    id: str
    teacher_id: str
    pref_type: PrefType
    params: dict[str, Any] = Field(default_factory=dict)
    hard: bool = False
    weight: int = 1
    source_text: str = ""
    status: PrefStatus = "approved"


class Instance(BaseModel):
    id: str
    name: str
    description: str = ""
    days: list[str] = Field(
        default_factory=lambda: ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
    )
    slot_labels: list[str] = Field(
        default_factory=lambda: [
            "07:00-08:00",
            "08:00-09:00",
            "09:00-10:00",
            "10:00-11:00",
            "11:00-12:00",
            "12:00-13:00",
            "13:00-14:00",
            "14:00-15:00",
        ]
    )
    recess_slots: list[int] = Field(default_factory=list)
    morning_cutoff_slot: int = 4  # slots < 4 son manana, >= 4 son tarde/mediodia
    teachers: list[Teacher] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    courses: list[Course] = Field(default_factory=list)
    preferences: list[Preference] = Field(default_factory=list)

    @property
    def num_days(self) -> int:
        return len(self.days)

    @property
    def slots_per_day(self) -> int:
        return len(self.slot_labels)

    def valid_slots(self) -> list[int]:
        recess = set(self.recess_slots)
        return [s for s in range(self.slots_per_day) if s not in recess]

    def teacher_map(self) -> dict[str, Teacher]:
        return {t.id: t for t in self.teachers}

    def group_map(self) -> dict[str, Group]:
        return {g.id: g for g in self.groups}

    def room_map(self) -> dict[str, Room]:
        return {r.id: r for r in self.rooms}

    def course_map(self) -> dict[str, Course]:
        return {c.id: c for c in self.courses}

    def approved_preferences(self) -> list[Preference]:
        return [p for p in self.preferences if p.status == "approved"]

    def teacher_blocked_slots(self, teacher_id: str) -> set[tuple[int, int]]:
        t = self.teacher_map()[teacher_id]
        blocked = set(t.blocked_slots())
        for p in self.approved_preferences():
            if not p.hard or p.teacher_id != teacher_id:
                continue
            if p.pref_type == "avoid_day" and "day" in p.params:
                d = int(p.params["day"])
                for s in range(self.slots_per_day):
                    blocked.add((d, s))
            elif p.pref_type == "avoid_slot":
                s = int(p.params["slot"])
                if "day" in p.params and p.params["day"] is not None:
                    blocked.add((int(p.params["day"]), s))
                else:
                    for d in range(self.num_days):
                        blocked.add((d, s))
        return blocked

    def is_teacher_available(self, teacher_id: str, day: int, slot: int) -> bool:
        if slot in self.recess_slots:
            return False
        return (day, slot) not in self.teacher_blocked_slots(teacher_id)

    def is_room_available(self, room_id: str, day: int, slot: int) -> bool:
        if slot in self.recess_slots:
            return False
        r = self.room_map()[room_id]
        return (day, slot) not in r.blocked_slots()

    def is_room_suitable(self, course: Course, room: Room) -> bool:
        gmap = self.group_map()
        req_size = max((gmap[gid].size for gid in course.all_group_ids() if gid in gmap), default=0)
        if room.capacity < req_size:
            return False
        if course.room_type == "laboratorio" and room.room_type != "laboratorio":
            return False
        if course.required_equipment:
            room_eq = set(room.equipment)
            if not set(course.required_equipment).issubset(room_eq):
                return False
        return True

    def compatible_rooms(self, course_id: str) -> list[Room]:
        course = self.course_map()[course_id]
        return [r for r in self.rooms if self.is_room_suitable(course, r)]

    def total_sessions(self) -> int:
        return sum(c.sessions_per_week for c in self.courses)

    def to_json_file(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return p

    @classmethod
    def from_json_file(cls, path: str | Path) -> "Instance":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)


class Assignment(BaseModel):
    course_id: str
    session_idx: int
    day: int
    slot: int
    room_id: str


class Schedule(BaseModel):
    instance_id: str
    method: str
    scenario: ScenarioId = "C"
    assignments: list[Assignment] = Field(default_factory=list)
    solve_time_sec: float = 0.0
    seed: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_json_file(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return p

    @classmethod
    def from_json_file(cls, path: str | Path) -> "Schedule":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)
