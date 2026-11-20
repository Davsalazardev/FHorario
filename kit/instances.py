"""Instancias oficiales de prueba (I1, I2, I3, I4) y parser ITC-2007 (.ctt).

- I1: Instancia publica estilo ITC-2007 (formato .ctt estandar).
- I2: Un cuatrimestre de ITIID (7o cuatrimestre, 2 grupos) + horario manual de referencia.
- I3: Todos los cuatrimestres activos de ITIID (1o, 4o y 7o, 6 grupos) con recursos
      compartidos bloqueados (docentes y laboratorios compartidos con otros programas)
      + horario manual de referencia.
- I4: Escenario de cambio derivado de I3 ("el docente DOC_01 ya no puede los miercoles")
      para medir reprogramacion y estabilidad.
"""

from __future__ import annotations

import copy
import random
from pathlib import Path

from kit.preferences_dataset import build_preferences_for_teachers
from kit.schema import Assignment, Course, Group, Instance, Preference, Room, Schedule, Teacher


ITC2007_SAMPLE_CTT = """Name: comp01_itc2007_mini
Courses: 12
Rooms: 4
Days: 5
Periods_per_day: 6
Curricula: 3
Constraints: 6

COURSES:
c0001 t000 4 3 30
c0002 t001 4 3 30
c0003 t002 3 3 30
c0004 t003 3 2 30
c0005 t000 3 2 28
c0006 t004 4 3 28
c0007 t001 3 2 28
c0008 t005 4 3 28
c0009 t002 4 3 25
c0010 t003 4 3 25
c0011 t004 3 2 25
c0012 t005 3 2 25

ROOMS:
R1 35
R2 35
R3 30
R4 30

CURRICULA:
q000 4 c0001 c0002 c0003 c0004
q001 4 c0005 c0006 c0007 c0008
q002 4 c0009 c0010 c0011 c0012

UNAVAILABILITY_CONSTRAINTS:
c0001 0 0
c0002 4 5
c0006 2 3
c0008 1 0
c0010 3 5
c0012 4 4

END.
"""


def parse_itc2007_ctt(ctt_text: str, instance_id: str = "I1") -> Instance:
    """Parsea una instancia en formato estandar CB-CTT de ITC-2007 (.ctt) al esquema JSON comun."""
    lines = [ln.strip() for ln in ctt_text.splitlines() if ln.strip()]
    meta: dict[str, str] = {}
    section = None
    raw_courses: list[tuple[str, str, int, int, int]] = []
    raw_rooms: list[tuple[str, int]] = []
    curricula: dict[str, list[str]] = {}
    unavail: list[tuple[str, int, int]] = []

    for ln in lines:
        if ln == "END.":
            break
        if ln in ("COURSES:", "ROOMS:", "CURRICULA:", "UNAVAILABILITY_CONSTRAINTS:"):
            section = ln[:-1]
            continue
        if section is None and ":" in ln:
            k, v = ln.split(":", 1)
            meta[k.strip()] = v.strip()
            continue

        parts = ln.split()
        if section == "COURSES":
            cid, tid, lectures, _min_days, students = (
                parts[0],
                parts[1],
                int(parts[2]),
                int(parts[3]),
                int(parts[4]),
            )
            raw_courses.append((cid, tid, lectures, _min_days, students))
        elif section == "ROOMS":
            raw_rooms.append((parts[0], int(parts[1])))
        elif section == "CURRICULA":
            qid = parts[0]
            curricula[qid] = parts[2:]
        elif section == "UNAVAILABILITY_CONSTRAINTS":
            unavail.append((parts[0], int(parts[1]), int(parts[2])))

    num_days = int(meta.get("Days", "5"))
    periods = int(meta.get("Periods_per_day", "6"))
    day_names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"][:num_days]
    slot_labels = [f"{7 + s:02d}:00-{8 + s:02d}:00" for s in range(periods)]

    # Mapear curso -> curriculum (grupo)
    course_to_group: dict[str, str] = {}
    group_sizes: dict[str, int] = {}
    course_students = {c[0]: c[4] for c in raw_courses}
    for qid, cids in curricula.items():
        for cid in cids:
            course_to_group[cid] = qid
        group_sizes[qid] = max((course_students.get(cid, 25) for cid in cids), default=25)

    # Mapear indisponibilidades de curso a su docente
    course_teacher = {c[0]: c[1] for c in raw_courses}
    teacher_unavail: dict[str, list[tuple[int, int]]] = {}
    for cid, d, p in unavail:
        tid = course_teacher.get(cid)
        if tid:
            teacher_unavail.setdefault(tid, []).append((d, p))

    teacher_ids = sorted({c[1] for c in raw_courses})
    teachers = [
        Teacher(
            id=tid,
            name=f"Docente {tid}",
            department="ITC-2007",
            max_daily_hours=periods,
            unavailable_slots=sorted(set(teacher_unavail.get(tid, []))),
        )
        for tid in teacher_ids
    ]
    groups = [
        Group(
            id=qid,
            name=f"Curriculum {qid}",
            quarter=1,
            size=group_sizes[qid],
            max_daily_hours=min(periods, 5),
        )
        for qid in sorted(curricula.keys())
    ]
    rooms = [
        Room(id=rid, name=f"Aula {rid}", capacity=cap, room_type="aula")
        for rid, cap in raw_rooms
    ]
    courses = [
        Course(
            id=cid,
            name=f"Curso {cid}",
            group_id=course_to_group.get(cid, groups[0].id),
            teacher_id=tid,
            sessions_per_week=lectures,
            room_type="aula",
            max_sessions_per_day=2,
            quarter=1,
        )
        for cid, tid, lectures, _md, _st in raw_courses
    ]

    return Instance(
        id=instance_id,
        name=f"I1 - ITC-2007 ({meta.get('Name', 'benchmark')})",
        description="Instancia publica estilo CB-CTT ITC-2007 para validar motores antes de datos ITIID.",
        days=day_names,
        slot_labels=slot_labels,
        recess_slots=[],
        morning_cutoff_slot=periods // 2,
        teachers=teachers,
        groups=groups,
        rooms=rooms,
        courses=courses,
        preferences=[],
    )


