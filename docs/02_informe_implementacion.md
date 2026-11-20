# Informe de Implementación — Sistema Inteligente de Horarios ITIID

## 1. Estructura del repositorio

```text
horarios_itiid/
├── kit/                           # Kit comun oficial (inmutable para todos los metodos)
│   ├── schema.py                  # Esquema JSON comun (Instance, Schedule, Preference, Escenarios A/B/C)
│   ├── validator.py               # Validador oficial (arbitro) + calculo de estabilidad I4
│   ├── cpsat_base.py              # Modelo CP-SAT base (solo restricciones duras)
│   ├── instances.py               # Parser ITC-2007 (.ctt) e instancias I1, I2, I3, I4 + manuales
│   └── preferences_dataset.py     # 25 preferencias docentes en espanol anotadas (Gold Standard)
├── src/
│   ├── fast_eval.py               # Evaluador compacto sincronizado 1-a-1 con el validador oficial
│   ├── llm_client.py              # Cliente unificado: Ollama local, Gemini API, OpenAI API y offline
│   ├── r1_data/                   # R1: Excel <-> JSON, validacion de faltantes, anonimizacion, PDF/Excel
│   ├── r2_cpsat/                  # R2: CP-SAT extendido (blandas linealizadas, A/B/C, reprogramacion I4)
│   ├── r3_genetic/                # R3: Algoritmo Genetico + ejecutor oficial de 10 corridas
│   ├── r4_local_search/           # R4: Backtracking MRV, reparacion Min-Conflicts y Hill Climbing
│   ├── r5_llm_extractor/          # R5: Prompts, revision humana (pending_review) y metricas P/R/F1
│   ├── r6_llm_explainer/          # R6: Horario directo LLM (P1), explicador (P4) y cambio I4
│   └── evaluation/                # Benchmarking justo, tabla de posiciones y graficas PNG
├── tests/test_system.py           # Pruebas automatizadas (validador, equivalencia exacta, R1-R6)
├── data/                          # Instancias JSON, .ctt ITC-2007, manuales y plantillas Excel
├── results/                       # Tabla de posiciones, JSON de metricas, graficas, Excel y PDF
├── docs/                          # Informes de diseno, implementacion y evaluacion
├── main.py                        # Interfaz de linea de comandos (CLI)
└── app.py                         # Aplicacion web interactiva en Streamlit
```

---

## 2. Decisiones clave por módulo (`R1`–`R6`)

### Kit Común (`kit/`)
- **Esquema JSON común (`kit/schema.py`):** Implementado con Pydantic v2 para validación estricta de tipos y serialización reproducible. Todos los componentes leen/escriben `Instance` y `Schedule`.
- **Validador oficial (`kit/validator.py`):** Evalúa 8 categorías de violaciones duras (`teacher_conflict`, `group_conflict`, `room_conflict`, `teacher_availability`, `room_availability`, `room_suitability`, `course_coverage`, `invalid_reference`) y 7 criterios blandos ponderados por escenario (`A`, `B`, `C`). Incluye `compute_stability()` con emparejamiento exacto por asignatura para no penalizar permutaciones internas del índice de sesión.
- **Equivalencia verificada:** Las pruebas automatizadas comprueban que el objetivo reportado por OR-Tools CP-SAT (`solver.ObjectiveValue()`) y el puntaje de `FastEvaluator` coinciden **al entero exacto** con `kit.validator.validate_schedule()` en los escenarios A, B y C.

### R1 — Datos, Anonimización y Exportación (`src/r1_data/`)
- **Importación Excel (`excel_io.py`):** Lee las hojas `Config`, `Docentes`, `Grupos`, `Aulas`, `Asignaturas` y `Preferencias` con `pandas` y `openpyxl`. Detecta faltantes obligatorios, IDs duplicados, referencias rotas (`group_id`/`teacher_id` inexistentes) y formatos de franja inválidos lanzando `ExcelValidationError` con la lista completa de errores.
- **Exportación Excel y PDF (`excel_io.py`, `pdf_export.py`):** Produce los 4 entregables solicitados en ambos formatos (`.xlsx` y `.pdf` horizontal con ReportLab):
  1. `sabana_general_itiid.{xlsx,pdf}`
  2. `horarios_por_docente.{xlsx,pdf}`
  3. `horarios_por_grupo.{xlsx,pdf}`
  4. `horarios_por_aula.{xlsx,pdf}`
