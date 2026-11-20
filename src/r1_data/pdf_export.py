"""R1: Exportacion de horarios a PDF (sabana general ITIID, docente, grupo y aula)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from kit.schema import Instance, Schedule
from kit.validator import ValidationReport, validate_schedule


_NAVY = colors.HexColor("#1B365D")
_SLATE = colors.HexColor("#2C5282")
_LIGHT_BG = colors.HexColor("#EDF2F7")
_BLOCKED_BG = colors.HexColor("#FED7D7")
_CELL_BG = colors.HexColor("#EBF8FF")
_BORDER = colors.HexColor("#CBD5E0")


def _build_styles():
    base = getSampleStyleSheet()
    title = ParagraphStyle(
        "ItiidTitle",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=_NAVY,
        spaceAfter=4,
    )
    subtitle = ParagraphStyle(
        "ItiidSub",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        textColor=colors.HexColor("#4A5568"),
        spaceAfter=8,
    )
    cell = ParagraphStyle(
        "ItiidCell",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=7.2,
        leading=8.8,
        alignment=1,
    )
    header = ParagraphStyle(
        "ItiidHdr",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        textColor=colors.white,
        alignment=1,
    )
    return title, subtitle, cell, header


def _build_grid_table(
    instance: Instance,
    cell_map: dict[tuple[int, int], list[str]],
    blocked: set[tuple[int, int]],
    cell_style: ParagraphStyle,
    hdr_style: ParagraphStyle,
) -> Table:
    data = [[Paragraph("Horario", hdr_style)] + [Paragraph(d, hdr_style) for d in instance.days]]
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
        ("GRID", (0, 0), (-1, -1), 0.5, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]

    for s, slabel in enumerate(instance.slot_labels):
        row = [Paragraph(f"<b>{slabel}</b>", cell_style)]
        style_cmds.append(("BACKGROUND", (0, s + 1), (0, s + 1), _LIGHT_BG))
        for d in range(instance.num_days):
            entries = cell_map.get((d, s), [])
            if entries:
                row.append(Paragraph("<br/>".join(entries), cell_style))
                style_cmds.append(("BACKGROUND", (d + 1, s + 1), (d + 1, s + 1), _CELL_BG))
            elif (d, s) in blocked:
                row.append(Paragraph("<i>OCUPADO</i>", cell_style))
                style_cmds.append(("BACKGROUND", (d + 1, s + 1), (d + 1, s + 1), _BLOCKED_BG))
            else:
                row.append(Paragraph("", cell_style))
        data.append(row)

    usable_w = landscape(letter)[0] - 2.4 * cm
    time_w = 2.4 * cm
    day_w = (usable_w - time_w) / max(1, instance.num_days)
    tbl = Table(data, colWidths=[time_w] + [day_w] * instance.num_days, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def export_schedule_pdfs(
    instance: Instance,
    schedule: Schedule,
    output_dir: str | Path,
    report: ValidationReport | None = None,
) -> dict[str, Path]:
    """Genera los 4 PDFs oficiales: sabana general, docente, grupo y aula."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rep = report or validate_schedule(instance, schedule)
    courses = instance.course_map()
    t_style, sub_style, c_style, h_style = _build_styles()
    summary_line = (
        f"Metodo: {schedule.method} | Escenario: {rep.scenario} | "
        f"Violaciones duras: {rep.hard_violations_total} | Penalizacion blanda: {rep.soft_penalty_total} | "
        f"Horas muertas grupos: {rep.student_dead_hours} | Huecos docentes: {rep.teacher_gaps}"
    )

    paths: dict[str, Path] = {}

    # 1. Sabana general (una tabla por dia con todos los grupos como columnas)
    p_sabana = out_dir / "sabana_general_itiid.pdf"
    doc = SimpleDocTemplate(
        str(p_sabana),
        pagesize=landscape(letter),
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=1.0 * cm,
        bottomMargin=1.0 * cm,
    )
    story = []
    lookup: dict[tuple[str, int, int], list[str]] = defaultdict(list)
    for a in schedule.assignments:
        c = courses.get(a.course_id)
        if c:
            lookup[(c.group_id, a.day, a.slot)].append(
                f"<b>{c.name}</b><br/>{c.teacher_id} | {a.room_id}"
            )

    usable_w = landscape(letter)[0] - 2.4 * cm
    time_w = 2.3 * cm
    grp_w = (usable_w - time_w) / max(1, len(instance.groups))

    for d, dname in enumerate(instance.days):
        story.append(Paragraph(f"Sabana General ITIID — {dname} ({instance.name})", t_style))
        story.append(Paragraph(summary_line, sub_style))
        data = [[Paragraph("Franja", h_style)] + [Paragraph(g.name, h_style) for g in instance.groups]]
        cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
            ("GRID", (0, 0), (-1, -1), 0.5, _BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for s, slabel in enumerate(instance.slot_labels):
            row = [Paragraph(f"<b>{slabel}</b>", c_style)]
            cmds.append(("BACKGROUND", (0, s + 1), (0, s + 1), _LIGHT_BG))
            for gi, g in enumerate(instance.groups):
                entries = lookup.get((g.id, d, s), [])
                if entries:
                    row.append(Paragraph("<br/>".join(entries), c_style))
                    cmds.append(("BACKGROUND", (gi + 1, s + 1), (gi + 1, s + 1), _CELL_BG))
                else:
                    row.append(Paragraph("", c_style))
            data.append(row)
        tbl = Table(data, colWidths=[time_w] + [grp_w] * len(instance.groups), repeatRows=1)
        tbl.setStyle(TableStyle(cmds))
        story.append(tbl)
        if d < instance.num_days - 1:
            story.append(PageBreak())
    doc.build(story)
    paths["sabana_general"] = p_sabana

    # Helper para reportes por entidad (docente, grupo, aula)
    def build_entity_pdf(filename: str, pages: list[tuple[str, dict[tuple[int, int], list[str]], set[tuple[int, int]]]]) -> Path:
        fpath = out_dir / filename
        edoc = SimpleDocTemplate(
            str(fpath),
            pagesize=landscape(letter),
            leftMargin=1.2 * cm,
            rightMargin=1.2 * cm,
            topMargin=1.0 * cm,
            bottomMargin=1.0 * cm,
        )
        estory = []
        for idx, (heading, cmap, blocked) in enumerate(pages):
            estory.append(Paragraph(heading, t_style))
            estory.append(Paragraph(summary_line, sub_style))
            estory.append(_build_grid_table(instance, cmap, blocked, c_style, h_style))
            estory.append(Spacer(1, 0.2 * cm))
            if idx < len(pages) - 1:
                estory.append(PageBreak())
        edoc.build(estory)
        return fpath

    # 2. Por docente
    doc_pages = []
    for t in instance.teachers:
        cmap: dict[tuple[int, int], list[str]] = defaultdict(list)
        for a in schedule.assignments:
            c = courses.get(a.course_id)
            if c and c.teacher_id == t.id:
                cmap[(a.day, a.slot)].append(f"<b>{c.name}</b><br/>{c.group_id} | {a.room_id}")
        doc_pages.append(
            (f"Horario por Docente — {t.name} ({t.id})", cmap, instance.teacher_blocked_slots(t.id))
        )
    paths["por_docente"] = build_entity_pdf("horarios_por_docente.pdf", doc_pages)

    # 3. Por grupo
    grp_pages = []
    for g in instance.groups:
        cmap = defaultdict(list)
        for a in schedule.assignments:
            c = courses.get(a.course_id)
            if c and c.group_id == g.id:
                cmap[(a.day, a.slot)].append(f"<b>{c.name}</b><br/>{c.teacher_id} | {a.room_id}")
        grp_pages.append((f"Horario por Grupo — {g.name} ({g.id})", cmap, set()))
    paths["por_grupo"] = build_entity_pdf("horarios_por_grupo.pdf", grp_pages)

    # 4. Por aula
    room_pages = []
    for r in instance.rooms:
        cmap = defaultdict(list)
        for a in schedule.assignments:
            c = courses.get(a.course_id)
            if c and a.room_id == r.id:
                cmap[(a.day, a.slot)].append(f"<b>{c.name}</b><br/>{c.group_id} | {c.teacher_id}")
        room_pages.append(
            (f"Horario por Aula — {r.name} ({r.id}, {r.room_type}, cap {r.capacity})", cmap, r.blocked_slots())
        )
    paths["por_aula"] = build_entity_pdf("horarios_por_aula.pdf", room_pages)

    return paths
