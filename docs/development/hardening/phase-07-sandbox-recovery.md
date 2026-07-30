# Hardening Log — Fase 07: Sandbox de egreso y recuperación de proceso

Las reglas normativas y comandos viven en
[`maintainer-workflow.md`](../maintainer-workflow.md); este archivo registra el
plan y, durante la ejecución, las decisiones y evidencia de la fase.

## Identidad de la fase

| Campo | Valor |
|---|---|
| Fase | `07 — sandbox de egreso y recuperación de proceso` |
| Estado | `IN_PROGRESS` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `kimi/phase-7-sandbox-recovery` |
| Commit base | `96a8ccacec39e795564e16c38aacd0c8ffc29a70` (2026-07-28T18:06:28-05:00, confirmado con `git rev-parse HEAD` al abrir la fase; `main` sincronizado con `origin/main` por fast-forward) |
| Fecha de inicio | `2026-07-30` |
| PR | `<pendiente>` |
| Merge commit | `<pendiente>` |

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

- [ ] Todos los probes de aceptación de ADR-004 (tabla "Acceptance Criteria")
      pasan con tests deterministas: cada adaptador de red falla cerrado ante
      un host no permitido antes de abrir conexión; el arranque es fail-closed
      con `EGRESS_ALLOWED_HOSTS`; las herramientas locales no requieren egreso;
      no hay construcción de clientes HTTP fuera del borde de adaptadores; la
      auditoría de allowlist en arranque emite solo hostnames.
- [ ] `AppSettings` gana `egress_allowed_hosts` con derivación por defecto
      desde las base URLs configuradas más `api.telegram.org` cuando hay bot
      token, override explícito prioritario y validación fail-closed en
      `__post_init__`; `.env.example` documentado.
- [ ] Imagen de contenedor endurecida (usuario non-root, root filesystem
      read-only, capabilities eliminadas, `no-new-privileges`) con smoke de
      egreso documentado en `docs/runbook/hardened-local-deployment.md`.
- [ ] Ejercicio kill/restart automatizado contra PostgreSQL 16: terminación
      forzada del proceso en al menos tres puntos definidos (antes del commit
      de claim del outbox, después del commit y antes del I/O al proveedor, y
      durante I/O ambiguo) con reinicio y aserción de exactamente una entrega y
      cero efectos duplicados en calendario, outbox, event store, scheduler y
      estados de workflow; integrado al job `postgres-integration` de CI.
- [ ] La suite determinista completa pasa sin red externa y sin cambios en el
      corpus legacy de evals ni en su pin sha256.
- [ ] ADR-004 pasa a Status `Accepted` al cierre de la fase.

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
| 1 | A1 | Implementar allowlist de egreso (capa A de ADR-004) | `kimi/phase-7-a1-egress-allowlist` | `src/personal_assistant/adapters/outbound/**`, `src/personal_assistant/infrastructure/config.py`, `.env.example`, `tests/test_egress_allowlist.py` (nuevo), `tests/test_persistence_config.py` solo si la validación de arranque lo exige | `ninguna` | `PENDING` |
| 1 | A2 | Automatizar ejercicio kill/restart del ciclo de vida | `kimi/phase-7-a2-kill-restart` | `scripts/**`, `tests/test_process_recovery_postgres.py` (nuevo), `.github/workflows/**` solo para el job postgres-integration | `ninguna` | `PENDING` |
| 1 | A3 | Construir imagen de contenedor endurecida (capa B) | `kimi/phase-7-a3-container-image` | `Dockerfile` (nuevo), `deploy/**` o `scripts/**` para compose, `docs/runbook/hardened-local-deployment.md` (sección de egreso) | `ninguna` | `PENDING` |
| 2 | A4 | Integración: smoke de egreso en contenedor, ADR-004 a Accepted, actualizar README y runbooks | `kimi/phase-7-a4-integration-smoke` | `docs/adr/ADR-004-tool-execution-sandbox.md`, `README.md`, `docs/runbook/*.md`, `docs/development/production-readiness-v0.2.0-alpha.1.md` (addendum) | `A1, A2, A3 integrados` | `PENDING` |
| 2 | A5 | Hardening: corregir aislamiento de `test_public_artifacts`, regresiones reveladas por ola 1, gates completos | `kimi/phase-7-a5-hardening` | `tests/test_public_artifacts.py`, rutas reveladas por la ola 1 previa aprobación | `A1, A2, A3 integrados` | `PENDING` |

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
- [ ] La ola 2 parte del HEAD integrado: `<sha al commitear este registro>`.

