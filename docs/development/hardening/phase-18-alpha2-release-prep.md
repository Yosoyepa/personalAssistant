# Phase 18 — Release prep v0.2.0-alpha.2 (spec aprobado)

| Campo | Valor |
|---|---|
| Fase | `18 — alpha.2 release prep` |
| Estado | `COMPLETED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `gemini/phase-18-release-prep` (docs/evidencia), `yosoyepa/v0.2.0-alpha.2-bump` (bump protegido) |
| Fecha de inicio | `2026-08-18` |
| PR | `#57` (docs/evidencia), `#58` (bump protegido) |
| Merge commit | `50c7809` (#57), `7f9585e` (#58) |
| Tag / Release | `v0.2.0-alpha.2` (prerelease, 2026-08-18, por el mantenedor) |

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

1. **La versión vive en dos fuentes y el oráculo lo cazó.** El bump tocó solo
   `pyproject.toml`; `src/personal_assistant/__init__.py` (`__version__`) quedó
   en `0.2.0-alpha.1` y CI falló en
   `test_runtime_and_package_versions_share_the_project_source`
   (`tests/test_operational_readiness.py`). Fix de una línea (`cf6c7cc`) antes
   del tag. Propuesta para WCT: el procedimiento de release debe (a) derivar
   `__version__` de `importlib.metadata.version("personal_assistant")` para
   tener fuente única, o (b) documentar el bump como cambio de DOS archivos.
   Hallazgo clave: el test ya existía y funcionó como oráculo determinista —
   el error no llegó al tag.
2. **Comandos multilínea con backslash se rompen al pegarlos.** El primer
   intento del mantenedor de correr `integrity bless` falló porque los `\` de
   continuación no sobrevivieron al copy-paste. Propuesta: en runbooks y
   prompts de WCT, los comandos críticos se dan en UNA sola línea.
3. **La división coder/mantenedor por rutas protegidas no tuvo fricción.** El
   coder hizo docs+evidencia (#57) sin tocar rutas protegidas; el mantenedor
   hizo bump+bless+tag (#58) en su terminal. G-META-1 + bless documentado
   cubrieron la ruta protegida sin excepciones.
4. **Corrección del discriminador dentro de la PR del coder:** la ruta del
   webhook se documentó en singular; la real es `/webhooks/whatsapp`. Fix
   `8cd433b` en la misma PR. Refuerza la regla: toda ruta/comando documentado
   se verifica contra el código, no contra la memoria.

## Ejecución Phase 18 — Release prep v0.2.0-alpha.2 (Coder)

- **Fecha**: 2026-08-18
- **Rama**: `gemini/phase-18-release-prep`
- **Resultados**:
  - **Notas de Release (`docs/releases/v0.2.0-alpha.2.md`)**:
    - Estructura estándar completada con Highlights, Acceptance evidence, Database change y Compatibility/known gaps.
    - Cifras reales y reproducibles con sus respectivos comandos documentados.
    - Confirmada ausencia de migraciones nuevas (esquema `0005_worker_heartbeat.sql`).
    - Confirmada persistencia de `/healthz` con cabecera `Deprecation: true`.
  - **Runbook WhatsApp (`docs/runbook/whatsapp.md`)**:
    - Documentadas variables de entorno (`WHATSAPP_ENABLED`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ALLOWED_USER_IDS`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`).
    - Guía de alta en Meta for Developers, verificación HMAC SHA-256 (`X-Hub-Signature-256`), prueba de humo con payload sintético firmado y troubleshooting.
    - Cero secretos o credenciales reales utilizadas (SEC-001).
  - **Construcción de Paquete (`uv build`)**:
    - Generación exitosa de artefactos: `dist/personal_assistant-0.2.0a1.tar.gz` y `dist/personal_assistant-0.2.0a1-py3-none-any.whl`.
  - **Verificación**:
    - `ruff check src tests`: PASS
    - `mypy src`: PASS (195 source files)
    - `pytest -q`: **1015 passed, 3 skipped, 396 subtests passed**
    - `personal_assistant.evals`: **299/299 passed, 0 failed**
    - `pytest --cov`: **90% branch coverage**
    - `wct mutate scan`: 0 archivos modificados >100 sitios
    - `wct gate --tier fast`: 7/7 PASS
    - `wct gate --tier commit`: 17/17 PASS


## Ejecución mantenedor + discriminador (cierre)

- **PR #57 (docs/evidencia)**: verificada por el discriminador con números
  reproducidos en local (suite 1015 passed / 3 skipped / 396 subtests, corpus
  eval 299/299, gate commit 17/17); corrección de la ruta del webhook
  (`8cd433b`); merge `50c7809`.
- **PR #58 (bump protegido)**: bump + `uv lock` ejecutados por el mantenedor
  en su terminal (`92bc9f4`); bless por el mantenedor (`dc5026b`, reason
  "version bump v0.2.0-alpha.2 release"); fix de `__version__` por el
  discriminador (`cf6c7cc`); merge `7f9585e`.
- **Tag + prerelease**: tag anotado `v0.2.0-alpha.2` y prerelease en GitHub
  creados por el mantenedor (2026-08-18).
