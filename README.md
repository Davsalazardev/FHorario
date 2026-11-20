# Sistema Inteligente para la Generación de Horarios de ITIID

Proyecto final de **Fundamentos de Inteligencia Artificial** (7.º cuatrimestre, ITIID — Universidad Politécnica de Victoria).

> **Regla de diseño:** *Las asignaciones las decide un motor de optimización y las revisa un validador oficial; el LLM nunca decide solo.* El LLM captura preferencias en español (con revisión humana), explica conflictos y traduce peticiones de cambio para reprogramación.

## Módulos (`R1`–`R6`) y Kit Común

- **`kit/` (Kit Común):** Esquema JSON común (`schema.py`), validador oficial árbitro (`validator.py`), modelo CP-SAT base con restricciones duras (`cpsat_base.py`), instancias `I1`–`I4` + parser ITC-2007 `.ctt` (`instances.py`) y 25 preferencias anotadas (`preferences_dataset.py`).
- **`src/r1_data/` (`R1`):** Plantillas e importación Excel con revisión de faltantes (`pandas`/`openpyxl`), anonimizador ético y exportación a **Excel y PDF** por **sábana general de ITIID, docente, grupo y aula**.
- **`src/r2_cpsat/` (`R2`):** Extensión de OR-Tools CP-SAT con penalizaciones blandas linealizadas exactas, **Escenarios A, B y C**, y modo **reprogramación (`I4`)** con estabilidad mínima.
- **`src/r3_genetic/` (`R3`):** Algoritmo Genético con cruce por bloques de grupo, reparación `Min-Conflicts`, mutación guiada y **10 corridas por instancia**.
- **`src/r4_local_search/` (`R4`):** Backtracking con heurística **MRV**, reparación y *Hill Climbing* con reinicios aleatorios.
- **`src/r5_llm_extractor/` (`R5`):** Extractor español $\rightarrow$ restricciones JSON con revisión humana (`pending_review`), soporte Ollama / Gemini / OpenAI / baseline offline y evaluación **Precisión, Recall y F1** por tipo de restricción.
- **`src/r6_llm_explainer/` (`R6`):** Comparación de horario directo con LLM (`P1`), explicador de conflictos para coordinación (`P4`) y traductor de cambios (`I4`).
- **`app.py`:** Interfaz web interactiva en **Streamlit**.

## Instalación y uso

```bash
# Activar entorno virtual ya creado con Python 3.11
source .venv/bin/activate

# 1. Ejecutar pruebas automatizadas
pytest -v

# 2. Correr el benchmark oficial completo (I1-I4, 10 corridas AG, Excel, PDF y graficas)
python main.py benchmark --time-limit 3.0 --ga-runs 10

# 3. Resolver una instancia o plantilla Excel especifica
python main.py solve --instance I3 --method cpsat --scenario C --time-limit 10

# 4. Abrir la interfaz web en Streamlit
streamlit run app.py
```

## Informes y resultados

- [Informe de Diseño](docs/01_informe_diseno.md)
- [Informe de Implementación](docs/02_informe_implementacion.md)
- [Informe de Evaluación (P1–P4)](docs/03_informe_evaluacion.md)
- [Tabla de Posiciones (CSV)](results/leaderboard.csv)
- Gráficas en `results/charts/` y entregables Excel/PDF en `results/exports/`.