## Ledger de cambios

| Tarea | Commit(s) | Resumen | Tests enfocados | Riesgo residual | Decisión |
|---|---|---|---|---|---|
| `A1` | `6f03ea1` | Módulo `adapters/outbound/egress.py` (allowlist exacta scheme+host, deny-by-default, error de dominio `EgressNotAllowedError`/GUARDRAIL_BLOCKED, derivación por defecto, validación fail-closed de arranque, auditoría de hostnames); cableado opcional en los 4 adaptadores de red; `AppSettings.egress_allowed_hosts` con derivación/override; `.env.example` documentado | `pytest -q tests/test_egress_allowlist.py` → 30 passed / 12 subtests; afectados (`test_llm_adapters`, `test_telegram_notifications`, `test_config_repr`, `test_persistence_config`, `test_architecture_boundaries`, `test_http_runtime`, `test_local_auth`) → 131 passed; suite hermética 687 passed con los 2 fallos preexistentes de `main` | El cableado del composition root (`bootstrap.py`/`http.py`/`worker.py`) que pasa la allowlist efectiva a los adaptadores y la emisión del log de auditoría de arranque quedan para ola 2 (rutas fuera de la autorización de A1); hasta entonces la enforcement de adaptador se activa solo cuando el llamador pasa la allowlist — el fail-closed de arranque en `AppSettings` sí queda activo | `ACCEPTED` |
| `A2` | `d8214d2` | `tests/test_process_recovery_postgres.py`: ejercicio kill/restart con procesos hijos spawn que se auto-matan (`os._exit(99)`) en 3 puntos instrumentados (antes del commit de claim, después del commit de sending antes del I/O, en medio del I/O de proveedor); reinicio con persistencia fresca y aserciones de exactamente-una-entrega, sweep a `uncertain` sin reenvío automático y reconciliación por operador | `pytest -q tests/test_process_recovery_postgres.py` con `TEST_POSTGRES_DSN` (PostgreSQL 16, `postgres:16-alpine`) → 3 passed; hermético → 3 skipped; suite completa con DSN → 728 passed / 3 skipped (solo falla el flake preexistente de `test_public_artifacts`) | No se requirió cambio en `.github/workflows/**`: el job `postgres-integration` ya ejecuta `uv run pytest -q` con DSN y recoge el archivo automáticamente (autorización de workflows queda sin uso, sin commit vacío) | `ACCEPTED` |
| `A3` | `8474522` | `Dockerfile` multi-stage (builder + `python:3.12-slim`, usuario non-root 10001, sin secretos, catálogos `prompts/`/`locales/` copiados a las rutas que el runtime resuelve); `.dockerignore`; `deploy/compose.yaml` endurecido (`read_only`, `tmpfs /tmp`, `cap_drop ALL`, `no-new-privileges`, publicación loopback); runbook gana secciones "Container Profile" y "Egress Verification" | Build real: `docker build -t personal-assistant:0.2.0-alpha.1 .` OK; smoke con banderas endurecidas: `id` → `uid=10001(assistant)`; `touch /x` → "Read-only file system"; inspect → `CapDrop=[ALL]`, `no-new-privileges:true`, `ReadonlyRootfs=true`, `Health=healthy`; `/livez` responde `{"status":"ok"}` | Primer arranque falló por catálogos `prompts/`/`locales/` ausentes en la imagen (el runtime los resuelve relativo a la instalación); corregido copiándolos en el Dockerfile y ajustando `.dockerignore`. El smoke de egreso desde dentro del contenedor contra host no permitido queda para A4 | `ACCEPTED` |
| `A4` | `<pendiente>` | `<pendiente>` | `<pendiente>` | `<pendiente>` | `PENDING` |
| `A5` | `<pendiente>` | `<pendiente>` | `<pendiente>` | `<pendiente>` | `PENDING` |

## Revisión de diff y staging

- [ ] `git status --short` revisado.
- [ ] `git diff --stat` revisado.
- [ ] `git diff --check` pasó.
- [ ] `git diff --` completo revisado, incluidos archivos nuevos.
- [ ] Las rutas staged fueron enumeradas; no se usó `git add .`, `git add -A`
      ni `git commit -am`.
- [ ] `git diff --cached --stat` revisado.
- [ ] `git diff --cached --check` pasó.
- [ ] `git diff --cached --` completo revisado.

**Rutas staged:**

```text
<una ruta por línea>
```

**Mensaje Conventional Commit previsto:**

```text
<type>(<scope>): <descripción>
```

## Evidencia de gates

