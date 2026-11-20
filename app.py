"""Interfaz web en Streamlit para el Sistema Inteligente de Horarios ITIID.

Direccion visual: Consola de Coordinacion Academica (azul marino UPV #0F2A44,
lienzos limpios #F7F8FA, grilla horaria codificada por color y bloques compartidos
resaltados). Conexion nativa a modelos locales de Ollama (ej. qwen2.5:7b) y flujo
completo Human-in-the-Loop para R5 (Aprobar/Rechazar) y R6/I4 (Traducir -> Confirmar).

Ejecutar con:
    .venv/bin/streamlit run app.py
"""

from __future__ import annotations

import copy
import html
import json
import tempfile
from collections import defaultdict
from pathlib import Path

import pandas as pd
import streamlit as st

from kit.instances import build_instance_i1, build_instance_i2, build_instance_i3
from kit.schema import Instance, Preference, SCENARIOS, Schedule
from kit.validator import validate_schedule
from src.llm_client import LLMConfig, check_ollama_available, detect_backend
from src.r1_data.excel_io import (
    ExcelValidationError,
    export_schedule_excels,
    import_instance_from_excel,
)
from src.r1_data.pdf_export import export_schedule_pdfs
from src.r2_cpsat.solver import solve_cpsat
from src.r3_genetic.solver import solve_genetic
from src.r4_local_search.solver import solve_local_search
from src.r5_llm_extractor.extractor import evaluate_extractor, extract_preferences_from_text
from src.r6_llm_explainer.explainer import (
    ChangeRequest,
    apply_change_and_reprogram,
    explain_validation_report,
    parse_change_request,
)


PROJECT_ROOT = Path(__file__).resolve().parent

st.set_page_config(
    page_title="Horarios ITIID — Consola de Optimizacion",
    page_icon="📅",
    layout="wide",
    initial_sidebar_state="expanded",
)

_CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;
}
.itiid-banner {
    background: #0F2A44;
    color: #FFFFFF;
    padding: 1.15rem 1.45rem;
    border-radius: 10px;
    border-left: 6px solid #D97706;
    margin-bottom: 1.0rem;
}
.itiid-banner h1 {
    margin: 0;
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #FFFFFF;
}
.itiid-banner p {
    margin: 0.3rem 0 0 0;
    font-size: 0.9rem;
    color: #D9E2EC;
}
.status-pill {
    display: inline-block;
    padding: 0.22rem 0.65rem;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    margin-right: 0.4rem;
    margin-bottom: 0.35rem;
}
.pill-ok { background: #D1FAE5; color: #065F46; }
.pill-bad { background: #FEE2E2; color: #991B1B; }
.pill-warn { background: #FEF3C7; color: #92400E; }
.pill-info { background: #DBEAFE; color: #1E40AF; }

/* Tabla visual de horarios — ajuste completo sin scroll innecesario */
.tt-wrap {
    overflow-x: auto;
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 10px;
    padding: 0.45rem;
    margin-top: 0.5rem;
}
.tt-table {
    width: 100%;
    table-layout: fixed;
    border-collapse: collapse;
    font-size: 0.79rem;
}
.tt-table th {
    background: #0F2A44;
    color: #FFFFFF;
    padding: 0.5rem 0.35rem;
    text-align: center;
    font-weight: 600;
    border: 1px solid #243B53;
}
.tt-table td {
    border: 1px solid #D9E2EC;
    padding: 0.3rem 0.32rem;
    vertical-align: top;
    word-wrap: break-word;
}
.tt-time {
    background: #F0F4F8;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 600;
    font-size: 0.72rem;
    color: #102A43;
    text-align: center;
    white-space: nowrap;
    width: 88px;
}
.tt-card {
    border-radius: 6px;
    padding: 0.3rem 0.4rem;
    margin-bottom: 0.2rem;
    border-left: 4px solid #1D4ED8;
    line-height: 1.22;
}
.tt-card .tt-course {
    font-weight: 700;
    color: #102A43;
    font-size: 0.76rem;
}
.tt-card .tt-meta {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.68rem;
    color: #334E68;
    margin-top: 2px;
}
.tt-blocked {
    background: repeating-linear-gradient(
        -45deg,
        #FEE2E2,
        #FEE2E2 6px,
        #FECACA 6px,
        #FECACA 12px
    );
    color: #991B1B;
    font-size: 0.69rem;
    font-weight: 700;
    text-align: center;
    padding: 0.45rem 0.2rem;
    border-radius: 5px;
}
.pref-card {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 8px;
    padding: 0.7rem 0.95rem;
    margin-bottom: 0.55rem;
    color: #102A43;
}
</style>
"""

_COURSE_COLORS = [
    ("#EFF6FF", "#2563EB"),
    ("#ECFDF5", "#059669"),
    ("#FFFBEB", "#D97706"),
    ("#F5F3FF", "#7C3AED"),
    ("#FDF2F8", "#DB2777"),
    ("#ECFEFF", "#0891B2"),
    ("#F0FDF4", "#16A34A"),
    ("#FFF7ED", "#EA580C"),
    ("#EEF2FF", "#4F46E5"),
    ("#F8FAFC", "#475569"),
]


def _course_style(course_id: str) -> tuple[str, str]:
    idx = sum(ord(ch) for ch in course_id) % len(_COURSE_COLORS)
    return _COURSE_COLORS[idx]


def _render_timetable_html(
    instance: Instance,
    schedule: Schedule,
    mode: str,
    entity_id: str | None = None,
    sabana_day: int = 0,
) -> str:
    courses = instance.course_map()
    if mode == "sabana":
        lookup = defaultdict(list)
        for a in schedule.assignments:
            c = courses.get(a.course_id)
            if c and a.day == sabana_day:
                lookup[(c.group_id, a.slot)].append((c, a))
        headers = ["Franja"] + [html.escape(g.name) for g in instance.groups]
        rows_html = []
        for s, slabel in enumerate(instance.slot_labels):
            tds = [f"<td class='tt-time'>{html.escape(slabel)}</td>"]
            for g in instance.groups:
                entries = lookup.get((g.id, s), [])
                if entries:
                    cards = []
                    for c, a in entries:
                        bg, border = _course_style(c.id)
                        cards.append(
                            f"<div class='tt-card' style='background:{bg};border-left-color:{border};'>"
                            f"<div class='tt-course'>{html.escape(c.name)}</div>"
                            f"<div class='tt-meta'>{html.escape(c.teacher_id)} · {html.escape(a.room_id)}</div>"
                            f"</div>"
                        )
                    tds.append(f"<td>{''.join(cards)}</td>")
                else:
                    tds.append("<td></td>")
            rows_html.append(f"<tr>{''.join(tds)}</tr>")
        th_html = "".join(f"<th>{h}</th>" for h in headers)
        return f"<div class='tt-wrap'><table class='tt-table'><thead><tr>{th_html}</tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>"

    cell_map = defaultdict(list)
    blocked: set[tuple[int, int]] = set()
    if mode == "docente" and entity_id:
        blocked = instance.teacher_blocked_slots(entity_id)
        for a in schedule.assignments:
            c = courses.get(a.course_id)
            if c and c.teacher_id == entity_id:
                cell_map[(a.day, a.slot)].append((c.id, c.name, f"{c.group_id} · {a.room_id}"))
    elif mode == "grupo" and entity_id:
        for a in schedule.assignments:
            c = courses.get(a.course_id)
            if c and entity_id in c.all_group_ids():
                cell_map[(a.day, a.slot)].append((c.id, c.name, f"{c.teacher_id} · {a.room_id}"))
    elif mode == "aula" and entity_id:
        r = instance.room_map()[entity_id]
        blocked = r.blocked_slots()
        for a in schedule.assignments:
            c = courses.get(a.course_id)
            if c and a.room_id == entity_id:
                cell_map[(a.day, a.slot)].append((c.id, c.name, f"{c.group_id} · {c.teacher_id}"))

    headers = ["Franja"] + [html.escape(d) for d in instance.days]
    rows_html = []
    for s, slabel in enumerate(instance.slot_labels):
        tds = [f"<td class='tt-time'>{html.escape(slabel)}</td>"]
        for d in range(instance.num_days):
            entries = cell_map.get((d, s), [])
            if entries:
                cards = []
                for cid, cname, meta in entries:
                    bg, border = _course_style(cid)
                    cards.append(
                        f"<div class='tt-card' style='background:{bg};border-left-color:{border};'>"
                        f"<div class='tt-course'>{html.escape(cname)}</div>"
                        f"<div class='tt-meta'>{html.escape(meta)}</div>"
                        f"</div>"
                    )
                tds.append(f"<td>{''.join(cards)}</td>")
            elif (d, s) in blocked:
                tds.append("<td><div class='tt-blocked'>OCUPADO / NO DISP.</div></td>")
            else:
                tds.append("<td></td>")
        rows_html.append(f"<tr>{''.join(tds)}</tr>")

    th_html = "".join(f"<th>{h}</th>" for h in headers)
    return f"<div class='tt-wrap'><table class='tt-table'><thead><tr>{th_html}</tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>"


def _get_base_instance(choice: str) -> Instance:
    if choice.startswith("I1"):
        return build_instance_i1(PROJECT_ROOT / "data")
    if choice.startswith("I2"):
        return build_instance_i2()
    return build_instance_i3()


def main() -> None:
    st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)

    st.markdown(
        """
        <div class="itiid-banner">
            <h1>Sistema Inteligente de Horarios — ITIID (UPV)</h1>
            <p>Motor formal de optimizacion (OR-Tools CP-SAT, AG Memetico, Busqueda Local) + Validador Oficial Arbitro + LLM local (Ollama) para preferencias, explicaciones y cambios.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "pending_prefs" not in st.session_state:
        st.session_state["pending_prefs"] = []
    if "approved_custom_prefs" not in st.session_state:
        st.session_state["approved_custom_prefs"] = []
    if "pending_change_req" not in st.session_state:
        st.session_state["pending_change_req"] = None

    # Detectar modelos Ollama locales en vivo
    ollama_ok, ollama_models = check_ollama_available()
    chat_ollama_models = [m for m in ollama_models if "embed" not in m.lower()]

    with st.sidebar:
        st.markdown("### 1. Instancia de Datos")
        source = st.radio("Origen", ["Kit Comun (I1–I3)", "Subir Plantilla Excel (.xlsx)"])
        base_instance: Instance | None = None

        if source == "Kit Comun (I1–I3)":
            choice = st.selectbox(
                "Instancia activa",
                ["I3 (ITIID Completo — 142 ses.)", "I2 (ITIID 7o Cuat. — 50 ses.)", "I1 (ITC-2007 — 42 ses.)"],
            )
            base_instance = _get_base_instance(choice)
        else:
            up = st.file_uploader("Plantilla Excel (.xlsx)", type=["xlsx"])
            if up is not None:
                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                    tmp.write(up.getvalue())
                    tmp_path = Path(tmp.name)
                try:
                    base_instance, warns = import_instance_from_excel(tmp_path)
                    for w in warns:
                        st.warning(w)
                except ExcelValidationError as err:
                    st.error(str(err))
                    st.stop()
            else:
                st.info("Carga una plantilla .xlsx o cambia a Kit Comun.")
                st.stop()

        assert base_instance is not None
        # Inyectar preferencias aprobadas por humano desde R5 (solo las de docentes presentes)
        instance = copy.deepcopy(base_instance)
        valid_tids = {t.id for t in instance.teachers}
        injected = [p for p in st.session_state["approved_custom_prefs"] if p.teacher_id in valid_tids]
        instance.preferences.extend(injected)

        st.markdown(
            f"**Grupos:** {len(instance.groups)} · **Docentes:** {len(instance.teachers)}  \n"
            f"**Aulas/Labs:** {len(instance.rooms)} · **Sesiones:** {instance.total_sessions()}  \n"
            f"**Preferencias activas:** {len(instance.approved_preferences())} "
            f"*(+{len(injected)} aprobadas en R5)*"
        )

        st.divider()
        st.markdown("### 2. Motor de Optimizacion")
        method = st.selectbox("Algoritmo", ["CP-SAT (R2)", "Algoritmo Genetico (R3)", "Busqueda Local (R4)"])
        scenario = st.selectbox(
            "Escenario",
            ["C", "A", "B"],
            format_func=lambda s: f"Escenario {s} — {SCENARIOS[s].description.split('(')[0].strip()}",
        )
        time_limit = st.slider("Limite de tiempo (s)", 2, 15, 4)

        st.divider()
        st.markdown("### 3. Modelo LLM (R5 y R6)")
        if ollama_ok and chat_ollama_models:
            default_idx = next((i for i, m in enumerate(chat_ollama_models) if "qwen" in m.lower()), 0)
            prov_mode = st.selectbox("Proveedor LLM", ["ollama", "offline", "gemini", "openai"], index=0)
            if prov_mode == "ollama":
                sel_model = st.selectbox("Modelo local Ollama", chat_ollama_models, index=default_idx)
                llm_cfg = LLMConfig(provider="ollama", model=sel_model, timeout_sec=60.0)
                st.success(f"Ollama activo: {sel_model}")
            else:
                llm_cfg = LLMConfig(provider=prov_mode)
                det_p, det_m = detect_backend(llm_cfg)
                st.info(f"Backend: {det_p} ({det_m})")
        else:
            prov_mode = st.selectbox("Proveedor LLM", ["auto", "offline", "gemini", "openai"])
            llm_cfg = LLMConfig(provider=prov_mode)
            det_p, det_m = detect_backend(llm_cfg)
            st.warning(f"Ollama local no detectado. Usando: {det_p} ({det_m})")

    tabs = st.tabs(
        [
            "Generador y Validador Oficial",
            "R5: Preferencias (Revision Humana)",
            "I4 / R6: Reprogramacion y Explicador",
            "Tabla de Posiciones y Tuning",
        ]
    )

    # =========================================================================
    # TAB 1: GENERAR, VALIDAR Y VISUALIZAR
    # =========================================================================
    with tabs[0]:
        col_btn, col_info = st.columns([1.8, 2.6])
        with col_btn:
            run_clicked = st.button("Generar y validar horario", type="primary", use_container_width=True)
        with col_info:
            st.caption(
                f"Escenario **{scenario}**: {SCENARIOS[scenario].description}. "
                "Todo horario pasa automaticamente por el validador oficial `kit/validator.py`."
            )

        if run_clicked:
            with st.spinner(f"Resolviendo {instance.id} con {method} (Escenario {scenario})..."):
                if method.startswith("CP-SAT"):
                    sch = solve_cpsat(instance, scenario=scenario, time_limit_sec=float(time_limit))
                elif method.startswith("Algoritmo"):
                    sch = solve_genetic(instance, scenario=scenario, time_limit_sec=float(time_limit))
                else:
                    sch = solve_local_search(instance, scenario=scenario, time_limit_sec=float(time_limit))
                rep = validate_schedule(instance, sch, scenario=scenario)
                st.session_state["active_schedule"] = sch
                st.session_state["active_report"] = rep
                st.session_state["active_instance"] = instance
                st.session_state.pop("active_explanation", None)

        if "active_schedule" in st.session_state and st.session_state["active_instance"].id == instance.id:
            sch: Schedule = st.session_state["active_schedule"]
            rep = st.session_state["active_report"]
            active_inst: Instance = st.session_state["active_instance"]

            pill_cls = "pill-ok" if rep.feasible else "pill-bad"
            pill_lbl = "FACTIBLE — 0 VIOLACIONES DURAS" if rep.feasible else f"NO FACTIBLE — {rep.hard_violations_total} VIOLACIONES DURAS"
            st.markdown(
                f"<span class='status-pill {pill_cls}'>{pill_lbl}</span>"
                f"<span class='status-pill pill-info'>{html.escape(rep.method)}</span>"
                f"<span class='status-pill pill-warn'>Tiempo: {rep.solve_time_sec:.2f} s</span>",
                unsafe_allow_html=True,
            )

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Violaciones duras", rep.hard_violations_total)
            c2.metric("Penalizacion blanda", f"{rep.soft_penalty_total} pts")
            c3.metric("Horas muertas grupos", rep.student_dead_hours)
            c4.metric("Huecos docentes", rep.teacher_gaps)
            c5.metric("Preferencias cumplidas", f"{rep.preferences_pct:.1f}%")

            vcol1, vcol2 = st.columns([1.8, 1.5])
            with vcol1:
                view_mode = st.radio(
                    "Vista de horario",
                    ["sabana", "grupo", "docente", "aula"],
                    horizontal=True,
                    format_func=lambda m: {
                        "sabana": "Sabana general ITIID",
                        "grupo": "Por grupo",
                        "docente": "Por docente",
                        "aula": "Por aula/lab",
                    }[m],
                )
            entity_id = None
            sabana_day = 0
            with vcol2:
                if view_mode == "sabana":
                    sabana_day = st.selectbox(
                        "Dia de la sabana",
                        list(range(active_inst.num_days)),
                        format_func=lambda d: active_inst.days[d],
                    )
                elif view_mode == "grupo":
                    entity_id = st.selectbox("Grupo", [g.id for g in active_inst.groups])
                elif view_mode == "docente":
                    entity_id = st.selectbox(
                        "Docente",
                        [t.id for t in active_inst.teachers],
                        format_func=lambda tid: f"{tid} — {active_inst.teacher_map()[tid].name}",
                    )
                elif view_mode == "aula":
                    entity_id = st.selectbox(
                        "Aula / Laboratorio",
                        [r.id for r in active_inst.rooms],
                        format_func=lambda rid: f"{rid} — {active_inst.room_map()[rid].name} ({active_inst.room_map()[rid].room_type})",
                    )

            st.markdown(
                _render_timetable_html(active_inst, sch, view_mode, entity_id=entity_id, sabana_day=sabana_day),
                unsafe_allow_html=True,
            )

            st.markdown("")
            exp_col1, exp_col2 = st.columns([1.5, 1.5])
            with exp_col1:
                with st.expander("Explicacion R6 para Coordinacion Academica", expanded=True):
                    if st.button("Generar explicacion con LLM activo (R6)"):
                        with st.spinner(f"Consultando R6 ({llm_cfg.provider}:{llm_cfg.model or 'auto'})..."):
                            txt, bk = explain_validation_report(active_inst, rep, llm_cfg=llm_cfg)
                            st.session_state["active_explanation"] = (txt, bk)
                    if "active_explanation" in st.session_state:
                        txt, bk = st.session_state["active_explanation"]
                        st.markdown(txt)
                        st.caption(f"Generado por: `{bk}`")
                    else:
                        txt_off, _ = explain_validation_report(
                            active_inst, rep, llm_cfg=LLMConfig(provider="offline")
                        )
                        st.markdown(txt_off)
                        st.caption("Vista rapida estructurada · Pulsa el boton superior para redactarla con Ollama/LLM.")

            with exp_col2:
                with st.expander("Exportar paquetes oficiales (R1: Excel y PDF)", expanded=True):
                    if st.button("Construir 4 Excel + 4 PDF del horario activo"):
                        tmp_dir = Path(tempfile.mkdtemp(prefix="itiid_export_"))
                        st.session_state["export_ex"] = export_schedule_excels(
                            active_inst, sch, tmp_dir / "excel", report=rep
                        )
                        st.session_state["export_pdf"] = export_schedule_pdfs(
                            active_inst, sch, tmp_dir / "pdf", report=rep
                        )
                    if "export_ex" in st.session_state:
                        ce, cp = st.columns(2)
                        with ce:
                            st.markdown("**Excel (.xlsx)**")
                            for k, p in st.session_state["export_ex"].items():
                                st.download_button(p.name, p.read_bytes(), file_name=p.name, key=f"dl_ex_{k}")
                        with cp:
                            st.markdown("**PDF (.pdf)**")
                            for k, p in st.session_state["export_pdf"].items():
                                st.download_button(p.name, p.read_bytes(), file_name=p.name, key=f"dl_pdf_{k}")
        else:
            st.info(
                "Pulsa **Generar y validar horario** para construir el horario de la instancia seleccionada "
                "y pasar el resultado por el validador oficial."
            )

    # =========================================================================
    # TAB 2: R5 EXTRACTOR DE PREFERENCIAS CON REVISION HUMANA REAL
    # =========================================================================
    with tabs[1]:
        st.subheader("R5: Traduccion de preferencias en espanol con revision humana obligatoria")
        st.caption(
            "Flujo oficial: 1) El LLM extrae restricciones en estado `pending_review` -> "
            "2) Una persona aprueba o rechaza cada restriccion -> "
            "3) Solo las aprobadas entran al motor de optimizacion."
        )

        col_a, col_b = st.columns([1, 2.2])
        with col_a:
            tid = st.selectbox(
                "Docente anonimizado",
                [t.id for t in instance.teachers],
                format_func=lambda x: f"{x} — {instance.teacher_map()[x].name}",
                key="r5_tid",
            )
        with col_b:
            pref_text = st.text_input(
                "Preferencia escrita en espanol",
                value="Prefiero no tener clases los viernes y quiero un horario compacto sin huecos.",
            )

        if st.button("1. Extraer restricciones JSON con LLM (R5)", type="primary"):
            with st.spinner(f"Extrayendo con {llm_cfg.provider}:{llm_cfg.model or 'auto'}..."):
                extracted, backend = extract_preferences_from_text(
                    tid, pref_text, pref_id_prefix=f"HUM_{len(st.session_state['pending_prefs']) + 1}", llm_cfg=llm_cfg
                )
                for p in extracted:
                    st.session_state["pending_prefs"].append({"pref": p, "backend": backend})
                if not extracted:
                    st.warning(f"No se detectaron restricciones estructuradas (backend `{backend}`).")

        if st.session_state["pending_prefs"]:
            st.markdown("#### 2. Bandeja de revision humana (`pending_review`)")
            for idx, entry in enumerate(st.session_state["pending_prefs"]):
                pref: Preference = entry["pref"]
                bk = entry["backend"]
                st_badge = {
                    "pending_review": ("pill-warn", "PENDIENTE DE REVISION"),
                    "approved": ("pill-ok", "APROBADA"),
                    "rejected": ("pill-bad", "RECHAZADA"),
                }[pref.status]

                c_info, c_ok, c_no = st.columns([3.6, 1.2, 1.2])
                with c_info:
                    st.markdown(
                        f"<div class='pref-card'>"
                        f"<span class='status-pill {st_badge[0]}'>{st_badge[1]}</span>"
                        f"<span class='status-pill pill-info'>{html.escape(bk)}</span>"
                        f"<strong>{html.escape(pref.teacher_id)}</strong> · "
                        f"<code>{html.escape(pref.pref_type)}</code> · "
                        f"params=<code>{html.escape(json.dumps(pref.params, ensure_ascii=False))}</code> · "
                        f"hard=<code>{pref.hard}</code><br/>"
                        f"<small style='color:#486581;'>\"{html.escape(pref.source_text)}\"</small>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with c_ok:
                    if st.button("Aprobar", key=f"appr_{idx}", use_container_width=True):
                        pref.status = "approved"
                        st.rerun()
                with c_no:
                    if st.button("Rechazar", key=f"rej_{idx}", use_container_width=True):
                        pref.status = "rejected"
                        st.rerun()

            col_apply, col_clear = st.columns([1.6, 1.0])
            with col_apply:
                if st.button("3. Aplicar aprobadas a la instancia activa", type="primary"):
                    approved_now = [e["pref"] for e in st.session_state["pending_prefs"] if e["pref"].status == "approved"]
                    st.session_state["approved_custom_prefs"] = approved_now
                    st.success(
                        f"Se aplicaron {len(approved_now)} preferencias aprobadas a la instancia. "
                        "Vuelve a la pestana 'Generador y Validador Oficial' para re-optimizar."
                    )
                    st.rerun()
            with col_clear:
                if st.button("Limpiar bandeja"):
                    st.session_state["pending_prefs"] = []
                    st.session_state["approved_custom_prefs"] = []
                    st.rerun()

        st.divider()
        st.markdown("#### Evaluacion formal de P2 (Precision, Recall y F1 sobre 25 preferencias anotadas)")
        eval_mode = st.radio(
            "Backend para evaluar P2",
            ["Usar LLM seleccionado en barra lateral", "Comparar baseline determinista offline"],
            horizontal=True,
        )
        if st.button("Correr evaluacion P2 (25 ejemplos)"):
            ev_cfg = llm_cfg if eval_mode.startswith("Usar") else LLMConfig(provider="offline")
            with st.spinner(f"Evaluando 25 preferencias con {ev_cfg.provider}:{ev_cfg.model or 'auto'}..."):
                ev_res = evaluate_extractor(llm_cfg=ev_cfg)
                st.session_state["r5_live_eval"] = ev_res

        if "r5_live_eval" in st.session_state:
            ev_res = st.session_state["r5_live_eval"]
            ov = ev_res["overall"]
            st.caption(f"Backend evaluado: `{ev_res['backend']}` · Exact match: **{ev_res['exact_match_rate'] * 100:.1f}%**")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Precision (micro)", f"{ov['precision'] * 100:.1f}%")
            m2.metric("Recall (micro)", f"{ov['recall'] * 100:.1f}%")
            m3.metric("F1 Micro", f"{ov['f1_micro'] * 100:.1f}%")
            m4.metric("F1 Macro", f"{ov['f1_macro'] * 100:.1f}%")
            st.dataframe(pd.DataFrame(ev_res["by_type"]).T, use_container_width=True)

    # =========================================================================
    # TAB 3: I4 / R6 REPROGRAMACION EN DOS PASOS (TRADUCIR -> CONFIRMAR)
    # =========================================================================
    with tabs[2]:
        st.subheader("I4 / R6: Reprogramacion ante cambio de ultimo momento (con confirmacion humana)")
        if "active_schedule" not in st.session_state:
            st.warning("Primero genera un horario base en la pestana 'Generador y Validador Oficial'.")
        else:
            active_inst = st.session_state["active_instance"]
            change_txt = st.text_input(
                "Describe el cambio en espanol",
                value=f"El docente {active_inst.teachers[0].id} ya no puede los miercoles.",
            )
            if st.button("1. Traducir peticion de cambio con R6 (LLM)"):
                with st.spinner(f"Traduciendo cambio con {llm_cfg.provider}:{llm_cfg.model or 'auto'}..."):
                    ch_req = parse_change_request(change_txt, active_inst, llm_cfg=llm_cfg)
                    st.session_state["pending_change_req"] = ch_req

            if st.session_state["pending_change_req"] is not None:
                ch: ChangeRequest = st.session_state["pending_change_req"]
                st.markdown("#### 2. Revision humana del cambio traducido antes de reprogramar")
                st.info(f"Resumen propuesto por `{ch.backend}`: **{ch.human_summary}**")

                ec1, ec2, ec3 = st.columns(3)
                with ec1:
                    all_entities = [t.id for t in active_inst.teachers] + [r.id for r in active_inst.rooms]
                    e_idx = all_entities.index(ch.entity_id) if ch.entity_id in all_entities else 0
                    conf_entity = st.selectbox("Entidad afectada", all_entities, index=e_idx)
                with ec2:
                    conf_day = st.selectbox(
                        "Dia bloqueado",
                        list(range(active_inst.num_days)),
                        index=min(ch.day, active_inst.num_days - 1),
                        format_func=lambda d: active_inst.days[d],
                    )
                with ec3:
                    slot_opts = [None] + list(range(active_inst.slots_per_day))
                    s_idx = slot_opts.index(ch.slot) if ch.slot in slot_opts else 0
                    conf_slot = st.selectbox(
                        "Franja (o todo el dia)",
                        slot_opts,
                        index=s_idx,
                        format_func=lambda s: "Todo el dia" if s is None else active_inst.slot_labels[s],
                    )

                if st.button("Confirmar cambio y reprogramar con R2 (CP-SAT)", type="primary"):
                    is_room = conf_entity in active_inst.room_map()
                    confirmed = ChangeRequest(
                        action="block_room_slot" if is_room else ("block_teacher_day" if conf_slot is None else "block_teacher_slot"),
                        entity_id=conf_entity,
                        day=conf_day,
                        slot=conf_slot,
                        source_text=ch.source_text,
                        human_summary=f"Bloquear {conf_entity} el {active_inst.days[conf_day]}",
                        backend=ch.backend,
                    )
                    with st.spinner("Reprogramando con R2 CP-SAT y generando explicacion R6..."):
                        res = apply_change_and_reprogram(
                            instance=active_inst,
                            base_schedule=st.session_state["active_schedule"],
                            change=confirmed,
                            scenario=scenario,
                            time_limit_sec=float(max(time_limit, 5)),
                            llm_cfg=llm_cfg,
                        )
                        st.session_state["i4_last_result"] = res

            if "i4_last_result" in st.session_state:
                res = st.session_state["i4_last_result"]
                rep_new = res["validation_report"]
                stab = res["stability_report"]
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Factible", "SI (0 duras)" if rep_new.feasible else f"NO ({rep_new.hard_violations_total})")
                k2.metric("Sesiones movidas", f"{stab.moved_sessions} / {stab.total_sessions}")
                k3.metric("Estabilidad", f"{stab.stability_pct:.1f}%")
                k4.metric("Penalizacion blanda", f"{rep_new.soft_penalty_total} pts")

                if stab.changes:
                    st.markdown("**Detalle exacto de sesiones reubicadas:**")
                    diff_rows = []
                    inst_u = res["updated_instance"]
                    for item in stab.changes:
                        d0, s0, r0 = item["from"]
                        d1, s1, r1 = item["to"]
                        diff_rows.append(
                            {
                                "Asignatura": item["course_id"],
                                "Tipo": item["type"],
                                "Horario anterior": f"{inst_u.days[d0]} {inst_u.slot_labels[s0]} ({r0})",
                                "Horario nuevo": f"{inst_u.days[d1]} {inst_u.slot_labels[s1]} ({r1})",
                            }
                        )
                    st.dataframe(pd.DataFrame(diff_rows), use_container_width=True, hide_index=True)

                st.markdown("#### Explicacion R6 para Coordinacion")
                st.markdown(res["explanation"])
                st.caption(f"Explicador usado: `{res['explainer_backend']}`")

    # =========================================================================
    # TAB 4: LEADERBOARD, TUNING Y GRAFICAS
    # =========================================================================
    with tabs[3]:
        st.subheader("Tabla de Posiciones Oficial y Resultados Experimentales")
        lb_path = PROJECT_ROOT / "results" / "leaderboard.csv"
        if lb_path.exists():
            st.dataframe(pd.read_csv(lb_path), use_container_width=True, hide_index=True)
        else:
            st.info("Ejecuta `python main.py benchmark` para generar la tabla de posiciones.")

        tun_path = PROJECT_ROOT / "results" / "tuning_results.json"
        if tun_path.exists():
            st.markdown("#### Ajuste de Hiperparametros de R3 (Algoritmo Genetico)")
            tun = json.loads(tun_path.read_text(encoding="utf-8"))
            st.caption(f"Configuracion recomendada en I3: **{tun['recommended_config']}**")
            st.dataframe(pd.DataFrame(tun["results"]), use_container_width=True, hide_index=True)

        charts_dir = PROJECT_ROOT / "results" / "charts"
        if charts_dir.exists():
            st.markdown("#### Graficas Oficiales del Benchmark")
            imgs = sorted(charts_dir.glob("*.png"))
            for i in range(0, len(imgs), 2):
                cols = st.columns(2)
                for col, img in zip(cols, imgs[i : i + 2]):
                    col.image(str(img), caption=img.name, use_container_width=True)


if __name__ == "__main__":
    main()
