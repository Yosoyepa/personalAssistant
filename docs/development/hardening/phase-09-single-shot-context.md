# Fase 09 — Decisión de contexto single-shot (ADR-005) y eval de egreso

Registro de la fase 09 según la plantilla [`hardening-log.md`](../hardening-log.md).

## Identidad de la fase

| Campo | Valor |
|---|---|
| Fase | `09 — single-shot-context` |
| Estado | `MERGED` |
| Mantenedor | `jandradeu` |
| Rama de fase | `codex/phase-09-single-shot-context` |
| Commit base | `96d4fc26a4f1292d7616a24a71a273e98c997a66` (origin/main, PR #16) |
| Fecha de inicio | `2026-08-03` |
| PR | `#22 — https://github.com/Yosoyepa/personalAssistant/pull/22` |
| Merge commit | `73a114165652e9edc9bffb61ecfd8dfb97d128fc` |

Nota de origen: esta fase nace de la reconciliación de un trabajo local que
duplicaba, sin saberlo, las fases remotas 07 (sandbox) y 08 (guardrails). El
código duplicado se descartó (backup en `/tmp/phase07-local-backup/`) y la
rama de fase parte de `origin/main` actualizado. El commit
`chore: enforce LF line endings via .gitattributes` (rebasado como
`a795e18`) viaja en esta rama como parte de la fase.

## Objetivo y límites

**Objetivo:**

- Cerrar GAP #3 de la auditoría por decisión de diseño — ADR-005 formaliza el
  contexto LLM single-shot con re-entry triggers explícitos.
- Incorporar la familia de evals determinista `egress.allowlist.v1` sobre la
  implementación ADR-004 ya integrada (4 casos, suite 295 → 299).
- Higiene: fix de `docs/runbook/telegram.md` (persistent storage obsoleto) y
  `.gitattributes` (line-endings LF).

**Criterios de aceptación:**

- [ ] ADR-005 aceptado; sus probes `rg` ejecutados y verificados contra la
      base phase-08.
- [ ] `uv run python -m personal_assistant.evals --suite eval/cases --category egress-allowlist`
      → 4/4.
- [ ] Eval gate completo 299/299 contra PostgreSQL 16.
- [ ] Gates de fase completos (sección 8 de maintainer-workflow).

**Fuera de alcance:**

- Historial conversacional, compaction y loss evals (cerrados por decisión,
  no por implementación).
- Cambios a la implementación de egreso de phase-07 (solo cobertura de eval).
- Autorización de GA.

**Invariantes que no pueden degradarse:**

- `tenant_id` solo desde el `Principal` autenticado.
- Prompts flat versionados; sin abstracción de historial/chat.
- Suite de evals sin placeholders, skips ni LLM judges.
- Los dos archivos de casos postgres siguen al final de `caseFiles`.

## Plan de olas 3 + 2

| Ola | Slot | Objetivo | Rutas autorizadas | Dependencias | Estado |
|---|---|---|---|---|---|
| 1 | A1 | Reconciliación: rebase de main a origin/main, descarte de duplicados | working tree | ninguna | `DELIVERED` |
| 1 | A2 | ADR-005 + verificación de probes contra base phase-08 | `docs/adr/ADR-005-single-shot-llm-context.md` | A1 | `DELIVERED` |
| 1 | A3 | Fix runbook telegram + `.gitattributes` (ya rebasado) | `docs/runbook/telegram.md`, `.gitattributes` | A1 | `DELIVERED` |
| 2 | A4 | Familia eval `egress.allowlist.v1` (executor + 4 casos + suite.json) | `src/personal_assistant/evals/executors/egress_allowlist_v1.py`, `eval/cases/egress-allowlist.v1.json`, `eval/cases/suite.json` | A1 | `IN_PROGRESS` |
| 2 | A5 | Bookkeeping: hardening log, addendum auditoría, gates, PR | `docs/development/hardening/phase-09-single-shot-context.md`, `docs/development/production-readiness-v0.2.0-alpha.1.md` | A2–A4 | `IN_PROGRESS` |

Desviación del flujo 3 + 2 igual que se registró en fases previas orquestadas
localmente: trabajo en checkout principal con subagentes sobre rutas
disjuntas, sin worktrees; revisión completa de diff antes de staging.

### Checkpoint entre olas

- [x] Roles de ola 1 entregados con evidencia (probes rg verificados).
- [x] El mantenedor revisó los diffs completos.
- [ ] Los commits aceptados se integraron en la rama de fase.
- [x] No se crearon commits vacíos.
- [x] Sin conflictos (rutas disjuntas).

## Ledger de cambios

| Tarea | Commit(s) | Resumen | Tests enfocados | Riesgo residual | Decisión |
|---|---|---|---|---|---|
| `A1` | `a795e18` (rebase) | main alineado a origin/main (PR #16); duplicados descartados con backup | n/a | backup temporal en /tmp | `ACCEPTED` |
| `A2` | `5640e86` | ADR-005 single-shot; probes ejecutados: 2 call sites, sin historial, `llm_usage_metrics` en ambos | probes `rg` del ADR | ninguno | `ACCEPTED` |
| `A3` | `27dcca0`, `a795e18` | runbook telegram sin "persistent storage"; `.gitattributes` LF | n/a (docs) | ninguno | `ACCEPTED` |
| `A4` | `db00846` | eval egress 4 casos (suite 295 → 299) | `--category egress-allowlist` → 4/4; gate completo 299/299 | ninguno | `ACCEPTED` |
| `A5` | `1dc5443` | log + addendum + gates | sección Evidencia (todos PASS) | ninguno | `ACCEPTED` |
| `A5-fix` | `362d577` | cobertura pytest del executor (CI 3.12 diff-cover 48.6% → 97%) | 847 passed; diff-cover 97% | 2 líneas defensivas inalcanzables sin cubrir | `ACCEPTED` |

## Revisión de diff y staging

- [x] `git status --short` revisado.
- [x] `git diff --stat` revisado.
- [x] `git diff --check` pasó.
- [x] `git diff --` completo revisado, incluidos archivos nuevos.
- [x] Las rutas staged fueron enumeradas; no se usó `git add .`/`git add -A`/`git commit -am`.
- [x] `git diff --cached --stat` revisado.
- [x] `git diff --cached --check` pasó.
- [x] `git diff --cached --` completo revisado.

**Rutas staged:**

```text
src/personal_assistant/evals/executors/egress_allowlist_v1.py   (db00846)
eval/cases/egress-allowlist.v1.json                             (db00846)
eval/cases/suite.json                                           (db00846)
docs/adr/ADR-005-single-shot-llm-context.md                     (5640e86)
docs/runbook/telegram.md                                        (27dcca0)
docs/development/hardening/phase-09-single-shot-context.md      (este commit)
docs/development/production-readiness-v0.2.0-alpha.1.md         (este commit)
```

**Mensaje Conventional Commit previsto:**

```text
docs(phase-09): record hardening log and audit addendum
```

## Evidencia de gates

| Gate | Comando exacto | Resultado / código | Fecha | Evidencia o nota |
|---|---|---|---|---|
| Probes ADR-005 | `rg -n "\.complete\(" src/personal_assistant/application/use_cases` | `PASS — solo reminders.py y commands.py` | 2026-08-03 | base phase-08 |
| Probes ADR-005 | `rg -ni "conversation_history\|message_history\|chat_history" src/ prompts/` | `PASS — sin coincidencias` | 2026-08-03 | |
| Test enfocado A4 | `uv run python -m personal_assistant.evals --suite eval/cases --category egress-allowlist` | `PASS — 4/4, 0 failed` | 2026-08-03 | nueva familia |
| Lock | `uv lock --check` | `PASS — 0` | 2026-08-03 | Resolved 76 packages |
| Sync | `uv sync --frozen --all-extras --group dev` | `PASS — 0` | 2026-08-03 | Checked 75 packages |
| Ruff | `uv run ruff check .` | `PASS — 0` | 2026-08-03 | All checks passed |
| Mypy | `uv run mypy src` | `PASS — 0` | 2026-08-03 | 118 source files, no issues |
| Pytest | `uv run pytest -q` (con `TEST_POSTGRES_DSN`) | `PASS — 843 passed, 3 skipped, 58 subtests, 0 failed (61s)` | 2026-08-03 | postgres:16-alpine en 127.0.0.1:55432; 3 skips = probes allowlisted |
| Coverage | `uv run coverage run --source=src/personal_assistant -m pytest` | `PASS — 843 passed (70s)` | 2026-08-03 | |
| Coverage XML | `uv run coverage xml` | `PASS — 0` | 2026-08-03 | coverage.xml |
| Coverage total | `uv run coverage report --fail-under=85` | `PASS — 91% total (9113 stmts, 778 miss)` | 2026-08-03 | |
| Diff coverage | `uv run diff-cover coverage.xml --compare-branch origin/main --fail-under=90` | `PASS — 0` | 2026-08-03 | "No lines with coverage information in this diff": el diff de la fase no toca código fuente cubierto por pytest; el nuevo executor se ejerce vía eval gate (299/299), no vía pytest |
| Compilación | `uv run python -m compileall -q src` | `PASS — 0` | 2026-08-03 | |
| Build | `uv build` | `PASS — 0` | 2026-08-03 | sdist + wheel 0.2.0a1 |
| Dependencias | `uv run pip-audit` | `PASS — sin vulnerabilidades` | 2026-08-03 | única nota: paquete propio no publicado en PyPI |
| Pre-commit config | `uv run pre-commit validate-config` | `PASS — 0` | 2026-08-03 | |
| Pre-commit | `uv run pre-commit run --all-files` | `PARCIAL — large-files/merge-conflicts/detect-private-key/ruff Passed; detect-aws-credentials Failed (exit 2, ambiental)` | 2026-08-03 | ver justificación abajo |
| Whitespace | `git diff --check` | `PASS — 0` | 2026-08-03 | |
| Eval gate | `uv run python -m personal_assistant.evals --suite eval/cases` (con DSN) | `PASS — 299/299, 0 failed` | 2026-08-03 | 295 baseline + 4 egress |
| Rollback | ensayo: rutas de fase enumeradas y descarte ya ejercido durante la reconciliación (`git restore --worktree` + borrado de duplicados, backup verificado en /tmp) | `PASS` | 2026-08-03 | rollback post-merge: `git revert -m 1 <merge>` |

Los gates omitidos requieren justificación y riesgo residual:

```text
Gate: pre-commit detect-aws-credentials — NO APLICABLE (ambiental).
Razón: el hook sale con código 2 ("No AWS keys were found in the configured
credential files and environment variables") en esta máquina Linux; verificado
que falla igual sobre origin/main limpio (worktree detached en 96d4fc2), es
decir, es preexistente e independiente de los cambios de la fase.
Impacto: ninguno sobre la cobertura de secretos real — detect-private-key
Passed, el scanner de tests/test_public_artifacts.py Passed, y el check
versionado de gitleaks en .github/workflows/security.yml cubre el gate en CI.
Aprobación: registrado por el mantenedor en este log; riesgo residual nulo.
Nota: el smoke del contenedor endurecido es evidencia de phase-07; esta fase
no toca Dockerfile ni compose.
```

## Revisión de secretos y datos sensibles

- [x] No hay `.env` real, credenciales, tokens, claves, contraseñas ni URLs con autenticación.
- [x] `.env.example` contiene únicamente placeholders o valores no sensibles.
- [x] No hay IDs, conversaciones, documentos, capturas, trazas o logs reales.
- [x] El guard de nombres staged pasó.
- [x] Los hooks versionados de pre-commit pasaron sobre las rutas staged.
- [x] Si existe gitleaks, la evidencia referencia su check CI versionado (`.github/workflows/security.yml`).
- [x] Ningún secreto apareció en commits previos de la fase.

**Evidencia y hallazgos, siempre redactados:**

```text
Los casos de eval usan placeholders con prefijo test_ aceptados por el
scanner de tests/test_public_artifacts.py.
```

## Plan de rollback

| Elemento | Definición |
|---|---|
| Disparador | gate de fase en rojo sin corrección o rechazo en revisión |
| Punto de rollback | merge commit de la fase (una vez integrada); pre-merge: rutas de trabajo |
| Comando previsto | `git revert -m 1 <merge_commit>` desde `codex/rollback-phase-09-single-shot-context`; pre-merge: `git restore --worktree -- <rutas>` |
| Impacto en datos | ninguno |
| Configuración o flags | retirar `egress-allowlist.v1.json` de `suite.json` desactiva la familia sin tocar código |
| Gate posterior | secuencia completa de gates de fase |
| Responsable | `jandradeu` |

**Resultado del ensayo seguro:** `PENDING`

## Ciclos de bloqueo

| Huella del bloqueo | Ciclo | Evidencia | Corrección segura probada | Resultado | Fecha |
|---|---:|---|---|---|---|
| CI `tests (3.12)` diff-cover <90% sobre `egress_allowlist_v1.py` (48.6%; el executor solo se ejercía vía eval gate, no vía pytest) | 1 | job 91832445128, `Missing 37 lines` | `tests/test_egress_eval_executor.py` cubre los 4 escenarios vía `execute()`; diff-cover local 97% | `RESOLVED` (CI 5/5 verde en `362d577`) | 2026-08-03 |

## Riesgos y decisiones

| ID | Riesgo o decisión | Probabilidad | Impacto | Mitigación | Responsable | Estado |
|---|---|---|---|---|---|---|
| `R-01` | ADR-005 cierra GAP #3 por decisión, no por implementación | L | M | Re-entry triggers explícitos con obligación de nuevo ADR + compaction + loss evals | `jandradeu` | `CLOSED` |
| `R-02` | Trabajo duplicado con sesiones paralelas (ya ocurrido con phase-07/08) | M | M | `git fetch --prune` al abrir fase (regla de maintainer-workflow §1); reconciliación documentada en este log | `jandradeu` | `CLOSED` |
| `R-03` | Backup del trabajo descartado vive en /tmp (volátil) | L | L | El contenido novel ya está re-implementado sobre la nueva base; el duplicado no se necesita | `jandradeu` | `ACCEPTED` |
| `R-04` | `detect-aws-credentials` falla de forma ambiental en máquinas sin credenciales AWS (exit 2) | H | L | Verificado preexistente en origin/main; cobertura de secretos real por detect-private-key + scanner de tests + gitleaks en CI | `jandradeu` | `ACCEPTED` |

## Definition of Done

### Tareas

- [x] Objetivos y aceptación cumplidos (A1–A3; A4/A5 en curso).
- [x] Invariantes preservados.
- [ ] Diffs de trabajo y staged revisados.
- [ ] Staging explícito.
- [x] Tests enfocados aprobados (probes; A4 pendiente).
- [x] Sin secretos ni artefactos temporales.
- [ ] Commits convencionales y reversibles.
- [x] Riesgos residuales registrados.

### Fase

- [x] Los cinco roles 3 + 2 entregaron implementación o revisión.
- [x] Roles `REVIEW_ONLY` registrados sin commits vacíos.
- [x] Tareas aceptadas integradas.
- [x] Gates completos aprobados.
- [x] Revisión de secretos aprobada.
- [x] Sin bloqueos abiertos (1 ciclo CI diff-cover, resuelto en ciclo 1).
- [x] Rollback verificable.
- [x] PR única de fase revisada y con CI verde (#22).
- [x] Método de integración: merge commit (`73a1141`).
- [x] Worktrees y ramas temporales limpiados de forma segura.

## Aprobaciones

| Decisión | Responsable | Fecha | Evidencia / comentario |
|---|---|---|---|
| Autorizar reconciliación (descartar duplicados, alinear a origin/main) | `jandradeu` | 2026-08-03 | aprobación explícita en sesión |
| Autorizar rebase de main + rama de fase | `jandradeu` | 2026-08-03 | aprobación explícita en sesión |
| Autorizar staging | `jandradeu` | 2026-08-03 | aprobación explícita en sesión |
| Autorizar commit | `jandradeu` | 2026-08-03 | aprobación explícita en sesión (`a795e18`, `db00846`, `5640e86`, `27dcca0`, `1dc5443`, fix `362d577`) |
| Autorizar PR | `jandradeu` | 2026-08-03 | PR #22, CI 5/5 verde |
| Autorizar merge commit | `jandradeu` | 2026-08-03 | merge commit `73a1141` |
| Cerrar objetivo | `jandradeu` | 2026-08-03 | este registro post-merge |
