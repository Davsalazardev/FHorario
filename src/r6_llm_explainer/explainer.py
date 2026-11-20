"""R6: LLM Explicador, Horario Directo con LLM (P1) y Escenario de Cambio (I4).

Regla del proyecto:
- El horario pedido directo al LLM solo sirve para compararlo y tambien pasa por el validador.
- R6 explica conflictos en lenguaje claro para coordinacion academica y traduce pedidos de cambio
  a parametros estructurados para que R2 (CP-SAT) reprograme moviendo lo menos posible.
"""

from __future__ import annotations

import copy
import json
import random
import re
import time
import unicodedata
from typing import Any

from pydantic import BaseModel

from kit.schema import Assignment, Instance, Preference, Schedule
from kit.validator import StabilityReport, ValidationReport, compute_stability, validate_schedule
from src.llm_client import LLMConfig, LLMUnavailableError, call_llm_text, parse_json_from_llm
from src.r2_cpsat.solver import solve_cpsat_reprogram


class ChangeRequest(BaseModel):
    """Peticion de cambio estructurada traducida por R6 (requiere confirmacion humana)."""

    action: str  # "block_teacher_day", "block_teacher_slot", "block_room_slot"
    entity_id: str
    day: int
    slot: int | None = None
    source_text: str
    human_summary: str
    backend: str = "offline_nlp_baseline"


_DAY_MAP = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
}


def _norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


# =============================================================================
# 1. HORARIO PEDIDO DIRECTO AL LLM (P1) - CON Y SIN CORRECCIONES DEL VALIDADOR
# =============================================================================

_DIRECT_SCHEDULE_SYSTEM = """Genera un horario universitario completo en JSON compacto.
Devuelve UNICAMENTE JSON con una lista de tuplas [course_id, day, slot, room_id]:
{"a": [["c0001", 0, 0, "R1"], ["c0001", 1, 2, "R1"]]}
Sin texto extra, respetando dias 0..D-1, franjas 0..S-1 y el numero exacto de sesiones por curso.
"""


