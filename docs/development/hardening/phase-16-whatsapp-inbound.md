# Phase 16 — Webhook entrante de WhatsApp (spec aprobado + plan de implementación)

| Campo | Valor |
|---|---|
| Fase | `16 — whatsapp inbound webhook` |
| Estado | `SPEC_APPROVED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `kimi/phase-16-config-split` (16a), `kimi/phase-16-whatsapp-inbound` (16b) |
| Fecha de inicio | `2026-08-14` |
| PR | pendiente (16a), pendiente (16b) |
| Merge commit | pendiente |

## Objetivo

Recibir mensajes de texto por WhatsApp Cloud API y convertirlos en comandos
del asistente (molde del canal Telegram), con verificación de firma HMAC y
feature flag. Scope: **solo inbound**. El reply saliente vía Graph API queda
para una fase futura — la respuesta HTTP lleva el reply con `sent=false`.

Ejecución piloto WCT: coder externo implementa; el discriminador (Kimi) solo
especifica, ejecuta gates y triagia. Cero líneas propias del discriminador.

## Precondición detectada en recon (bloqueante para 16b)

`src/personal_assistant/infrastructure/config.py` tiene **355 sitios de
mutación** (límite G-MUT-SITES: 100). Hoy está limpio contra el manifest,
pero añadir settings de WhatsApp obliga a modificar `AppSettings.from_env` →
el gate bloquea. Es el mismo muro que tenía `http.py` en fase 15. Por eso la
fase se parte en 16a (split de config) y 16b (webhook).

Lo que **ya existe** y NO hay que reconstruir (MIN-001/MIN-006):

- `adapters/inbound/channels/whatsapp.py` → `WhatsAppAdapter.normalize_webhook`
  (31 sitios): extrae texto, `wa_id`, `message_id` del payload de Meta.
- `adapters/inbound/api.py` → `normalize_whatsapp_webhook(payload, tenant_id=...)`.
- `application/dto/channels.py` → `ChannelName.whatsapp` y `NormalizedMessage`.
- `container.commands.handle(principal, message, now=..., timezone=...)` es
  agnóstico de canal: es el mismo punto de entrada que usa Telegram.

Lo que **no existe** y es el trabajo de 16b: ruta HTTP, handshake GET de Meta,
verificación HMAC-SHA256, principal con lista permitida, settings por env.

## Gherkin aprobado por el mantenedor (2026-08-14)

Vive en `features/whatsapp_inbound_webhook.feature`. Reproducido aquí solo
como referencia; el archivo es la fuente de verdad:

- Handshake de suscripción Meta: token configurado → echo del challenge en
  texto plano; token desconocido → rechazo.
- Scenario Outline: texto firmado entrante → comando manejado para el tenant,
  respuesta con reply y `sent=false` (dos ejemplos parametrizados).
- Firma inválida → rechazo sin efectos laterales (ni reminder, ni reply, ni
  outbox).
- Callbacks de solo `statuses` → ack 200 no-op.
- Replay del mismo `message_id` → ack, reminder existe exactamente una vez.
- Canal deshabilitado → respuesta de canal no disponible.

## Plan detallado — 16a: split de `config.py`

Rama: `kimi/phase-16-config-split` desde `main`. Refactor puro: comportamiento
idéntico, misma suite verde, mismo patrón de fachada que el split de
`http.py` (15a) y de `admin.py` (14).

1. **Inventario de superficie.** Grepear todos los imports de
   `personal_assistant.infrastructure.config` en `src/` y `tests/`
   (públicos y privados). Todo símbolo importado desde afuera debe quedar
   re-exportado por la fachada `config.py`.
2. **Corte sugerido** (el coder mide y ajusta; cada módulo ≤100 sitios con
   `wct mutate scan` — ojo: cada literal string cuenta como sitio):
   - `config_env.py` — helpers `_load_env_file`, `_env`, `_optional_env`,
     `_env_bool`, `_finite_seconds`, `_env_permission_tier`, `_parse_csv`.
   - `config_persistence.py` — `load_persistence_settings_from_env`,
     `load_database_settings_from_env`.
   - `config_settings.py` — dataclass `AppSettings` + `__post_init__`.
   - `from_env` (~170 líneas, probablemente >100 sitios por sí solo):
     descomponer en loaders por sección (p.ej. `config_loader_telegram.py`,
     `config_loader_runtime.py`, …) con `from_env` como orquestador fino.
   - `config.py` — fachada de re-exportación.
3. **Verificación del coder antes de PR**:
   - `uv run ruff check src tests` y `uv run mypy src` limpios.
   - `TEST_POSTGRES_DSN="postgresql://assistant_ci:test_postgres@127.0.0.1:5432/assistant_ci" uv run pytest -q`
     con los mismos conteos que `main` (registrar ambos en el PR).
   - `uv run python -m tools.wct mutate scan` → ningún archivo >100 sitios.
   - `uv run python -m tools.wct gate --tier fast` → 7/7.
   - NO correr `mutate update-manifest` ni `integrity bless` ni tocar rutas
     protegidas: eso lo hace el discriminador tras verificar.
4. **PR** con template `.github/pull_request_template.md`, base `main`,
   merge commit. El discriminador verifica diff de comportamiento (mismo
   response shapes, mismos defaults, mismo orden de precedencia env > archivo)
   antes de mergear.

## Plan detallado — 16b: webhook entrante

Rama: `kimi/phase-16-whatsapp-inbound` desde `main` **con 16a ya mergeada**.
TDD (TEST-001): cada comportamiento empieza con un test que fallaría ante una
implementación plausiblemente incorrecta. Archivo de tests nuevo:
`tests/test_whatsapp_inbound_webhook.py` (molde:
`tests/test_reminder_boundary_telegram.py` y `tests/test_http_local_auth.py`).

1. **Settings** (en la estructura nueva de 16a): dataclass `WhatsAppSettings`
   con `enabled: bool` (default `False`, feature flag), `app_secret: str`
   (`repr=False`), `verify_token: str` (`repr=False`),
   `allowed_user_ids: frozenset[str]`. Loader desde env:
   `WHATSAPP_ENABLED`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`,
   `WHATSAPP_ALLOWED_USER_IDS` (CSV, reusa `_parse_csv`). SEC-001: nada de
   valores de ejemplo realistas. `AppSettings` gana el campo `whatsapp` y
   `from_env` lo cablea. Sin cambios de egress (no hay outbound).
