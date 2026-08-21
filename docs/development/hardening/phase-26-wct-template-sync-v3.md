# Phase 26 — Sync WCT template v3 (tier pr con paridad CI + split-plan)

| Campo | Valor |
|---|---|
| Fase | `26 — wct template sync v3` |
| Estado | `APPROVED` (alcance aprobado por el mantenedor, 2026-08-21) |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `gemini/phase-26-wct-sync-v3` (coder a **esfuerzo ALTO** — toca harness y governance; no es clase mecánica) |
| Commit base | `6394a82` (main tras merge #81) |
| Fuente del template | `/home/jandradeu/Documents/well_code_template` @ `a8324a9` (PRs #8 y #9; sin tag — referenciar por hash) |
| Fecha de inicio | `2026-08-21` |
| PR | TBD (spec), TBD (sync) |
| Merge commit | TBD |

## Objetivo

Sincronizar el harness `tools/wct/` del piloto con el template @ `a8324a9`,
incorporando el feedback del piloto que ya aterrizó allí (tier `pr` con
**G-COV-DIFF local** —el gate que tumbó la ronda 1 de la fase 25—, preflight
**G-ENV**, sugerencia outline-aware en placeholder-variant, **split-plan** +
`function_sites`, diagnóstico de manifiesto legacy/missing, evidencia en
`ratchet record`), conservando las adaptaciones locales documentadas en la
fase 23.

## Qué trae el template (verificado en su código, 2026-08-21)

- **#8 `efd1548`** — feedback piloto fases 22–24: `wct split-plan` (módulo
  `splitplan/`, propone —nunca ejecuta— la partición fachada de TEST-007),
  `function_sites()` + `MUTABLE_NODES`/`_is_site` + status
  `manifest: ok|legacy|missing` en `mutate/engine.py`, y
  `MANIFEST_DIAGNOSTICS` en G-MUT-SITES (`gate/runner.py`).
- **#9 `92096bd`** — tier `pr` (commit 17 + `G-HOOKS-WIRED`, `G-COV-TOTAL`,
  `G-COV-DIFF`, `G-PROP`, `G-ACCEPT-MUT`, `G-REDTEAM`), `gate_coverage_diff()`
  con `diff-cover build/coverage/lcov.info --compare-branch <base>
  --fail-under 90 --include-untracked` y `remote_base()` en `util/git.py`;
  preflight **G-ENV** (`policy.yaml → environment_required[tier]`, ERROR antes
  de correr gates si falta una variable); sugerencia de reformulación que
  nombra el Scenario Outline en `accept/pipeline.py`; reviviscencia de gates
  muertos (G-COV-DIFF, G-DOC, G-PROP, G-ACCEPT-MUT, G-HOOKS-WIRED).
- Ratchet: `ratchet/measure.py` → `record()` exige
  `require_approval_evidence(reason)` (URL o `#N`).

## Adaptaciones del piloto a PRESERVAR (no pisar)

- `hooks/guard.py` — el piloto va POR DELANTE (`_normalize_module_invocation`
  tolerante a flags entre `python` y `-m`, fix de fase 23). **No copiar el del
  template.** Reportar al template como feedback inverso.
- `selftest/redteam.py` — `SECRET_PATTERN` local (fase 13).
- `__init__.py` — `__version__ = "0.1.0"` pineada (el template deriva de
  metadata del paquete `write-check-trust`; aquí resolvería al fallback).
- `gate/runner.py` — cableado local de gates: ruff sin `--config`, `mypy src`,
  `pytest -q`, `deptry src --known-first-party personal_assistant`, vulture
  con `tools/vulture_whitelist.py`, `bandit -c pyproject.toml`, paths G-SECRET
  `.agents/eval/prompts/replies`, `_audited_secrets`.
- Layout de tests del harness: `tests/test_wct_*.py` plano (no el árbol
  `tests/unit/` del template). El fixture `project_factory` ya existe en
  `tests/conftest.py:11`.

## Plan de sync por archivo

### Reemplazo/adición directa

- `tools/wct/splitplan/__init__.py` + `tools/wct/splitplan/engine.py` — módulo
  nuevo, sin conflicto.
- `tools/wct/util/git.py` — añade `remote_base()` (resto convergente).

### Merge cuidadoso (hunk a hunk)

- `tools/wct/gate/runner.py` — incorporar `gate_coverage_diff`,
  `MANIFEST_DIAGNOSTICS`, preflight `G-ENV`, tier `pr` **adaptado** (ver abajo)
  y `--exclude-files ^governance/generated/` en G-SECRET si aplica limpio.
  CONSERVAR todo el cableado local.
- `tools/wct/mutate/engine.py` — incorporar `function_sites` +
  `MUTABLE_NODES`/`_is_site` + status `manifest`.
- `tools/wct/accept/pipeline.py` — solo el hunk de sugerencia outline-aware.
- `tools/wct/cli.py` — solo el subcomando `split-plan`.
- `tools/wct/ratchet/measure.py` — adoptar `require_approval_evidence(reason)`
  en `record()` (cierra el mismo hueco que el bless; sin tests existentes del
  piloto que lo cubran — añadir uno).
- `governance/policy.yaml` — añadir bloque `environment_required` (ver abajo).
  Autorizado explícitamente por esta fase (excepción a SEC-005 vía spec
  aprobado, igual que fase 23); va en el bless del mantenedor.

### Tier `pr` adaptado al piloto (decisión del planner, aprobada)

