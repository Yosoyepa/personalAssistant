# Fase 11 — Modos de fallo top con ≥50 casos (GAP #10)

Registro de la fase 11 según la plantilla [`hardening-log.md`](../hardening-log.md).

## Identidad de la fase

| Campo | Valor |
|---|---|
| Fase | `11 — top-failure-modes` |
| Estado | `MERGED` |
| Mantenedor | `jandradeu` |
| Rama de fase | `phase-11-top-failure-modes` |
| Commit base | `a6b3155` (origin/main, merge PR #25) |
| Fecha de inicio | `2026-08-04` |
| PR | `#26 — https://github.com/Yosoyepa/personalAssistant/pull/26` |
| Merge commit | `24b2cc4a28ab4de2a180b2aab8c75c527c67f984` |

Nota de origen: la fila 10 de la auditoría exige cinco modos de fallo
nombrados con ≥50 casos representativos por `failureMode` distinto, y rechaza
la agregación por familia/categoría. La exploración mostró que el corpus ya
tenía cinco familias con ≥50 casos (50/50/57/60/50) pero con slugs finos
casi únicos por caso. El plan aprobado (enfoque A) hace literal el requisito
sin inventar casos duplicados: `failureMode` pasa al slug canónico y el detalle
fino se conserva en el nuevo campo opcional `failureModeDetail`.

## Objetivo y límites

**Objetivo:**

- Definir los cinco modos de fallo top y dejar cada uno con ≥50 casos a nivel
  literal de `failureMode`.
- Preservar la granularidad fina (`failureModeDetail`) y la capacidad de
  filtrado (`--failure-mode-detail`).
- Candar el requisito con un test de registro.

**Criterios de aceptación:**

- [x] 5 modos canónicos con 50/50/57/60/50 casos, verificados por
      `tests/test_top_failure_modes.py`.
- [x] Suite estable en 299 casos; eval gate 299/299 contra PostgreSQL 16.
- [x] Gates de fase completos.

**Fuera de alcance:**

- Escritura de casos nuevos (enfoque B descartado en el plan aprobado).
- Juez LLM / calibración TPR-TNR (fila 11; no se introduce juez).
- Re-etiquetado de archivos fuera de las cinco familias (legacy, egress,
  trace-completeness intactos).
- Autorización de GA; el cierre formal de la fila 10 lo decide el re-run de
  auditoría.

**Invariantes que no pueden degradarse:**

- 299 casos, mismos IDs, mismos `contractRefs`, mismos `expected`.
- Corpus legacy y su sha256 intactos; `suite.json` y orden de `caseFiles`
  intactos.
- Sin placeholders, skips ni LLM judges.
- Sin cambios de comportamiento en runtime de producción.

## Plan de olas 3 + 2

Fase mecánica acotada ejecutada con un subagente coder bajo política exacta
(mapeo canónico, script de re-etiquetado determinista, tests, docs) con
revisión completa del diff por el mantenedor; la estructura de olas no aplica
a un cambio de una sola ruta lógica (capa de evals + tests + docs).

## Ledger de cambios

| Tarea | Commit(s) | Resumen | Tests enfocados | Riesgo residual | Decisión |
|---|---|---|---|---|---|
| Schema + runner + relabel + README | `1b33f6e` | `failureModeDetail` opcional en `EvalCase`; filtro `failure_mode_details` y flag CLI; 267 casos re-etiquetados en 5 archivos vía script determinista (diff verificado: solo las dos claves por caso); registro en `eval/README.md` | suite completa | ninguno | `ACCEPTED` |
| Test candado + updates | `e0ca01a` | `tests/test_top_failure_modes.py` (registro, ≥50, detalle no vacío, unicidad preservada, filtros 60/2); `test_eval_runner.py` migra filtro `dst-gap` a detail; `test_security_privacy_eval_corpus.py` unicidad sobre detail | 853 passed | ninguno | `ACCEPTED` |
| Fix revisión | (en `e0ca01a`) | el conjunto de claves permitidas del corpus security-privacy admitía solo las claves viejas; se añadió `failureModeDetail` tras un fallo detectado en el gate local | test afectado | ninguno | `ACCEPTED` |

## Revisión de diff y staging

- [x] `git status --short` revisado.
- [x] `git diff --stat` revisado (12 archivos, +640/−273).
- [x] Re-etiquetado verificado caso a caso por muestreo + contadores
      (solo `failureMode`/`failureModeDetail` modificados).
- [x] Line-endings: el subagente introdujo CRLF en `eval/README.md` y
      `__main__.py`; normalizado a LF antes de staging (`.gitattributes`).
- [x] Staging explícito por rutas en dos commits convencionales.

## Evidencia de gates

| Gate | Comando exacto | Resultado / código | Fecha | Evidencia o nota |
|---|---|---|---|---|
| Lock | `uv lock --check` | `PASS — 0` | 2026-08-04 | sin cambios de dependencias |
| Ruff | `uv run ruff check .` | `PASS — All checks passed` | 2026-08-04 | |
| Mypy | `uv run mypy src` | `PASS — 0 errors, 118 files` | 2026-08-04 | |
| Pytest | `uv run pytest tests -q` (con `TEST_POSTGRES_DSN`) | `PASS — 853 passed, 3 skipped, 58 subtests, 0 failed (74s)` | 2026-08-04 | postgres:16-alpine en 127.0.0.1:55432; 1 fallo inicial del corpus security-privacy corregido en ciclo 1 |
| Eval gate | `uv run python -m personal_assistant.evals --suite eval/cases` (con DSN) | `PASS — 299/299, 0 failed` | 2026-08-04 | conteo inalterado |
| CLI smoke | `--failure-mode temporal-misinterpretation` / `--failure-mode-detail dst-gap` | `PASS — 60/60 y 2/2` | 2026-08-04 | |
| Coverage | `uv run coverage report --fail-under=85` | `PASS — 92% total` | 2026-08-04 | |
| Diff coverage | `uv run diff-cover coverage.xml --compare-branch origin/main --fail-under=90` | `PASS — 100%` | 2026-08-04 | schema/runner/`__main__` cubiertos |
| CI alojado | checks de PR #26 | `PASS — 5/5 (quality, tests 3.11/3.12, postgres-integration, security)` | 2026-08-04 | merge `24b2cc4` |

## Revisión de secretos y datos sensibles

- [x] Diff íntegro sobre capa de evals/tests/docs; sin credenciales ni datos reales.
- [x] Job `security` de CI (gitleaks versionado) verde en la PR #26.

## Plan de rollback

| Elemento | Definición |
|---|---|
| Disparador | gate en rojo sin corrección o rechazo del re-etiquetado |
| Punto de rollback | merge commit `24b2cc4` |
| Comando previsto | `git revert -m 1 24b2cc4` |
| Impacto en datos | ninguno (suite y runtime intactos) |
| Configuración o flags | `failureModeDetail` es opcional; retirarlo no rompe archivos sin el campo |
| Gate posterior | secuencia completa de gates de fase |
| Responsable | `jandradeu` |

## Ciclos de bloqueo

| Huella del bloqueo | Ciclo | Evidencia | Corrección segura probada | Resultado | Fecha |
|---|---:|---|---|---|---|
| `test_security_privacy_corpus_has_unique_strict_cases_and_minimum_size` falló en gate local: el conjunto de claves permitidas no incluía `failureModeDetail` | 1 | pytest local (852 passed / 1 failed) | añadir la clave al conjunto permitido; re-run 853 passed | `RESOLVED` | 2026-08-04 |

## Riesgos y decisiones

| ID | Riesgo o decisión | Probabilidad | Impacto | Mitigación | Responsable | Estado |
|---|---|---|---|---|---|---|
| `R-01` | El re-run de auditoría podría exigir casos *nuevos* y no re-etiquetados | L | M | Los 267 casos son representativos y preexistentes; el test candado documenta la interpretación; si la auditoría lo rechaza, se escala al enfoque B del plan | `jandradeu` | `ACCEPTED` |
| `R-02` | Consumidores externos del corpus que lean `failureMode` fino | L | L | `failureModeDetail` preserva el slug; `--failure-mode-detail` mantiene el filtrado; CI no filtraba por failure-mode | `jandradeu` | `CLOSED` |

## Definition of Done

### Fase

- [x] Objetivos y aceptación cumplidos.
- [x] Invariantes preservados (299 casos, IDs/refs/expected intactos).
- [x] Gates completos aprobados (locales + CI 5/5).
- [x] Revisión de secretos aprobada.
- [x] Sin bloqueos abiertos (1 ciclo local, resuelto).
- [x] Rollback verificable.
- [x] PR única de fase con CI verde (#26), merge commit (`24b2cc4`).
- [x] Ramas temporales limpiadas.

## Aprobaciones

| Decisión | Responsable | Fecha | Evidencia / comentario |
|---|---|---|---|
| Autorizar fase GAP #10 | `jandradeu` | 2026-08-04 | selección explícita entre tres candidatas |
| Aprobar plan (enfoque A) | `jandradeu` | 2026-08-04 | plan de fase aprobado en sesión |
| Autorizar commits / push / PR / merge | `jandradeu` | 2026-08-04 | flujo autorizado de la sesión |
| Cerrar objetivo | `jandradeu` | 2026-08-04 | este registro post-merge |
