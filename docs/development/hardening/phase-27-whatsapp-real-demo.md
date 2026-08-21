# Phase 27 — Demo WhatsApp real (runbook + guión + validación de payload real)

| Campo | Valor |
|---|---|
| Fase | `27 — whatsapp real demo (Meta sandbox)` |
| Estado | `IN_PROGRESS` (alcance aprobado por el mantenedor, 2026-08-21; validación en vivo pendiente del setup de Meta) |
| Mantenedor | `Yosoyepa` |
| Ejecutor | planner (docs + config menor); coder solo si la validación en vivo revela drift |
| Commit base | `58b7403` (main tras merge #86, release v0.2.0-alpha.4) |
| Fecha de inicio | `2026-08-21` |
| PR | TBD |
| Merge commit | TBD |

## Objetivo

Que el mantenedor pueda mostrar el loop completo del producto con mensajes
**reales** de WhatsApp usando la sandbox de Meta: texto y nota de voz →
transcripción/extracción → recordatorio agendado → entrega proactiva por el
worker → visibilidad total en el panel admin.

## Alcance (aprobado 2026-08-21)

1. **Runbook de demo** `docs/runbook/whatsapp-demo.md`: setup de Meta paso a
   paso, wiring de env vars, borde HTTPS (túnel), verificación del handshake,
   checklist y troubleshooting.
2. **Guión de demo** (dentro del runbook): mensajes exactos, qué observar en
   cada sección del panel, y cómo mostrar idempotencia y aprobación P5.
3. **Bloque WhatsApp en `.env.example`** (gap detectado: no existía ninguna
   variable `WHATSAPP_*`): `WHATSAPP_ENABLED`, `WHATSAPP_APP_SECRET`,
   `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ALLOWED_USER_IDS`,
   `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` — valores vacíos, con
   comentarios (SEC-001: jamás valores reales).
4. **Ajustes menores**: tag de imagen de `deploy/compose.yaml`
   (`0.2.0-alpha.1` → `0.2.0-alpha.4`) y comentario del edge loopback que solo
   mencionaba Telegram (añadir GET+POST `/webhooks/whatsapp`).
5. **Validación contra payload real (segunda etapa, en vivo)**: capturar los
   payloads reales de la sandbox (texto y audio) desde el panel de Webhooks de
   Meta y fijarlos como fixtures de regresión en
   `tests/test_whatsapp_inbound_webhook.py` (o archivo nuevo). Puntos a
   validar: (a) Meta no envía `file_size` en el objeto `audio` — el pre-check
   de tamaño se omite y el post-descarga de 20 MB queda como defensa real;
   (b) las notas de voz llegan como `type: "audio"` con `voice: true` —
   cubierto por la rama `audio` ya soportada.

## Fuera de alcance

- Código nuevo de producto (MIN-001: el pipeline ya soporta el loop completo).
- Cambios en `.github/workflows/**` ni `governance/**`.
- Cualquier credencial real en el repo (todo vive en el `.env` local ignorado).

## Criterios de salida

1. PR de docs/config mergeada con CI verde (suite + gates sin cambios de
   comportamiento → sin mutación).
2. Demo en vivo ejecutada por el mantenedor siguiendo el runbook: texto OK,
   voz transcrita OK, recordatorio agendado y entregado, panel mostrando
   traces/scheduler/outbox/events.
3. Payloads reales capturados y fijados como fixtures de regresión (PR de
   tests, con mini-escenario Gherkin si la captura revela drift de
   comportamiento — PROC-003).
4. Phase log cerrado con el resultado de la validación en vivo.

## Registro de validación en vivo

(pendiente — se llena tras la demo real con fecha, capturas de payload y
cualquier drift encontrado)

## Feedback WCT de la fase

(pendiente — se llena al cierre por el planner)
