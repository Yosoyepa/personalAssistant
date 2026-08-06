# Fase 12 — Tier conductual de evals y juez LLM (GAP #11)

Registro de la fase 12 según la plantilla [`hardening-log.md`](../hardening-log.md).

## Identidad de la fase

| Campo | Valor |
|---|---|
| Fase | `12 — behavioral-evals-judge` |
| Estado | `PULL REQUEST PENDING` |
| Mantenedor | `jandradeu` |
| Rama de fase | `codex/phase-12-behavioral-evals-judge` |
| Commit base | `05fb521` (origin/main, merge PR #27) |
| Fecha de inicio | `2026-08-06` |
| PR | pendiente (`gh` no está instalado en el entorno) |
| Merge commit | pendiente |

Nota de origen: la fila 11 de la auditoría era la única fila abierta que se
cierra construyendo. El problema de fondo excede la fila: los 299 casos del
gate L1 son deterministas y **ninguno ejerce un LLM**, así que las dos
superficies realmente LLM del runtime no tenían cobertura conductual alguna, y
`LLM_INTENT_CONFIDENCE_THRESHOLD = 0.65` era una constante elegida a mano y
nunca validada contra datos.

## Objetivo y límites

**Objetivo:**

- Tier conductual (nivel 2) separado del gate L1, ejecutable offline en
  `replay` y a mano en `record`/`live`.
- Corpus etiquetado de ≥100 items, sintético, sin datos personales.
- Juez LLM con prompt versionado y veredicto estricto fail-closed.
- Reporte TPR/TNR con intervalos de Wilson sobre holdout.
- ADR-006 fijando por qué L1 sigue determinista y cuál es la autoridad del juez.

**Criterios de aceptación:**

- [x] 154 etiquetas (120 intención / 34 extracción), ≥100 cumplido.
- [x] `replay` determinista y sin red; L1 sigue 299/299.
- [x] Umbrales pre-registrados aplicados **en código**
      (`calibration.judge_authority`), no sólo en prosa.
- [x] Reporte publicado con intervalos y con sus límites declarados.
- [ ] **Cifras sobre un modelo real** — no alcanzable en este entorno (ver
      Riesgos materializados).
- [ ] **Etiquetas humanas** — las etiquetas son `assistant-draft`.

**Fuera de alcance:**

- `eval/cases`, sus 299 casos, IDs, `expected`, `contractRefs` y el pin sha256.
- Mover `LLM_INTENT_CONFIDENCE_THRESHOLD` (sólo se mide).
- Historial multi-turno (dispararía los re-entry triggers de ADR-005).
- Filas #1, #7, #9 (producción) y #12.

**Invariantes que no pueden degradarse:**

- `tenant_id` sólo desde `Principal`.
- Allowlist de egress deny-by-default (ADR-004).
- CI sin red: el tier corre sólo en `replay`.
- Sin secretos ni datos personales en el repo, cassettes incluidos.
- ADR-005 single-shot.

## Plan de olas 3 + 2

| Ola | Slot | Objetivo | Commit |
|---|---|---|---|
| 1 | A1 | `schema`, `corpus`, `metrics`, `replay` | `a9aa04d` |
| 1 | A2 | corpus etiquetado + rúbrica | `86e8d00` |
| 1 | A3 | prompt de juez + módulo `judge` | `4123b12` |
| 2 | A4 | runner, CLI, cassettes, CI | `66ce334` |
| 2 | A5 | calibración, ADR-006, candado, docs | (esta entrega) |

## Ledger de cambios

| Tarea | Resumen | Tests enfocados | Riesgo residual | Decisión |
|---|---|---|---|---|
| A1 | `StrictModel`-based corpus/cassette schemas; carga sin glob ni `..`; matriz de confusión + Wilson; record/replay sobre el Protocol `LLMProvider` | `test_behavioral_corpus.py`, `test_calibration_metrics.py`, `test_behavioral_replay.py` | ninguno | `ACCEPTED` |
| A2 | 154 etiquetas en español; rúbrica de etiquetado | `test_behavioral_corpus.py` | **etiquetas `assistant-draft`, no humanas** | `ACCEPTED CON RESERVA` |
| A3 | `prompts/judge_reminder_extraction/v1.md` + registry; veredicto estricto, salida malformada = fallo saneado | `test_judge_contract.py` | ninguno | `ACCEPTED` |
| A4 | runner sobre los renderizadores y parsers **del propio runtime**; CLI espejo del L1; cassettes sintéticos; paso CI sin red | `test_behavioral_cli.py` | cassettes sintéticos | `ACCEPTED CON RESERVA` |
| A5 | `judge_authority()` con barras pre-registradas; reporte v1; ADR-006; candado de umbral | `test_intent_threshold_calibration.py` | ninguno | `ACCEPTED` |

Decisiones de diseño que merecen registro:

- **El runner importa los privados del runtime** (`_render_intent_prompt`,
  `_render_reminder_extraction_prompt`, `_reminder_extraction_from_llm`). Es un
  acoplamiento deliberado: un eval que renderiza su propia copia del prompt
  sigue pasando mientras el prompt real se desvía.
- **`provenance` es obligatorio y sin default.** Un cassette que olvide decir
  qué es heredaría la respuesta favorable.
- **Un juez roto es `error`, no `FAIL`.** Puntuarlo como rechazo legítimo
  inflaría la tasa de verdaderos negativos.
- **Los splits nunca se agrupan.** Un umbral elegido en `calibration` y luego
  puntuado sobre el conjunto agrupado reportaría en parte sobre los datos que lo
  eligieron.

## Riesgos materializados

**R-06 (no previsto en el plan) — no hay proveedor configurado.**
`AppSettings()` reporta `llm_provider='disabled'`, sin API key y con
`egress_allowed_hosts=frozenset()`. Las únicas credenciales presentes en el
entorno son las de la sesión de Claude Code
(`ANTHROPIC_BASE_URL=https://agentrouter.org`). **No se usaron**: son
herramientas del operador, no configuración del proyecto, y ese host no está en
la allowlist del proyecto, así que usarlas gastaría cuota ajena y violaría
ADR-004.

Mitigación aplicada: en vez de dejar el tier inejecutable o falsear una
grabación, `provenance` pasó a ser campo obligatorio del schema y se conectó a
`is_calibration_evidence` y a `judge_authority()`. Un fixture sintético **no
puede** citarse como comportamiento medido de un modelo. Es la negativa en
código que R-01 pedía, aplicada a un riesgo que el plan no anticipó.

**R-01 se materializó por otra vía.** El juez queda advisory-only, pero no por
no alcanzar las barras: las alcanza nominalmente (TPR 1.000). Queda advisory por
procedencia y por soporte de clase (TNR sobre n=0). El diseño es intencional:
una cifra favorable no compra autoridad por sí sola.

## Evidencia de gates

| Gate | Comando | Resultado | Fecha |
|---|---|---|---|
| L1 sin contaminar | `python -m personal_assistant.evals --suite eval/cases` | `PASS — 299/299` | 2026-08-06 |
| Replay determinista | dos corridas `--json` + `diff` | `PASS — idénticas (57 509 bytes)` | 2026-08-06 |
| Replay sin red | allowlist vacía, `LLM_PROVIDER=disabled` | `PASS — 154/154` | 2026-08-06 |
| Lock | `uv lock --check` | `PASS — 76 paquetes, sin cambios` | 2026-08-06 |
| Ruff | `uv run ruff check .` | `PASS` | 2026-08-06 |
| Mypy | `uv run mypy src` | `PASS — 127 archivos` | 2026-08-06 |
| Pytest | `uv run coverage run --source=src/personal_assistant -m pytest -q` | `PASS — 949 passed, 3 skips permitidos, 386 subtests` | 2026-08-06 |
| Coverage total | `uv run coverage report --fail-under=85` | `PASS — 92%` | 2026-08-06 |
| Diff coverage | `uv run diff-cover coverage.xml --compare-branch origin/main --fail-under=90` | `PASS — 95% (27/574 líneas sin cubrir)` | 2026-08-06 |
| Compileall | `uv run python -m compileall -q src tests scripts` | `PASS` | 2026-08-06 |
| Build | `uv build` | `PASS — sdist + wheel 0.2.0a1` | 2026-08-06 |
| pip-audit | `uv run pip-audit` | `PASS — sin vulnerabilidades (el propio paquete se omite, no está en PyPI)` | 2026-08-06 |
| Whitespace | `git diff --check` | `PASS` | 2026-08-06 |
| Secretos en cassettes | `grep -nEi '(api[_-]?key|token|secret|sk-|bearer)'` sobre `eval/behavioral/` | `PASS — sólo coincidencias en los nombres de campo inputTokens/outputTokens` | 2026-08-06 |
| `detect-private-key` | `pre-commit run --all-files` | `PASS` | 2026-08-06 |

`gitleaks` no se ejecutó localmente: no está instalado y no existe hook en
`.pre-commit-config.yaml`. El escaneo vive sólo en
`.github/workflows/security.yml` (`gitleaks/gitleaks-action@v3`), así que esa
evidencia la produce CI, no esta máquina.

### Dos fallos de gate pre-existentes, ajenos a esta fase

`uv run pre-commit run --all-files` falla en dos hooks, y ninguno de los dos se
debe a código de la fase 12:

1. **`ruff` (hook)** reporta 13 errores — entre ellos `UP038` en archivos que la
   fase no toca — mientras `uv run ruff check .` pasa limpio. Es exactamente el
   hallazgo colateral #2: el hook está pineado en `v0.11.0` y `pyproject.toml`
   exige `ruff>=0.16.1`, así que el hook aplica un conjunto de reglas distinto
   del que el proyecto declara.
2. **`detect-aws-credentials`** falla por falta de `--credentials-file`
   configurado, no por haber encontrado claves; su propia salida dice
   `No AWS keys were found`.

El arreglo del hallazgo #1 existe ya en la rama hermana
`codex/chore-gate-and-log-hygiene` (`45d6dce`), aún sin mergear a `main`. Se deja
constancia aquí en vez de arreglarlo dentro de esta fase: mezclar el arreglo del
gate con la entrega del tier conductual haría el diff de la fase imposible de
revisar por separado.

Las líneas sin cubrir de `runner.py` son las ramas `record`/`live`, que por
diseño no se ejercitan en pruebas: construirlas requeriría un proveedor. Las
cubre `scripts/build_synthetic_cassettes.py` al regenerar los cassettes.

## Estado de la fila 11

**No cerrada.** El DoD pide un set de ≥100 etiquetas y un reporte TPR/TNR
publicado. El set existe y el reporte está publicado, pero sus números salen de
un fixture y sus etiquetas son `assistant-draft`. Reclamar la fila sería
reclamar una medición que nunca se tomó.

Dos pasos la cierran, en este orden: revisión humana del corpus, y una pasada de
grabación real que regenere el reporte como v2. Ver
`docs/development/judge-calibration-v1.md` y los re-entry triggers de ADR-006.

## Pendientes de cierre

- [x] Gate completo de fase (`maintainer-workflow.md` §8) — ver tabla arriba.
- [ ] Push de rama y PR (`gh` no instalado; queda al mantenedor).
- [ ] Addendum de fase 12 en el documento de auditoría.