def build_instance_i1(data_dir: Path | None = None) -> Instance:
    """Carga o genera la instancia I1 (ITC-2007)."""
    if data_dir is not None:
        ctt_path = data_dir / "itc2007" / "comp01_mini.ctt"
        if ctt_path.exists():
            return parse_itc2007_ctt(ctt_path.read_text(encoding="utf-8"), instance_id="I1")
    return parse_itc2007_ctt(ITC2007_SAMPLE_CTT, instance_id="I1")


def build_instance_i2() -> Instance:
    """Instancia I2: Un cuatrimestre de ITIID (7o cuatrimestre, periodo cerrado, anonimizado)."""
    teachers = [
        Teacher(
            id="DOC_01",
            name="Docente 01 (IA / Optimizacion)",
            max_daily_hours=5,
            unavailable_slots=[(0, 7), (4, 7)],
            shared_occupied_slots=[(1, 0), (1, 1)],
            shared_with=["IM"],
        ),
        Teacher(
            id="DOC_02",
            name="Docente 02 (Ingenieria de Software)",
            max_daily_hours=5,
            unavailable_slots=[(0, 0)],
            shared_occupied_slots=[(3, 6), (3, 7)],
            shared_with=["ISA"],
        ),
        Teacher(
            id="DOC_03",
            name="Docente 03 (Redes y Seguridad)",
            max_daily_hours=5,
            unavailable_slots=[(4, 6), (4, 7)],
        ),
        Teacher(
            id="DOC_04",
            name="Docente 04 (Bases de Datos)",
            max_daily_hours=5,
            unavailable_slots=[(1, 7)],
        ),
        Teacher(
            id="DOC_05",
            name="Docente 05 (Desarrollo Web/Movil)",
            max_daily_hours=5,
            unavailable_slots=[(2, 0)],
            shared_occupied_slots=[(0, 5), (0, 6)],
            shared_with=["LAYGE"],
        ),
        Teacher(
            id="DOC_06",
            name="Docente 06 (Ingles / Habilidades)",
            max_daily_hours=6,
            unavailable_slots=[],
            shared_occupied_slots=[(2, 2), (2, 3), (4, 2)],
            shared_with=["IM", "ISA"],
        ),
    ]

    groups = [
        Group(id="ITIID_7A", name="ITIID 7-A", quarter=7, size=32, max_daily_hours=6),
        Group(id="ITIID_7B", name="ITIID 7-B", quarter=7, size=28, max_daily_hours=6),
    ]

    rooms = [
        Room(id="AULA_01", name="Aula B-101", capacity=35, room_type="aula"),
        Room(id="AULA_02", name="Aula B-102", capacity=35, room_type="aula"),
        Room(
            id="LAB_01",
            name="Lab Computo 1",
            capacity=35,
            room_type="laboratorio",
            equipment=["pc", "proyector", "linux"],
            shared_occupied_slots=[(0, 2), (0, 3), (2, 4)],
            shared_with=["IM"],
        ),
        Room(
            id="LAB_02",
            name="Lab Redes y Datos",
            capacity=34,
            room_type="laboratorio",
            equipment=["pc", "redes", "proyector"],
            shared_occupied_slots=[(1, 4), (1, 5)],
            shared_with=["ISA"],
        ),
    ]

    courses = [
        # Grupo 7A (25 horas/semana)
        Course(id="C_7A_FIA", name="Fundamentos de IA", group_id="ITIID_7A", teacher_id="DOC_01", sessions_per_week=5, room_type="laboratorio", required_equipment=["pc"]),
        Course(id="C_7A_ISW", name="Ingenieria de Software", group_id="ITIID_7A", teacher_id="DOC_02", sessions_per_week=4, room_type="aula"),
        Course(id="C_7A_RED", name="Seguridad en Redes", group_id="ITIID_7A", teacher_id="DOC_03", sessions_per_week=4, room_type="laboratorio", required_equipment=["redes"]),
        Course(id="C_7A_BDA", name="Bases de Datos Avanzadas", group_id="ITIID_7A", teacher_id="DOC_04", sessions_per_week=4, room_type="laboratorio", required_equipment=["pc"]),
        Course(id="C_7A_WEB", name="Programacion Web II", group_id="ITIID_7A", teacher_id="DOC_05", sessions_per_week=4, room_type="laboratorio", required_equipment=["pc"]),
        Course(id="C_7A_ING", name="Ingles VII", group_id="ITIID_7A", teacher_id="DOC_06", sessions_per_week=4, room_type="aula"),
        # Grupo 7B (25 horas/semana)
        Course(id="C_7B_FIA", name="Fundamentos de IA", group_id="ITIID_7B", teacher_id="DOC_01", sessions_per_week=5, room_type="laboratorio", required_equipment=["pc"]),
        Course(id="C_7B_ISW", name="Ingenieria de Software", group_id="ITIID_7B", teacher_id="DOC_02", sessions_per_week=4, room_type="aula"),
        Course(id="C_7B_RED", name="Seguridad en Redes", group_id="ITIID_7B", teacher_id="DOC_03", sessions_per_week=4, room_type="laboratorio", required_equipment=["redes"]),
        Course(id="C_7B_BDA", name="Bases de Datos Avanzadas", group_id="ITIID_7B", teacher_id="DOC_04", sessions_per_week=4, room_type="laboratorio", required_equipment=["pc"]),
        Course(id="C_7B_WEB", name="Programacion Web II", group_id="ITIID_7B", teacher_id="DOC_05", sessions_per_week=4, room_type="laboratorio", required_equipment=["pc"]),
        Course(id="C_7B_ING", name="Ingles VII", group_id="ITIID_7B", teacher_id="DOC_06", sessions_per_week=4, room_type="aula"),
    ]

    tids = {t.id for t in teachers}
    prefs = build_preferences_for_teachers(tids, include_hard=False)

    return Instance(
        id="I2",
        name="I2 - ITIID 7o Cuatrimestre (Periodo cerrado)",
        description="Caso pequeno (1 cuatrimestre, 2 grupos, 12 materias, 50 sesiones) con horario manual de referencia.",
        teachers=teachers,
        groups=groups,
        rooms=rooms,
        courses=courses,
        preferences=prefs,
    )


