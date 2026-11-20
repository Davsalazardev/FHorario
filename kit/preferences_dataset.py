"""Conjunto de preferencias docentes en espanol y anotaciones de referencia (Gold Standard).

Sirve para evaluar R5 (LLM extractor) con Precision, Recall y F1 por tipo de restriccion
(Pregunta guia P2 del informe final). Todos los docentes estan anonimizados.
"""

from __future__ import annotations

from typing import Any

from kit.schema import Preference


# 25 enunciados realistas en espanol escritos por docentes (anonimizados)
# Cada entrada incluye el texto original, docente, y la lista esperada de restricciones estructuradas.
ANNOTATED_PREFERENCES_DATASET: list[dict[str, Any]] = [
    {
        "id": "PREF_01",
        "teacher_id": "DOC_01",
        "text": "Prefiero no tener clases los viernes porque tengo reuniones de academia.",
        "expected": [{"pref_type": "avoid_day", "params": {"day": 4}, "hard": False}],
    },
    {
        "id": "PREF_02",
        "teacher_id": "DOC_02",
        "text": "Por favor evitar la primera hora de 7:00 a 8:00 todos los dias.",
        "expected": [{"pref_type": "avoid_slot", "params": {"slot": 0}, "hard": False}],
    },
    {
        "id": "PREF_03",
        "teacher_id": "DOC_03",
        "text": "Me conviene dar clases por la manana, antes de las 11:00.",
        "expected": [{"pref_type": "prefer_morning", "params": {}, "hard": False}],
    },
    {
        "id": "PREF_04",
        "teacher_id": "DOC_04",
        "text": "Quisiera dar mis materias preferentemente en la tarde despues de las 11:00.",
        "expected": [{"pref_type": "prefer_afternoon", "params": {}, "hard": False}],
    },
    {
        "id": "PREF_05",
        "teacher_id": "DOC_05",
        "text": "No programar mas de 3 horas diarias de clase para poder atender tutorias.",
        "expected": [{"pref_type": "max_daily_hours", "params": {"max_hours": 3}, "hard": False}],
    },
    {
        "id": "PREF_06",
        "teacher_id": "DOC_06",
        "text": "Necesito un horario compacto sin huecos entre mis clases.",
        "expected": [{"pref_type": "compact_schedule", "params": {}, "hard": False}],
    },
    {
        "id": "PREF_07",
        "teacher_id": "DOC_07",
        "text": "De preferencia asignarme laboratorio para mis sesiones practicas.",
        "expected": [{"pref_type": "prefer_room_type", "params": {"room_type": "laboratorio"}, "hard": False}],
    },
    {
        "id": "PREF_08",
        "teacher_id": "DOC_08",
        "text": "El lunes no puedo a primera hora (07:00-08:00) y prefiero bloques seguidos sin huecos.",
        "expected": [
            {"pref_type": "avoid_slot", "params": {"day": 0, "slot": 0}, "hard": False},
            {"pref_type": "compact_schedule", "params": {}, "hard": False},
        ],
    },
    {
        "id": "PREF_09",
        "teacher_id": "DOC_01",
        "text": "Evitar la ultima franja de 14:00 a 15:00 de ser posible.",
        "expected": [{"pref_type": "avoid_slot", "params": {"slot": 7}, "hard": False}],
    },
    {
        "id": "PREF_10",
        "teacher_id": "DOC_02",
        "text": "Como maximo 4 horas al dia y preferiblemente en horario matutino.",
        "expected": [
            {"pref_type": "max_daily_hours", "params": {"max_hours": 4}, "hard": False},
            {"pref_type": "prefer_morning", "params": {}, "hard": False},
        ],
    },
    {
        "id": "PREF_11",
        "teacher_id": "DOC_03",
        "text": "Si se puede, dejarme libre el dia jueves.",
        "expected": [{"pref_type": "avoid_day", "params": {"day": 3}, "hard": False}],
    },
    {
        "id": "PREF_12",
        "teacher_id": "DOC_04",
        "text": "Prefiero impartir en aula normal en lugar de laboratorio.",
        "expected": [{"pref_type": "prefer_room_type", "params": {"room_type": "aula"}, "hard": False}],
    },
    {
        "id": "PREF_13",
        "teacher_id": "DOC_05",
        "text": "No me pongan clase los lunes, por favor.",
        "expected": [{"pref_type": "avoid_day", "params": {"day": 0}, "hard": False}],
    },
    {
        "id": "PREF_14",
        "teacher_id": "DOC_06",
        "text": "Preferiria turno vespertino (tarde) y no mas de 4 horas por dia.",
        "expected": [
            {"pref_type": "prefer_afternoon", "params": {}, "hard": False},
            {"pref_type": "max_daily_hours", "params": {"max_hours": 4}, "hard": False},
        ],
    },
    {
        "id": "PREF_15",
        "teacher_id": "DOC_07",
        "text": "Evitar huecos largos entre clases; prefiero horario corrido.",
        "expected": [{"pref_type": "compact_schedule", "params": {}, "hard": False}],
    },
    {
        "id": "PREF_16",
        "teacher_id": "DOC_08",
        "text": "No quiero dar clase los miercoles en la franja de 13:00 a 14:00.",
        "expected": [{"pref_type": "avoid_slot", "params": {"day": 2, "slot": 6}, "hard": False}],
    },
    {
        "id": "PREF_17",
        "teacher_id": "DOC_09",
        "text": "Solicito trabajar solo por la manana y evitar los viernes.",
        "expected": [
            {"pref_type": "prefer_morning", "params": {}, "hard": False},
            {"pref_type": "avoid_day", "params": {"day": 4}, "hard": False},
        ],
    },
    {
        "id": "PREF_18",
        "teacher_id": "DOC_10",
        "text": "El martes evitar dar clase si es posible.",
        "expected": [{"pref_type": "avoid_day", "params": {"day": 1}, "hard": False}],
    },
    {
        "id": "PREF_19",
        "teacher_id": "DOC_11",
        "text": "Tope de 3 horas diarias para equilibrar con investigacion.",
        "expected": [{"pref_type": "max_daily_hours", "params": {"max_hours": 3}, "hard": False}],
    },
    {
        "id": "PREF_20",
        "teacher_id": "DOC_12",
        "text": "Quisiera evitar la franja de las 07:00 los viernes.",
        "expected": [{"pref_type": "avoid_slot", "params": {"day": 4, "slot": 0}, "hard": False}],
    },
    {
        "id": "PREF_21",
        "teacher_id": "DOC_09",
        "text": "De preferencia usar laboratorio de computo.",
        "expected": [{"pref_type": "prefer_room_type", "params": {"room_type": "laboratorio"}, "hard": False}],
    },
    {
        "id": "PREF_22",
        "teacher_id": "DOC_10",
        "text": "Clases seguidas sin horas muertas por favor.",
        "expected": [{"pref_type": "compact_schedule", "params": {}, "hard": False}],
    },
    {
        "id": "PREF_23",
        "teacher_id": "DOC_11",
        "text": "Prefiero turno de tarde para todas mis materias.",
        "expected": [{"pref_type": "prefer_afternoon", "params": {}, "hard": False}],
    },
    {
        "id": "PREF_24",
        "teacher_id": "DOC_12",
        "text": "No exceder 5 horas en un mismo dia.",
        "expected": [{"pref_type": "max_daily_hours", "params": {"max_hours": 5}, "hard": False}],
    },
    {
        "id": "PREF_25",
        "teacher_id": "DOC_01",
        "text": "Definitivamente no puedo los miercoles por comision academica (obligatorio).",
        "expected": [{"pref_type": "avoid_day", "params": {"day": 2}, "hard": True}],
    },
]


def build_preferences_for_teachers(teacher_ids: set[str], include_hard: bool = False) -> list[Preference]:
    """Convierte las anotaciones del dataset en objetos Preference para los docentes presentes."""
    prefs: list[Preference] = []
    idx = 1
    for item in ANNOTATED_PREFERENCES_DATASET:
        tid = item["teacher_id"]
        if tid not in teacher_ids:
            continue
        for exp in item["expected"]:
            if exp.get("hard", False) and not include_hard:
                continue
            prefs.append(
                Preference(
                    id=f"P_{idx:02d}",
                    teacher_id=tid,
                    pref_type=exp["pref_type"],
                    params=dict(exp["params"]),
                    hard=bool(exp.get("hard", False)),
                    weight=1,
                    source_text=item["text"],
                    status="approved",
                )
            )
            idx += 1
    return prefs
