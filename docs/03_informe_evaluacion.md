# Informe de Evaluación — Sistema Inteligente de Horarios ITIID

Todos los métodos fueron evaluados sobre las **mismas instancias** (`I1`, `I2`, `I3`, `I4`), bajo el **mismo límite de tiempo** (`3.0 s` por corrida para motores de optimización; `6.0 s` para Escenarios `A`/`B`/`C` y reprogramación `I4`) y juzgados por el **mismo validador oficial** (`kit/validator.py`).

Los experimentos con LLM (`P1`, `P2`, `P4` y parseo de `I4`) se ejecutaron **en vivo** contra el modelo local **`qwen2.5:7b` servido en Ollama** (`http://localhost:11434`), garantizando privacidad total (ningún dato salió de la máquina local).

---

## 1. Ajuste de Hiperparámetros del Algoritmo Genético (`R3`)

Antes del benchmark oficial se ejecutó un barrido controlado (`python main.py tune-ga --time-limit 2.5`, 3 semillas por configuración sobre `I2` e `I3`) comparando población, tasa de mutación y pasos de refinamiento local memético:

| Instancia | Configuración `R3` | `pop_size` | `mut_rate` | `polish_steps` | Factibles | Mejor Blanda | Media Blanda | Desv. Est. | Gen. Prom. |
| :---: | :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| `I2` | AG-Base (sin pulido) | 28 | 0.35 | 0 | 3/3 | 128 | 144.7 | 17.0 | 212.0 |
| `I2` | **AG-PobGrande (ganador)** | **32** | **0.30** | **8** | **3/3** | **108** | **126.0** | 18.0 | 51.3 |
| `I2` | AG-Balanceado | 18 | 0.40 | 18 | 3/3 | 114 | 136.0 | 28.4 | 26.0 |
| `I2` | AG-Intensivo | 14 | 0.45 | 28 | 3/3 | 116 | 134.7 | 16.2 | 25.3 |
| `I3` | AG-Base (sin pulido) | 28 | 0.35 | 0 | 3/3 | 666 | 731.3 | 104.6 | 231.0 |
| `I3` | **AG-PobGrande (ganador)** | **32** | **0.30** | **8** | **3/3** | **464** | **524.0** | **63.2** | 38.0 |
| `I3` | AG-Balanceado | 18 | 0.40 | 18 | 3/3 | 988 | 1116.7 | 173.5 | 3.3 |
| `I3` | AG-Intensivo | 14 | 0.45 | 28 | 3/3 | 558 | 675.3 | 120.1 | 7.3 |

**Hallazgo del ajuste:** Combinar población amplia (`pop=32`) con un pulido memético ligero (`polish=8`, `prob=0.35`) logró el mejor compromiso entre exploración evolutiva (38 generaciones) y explotación local, reduciendo la penalización media en `I3` de `731.3` a **`524.0 pts`** (y frente a la versión sin ajustar de `1272 pts`).

---

## 2. Tabla de Posiciones Oficial (Escenario C — Equilibrio)

Criterio del árbitro oficial: primero **0 violaciones duras**; entre horarios válidos, gana la **menor penalización blanda**.

### Instancia `I1` — ITC-2007 (42 sesiones, 3 grupos, 6 docentes, 4 aulas)
| Pos | Método | Factible | Viol. Duras | Penaliz. Blanda | Horas Muertas Est. | Huecos Doc. | Tiempo (s) |
| :-: | :--- | :-: | :-: | :-: | :-: | :-: | :-: |
| 1 | **CP-SAT (Escenario C)** | Sí | **0** | **0** | 0 | 0 | **0.245** |
| 2 | Búsqueda Local (MRV + Hill Climbing) | Sí | **0** | **0** | 0 | 0 | 3.000 |
| 3 | Algoritmo Genético Memético (mejor de 10) | Sí | **0** | **0** | 0 | 0 | 3.000 |
| 4 | LLM directo sin validador (`qwen2.5:7b`) | **No** | **10** | 156 | 13 | 13 | 62.236 |
| 5 | LLM directo + feedback (`qwen2.5:7b`) | **No** | **15** | 120 | 8 | 12 | 113.602 |