def build_instance_i3() -> Instance:
    """Instancia I3: Todos los cuatrimestres activos de ITIID con recursos compartidos bloqueados."""
    teachers = [
        Teacher(id="DOC_01", name="Docente 01 (IA)", max_daily_hours=5, unavailable_slots=[(0, 7)], shared_occupied_slots=[(1, 0), (1, 1)], shared_with=["IM"]),
        Teacher(id="DOC_02", name="Docente 02 (Ing. Software)", max_daily_hours=5, unavailable_slots=[(0, 0)], shared_occupied_slots=[(3, 6), (3, 7)], shared_with=["ISA"]),
        Teacher(id="DOC_03", name="Docente 03 (Redes)", max_daily_hours=5, unavailable_slots=[(4, 6), (4, 7)]),
        Teacher(id="DOC_04", name="Docente 04 (Bases de Datos)", max_daily_hours=5, unavailable_slots=[(1, 7)]),
        Teacher(id="DOC_05", name="Docente 05 (Web/Movil)", max_daily_hours=5, unavailable_slots=[(2, 0)], shared_occupied_slots=[(0, 5), (0, 6)], shared_with=["LAYGE"]),
        Teacher(id="DOC_06", name="Docente 06 (Ingles I)", max_daily_hours=6, shared_occupied_slots=[(2, 2), (2, 3), (4, 2)], shared_with=["IM", "ISA"]),
        Teacher(id="DOC_07", name="Docente 07 (Programacion)", max_daily_hours=5, unavailable_slots=[(4, 7)], shared_occupied_slots=[(0, 0), (0, 1)], shared_with=["IM"]),
        Teacher(id="DOC_08", name="Docente 08 (Matematicas)", max_daily_hours=5, unavailable_slots=[(3, 0)], shared_occupied_slots=[(1, 4), (1, 5), (3, 4)], shared_with=["IM", "ITM"]),
        Teacher(id="DOC_09", name="Docente 09 (Sistemas Operativos)", max_daily_hours=5, unavailable_slots=[(0, 7), (2, 7)]),
        Teacher(id="DOC_10", name="Docente 10 (Estructuras Datos)", max_daily_hours=5, unavailable_slots=[(4, 0)]),
        Teacher(id="DOC_11", name="Docente 11 (Arquitectura/Cloud)", max_daily_hours=5, shared_occupied_slots=[(2, 5), (2, 6)], shared_with=["ITM"]),
        Teacher(id="DOC_12", name="Docente 12 (Ingles II / Expresion)", max_daily_hours=6, shared_occupied_slots=[(1, 2), (1, 3), (3, 2)], shared_with=["LAYGE"]),
    ]

    groups = [
        Group(id="ITIID_1A", name="ITIID 1-A", quarter=1, size=36, max_daily_hours=6),
        Group(id="ITIID_1B", name="ITIID 1-B", quarter=1, size=34, max_daily_hours=6),
        Group(id="ITIID_4A", name="ITIID 4-A", quarter=4, size=31, max_daily_hours=6),
        Group(id="ITIID_4B", name="ITIID 4-B", quarter=4, size=29, max_daily_hours=6),
        Group(id="ITIID_7A", name="ITIID 7-A", quarter=7, size=30, max_daily_hours=6),
        Group(id="ITIID_7B", name="ITIID 7-B", quarter=7, size=27, max_daily_hours=6),
    ]

    rooms = [
        Room(id="AULA_01", name="Aula B-101", capacity=40, room_type="aula"),
        Room(id="AULA_02", name="Aula B-102", capacity=38, room_type="aula"),
        Room(id="AULA_03", name="Aula B-103", capacity=36, room_type="aula", shared_occupied_slots=[(0, 0), (2, 0), (4, 0)], shared_with=["LAYGE"]),
        Room(id="AULA_04", name="Aula B-104", capacity=35, room_type="aula", shared_occupied_slots=[(1, 6), (3, 6)], shared_with=["IM"]),
        Room(id="LAB_01", name="Lab Computo 1", capacity=38, room_type="laboratorio", equipment=["pc", "proyector", "linux"], shared_occupied_slots=[(0, 2), (0, 3), (2, 4)], shared_with=["IM"]),
        Room(id="LAB_02", name="Lab Redes y Datos", capacity=36, room_type="laboratorio", equipment=["pc", "redes", "proyector"], shared_occupied_slots=[(1, 4), (1, 5)], shared_with=["ISA"]),
        Room(id="LAB_03", name="Lab Desarrollo SW", capacity=36, room_type="laboratorio", equipment=["pc", "proyector", "linux"], shared_occupied_slots=[(3, 0), (3, 1)], shared_with=["ITM"]),
        Room(id="LAB_04", name="Lab Inteligencia Artificial", capacity=34, room_type="laboratorio", equipment=["pc", "gpu", "linux", "redes"], shared_occupied_slots=[(4, 4), (4, 5)], shared_with=["IM"]),
    ]

    courses = [
        # 1o Cuatrimestre (1A y 1B) - 22 sesiones c/u
        Course(id="C_1A_PRO", name="Introduccion a Programacion", group_id="ITIID_1A", teacher_id="DOC_07", sessions_per_week=5, room_type="laboratorio", required_equipment=["pc"], quarter=1),
        Course(id="C_1A_MAT", name="Matematicas Discretas", group_id="ITIID_1A", teacher_id="DOC_08", sessions_per_week=5, room_type="aula", quarter=1),
        Course(id="C_1A_ARQ", name="Fundamentos de TI", group_id="ITIID_1A", teacher_id="DOC_11", sessions_per_week=4, room_type="aula", quarter=1),
        Course(id="C_1A_SOP", name="Introduccion a SO", group_id="ITIID_1A", teacher_id="DOC_09", sessions_per_week=4, room_type="laboratorio", required_equipment=["linux"], quarter=1),
        Course(id="C_1A_ING", name="Ingles I", group_id="ITIID_1A", teacher_id="DOC_12", sessions_per_week=4, room_type="aula", quarter=1),

        Course(id="C_1B_PRO", name="Introduccion a Programacion", group_id="ITIID_1B", teacher_id="DOC_07", sessions_per_week=5, room_type="laboratorio", required_equipment=["pc"], quarter=1),
        Course(id="C_1B_MAT", name="Matematicas Discretas", group_id="ITIID_1B", teacher_id="DOC_08", sessions_per_week=5, room_type="aula", quarter=1),
        Course(id="C_1B_ARQ", name="Fundamentos de TI", group_id="ITIID_1B", teacher_id="DOC_11", sessions_per_week=4, room_type="aula", quarter=1),
        Course(id="C_1B_SOP", name="Introduccion a SO", group_id="ITIID_1B", teacher_id="DOC_09", sessions_per_week=4, room_type="laboratorio", required_equipment=["linux"], quarter=1),
        Course(id="C_1B_ING", name="Ingles I", group_id="ITIID_1B", teacher_id="DOC_12", sessions_per_week=4, room_type="aula", quarter=1),

        # 4o Cuatrimestre (4A y 4B) - 24 sesiones c/u
        Course(id="C_4A_EDD", name="Estructuras de Datos", group_id="ITIID_4A", teacher_id="DOC_10", sessions_per_week=5, room_type="laboratorio", required_equipment=["pc"], quarter=4),
        Course(id="C_4A_BDD", name="Diseno de Bases de Datos", group_id="ITIID_4A", teacher_id="DOC_04", sessions_per_week=4, room_type="laboratorio", required_equipment=["pc"], quarter=4),
        Course(id="C_4A_RED", name="Fundamentos de Redes", group_id="ITIID_4A", teacher_id="DOC_03", sessions_per_week=4, room_type="laboratorio", required_equipment=["redes"], quarter=4),
        Course(id="C_4A_WEB", name="Programacion Web I", group_id="ITIID_4A", teacher_id="DOC_05", sessions_per_week=4, room_type="laboratorio", required_equipment=["pc"], quarter=4),
        Course(id="C_4A_ISW", name="Analisis de Requisitos", group_id="ITIID_4A", teacher_id="DOC_02", sessions_per_week=3, room_type="aula", quarter=4),
        Course(id="C_4A_ING", name="Ingles IV", group_id="ITIID_4A", teacher_id="DOC_12", sessions_per_week=4, room_type="aula", quarter=4),

        Course(id="C_4B_EDD", name="Estructuras de Datos", group_id="ITIID_4B", teacher_id="DOC_10", sessions_per_week=5, room_type="laboratorio", required_equipment=["pc"], quarter=4),
        Course(id="C_4B_BDD", name="Diseno de Bases de Datos", group_id="ITIID_4B", teacher_id="DOC_04", sessions_per_week=4, room_type="laboratorio", required_equipment=["pc"], quarter=4),
        Course(id="C_4B_RED", name="Fundamentos de Redes", group_id="ITIID_4B", teacher_id="DOC_03", sessions_per_week=4, room_type="laboratorio", required_equipment=["redes"], quarter=4),
        Course(id="C_4B_WEB", name="Programacion Web I", group_id="ITIID_4B", teacher_id="DOC_05", sessions_per_week=4, room_type="laboratorio", required_equipment=["pc"], quarter=4),
        Course(id="C_4B_ISW", name="Analisis de Requisitos", group_id="ITIID_4B", teacher_id="DOC_02", sessions_per_week=3, room_type="aula", quarter=4),
        Course(id="C_4B_ING", name="Ingles IV", group_id="ITIID_4B", teacher_id="DOC_06", sessions_per_week=4, room_type="aula", quarter=4),

        # 7o Cuatrimestre (7A y 7B) - 25 sesiones c/u
        Course(id="C_7A_FIA", name="Fundamentos de IA", group_id="ITIID_7A", teacher_id="DOC_01", sessions_per_week=5, room_type="laboratorio", required_equipment=["pc"], quarter=7),
        Course(id="C_7A_ISW", name="Ingenieria de Software", group_id="ITIID_7A", teacher_id="DOC_02", sessions_per_week=4, room_type="aula", quarter=7),
        Course(id="C_7A_CLD", name="Computo en la Nube", group_id="ITIID_7A", teacher_id="DOC_11", sessions_per_week=4, room_type="laboratorio", required_equipment=["linux"], quarter=7),
        Course(id="C_7A_SOP", name="Sistemas Distribuidos", group_id="ITIID_7A", teacher_id="DOC_09", sessions_per_week=4, room_type="laboratorio", required_equipment=["linux"], quarter=7),
        Course(id="C_7A_WEB", name="Aplicaciones Moviles", group_id="ITIID_7A", teacher_id="DOC_05", sessions_per_week=4, room_type="laboratorio", required_equipment=["pc"], quarter=7),
        Course(id="C_7A_ING", name="Ingles VII", group_id="ITIID_7A", teacher_id="DOC_06", sessions_per_week=4, room_type="aula", quarter=7),

        Course(id="C_7B_FIA", name="Fundamentos de IA", group_id="ITIID_7B", teacher_id="DOC_01", sessions_per_week=5, room_type="laboratorio", required_equipment=["pc"], quarter=7),
        Course(id="C_7B_ISW", name="Ingenieria de Software", group_id="ITIID_7B", teacher_id="DOC_02", sessions_per_week=4, room_type="aula", quarter=7),
        Course(id="C_7B_CLD", name="Computo en la Nube", group_id="ITIID_7B", teacher_id="DOC_11", sessions_per_week=4, room_type="laboratorio", required_equipment=["linux"], quarter=7),
        Course(id="C_7B_SOP", name="Sistemas Distribuidos", group_id="ITIID_7B", teacher_id="DOC_09", sessions_per_week=4, room_type="laboratorio", required_equipment=["linux"], quarter=7),
        Course(id="C_7B_EDD", name="Mineria de Datos", group_id="ITIID_7B", teacher_id="DOC_10", sessions_per_week=4, room_type="laboratorio", required_equipment=["pc"], quarter=7),
        Course(id="C_7B_ING", name="Ingles VII", group_id="ITIID_7B", teacher_id="DOC_06", sessions_per_week=4, room_type="aula", quarter=7),
    ]

    tids = {t.id for t in teachers}
    prefs = build_preferences_for_teachers(tids, include_hard=False)

    return Instance(
        id="I3",
        name="I3 - Programa ITIID Completo (1o, 4o y 7o cuatrimestre)",
        description="Caso completo del programa ITIID (6 grupos, 12 docentes, 8 aulas/labs, 34 materias, 142 sesiones) con recursos compartidos bloqueados.",
        teachers=teachers,
        groups=groups,
        rooms=rooms,
        courses=courses,
        preferences=prefs,
    )


