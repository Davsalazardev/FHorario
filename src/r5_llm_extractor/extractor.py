"""R5: Extractor de preferencias docentes en espanol a restricciones JSON + evaluacion P/R/F1.

Regla del proyecto:
- El LLM convierte preferencias escritas en espanol en restricciones estructuradas JSON.
- Todas las restricciones extraidas nacen con status='pending_review' para revision humana.
- Se evalua el desempeno con Precision, Recall y F1 global y por tipo de restriccion (P2).
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from kit.preferences_dataset import ANNOTATED_PREFERENCES_DATASET
from kit.schema import Preference
from src.llm_client import LLMConfig, LLMUnavailableError, call_llm_text, parse_json_from_llm


EXTRACTION_SYSTEM_PROMPT = """Eres un asistente experto en modelado de horarios universitarios (ITIID).
Tu tarea es traducir una preferencia escrita en espanol por un docente a una lista JSON de restricciones estructuradas.

Dias validos: 0=Lunes, 1=Martes, 2=Miercoles, 3=Jueves, 4=Viernes.
Franjas horarias:
0=07:00-08:00 (primera hora), 1=08:00-09:00, 2=09:00-10:00, 3=10:00-11:00,
4=11:00-12:00, 5=12:00-13:00, 6=13:00-14:00, 7=14:00-15:00 (ultima hora).

Tipos permitidos (pref_type) y sus params:
- "avoid_day": {"day": <0..4>}
- "avoid_slot": {"slot": <0..7>} o {"day": <0..4>, "slot": <0..7>}
- "prefer_morning": {}
- "prefer_afternoon": {}
- "max_daily_hours": {"max_hours": <int>}
- "compact_schedule": {}
- "prefer_room_type": {"room_type": "aula" | "laboratorio"}

Marca "hard": true SOLO si el texto indica imposibilidad absoluta u obligatoria (ej. "definitivamente no puedo", "obligatorio"). En otro caso "hard": false.

