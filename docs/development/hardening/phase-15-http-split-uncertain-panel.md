# Phase 15 — Split de http.py + reconciliación de entregas uncertain desde el panel

| Campo | Valor |
|---|---|
| Fase | `15 — http split + uncertain reconciliation` |
| Estado | `COMPLETED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `kimi/phase-15-http-split` (15a), `kimi/phase-15b-uncertain-panel` (15b) |
| Fecha de inicio | `2026-08-13` |
| PR | `#39` (15a), `#40` (15b) |
| Merge commit | `9767f1f` (15a), `f29c58c` (15b) |

## Objetivo

Doble: (1) desbloquear la superficie HTTP para trabajo futuro — `http.py`
(1434 líneas, 646 sitios de mutación) está congelado por G-MUT-SITES y tanto
15b como el webhook de WhatsApp necesitan añadir endpoints; (2) primera
medición limpia del flujo WCT: implementación 100% delegada al coder, el
discriminador solo especifica, ejecuta gates y triagia — cero líneas propias.

## Plan aprobado

- **15a**: split puro de `http.py` en módulos/routers ≤100
  sitios, comportamiento preservado, fachada de compatibilidad de imports
  (mismo patrón que el split de `admin.py` en fase 14). Integrado en PR #39.
- **15b**: reconciliación de entregas `uncertain` desde el panel, con spec
  Gherkin aprobado por el mantenedor:

  `features/admin_uncertain_resolution.feature`:
  - Escenario Outline: resolver `delivered`/`retry` desde la sección outbox
    llamando a `POST /v1/runtime/outbox/{id}/resolve` (endpoint nuevo, grant
    P5 emitido por servidor, mismo patrón que el CLI `resolve-uncertain`).
  - Acciones solo en filas `uncertain`.
  - `retry` con intentos agotados → error inline sin efectos laterales.

## Superficie actual (reconocimiento)

- Caso de uso existente: `reminder_notifications.resolve_uncertain`
  (`application/use_cases/reminder_notifications.py:182`) con guards P5 +
  ApprovalGrant, límite `MAX_DELIVERY_ATTEMPTS`, mirror terminal.
- CLI existente: `infrastructure/worker.py resolve-uncertain
  --message-id --resolution --confirm` (confirmación literal).
- Panel: outbox read-only; sin endpoint HTTP para resolve.

## Ejecución 15a (Split de `http.py`)

- Monolito inicial: `src/personal_assistant/infrastructure/http.py` (1434 líneas, 646 sitios de mutación).
- Desglose modular resultante (todos ≤100 sitios):
  - `http_models.py`: 82 sitios
  - `http_errors.py`: 23 sitios
  - `http_auth.py`: 49 sitios
  - `http_worker.py`: 89 sitios
  - `http_container.py`: 13 sitios
  - `http_converters.py`: 24 sitios
  - `http_dynamic.py`: 7 sitios
  - `http_telegram_replies.py`: 71 sitios
  - `http_telegram_transcription.py`: 93 sitios
  - `http_routes_health.py`: 19 sitios
  - `http_routes_telegram.py`: 34 sitios
  - `http_routes_admin_metrics.py`: 49 sitios
  - `http_routes_admin_data.py`: 60 sitios
  - `http_routes_runtime.py`: 42 sitios
  - `http_app.py`: 36 sitios
  - `http.py` (fachada de reexportación): 47 sitios
- Verificación: 17/17 gates PASS en `wct gate --tier commit`, 961 tests pasando, 0 regresiones. PR #39 merged a main (`9767f1f`).

## Ejecución 15b (Reconciliación de entregas `uncertain` desde el panel)

- Gherkin spec: `features/admin_uncertain_resolution.feature` (G-ACCEPT PASS).
- Endpoint HTTP: `POST /v1/runtime/outbox/{message_id}/resolve` implementado en `src/personal_assistant/infrastructure/http_routes_outbox.py` (9 sitios) con modelos en `http_models.py` (87 sitios).
- Renderizado de panel: `src/personal_assistant/infrastructure/admin_render_outbox.py` (65 sitios) con script JS para confirmación, envío bearer token y feedback inline.
- Ensamblado: `admin_render.py` (80 sitios), `http_app.py` (36 sitios), `http.py` (49 sitios).
- Suite de pruebas: `tests/test_admin_uncertain_resolution.py` (9 tests unitarios y de integración HTTP).
- Verificación: 17/17 gates PASS en `wct gate --tier commit`, 970 tests pasando (386 subtests), 0 mutantes sobrevivientes. PR #40 merged a main (`f29c58c`).