| Gate | Comando exacto | Resultado / código | Fecha | Evidencia o nota |
|---|---|---|---|---|
| Test enfocado A1 | `uv run pytest -q tests/test_egress_allowlist.py` | `PENDING` | | |
| Test enfocado A2 | `TEST_POSTGRES_DSN=... uv run pytest -q tests/test_process_recovery_postgres.py` | `PENDING` | | Requiere PostgreSQL 16 |
| Test enfocado A3 | build de imagen + smoke de egreso documentado | `PENDING` | | |
| Lock | `uv lock --check` | `PENDING` | | |
| Sync | `uv sync --frozen --all-extras --group dev` | `PENDING` | | |
| Ruff | `uv run ruff check .` | `PENDING` | | |
| Mypy | `uv run mypy src` | `PENDING` | | |
| Pytest | `uv run pytest -q` | `PENDING` | | |
| Coverage | `uv run coverage run --source=src/personal_assistant -m pytest` | `PENDING` | | |
| Coverage XML | `uv run coverage xml` | `PENDING` | | |
| Coverage total | `uv run coverage report --fail-under=85` | `PENDING` | | |
| Diff coverage | `uv run diff-cover coverage.xml --compare-branch origin/main --fail-under=90` | `PENDING` | | |
| Compilación | `uv run python -m compileall -q src` | `PENDING` | | |
| Build | `uv build` | `PENDING` | | |
| Dependencias | `uv run pip-audit` | `PENDING` | | |
| Pre-commit config | `uv run pre-commit validate-config` | `PENDING` | | |
| Pre-commit | `uv run pre-commit run --all-files` | `PENDING` | | |
| Whitespace | `git diff --check` | `PENDING` | | |
| Rollback | `<prueba segura>` | `PENDING` | | |

Los gates omitidos requieren justificación y riesgo residual:

```text
<gate, razón, impacto, aprobación>
```

## Revisión de secretos y datos sensibles

- [ ] No hay `.env` real, credenciales, tokens, claves, contraseñas ni URLs con
      autenticación.
- [ ] `.env.example` contiene únicamente placeholders o valores no sensibles.
- [ ] No hay IDs, conversaciones, documentos, capturas, trazas o logs reales.
- [ ] La imagen de contenedor y su build context no contienen secretos; los
      secretos llegan solo por variables de entorno en arranque.
- [ ] La auditoría de allowlist en arranque emite solo hostnames.
- [ ] El guard de nombres staged pasó.
- [ ] Los hooks versionados de pre-commit pasaron sobre las rutas staged.
- [ ] Si existe gitleaks, la evidencia referencia su check CI versionado; no se
      usó un comando local improvisado.
- [ ] Ningún secreto apareció en commits previos de la fase.

**Evidencia y hallazgos, siempre redactados:**

```text
<resultado sin incluir el valor sensible>
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

**Resultado del ensayo seguro:** `PENDING`

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
| `R-04` | `test_public_artifacts` falla en corrida completa pero pasa aislado (observado 2026-07-30, suite hermética local): posible dependencia de orden o estado compartido entre tests. | `M` | `L` | A5 investiga y corrige el aislamiento; si resulta `REVIEW_ONLY`, se registra evidencia y riesgo residual. | `Yosoyepa` | `OPEN` |
| `R-05` | Cerrar GAP #6 y GAP #7 no autoriza GA: el scorecard solo cambia con una re-auditoría completa. | `H` | `L` | A4 redacta el addendum en `production-readiness-v0.2.0-alpha.1.md` sin modificar el scorecard histórico; la re-auditoría queda como fase posterior. | `Yosoyepa` | `ACCEPTED` |

## Definition of Done

### Tareas

- [ ] Objetivos y aceptación cumplidos.
- [ ] Invariantes preservados.
- [ ] Diffs de trabajo y staged revisados.
- [ ] Staging explícito.
- [ ] Tests enfocados aprobados.
- [ ] Sin secretos ni artefactos temporales.
- [ ] Commits convencionales y reversibles.
- [ ] Riesgos residuales registrados.

### Fase

- [ ] Los cinco roles 3 + 2 entregaron implementación o revisión.
- [ ] Roles `REVIEW_ONLY` registrados sin commits vacíos.
- [ ] Conflictos devueltos al agente afectado y revalidados antes de reintegrar.
- [ ] Tareas aceptadas integradas.
- [ ] Gates completos aprobados.
- [ ] Revisión de secretos aprobada.
- [ ] Sin bloqueos abiertos.
- [ ] Rollback verificable.
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