2. **Auth** — `verify_whatsapp_signature(settings, body: bytes,
   signature_header: str | None)`: HMAC-SHA256 hex del body crudo con
   `app_secret`, comparado con `secrets.compare_digest` contra el header
   `X-Hub-Signature-256` (formato `sha256=<hex>`). `whatsapp_principal(
   settings, actor_id)`: molde de `telegram_principal`
   (`http_auth.py:48-61`) — número no permitido → `AssistantError`
   PERMISSION_DENIED sanitizado. `http_auth.py` tiene 49 sitios: si al añadir
   supera 100, extraer a `http_auth_whatsapp.py`.
3. **Rutas** — `http_routes_whatsapp.py` (nuevo, ≤100 sitios), función
   `register_whatsapp_routes(app, container, settings)`:
   - `GET /webhooks/whatsapp`: parámetros `hub.mode`, `hub.verify_token`,
     `hub.challenge`. Si `mode == "subscribe"` y el token coincide
     (`compare_digest`) → `PlainTextResponse(challenge)`. Si no → 403
     sanitizado. El handshake responde con el verify_token configurado
     independientemente del flag `enabled` (el flag gobierna mensajes, no la
     suscripción).
   - `POST /webhooks/whatsapp`: si `not settings.whatsapp.enabled` → error
     sanitizado de canal no disponible (sin procesar nada). Leer body crudo
     (`Request.body()`) → verificar firma (firma inválida → rechazo
     sanitizado, cero efectos laterales) → parsear JSON → si el payload no
     trae `messages` (callbacks de `statuses`) → ack 200 no-op →
     `normalize_whatsapp_webhook` → `whatsapp_principal` →
     `container.commands.handle(...)` → `WhatsAppWebhookResponse` con
     `status`, `reply`, `sent=False`. `ReminderIdempotencyConflict` → ack 200
     con reply sanitizado y `sent=False` (patrón `http_routes_telegram.py:106-117`).
4. **Modelos**: `http_models.py` está en 87 sitios (margen 13). Medir antes
   de añadir `WhatsAppWebhookResponse`; si no cabe, módulo
   `http_models_whatsapp.py` nuevo.
5. **Ensamblado**: registrar el router en `http_app.py` (36 sitios, suma
   pequeña).
6. **Feature file**: ya existe `features/whatsapp_inbound_webhook.feature`
   (aprobado). No editarlo sin re-aprobación del mantenedor.
7. **Tests mínimos** (cada uno con aserción sobre el SUT, G-INTROVERT):
   handshake ok / token erróneo; firma válida / inválida (computar el HMAC en
   el test con un secreto de pruebas y postear bytes crudos); canal
   deshabilitado; payload de solo `statuses` → no-op; replay del mismo
   `message_id` → un solo reminder; número no permitido → rechazo; respuesta
   con `sent=False`; errores sin filtrar PII (wa_id) en el body.
8. **Verificación del coder antes de PR**: idéntica a 16a paso 3, más cobertura
   de rama ≥90 % en líneas nuevas (G-COV-DIFF) y `pytest --collect-only -q`
   tras tocar tests (TEST-011).
9. **PR** con template, base `main`, merge commit. No mergear: el
   discriminador verifica y mergea.

## Restricciones WCT para el coder (16a y 16b)

- Rutas protegidas prohibidas: `governance/**`, `tools/wct/**`,
  `pyproject.toml`, `uv.lock`, `.importlinter`, `.github/**`, `.claude/**`,
  `.agents/**`, `.pre-commit-config.yaml`, `quality/redteam/**`.
- Cero dependencias nuevas: HMAC con `hmac`/`hashlib` de stdlib (MIN-001,
  peldaño 3). Si algo parece requerir dependencia, parar y reportar.
- Temporales solo en `build/tmp/`. Nada de `/tmp`.
- Commits conventional; sin `--no-verify`; push solo a la rama de la fase.
- Si un gate falla por causa ajena al cambio (p.ej. baseline de secretos),
  parar y reportar al discriminador en vez de rodearlo.

## Checklist del discriminador (por PR)

1. `gate --tier fast` + suite completa con `TEST_POSTGRES_DSN` (conteos
   comparados contra `main`).
2. `mutate scan`: todo archivo ≤100 sitios.
3. Diff de comportamiento: 16a no cambia nada observable; 16b solo añade
   superficie nueva (ningún endpoint existente tocado).
4. `mutate update-manifest` → `integrity bless --approved-by "Yosoyepa"
   --reason "..."` → `gate --tier commit` completo (17/17 esperado).
5. Merge commit + borrar rama + actualizar este log (PR docs aparte, main
   protegida).

## Criterios de salida de la fase

- 16a: `config.py` partido, suite verde con los mismos conteos, PR mergeado.
- 16b: Gherkin cubierto por tests, 17/17 gates, CI 5/5, PR mergeado.
- Feedback WCT del piloto registrado en la sección siguiente (se llena al
  cerrar la fase).

## Feedback WCT de la fase

(pendiente — se llena al cierre)
