# Fase 10 — Triaje de estilo ruff 0.16

Registro de la fase 10 según la plantilla [`hardening-log.md`](../hardening-log.md).

## Identidad de la fase

| Campo | Valor |
|---|---|
| Fase | `10 — ruff-style-triage` |
| Estado | `MERGED` |
| Mantenedor | `jandradeu` |
| Rama de fase | `phase-10-ruff-style-triage` |
| Commit base | `9070a48` (origin/main, merge PR #21) |
| Fecha de inicio | `2026-08-04` |
| PR | `#24 — https://github.com/Yosoyepa/personalAssistant/pull/24` |
| Merge commit | `4cb607affda3cde237ad8f04742c8cd48e91e4c4` |

Nota de origen: esta fase salda la deuda registrada al resolver la PR #17 de
Dependabot (ruff ≥0.16.1). Ruff 0.16 amplió su `select` implícito y marcaba
252 diagnósticas sobre código preexistente; como medida temporal se fijó el
conjunto clásico por defecto (`E4, E7, E9, F`). Esta fase adopta familias de
reglas explícitas y triaja las diagnósticas. En la misma sesión se resolvieron
las 5 PRs abiertas de Dependabot (#17–#21: ruff, psycopg, coverage, fastapi,
mypy), cada una con rebase, regeneración de `uv.lock`, CI 5/5 y merge commit.

## Objetivo y límites

**Objetivo:**

- Sustituir el pin temporal de reglas por un `select` explícito de familias.
- Corregir las diagnósticas adoptadas (autofix + manual) sin cambiar semántica.
- Documentar con justificación cada regla rechazada en `ignore`.

**Criterios de aceptación:**

- [x] `uv run ruff check .` limpio con el nuevo `select`.
- [x] `uv run mypy src` 0 errores (118 archivos).
- [x] Suite completa pytest contra PostgreSQL 16 verde.
- [x] Cada regla en `ignore` lleva comentario de justificación.

**Fuera de alcance:**

- Cambios de contratos de excepción (TRY004) y rediseño de `except Exception`
  (BLE001) — reglas rechazadas con justificación.
- Refactors de complejidad (PLR09xx) y `ruff format` (no forma parte del gate).
- Autorización de GA; no aplica a ninguna fila del scorecard.

**Invariantes que no pueden degradarse:**

- Sin cambios de semántica en runtime ni en aserciones de tests.
- Bloques `try/except ImportError` de imports condicionales intactos.
- Suite de evals y corpus legacy intactos.

## Plan de olas 3 + 2

Fase de mantenimiento ejecutada con un solo subagente coder bajo política
explícita de triaje (adoptar/corregir/rechazar con justificación) y revisión
completa del diff por el mantenedor; la estructura de olas no aplica a un
cambio mecánico de una sola ruta lógica (`pyproject.toml` + reestilo).

## Ledger de cambios

| Tarea | Commit(s) | Resumen | Tests enfocados | Riesgo residual | Decisión |
|---|---|---|---|---|---|
| Select + autofix | `14c6d6d` | `select` de 18 familias; 187 autofixes (I001, UP037, UP035, UP012, B009, FURB167, RUF022, B033, PLR0402, PLR1711, RET501, SIM300, FURB110) | suite completa | B009 en `security_boundary_v1.py` requirió ampliar anotación a `Any` para mypy | `ACCEPTED` |
| Triaje manual | `4e3ccf8` | 47 fixes manuales (SIM117 ×8, PYI034 ×13, PYI036 ×3, SIM105/S110 ×3, SIM102/SIM108 ×2+2, FURB162 ×2, FLY002 ×2, RUF059 ×3, SIM101, PLR1714, RET504, RUF012, RUF043, UP031); 24 reglas rechazadas con justificación | suite completa | ninguno | `ACCEPTED` |

Reglas rechazadas (24, todas con comentario en `pyproject.toml`): TRY004,
BLE001 (contratos/robustez intencional), DTZ001 (datetimes naive como input
inválido en tests de validación), S101/S105/S106/S301/S310/S314/S603/S607/S608
(idioma de tests y contextos controlados), TRY003/TRY300/TRY301 (estilo de
errores del proyecto), UP042 (StrEnum cambiaría comportamiento público),
PLR0911/0912/0913/0915/0917/2004 (refactors fuera de alcance).

## Revisión de diff y staging

- [x] `git status --short` revisado.
- [x] `git diff --stat` revisado (107 archivos, +530/−476).
- [x] Autofixes verificados: `try/except ImportError` intactos (grep del diff).
- [x] Commits convencionales; staging explícito por el subagente y revisión del mantenedor.

## Evidencia de gates

| Gate | Comando exacto | Resultado / código | Fecha | Evidencia o nota |
|---|---|---|---|---|
| Lock | `uv lock --check` | `PASS — 0` | 2026-08-04 | |
| Ruff | `uv run ruff check .` | `PASS — All checks passed` | 2026-08-04 | verificado por mantenedor tras el subagente |
| Mypy | `uv run mypy src` | `PASS — 0 errors, 118 files` | 2026-08-04 | |
| Pytest | `uv run pytest tests -q` (con `TEST_POSTGRES_DSN`) | `PASS — 847 passed, 3 skipped, 58 subtests, 0 failed (70s)` | 2026-08-04 | postgres:16-alpine en 127.0.0.1:55432; 3 skips = probes allowlisted |
| CI alojado | checks de PR #24 | `PASS — 5/5 (quality, tests 3.11/3.12, postgres-integration, security)` | 2026-08-04 | merge `4cb607a` |

Gates omitidos con justificación: coverage/diff-cover (el diff es reestilo
mecánico sin líneas lógicas nuevas; el gate CI de diff-cover pasó), eval gate
(sin cambios en ejecutores ni corpus; pytest incluye la puerta de corpus),
pip-audit/build/pre-commit (sin cambios de dependencias ni de configuración
de hooks; cubiertos en la resolución Dependabot de la misma sesión).

## Revisión de secretos y datos sensibles

- [x] Diff íntegro de reestilo; no se añadieron credenciales, tokens ni URLs con autenticación.
- [x] Job `security` de CI (gitleaks versionado) verde en la PR #24.

## Plan de rollback

| Elemento | Definición |
|---|---|
| Disparador | gate en rojo sin corrección o regresión de estilo detectada |
| Punto de rollback | merge commit `4cb607a` |
| Comando previsto | `git revert -m 1 4cb607a` (revertiría también el `select`; alternativa puntual: añadir la regla problemática a `ignore`) |
| Impacto en datos | ninguno |
| Configuración o flags | `pyproject.toml [tool.ruff.lint]` |
| Gate posterior | ruff + mypy + pytest completo |
| Responsable | `jandradeu` |

## Ciclos de bloqueo

Sin ciclos: CI 5/5 verde en el primer intento.

## Riesgos y decisiones

| ID | Riesgo o decisión | Probabilidad | Impacto | Mitigación | Responsable | Estado |
|---|---|---|---|---|---|---|
| `R-01` | Reglas rechazadas (TRY004, BLE001, PLR09xx) acumulan deuda silenciosa | M | L | Cada `ignore` lleva justificación; revisión en próxima auditoría | `jandradeu` | `ACCEPTED` |
| `R-02` | Futuros upgrades de ruff vuelven a romper CI por cambios de familia | L | M | `select` ahora explícito: las reglas ya no dependen del default de la versión | `jandradeu` | `CLOSED` |

## Definition of Done

### Fase

- [x] Objetivos y aceptación cumplidos.
- [x] Invariantes preservados (semántica y aserciones intactas; suite verde).
- [x] Gates completos aprobados (locales + CI 5/5).
- [x] Revisión de secretos aprobada.
- [x] Sin bloqueos abiertos.
- [x] Rollback verificable.
- [x] PR única de fase con CI verde (#24), merge commit (`4cb607a`).
- [x] Ramas temporales limpiadas (rama de fase y las 5 de Dependabot eliminadas local y remotamente).

## Aprobaciones

| Decisión | Responsable | Fecha | Evidencia / comentario |
|---|---|---|---|
| Autorizar fase de estilo (triaje ruff 0.16) | `jandradeu` | 2026-08-04 | instrucción explícita "continua con la siguiente fase" |
| Autorizar commits / push / PR / merge | `jandradeu` | 2026-08-04 | flujo autorizado de resolución de PRs en la sesión |
| Cerrar objetivo | `jandradeu` | 2026-08-04 | este registro post-merge |
