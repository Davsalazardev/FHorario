"""Generador de la presentacion final ejecutiva (PDF 16:9 + guion Markdown).

Crea docs/presentacion_horarios_itiid.pdf con diapositivas limpias, tablas de
resultados medidos y las graficas reales de results/charts/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import inch
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


SLIDE_W, SLIDE_H = 13.333 * inch, 7.5 * inch
NAVY = colors.HexColor("#0F2A44")
AMBER = colors.HexColor("#D97706")
SLATE = colors.HexColor("#334E68")
LIGHT = colors.HexColor("#F0F4F8")
BORDER = colors.HexColor("#CBD5E1")
GREEN = colors.HexColor("#065F46")


def _draw_slide_chrome(canvas, doc) -> None:
    canvas.saveState()
    # Barra superior delgada + franja inferior
    canvas.setFillColor(NAVY)
    canvas.rect(0, SLIDE_H - 0.14 * inch, SLIDE_W, 0.14 * inch, fill=1, stroke=0)
    canvas.setFillColor(AMBER)
    canvas.rect(0, SLIDE_H - 0.19 * inch, 2.4 * inch, 0.05 * inch, fill=1, stroke=0)

    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.6)
    canvas.line(0.6 * inch, 0.48 * inch, SLIDE_W - 0.6 * inch, 0.48 * inch)
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(SLATE)
    canvas.drawString(0.6 * inch, 0.25 * inch, "Fundamentos de Inteligencia Artificial (7.o Cuatrimestre, ITIID — UPV)")
    canvas.drawRightString(SLIDE_W - 0.6 * inch, 0.25 * inch, f"Diapositiva {doc.page}")
    canvas.restoreState()


def build_final_presentation(project_root: str | Path) -> tuple[Path, Path]:
    """Construye docs/presentacion_horarios_itiid.pdf y docs/04_presentacion_final.md."""
    root = Path(project_root)
    res = json.loads((root / "results" / "benchmark_results.json").read_text(encoding="utf-8"))
    tun_path = root / "results" / "tuning_results.json"
    tun = json.loads(tun_path.read_text(encoding="utf-8")) if tun_path.exists() else None
    charts_dir = root / "results" / "charts"

    pdf_path = root / "docs" / "presentacion_horarios_itiid.pdf"
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=(SLIDE_W, SLIDE_H),
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.65 * inch,
    )

    base = getSampleStyleSheet()
    st_cover = ParagraphStyle("Cover", parent=base["Title"], fontName="Helvetica-Bold", fontSize=28, leading=34, textColor=NAVY, alignment=0, spaceAfter=14)
    st_sub = ParagraphStyle("Sub", parent=base["Normal"], fontName="Helvetica", fontSize=14, leading=20, textColor=SLATE, spaceAfter=14)
    st_h = ParagraphStyle("SlideH", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=NAVY, spaceAfter=10)
    st_body = ParagraphStyle("Body", parent=base["Normal"], fontName="Helvetica", fontSize=12.5, leading=18, textColor=colors.HexColor("#102A43"), spaceAfter=6)
    st_small = ParagraphStyle("Small", parent=base["Normal"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=SLATE)
    st_cell = ParagraphStyle("Cell", parent=base["Normal"], fontName="Helvetica", fontSize=9.5, leading=12, alignment=1)
    st_chdr = ParagraphStyle("CHdr", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9.5, textColor=colors.white, alignment=1)

    story: list[Any] = []

    # Slide 1: Portada
    story.append(Spacer(1, 0.9 * inch))
    story.append(Paragraph("Sistema Inteligente para la Generación de Horarios de ITIID", st_cover))
    story.append(
        Paragraph(
            "Optimización combinatoria (OR-Tools CP-SAT, Algoritmo Genético Memético y Búsqueda Local) "
            "+ Validador Oficial Árbitro + LLM local (Ollama) para preferencias, explicación y reprogramación.",
            st_sub,
        )
    )
    story.append(Spacer(1, 0.3 * inch))
    story.append(
        Paragraph(
            "<b>Regla central de diseño:</b> Las asignaciones las decide el motor de optimización y las revisa el "
            "validador común; el LLM nunca decide solo.",
            st_body,
        )
    )
    story.append(Spacer(1, 0.4 * inch))
    story.append(Paragraph("Universidad Politécnica de Victoria · Ingeniería en TI e Innovación Digital (ITIID) · 7.º Cuatrimestre", st_small))
    story.append(PageBreak())

    # Slide 2: Problema, Alcance y Arquitectura R1-R6
    story.append(Paragraph("Problema, Alcance y Arquitectura (R1–R6)", st_h))
    story.append(
        Paragraph(
            "• <b>Problema NP-difícil:</b> Asignar 142 sesiones en 6 grupos, 12 docentes y 8 aulas/laboratorios sin traslapes.<br/>"
            "• <b>Alcance por programa (no universidad):</b> Docentes y laboratorios compartidos con IM, ISA, ITM y LAYGE entran como franjas bloqueadas.<br/>"
            "• <b>Kit común inmutable:</b> Esquema JSON único, validador oficial (árbitro) y modelo CP-SAT base.<br/>"
            "• <b>Separación de roles:</b> R1 (Excel/PDF + anonimización), R2 (CP-SAT A/B/C + reprogramación), R3 (AG memético, 10 corridas), "
            "R4 (MRV + Hill Climbing), R5 (LLM extractor con revisión humana <i>pending_review</i>), R6 (explicador + escenario de cambio I4).",
            st_body,
        )
    )
    story.append(PageBreak())

    # Slide 3: P1 — ¿Un LLM puede generar un horario válido por sí solo?
    p1 = res["llm_direct_p1"]
    p1_bk = p1[0].get("backend", "N/A")
    story.append(Paragraph("P1: ¿Un LLM puede generar por sí solo un horario válido?", st_h))
    story.append(
        Paragraph(
            f"<b>Respuesta: No.</b> Evaluado con <code>{p1_bk}</code> frente al validador oficial, las violaciones duras "
            f"crecen con el tamaño del problema (Alharbi, 2026), mientras CP-SAT, AG y Búsqueda Local mantienen <b>0 violaciones duras</b>.",
            st_body,
        )
    )
    img1 = charts_dir / "01_p1_violaciones_duras.png"
    if img1.exists():
        story.append(Image(str(img1), width=9.6 * inch, height=4.7 * inch))
    story.append(PageBreak())

    # Slide 4: P2 — R5 Extracción de preferencias (P/R/F1)
    r5 = res["r5_extraction_p2"]
    ov = r5["overall"]
    story.append(Paragraph("P2: Extracción de preferencias en español (R5) — Precisión, Recall y F1", st_h))
    story.append(
        Paragraph(
            f"Evaluado sobre <b>{r5['num_examples']} preferencias docentes anotadas</b> (7 tipos de restricción) "
            f"con <code>{r5['backend']}</code> y revisión humana obligatoria (<code>pending_review</code> → <code>approved</code>):<br/>"
            f"<b>Precisión:</b> {ov['precision']*100:.1f}% · <b>Recall:</b> {ov['recall']*100:.1f}% · "
            f"<b>F1 Micro:</b> {ov['f1_micro']*100:.1f}% · <b>F1 Macro:</b> {ov['f1_macro']*100:.1f}%.",
            st_body,
        )
    )
    t_rows = [[Paragraph("Tipo de restricción", st_chdr), Paragraph("TP", st_chdr), Paragraph("FP", st_chdr), Paragraph("FN", st_chdr), Paragraph("Precisión", st_chdr), Paragraph("Recall", st_chdr), Paragraph("F1", st_chdr)]]
    for k, v in r5["by_type"].items():
        t_rows.append([
            Paragraph(k, st_cell),
            Paragraph(str(v["tp"]), st_cell),
            Paragraph(str(v["fp"]), st_cell),
            Paragraph(str(v["fn"]), st_cell),
            Paragraph(f"{v['precision']*100:.1f}%", st_cell),
            Paragraph(f"{v['recall']*100:.1f}%", st_cell),
            Paragraph(f"{v['f1']*100:.1f}%", st_cell),
        ])
    tbl = Table(t_rows, colWidths=[2.8*inch, 0.8*inch, 0.8*inch, 0.8*inch, 1.4*inch, 1.4*inch, 1.4*inch])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(tbl)
    story.append(PageBreak())

    # Slide 5: P3 — Comparación de métodos y ajuste de AG
    story.append(Paragraph("P3: Comparación de Métodos — CP-SAT vs AG (10 corridas) vs Búsqueda Local", st_h))
    img2 = charts_dir / "02_p3_comparacion_metodos.png"
    if img2.exists():
        story.append(Image(str(img2), width=11.4 * inch, height=4.4 * inch))
    if tun:
        story.append(
            Paragraph(
                f"<b>Ajuste de hiperparámetros (R3):</b> Se evaluaron 4 configuraciones sobre I2 e I3; "
                f"el enfoque memético (<b>{tun['recommended_config']}</b>) redujo sustancialmente la penalización blanda en I3 manteniendo 100% factibilidad.",
                st_small,
            )
        )
    story.append(PageBreak())

    # Slide 6: Escenarios A, B y C en I3
    story.append(Paragraph("Comparación de Escenarios A (Estudiantes), B (Docentes) y C (Equilibrio)", st_h))
    img3 = charts_dir / "03_escenarios_A_B_C.png"
    if img3.exists():
        story.append(Image(str(img3), width=10.8 * inch, height=4.5 * inch))
    story.append(
        Paragraph(
            "La coordinación académica elige la política institucional comparando métricas auditables: "
            "Escenario A elimina horas muertas de grupos, B maximiza preferencias docentes y C logra el menor costo equilibrado.",
            st_small,
        )
    )
    story.append(PageBreak())

    # Slide 7: I4 Reprogramación, Estabilidad y P4 (Explicador)
    i4 = res["i4_reprogramming"]
    rm = i4["reprogram_mode"]
    fs = i4["from_scratch_mode"]
    story.append(Paragraph("Escenario de Cambio (I4) y Explicador (R6 / P4)", st_h))
    story.append(
        Paragraph(
            f"<b>Cambio solicitado:</b> <i>\"{i4['change_text']}\"</i><br/>"
            f"• <b>Reprogramación R2:</b> 0 duras · solo <b>{rm['moved_sessions']}/{rm['total_sessions']}</b> sesiones movidas "
            f"(<b>{rm['stability_pct']:.1f}% estabilidad</b>) en <b>{rm['solve_time_sec']:.2f} s</b>.<br/>"
            f"• <b>Resolver desde cero:</b> mueve <b>{fs['moved_sessions']}/{fs['total_sessions']}</b> sesiones ({fs['stability_pct']:.1f}% estabilidad).",
            st_body,
        )
    )
    img4 = charts_dir / "04_p2_f1_y_i4_estabilidad.png"
    if img4.exists():
        story.append(Image(str(img4), width=10.6 * inch, height=4.1 * inch))
    story.append(PageBreak())

    # Slide 8: Ética, Demo en Vivo y Conclusiones
    story.append(Paragraph("Ética, Demostración en Vivo y Conclusiones", st_h))
    story.append(
        Paragraph(
            "• <b>Ética y privacidad:</b> Anonimización automática (DOC_01, ITIID_7A, LAB_01) y limpieza de correos/teléfonos antes de cualquier API externa; "
            "opción 100% local con <b>Ollama (qwen2.5:7b)</b> para que ningún dato salga de la universidad.<br/>"
            "• <b>Demostración en vivo (Streamlit):</b> 1) Cargar instancia/Excel y ejecutar CP-SAT (A/B/C) → 2) Ver sábana/docente/grupo/aula y exportar Excel/PDF → "
            "3) Extraer y aprobar preferencia en español (R5) → 4) Traducir, confirmar y reprogramar I4 (R6+R2) → 5) Recoger rúbrica P4.<br/>"
            "• <b>Conclusión:</b> <b>OR-Tools CP-SAT</b> es el motor óptimo y más rápido para ITIID; los LLMs brillan como interfaz en español (R5/R6), nunca como solver autónomo.",
            st_body,
        )
    )

    doc.build(story, onFirstPage=_draw_slide_chrome, onLaterPages=_draw_slide_chrome)

    # Tambien guardar guion de presentacion Markdown
    md_path = root / "docs" / "04_presentacion_final.md"
    md_content = f"""# Guion de Presentación Final y Demostración — Horarios ITIID