Composición: **commit (17) + `G-COV-TOTAL` + `G-COV-DIFF` + `G-HOOKS-WIRED` +
`G-REDTEAM`** = 21 gates.

- `G-HOOKS-WIRED` y `G-REDTEAM` se **recablean** a `uv run python -m
  tools.wct ...` (el piloto no tiene el binario `wct` en PATH — invocarlos tal
  cual daría ERROR de herramienta ausente).
- `G-COV-DIFF`: adoptar tal cual (`diff-cover>=10.5.0` ya es dependencia).
  Nota de paridad: el gate usa lcov de cobertura de rama mientras
  `ci.yml:132` usa `coverage.xml` de línea — paridad aproximada; CI sigue
  siendo la autoridad y el gate local es early warning. Documentarlo en el
  phase log.
- **Fuera del tier pr del piloto** (documentado): `G-PROP` (no existe
  `tests/property/`; TEST-008 aplicará cuando existan) y `G-ACCEPT-MUT`
  (flujo deliberado por fase según PROC-004, no por PR; además su default
  `features/example.feature` no existe aquí).
- `environment_required` en `policy.yaml`: `pr: [TEST_POSTGRES_DSN]`. Las
  variables `APP_ENV_FILE/LLM_PROVIDER/TRANSCRIPTION_PROVIDER/TTS_PROVIDER`
  que CI exporta como `disabled` quedan fuera: la suite local pasa sin ellas
  (los providers caen a disabled por defecto) y exigirlas rompería corridas
  mínimas. Si G-ENV revelara nondeterminismo por providers, se añaden después.

### Diferido (con razón, sin marcador en código — nota de phase log)

- **G-DOC en tier `full`**: el template hardcodea `--fail-under 34` (piso de
  SU `src/example`). Cablearlo aquí exige medir el piso propio
  (`interrogate src`) y fijar baseline propia vía bless. Diferido a una fase
  de gobernanza futura para no inflar esta.
- **SHA pinning de workflows** (#10 del template): decisión aparte sobre
  `.github/workflows/**` (ruta protegida); no entra en esta fase.

### NO copiar del template

`features/wct-split-plan.feature` (G-ACCEPT lo parsearía como feature de
producto con steps sin implementar), `governance/baselines/docstring-coverage.json`,
`.github/workflows/*`, `Makefile`, `docs/runbook.md`, `README.md`, `.zcode/`,
`.claude/agents/coder.md` (opcional: fundir a mano las viñetas de
Definition-of-done en el coder.md del piloto), los 3
`skills/wct-security/SKILL.md` (ruta inexistente aquí), `rules/engine.py`
(título generado distinto — tocarlo regenera AGENTS.md, PROC-010),
`webhook.py` (User-Agent).

## Tests a portar (layout plano `tests/test_wct_*.py`)

- `test_splitplan.py` → nuevo `tests/test_wct_splitplan.py` (usa
  `tools.wct.cli.main` + `tmp_path`: portable).
- `test_gate_preflight.py` → nuevo `tests/test_wct_gate_preflight.py`
  (inyecta `environment_required` sobre el policy de `project_factory`;
  funciona antes de tocar `policy.yaml`).
- `test_gate_tiers.py` → asertando la composición del tier pr **del piloto**
  (21 gates: commit + COV-TOTAL + COV-DIFF + HOOKS-WIRED + REDTEAM), no la
  del template.
- Casos `manifest` ok/legacy/missing de `test_mutation_scan.py` + diagnóstico
  de `test_mutation_gate.py` → fundir en `tests/test_wct_mutate.py`.
- Caso de sugerencia outline-aware de `test_accept_pipeline.py` → extender
  `tests/test_wct_accept.py`.
- Test nuevo: `ratchet record` sin evidencia en `--reason` es rechazado.

## Criterios de salida

1. Suite completa en verde contra PostgreSQL real + `wct gate --tier commit`
   17/17 + redteam 30/30.
2. **`wct gate --tier pr` pasa en verde** sobre la rama de la fase (con
   `TEST_POSTGRES_DSN` exportado): 21/21 no bloqueantes, incluido
   `G-COV-DIFF` contra `origin/main`. Esta es la prueba de que el hueco de la
   fase 25 quedó cerrado.
3. `wct split-plan <archivo conocido >100 sitios>` produce una partición
   fachada razonable (demostración en el reporte, sin aplicarla).
4. G-META-1 en rojo solo dentro de la ventana esperada (sync → bless del
   mantenedor con `--reason <PR URL>`), como en fase 23.
5. Reporte con TODA desviación declarada (regla fase 24) y diff por archivo
   clasificado según el plan (reemplazo/merge/preservado).

## Instrucciones de proceso para el coder

- Esfuerzo **alto**; trabaja autónomo hasta terminar. TDD (TEST-001).
- NO pises las adaptaciones listadas en "PRESERVAR"; el diff de
  `gate/runner.py` se revisa hunk a hunk.
- NO edites `.github/workflows/**`, `governance/baselines/**`, ni
  `governance/generated/**`. `governance/policy.yaml` y
  `governance/thresholds.yaml` SOLO en lo autorizado arriba.
- El bless lo hace el mantenedor tras tu PR (no lo intentes: el hook lo
  bloquea).
- Anota tu ejecución en este documento con "## Ejecución del coder"
  APPEND-ONLY.

## Feedback WCT de la fase

(pendiente — se llena al cierre por el planner)

## Notas de cierre

(pendiente — paridad aproximada G-COV-DIFF vs CI, diferidos G-DOC/G-PROP/
G-ACCEPT-MUT, feedback inverso de guard.py al template)
