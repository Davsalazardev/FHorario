# Guion de Presentación Final y Demostración — Horarios ITIID

**Archivo de diapositivas PDF (16:9):** `docs/presentacion_horarios_itiid.pdf` (8 diapositivas)  
**Rúbrica para evaluar P4 durante la demo:** `docs/05_rubrica_evaluacion_p4.md`

## Orden sugerido de la presentación (10–12 minutos)

1. **Portada y Regla de Diseño (1 min):** Explicar por qué el motor de optimización decide, el validador común juzga y el LLM nunca asigna clases solo.
2. **Problema, Alcance y Arquitectura R1–R6 (1.5 min):** Recalcar alcance por programa ITIID (recursos compartidos bloqueados) y el kit común inmutable.
3. **P1 — Por qué no basta un LLM autónomo (1.5 min):** Mostrar la gráfica `01_p1_violaciones_duras.png` con resultados de `ollama:qwen2.5:7b` y conexión con Alharbi (2026).
4. **P2 — R5 Extractor de Preferencias (1.5 min):** Mostrar P/R/F1 por tipo (`F1 micro = 100.0%`, `backend = ollama:qwen2.5:7b`) y la revisión humana `pending_review`.
5. **P3 — Comparación de Métodos y Ajuste de AG (2 min):** Mostrar tabla de posiciones en I1–I3, 10 corridas del AG memético ajustado y superioridad de CP-SAT.
6. **Escenarios A, B, C y Reprogramación I4 (1.5 min):** Tradeoff estudiantes vs docentes + estabilidad de `98.6%` (`2/142` sesiones movidas en `0.63 s`).
7. **Demostración en vivo en Streamlit + Evaluación P4 (2–3 min):**
   - Abrir `streamlit run app.py` conectado a **Ollama (`qwen2.5:7b`)**.
   - Generar horario de `I3` en Escenario C, mostrar sábana general y horario por docente (bloques rojos compartidos).
   - Extraer una preferencia en español en `R5`, aprobarla manualmente e inyectarla.
   - Ejecutar `I4` en 2 pasos (Traducir con `R6` → Confirmar → Reprogramar con `R2`) y pedir a coordinación/equipos llenar `docs/05_rubrica_evaluacion_p4.md`.
