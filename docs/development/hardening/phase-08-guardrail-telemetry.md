# Hardening Log — Fase 08: Guardrails de contenido/citas y telemetría de hit-rate

Las reglas normativas y comandos viven en
[`maintainer-workflow.md`](../maintainer-workflow.md); este archivo registra el
plan y, durante la ejecución, las decisiones y evidencia de la fase.

## Identidad de la fase

| Campo | Valor |
|---|---|
| Fase | `08 — guardrails de contenido/citas y telemetría de hit-rate` |
| Estado | `PLAN_PENDING_REVIEW` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `kimi/phase-8-guardrail-telemetry` |
| Commit base | `739ccb9d212a8d2b983f88e9b10b0fdcdddcf55d` (merge commit de la PR #15, fase 07; `main` sincronizado con `origin/main`) |
| Fecha de inicio | `<pendiente — se registra al aprobar el plan>` |
| PR | `<pendiente>` |
| Merge commit | `<pendiente>` |

## Contexto y justificación

La auditoría `docs/development/production-readiness-v0.2.0-alpha.1.md` mantiene
el scorecard en **4 PASS / 8 GAP** y GA no autorizada. El GAP #9
(Input/output guardrails) dice textualmente: *"PII and prompt-injection
defenses plus output schemas exist; content-policy/citation guardrails and
production hit-rate metrics do not."* El roadmap de 30 días la programó para
días 15–21 junto al ejercicio kill/restart, pero la fase 07 la dejó
explícitamente fuera de alcance; es la continuación cronológica natural.

Estado actual verificado en código (commit base):

- `domain/common/guardrails.py` cubre solo `PROMPT_INJECTION` y `PII` con
  regexes locales; no existe categoría de política de contenido ni escaneo del
  lado de salida (las respuestas del asistente no se escanean).
- `DocumentService.summarize` emite citas triviales (`filename:1`) sin modelo
  formal ni verificación de grounding.
- El tipo de traza `guardrail.checked` existe desde la fase 06
  (`application/dto/tracing.py`, campo requerido `validation`), pero **ningún
  punto del código lo emite**: no hay telemetría de hit-rate en producción.

GAP #3 (compaction) permanece fuera: bloqueado por decisión de diseño de
historial conversacional (ver `R-03` de la fase 07). GAP #10/#11 (evals ≥50 y
juez calibrado) quedan para la fase siguiente según el roadmap (días 22–30).

## Objetivo y límites

**Objetivo:** cerrar la evidencia del GAP #9 definiendo y enforceando una
política de contenido determinista y local para los workflows habilitados
(recordatorios vía Telegram y resumen de documentos), formalizando y
verificando las citas de documentos, y emitiendo telemetría de hit-rate de
guardrails en producción a través de las trazas y la superficie admin
existentes.

**Criterios de aceptación:**

- [ ] Política de contenido ratificada en `docs/policy/content-policy.md`:
      reglas explícitas, cada una con ID estable, severidad (flag/block) y al
      menos un caso de test positivo y uno negativo; enforcement determinista y
      local (sin APIs externas de moderación).
- [ ] `domain/common/guardrails.py` gana la categoría de política de contenido
      y una API de escaneo de salida (`scan_output`/`assert_output_safe`)
      cableada en los límites de respuesta (reply de recordatorios, resumen de
      documentos, runtime local); un bloqueo produce `GuardrailViolation` /
      `GUARDRAIL_BLOCKED` sin filtrar contenido sensible.
- [ ] Las citas de documentos ganan un modelo formal (parse/validación de
      `filename:línea`), verificación de grounding contra el contenido fuente
      (la línea referenciada existe y coincide) y rechazo fail-closed de citas
      malformadas o no fundamentadas, con aislamiento de tenant preservado.
- [ ] Cada escaneo de guardrail (entrada y salida) emite un evento de traza
      `guardrail.checked` con payload `validation` sanitizado: solo categorías,
      severidades, conteos y acción (`allowed`/`flagged`/`blocked`); nunca
      excerpts ni PII.
- [ ] El hit-rate (scanned/flagged/blocked por categoría y ventana) es
      consultable vía la superficie admin con contrato cerrado (extensión
      aditiva de `AdminMetricsResponse` o endpoint dedicado con response model
      propio); agregación en tiempo de lectura sobre las trazas existentes, sin
      migración de esquema.