def build_instance_i4(base_instance: Instance | None = None, teacher_id: str = "DOC_01", blocked_day: int = 2) -> Instance:
    """Instancia I4: Escenario de cambio ('el docente A ya no puede los miercoles')."""
    base = copy.deepcopy(base_instance or build_instance_i3())
    base.id = "I4"
    day_name = base.days[blocked_day] if 0 <= blocked_day < len(base.days) else f"Dia {blocked_day}"
    base.name = f"I4 - Escenario de cambio ({teacher_id} bloqueado {day_name})"
    base.description = (
        f"Reprogramacion sobre {base_instance.id if base_instance else 'I3'}: "
        f"el docente {teacher_id} ya no puede los {day_name.lower()}s. Mover lo menos posible."
    )
    # Agregar restriccion dura de indisponibilidad ese dia
    for t in base.teachers:
        if t.id == teacher_id:
            existing = set(t.unavailable_slots)
            for s in range(base.slots_per_day):
                existing.add((blocked_day, s))
            t.unavailable_slots = sorted(existing)
            break

    base.preferences.append(
        Preference(
            id="P_CHANGE_I4",
            teacher_id=teacher_id,
            pref_type="avoid_day",
            params={"day": blocked_day},
            hard=True,
            weight=1,
            source_text=f"Cambio de ultimo momento: el docente {teacher_id} ya no puede los {day_name.lower()}s.",
            status="approved",
        )
    )
    return base