---

## Feedback Estructurado para el Well Code Template (WCT)

La ejecución de la Fase 15 sirvió como laboratorio de medición del flujo agentic con gates estrictos de WCT. A continuación se detallan las lecciones aprendidas y oportunidades de mejora para el harness:

### 1. Robustez del Flujo Coder-Verificador mediante Gates Objetivos
- **Acierto**: La combinación de especificación Gherkin (`G-ACCEPT`), presupuesto estricto de mutación (`G-MUT-SITES`), tipado estricto (`G-TYPE`) y suite completa (`G-TEST`) operó como un oráculo determinista. El subagente pudo iterar y autocorregirse sin requerir inspección manual de código por parte del mantenedor para detectar errores sutiles (como validaciones de Pydantic de outbox o restricciones de reintentos).

### 2. Puntos de Fricción y Mejoras Propuestas para WCT

#### A. Identificación de Funciones en `G-MUT-SITES` (`nombre:línea`)
- **Problema**: El manifiesto de mutación identifica funciones como `modulo.py::funcion:linea`. Cuando se añade un import o una línea arriba en un archivo, todas las líneas de funciones subsecuentes se desplazan, provocando que el hash del manifiesto no coincida y WCT considere que *todas* las funciones del archivo cambiaron, disparando `G-MUT-SITES` en archivos legacy que excedían el límite histórico.
- **Propuesta de Mejora**: Identificar y hashear funciones basándose en su **AST normalizado / semantic fingerprint** (`ast.dump(func_node)`) en vez del número de línea físico. De esta forma, cambios que solo mueven de posición una función no invalidan su estado en el manifiesto.

#### B. Diagnóstico de `G-ACCEPT` en `placeholder-variant`
- **Problema**: Cuando dos escenarios contienen un paso que normaliza a la misma forma (`<value>`), `G-ACCEPT` reporta `features/x.feature:<linea>: placeholder-variant` sin imprimir qué paso colisionó con cuál otro escenario.
- **Propuesta de Mejora**: Enriquecer el mensaje de error de `G-ACCEPT` mostrando el texto del paso normalizado y la referencia cruzada exacta: `Paso '<step>' en escenario A colisiona con paso en escenario B (línea Y)`.

#### C. Aislamiento de Formato (`wct fmt` / `ruff format` diferencial)
- **Problema**: Si un agente corre `ruff format` sobre todo el árbol cuando `G-FMT` está desactivado (en proyectos en transición), formatea archivos legacy intactos y dispara fallos de diff en cascada (`G-MUT-SITES`).
- **Propuesta de Mejora**: Proveer un flag nativo `--staged` o `--diff-only` en las herramientas de formato de WCT (`wct fmt --staged`) para que los agentes sólo formateen las líneas o archivos involucrados en su cambio.

#### D. Operación Atómica de `wct mutate update-manifest` con `integrity bless`
- **Problema**: Tras actualizar el manifiesto con `wct mutate update-manifest`, el gate `G-META-1` falla de inmediato porque `integrity.lock` no está sincronizado, obligando a un paso manual adicional de `wct integrity bless`.
- **Propuesta de Mejora**: Permitir que `wct mutate update-manifest --approved-by "..." --reason "..."` regenere automáticamente el lockfile de integridad de forma atómica y consistente con el log de gobernanza.

#### E. Validación de Precondiciones de Entorno en `G-TEST`
- **Problema**: Cuando `G-TEST` ejecuta `pytest -q`, si falta una variable como `TEST_POSTGRES_DSN`, las pruebas de integración pueden fallar o saltarse sin una advertencia explícita en el resumen del gate.
- **Propuesta de Mejora**: Permitir declarar en `governance/policy.yaml → environment_required` las variables requeridas para cada tier de gates, emitiendo un mensaje claro de prerrequisito faltante antes de lanzar la suite.


