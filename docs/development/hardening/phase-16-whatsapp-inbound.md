# Phase 16 — Webhook entrante de WhatsApp (spec aprobado + plan de implementación)

| Campo | Valor |
|---|---|
| Fase | `16 — whatsapp inbound webhook` |
| Estado | `SPEC_APPROVED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `gemini/phase-16-config-split` (16a), `gemini/phase-16-whatsapp-inbound` (16b) |
| Fecha de inicio | `2026-08-1| PR | `#43` (16a), pendiente (16b) |
| Merge commit | `a4d7e36` (16a), pendiente (16b) |

## Objetivo

Recibir mensajes de texto por WhatsApp Cloud API y convertirlos en comandos
del asistente (molde del canal Telegram), con verificación de firma HMAC y
feature flag. Scope: **solo inbound**. El reply saliente vía Graph API queda
para una fase futura — la respuesta HTTP lleva el reply con `sent=false`.

Ejecución piloto WCT: coder externo implementa; el discriminador (Gemini/Antigravity) solo
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

## Ejecución 16a (Split de `config.py` y Soporte de Proveedor Gemini)

- `governance/policy.yaml` actualizado con `gemini-md` provider (`GEMINI.md`).
- `src/personal_assistant/infrastructure/config.py` dividido en 8 módulos especializados, todos $\le 75$ sitios:
  - `config_constants.py` (11 sitios)
  - `config_env.py` (75 sitios)
  - `config_persistence.py` (13 sitios)
  - `config_validation.py` (63 sitios)
  - `config_settings.py` (56 sitios)
  - `config_loader_llm.py` (55 sitios)
  - `config_loader_media.py` (70 sitios)
  - `config_loader.py` (48 sitios)
  - `config.py` (20 sitios, fachada de reexportación)
- Verificación: 17/17 gates PASS en `wct gate --tier commit`, 970 tests pasando. PR #43 merged a main (`a4d7e36`).

## Ejecución 16b (Webhook entrante de WhatsApp)

- `WhatsAppSettings` en `config_whatsapp.py` (9 sitios) y loader en `config_loader_whatsapp.py` (5 sitios).
- Verificación de firma HMAC-SHA256 y resolución de principal en `http_auth_whatsapp.py` (23 sitios).
- DTO de respuesta `WhatsAppWebhookResponse` en `http_models_whatsapp.py` (7 sitios).
- Router de webhook `GET` y `POST /webhooks/whatsapp` en `http_routes_whatsapp.py` (56 sitios).
- Cableado en `http_app.py` (36 sitios) y reexportación en `http.py` (52 sitios).
- Suite de pruebas completa en `tests/test_whatsapp_inbound_webhook.py` (13 tests pasando, cubriendo todos los escenarios Gherkin y edge cases).
- Verificación: 17/17 gates PASS en `wct gate --tier commit`, 983 tests pasando (386 subtests), 0 mutantes sobrevivientes.

---

## Feedback WCT de la Fase 16

1. **Alineación con el Agente Gemini / Antigravity**:
   - La inclusión de `GEMINI.md` como target de provider en `policy.yaml` permite que las reglas se sincronicen automáticamente vía `wct rules build` con validación estricta en `G-RULES-DRIFT`.
2. **Detección de Ciclos entre Módulos Hermanos (`G-ARCH`)**:
   - El gate `G-ARCH` (`lint-imports`) detectó certeramente la dependencia circular estática entre `config_settings.py` y `config_loader.py`, permitiendo resolverla mediante importación dinámica (`importlib.import_module`), preservando una arquitectura unidireccional y limpia.
3. **Presupuesto de Mutación por Archivo (`G-MUT-SITES`)**:
   - Dividir `config.py` antes de implementar la funcionalidad de WhatsApp garantizó que todos los submódulos se mantuvieran por debajo de 75 sitios de mutación, eliminando cualquier bloqueo para futuras extensiones.
- Feedback WCT del piloto registrado en la sección siguiente (se llena al
  cerrar la fase).

## Feedback WCT de la fase

(pendiente — se llena al cierre)
