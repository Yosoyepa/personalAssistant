# Hardening Log — plantilla de fase

Copiar esta plantilla a `docs/development/hardening/phase-<nn>-<slug>.md` para
cada fase. Las reglas normativas y comandos viven en
[`maintainer-workflow.md`](maintainer-workflow.md); este archivo solo registra
decisiones y evidencia para evitar duplicación.

## Identidad de la fase

| Campo | Valor |
|---|---|
| Fase | `<nn> — <nombre>` |
| Estado | `DRAFT | IN_PROGRESS | BLOCKED | READY_FOR_PR | MERGED | ROLLED_BACK` |
| Mantenedor | `<nombre>` |
| Rama de fase | `codex/phase-<nn>-<slug>` |
| Commit base | `<sha>` |
| Fecha de inicio | `<YYYY-MM-DD>` |
| PR | `<url o pendiente>` |
| Merge commit | `<sha o pendiente>` |

## Objetivo y límites

**Objetivo:** `<resultado verificable>`

**Criterios de aceptación:**

- [ ] `<criterio 1>`
- [ ] `<criterio 2>`

**Fuera de alcance:**

- `<elemento explícitamente excluido>`

**Invariantes que no pueden degradarse:**

- `<contrato, seguridad, tenant, permisos, idempotencia u operación>`

## Plan de olas 3 + 2

| Ola | Slot | Objetivo | Rama / worktree | Rutas autorizadas | Dependencias | Estado |
|---|---|---|---|---|---|---|
| 1 | A1 | `<implementación o revisión>` | `codex/...` | `<rutas>` | `ninguna` | `PENDING` |
| 1 | A2 | `<implementación o revisión>` | `codex/...` | `<rutas>` | `ninguna` | `PENDING` |
| 1 | A3 | `<implementación o revisión>` | `codex/...` | `<rutas>` | `ninguna` | `PENDING` |
| 2 | A4 | `<integración/hardening/revisión>` | `codex/...` | `<rutas>` | `<resultados ola 1>` | `PENDING` |
| 2 | A5 | `<integración/hardening/revisión>` | `codex/...` | `<rutas>` | `<resultados ola 1>` | `PENDING` |

Los cinco roles están reservados. Un rol sin mutación termina `REVIEW_ONLY`,
entrega evidencia y no crea un commit vacío.

### Checkpoint entre olas

- [ ] Los tres roles de ola 1 entregaron diff o evidencia `REVIEW_ONLY`,
      validaciones y riesgos.
- [ ] El mantenedor revisó los diffs completos.
- [ ] Los commits aceptados se integraron en la rama de fase.
- [ ] No se crearon commits vacíos para roles `REVIEW_ONLY`.
- [ ] Todo conflicto volvió al agente afectado; este integró la rama de fase,
      resolvió, revalidó y entregó una nueva revisión.
- [ ] Los gates de checkpoint pasaron.
- [ ] La ola 2 parte del HEAD integrado: `<sha>`.

## Ledger de cambios

| Tarea | Commit(s) | Resumen | Tests enfocados | Riesgo residual | Decisión |
|---|---|---|---|---|---|
| `A1` | `<sha o N/A>` | `<qué cambió o revisó>` | `<comando + resultado>` | `<riesgo>` | `ACCEPTED | REVIEW_ONLY | REWORK | REJECTED` |

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
| Test enfocado | `<comando>` | `PASS/FAIL — <code>` | `<fecha>` | `<resumen>` |
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
| Disparador | `<señal que obliga a revertir>` |
| Punto de rollback | `<commit o merge commit>` |
| Comando previsto | `git revert ...` |
| Impacto en datos | `<ninguno / migración / recuperación>` |
| Configuración o flags | `<cómo desactivar de forma segura>` |
| Gate posterior | `<comandos de verificación>` |
| Responsable | `<nombre>` |

**Resultado del ensayo seguro:** `<PASS/FAIL/PENDING y evidencia>`

## Ciclos de bloqueo

Usar una fila por ciclo del mismo bloqueo. La huella combina comando, error
principal y precondición faltante.

| Huella del bloqueo | Ciclo | Evidencia | Corrección segura probada | Resultado | Fecha |
|---|---:|---|---|---|---|
| `<huella>` | 1 | `<evidencia>` | `<acción>` | `PERSISTS/RESOLVED` | `<fecha>` |
| `<huella>` | 2 | `<evidencia>` | `<acción>` | `PERSISTS/RESOLVED` | `<fecha>` |
| `<huella>` | 3 | `<evidencia>` | `<acción>` | `PERSISTS/RESOLVED` | `<fecha>` |

Si el ciclo 3 persiste:

- [ ] Estado cambiado a `BLOCKED`.
- [ ] Reintentos detenidos.
- [ ] Cambios conservados sin stage/commit adicional.
- [ ] Autoridad, decisión o cambio externo requerido: `<detalle>`.
- [ ] Solicitud enviada al mantenedor: `<fecha/canal>`.

## Riesgos y decisiones

| ID | Riesgo o decisión | Probabilidad | Impacto | Mitigación | Responsable | Estado |
|---|---|---|---|---|---|---|
| `R-01` | `<riesgo>` | `L/M/H` | `L/M/H` | `<mitigación>` | `<nombre>` | `OPEN/CLOSED/ACCEPTED` |

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