def build_manual_reference_schedule(instance: Instance, seed: int = 42) -> Schedule:
    """Genera un horario 'manual de coordinacion' factible (0 violaciones duras)
    mediante asignacion voraz tipo prueba-y-error, simulando un horario armado a mano
    (valido en duras, pero con huecos y menor optimizacion en blandas)."""
    rng = random.Random(seed)
    valid_slots = instance.valid_slots()
    t_used: set[tuple[str, int, int]] = set()
    g_used: set[tuple[str, int, int]] = set()
    r_used: set[tuple[str, int, int]] = set()

    # Pre-cargar bloqueos
    for t in instance.teachers:
        for d, s in instance.teacher_blocked_slots(t.id):
            t_used.add((t.id, d, s))
    for r in instance.rooms:
        for d, s in r.blocked_slots():
            r_used.add((r.id, d, s))

    # Construir lista de sesiones ordenadas por escasez de aulas
    sessions: list[tuple[str, int]] = []
    sorted_courses = sorted(
        instance.courses,
        key=lambda c: (len(instance.compatible_rooms(c.id)), -c.sessions_per_week, c.id),
    )
    for c in sorted_courses:
        for idx in range(c.sessions_per_week):
            sessions.append((c.id, idx))

    course_map = instance.course_map()
    assignments: list[Assignment] = []

    # Generacion voraz con orden pseudoaleatorio (tipico armado manual por prueba y error)
    all_day_slots = [(d, s) for d in range(instance.num_days) for s in valid_slots]
    for cid, sidx in sessions:
        c = course_map[cid]
        comp_rooms = instance.compatible_rooms(cid)
        candidates = list(all_day_slots)
        rng.shuffle(candidates)
        placed = False
        for d, s in candidates:
            if (c.teacher_id, d, s) in t_used or (c.group_id, d, s) in g_used:
                continue
            rooms_shuffled = list(comp_rooms)
            rng.shuffle(rooms_shuffled)
            for r in rooms_shuffled:
                if (r.id, d, s) not in r_used:
                    assignments.append(
                        Assignment(course_id=cid, session_idx=sidx, day=d, slot=s, room_id=r.id)
                    )
                    t_used.add((c.teacher_id, d, s))
                    g_used.add((c.group_id, d, s))
                    r_used.add((r.id, d, s))
                    placed = True
                    break
            if placed:
                break

    return Schedule(
        instance_id=instance.id,
        method="Horario manual (coordinacion)",
        scenario="C",
        assignments=assignments,
        solve_time_sec=0.0,
        seed=seed,
        metadata={"source": "manual_reference_baseline"},
    )