def _simulate_autoregressive_llm_schedule(
    instance: Instance,
    seed: int = 77,
    with_validator_feedback: bool = False,
    feedback_rounds: int = 3,
) -> Schedule:
    """Simulacion transparente offline del comportamiento de un LLM autonomo (Alharbi, 2026).

    Un LLM genera asignaciones secuencialmente con atencion local sin un solver global:
    - En instancias pequenas comete pocos choques; conforme crece el numero de sesiones/recursos
      compartidos, los conflictos duros aumentan (choques de docente/aula y bloqueos ignorados).
    - Con retroalimentacion del validador, corrige una parte de los errores reportados, pero sin
      busqueda combinatoria exacta suele dejar violaciones residuales en instancias grandes.
    """
    t0 = time.perf_counter()
    rng = random.Random(seed)
    valid_slots = instance.valid_slots()
    courses = instance.courses
    rooms = instance.rooms
    n_sessions = instance.total_sessions()

    # Probabilidad de perder consistencia global crece con la escala (efecto contexto/atencion)
    scale_factor = min(0.42, 0.08 + n_sessions / 420.0)
    assignments: list[Assignment] = []
    local_g_used: set[tuple[str, int, int]] = set()
    local_t_used: set[tuple[str, int, int]] = set()

    for c in courses:
        comp_rooms = instance.compatible_rooms(c.id) or rooms
        for idx in range(c.sessions_per_week):
            # Intento local miope (suele olvidar bloqueos compartidos y aulas de otros grupos)
            chosen_d, chosen_s, chosen_r = 0, valid_slots[0], comp_rooms[0].id
            for _attempt in range(10):
                d = rng.randrange(instance.num_days)
                s = rng.choice(valid_slots)
                r = rng.choice(comp_rooms if rng.random() > 0.08 else rooms)
                # Con probabilidad scale_factor el LLM "pierde de vista" choques previos
                if rng.random() < scale_factor:
                    chosen_d, chosen_s, chosen_r = d, s, r.id
                    break
                if (c.group_id, d, s) not in local_g_used and (c.teacher_id, d, s) not in local_t_used:
                    chosen_d, chosen_s, chosen_r = d, s, r.id
                    break
            local_g_used.add((c.group_id, chosen_d, chosen_s))
            local_t_used.add((c.teacher_id, chosen_d, chosen_s))
            assignments.append(
                Assignment(
                    course_id=c.id,
                    session_idx=idx,
                    day=chosen_d,
                    slot=chosen_s,
                    room_id=chosen_r,
                )
            )

    sch = Schedule(
        instance_id=instance.id,
        method="LLM directo (sin validador)" if not with_validator_feedback else "LLM directo + feedback validador",
        scenario="C",
        assignments=assignments,
        solve_time_sec=0.0,
        seed=seed,
        metadata={"backend": "simulated_offline_llm_alharbi2026", "live_llm": False},
    )

    if with_validator_feedback:
        # Simula 3 rondas donde el validador reporta conflictos y el LLM reasigna localmente
        course_map = instance.course_map()
        for _round in range(feedback_rounds):
            rep = validate_schedule(instance, sch, scenario="C")
            if rep.feasible:
                break
            # Recolectar cursos mencionados en violaciones y mover algunos localmente
            flagged_courses = {
                v.entities.get("course_id") for v in rep.hard_violation_details if "course_id" in v.entities
            }
            for v in rep.hard_violation_details:
                for cid in v.entities.get("courses", []):
                    flagged_courses.add(cid)

            t_used = set()
            g_used = set()
            r_used = set()
            for a in sch.assignments:
                c = course_map[a.course_id]
                t_used.add((c.teacher_id, a.day, a.slot))
                g_used.add((c.group_id, a.day, a.slot))
                r_used.add((a.room_id, a.day, a.slot))

            for i, a in enumerate(sch.assignments):
                if a.course_id not in flagged_courses or rng.random() > 0.65:
                    continue
                c = course_map[a.course_id]
                comp_rooms = instance.compatible_rooms(c.id) or rooms
                t_blk = instance.teacher_blocked_slots(c.teacher_id)
                for _ in range(15):
                    d = rng.randrange(instance.num_days)
                    s = rng.choice(valid_slots)
                    if (d, s) in t_blk:
                        continue
                    r = rng.choice(comp_rooms)
                    if (d, s) in r.blocked_slots():
                        continue
                    if (
                        (c.teacher_id, d, s) not in t_used
                        and (c.group_id, d, s) not in g_used
                        and (r.id, d, s) not in r_used
                    ):
                        sch.assignments[i] = Assignment(
                            course_id=a.course_id,
                            session_idx=a.session_idx,
                            day=d,
                            slot=s,
                            room_id=r.id,
                        )
                        t_used.add((c.teacher_id, d, s))
                        g_used.add((c.group_id, d, s))
                        r_used.add((r.id, d, s))
                        break

        sch.metadata["feedback_rounds"] = feedback_rounds

    sch.solve_time_sec = round(time.perf_counter() - t0, 4)
    return sch


def _parse_llm_assignments_lenient(raw_text: str) -> tuple[list[Assignment], str | None]:
    """Parsea asignaciones devueltas por un LLM real (formato compacto [cid,d,s,rid] o dict)."""
    try:
        parsed = parse_json_from_llm(raw_text)
    except Exception as exc:
        return [], f"json_parse_error: {exc}"
    if isinstance(parsed, dict):
        items = parsed.get("a") or parsed.get("assignments") or []
    else:
        items = parsed
    if not isinstance(items, list):
        return [], "unexpected_json_structure"
    out: list[Assignment] = []
    per_course: dict[str, int] = {}
    for item in items:
        if isinstance(item, list) and len(item) >= 4:
            cid = str(item[0]).strip()
            rid = str(item[3]).strip()
            try:
                d, s = int(item[1]), int(item[2])
            except (TypeError, ValueError):
                d, s = -1, -1
            sidx = per_course.get(cid, 0)
            per_course[cid] = sidx + 1
            if cid:
                out.append(Assignment(course_id=cid, session_idx=sidx, day=d, slot=s, room_id=rid))
        elif isinstance(item, dict):
            cid = str(item.get("course_id", item.get("course", ""))).strip()
            rid = str(item.get("room_id", item.get("room", ""))).strip()
            try:
                d = int(item.get("day", -1))
                s = int(item.get("slot", -1))
            except (TypeError, ValueError):
                d, s = -1, -1
            sidx_raw = item.get("session_idx")
            if sidx_raw is None:
                sidx = per_course.get(cid, 0)
            else:
                try:
                    sidx = int(sidx_raw)
                except (TypeError, ValueError):
                    sidx = per_course.get(cid, 0)
            per_course[cid] = per_course.get(cid, 0) + 1
            if cid:
                out.append(Assignment(course_id=cid, session_idx=sidx, day=d, slot=s, room_id=rid))
    return out, None


