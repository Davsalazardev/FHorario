"""R1: Importacion desde plantillas Excel -> JSON comun y exportacion a Excel.

Cumple con:
- Carga desde Excel con pandas y openpyxl.
- Revision de faltantes, duplicados e integridad referencial.
- Exportacion por sabana general de horario de ITIID, docente, grupo y aula.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from kit.schema import Course, Group, Instance, Preference, Room, Schedule, Teacher
from kit.validator import ValidationReport, validate_schedule


class ExcelValidationError(ValueError):
    """Error lanzado cuando la plantilla Excel tiene faltantes criticos o referencias invalidas."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Errores en plantilla Excel:\n- " + "\n- ".join(errors))


def _slots_to_str(slots: list[tuple[int, int]]) -> str:
    return ";".join(f"{d}:{s}" for d, s in slots)


def _str_to_slots(raw: Any) -> list[tuple[int, int]]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    text = str(raw).strip()
    if not text:
        return []
    out: list[tuple[int, int]] = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"Formato de franja invalido '{part}', esperado 'dia:franja'")
        d_str, s_str = part.split(":", 1)
        out.append((int(d_str.strip()), int(s_str.strip())))
    return out


def _list_to_str(items: list[str]) -> str:
    return ",".join(items)


def _str_to_list(raw: Any) -> list[str]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    text = str(raw).strip()
    if not text:
        return []
    return [x.strip() for x in text.split(",") if x.strip()]