**Archivo de diapositivas PDF (16:9):** `docs/presentacion_horarios_itiid.pdf` (8 diapositivas)  
**Rúbrica para evaluar P4 durante la demo:** `docs/05_rubrica_evaluacion_p4.md`

## Orden sugerido de la presentación (10–12 minutos)

1. **Portada y Regla de Diseño (1 min):** Explicar por qué el motor de optimización decide, el validador común juzga y el LLM nunca asigna clases solo.
2. **Problema, Alcance y Arquitectura R1–R6 (1.5 min):** Recalcar alcance por programa ITIID (recursos compartidos bloqueados) y el kit común inmutable.
3. **P1 — Por qué no basta un LLM autónomo (1.5 min):** Mostrar la gráfica `01_p1_violaciones_duras.png` con resultados de `{p1_bk}` y conexión con Alharbi (2026).
4. **P2 — R5 Extractor de Preferencias (1.5 min):** Mostrar P/R/F1 por tipo (`F1 micro = {ov['f1_micro']*100:.1f}%`, `backend = {r5['backend']}`) y la revisión humana `pending_review`.
5. **P3 — Comparación de Métodos y Ajuste de AG (2 min):** Mostrar tabla de posiciones en I1–I3, 10 corridas del AG memético ajustado y superioridad de CP-SAT.
6. **Escenarios A, B, C y Reprogramación I4 (1.5 min):** Tradeoff estudiantes vs docentes + estabilidad de `{rm['stability_pct']:.1f}%` (`{rm['moved_sessions']}/{rm['total_sessions']}` sesiones movidas en `{rm['solve_time_sec']:.2f} s`).
7. **Demostración en vivo en Streamlit + Evaluación P4 (2–3 min):**
   - Abrir `streamlit run app.py` conectado a **Ollama (`qwen2.5:7b`)**.
   - Generar horario de `I3` en Escenario C, mostrar sábana general y horario por docente (bloques rojos compartidos).
   - Extraer una preferencia en español en `R5`, aprobarla manualmente e inyectarla.
   - Ejecutar `I4` en 2 pasos (Traducir con `R6` → Confirmar → Reprogramar con `R2`) y pedir a coordinación/equipos llenar `docs/05_rubrica_evaluacion_p4.md`.
"""
    md_path.write_text(md_content, encoding="utf-8")
    return pdf_path, md_path
