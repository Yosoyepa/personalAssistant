# Phase 18 — Release prep v0.2.0-alpha.2 (spec aprobado)

| Campo | Valor |
|---|---|
| Fase | `18 — alpha.2 release prep` |
| Estado | `SPEC_APPROVED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | rama coder pendiente (docs/evidencia), rama mantenedor (bump+tag) |
| Fecha de inicio | `2026-08-18` |
| PR | pendiente |
| Merge commit | pendiente |

## Objetivo

Cortar `v0.2.0-alpha.2` con la evidencia de aceptación completa, siguiendo la
ceremonia establecida en `docs/releases/v0.2.0-alpha.1.md`: notas de release,
runbook, evidencia real de gates/suite/eval, bump de versión protegido, tag
anotado y prerelease.

Alcance funcional que entra en alpha.2 (todo ya en `main` o en vuelo por 17b):
panel admin con aprobaciones P5 y reconciliación de `uncertain` (14–15),
webhook WhatsApp inbound con HMAC (16b), reply y entrega proactiva por
WhatsApp (17b), harness WCT adoptado (13) y splits estructurales
(15a/16a/17a/17b).

## División de trabajo (lección de gobernanza de fase 16 aplicada)

### Coder — PR de documentación y evidencia (rama `gemini/phase-18-release-prep`)

Todo lo que NO es ruta protegida:

1. `docs/releases/v0.2.0-alpha.2.md` — misma estructura que alpha.1:
   Highlights, Acceptance evidence, Database change, Compatibility and known
   gaps. Reglas:
   - Toda cifra debe salir de una corrida real hecha por el coder en esta
     rama (suite con `TEST_POSTGRES_DSN`, corpus de eval determinista, gate
     commit, coverage). Prohibido inventar o copiar números de alpha.1.
   - Verificar y documentar la verdad actual: ¿hay migraciones nuevas desde
     `0005_worker_heartbeat.sql`? (listar `migrations/`); ¿sigue vivo el alias
     `/healthz` o ya se retiró? (grepear rutas); gaps declarados: WhatsApp sin
     audio/TTS ni media entrante, panel loopback-only, worker requiere
     PostgreSQL.
2. `docs/runbook/whatsapp.md` — variables de entorno (`WHATSAPP_ENABLED`,
   `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ALLOWED_USER_IDS`,
   `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`), alta de la app de
   Meta, suscripción del webhook, verificación de firma, smoke local con
   payload sintético firmado (ejemplo `curl` con HMAC computado), y
   troubleshooting (canal deshabilitado, firma inválida, `uncertain`).
   Nada de credenciales reales ni placeholders realistas (SEC-001).
3. `uv build` para confirmar que el paquete construye; reportar artefactos.
4. PR con template, base `main`. NO mergear.

El coder **no** toca `pyproject.toml` ni `uv.lock` ni crea tags: el bump es
ruta protegida y queda fuera de su PR.

### Mantenedor (humano) — bump + release

Tras mergear la PR del coder, en una rama propia:

1. Bump `version = "0.2.0-alpha.2"` en `pyproject.toml` + `uv lock`.
2. `wct integrity bless --approved-by "Yosoyepa" --reason "version bump
   v0.2.0-alpha.2 release"` **ejecutado por el mantenedor en su terminal**
   (regla dura post-fase-16: ningún agente corre bless).
3. PR protegida, merge commit, tag anotado `v0.2.0-alpha.2`, prerelease en
   GitHub con las notas.

### Discriminador (Kimi)

Verifica la PR del coder (números reproducidos, no solo leídos), prepara el
diff del bump si el mantenedor lo pide, y registra el cierre en este log.

## Restricciones WCT para el coder

Las mismas de la fase 17 (sin bless, sin rutas protegidas, logs append-only,
temporales en `build/tmp/`), más: los números de evidencia deben ser
reproducibles — cada cifra en las notas de release lleva el comando que la
generó.

## Criterios de salida

- PR docs/evidencia mergeada; notas de release con evidencia real.
- Bump + tag + prerelease hechos por el mantenedor.
- Este log actualizado a `COMPLETED` con el feedback WCT de la fase.

## Feedback WCT de la fase

(pendiente — se llena al cierre por el discriminador)