def export_instance_template_excel(instance: Instance, output_path: str | Path) -> Path:
    """Exporta una Instance a la plantilla Excel oficial del proyecto."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    config_df = pd.DataFrame(
        [
            {"clave": "id", "valor": instance.id},
            {"clave": "name", "valor": instance.name},
            {"clave": "description", "valor": instance.description},
            {"clave": "days", "valor": ",".join(instance.days)},
            {"clave": "slot_labels", "valor": ",".join(instance.slot_labels)},
            {"clave": "recess_slots", "valor": ",".join(str(x) for x in instance.recess_slots)},
            {"clave": "morning_cutoff_slot", "valor": str(instance.morning_cutoff_slot)},
        ]
    )

    teachers_df = pd.DataFrame(
        [
            {
                "id": t.id,
                "name": t.name,
                "department": t.department,
                "max_daily_hours": t.max_daily_hours,
                "unavailable_slots": _slots_to_str(t.unavailable_slots),
                "shared_occupied_slots": _slots_to_str(t.shared_occupied_slots),
                "shared_with": _list_to_str(t.shared_with),
            }
            for t in instance.teachers
        ]
    )

    groups_df = pd.DataFrame(
        [
            {
                "id": g.id,
                "name": g.name,
                "quarter": g.quarter,
                "size": g.size,
                "max_daily_hours": g.max_daily_hours,
            }
            for g in instance.groups
        ]
    )

    rooms_df = pd.DataFrame(
        [
            {
                "id": r.id,
                "name": r.name,
                "capacity": r.capacity,
                "room_type": r.room_type,
                "equipment": _list_to_str(r.equipment),
                "unavailable_slots": _slots_to_str(r.unavailable_slots),
                "shared_occupied_slots": _slots_to_str(r.shared_occupied_slots),
                "shared_with": _list_to_str(r.shared_with),
            }
            for r in instance.rooms
        ]
    )

    courses_df = pd.DataFrame(
        [
            {
                "id": c.id,
                "name": c.name,
                "group_id": c.group_id,
                "teacher_id": c.teacher_id,
                "sessions_per_week": c.sessions_per_week,
                "room_type": c.room_type,
                "required_equipment": _list_to_str(c.required_equipment),
                "max_sessions_per_day": c.max_sessions_per_day,
                "quarter": c.quarter,
            }
            for c in instance.courses
        ]
    )

    prefs_df = pd.DataFrame(
        [
            {
                "id": p.id,
                "teacher_id": p.teacher_id,
                "pref_type": p.pref_type,
                "params_json": json.dumps(p.params, ensure_ascii=False),
                "hard": p.hard,
                "weight": p.weight,
                "source_text": p.source_text,
                "status": p.status,
            }
            for p in instance.preferences
        ]
        or [
            {
                "id": "",
                "teacher_id": "",
                "pref_type": "",
                "params_json": "{}",
                "hard": False,
                "weight": 1,
                "source_text": "",
                "status": "approved",
            }
        ]
    )

    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        config_df.to_excel(writer, sheet_name="Config", index=False)
        teachers_df.to_excel(writer, sheet_name="Docentes", index=False)
        groups_df.to_excel(writer, sheet_name="Grupos", index=False)
        rooms_df.to_excel(writer, sheet_name="Aulas", index=False)
        courses_df.to_excel(writer, sheet_name="Asignaturas", index=False)
        prefs_df.to_excel(writer, sheet_name="Preferencias", index=False)

    return out


def import_instance_from_excel(excel_path: str | Path) -> tuple[Instance, list[str]]:
    """Carga una instancia desde la plantilla Excel, revisa faltantes y construye el JSON comun."""
    path = Path(excel_path)
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo Excel: {path}")

    required_sheets = ["Config", "Docentes", "Grupos", "Aulas", "Asignaturas"]
    xl = pd.ExcelFile(path, engine="openpyxl")
    errors: list[str] = []
    warnings: list[str] = []

    for sh in required_sheets:
        if sh not in xl.sheet_names:
            errors.append(f"Falta la hoja obligatoria '{sh}'.")
    if errors:
        raise ExcelValidationError(errors)

    # 1. Config
    cfg_df = pd.read_excel(xl, sheet_name="Config")
    cfg: dict[str, str] = {}
    for _, row in cfg_df.iterrows():
        if pd.notna(row.get("clave")):
            val = "" if pd.isna(row.get("valor")) else str(row.get("valor")).strip()
            cfg[str(row["clave"]).strip()] = val

    days = [d.strip() for d in cfg.get("days", "Lunes,Martes,Miércoles,Jueves,Viernes").split(",") if d.strip()]
    slot_labels = [
        s.strip()
        for s in cfg.get(
            "slot_labels",
            "07:00-08:00,08:00-09:00,09:00-10:00,10:00-11:00,11:00-12:00,12:00-13:00,13:00-14:00,14:00-15:00",
        ).split(",")
        if s.strip()
    ]
    recess_raw = cfg.get("recess_slots", "")
    recess_slots = [int(x.strip()) for x in recess_raw.split(",") if x.strip()] if recess_raw else []
    morning_cutoff = int(cfg.get("morning_cutoff_slot", "4") or "4")

    # 2. Docentes
    t_df = pd.read_excel(xl, sheet_name="Docentes")
    teachers: list[Teacher] = []
    seen_t: set[str] = set()
    for idx, row in t_df.iterrows():
        tid = "" if pd.isna(row.get("id")) else str(row["id"]).strip()
        if not tid:
            errors.append(f"Docentes fila {idx + 2}: falta 'id'.")
            continue
        if tid in seen_t:
            errors.append(f"Docentes fila {idx + 2}: id duplicado '{tid}'.")
            continue
        seen_t.add(tid)
        name = tid if pd.isna(row.get("name")) else str(row["name"]).strip()
        try:
            unav = _str_to_slots(row.get("unavailable_slots"))
            shared = _str_to_slots(row.get("shared_occupied_slots"))
        except ValueError as exc:
            errors.append(f"Docentes '{tid}': {exc}")
            continue
        teachers.append(
            Teacher(
                id=tid,
                name=name,
                department="ITIID" if pd.isna(row.get("department")) else str(row["department"]).strip(),
                max_daily_hours=int(row["max_daily_hours"]) if pd.notna(row.get("max_daily_hours")) else 6,
                unavailable_slots=unav,
                shared_occupied_slots=shared,
                shared_with=_str_to_list(row.get("shared_with")),
            )
        )

    # 3. Grupos
    g_df = pd.read_excel(xl, sheet_name="Grupos")
    groups: list[Group] = []
    seen_g: set[str] = set()
    for idx, row in g_df.iterrows():
        gid = "" if pd.isna(row.get("id")) else str(row["id"]).strip()
        if not gid:
            errors.append(f"Grupos fila {idx + 2}: falta 'id'.")
            continue
        if gid in seen_g:
            errors.append(f"Grupos fila {idx + 2}: id duplicado '{gid}'.")
            continue
        seen_g.add(gid)
        if pd.isna(row.get("size")):
            errors.append(f"Grupos '{gid}': falta 'size' (tamano del grupo).")
            continue
        groups.append(
            Group(
                id=gid,
                name=gid if pd.isna(row.get("name")) else str(row["name"]).strip(),
                quarter=int(row["quarter"]) if pd.notna(row.get("quarter")) else 7,
                size=int(row["size"]),
                max_daily_hours=int(row["max_daily_hours"]) if pd.notna(row.get("max_daily_hours")) else 6,
            )
        )

    # 4. Aulas
    r_df = pd.read_excel(xl, sheet_name="Aulas")
    rooms: list[Room] = []
    seen_r: set[str] = set()
    for idx, row in r_df.iterrows():
        rid = "" if pd.isna(row.get("id")) else str(row["id"]).strip()
        if not rid:
            errors.append(f"Aulas fila {idx + 2}: falta 'id'.")
            continue
        if rid in seen_r:
            errors.append(f"Aulas fila {idx + 2}: id duplicado '{rid}'.")
            continue
        seen_r.add(rid)
        if pd.isna(row.get("capacity")):
            errors.append(f"Aulas '{rid}': falta 'capacity'.")
            continue
        rtype = "aula" if pd.isna(row.get("room_type")) else str(row["room_type"]).strip().lower()
        if rtype not in ("aula", "laboratorio"):
            errors.append(f"Aulas '{rid}': room_type invalido '{rtype}' (use 'aula' o 'laboratorio').")
            continue
        try:
            unav = _str_to_slots(row.get("unavailable_slots"))
            shared = _str_to_slots(row.get("shared_occupied_slots"))
        except ValueError as exc:
            errors.append(f"Aulas '{rid}': {exc}")
            continue
        rooms.append(
            Room(
                id=rid,
                name=rid if pd.isna(row.get("name")) else str(row["name"]).strip(),
                capacity=int(row["capacity"]),
                room_type=rtype,  # type: ignore[arg-type]
                equipment=_str_to_list(row.get("equipment")),
                unavailable_slots=unav,
                shared_occupied_slots=shared,
                shared_with=_str_to_list(row.get("shared_with")),
            )
        )

    # 5. Asignaturas
    c_df = pd.read_excel(xl, sheet_name="Asignaturas")
    courses: list[Course] = []
    seen_c: set[str] = set()
    for idx, row in c_df.iterrows():
        cid = "" if pd.isna(row.get("id")) else str(row["id"]).strip()
        if not cid:
            errors.append(f"Asignaturas fila {idx + 2}: falta 'id'.")
            continue
        if cid in seen_c:
            errors.append(f"Asignaturas fila {idx + 2}: id duplicado '{cid}'.")
            continue
        seen_c.add(cid)

        gid = "" if pd.isna(row.get("group_id")) else str(row["group_id"]).strip()
        tid = "" if pd.isna(row.get("teacher_id")) else str(row["teacher_id"]).strip()
        if gid not in seen_g:
            errors.append(f"Asignaturas '{cid}': group_id '{gid}' no existe en hoja Grupos.")
        if tid not in seen_t:
            errors.append(f"Asignaturas '{cid}': teacher_id '{tid}' no existe en hoja Docentes.")
        if pd.isna(row.get("sessions_per_week")) or int(row["sessions_per_week"]) <= 0:
            errors.append(f"Asignaturas '{cid}': sessions_per_week debe ser > 0.")
            continue
        rtype = "aula" if pd.isna(row.get("room_type")) else str(row["room_type"]).strip().lower()
        if rtype not in ("aula", "laboratorio"):
            errors.append(f"Asignaturas '{cid}': room_type invalido '{rtype}'.")
            continue

        courses.append(
            Course(
                id=cid,
                name=cid if pd.isna(row.get("name")) else str(row["name"]).strip(),
                group_id=gid,
                teacher_id=tid,
                sessions_per_week=int(row["sessions_per_week"]),
                room_type=rtype,  # type: ignore[arg-type]
                required_equipment=_str_to_list(row.get("required_equipment")),
                max_sessions_per_day=int(row["max_sessions_per_day"]) if pd.notna(row.get("max_sessions_per_day")) else 2,
                quarter=int(row["quarter"]) if pd.notna(row.get("quarter")) else 7,
            )
        )

    # 6. Preferencias (opcional)
    preferences: list[Preference] = []
    if "Preferencias" in xl.sheet_names:
        p_df = pd.read_excel(xl, sheet_name="Preferencias")
        for idx, row in p_df.iterrows():
            pid = "" if pd.isna(row.get("id")) else str(row["id"]).strip()
            if not pid:
                continue
            tid = "" if pd.isna(row.get("teacher_id")) else str(row["teacher_id"]).strip()
            if tid not in seen_t:
                warnings.append(f"Preferencia '{pid}' refiere a docente inexistente '{tid}'; omitida.")
                continue
            raw_params = "{}" if pd.isna(row.get("params_json")) else str(row["params_json"]).strip()
            try:
                params = json.loads(raw_params) if raw_params else {}
            except json.JSONDecodeError:
                errors.append(f"Preferencia '{pid}': params_json invalido.")
                continue
            preferences.append(
                Preference(
                    id=pid,
                    teacher_id=tid,
                    pref_type=str(row["pref_type"]).strip(),  # type: ignore[arg-type]
                    params=params,
                    hard=bool(row["hard"]) if pd.notna(row.get("hard")) else False,
                    weight=int(row["weight"]) if pd.notna(row.get("weight")) else 1,
                    source_text="" if pd.isna(row.get("source_text")) else str(row["source_text"]),
                    status="approved" if pd.isna(row.get("status")) else str(row["status"]).strip(),  # type: ignore[arg-type]
                )
            )

    if errors:
        raise ExcelValidationError(errors)

    instance = Instance(
        id=cfg.get("id", path.stem),
        name=cfg.get("name", path.stem),
        description=cfg.get("description", ""),
        days=days,
        slot_labels=slot_labels,
        recess_slots=recess_slots,
        morning_cutoff_slot=morning_cutoff,
        teachers=teachers,
        groups=groups,
        rooms=rooms,
        courses=courses,
        preferences=preferences,
    )

    # Verificacion rapida de aulas compatibles por materia
    for c in instance.courses:
        if not instance.compatible_rooms(c.id):
            warnings.append(
                f"Advertencia: la asignatura '{c.id}' no tiene ningun aula/laboratorio compatible."
            )

    return instance, warnings


# =============================================================================
# EXPORTACION DE HORARIOS A EXCEL (Sabana general, docente, grupo y aula)
# =============================================================================

_HEADER_FILL = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")
_SUBHEADER_FILL = PatternFill(start_color="2C5282", end_color="2C5282", fill_type="solid")
_TIME_FILL = PatternFill(start_color="EDF2F7", end_color="EDF2F7", fill_type="solid")
_BLOCKED_FILL = PatternFill(start_color="FED7D7", end_color="FED7D7", fill_type="solid")
_PALETTE = [
    "EBF8FF",
    "E6FFFA",
    "FEFCBF",
    "FAF5FF",
    "FFF5F5",
    "F0FFF4",
    "EDF2F7",
    "E9D8FD",
    "BEE3F8",
    "C6F6D5",
]
_THIN = Side(border_style="thin", color="CBD5E0")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _style_header(cell, fill=_HEADER_FILL) -> None:
    cell.fill = fill
    cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = _BORDER


def _course_fill(course_id: str) -> PatternFill:
    idx = sum(ord(ch) for ch in course_id) % len(_PALETTE)
    return PatternFill(start_color=_PALETTE[idx], end_color=_PALETTE[idx], fill_type="solid")


def _safe_sheet_name(prefix: str, raw: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in f"{prefix}_{raw}")
    return clean[:31]


def _write_entity_grid_sheet(
    wb: openpyxl.Workbook,
    sheet_title: str,
    header_title: str,
    instance: Instance,
    cell_map: dict[tuple[int, int], list[tuple[str, str]]],
    blocked_slots: set[tuple[int, int]] | None = None,
) -> None:
    ws = wb.create_sheet(title=sheet_title)
    blocked = blocked_slots or set()
    num_days = instance.num_days

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=num_days + 1)
    title_cell = ws.cell(row=1, column=1, value=header_title)
    _style_header(title_cell, _HEADER_FILL)
    ws.row_dimensions[1].height = 26

    ws.cell(row=2, column=1, value="Horario")
    _style_header(ws.cell(row=2, column=1), _SUBHEADER_FILL)
    for d, dname in enumerate(instance.days):
        c = ws.cell(row=2, column=d + 2, value=dname)
        _style_header(c, _SUBHEADER_FILL)
    ws.row_dimensions[2].height = 22

    for s, slabel in enumerate(instance.slot_labels):
        row = s + 3
        ws.row_dimensions[row].height = 42
        tcell = ws.cell(row=row, column=1, value=slabel)
        tcell.fill = _TIME_FILL
        tcell.font = Font(name="Calibri", size=9, bold=True, color="1A202C")
        tcell.alignment = Alignment(horizontal="center", vertical="center")
        tcell.border = _BORDER

        for d in range(num_days):
            cell = ws.cell(row=row, column=d + 2)
            cell.border = _BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            entries = cell_map.get((d, s), [])
            if entries:
                cell.value = "\n---\n".join(txt for txt, _ in entries)
                cell.fill = _course_fill(entries[0][1])
                cell.font = Font(name="Calibri", size=8.5, color="1A202C")
            elif (d, s) in blocked:
                cell.value = "OCUPADO / NO DISP."
                cell.fill = _BLOCKED_FILL
                cell.font = Font(name="Calibri", size=8, italic=True, color="9B2C2C")
            else:
                cell.value = ""

    ws.column_dimensions["A"].width = 14
    for d in range(num_days):
        ws.column_dimensions[get_column_letter(d + 2)].width = 24


def _write_sabana_sheet(
    wb: openpyxl.Workbook,
    instance: Instance,
    schedule: Schedule,
) -> None:
    """Hoja de sabana general de ITIID: filas = (Dia, Franja), columnas = Grupos."""
    ws = wb.create_sheet(title="Sabana_General_ITIID")
    courses = instance.course_map()
    groups = instance.groups

    total_cols = 2 + len(groups)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    tcell = ws.cell(
        row=1,
        column=1,
        value=f"SABANA GENERAL DE HORARIOS ITIID — {instance.name} ({schedule.method}, Escenario {schedule.scenario})",
    )
    _style_header(tcell, _HEADER_FILL)
    ws.row_dimensions[1].height = 28

    _style_header(ws.cell(row=2, column=1, value="Dia"), _SUBHEADER_FILL)
    _style_header(ws.cell(row=2, column=2, value="Franja"), _SUBHEADER_FILL)
    for idx, g in enumerate(groups, start=3):
        _style_header(ws.cell(row=2, column=idx, value=f"{g.name}\n({g.id})"), _SUBHEADER_FILL)
    ws.row_dimensions[2].height = 26

    lookup: dict[tuple[str, int, int], list[tuple[str, str]]] = defaultdict(list)
    for a in schedule.assignments:
        c = courses.get(a.course_id)
        if not c:
            continue
        lookup[(c.group_id, a.day, a.slot)].append(
            (f"{c.name}\n{c.teacher_id} | {a.room_id}", c.id)
        )

    row = 3
    for d, dname in enumerate(instance.days):
        for s, slabel in enumerate(instance.slot_labels):
            ws.row_dimensions[row].height = 36
            c_day = ws.cell(row=row, column=1, value=dname)
            c_slot = ws.cell(row=row, column=2, value=slabel)
            for c_meta in (c_day, c_slot):
                c_meta.fill = _TIME_FILL
                c_meta.font = Font(name="Calibri", size=9, bold=True)
                c_meta.alignment = Alignment(horizontal="center", vertical="center")
                c_meta.border = _BORDER

            for idx, g in enumerate(groups, start=3):
                cell = ws.cell(row=row, column=idx)
                cell.border = _BORDER
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                entries = lookup.get((g.id, d, s), [])
                if entries:
                    cell.value = "\n---\n".join(x[0] for x in entries)
                    cell.fill = _course_fill(entries[0][1])
                    cell.font = Font(name="Calibri", size=8.5)
            row += 1

    ws.column_dimensions["A"].width = 13
    ws.column_dimensions["B"].width = 14
    for idx in range(3, total_cols + 1):
        ws.column_dimensions[get_column_letter(idx)].width = 24


def export_schedule_excels(
    instance: Instance,
    schedule: Schedule,
    output_dir: str | Path,
    report: ValidationReport | None = None,
) -> dict[str, Path]:
    """Exporta los 4 archivos Excel requeridos: sabana general, por docente, por grupo y por aula."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rep = report or validate_schedule(instance, schedule)
    courses = instance.course_map()

    paths: dict[str, Path] = {}

    # 1. Sabana general + hoja de resumen del validador oficial
    wb_sabana = openpyxl.Workbook()
    wb_sabana.remove(wb_sabana.active)
    _write_sabana_sheet(wb_sabana, instance, schedule)
    ws_rep = wb_sabana.create_sheet(title="Validacion_Oficial")
    rows_rep = [
        ("Instancia", instance.id),
        ("Metodo", schedule.method),
        ("Escenario", rep.scenario),
        ("Factible (0 duras)", "SI" if rep.feasible else "NO"),
        ("Violaciones duras totales", rep.hard_violations_total),
        ("Penalizacion blanda total", rep.soft_penalty_total),
        ("Horas muertas estudiantes", rep.student_dead_hours),
        ("Huecos docentes", rep.teacher_gaps),
        ("% Preferencias cumplidas", f"{rep.preferences_pct:.1f}% ({rep.preferences_fulfilled}/{rep.preferences_total})"),
        ("Tiempo de computo (s)", rep.solve_time_sec),
    ]
    for r_idx, (k, v) in enumerate(rows_rep, start=1):
        ws_rep.cell(row=r_idx, column=1, value=k).font = Font(bold=True)
        ws_rep.cell(row=r_idx, column=2, value=v)
    ws_rep.column_dimensions["A"].width = 30
    ws_rep.column_dimensions["B"].width = 35
    p_sabana = out_dir / "sabana_general_itiid.xlsx"
    wb_sabana.save(p_sabana)
    paths["sabana_general"] = p_sabana

    # 2. Por Docente
    wb_doc = openpyxl.Workbook()
    wb_doc.remove(wb_doc.active)
    for t in instance.teachers:
        cmap: dict[tuple[int, int], list[tuple[str, str]]] = defaultdict(list)
        for a in schedule.assignments:
            c = courses.get(a.course_id)
            if c and c.teacher_id == t.id:
                cmap[(a.day, a.slot)].append((f"{c.name}\nGrupo: {c.group_id}\nAula: {a.room_id}", c.id))
        _write_entity_grid_sheet(
            wb_doc,
            _safe_sheet_name("DOC", t.id),
            f"HORARIO DOCENTE: {t.name} ({t.id}) — {schedule.method}",
            instance,
            cmap,
            blocked_slots=instance.teacher_blocked_slots(t.id),
        )
    p_doc = out_dir / "horarios_por_docente.xlsx"
    wb_doc.save(p_doc)
    paths["por_docente"] = p_doc

    # 3. Por Grupo
    wb_grp = openpyxl.Workbook()
    wb_grp.remove(wb_grp.active)
    for g in instance.groups:
        cmap = defaultdict(list)
        for a in schedule.assignments:
            c = courses.get(a.course_id)
            if c and c.group_id == g.id:
                cmap[(a.day, a.slot)].append((f"{c.name}\nDocente: {c.teacher_id}\nAula: {a.room_id}", c.id))
        _write_entity_grid_sheet(
            wb_grp,
            _safe_sheet_name("GRP", g.id),
            f"HORARIO GRUPO: {g.name} ({g.id}) — {schedule.method}",
            instance,
            cmap,
        )
    p_grp = out_dir / "horarios_por_grupo.xlsx"
    wb_grp.save(p_grp)
    paths["por_grupo"] = p_grp

    # 4. Por Aula
    wb_room = openpyxl.Workbook()
    wb_room.remove(wb_room.active)
    for r in instance.rooms:
        cmap = defaultdict(list)
        for a in schedule.assignments:
            c = courses.get(a.course_id)
            if c and a.room_id == r.id:
                cmap[(a.day, a.slot)].append((f"{c.name}\nGrupo: {c.group_id}\nDocente: {c.teacher_id}", c.id))
        _write_entity_grid_sheet(
            wb_room,
            _safe_sheet_name("AULA", r.id),
            f"HORARIO AULA: {r.name} ({r.id}, cap {r.capacity}, {r.room_type}) — {schedule.method}",
            instance,
            cmap,
            blocked_slots=r.blocked_slots(),
        )
    p_room = out_dir / "horarios_por_aula.xlsx"
    wb_room.save(p_room)
    paths["por_aula"] = p_room

    return paths
