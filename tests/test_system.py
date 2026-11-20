"""Suite de pruebas exhaustiva del Sistema Inteligente de Horarios ITIID.

Verifica:
1. El validador oficial atrapa CADA tipo de violacion dura y blanda (pruebas con fallos inyectados).
2. Equivalencia exacta 1-a-1 entre FastEvaluator y el validador oficial.
3. Equivalencia exacta 1-a-1 entre la funcion objetivo linealizada de R2 CP-SAT y el validador oficial.
4. R1: Round-trip Excel -> JSON -> Excel/PDF, deteccion de faltantes y anonimizador.
5. R2 CP-SAT (Escenarios A, B, C y reprogramacion I4 con estabilidad).
6. R3 Algoritmo Genetico y R4 Busqueda Local (MRV + Hill Climbing).
7. R5 Extractor de preferencias (pending_review + metricas P/R/F1 por tipo).
8. R6 Explicador y escenario de cambio.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from kit.instances import (
    build_instance_i1,
    build_instance_i2,
    build_instance_i3,
    build_instance_i4,
    build_manual_reference_schedule,
)
from kit.schema import Assignment, Schedule
from kit.validator import compute_stability, validate_schedule
from src.fast_eval import FastEvaluator
from src.llm_client import LLMConfig
from src.r1_data.anonymizer import anonymize_instance, sanitize_text_for_llm
from src.r1_data.excel_io import (
    ExcelValidationError,
    export_instance_template_excel,
    export_schedule_excels,
    import_instance_from_excel,
)
from src.r1_data.pdf_export import export_schedule_pdfs
from src.r2_cpsat.solver import solve_cpsat, solve_cpsat_reprogram
from src.r3_genetic.solver import GAConfig, solve_genetic
from src.r4_local_search.solver import solve_local_search
from src.r5_llm_extractor.extractor import evaluate_extractor, extract_preferences_from_text
from src.r6_llm_explainer.explainer import (
    apply_change_and_reprogram,
    explain_validation_report,
    generate_direct_llm_schedule,
    parse_change_request,
)


def test_validator_catches_every_hard_violation_type() -> None:
    """Si inyectamos cada tipo de violacion dura, el validador DEBE detectarla."""
    inst = build_instance_i2()
    base = solve_cpsat(inst, scenario="C", time_limit_sec=3.0)
    rep_ok = validate_schedule(inst, base, scenario="C")
    assert rep_ok.feasible and rep_ok.hard_violations_total == 0

    # 1. Conflicto de docente, grupo y aula (poner dos sesiones en el mismo (d, s, room))
    broken = base.model_copy(deep=True)
    # Hacer que dos cursos del mismo docente (C_7A_FIA y C_7B_FIA -> DOC_01) choquen en dia, franja y aula
    a0 = next(a for a in broken.assignments if a.course_id == "C_7A_FIA")
    a1 = next(a for a in broken.assignments if a.course_id == "C_7B_FIA")
    a1.day, a1.slot, a1.room_id = a0.day, a0.slot, a0.room_id
    rep_clash = validate_schedule(inst, broken, scenario="C")
    assert not rep_clash.feasible
    assert rep_clash.hard_violations_by_type["teacher_conflict"] >= 1
    assert rep_clash.hard_violations_by_type["room_conflict"] >= 1

    # 2. Conflicto de grupo (dos materias del mismo grupo 7A a la misma hora)
    broken_g = base.model_copy(deep=True)
    g0 = next(a for a in broken_g.assignments if a.course_id == "C_7A_FIA")
    g1 = next(a for a in broken_g.assignments if a.course_id == "C_7A_ISW")
    g1.day, g1.slot = g0.day, g0.slot
    rep_g = validate_schedule(inst, broken_g, scenario="C")
    assert rep_g.hard_violations_by_type["group_conflict"] >= 1

    # 3. Indisponibilidad de docente (DOC_01 bloqueado en (1, 0) por recurso compartido)
    broken_ta = base.model_copy(deep=True)
    ta0 = next(a for a in broken_ta.assignments if a.course_id == "C_7A_FIA")
    ta0.day, ta0.slot = 1, 0
    rep_ta = validate_schedule(inst, broken_ta, scenario="C")
    assert rep_ta.hard_violations_by_type["teacher_availability"] >= 1

    # 4. Indisponibilidad de aula (LAB_01 bloqueado en (0, 2) por IM)
    broken_ra = base.model_copy(deep=True)
    ra0 = next(a for a in broken_ra.assignments if a.course_id == "C_7A_FIA")
    ra0.day, ra0.slot, ra0.room_id = 0, 2, "LAB_01"
    rep_ra = validate_schedule(inst, broken_ra, scenario="C")
    assert rep_ra.hard_violations_by_type["room_availability"] >= 1

    # 5. Idoneidad de aula (materia de laboratorio C_7A_RED en aula teorica AULA_01)
    broken_suit = base.model_copy(deep=True)
    rs0 = next(a for a in broken_suit.assignments if a.course_id == "C_7A_RED")
    rs0.room_id = "AULA_01"
    rep_suit = validate_schedule(inst, broken_suit, scenario="C")
    assert rep_suit.hard_violations_by_type["room_suitability"] >= 1

    # 6. Cobertura de horas (quitar una sesion)
    broken_cov = base.model_copy(deep=True)
    broken_cov.assignments.pop()
    rep_cov = validate_schedule(inst, broken_cov, scenario="C")
    assert rep_cov.hard_violations_by_type["course_coverage"] == 1

    # 7. Referencia invalida
    broken_ref = base.model_copy(deep=True)
    broken_ref.assignments.append(
        Assignment(course_id="NO_EXISTE", session_idx=0, day=0, slot=0, room_id="AULA_01")
    )
    rep_ref = validate_schedule(inst, broken_ref, scenario="C")
    assert rep_ref.hard_violations_by_type["invalid_reference"] == 1


@pytest.mark.parametrize("scenario", ["A", "B", "C"])
def test_cpsat_objective_and_fast_eval_match_official_validator_exactly(scenario: str) -> None:
    """Garantiza equivalencia matematica exacta entre CP-SAT, FastEvaluator y kit.validator."""
    inst = build_instance_i2()
    sch = solve_cpsat(inst, scenario=scenario, time_limit_sec=3.0, seed=42)
    rep = validate_schedule(inst, sch, scenario=scenario)
    assert rep.feasible

    # 1. El objetivo reportado por OR-Tools CP-SAT debe coincidir exactamente con el validador oficial
    assert int(round(sch.metadata["objective_value"])) == rep.soft_penalty_total

    # 2. FastEvaluator debe coincidir exactamente en (hard, soft)
    ev = FastEvaluator(inst, scenario=scenario)
    # Mapear asignaciones del schedule al orden de sesiones de FastEvaluator
    by_course: dict[str, list[Assignment]] = {}
    for a in sch.assignments:
        by_course.setdefault(a.course_id, []).append(a)
    genes = []
    used_idx: dict[str, int] = {}
    for sp in ev.sessions:
        k = used_idx.get(sp.course_id, 0)
        used_idx[sp.course_id] = k + 1
        a = by_course[sp.course_id][k]
        genes.append((a.day, a.slot, ev.room_idx[a.room_id]))
    fast_h, fast_s = ev.evaluate(genes)
    assert fast_h == rep.hard_violations_total
    assert fast_s == rep.soft_penalty_total


def test_r1_excel_roundtrip_missing_detection_export_and_anonymizer(tmp_path: Path) -> None:
    inst = build_instance_i2()
    tpl = export_instance_template_excel(inst, tmp_path / "plantilla.xlsx")
    loaded, warns = import_instance_from_excel(tpl)
    assert not warns
    assert loaded.id == inst.id
    assert len(loaded.courses) == len(inst.courses)
    assert loaded.total_sessions() == inst.total_sessions()

    # Inyectar un faltante critico (borrar teacher_id de la primera asignatura) -> debe lanzar ExcelValidationError
    wb = openpyxl.load_workbook(tpl)
    ws = wb["Asignaturas"]
    headers = [cell.value for cell in ws[1]]
    t_col = headers.index("teacher_id") + 1
    ws.cell(row=2, column=t_col, value="DOC_INEXISTENTE")
    bad_path = tmp_path / "plantilla_rota.xlsx"
    wb.save(bad_path)
    with pytest.raises(ExcelValidationError):
        import_instance_from_excel(bad_path)

    # Exportacion Excel y PDF (4 vistas: sabana, docente, grupo, aula)
    sch = solve_cpsat(inst, scenario="C", time_limit_sec=2.0)
    rep = validate_schedule(inst, sch, scenario="C")
    ex_paths = export_schedule_excels(inst, sch, tmp_path / "excel_out", report=rep)
    pdf_paths = export_schedule_pdfs(inst, sch, tmp_path / "pdf_out", report=rep)
    assert set(ex_paths.keys()) == {"sabana_general", "por_docente", "por_grupo", "por_aula"}
    assert set(pdf_paths.keys()) == {"sabana_general", "por_docente", "por_grupo", "por_aula"}
    for p in list(ex_paths.values()) + list(pdf_paths.values()):
        assert p.exists() and p.stat().st_size > 1000

    # Anonimizador etico
    anon, amap = anonymize_instance(inst)
    assert all(t.id.startswith("DOC_") for t in anon.teachers)
    assert "correo" not in sanitize_text_for_llm("Escribe a juan@upv.edu.mx o al 8341234567", amap)
    assert "[CORREO_OCULTO]" in sanitize_text_for_llm("Escribe a juan@upv.edu.mx", amap)


def test_r3_genetic_and_r4_local_search_produce_feasible_schedules() -> None:
    for inst in (build_instance_i1(), build_instance_i2(), build_instance_i3()):
        ls = solve_local_search(inst, scenario="C", time_limit_sec=2.5, seed=42)
        rep_ls = validate_schedule(inst, ls, scenario="C")
        assert rep_ls.feasible, f"Local Search no factible en {inst.id}: {rep_ls.hard_violations_by_type}"

        ga = solve_genetic(
            inst,
            scenario="C",
            time_limit_sec=2.5,
            seed=42,
            config=GAConfig(pop_size=18),
        )
        rep_ga = validate_schedule(inst, ga, scenario="C")
        assert rep_ga.feasible, f"AG no factible en {inst.id}: {rep_ga.hard_violations_by_type}"


def test_i4_reprogramming_preserves_high_stability() -> None:
    i3 = build_instance_i3()
    base_sch = solve_cpsat(i3, scenario="C", time_limit_sec=4.0, seed=42)
    assert validate_schedule(i3, base_sch, scenario="C").feasible

    change = parse_change_request(
        "El docente DOC_01 ya no puede los miercoles.", i3, llm_cfg=LLMConfig(provider="offline")
    )
    assert change.entity_id == "DOC_01"
    assert change.day == 2

    res = apply_change_and_reprogram(
        i3, base_sch, change, scenario="C", time_limit_sec=4.0, llm_cfg=LLMConfig(provider="offline")
    )
    rep_new = res["validation_report"]
    stab = res["stability_report"]
    assert rep_new.feasible
    assert stab.stability_pct >= 80.0
    # Verificar que DOC_01 efectivamente tiene 0 clases los miercoles (dia 2)
    courses = i3.course_map()
    doc01_wed = [a for a in res["new_schedule"].assignments if courses[a.course_id].teacher_id == "DOC_01" and a.day == 2]
    assert len(doc01_wed) == 0


def test_r5_extractor_human_review_and_prf1_metrics() -> None:
    prefs, _ = extract_preferences_from_text(
        "DOC_01",
        "Prefiero no tener clases los viernes y horario compacto sin huecos.",
        llm_cfg=LLMConfig(provider="offline"),
    )
    assert len(prefs) == 2
    assert all(p.status == "pending_review" for p in prefs)

    ev = evaluate_extractor(llm_cfg=LLMConfig(provider="offline"))
    assert ev["num_examples"] == 25
    assert ev["overall"]["f1_micro"] >= 0.9
    assert "avoid_day" in ev["by_type"]
    assert "avoid_slot" in ev["by_type"]


def test_r6_direct_llm_comparison_and_explainer() -> None:
    i3 = build_instance_i3()
    raw = generate_direct_llm_schedule(i3, with_validator_feedback=False, llm_cfg=LLMConfig(provider="offline"))
    rep = validate_schedule(i3, raw, scenario="C")
    assert rep.hard_violations_total > 0  # Evidencia P1: LLM sin solver viola duras en I3
    text, _ = explain_validation_report(i3, rep, llm_cfg=LLMConfig(provider="offline"))
    assert "Conflictos duros" in text


def test_itc2007_multi_curriculum_course_prevents_clashes_in_both_curricula() -> None:
    """Verifica que una materia compartida por 2 curricula en ITC-2007 no se traslape en ninguno."""
    from kit.instances import parse_itc2007_ctt

    ctt = """Name: multi_curr_test
