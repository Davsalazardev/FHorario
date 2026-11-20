"""Modulo de etica y privacidad: anonimizacion de docentes, grupos y aulas.

Regla del PDF (Datos y etica):
'Anonimizar docentes, grupos y aulas antes de entregar datos a los estudiantes
o enviarlos a un LLM externo.'
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from kit.schema import Instance


class AnonymizationMap(BaseModel):
    teacher_real_to_anon: dict[str, str] = Field(default_factory=dict)
    teacher_name_real_to_anon: dict[str, str] = Field(default_factory=dict)
    group_real_to_anon: dict[str, str] = Field(default_factory=dict)
    room_real_to_anon: dict[str, str] = Field(default_factory=dict)

    def save_local(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return p


def anonymize_instance(instance: Instance) -> tuple[Instance, AnonymizationMap]:
    """Anonimiza IDs y nombres de docentes, grupos y aulas (y textos de preferencias)."""
    inst = copy.deepcopy(instance)
    amap = AnonymizationMap()

    for idx, t in enumerate(inst.teachers, start=1):
        new_id = f"DOC_{idx:02d}"
        new_name = f"Docente {idx:02d}"
        amap.teacher_real_to_anon[t.id] = new_id
        amap.teacher_name_real_to_anon[t.name] = new_name
        t.id = new_id
        t.name = new_name

    for idx, g in enumerate(inst.groups, start=1):
        new_id = f"GRP_{g.quarter}_{idx:02d}"
        amap.group_real_to_anon[g.id] = new_id
        g.id = new_id
        g.name = f"Grupo {g.quarter}-{idx:02d}"

    aula_n = 1
    lab_n = 1
    for r in inst.rooms:
        if r.room_type == "laboratorio":
            new_id = f"LAB_{lab_n:02d}"
            new_name = f"Laboratorio {lab_n:02d}"
            lab_n += 1
        else:
            new_id = f"AULA_{aula_n:02d}"
            new_name = f"Aula {aula_n:02d}"
            aula_n += 1
        amap.room_real_to_anon[r.id] = new_id
        r.id = new_id
        r.name = new_name

    for c in inst.courses:
        c.teacher_id = amap.teacher_real_to_anon.get(c.teacher_id, c.teacher_id)
        c.group_id = amap.group_real_to_anon.get(c.group_id, c.group_id)

    for p in inst.preferences:
        p.teacher_id = amap.teacher_real_to_anon.get(p.teacher_id, p.teacher_id)
        if p.source_text:
            sanitized = p.source_text
            for real_name, anon_name in amap.teacher_name_real_to_anon.items():
                if real_name:
                    sanitized = re.sub(re.escape(real_name), anon_name, sanitized, flags=re.IGNORECASE)
            p.source_text = sanitized

    return inst, amap


def sanitize_text_for_llm(text: str, amap: AnonymizationMap | None = None) -> str:
    """Elimina correos, telefonos y nombres mapeados antes de enviar texto a un LLM externo."""
    cleaned = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[CORREO_OCULTO]", text)
    cleaned = re.sub(r"\b\d{10}\b", "[TEL_OCULTO]", cleaned)
    if amap:
        for real_name, anon_name in amap.teacher_name_real_to_anon.items():
            if real_name and len(real_name) > 2:
                cleaned = re.sub(re.escape(real_name), anon_name, cleaned, flags=re.IGNORECASE)
    return cleaned