- [ ] La suite determinista completa pasa sin red externa y sin cambios en el
      corpus legacy de evals ni en su pin sha256.
- [ ] Addendum en `production-readiness-v0.2.0-alpha.1.md` con la evidencia del
      GAP #9, sin modificar el scorecard histórico (la re-auditoría es fase
      posterior).

**Fuera de alcance:**

- Compaction / historial conversacional (GAP #3; requiere ADR previo).
- Crecimiento de evals a ≥50 por modo de fallo (GAP #10) y juez LLM calibrado
  (GAP #11): fase 09 según roadmap días 22–30.
- Prune de retención en operación real (GAP #12 sigue abierto; esta fase solo
  registra la interacción volumen/retención en riesgos).
- Moderación por LLM o APIs externas (OpenAI moderation, etc.): rompería la
  unidad desplegable única y añadiría egreso.
- Re-auditoría del scorecard y decisión de GA.

**Invariantes que no pueden degradarse:**

- Autoridad de tenant derivada del `Principal` autenticado; nunca de texto,
  bodies, salida LLM o documentos.
- Aprobaciones P3/P5 e idempotencia de efectos; outbox canónico y scheduler
  como vista espejo.
- `repr=False` en secretos y sanitizador de trazas; el payload `validation` de
  `guardrail.checked` contiene únicamente etiquetas, severidades, conteos y
  acción — nunca excerpts, PII ni contenido de usuario.
- Contratos cerrados de la superficie admin: solo extensiones aditivas con
  response models Pydantic `extra="forbid"`.
- Corpus legacy de evals y pin sha256 intactos.
- Una sola unidad desplegable (ADR-001) y fronteras hexagonales (ADR-003);
  allowlist de egreso (ADR-004, Accepted) sin nuevos hosts.
- Enforcement local y determinista: ningún guardrail nuevo introduce llamadas
  de red ni dependencias externas.

## Plan de olas 3 + 2

| Ola | Slot | Objetivo | Rama / worktree | Rutas autorizadas | Dependencias | Estado |
|---|---|---|---|---|---|---|
| 1 | A1 | Política de contenido: documento ratificado + categoría nueva + escaneo de salida cableado en replies de recordatorios y runtime | `kimi/phase-8-a1-content-policy` | `src/personal_assistant/domain/common/guardrails.py`, `src/personal_assistant/application/use_cases/reminders.py`, `src/personal_assistant/application/use_cases/runtime.py`, `docs/policy/content-policy.md` (nuevo), `tests/test_content_policy_guardrails.py` (nuevo) | `ninguna` | `PENDING` |
| 1 | A2 | Citas: modelo formal + verificación de grounding + escaneo de política en resúmenes de documentos | `kimi/phase-8-a2-citation-grounding` | `src/personal_assistant/domain/common/citations.py` (nuevo), `src/personal_assistant/application/dto/documents.py`, `src/personal_assistant/application/use_cases/documents.py`, `tests/test_citation_guardrails.py` (nuevo) | `ninguna` | `PENDING` |
| 1 | A3 | Telemetría de hit-rate: helpers de emisión de `guardrail.checked` (payload sanitizado) + agregación por lectura + contrato admin (sin cablear aún los puntos de escaneo) | `kimi/phase-8-a3-hit-rate-telemetry` | `src/personal_assistant/application/dto/tracing.py` (solo aditivo), `src/personal_assistant/application/ports/observability.py`, `src/personal_assistant/infrastructure/admin.py`, `src/personal_assistant/infrastructure/http.py`, `tests/test_guardrail_telemetry.py` (nuevo) | `ninguna` | `PENDING` |
| 2 | A4 | Integración: cablear emisión de `guardrail.checked` en todos los puntos de escaneo (runtime, recordatorios, documentos), composition root, actualizar `security_boundary_v1` si aplica, README/runbooks, addendum del GAP #9 | `kimi/phase-8-a4-integration` | `src/personal_assistant/infrastructure/bootstrap.py`, `src/personal_assistant/application/use_cases/*.py` (solo llamadas de emisión), `src/personal_assistant/evals/executors/security_boundary_v1.py`, `README.md`, `docs/runbook/*.md`, `docs/development/production-readiness-v0.2.0-alpha.1.md` (addendum) | `A1, A2, A3 integrados` | `PENDING` |
| 2 | A5 | Hardening: regresiones reveladas por ola 1, gates completos sección 8 | `kimi/phase-8-a5-hardening` | rutas reveladas por la ola 1 previa aprobación | `A1, A2, A3 integrados` | `PENDING` |

Los cinco roles están reservados. Un rol sin mutación termina `REVIEW_ONLY`,
entrega evidencia y no crea un commit vacío.

Notas de solapamiento:

- A1 es el único dueño de `domain/common/guardrails.py`, `reminders.py` y
  `runtime.py` en ola 1. El escaneo de política dentro del resumen de
  documentos lo ejecuta A2 (dueño de `documents.py`) usando la API pública que
  A1 entregue; si A1 aún no está integrado cuando A2 lo necesite, A2 define la
  llamada contra la interfaz acordada en este plan y A4 resuelve la integración
  final. (Alternativa de secuenciación: integrar A1 antes de arrancar A2 si la
  revisión lo prefiere.)
- A3 es el único dueño de `infrastructure/admin.py` y del contrato admin; A4
  cablea las llamadas de emisión en los use cases para evitar que A1/A2/A3
  editen los mismos archivos.
- La emisión de trazas en ola 1 se prueba con recorders inyectados (unitario);
  el cableado real de puntos de escaneo es exclusivo de A4.
- Cualquier solapamiento imprevisto se resuelve devolviendo el conflicto al
  agente afectado según la sección 2 del workflow.

## Ledger de cambios

| Tarea | Commit(s) | Resumen | Tests enfocados | Riesgo residual | Decisión |
|---|---|---|---|---|---|
| `A1` | `<pendiente>` | `<pendiente>` | `<pendiente>` | `<pendiente>` | `PENDING` |
| `A2` | `<pendiente>` | `<pendiente>` | `<pendiente>` | `<pendiente>` | `PENDING` |
| `A3` | `<pendiente>` | `<pendiente>` | `<pendiente>` | `<pendiente>` | `PENDING` |
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
docs/development/hardening/phase-08-guardrail-telemetry.md
```

**Mensaje Conventional Commit previsto:**

```text
docs(phase-8): define content/citation guardrails and hit-rate telemetry phase plan
```

## Evidencia de gates

| Gate | Comando exacto | Resultado / código | Fecha | Evidencia o nota |
|---|---|---|---|---|
| Test enfocado A1 | `uv run pytest -q tests/test_content_policy_guardrails.py` | `PENDING` | | |
| Test enfocado A2 | `uv run pytest -q tests/test_citation_guardrails.py` | `PENDING` | | |
| Test enfocado A3 | `uv run pytest -q tests/test_guardrail_telemetry.py` | `PENDING` | | |
| Lock | `uv lock --check` | `PENDING` | | |
| Sync | `uv sync --frozen --all-extras --group dev` | `PENDING` | | |
| Ruff | `uv run ruff check .` | `PENDING` | | |
| Mypy | `uv run mypy src` | `PENDING` | | |
| Pytest | `TEST_POSTGRES_DSN=... uv run pytest -q` | `PENDING` | | Requiere PostgreSQL 16 |
| Coverage | `uv run coverage run --source=src/personal_assistant -m pytest` | `PENDING` | | |
| Coverage XML | `uv run coverage xml` | `PENDING` | | |
| Coverage total | `uv run coverage report --fail-under=85` | `PENDING` | | |
| Diff coverage | `uv run diff-cover coverage.xml --compare-branch origin/main --fail-under=90` | `PENDING` | | |
| Compilación | `uv run python -m compileall -q src` | `PENDING` | | |
| Build | `uv build` | `PENDING` | | |
| Dependencias | `uv run pip-audit` | `PENDING` | | |
| Pre-commit config | `uv run pre-commit validate-config` | `PENDING` | | |
| Pre-commit | `uv run pre-commit run --all-files` | `PENDING` | | En Git Bash Windows ejecutar con `env -u GIT_CONFIG_*` (limitación ambiental documentada en fase 07) |
| Whitespace | `git diff --check` | `PENDING` | | |
| Rollback | ensayo merge + `git revert -m 1` en worktree temporal | `PENDING` | | |

Los gates omitidos requieren justificación y riesgo residual:

```text
<gate, razón, impacto, aprobación>
```

## Revisión de secretos y datos sensibles

- [ ] No hay `.env` real, credenciales, tokens, claves, contraseñas ni URLs con
      autenticación.
- [ ] `.env.example` contiene únicamente placeholders o valores no sensibles.
- [ ] No hay IDs, conversaciones, documentos, capturas, trazas o logs reales.
- [ ] El payload `validation` de los eventos `guardrail.checked` no contiene
      excerpts, PII ni contenido de usuario (verificado por tests).
- [ ] El guard de nombres staged pasó.
- [ ] Los hooks versionados de pre-commit pasaron sobre las rutas staged.
- [ ] Ningún secreto apareció en commits previos de la fase.

**Evidencia y hallazgos, siempre redactados:**

```text
<resultado sin incluir el valor sensible>
```

## Plan de rollback

| Elemento | Definición |
|---|---|
| Disparador | La política de contenido bloquea recordatorios legítimos en uso real (falsos positivos), o la emisión de trazas degrada el pipeline de entrega. |
| Punto de rollback | Merge commit de la fase (única PR). |
| Comando previsto | `git revert -m 1 <merge-commit>` desde rama `kimi/rollback-phase-8-guardrail-telemetry`. |
| Impacto en datos | Ninguno previsto: la fase reutiliza el almacén de trazas existente y agrega en tiempo de lectura; no introduce migraciones de esquema. Los eventos `guardrail.checked` ya emitidos permanecen (datos válidos para el tipo de traza existente). |
| Configuración o flags | La enforcement de política de contenido sí cambia comportamiento (nuevos bloqueos posibles); el modelo de severidades (flag vs block) acota el impacto y la telemetría de hit-rate provee la medición para ajuste. No se prevén flags; el rollback es el revert. |
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
| `R-01` | Falsos positivos de la política de contenido basada en regexes bloquean recordatorios legítimos. | `M` | `M` | Modelo de severidades: reglas nuevas nacen como `flag` (no bloquean) salvo las de riesgo alto explícito en la política ratificada; la telemetría de hit-rate da la medición para promover/ajustar reglas con datos. | `Yosoyepa` | `OPEN` |
| `R-02` | El modelo de citas actual es trivial (`filename:1`); el grounding real exige mapeo línea↔contenido que podría revelar límites del formato de resumen (60 palabras). | `M` | `M` | Alcance acotado al formato actual de citas; si la verificación revela que el formato no soporta grounding honesto, la tarea documenta el límite y propone el cambio de formato como follow-up en vez de forzarlo en esta fase. | `Yosoyepa` | `OPEN` |
| `R-03` | Emitir `guardrail.checked` en cada escaneo aumenta el volumen de trazas; la retención/prune sigue abierta (GAP #12). | `M` | `L` | Un evento por escaneo (no por finding); payload mínimo; la interacción con retención queda registrada aquí y alimenta la priorización de GAP #12. | `Yosoyepa` | `OPEN` |
| `R-04` | Solapamiento potencial A1/A2 en `documents.py` y A3/A4 en use cases. | `L` | `M` | Ownership explícito en las notas de solapamiento; emisión real solo en A4; conflictos vuelven al agente afectado. | `Yosoyepa` | `OPEN` |
| `R-05` | Cerrar la evidencia del GAP #9 no cambia el scorecard ni autoriza GA: requiere re-auditoría completa. | `H` | `L` | A4 redacta el addendum sin tocar el scorecard histórico; la re-auditoría queda como fase posterior junto a GAP #10/#11. | `Yosoyepa` | `ACCEPTED` |
| `R-06` | Las reglas de contenido locales (regex) son inherentemente más débiles que un moderador LLM; el GAP #9 podría no considerarse cerrado en la re-auditoría si se exige moderación semántica. | `M` | `M` | La política documenta explícitamente el alcance determinista y sus límites; la decisión de introducir moderación por LLM (con su ADR, egreso y juez calibrado) queda vinculada a GAP #11, no a esta fase. | `Yosoyepa` | `OPEN` |

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
| Aprobar plan de fase | `<nombre>` | `<fecha>` | `<referencia>` |
| Autorizar staging | `<nombre>` | `<fecha>` | `<referencia>` |
| Autorizar commit | `<nombre>` | `<fecha>` | `<referencia>` |
| Autorizar PR | `<nombre>` | `<fecha>` | `<referencia>` |
| Autorizar merge commit | `<nombre>` | `<fecha>` | `<referencia>` |
| Cerrar objetivo | `<nombre>` | `<fecha>` | `<referencia>` |