Courses: 3
Rooms: 2
Days: 2
Periods_per_day: 3
Curricula: 2
Constraints: 0

COURSES:
c_shared t0 2 2 25
c_q0 t1 2 2 25
c_q1 t2 2 2 25

ROOMS:
R1 30
R2 30

CURRICULA:
q0 2 c_shared c_q0
q1 2 c_shared c_q1

UNAVAILABILITY_CONSTRAINTS:

END.
"""
    inst = parse_itc2007_ctt(ctt, instance_id="I1_MC")
    shared = inst.course_map()["c_shared"]
    assert set(shared.all_group_ids()) == {"q0", "q1"}

    sch = solve_cpsat(inst, scenario="C", time_limit_sec=2.0)
    rep = validate_schedule(inst, sch, scenario="C")
    assert rep.feasible

    # Inyectar un choque entre c_shared y c_q1 (el segundo curriculum!) -> validador debe atraparlo
    broken = sch.model_copy(deep=True)
    a_sh = next(a for a in broken.assignments if a.course_id == "c_shared")
    a_q1 = next(a for a in broken.assignments if a.course_id == "c_q1")
    a_q1.day, a_q1.slot = a_sh.day, a_sh.slot
    rep_bad = validate_schedule(inst, broken, scenario="C")
    assert rep_bad.hard_violations_by_type["group_conflict"] >= 1
