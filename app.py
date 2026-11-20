"""Interfaz web en Streamlit para el Sistema Inteligente de Horarios ITIID.

Ejecutar con:
    .venv/bin/streamlit run app.py
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from pathlib import Path

import pandas as pd
import streamlit as st

from kit.instances import build_instance_i1, build_instance_i2, build_instance_i3
from kit.schema import Instance, SCENARIOS, Schedule
from kit.validator import validate_schedule
from src.llm_client import LLMConfig, detect_backend
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
    apply_change_and_reprogram,
    explain_validation_report,
    parse_change_request,
)


PROJECT_ROOT = Path(__file__).resolve().parent
st.set_page_config(
    page_title="Horarios ITIID — Sistema Inteligente",
    page_icon="📅",
    layout="wide",
)


def _schedule_to_grid_df(instance: Instance, schedule: Schedule, mode: str, entity_id: str | None = None) -> pd.DataFrame:
    courses = instance.course_map()
    if mode == "sabana":
        rows = []
        lookup = defaultdict(list)
        for a in schedule.assignments:
            c = courses[a.course_id]
            lookup[(c.group_id, a.day, a.slot)].append(f"{c.name} ({c.teacher_id} | {a.room_id})")
        for d, dname in enumerate(instance.days):
            for s, slabel in enumerate(instance.slot_labels):
                row = {"Dia": dname, "Franja": slabel}
                for g in instance.groups:
                    row[g.name] = " / ".join(lookup.get((g.id, d, s), []))
                rows.append(row)
        return pd.DataFrame(rows)

    cell_map = defaultdict(list)
    blocked: set[tuple[int, int]] = set()
    if mode == "docente" and entity_id:
        blocked = instance.teacher_blocked_slots(entity_id)
        for a in schedule.assignments:
            c = courses[a.course_id]
            if c.teacher_id == entity_id:
                cell_map[(a.day, a.slot)].append(f"{c.name} [{c.group_id} | {a.room_id}]")
    elif mode == "grupo" and entity_id:
        for a in schedule.assignments:
            c = courses[a.course_id]
            if c.group_id == entity_id:
                cell_map[(a.day, a.slot)].append(f"{c.name} [{c.teacher_id} | {a.room_id}]")
    elif mode == "aula" and entity_id:
        r = instance.room_map()[entity_id]
        blocked = r.blocked_slots()
        for a in schedule.assignments:
            c = courses[a.course_id]
            if a.room_id == entity_id:
                cell_map[(a.day, a.slot)].append(f"{c.name} [{c.group_id} | {c.teacher_id}]")

    rows = []
    for s, slabel in enumerate(instance.slot_labels):
        row = {"Franja": slabel}
        for d, dname in enumerate(instance.days):
            if (d, s) in cell_map:
                row[dname] = " / ".join(cell_map[(d, s)])
            elif (d, s) in blocked:
                row[dname] = "[OCUPADO / NO DISP.]"
            else:
                row[dname] = ""
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    st.title("Sistema Inteligente de Generacion de Horarios — ITIID")
    st.caption(
        "Regla de diseno: las asignaciones las decide el motor de optimizacion y las revisa el validador oficial; "
        "el LLM solo traduce preferencias, explica conflictos y propone ajustes."
    )

    with st.sidebar:
        st.header("1. Instancia y Motor")
        source = st.radio("Fuente de datos", ["Instancia del Kit (I1-I3)", "Subir Plantilla Excel (.xlsx)"])
        instance: Instance | None = None

        if source == "Instancia del Kit (I1-I3)":
            choice = st.selectbox("Instancia", ["I3 (ITIID Completo)", "I2 (ITIID 7o Cuatrimestre)", "I1 (ITC-2007)"])
            if choice.startswith("I1"):
                instance = build_instance_i1(PROJECT_ROOT / "data")
            elif choice.startswith("I2"):
                instance = build_instance_i2()
            else:
                instance = build_instance_i3()
        else:
            up = st.file_uploader("Plantilla Excel", type=["xlsx"])
            if up is not None:
                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                    tmp.write(up.getvalue())
                    tmp_path = Path(tmp.name)
                try:
                    instance, warns = import_instance_from_excel(tmp_path)
                    for w in warns:
                        st.warning(w)
                except ExcelValidationError as err:
                    st.error(str(err))
                    st.stop()
            else:
                st.info("Sube un archivo .xlsx o usa una instancia del kit.")
                st.stop()

        assert instance is not None
        st.markdown(
            f"**Resumen instancia:** {len(instance.groups)} grupos · {len(instance.teachers)} docentes · "
            f"{len(instance.rooms)} aulas/labs · {instance.total_sessions()} sesiones"
        )

        st.divider()
        method = st.selectbox("Motor de optimizacion", ["CP-SAT (R2)", "Algoritmo Genetico (R3)", "Busqueda Local (R4)"])
        scenario = st.selectbox(
            "Escenario de pesos",
            ["C", "A", "B"],
            format_func=lambda s: f"Escenario {s}: {SCENARIOS[s].description}",
        )
        time_limit = st.slider("Limite de tiempo (segundos)", 2, 20, 5)
        prov_choice = st.selectbox("Backend LLM (R5/R6)", ["auto", "ollama", "gemini", "openai", "offline"])
        llm_cfg = LLMConfig(provider=prov_choice)
        det_p, det_m = detect_backend(llm_cfg)
        st.caption(f"LLM detectado: `{det_p}` ({det_m})")

    tabs = st.tabs(
        [
            "Generar y Validar Horario",
            "R5: Preferencias en Espanol",
            "I4 / R6: Reprogramacion y Explicador",
            "Tabla de Posiciones (Benchmark)",
        ]
    )

    # TAB 1: Generar y Validar
    with tabs[0]:
        if st.button("Ejecutar optimizador y pasar por validador oficial", type="primary"):
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

        if "active_schedule" in st.session_state and st.session_state["active_instance"].id == instance.id:
            sch: Schedule = st.session_state["active_schedule"]
            rep = st.session_state["active_report"]

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Factible", "SI (0 duras)" if rep.feasible else f"NO ({rep.hard_violations_total})")
            c2.metric("Penalizacion blanda", rep.soft_penalty_total)
            c3.metric("Horas muertas grupos", rep.student_dead_hours)
            c4.metric("Huecos docentes", rep.teacher_gaps)
            c5.metric("Preferencias cumplidas", f"{rep.preferences_pct:.1f}%")

            view_mode = st.radio(
                "Vista del horario",
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
            if view_mode == "grupo":
                entity_id = st.selectbox("Grupo", [g.id for g in instance.groups])
            elif view_mode == "docente":
                entity_id = st.selectbox("Docente", [t.id for t in instance.teachers])
            elif view_mode == "aula":
                entity_id = st.selectbox("Aula / Laboratorio", [r.id for r in instance.rooms])

            df_view = _schedule_to_grid_df(instance, sch, view_mode, entity_id)
            st.dataframe(df_view, use_container_width=True, hide_index=True)

            # Exportacion Excel y PDF (R1)
            with st.expander("Exportar reportes oficiales (Excel y PDF)"):
                if st.button("Generar paquetes Excel y PDF"):
                    tmp_dir = Path(tempfile.mkdtemp(prefix="itiid_export_"))
                    ex_paths = export_schedule_excels(instance, sch, tmp_dir / "excel", report=rep)
                    pdf_paths = export_schedule_pdfs(instance, sch, tmp_dir / "pdf", report=rep)
                    col_e, col_p = st.columns(2)
                    with col_e:
                        st.subheader("Excel (.xlsx)")
                        for k, p in ex_paths.items():
                            st.download_button(f"Descargar {p.name}", p.read_bytes(), file_name=p.name, key=f"ex_{k}")
                    with col_p:
                        st.subheader("PDF (.pdf)")
                        for k, p in pdf_paths.items():
                            st.download_button(f"Descargar {p.name}", p.read_bytes(), file_name=p.name, key=f"pdf_{k}")

            with st.expander("Explicacion R6 para Coordinacion Academica", expanded=True):
                exp_text, exp_backend = explain_validation_report(instance, rep, llm_cfg=llm_cfg)
                st.markdown(exp_text)
                st.caption(f"Explicador usado: `{exp_backend}`")

    # TAB 2: R5 Extractor de Preferencias
    with tabs[1]:
        st.subheader("R5: Traducir preferencias en espanol a restricciones JSON (con revision humana)")
        col_a, col_b = st.columns([1, 2])
        with col_a:
            tid = st.selectbox("Docente anonimizado", [t.id for t in instance.teachers], key="r5_tid")
        with col_b:
            pref_text = st.text_input(
                "Preferencia escrita en espanol",
                value="Prefiero no tener clases los viernes y horario compacto sin huecos.",
            )
        if st.button("Extraer restricciones JSON"):
            extracted, backend = extract_preferences_from_text(tid, pref_text, llm_cfg=llm_cfg)
            st.info(f"Backend: `{backend}` | Estado inicial: `pending_review` (requiere aprobacion humana)")
            st.json([p.model_dump() for p in extracted])

        st.divider()
        st.subheader("Evaluacion oficial de R5 (Precision, Recall y F1 por tipo)")
        if st.button("Evaluar sobre dataset anotado (25 ejemplos)"):
            ev_res = evaluate_extractor(llm_cfg=llm_cfg)
            ov = ev_res["overall"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Precision (micro)", f"{ov['precision'] * 100:.1f}%")
            m2.metric("Recall (micro)", f"{ov['recall'] * 100:.1f}%")
            m3.metric("F1 Micro", f"{ov['f1_micro'] * 100:.1f}%")
            m4.metric("F1 Macro", f"{ov['f1_macro'] * 100:.1f}%")
            st.dataframe(pd.DataFrame(ev_res["by_type"]).T, use_container_width=True)

    # TAB 3: I4 / R6 Reprogramacion
    with tabs[2]:
        st.subheader("Escenario de cambio (I4): Reprogramar moviendo lo menos posible")
        if "active_schedule" not in st.session_state:
            st.warning("Primero genera un horario base en la pestana 'Generar y Validar Horario'.")
        else:
            change_txt = st.text_input(
                "Describe el cambio en espanol",
                value=f"El docente {instance.teachers[0].id} ya no puede los miercoles.",
            )
            if st.button("Traducir cambio (R6) y Reprogramar (R2 CP-SAT)", type="primary"):
                with st.spinner("Traduciendo cambio y reprogramando con minima perturbacion..."):
                    ch_req = parse_change_request(change_txt, instance, llm_cfg=llm_cfg)
                    st.info(f"Cambio estructurado confirmado: **{ch_req.human_summary}** (`{ch_req.backend}`)")
                    res = apply_change_and_reprogram(
                        instance=st.session_state["active_instance"],
                        base_schedule=st.session_state["active_schedule"],
                        change=ch_req,
                        scenario=scenario,
                        time_limit_sec=float(time_limit),
                        llm_cfg=llm_cfg,
                    )
                    rep_new = res["validation_report"]
                    stab = res["stability_report"]
                    k1, k2, k3, k4 = st.columns(4)
                    k1.metric("Factible", "SI (0 duras)" if rep_new.feasible else f"NO ({rep_new.hard_violations_total})")
                    k2.metric("Sesiones movidas", f"{stab.moved_sessions} / {stab.total_sessions}")
                    k3.metric("Estabilidad", f"{stab.stability_pct:.1f}%")
                    k4.metric("Penalizacion blanda", rep_new.soft_penalty_total)
                    st.markdown(res["explanation"])

    # TAB 4: Leaderboard
    with tabs[3]:
        st.subheader("Tabla de Posiciones Oficial (Benchmark guardado)")
        lb_path = PROJECT_ROOT / "results" / "leaderboard.csv"
        if lb_path.exists():
            st.dataframe(pd.read_csv(lb_path), use_container_width=True, hide_index=True)
            charts_dir = PROJECT_ROOT / "results" / "charts"
            if charts_dir.exists():
                for img in sorted(charts_dir.glob("*.png")):
                    st.image(str(img), use_container_width=True)
        else:
            st.info("Ejecuta `python main.py benchmark` para generar la tabla de posiciones y graficas.")


if __name__ == "__main__":
    main()