def generate_direct_llm_schedule(
    instance: Instance,
    with_validator_feedback: bool = False,
    feedback_rounds: int = 2,
    llm_cfg: LLMConfig | None = None,
    seed: int = 77,
) -> Schedule:
    """Pide un horario directo a un LLM (Ollama/API) o corre el baseline documentado si esta offline."""
    t0 = time.perf_counter()
    method_name = (
        "LLM directo + feedback validador"
        if with_validator_feedback
        else "LLM directo (sin validador)"
    )

    compact_prompt = {
        "days": list(range(instance.num_days)),
        "valid_slots": instance.valid_slots(),
        "rooms": [
            {"id": r.id, "type": r.room_type, "cap": r.capacity, "blocked": sorted(r.blocked_slots())}
            for r in instance.rooms
        ],
        "teachers_blocked": {
            t.id: sorted(instance.teacher_blocked_slots(t.id)) for t in instance.teachers
        },
        "courses": [
            {
                "id": c.id,
                "group": c.group_id,
                "teacher": c.teacher_id,
                "sessions": c.sessions_per_week,
                "room_type": c.room_type,
            }
            for c in instance.courses
        ],
    }

    # Dar timeout acotado y limite de tokens para JSON compacto (evita loops infinitos en Ollama)
    eff_cfg = copy.deepcopy(llm_cfg) if llm_cfg else LLMConfig()
    eff_cfg.timeout_sec = max(eff_cfg.timeout_sec, 90.0)
    eff_cfg.max_tokens = eff_cfg.max_tokens or min(2400, max(900, instance.total_sessions() * 18))

    try:
        user_msg = (
            f"Genera un horario completo valido sin traslapes de docente, grupo ni aula.\n"
            f"Devuelve exactamente {instance.total_sessions()} elementos en 'a': [[course_id, day, slot, room_id], ...].\n"
            f"Instancia:\n{json.dumps(compact_prompt, ensure_ascii=False)}"
        )
        try:
            raw, backend = call_llm_text(_DIRECT_SCHEDULE_SYSTEM, user_msg, cfg=eff_cfg, expect_json=True)
            assignments, parse_err = _parse_llm_assignments_lenient(raw)
        except LLMUnavailableError:
            raise
        except Exception as exc:
            assignments, parse_err = [], f"llm_runtime_error: {exc}"
            backend = f"{eff_cfg.provider}:{eff_cfg.model or 'auto'}"

        sch = Schedule(
            instance_id=instance.id,
            method=method_name,
            scenario="C",
            assignments=assignments,
            solve_time_sec=round(time.perf_counter() - t0, 4),
            metadata={"backend": backend, "live_llm": True, "parse_error": parse_err},
        )
        if with_validator_feedback:
            for r_idx in range(feedback_rounds):
                rep = validate_schedule(instance, sch, scenario="C")
                if rep.feasible:
                    break
                err_summary = [v.message for v in rep.hard_violation_details[:15]]
                fb_msg = (
                    f"Instancia:\n{json.dumps(compact_prompt, ensure_ascii=False)}\n\n"
                    f"Tu horario previo tuvo {rep.hard_violations_total} violaciones duras "
                    f"({rep.hard_violations_by_type}). Ejemplos:\n"
                    + "\n".join(f"- {m}" for m in err_summary)
                    + f"\nCorrige y devuelve JSON compacto 'a' con exactamente {instance.total_sessions()} tuplas [course_id, day, slot, room_id]."
                )
                try:
                    raw2, _ = call_llm_text(_DIRECT_SCHEDULE_SYSTEM, fb_msg, cfg=eff_cfg, expect_json=True)
                    assignments2, parse_err2 = _parse_llm_assignments_lenient(raw2)
                    if assignments2:
                        sch.assignments = assignments2
                    sch.metadata["last_parse_error"] = parse_err2
                except Exception as exc:
                    sch.metadata["last_parse_error"] = f"llm_feedback_error: {exc}"
                sch.metadata["feedback_rounds"] = r_idx + 1
            sch.solve_time_sec = round(time.perf_counter() - t0, 4)
        return sch
    except LLMUnavailableError:
        return _simulate_autoregressive_llm_schedule(
            instance,
            seed=seed,
            with_validator_feedback=with_validator_feedback,
            feedback_rounds=max(feedback_rounds, 3),
        )