Responde UNICAMENTE un objeto JSON con el formato:
{"constraints": [{"pref_type": "...", "params": {...}, "hard": false}]}
"""


_DAY_MAP = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
}

_HOUR_TO_SLOT = {
    7: 0,
    8: 1,
    9: 2,
    10: 3,
    11: 4,
    12: 5,
    13: 6,
    14: 7,
}


def _norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def extract_preferences_offline_nlp(text: str) -> list[dict[str, Any]]:
    """Extractor determinista de lenguaje natural en espanol (baseline / fallback offline)."""
    t = _norm(text)
    results: list[dict[str, Any]] = []

    is_hard = any(
        kw in t for kw in ("definitivamente no puedo", "obligatorio", "imposible", "ya no puede", "no estara disponible")
    )

    # Detectar dias mencionados
    days_found = [d_idx for name, d_idx in _DAY_MAP.items() if re.search(rf"\b{name}s?\b", t)]

    # Detectar franjas/horas explicitas (ej. "07:00", "7:00", "13:00", "primera hora", "ultima franja")
    slots_found: list[int] = []
    if re.search(r"primera\s+(hora|franja)", t):
        slots_found.append(0)
    if re.search(r"ultima\s+(hora|franja)", t):
        slots_found.append(7)
    for m in re.finditer(r"\b(0?[7-9]|1[0-4]):00\b", t):
        hr = int(m.group(1))
        # Si aparece un rango "07:00 a 08:00" o "13:00-14:00", tomar la hora de inicio
        start_pos = m.start()
        prefix = t[max(0, start_pos - 12) : start_pos]
        if re.search(r"(a\s+las\s+|-\s*|a\s+)$", prefix) and slots_found:
            # Probablemente es la hora de fin de un rango (ej. "a 8:00", "a 14:00") o cutoff de manana/tarde
            continue
        if re.search(r"(antes|despues)\s+de\s+las\s+$", prefix):
            continue
        if hr in _HOUR_TO_SLOT and _HOUR_TO_SLOT[hr] not in slots_found:
            slots_found.append(_HOUR_TO_SLOT[hr])

    # 1. Evitar franja especifica (avoid_slot) vs evitar dia completo (avoid_day)
    avoid_cue = any(
        kw in t
        for kw in ("evitar", "no tener", "no puedo", "no quiero", "no me pongan", "libre el", "dejarme libre", "ya no puede")
    )
    if slots_found and avoid_cue:
        for sl in slots_found:
            if days_found:
                for d in days_found:
                    results.append({"pref_type": "avoid_slot", "params": {"day": d, "slot": sl}, "hard": is_hard})
            else:
                results.append({"pref_type": "avoid_slot", "params": {"slot": sl}, "hard": is_hard})
    elif days_found and avoid_cue:
        for d in days_found:
            results.append({"pref_type": "avoid_day", "params": {"day": d}, "hard": is_hard})

    # 2. Preferencia de turno (manana / tarde)
    if re.search(r"\b(manana|matutino)\b", t):
        results.append({"pref_type": "prefer_morning", "params": {}, "hard": False})
    if re.search(r"\b(tarde|vespertino)\b", t):
        results.append({"pref_type": "prefer_afternoon", "params": {}, "hard": False})

    # 3. Maximo de horas diarias
    m_max = re.search(
        r"(?:no\s+(?:programar\s+)?mas\s+de|como\s+maximo|tope\s+de|no\s+exceder|maximo\s+de?)\s+(\d+)\s*hora",
        t,
    )
    if m_max:
        results.append(
            {"pref_type": "max_daily_hours", "params": {"max_hours": int(m_max.group(1))}, "hard": False}
        )

    # 4. Horario compacto / sin huecos
    if re.search(r"(compacto|sin\s+huecos|bloques\s+seguidos|clases\s+seguidas|horario\s+corrido|evitar\s+huecos|sin\s+horas\s+muertas)", t):
        results.append({"pref_type": "compact_schedule", "params": {}, "hard": False})

    # 5. Tipo de aula preferida
    if re.search(r"aula\s+normal|en\s+aula\b", t):
        results.append({"pref_type": "prefer_room_type", "params": {"room_type": "aula"}, "hard": False})
    elif re.search(r"(asignarme\s+laboratorio|usar\s+laboratorio|prefiero\s+laboratorio|de\s+preferencia.*laboratorio)", t):
        results.append({"pref_type": "prefer_room_type", "params": {"room_type": "laboratorio"}, "hard": False})

    return results


def extract_preferences_from_text(
    teacher_id: str,
    text: str,
    pref_id_prefix: str = "EXT",
    llm_cfg: LLMConfig | None = None,
) -> tuple[list[Preference], str]:
    """Extrae preferencias en JSON usando LLM (Ollama/API) o fallback NLP determinista.

    Todas las restricciones devueltas llevan status='pending_review' para revision humana.
    Retorna (lista_de_Preference, backend_usado).
    """
    raw_items: list[dict[str, Any]] = []
    backend = "offline_nlp_baseline"

    try:
        user_msg = f"Docente: {teacher_id}\nPreferencia escrita:\n\"{text}\""
        resp_text, backend = call_llm_text(
            EXTRACTION_SYSTEM_PROMPT, user_msg, cfg=llm_cfg, expect_json=True
        )
        try:
            parsed = parse_json_from_llm(resp_text)
            if isinstance(parsed, dict) and isinstance(parsed.get("constraints"), list):
                raw_items = parsed["constraints"]
            elif isinstance(parsed, list):
                raw_items = parsed
        except Exception:
            # Si un LLM real devolvio JSON malformado, no ocultar el error con reglas offline
            raw_items = []
    except LLMUnavailableError:
        raw_items = extract_preferences_offline_nlp(text)
        backend = "offline_nlp_baseline"

    valid_types = {
        "avoid_day",
        "avoid_slot",
        "prefer_morning",
        "prefer_afternoon",
        "max_daily_hours",
        "compact_schedule",
        "prefer_room_type",
    }
    prefs: list[Preference] = []
    for idx, item in enumerate(raw_items, start=1):
        ptype = item.get("pref_type")
        if ptype not in valid_types:
            continue
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        prefs.append(
            Preference(
                id=f"{pref_id_prefix}_{idx:02d}",
                teacher_id=teacher_id,
                pref_type=ptype,
                params=params,
                hard=bool(item.get("hard", False)),
                weight=1,
                source_text=text,
                status="pending_review",  # Revision humana obligatoria
            )
        )
    return prefs, backend


def _canonical_key(item: dict[str, Any]) -> tuple[str, tuple[tuple[str, Any], ...], bool]:
    ptype = str(item["pref_type"])
    params = item.get("params", {}) or {}
    norm_params = tuple(sorted((str(k), params[k]) for k in params))
    hard = bool(item.get("hard", False))
    return (ptype, norm_params, hard)


def evaluate_extractor(
    dataset: list[dict[str, Any]] | None = None,
    llm_cfg: LLMConfig | None = None,
) -> dict[str, Any]:
    """Evalua R5 contra el Gold Standard anotado y calcula Precision, Recall y F1 global y por tipo."""
    ds = dataset if dataset is not None else ANNOTATED_PREFERENCES_DATASET
    tp_by_type: dict[str, int] = defaultdict(int)
    fp_by_type: dict[str, int] = defaultdict(int)
    fn_by_type: dict[str, int] = defaultdict(int)

    backends_used: set[str] = set()
    per_item_logs: list[dict[str, Any]] = []

    for row in ds:
        prefs, backend = extract_preferences_from_text(
            teacher_id=row["teacher_id"],
            text=row["text"],
            pref_id_prefix=row["id"],
            llm_cfg=llm_cfg,
        )
        backends_used.add(backend)
        pred_dicts = [{"pref_type": p.pref_type, "params": p.params, "hard": p.hard} for p in prefs]
        gold_dicts = row["expected"]

        pred_set = {_canonical_key(x) for x in pred_dicts}
        gold_set = {_canonical_key(x) for x in gold_dicts}

        for k in pred_set & gold_set:
            tp_by_type[k[0]] += 1
        for k in pred_set - gold_set:
            fp_by_type[k[0]] += 1
        for k in gold_set - pred_set:
            fn_by_type[k[0]] += 1

        per_item_logs.append(
            {
                "id": row["id"],
                "teacher_id": row["teacher_id"],
                "text": row["text"],
                "expected": gold_dicts,
                "predicted": pred_dicts,
                "exact_match": pred_set == gold_set,
            }
        )

    all_types = sorted(set(tp_by_type.keys()) | set(fp_by_type.keys()) | set(fn_by_type.keys()))
    by_type: dict[str, dict[str, Any]] = {}

    total_tp = sum(tp_by_type.values())
    total_fp = sum(fp_by_type.values())
    total_fn = sum(fn_by_type.values())

    def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0
        return round(p, 4), round(r, 4), round(f1, 4)

    for t in all_types:
        tp, fp, fn = tp_by_type[t], fp_by_type[t], fn_by_type[t]
        p, r, f1 = prf(tp, fp, fn)
        by_type[t] = {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}

    micro_p, micro_r, micro_f1 = prf(total_tp, total_fp, total_fn)
    macro_f1 = (
        round(sum(v["f1"] for v in by_type.values()) / len(by_type), 4) if by_type else 0.0
    )
    exact_matches = sum(1 for x in per_item_logs if x["exact_match"])

    return {
        "backend": ", ".join(sorted(backends_used)),
        "num_examples": len(ds),
        "exact_match_rate": round(exact_matches / max(1, len(ds)), 4),
        "overall": {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "precision": micro_p,
            "recall": micro_r,
            "f1_micro": micro_f1,
            "f1_macro": macro_f1,
        },
        "by_type": by_type,
        "details": per_item_logs,
    }
