# Phase 15 — Split de http.py + reconciliación de entregas uncertain desde el panel

| Campo | Valor |
|---|---|
| Fase | `15 — http split + uncertain reconciliation` |
| Estado | `COMPLETED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `kimi/phase-15-http-split` (15a), `kimi/phase-15b-uncertain-panel` (15b) |
| Fecha de inicio | `2026-08-13` |
| PR | `#39` (15a), `<pendiente>` (15b) |
| Merge commit | `9767f1f` (15a), `<pendiente>` (15b) |

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
- Verificación: 17/17 gates PASS en `wct gate --tier commit`, 970 tests pasando (386 subtests), 0 mutantes sobrevivientes.