# =============================================================================
# 2. EXPLICADOR DE CONFLICTOS Y CALIDAD PARA COORDINACION (P4)
# =============================================================================

_EXPLAIN_SYSTEM_PROMPT = """Eres R6, el asistente explicador del Sistema Inteligente de Horarios de ITIID (UPV).
Tu trabajo NO es asignar clases, sino explicar de forma clara, breve y accionable para la coordinacion academica:
1. Si el horario es valido o que conflictos duros detecto el validador oficial.
2. Donde se concentran las penalizaciones blandas:
   - student_gaps = horas muertas entre clases de estudiantes.
   - teacher_gaps = huecos libres entre clases en el horario de un docente (NO son faltas ni inasistencias).
   - course_spread = sesiones repetidas de la misma materia en un mismo dia.
   - preferences = preferencias docentes blandas no cumplidas.
3. Que 2 o 3 ajustes concretos conviene considerar (ej. cambiar a Escenario A o B) antes de re-ejecutar CP-SAT.
Usa listas Markdown con guiones '-' (nunca escribas la palabra 'Vineta'). Se fiel a los numeros del reporte.
"""


def _deterministic_explanation(
    instance: Instance,
    report: ValidationReport,
    stability: StabilityReport | None = None,
) -> str:
    lines: list[str] = []
    status_txt = "VALIDO (0 violaciones duras)" if report.feasible else f"NO VALIDO ({report.hard_violations_total} violaciones duras)"
    lines.append(f"### Diagnostico del horario — {instance.name}")
    lines.append(
        f"- **Estado oficial del validador:** {status_txt} | **Metodo:** {report.method} | **Escenario:** {report.scenario}"
    )
    lines.append(
        f"- **Calidad blanda:** {report.soft_penalty_total} pts "
        f"(horas muertas estudiantes: {report.student_dead_hours}, huecos docentes: {report.teacher_gaps}, "
        f"preferencias cumplidas: {report.preferences_fulfilled}/{report.preferences_total} = {report.preferences_pct:.1f}%)."
    )

    if not report.feasible:
        lines.append("\n#### Conflictos duros detectados (requieren correccion antes de publicar)")
        for vtype, cnt in report.hard_violations_by_type.items():
            if cnt > 0:
                lines.append(f"- `{vtype}`: **{cnt}** incidencias.")
        for detail in report.hard_violation_details[:5]:
            lines.append(f"  - Ejemplo: {detail.message}")
        if len(report.hard_violation_details) > 5:
            lines.append(f"  - *(y {len(report.hard_violation_details) - 5} incidencias adicionales).*")
        lines.append(
            "\n#### Accion recomendada\n"
            "- No usar este horario directamente. Re-ejecutar **R2 (CP-SAT)** sobre esta instancia o revisar "
            "si los bloqueos de recursos compartidos dejan algun docente/laboratorio sobre-suscrito."
        )
    else:
        # Identificar principal fuente de penalizacion blanda
        ranked_soft = sorted(
            report.soft_weighted_breakdown.items(), key=lambda kv: kv[1], reverse=True
        )
        top_factors = [(k, v) for k, v in ranked_soft if v > 0][:3]
        if top_factors:
            lines.append("\n#### Principales oportunidades de mejora (criterios blandos)")
            labels = {
                "student_gaps": "Horas muertas en grupos",
                "student_excessive_day": "Jornadas largas de estudiantes",
                "course_spread": "Concentracion de sesiones de una misma asignatura en el mismo dia",
                "teacher_gaps": "Huecos entre clases de docentes",
                "teacher_balance": "Desequilibrio de carga diaria docente",
                "room_utilization": "Uso suboptimo de laboratorios o capacidad de aulas",
                "preferences": "Preferencias docentes blandas no cumplidas",
            }
            for k, val in top_factors:
                lines.append(f"- **{labels.get(k, k)}:** {val} pts ({report.soft_raw_units[k]} unidades).")
        else:
            lines.append("\n- El horario alcanza **penalizacion blanda 0** en todos los criterios del escenario.")

        lines.append("\n#### Ajustes propuestos para coordinacion")
        if report.student_dead_hours > 0 and report.scenario != "A":
            lines.append("- Cambiar a **Escenario A** si la prioridad es eliminar las horas muertas de los grupos.")
        if (report.teacher_gaps > 0 or report.preferences_pct < 100.0) and report.scenario != "B":
            lines.append("- Probar **Escenario B** para priorizar huecos y preferencias docentes.")
        if report.soft_raw_units.get("room_utilization", 0) > 0:
            lines.append("- Revisar asignaciones de materias teoricas en laboratorios compartidos para liberar labs.")
        if not any(
            [
                report.student_dead_hours > 0 and report.scenario != "A",
                (report.teacher_gaps > 0 or report.preferences_pct < 100.0) and report.scenario != "B",
                report.soft_raw_units.get("room_utilization", 0) > 0,
            ]
        ):
            lines.append("- El compromiso actual del Escenario C es solido; listo para revision final de coordinacion.")

    if stability is not None:
        lines.append("\n#### Impacto de reprogramacion (Estabilidad I4)")
        lines.append(
            f"- Se conservaron **{stability.unchanged_sessions}/{stability.total_sessions}** sesiones sin cambio "
            f"(**{stability.stability_pct:.1f}% de estabilidad**)."
        )
        lines.append(
            f"- Sesiones movidas: **{stability.moved_sessions}** "
            f"(solo horario: {stability.moved_time_only}, solo aula: {stability.moved_room_only}, ambos: {stability.moved_both})."
        )
        for ch in stability.changes[:6]:
            d0, s0, r0 = ch["from"]
            d1, s1, r1 = ch["to"]
            day0 = instance.days[d0] if d0 < len(instance.days) else str(d0)
            day1 = instance.days[d1] if d1 < len(instance.days) else str(d1)
            sl0 = instance.slot_labels[s0] if s0 < len(instance.slot_labels) else str(s0)
            sl1 = instance.slot_labels[s1] if s1 < len(instance.slot_labels) else str(s1)
            lines.append(f"  - `{ch['course_id']}`: {day0} {sl0} ({r0}) -> {day1} {sl1} ({r1})")

    return "\n".join(lines)


