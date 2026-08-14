# Phase 17 — WhatsApp outbound: reply + entrega proactiva de recordatorios (spec aprobado)

| Campo | Valor |
|---|---|
| Fase | `17 — whatsapp outbound` |
| Estado | `SPEC_APPROVED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `kimi/phase-17-worker-split` (17a), rama coder 17b pendiente |
| Fecha de inicio | `2026-08-14` |
| PR | pendiente (17a), pendiente (17b) |
| Merge commit | pendiente |

## Objetivo

Cerrar el loop conversacional de WhatsApp: (1) la respuesta del asistente se
envía de vuelta por Graph API al número que escribió (`sent=true`), y (2) los
recordatorios creados desde WhatsApp se entregan proactivamente por WhatsApp
cuando llega su hora. Es la última pieza funcional para la demo y para
`v0.2.0-alpha.2` (fase 18 = release prep).

Ejecución piloto WCT: coder externo implementa; el discriminador (Kimi) solo
especifica, ejecuta gates y triagia.

## Hallazgos del recon (condicionan el plan)

- El puerto ya existe: `application/ports/notifications.py` →
  `NotificationPort.send(principal, NotificationRequest, approval=...)` con
  `NotificationResult` tipado por outcome (`success`, `known-transient`,
  `permanent`, `unknown-outcome`). `NotificationRequest.channel` ya viaja
  desde la creación del recordatorio (`reminders.py:106`).
- **Bloqueo G-MUT-SITES**: hoy `container.notifications` es
  `TelegramNotificationTool` cableado a fuego en dos puntos:
  `http_container.py:36` (13 sitios, editable) y `worker.py:182`
  (**148 sitios, sobre el límite → intocable sin split**). `bootstrap.py`
  (182 sitios) NO necesita cambios: `build_container` ya recibe
  `notifications=` por parámetro.
- `adapters/outbound/notifications/telegram.py` (248 sitios) es legacy
  intocable: el sender de WhatsApp es módulo nuevo, no edición.
- Egress: `config_validation.py:108-120` construye `required_egress`
  (patrón `TELEGRAM_BOT_TOKEN → DEFAULT_TELEGRAM_API_URL`). WhatsApp sigue el
  mismo patrón con una constante nueva en `config_constants.py` (11 sitios,
  margen amplio).
- `NotificationResult.provider_message_id` es `int` estricto; el `wamid` de
  WhatsApp es string → usar el campo `notification_id` (str).
- Molde de tests del sender: `tests/test_telegram_notifications.py`.

## Gherkin aprobado por el mantenedor (2026-08-14)

Vive en `features/whatsapp_outbound_delivery.feature`; el archivo es la fuente
de verdad. Resumen:

- Scenario Outline: texto entrante firmado → reply enviado por Graph API al
  mismo número y el webhook reporta `sent=true` (dos ejemplos).
- Recordatorio creado desde WhatsApp llega a su hora → el worker lo entrega
  por Graph API y el outbox queda `published`.
- Error transitorio del proveedor → el outbox queda `pending` para reintento.
- Resultado ambiguo (conexión caída tras intentar) → `uncertain`, sin filtrar
  contenido del mensaje.
- Sin access token configurado → el webhook responde OK reportando que no se
  envió reply.

## Plan detallado — 17a: split de `worker.py`

Rama desde `main`. Refactor puro, patrón fachada (igual que 15a y 16a).

1. **Inventario de superficie** (el coder lo verifica y amplía): imports de
   `personal_assistant.infrastructure.worker` en `bootstrap.py:66`,
   `tests/test_p0_safety_characterization.py:48`,
   `tests/test_scheduler_worker.py:21,25`. Todo símbolo importado desde afuera
   queda reexportado por la fachada `worker.py`.
2. Corte sugerido (medir y ajustar; todo ≤100 sitios): `worker_cli.py`
   (parsing/entrypoint `main`), `worker_runtime.py` (loop del worker),
   `worker_container.py` (construcción de container + notification tool).
3. Verificación del coder antes de PR: ruff/mypy limpios; suite completa con
   `TEST_POSTGRES_DSN` y los mismos conteos que `main` (registrados en la PR);
   `mutate scan` sin archivos >100; `gate --tier fast` 7/7.
4. PR con template, base `main`. No mergear: eso es del discriminador.

## Plan detallado — 17b: sender + router + reply + wiring

Rama desde `main` con 17a mergeada. TDD estricto.

1. **Config**: `WhatsAppSettings` gana `access_token: str | None`
   (`repr=False`) y `phone_number_id: str` (loader: `WHATSAPP_ACCESS_TOKEN`,
   `WHATSAPP_PHONE_NUMBER_ID`). Constante `DEFAULT_WHATSAPP_API_URL`
   (Graph API) en `config_constants.py`. En `config_validation.py`:
   `WHATSAPP_ACCESS_TOKEN → DEFAULT_WHATSAPP_API_URL` en `required_egress`
   cuando el token está configurado (margen: 63 → <100 sitios).
2. **Sender** — `adapters/outbound/notifications/whatsapp.py` (nuevo):
   `WhatsAppGraphApiClient` (POST `/vXX/{phone_number_id}/messages`, bearer
   token, pasa por el allowlist de egress) y `WhatsAppNotificationTool`
   implementando `NotificationPort`. Mapeo de outcomes espejo del de Telegram:
   2xx → `success` con `notification_id` = `wamid`; 4xx → `permanent`;
   429/5xx con `Retry-After` → `known-transient`; timeout/ambigüedad tras
   envío → `unknown-outcome` (alimenta `uncertain`). Cero contenido del
   mensaje ni destinatario en logs/traces/errores (patrón del repo).
3. **Router por canal** — `adapters/outbound/notifications/router.py`
   (nuevo): `ChannelNotificationRouter` implementa `NotificationPort` y
   delega por `request.channel` en `{telegram: ..., whatsapp: ...}`; canal
   desconocido → fallo `permanent` sanitizado.
4. **Reply inmediato** — `http_whatsapp_replies.py` (nuevo, molde
   `http_telegram_replies.py:21-49`): `_send_whatsapp_reply` con grant P5
   emitido por servidor y fallo del proveedor → `sent=False` sin tumbar el
   webhook. Cableado en `http_routes_whatsapp.py` (53 sitios; si la suma
   supera 100, el helper va entero en su módulo y la ruta solo lo llama).
   Sin `access_token` configurado → no se intenta envío y `sent=False`.
5. **Wiring**: `http_container.py` (13 sitios) y `worker.py` post-split
   construyen el `ChannelNotificationRouter` con las tools cuyos tokens estén
   configurados (telegram y/o whatsapp). `bootstrap.py` NO se toca.
6. **Tests** (`tests/test_whatsapp_outbound.py`, molde
   `tests/test_telegram_notifications.py`; más extensión de
   `tests/test_whatsapp_inbound_webhook.py` para el reply): sender con API
   mockeada a nivel de transporte para cada outcome; router por canal y canal
   desconocido; reply end-to-end vía TestClient con `sent=true`; sin token →
   `sent=false`; entrega de recordatorio debido por WhatsApp vía worker →
   `published`; transitorio → `pending`; ambiguo → `uncertain` sin PII.
   Aserciones sobre el SUT (G-INTROVERT); cobertura de rama ≥90 % en líneas
   nuevas; `pytest --collect-only -q` tras tocar tests (TEST-011).
7. **Verificación del coder antes de PR**: igual que 17a paso 3.

## Restricciones WCT para el coder (17a y 17b) — incluye lecciones de fase 16

- **PROHIBIDO correr `wct integrity bless` y `wct mutate update-manifest`.**
  Son comandos del mantenedor/discriminador. Fase 16 registró una brecha de
  auto-firma; desde esta fase, un bless ejecutado por el coder invalida la PR
  entera.
- Rutas protegidas intocables: `governance/**`, `tools/wct/**`,
  `pyproject.toml`, `uv.lock`, `.importlinter`, `.github/**`, `.claude/**`,
  `.agents/**`, `.pre-commit-config.yaml`, `quality/redteam/**`,
  `.secrets.baseline`. Si un gate pide tocarlas, parar y reportar.
- **Phase logs append-only para el coder**: solo añadir la sección de
  ejecución propia al final; jamás reescribir spec, roles ni feedback.
- Cero dependencias nuevas (Graph API vía cliente HTTP stdlib/ya instalado,
  como hace `TelegramBotApiClient`; verificar qué usa y reusar lo mismo).
- Temporales solo en `build/tmp/`. Commits conventional, sin `--no-verify`.
- Si un gate falla por causa ajena al cambio, parar y reportar.

## Checklist del discriminador (por PR)

1. `gate --tier fast` + suite completa con `TEST_POSTGRES_DSN` (conteos
   comparados contra `main`).
2. `mutate scan`: todo archivo ≤100 sitios.
3. Diff de comportamiento: 17a no cambia nada observable; 17b solo añade
   superficie (Telegram intacto).
4. `integrity-log.md`: verificar que NO haya entradas nuevas firmadas por el
   coder. Manifest/bless los corre el discriminador solo si son necesarios.
5. Merge commit + borrar rama + actualizar este log por PR docs aparte.

## Criterios de salida

- 17a: `worker.py` partido, suite verde con mismos conteos, PR mergeada.
- 17b: Gherkin cubierto por tests, 17/17 gates, CI 5/5, PR mergeada.
- Con 17b en `main`, el proyecto queda listo para fase 18 (release prep de
  `v0.2.0-alpha.2`: bump de versión, notas, runbook WhatsApp, smoke con
  evidencia, tag).

## Feedback WCT de la fase

(pendiente — se llena al cierre por el discriminador)

## Ejecución 17a — Split de `worker.py` (Coder)

- **Fecha**: 2026-08-14
- **Rama**: `gemini/phase-17-worker-split`
- **Resultados**:
  - `src/personal_assistant/infrastructure/worker.py` original (148 sitios) descompuesto en 3 módulos acotados:
    - `worker_runtime.py`: 52 sitios (loop de ejecución, ticks y approval policy).
    - `worker_cli.py`: 38 sitios (parser de argumentos CLI y formateo seguro de mensajes).
    - `worker.py` (fachada y ensamblado CLI): 73 sitios (reexportaciones, ensamblado runtime y entrypoint `main`).
  - **Presupuesto de mutación**: Todos los módulos ≤100 sitios (ningún archivo en infracción de `G-MUT-SITES`).
  - **Superficie de importación**: 100% preservada para consumidores externos y tests (`Clock`, `ReminderWorker`, `ReminderWorkerTick`, `RuntimeNotificationApprovalPolicy`, `Sleeper`, `StopPredicate`, `_parser`, `_print_rows`, `_runtime`, `_safe_message`, `_timestamp`, `main`, `utc_now`).
  - **Verificación**:
    - `ruff check src tests`: PASS
    - `mypy src`: PASS
    - `pytest -q`: 983 passed, 3 skipped, 386 subtests passed
    - `wct mutate scan`: 0 archivos modificados >100 sitios
    - `wct gate --tier commit`: 17/17 gates PASS