### Instancia `I2` — ITIID 7.º Cuatrimestre (50 sesiones, 2 grupos, 6 docentes, 4 espacios)
| Pos | Método | Factible | Viol. Duras | Penaliz. Blanda | Horas Muertas Est. | Huecos Doc. | Pref. Cumplidas | Tiempo (s) |
| :-: | :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 1 | **CP-SAT (Escenario C)** | Sí | **0** | **60** | **0** | **0** | **85.7%** | **2.477** |
| 2 | Algoritmo Genético Memético (mejor de 10) | Sí | **0** | 108 | 3 | 2 | 64.3% | 3.000 |
| 3 | Búsqueda Local (MRV + Hill Climbing) | Sí | **0** | 138 | 0 | 1 | 50.0% | 3.001 |
| 4 | Horario manual (coordinación) | Sí | 0 | 852 | 17 | 34 | 14.3% | — |
| 5 | LLM directo sin validador (`qwen2.5:7b`) | **No** | **29** | 168* | 13 | 4 | 64.3%* | 48.281 |
| 6 | LLM directo + feedback (`qwen2.5:7b`) | **No** | **29** | 168* | 13 | 4 | 64.3%* | 120.603 |

### Instancia `I3` — Programa ITIID Completo (142 sesiones, 6 grupos, 12 docentes, 8 espacios, recursos compartidos)
| Pos | Método | Factible | Viol. Duras | Penaliz. Blanda | Horas Muertas Est. | Huecos Doc. | Pref. Cumplidas | Tiempo (s) |
| :-: | :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 1 | **Algoritmo Genético Memético (mejor de 10, 3 s)** | Sí | **0** | **408** | 13 | 8 | **60.7%** | 3.001 |
| 2 | **CP-SAT (Escenario C, 3 s)** | Sí | **0** | **412** | **4** | **7** | 53.6% | 3.109 |
| 3 | Búsqueda Local (MRV + Hill Climbing, 3 s) | Sí | **0** | 488 | 7 | 15 | 39.3% | 3.003 |
| 4 | Horario manual (coordinación) | Sí | 0 | 1946 | 55 | 84 | 28.6% | — |
| 5 | LLM directo + feedback (`qwen2.5:7b`) | **No** | **116** | 160* | 7 | 5 | 75.0%* | 173.910 |
| 6 | LLM directo sin validador (`qwen2.5:7b`) | **No** | **135** | 130* | 1 | 4 | 71.4%* | 69.830 |

*\*Nota: Las penalizaciones blandas del LLM directo en `I2` e `I3` no son comparables con soluciones válidas porque el LLM omitió la mayoría de sesiones (`26/50` en `I2` y `34/142` en `I3`), generando decenas de violaciones de cobertura (`course_coverage`).*  
*Además, con **6.0 s** de cómputo en `I3`, CP-SAT Escenario C baja a **`312 pts`** (8 horas muertas, 4 huecos docentes, 57.1% preferencias).*

### Estadísticas de las 10 corridas independientes del Algoritmo Genético (`R3`)
| Instancia | Corridas Factibles | Mejor Blanda | Media Blanda | Mediana | Desv. Estándar | Tiempo Medio (s) |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: |
| `I1` | 10 / 10 (100%) | 0 | 2.4 | 0.0 | 3.10 | 3.001 |
| `I2` | 10 / 10 (100%) | 108 | 145.6 | 145.0 | 24.73 | 3.000 |
| `I3` | 10 / 10 (100%) | 408 | 640.6 | 489.0 | 358.04 | 3.006 |

---

## 3. Respuestas a las Preguntas Guía (`P1`–`P4`)

### P1. ¿Un LLM puede generar por sí solo un horario válido?
**No.** Las pruebas reales con **`ollama:qwen2.5:7b`** reproducen exactamente el hallazgo de **Alharbi (2026)**:
- **`I1` (42 sesiones):** Sin validador comete **10 violaciones duras** (2 choques de docente, 3 de grupo, 3 de aula, 1 disponibilidad, 1 cobertura) en `62.2 s`. Al darle retroalimentación del validador, intenta corregir y termina con **15 violaciones duras** (`113.6 s`), demostrando que al arreglar un choque rompe otras restricciones.
- **`I2` (50 sesiones):** Solo logra emitir **26/50 sesiones**, acumulando **29 violaciones duras** (24 de cobertura + 2 de aula + 2 de aula bloqueada + 1 de docente) tanto sin validador (`48.3 s`) como con feedback (`120.6 s`).
- **`I3` (142 sesiones):** Solo emite **34/142 sesiones**, disparándose a **135 violaciones duras** sin validador (`69.8 s`) y **116 violaciones duras** con feedback (`173.9 s`).
- En contraste, CP-SAT, AG Memético y Búsqueda Local generan **142/142 sesiones con 0 violaciones duras en 3 segundos**.

![Violaciones duras P1](../results/charts/01_p1_violaciones_duras.png)

---

### P2. ¿Qué tan bien convierte un LLM preferencias escritas en restricciones?
Evaluado en vivo con **`ollama:qwen2.5:7b`** sobre las **25 preferencias docentes anotadas en español** (29 restricciones atómicas en 7 categorías):
- **Exact Match:** `100.0%` (25/25)
- **Precisión (micro):** `1.000` | **Recall (micro):** `1.000` | **F1 Micro:** `1.000` | **F1 Macro:** `1.000`

