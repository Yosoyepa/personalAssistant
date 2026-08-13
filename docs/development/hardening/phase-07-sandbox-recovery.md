# Hardening Log — Fase 07: Sandbox de egreso y recuperación de proceso

Las reglas normativas y comandos viven en
[`maintainer-workflow.md`](../maintainer-workflow.md); este archivo registra el
plan y, durante la ejecución, las decisiones y evidencia de la fase.

## Identidad de la fase

| Campo | Valor |
|---|---|
| Fase | `07 — sandbox de egreso y recuperación de proceso` |
| Estado | `MERGED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `kimi/phase-7-sandbox-recovery` |
| Commit base | `96a8ccacec39e795564e16c38aacd0c8ffc29a70` (2026-07-28T18:06:28-05:00, confirmado con `git rev-parse HEAD` al abrir la fase; `main` sincronizado con `origin/main` por fast-forward) |
| Fecha de inicio | `2026-07-30` |
| PR | `#15` |
| Merge commit | `739ccb9d212a8d2b983f88e9b10b0fdcdddcf55d` (2026-07-30) |

## Contexto y justificación

La auditoría `docs/development/production-readiness-v0.2.0-alpha.1.md` mantiene
el scorecard en **4 PASS / 8 GAP** y GA no autorizada. El roadmap de 30 días
programa para días 8–14 la contenedorización con secretos solo en el borde de
adaptadores (GAP #6) y para días 15–21 la automatización del ejercicio
kill/restart (GAP #7). La fase 06 dejó ADR-004
(`docs/adr/ADR-004-tool-execution-sandbox.md`, Status: Proposed) con el diseño
de dos capas aprobado y sin cambio de comportamiento.

GAP #3 (compaction) permanece fuera: está bloqueado por una decisión de diseño
de historial conversacional que requiere un ADR propio (ver `R-03`).

## Objetivo y límites

**Objetivo:** implementar la capa A de ADR-004 (allowlist de egreso
deny-by-default, fail-closed, en el borde de adaptadores), la capa B (imagen de
contenedor endurecida como unidad de despliegue única) y un ejercicio
automatizado kill/restart que demuestre recuperación del ciclo de vida completo
del proceso sin efectos duplicados, cerrando GAP #6 y GAP #7 de la auditoría o
dejando evidencia suficiente para su re-evaluación.

**Criterios de aceptación:**

- [x] Todos los probes de aceptación de ADR-004 (tabla "Acceptance Criteria")
      pasan con tests deterministas: cada adaptador de red falla cerrado ante
      un host no permitido antes de abrir conexión; el arranque es fail-closed
      con `EGRESS_ALLOWED_HOSTS`; las herramientas locales no requieren egreso;
      no hay construcción de clientes HTTP fuera del borde de adaptadores; la
      auditoría de allowlist en arranque emite solo hostnames.
- [x] `AppSettings` gana `egress_allowed_hosts` con derivación por defecto
      desde las base URLs configuradas más `api.telegram.org` cuando hay bot
      token, override explícito prioritario y validación fail-closed en
      `__post_init__`; `.env.example` documentado.
- [x] Imagen de contenedor endurecida (usuario non-root, root filesystem
      read-only, capabilities eliminadas, `no-new-privileges`) con smoke de
      egreso documentado en `docs/runbook/hardened-local-deployment.md`.
- [x] Ejercicio kill/restart automatizado contra PostgreSQL 16: terminación
      forzada del proceso en al menos tres puntos definidos (antes del commit
      de claim del outbox, después del commit y antes del I/O al proveedor, y
      durante I/O ambiguo) con reinicio y aserción de exactamente una entrega y
      cero efectos duplicados en calendario, outbox, event store, scheduler y
      estados de workflow; integrado al job `postgres-integration` de CI.
- [x] La suite determinista completa pasa sin red externa y sin cambios en el
      corpus legacy de evals ni en su pin sha256.
- [x] ADR-004 pasa a Status `Accepted` al cierre de la fase.

**Fuera de alcance:**

- Compaction, historial conversacional multi-turno o diseño de contexto (GAP
  #3; requiere ADR previo).
- Telemetría de hit-rate de guardrails (GAP #9), crecimiento de evals a ≥50 por
  modo de fallo (GAP #10) y juez LLM calibrado (GAP #11).
- Prune de retención en operación real (GAP #12 sigue abierto).
- Sync de calendario externo, OAuth, memoria vectorial, rutas MCP/A2A activas.
- Aislamiento multi-tenant o claims de SaaS; la alpha sigue siendo de un solo
  operador.

**Invariantes que no pueden degradarse:**

- Autoridad de tenant derivada del `Principal` autenticado; nunca de texto,
  bodies, salida LLM o documentos.
- Aprobaciones P3/P5 e idempotencia de efectos; outbox canónico y scheduler
  como vista espejo.
- `repr=False` en secretos y sanitizador de trazas; ningún secreto en la
  imagen, el build context o la auditoría de allowlist.
- Contrato cerrado de `/admin/metrics`; corpus legacy de evals y pin sha256
  intactos.
- Una sola unidad desplegable (ADR-001) y fronteras hexagonales (ADR-003): la
  enforcement vive en adaptadores y composition root.
- Desarrollo local en proceso sigue soportado con la allowlist a nivel de
  código, sin requerir runtime de contenedores.

## Plan de olas 3 + 2

| Ola | Slot | Objetivo | Rama / worktree | Rutas autorizadas | Dependencias | Estado |
|---|---|---|---|---|---|---|
| 1 | A1 | Implementar allowlist de egreso (capa A de ADR-004) | `kimi/phase-7-a1-egress-allowlist` | `src/personal_assistant/adapters/outbound/**`, `src/personal_assistant/infrastructure/config.py`, `.env.example`, `tests/test_egress_allowlist.py` (nuevo), `tests/test_persistence_config.py` solo si la validación de arranque lo exige | `ninguna` | `ACCEPTED` (6f03ea1, integrado 3d4a03e) |
| 1 | A2 | Automatizar ejercicio kill/restart del ciclo de vida | `kimi/phase-7-a2-kill-restart` | `scripts/**`, `tests/test_process_recovery_postgres.py` (nuevo), `.github/workflows/**` solo para el job postgres-integration | `ninguna` | `ACCEPTED` (d8214d2, integrado 8ddf6a4) |
| 1 | A3 | Construir imagen de contenedor endurecida (capa B) | `kimi/phase-7-a3-container-image` | `Dockerfile` (nuevo), `deploy/**` o `scripts/**` para compose, `docs/runbook/hardened-local-deployment.md` (sección de egreso) | `ninguna` | `ACCEPTED` (8474522, integrado 3c58152) |
| 2 | A4 | Integración: smoke de egreso en contenedor, ADR-004 a Accepted, actualizar README y runbooks | `kimi/phase-7-a4-integration-smoke` | `docs/adr/ADR-004-tool-execution-sandbox.md`, `README.md`, `docs/runbook/*.md`, `docs/development/production-readiness-v0.2.0-alpha.1.md` (addendum) | `A1, A2, A3 integrados` | `ACCEPTED` (946413b, integrado c84425c) |
| 2 | A5 | Hardening: corregir aislamiento de `test_public_artifacts`, regresiones reveladas por ola 1, gates completos | `kimi/phase-7-a5-hardening` | `tests/test_public_artifacts.py`, rutas reveladas por la ola 1 previa aprobación | `A1, A2, A3 integrados` | `ACCEPTED` (f377e35, integrado 1b2433f) |

Los cinco roles están reservados. Un rol sin mutación termina `REVIEW_ONLY`,
entrega evidencia y no crea un commit vacío.

Notas de solapamiento:

- A1 es el único dueño de `infrastructure/config.py` y `.env.example` en ola 1.
- A2 y A3 comparten `scripts/**`: A2 es dueño de los scripts de ejercicio
  kill/restart; A3 de los scripts de build/compose. Nombres distintos, sin
  edición simultánea del mismo archivo.
- A2 puede tocar `.github/workflows/**` únicamente para cablear su ejercicio al
  job postgres-integration existente.
- Cualquier solapamiento imprevisto se resuelve devolviendo el conflicto al
  agente afectado según la sección 2 del workflow.

### Apertura de la fase (registro)

- 2026-07-30: fase abierta desde checkout principal limpio; `main` en
  `96a8cca` sincronizado con `origin/main` por fast-forward. Decisión del
  mantenedor: las ramas de esta fase usan el prefijo `kimi/` (autoría del
  agente) en lugar del prefijo `codex/` del workflow; el resto de invariantes
  se conserva.
- Rama de fase `kimi/phase-7-sandbox-recovery` creada desde `main`; plan
  integrado en commit `b87719a`
  `docs(phase-7): define sandbox and process-recovery phase plan` con staging
  explícito de una sola ruta y revisión de diff staged (`--stat`, `--check`,
  sin secretos, solo placeholders).
- Worktrees de ola 1 creados desde el HEAD de la rama de fase (`b87719a`),
  cada uno verificado con `branch --show-current`, `status --short --branch`
  y `rev-parse HEAD`:
  - `personalAssistant-worktrees/phase-7-a1-egress-allowlist` →
    `kimi/phase-7-a1-egress-allowlist`
  - `personalAssistant-worktrees/phase-7-a2-kill-restart` →
    `kimi/phase-7-a2-kill-restart`
  - `personalAssistant-worktrees/phase-7-a3-container-image` →
    `kimi/phase-7-a3-container-image`

### Checkpoint entre olas

- [x] Los tres roles de ola 1 entregaron diff o evidencia `REVIEW_ONLY`,
      validaciones y riesgos.
- [x] El mantenedor revisó los diffs completos.
- [x] Los commits aceptados se integraron en la rama de fase (merges `3d4a03e`,
      `8ddf6a4`, `3c58152`; sin conflictos).
- [x] No se crearon commits vacíos para roles `REVIEW_ONLY`.
- [x] Todo conflicto volvió al agente afectado; este integró la rama de fase,
      resolvió, revalidó y entregó una nueva revisión. (No hubo conflictos.)
- [x] Los gates de checkpoint pasaron: ruff pass; mypy pass (116 archivos);
      suite completa contra PostgreSQL 16 (`personal-assistant-pg16-phase7`,
      `postgres:16-alpine`): 758 passed / 3 skipped / 48 subtests / 1 failed.
      El único fallo es el problema de aislamiento preexistente de
      `test_public_artifacts` (pasa aislado, falla en corrida completa),
      registrado como `R-04` y asignado a A5; ningún cambio de la ola 1 toca
      artefactos públicos.
- [x] La ola 2 partió del HEAD integrado `dc186e5` (ramas A4 y A5 creadas
      desde ese commit); ambas tareas se integraron con merges `--no-ff`
      `c84425c` (A4) y `1b2433f` (A5), sin conflictos.

## Ledger de cambios

| Tarea | Commit(s) | Resumen | Tests enfocados | Riesgo residual | Decisión |
|---|---|---|---|---|---|
| `A1` | `6f03ea1` | Módulo `adapters/outbound/egress.py` (allowlist exacta scheme+host, deny-by-default, error de dominio `EgressNotAllowedError`/GUARDRAIL_BLOCKED, derivación por defecto, validación fail-closed de arranque, auditoría de hostnames); cableado opcional en los 4 adaptadores de red; `AppSettings.egress_allowed_hosts` con derivación/override; `.env.example` documentado | `pytest -q tests/test_egress_allowlist.py` → 30 passed / 12 subtests; afectados (`test_llm_adapters`, `test_telegram_notifications`, `test_config_repr`, `test_persistence_config`, `test_architecture_boundaries`, `test_http_runtime`, `test_local_auth`) → 131 passed; suite hermética 687 passed con los 2 fallos preexistentes de `main` | El cableado del composition root (`bootstrap.py`/`http.py`/`worker.py`) que pasa la allowlist efectiva a los adaptadores y la emisión del log de auditoría de arranque quedan para ola 2 (rutas fuera de la autorización de A1); hasta entonces la enforcement de adaptador se activa solo cuando el llamador pasa la allowlist — el fail-closed de arranque en `AppSettings` sí queda activo | `ACCEPTED` |
| `A2` | `d8214d2` | `tests/test_process_recovery_postgres.py`: ejercicio kill/restart con procesos hijos spawn que se auto-matan (`os._exit(99)`) en 3 puntos instrumentados (antes del commit de claim, después del commit de sending antes del I/O, en medio del I/O de proveedor); reinicio con persistencia fresca y aserciones de exactamente-una-entrega, sweep a `uncertain` sin reenvío automático y reconciliación por operador | `pytest -q tests/test_process_recovery_postgres.py` con `TEST_POSTGRES_DSN` (PostgreSQL 16, `postgres:16-alpine`) → 3 passed; hermético → 3 skipped; suite completa con DSN → 728 passed / 3 skipped (solo falla el flake preexistente de `test_public_artifacts`) | No se requirió cambio en `.github/workflows/**`: el job `postgres-integration` ya ejecuta `uv run pytest -q` con DSN y recoge el archivo automáticamente (autorización de workflows queda sin uso, sin commit vacío) | `ACCEPTED` |
| `A3` | `8474522` | `Dockerfile` multi-stage (builder + `python:3.12-slim`, usuario non-root 10001, sin secretos, catálogos `prompts/`/`locales/` copiados a las rutas que el runtime resuelve); `.dockerignore`; `deploy/compose.yaml` endurecido (`read_only`, `tmpfs /tmp`, `cap_drop ALL`, `no-new-privileges`, publicación loopback); runbook gana secciones "Container Profile" y "Egress Verification" | Build real: `docker build -t personal-assistant:0.2.0-alpha.1 .` OK; smoke con banderas endurecidas: `id` → `uid=10001(assistant)`; `touch /x` → "Read-only file system"; inspect → `CapDrop=[ALL]`, `no-new-privileges:true`, `ReadonlyRootfs=true`, `Health=healthy`; `/livez` responde `{"status":"ok"}` | Primer arranque falló por catálogos `prompts/`/`locales/` ausentes en la imagen (el runtime los resuelve relativo a la instalación); corregido copiándolos en el Dockerfile y ajustando `.dockerignore`. El smoke de egreso desde dentro del contenedor contra host no permitido queda para A4 | `ACCEPTED` |
| `A4` | `946413b` | Cableado del composition root: `bootstrap.py` gana `build_egress_allowlist`/`egress_audit_record`/`log_egress_audit` (logger `personal_assistant.egress` con nivel INFO garantizado bajo uvicorn); los 4 builders de adaptadores de red reciben la allowlist efectiva en `http.py` (2 sitios) y `worker.py` (1 sitio); auditoría de arranque emite solo hostnames; ADR-004 pasa a Status `Accepted`; README corrige límites (migraciones ya existen); addendum de fase en `production-readiness-v0.2.0-alpha.1.md` sin tocar el scorecard histórico | `pytest -q tests/test_egress_composition.py` (nuevo) → 13 passed; fakes de `test_http_runtime` actualizados con kwarg `egress_allowlist`; batería afectada → 141 passed; ruff/mypy limpios; smoke contenedor: log `egress allowlist hosts: api.minimax.io` + `Health=healthy`; override `EGRESS_ALLOWED_HOSTS="api.telegram.org"` con TTS habilitado → exit 1 con `ValueError` explícito (fail-closed) | Ninguno adicional: la derivación por defecto mantiene el comportamiento de despliegues actuales; el override inválido aborta el arranque con mensaje accionable | `ACCEPTED` |
| `A5` | `f377e35` | Causa raíz del flake de `test_public_artifacts` (R-04): Git Bash exporta config por `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_*`/`GIT_CONFIG_VALUE_*` con `GIT_CONFIG_VALUE_0` vacío; tras ciclos `patch.dict(os.environ, ..., clear=True)` Windows elimina las entradas vacías del bloque de entorno del proceso y el git hijo aborta con exit 128 (`fatal: unable to parse command-line config`). `_tracked_paths()` ahora pasa un `env` explícito que excluye `GIT_CONFIG_*` (innecesario para plumbing local de solo lectura) + test de regresión que fija el saneamiento | Reproductor mínimo (`test_http_runtime` + `test_public_artifacts`) fallaba antes y pasa después (44 passed); suite hermética → 689 passed (solo falla el corpus que exige DSN, esperado); suite completa con PostgreSQL 16 → 760 passed / 3 skipped / 48 subtests; ruff/mypy limpios | El mismo problema ambiental afecta a cualquier git lanzado vía Python desde Git Bash con valores vacíos reescritos (observado en `pre-commit run`); se mitiga ejecutando esos comandos con `GIT_CONFIG_*` desactivado y queda documentado en Evidencia de gates | `ACCEPTED` |

## Revisión de diff y staging

- [x] `git status --short` revisado.
- [x] `git diff --stat` revisado.
- [x] `git diff --check` pasó.
- [x] `git diff --` completo revisado, incluidos archivos nuevos.
- [x] Las rutas staged fueron enumeradas; no se usó `git add .`, `git add -A`
      ni `git commit -am`.
- [x] `git diff --cached --stat` revisado.
- [x] `git diff --cached --check` pasó.
- [x] `git diff --cached --` completo revisado.

**Rutas staged:**

```text
docs/development/hardening/phase-07-sandbox-recovery.md
```

**Mensaje Conventional Commit previsto:**

```text
docs(phase-7): record wave-2 integration, gates evidence, secrets review and rollback rehearsal
```

## Evidencia de gates

| Gate | Comando exacto | Resultado / código | Fecha | Evidencia o nota |
|---|---|---|---|---|
| Test enfocado A1 | `uv run pytest -q tests/test_egress_allowlist.py` | `PASS` (30 passed / 12 subtests) | 2026-07-30 | Ver ledger A1 |
| Test enfocado A2 | `TEST_POSTGRES_DSN=... uv run pytest -q tests/test_process_recovery_postgres.py` | `PASS` (3 passed) | 2026-07-30 | PostgreSQL 16 (`postgres:16-alpine`, contenedor `personal-assistant-pg16-phase7`) |
| Test enfocado A3 | build de imagen + smoke endurecido | `PASS` | 2026-07-30 | `uid=10001`, rootfs read-only, `CapDrop=[ALL]`, `Health=healthy`, `/livez` OK; ver ledger A3 |
| Test enfocado A4 | `uv run pytest -q tests/test_egress_composition.py` | `PASS` (13 passed) | 2026-07-30 | Más smoke de contenedor fail-closed con override inválido (ledger A4) |
| Lock | `uv lock --check` | `PASS` | 2026-07-30 | `Resolved 76 packages` |
| Sync | `uv sync --frozen --all-extras --group dev` | `PASS` | 2026-07-30 | `Checked 75 packages` |
| Ruff | `uv run ruff check .` | `PASS` | 2026-07-30 | `All checks passed!` |
| Mypy | `uv run mypy src` | `PASS` | 2026-07-30 | `Success: no issues found in 116 source files` |
| Pytest | `TEST_POSTGRES_DSN=... uv run pytest -q` | `PASS` (770 passed / 3 skipped / 48 subtests, 0 failed) | 2026-07-30 | Rama de fase integrada (HEAD `1b2433f`); los 3 skipped son opt-outs sin DSN alterno |
| Coverage | `uv run coverage run --source=src/personal_assistant -m pytest` | `PASS` (770 passed) | 2026-07-30 | |
| Coverage XML | `uv run coverage xml` | `PASS` | 2026-07-30 | `coverage.xml` generado |
| Coverage total | `uv run coverage report --fail-under=85` | `PASS` (TOTAL 92%) | 2026-07-30 | |
| Diff coverage | `uv run diff-cover coverage.xml --compare-branch origin/main --fail-under=90` | `PASS` (96%, 4 líneas missing) | 2026-07-30 | `origin/main` fetch previo |
| Compilación | `uv run python -m compileall -q src` | `PASS` | 2026-07-30 | |
| Build | `uv build` | `PASS` | 2026-07-30 | `dist/personal_assistant-0.2.0a1.{tar.gz,whl}` |
| Dependencias | `uv run pip-audit` | `PASS` | 2026-07-30 | `No known vulnerabilities found` (paquete local no auditable en PyPI, esperado) |
| Pre-commit config | `uv run pre-commit validate-config` | `PASS` | 2026-07-30 | |
| Pre-commit | `uv run pre-commit run --all-files` | `PASS` | 2026-07-30 | Requiere `env -u GIT_CONFIG_*` en Git Bash Windows: el mismo fenómeno de R-04 (valor vacío eliminado al re-serializar el entorno vía Python) rompe `git rev-parse` de los hooks; con el entorno saneado los 5 hooks pasan. Limitación ambiental local; en CI Linux no aplica |
| Whitespace | `git diff --check` y `git diff origin/main...HEAD --check` | `PASS` | 2026-07-30 | Sin salida |
| Rollback | ensayo en worktree temporal: merge `--no-ff` de la fase sobre `main` + `git revert -m 1 <merge>` | `PASS` | 2026-07-30 | Árbol revertido idéntico a `main` (`git diff main --quiet`); smoke post-rollback 7 passed; worktree y rama temporales eliminados |

Los gates omitidos requieren justificación y riesgo residual:

```text
Ningún gate omitido. El gate Pre-commit requirió desactivar las variables
ambientales GIT_CONFIG_* de Git Bash (fenómeno documentado en R-04/A5); es
una limitación del entorno local Windows, no del repositorio: los hooks
pasan con el entorno saneado y en CI Linux el problema no existe.
```

## Revisión de secretos y datos sensibles

- [x] No hay `.env` real, credenciales, tokens, claves, contraseñas ni URLs con
      autenticación.
- [x] `.env.example` contiene únicamente placeholders o valores no sensibles.
- [x] No hay IDs, conversaciones, documentos, capturas, trazas o logs reales.
- [x] La imagen de contenedor y su build context no contienen secretos; los
      secretos llegan solo por variables de entorno en arranque.
- [x] La auditoría de allowlist en arranque emite solo hostnames.
- [x] El guard de nombres staged pasó.
- [x] Los hooks versionados de pre-commit pasaron sobre las rutas staged
      (incluidos `detect private key` y `detect aws credentials`).
- [x] Si existe gitleaks, la evidencia referencia su check CI versionado; no se
      usó un comando local improvisado. (No hay gitleaks versionado; el job
      `security` de CI corre `pip-audit`, ejecutado también como gate local.)
- [x] Ningún secreto apareció en commits previos de la fase.

**Evidencia y hallazgos, siempre redactados:**

```text
Escaneo de líneas añadidas del diff origin/main...HEAD con patrones
api_key/secret/token/password/bearer/PEM: todas las coincidencias son prosa
documental o placeholders. La única credencial literal es el DSN de prueba
postgresql://assistant_ci:<password-de-prueba>@127.0.0.1:5432/assistant_ci,
idéntico al ya versionado en .github/workflows/ci.yml (credencial desechable
de CI, no un secreto real); el test kill/restart lo lee solo desde
TEST_POSTGRES_DSN. El test público anti-secretos pasa en suite completa.
```

## Plan de rollback

| Elemento | Definición |
|---|---|
| Disparador | Un proveedor habilitado queda bloqueado por la allowlist en despliegue real, o el ejercicio kill/restart revela duplicación de efectos tras integración. |
| Punto de rollback | Merge commit de la fase (única PR). |
| Comando previsto | `git revert -m 1 <merge-commit>` desde rama `kimi/rollback-phase-7-sandbox-recovery`. |
| Impacto en datos | Ninguno previsto: la fase no introduce migraciones de esquema; si A2 revelara la necesidad de una, la fase se bloquea antes del merge hasta tener migración reversible. |
| Configuración o flags | La allowlist deriva por defecto de las base URLs ya configuradas, por lo que los despliegues actuales no cambian de comportamiento salvo override explícito de `EGRESS_ALLOWED_HOSTS`; el contenedor es un perfil de despliegue adicional y el desarrollo en proceso sigue disponible. |
| Gate posterior | `uv run pytest -q` hermético + job postgres-integration verde tras el revert. |
| Responsable | `Yosoyepa` |

**Resultado del ensayo seguro:** `PASS` (2026-07-30) — en worktree temporal
desde `main`: merge `--no-ff` de la rama de fase, `git revert -m 1 <merge>`
sin conflictos, árbol resultante idéntico a `main` (`git diff main --quiet`)
y smoke post-rollback verde (7 passed). Worktree y rama temporales
eliminados; el comando exacto del plan queda validado de extremo a extremo.

## Ciclos de bloqueo

| Huella del bloqueo | Ciclo | Evidencia | Corrección segura probada | Resultado | Fecha |
|---|---:|---|---|---|---|
| `<huella>` | 1 | `<evidencia>` | `<acción>` | `PERSISTS/RESOLVED` | `<fecha>` |

## Riesgos y decisiones

| ID | Riesgo o decisión | Probabilidad | Impacto | Mitigación | Responsable | Estado |
|---|---|---|---|---|---|---|
| `R-01` | La derivación por defecto de `EGRESS_ALLOWED_HOSTS` podría no cubrir un host de proveedor configurado de forma no estándar y bloquear el arranque. | `L` | `M` | Validación fail-closed con mensaje explícito + override documentado en `.env.example` y runbook. | `Yosoyepa` | `OPEN` |
| `R-02` | El ejercicio kill/restart con `SIGKILL` real puede ser inestable en CI Windows; el job postgres-integration corre en Linux, pero el desarrollo local es Windows. | `M` | `M` | Implementar el harness multiplataforma (`terminate`/`kill` por señal de proceso, no shell-specific); si Windows no es reproducible, el gate canonico es el job Linux de CI y se registra la limitación. | `Yosoyepa` | `OPEN` |
| `R-03` | GAP #3 (compaction) sigue bloqueado: no existe decisión sobre historial conversacional. | `H` | `M` | Tratar como decisión de producto/arquitectura; abrir ADR propio fuera de esta fase antes de cualquier pipeline de compaction. | `Yosoyepa` | `OPEN` |
| `R-04` | `test_public_artifacts` falla en corrida completa pero pasa aislado (observado 2026-07-30, suite hermética local): posible dependencia de orden o estado compartido entre tests. | `M` | `L` | A5 investiga y corrige el aislamiento; si resulta `REVIEW_ONLY`, se registra evidencia y riesgo residual. | `Yosoyepa` | `RESOLVED` (2026-07-30, A5 `f377e35`: causa raíz ambiental GIT_CONFIG_* + saneamiento de env en `_tracked_paths` + test de regresión) |
| `R-05` | Cerrar GAP #6 y GAP #7 no autoriza GA: el scorecard solo cambia con una re-auditoría completa. | `H` | `L` | A4 redacta el addendum en `production-readiness-v0.2.0-alpha.1.md` sin modificar el scorecard histórico; la re-auditoría queda como fase posterior. | `Yosoyepa` | `ACCEPTED` |

## Definition of Done

### Tareas

- [x] Objetivos y aceptación cumplidos.
- [x] Invariantes preservados.
- [x] Diffs de trabajo y staged revisados.
- [x] Staging explícito.
- [x] Tests enfocados aprobados.
- [x] Sin secretos ni artefactos temporales.
- [x] Commits convencionales y reversibles.
- [x] Riesgos residuales registrados.

### Fase

- [x] Los cinco roles 3 + 2 entregaron implementación o revisión.
- [x] Roles `REVIEW_ONLY` registrados sin commits vacíos. (No hubo roles
      `REVIEW_ONLY`; la autorización de workflows de A2 quedó sin uso por
      innecesaria, sin commit vacío.)
- [x] Conflictos devueltos al agente afectado y revalidados antes de
      reintegrar. (No hubo conflictos en ninguna integración.)
- [x] Tareas aceptadas integradas.
- [x] Gates completos aprobados.
- [x] Revisión de secretos aprobada.
- [x] Sin bloqueos abiertos. (La tabla de ciclos de bloqueo quedó sin
      entradas: ningún bloqueo requirió ciclo de corrección.)
- [x] Rollback verificable.
- [ ] PR única de fase revisada y con CI verde.
- [ ] Método de integración: merge commit.
- [ ] Worktrees y ramas temporales limpiados de forma segura.

## Aprobaciones

| Decisión | Responsable | Fecha | Evidencia / comentario |
|---|---|---|---|
| Autorizar staging | `<nombre>` | `<fecha>` | `<referencia>` |
| Autorizar commit | `<nombre>` | `<fecha>` | `<referencia>` |
| Autorizar PR | `<nombre>` | `<fecha>` | `<referencia>` |
| Autorizar merge commit | `<nombre>` | `<fecha>` | `<referencia>` |
| Cerrar objetivo | `<nombre>` | `<fecha>` | `<referencia>` |
