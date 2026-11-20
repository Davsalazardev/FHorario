# Instrumento de Evaluación Cualitativa — Pregunta Guía P4

**Pregunta guía `P4`:** *¿Las explicaciones del LLM ayudan a entender los conflictos y tomar decisiones de ajuste?*  
**Cómo se responde (según el proyecto):** Opinión estructurada de la coordinación académica de ITIID y de otros equipos durante la presentación final con demostración en vivo.

---

## Contexto de la demostración en vivo

Durante la presentación se muestran dos casos reales generados por el módulo **`R6` (LLM Explicador con `qwen2.5:7b` local en Ollama)** a partir del reporte oficial del validador:
1. **Caso 1 (Conflicto en horario directo / instancia `I3`):** Explicación de violaciones duras y fuentes principales de penalización blanda comparando Escenarios `A`, `B` y `C`.
2. **Caso 2 (Reprogramación `I4` — *"El docente DOC_01 ya no puede los miércoles"*):** Explicación del cambio traducido, estabilidad lograda (`140/142` sesiones intactas) y detalle exacto de las 2 sesiones movidas.

---

## Rúbrica breve para evaluadores (Escala Likert 1–5)

*1 = Totalmente en desacuerdo · 2 = En desacuerdo · 3 = Neutral · 4 = De acuerdo · 5 = Totalmente de acuerdo*

| # | Criterio evaluado | 1 | 2 | 3 | 4 | 5 |
| :-: | :--- | :-: | :-: | :-: | :-: | :-: |
| 1 | **Claridad sin jerga técnica:** La explicación se entiende sin saber cómo funciona CP-SAT ni la lógica proposicional interna. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 2 | **Fidelidad al validador oficial:** Los conflictos, conteos y sesiones movidas que reporta el LLM coinciden exactamente con el validador común. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 3 | **Utilidad para decidir escenarios (`A`/`B`/`C`):** Ayuda a ver el compromiso real entre horas muertas de estudiantes, huecos y preferencias docentes. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 4 | **Accionabilidad ante cambios (`I4`):** Las propuestas de ajuste y el detalle de sesiones movidas permiten aprobar o corregir la reprogramación con confianza. | ☐ | ☐ | ☐ | ☐ | ☐ |
| 5 | **Confianza en la regla de diseño:** Separar optimizador + validador (decisión) del LLM (traducción y explicación) resulta más confiable que pedir el horario directo a un LLM. | ☐ | ☐ | ☐ | ☐ | ☐ |

### Preguntas abiertas (para el cierre de la presentación)
1. ¿Qué información faltó en la explicación de `R6` que coordinación necesitaría antes de publicar el horario?
2. ¿Qué parte del flujo de revisión humana (`R5` aprobación de preferencias o `R6` confirmación de cambios) consideras más útil?

---

## Plantilla de consolidación de resultados (llenar tras la presentación final)

| Perfil del evaluador | N respuestas | Prom. Claridad | Prom. Fidelidad | Prom. Utilidad | Prom. Accionabilidad | Prom. Confianza |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: |
| Coordinación académica ITIID | — | — | — | — | — | — |
| Equipos pares (7.º cuatrimestre) | — | — | — | — | — | — |
| **Total global** | — | — | — | — | — | — |