| Tipo de restricción | TP | FP | FN | Precisión | Recall | F1 |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: |
| `avoid_day` | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `avoid_slot` | 5 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `compact_schedule` | 4 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `max_daily_hours` | 5 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `prefer_afternoon` | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `prefer_morning` | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `prefer_room_type` | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |

El contraste entre **`P1`** (`135` violaciones duras en `I3`) y **`P2`** (`F1 = 1.000` en `R5`) con el **mismo modelo local `qwen2.5:7b`** es la prueba más contundente del proyecto: un LLM de 7B parámetros es sobresaliente traduciendo español a JSON estructurado, pero incapaz de resolver asignación combinatoria global. Aun así, en la interfaz Streamlit toda restricción pasa por la bandeja **`pending_review`** para aprobación humana antes de entrar al optimizador.

---

### P3. ¿Qué método da mejores horarios: CP-SAT, Algoritmo Genético o Búsqueda Local?
- **Motor principal recomendado: OR-Tools CP-SAT (`R2`).** Domina claramente en `I1` (`0 pts` en `0.24 s`), en `I2` (`60 pts` vs `108` del AG y `138` de BL), baja a **`312 pts` en `I3`** con 6 s (reduciendo en **84.0%** la penalización del horario manual de `1946 pts`), entrega cotas de optimalidad y soporta nativamente la reprogramación con mínima perturbación (`I4`).
- **Algoritmo Genético Memético (`R3`):** Tras el ajuste de hiperparámetros (`pop=32, polish=8`), logra 100% de factibilidad en 30/30 corridas (10 por instancia) y en presupuestos muy cortos de 3 s alcanza `408 pts` en `I3` (empate técnico con CP-SAT a 3 s, aunque con mayor varianza entre corridas: mediana `489`, desv. est. `358`).
- **Búsqueda Local (`R4`):** Logra 0 violaciones duras en todas las instancias y `488 pts` en `I3` (75% mejor que el horario manual), sirviendo además como base constructiva (`MRV` + `Min-Conflicts`) del ecosistema.

![Comparación de métodos P3](../results/charts/02_p3_comparacion_metodos.png)

### Comparación de Escenarios A, B y C sobre `I3` (CP-SAT, 6 s)
| Escenario | Prioridad | Horas Muertas Est. | Huecos Docentes | Pref. Cumplidas | Penaliz. (en C) |
| :---: | :--- | :-: | :-: | :-: | :-: |
| **A** | Estudiantes | **1** | 9 | 42.9% | 348 |
| **B** | Docentes | 18 | **2** | **67.9%** | 374 |
| **C** | Equilibrio | 8 | 4 | 57.1% | **312** |

![Escenarios A B C](../results/charts/03_escenarios_A_B_C.png)

---

### Estabilidad en Reprogramación (`I4`: *"DOC_01 ya no puede los miércoles"*)
`qwen2.5:7b` tradujo correctamente la petición en español a `{"action": "block_teacher_day", "entity_id": "DOC_01", "day": 2, "slot": null}` y `R2` reprogramó:

| Estrategia sobre `I4` | Viol. Duras | Sesiones Movidas | Sesiones Intactas | Estabilidad | Penaliz. Blanda | Tiempo (s) |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: |
| **R2 Reprogramación (mínima perturbación)** | **0** | **2 / 142** | **140 / 142** | **98.59%** | 364 | **0.626** |
| Resolver `I4` desde cero | 0 | 135 / 142 | 7 / 142 | 4.93% | 352 | 6.084 |

![F1 R5 y Estabilidad I4](../results/charts/04_p2_f1_y_i4_estabilidad.png)

---

### P4. ¿Las explicaciones del LLM ayudan a entender los conflictos?
- **Prueba técnica en vivo con `ollama:qwen2.5:7b`:** El explicador `R6` generó resúmenes estructurados de `I3` e `I4` destacando factibilidad (`0 duras`), horas muertas, distribución de materias y ajustes posibles.
- **Hallazgo real de ingeniería de prompts:** En la primera corrida con `qwen2.5:7b`, el modelo tradujo literalmente el campo `teacher_gaps` como *"faltas de docentes"* en lugar de *"huecos entre clases"*, por lo que se refinó el *system prompt* de `src/r6_llm_explainer/explainer.py` con glosario explícito de cada métrica.
- **Evaluación cualitativa en presentación final:** Se preparó la rúbrica Likert 1–5 en `docs/05_rubrica_evaluacion_p4.md` para recoger la opinión de coordinación académica y equipos pares durante la demostración en vivo.