def explain_validation_report(
    instance: Instance,
    report: ValidationReport,
    stability: StabilityReport | None = None,
    llm_cfg: LLMConfig | None = None,
) -> tuple[str, str]:
    """Genera explicacion en espanol de conflictos y propuestas de ajuste (LLM o plantilla estructurada)."""
    payload = {
        "instance": instance.name,
        "report": report.model_dump(exclude={"hard_violation_details"}),
        "sample_hard_violations": [v.message for v in report.hard_violation_details[:10]],
        "stability": stability.model_dump(exclude={"changes"}) if stability else None,
    }
    try:
        text, backend = call_llm_text(
            _EXPLAIN_SYSTEM_PROMPT,
            f"Explica este reporte oficial del validador:\n{json.dumps(payload, ensure_ascii=False)}",
            cfg=llm_cfg,
            expect_json=False,
        )
        return text, backend
    except (LLMUnavailableError, Exception):
        return _deterministic_explanation(instance, report, stability=stability), "structured_explainer_offline"


# =============================================================================
# 3. TRADUCTOR DE ESCENARIO DE CAMBIO (I4) -> REPROGRAMACION CON R2 CP-SAT
# =============================================================================

_CHANGE_SYSTEM_PROMPT = """Traduce una peticion de cambio de horario en espanol a JSON estructurado.
Dias: 0=Lunes, 1=Martes, 2=Miercoles, 3=Jueves, 4=Viernes.
Franjas: 0=07:00-08:00 ... 7=14:00-15:00.
Responde UNICAMENTE JSON:
{"action": "block_teacher_day" | "block_teacher_slot" | "block_room_slot", "entity_id": "DOC_01", "day": 2, "slot": null}
"""