def materialize_all_kit_files(data_dir: Path) -> dict[str, Path]:
    """Escribe en disco las instancias I1-I4, el .ctt de ITC-2007 y horarios manuales de referencia."""
    data_dir.mkdir(parents=True, exist_ok=True)
    itc_dir = data_dir / "itc2007"
    itc_dir.mkdir(parents=True, exist_ok=True)
    ctt_path = itc_dir / "comp01_mini.ctt"
    ctt_path.write_text(ITC2007_SAMPLE_CTT, encoding="utf-8")

    i1 = build_instance_i1(data_dir)
    i2 = build_instance_i2()
    i3 = build_instance_i3()
    i4 = build_instance_i4(i3)

    paths = {
        "I1_ctt": ctt_path,
        "I1": i1.to_json_file(data_dir / "instances" / "I1_itc2007.json"),
        "I2": i2.to_json_file(data_dir / "instances" / "I2_itiid_7q.json"),
        "I3": i3.to_json_file(data_dir / "instances" / "I3_itiid_completo.json"),
        "I4": i4.to_json_file(data_dir / "instances" / "I4_cambio_doc01.json"),
    }

    m2 = build_manual_reference_schedule(i2, seed=2026)
    m3 = build_manual_reference_schedule(i3, seed=2026)
    paths["manual_I2"] = m2.to_json_file(data_dir / "manual" / "manual_I2.json")
    paths["manual_I3"] = m3.to_json_file(data_dir / "manual" / "manual_I3.json")
    return paths