- **Anonimización (`anonymizer.py`):** Convierte nombres reales a códigos (`DOC_01`, `GRP_7_01`, `LAB_01`) y limpia correos/teléfonos antes de enviar cualquier texto a una API externa.

### R2 — Motor CP-SAT Extendido (`src/r2_cpsat/solver.py`)
- Extiende `build_base_cpsat_model()` sin modificar el kit base.
- Linealiza huecos de grupos y docentes mediante variables de prefijo/sufijo (`AddMaxEquality`) y cota inferior `gap >= pref + suff - occ - 1` (exacta al tener peso positivo en minimización).
- En modo reprogramación (`solve_cpsat_reprogram`), inyecta *warm-start hints* (`AddHint`) desde el horario base y penaliza cada sesión reubicada con $\lambda_{\text{move}} = 120$, logrando reprogramar `I4` con 0 violaciones duras y mínima perturbación.

### R3 — Algoritmo Genético (`src/r3_genetic/solver.py`)
- **Cromosoma:** Vector de tuplas `(day, slot, room_idx)` por sesión, restringido siempre al dominio válido de disponibilidad e idoneidad.
- **Inicialización híbrida:** 70% constructivo MRV aleatorizado + reparación ligera y 30% aleatorio reparado para mantener diversidad genética.
- **Operadores:** Selección por torneo ($k=3$), elitismo (top 2), cruce por bloques de grupo (preserva la estructura intra-grupo y evita destruir horarios válidos), reparación dirigida `Min-Conflicts` y mutación memética ligera hacia franjas libres.
- **Evaluación oficial:** `run_ga_multirun()` ejecuta **10 corridas independientes** con semillas distintas bajo el mismo límite de tiempo por instancia.

### R4 — Búsqueda Local y Heurística (`src/r4_local_search/solver.py`)
- **Constructivo:** Backtracking con heurística **MRV** (menor dominio libre), desempate por grado (*Degree heuristic*) y ordenamiento guiado de valores (favorece reparto semanal y aulas adecuadas) con poda temprana (*forward checking*).
- **Reparación:** Algoritmo `Min-Conflicts` para eliminar choques residuales.
- **Mejora:** *Hill Climbing* con reinicios aleatorios alternando dos vecindarios: reubicación de una sesión a una franja libre (72%) e intercambio (*swap*) de día/franja entre dos sesiones del mismo grupo (28%).

### R5 y R6 — Integración LLM (`src/llm_client.py`, `r5_llm_extractor/`, `r6_llm_explainer/`)
- **Soporte dual API + local:** Detecta automáticamente un servidor local de **Ollama** (`http://localhost:11434`), o credenciales de **Gemini** (`GEMINI_API_KEY`) / **OpenAI** (`OPENAI_API_KEY`). Si el entorno está sin conexión o sin servidor Ollama activo, utiliza el baseline determinista offline e informa explícitamente el backend usado en todas las métricas.
- **Revisión humana en R5:** Toda restricción extraída nace con `status="pending_review"`.
- **Explicación y reprogramación en R6:** Traduce peticiones de cambio en español (ej. *"El docente DOC_01 ya no puede los miércoles"*) a un `ChangeRequest` JSON, ejecuta `R2` y genera un informe claro con causas de conflicto, métricas de estabilidad y ajustes sugeridos.

---

## 3. Guía rápida de ejecución

```bash
# 1. Activar entorno virtual
source .venv/bin/activate

# 2. Correr todas las pruebas automatizadas
pytest -v

# 3. Ejecutar el benchmark oficial completo (I1-I4, 10 corridas AG, Excel, PDF y graficas)
python main.py benchmark --time-limit 4 --ga-runs 10

# 4. Resolver una instancia o plantilla Excel especifica
python main.py solve --instance I3 --method cpsat --scenario C --time-limit 10

# 5. Lanzar la interfaz interactiva en Streamlit
streamlit run app.py
```

Para usar un modelo local con Ollama o API externa:
```bash
# Opcion local (Ollama)
ollama serve &
ollama pull llama3.2
python main.py eval-r5 --provider ollama

# Opcion API (Gemini)
export GEMINI_API_KEY="tu_clave"
python main.py eval-r5 --provider gemini
```