def parse_change_request(
    text: str,
    instance: Instance,
    llm_cfg: LLMConfig | None = None,
) -> ChangeRequest:
    """Traduce un pedido de cambio en espanol (ej. 'el docente DOC_01 ya no puede los miercoles')."""
    try:
        teacher_ids = [t.id for t in instance.teachers]
        room_ids = [r.id for r in instance.rooms]
        user_msg = (
            f"Docentes validos: {teacher_ids}\nAulas validas: {room_ids}\n"
            f"Peticion de coordinacion: \"{text}\""
        )
        raw, backend = call_llm_text(_CHANGE_SYSTEM_PROMPT, user_msg, cfg=llm_cfg, expect_json=True)
        data = parse_json_from_llm(raw)
        day = int(data["day"])
        slot = data.get("slot")
        slot_i = int(slot) if slot is not None else None
        entity_id = str(data["entity_id"])
        action = str(data["action"])
        day_name = instance.days[day]
        summary = (
            f"Bloquear {entity_id} el dia {day_name}"
            + (f" en franja {instance.slot_labels[slot_i]}" if slot_i is not None else " (todo el dia)")
        )
        return ChangeRequest(
            action=action,
            entity_id=entity_id,
            day=day,
            slot=slot_i,
            source_text=text,
            human_summary=summary,
            backend=backend,
        )
    except (LLMUnavailableError, Exception):
        # Parser determinista en espanol
        t_norm = _norm(text)
        entity_id = instance.teachers[0].id
        action = "block_teacher_day"

        # Buscar ID explicito de docente o aula
        for t in instance.teachers:
            if t.id.lower() in t_norm:
                entity_id = t.id
                action = "block_teacher_day"
                break
        for r in instance.rooms:
            if r.id.lower() in t_norm:
                entity_id = r.id
                action = "block_room_slot"
                break

        day = 2  # Miercoles por defecto si no se detecta
        for dname, didx in _DAY_MAP.items():
            if re.search(rf"\b{dname}s?\b", t_norm):
                day = didx
                break

        slot_i: int | None = None
        m_hr = re.search(r"\b(0?[7-9]|1[0-4]):00\b", t_norm)
        if m_hr:
            slot_i = int(m_hr.group(1)) - 7
            if action == "block_teacher_day":
                action = "block_teacher_slot"

        day_name = instance.days[day]
        summary = (
            f"Bloquear {entity_id} el dia {day_name}"
            + (f" en franja {instance.slot_labels[slot_i]}" if slot_i is not None else " (todo el dia)")
        )
        return ChangeRequest(
            action=action,
            entity_id=entity_id,
            day=day,
            slot=slot_i,
            source_text=text,
            human_summary=summary,
            backend="offline_nlp_baseline",
        )


def apply_change_and_reprogram(
    instance: Instance,
    base_schedule: Schedule,
    change: ChangeRequest,
    scenario: str = "C",
    time_limit_sec: float = 15.0,
    llm_cfg: LLMConfig | None = None,
) -> dict[str, Any]:
    """Aplica el cambio confirmado a la instancia, reprograma con R2 CP-SAT, valida y explica."""
    updated = copy.deepcopy(instance)
    updated.id = f"{instance.id}_REPROG"

    if change.action in ("block_teacher_day", "block_teacher_slot"):
        for t in updated.teachers:
            if t.id == change.entity_id:
                existing = set(t.unavailable_slots)
                if change.slot is None:
                    for s in range(updated.slots_per_day):
                        existing.add((change.day, s))
                else:
                    existing.add((change.day, change.slot))
                t.unavailable_slots = sorted(existing)
                break
        updated.preferences.append(
            Preference(
                id="P_REPROG",
                teacher_id=change.entity_id,
                pref_type="avoid_day" if change.slot is None else "avoid_slot",
                params={"day": change.day} if change.slot is None else {"day": change.day, "slot": change.slot},
                hard=True,
                weight=1,
                source_text=change.source_text,
                status="approved",
            )
        )
    elif change.action == "block_room_slot":
        for r in updated.rooms:
            if r.id == change.entity_id:
                existing = set(r.unavailable_slots)
                if change.slot is None:
                    for s in range(updated.slots_per_day):
                        existing.add((change.day, s))
                else:
                    existing.add((change.day, change.slot))
                r.unavailable_slots = sorted(existing)
                break

    new_schedule = solve_cpsat_reprogram(
        updated_instance=updated,
        base_schedule=base_schedule,
        scenario=scenario,
        time_limit_sec=time_limit_sec,
    )
    report = validate_schedule(updated, new_schedule, scenario=scenario)
    stability = compute_stability(base_schedule, new_schedule)
    explanation, exp_backend = explain_validation_report(
        updated, report, stability=stability, llm_cfg=llm_cfg
    )

    return {
        "change_request": change,
        "updated_instance": updated,
        "new_schedule": new_schedule,
        "validation_report": report,
        "stability_report": stability,
        "explanation": explanation,
        "explainer_backend": exp_backend,
    }
