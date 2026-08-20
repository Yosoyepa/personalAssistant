# Phase 23 — Sync WCT template v2 (feedback aplicado al harness)

| Campo | Valor |
|---|---|
| Fase | `23 — wct template sync` |
| Estado | `SPEC_APPROVED` (dirección aprobada por el mantenedor, 2026-08-20) |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `gemini/phase-23-wct-sync` (coder), planner: spec + verificación |
| Commit base | `ac43954` (main tras merge #71) |
| Fecha de inicio | `2026-08-20` |
| PR | pendiente |
| Merge commit | pendiente |

## Objetivo

Traer al piloto la versión mejorada del harness WCT que el mantenedor aplicó
en `/home/jandradeu/Documents/well_code_template` a partir del feedback
consolidado (`docs/development/wct-template-improvements.md`), conservando
las adaptaciones locales intencionales del piloto (fase 13) y dejando el
repositorio verde con el harness nuevo.

No es copia al por mayor: es una **sincronización con reconciliación**
archivo por archivo, definida en este spec.

## Qué trae el template (verificado en su código, 2026-08-20)

Gate propio del template: 7/7 fast en verde, con tests propios nuevos
(`tests/unit/test_fmt.py`, `test_hooks.py`, `test_integrity*.py`,
`test_mutation_*.py`, `test_gate_secrets.py`).

1. **G-MUT-SITES por fingerprint AST** (`mutate/engine.py`): identidad de
   función = `sha256(ast.dump(node, include_attributes=False))`, sin
   `lineno`; manifiesto con `schema_version` (schema 1 legacy con lineno no
   casa → hay que regenerar).
2. **G-ACCEPT placeholder-variant enriquecido** (`accept/pipeline.py`):
   reporta escenario, línea y línea del escenario colisionado.
3. **`wct fmt --staged`** (`fmt/engine.py` + wiring en `cli.py`): formatea
   solo el changeset (`staged_files` / `changed_files` en `util/git.py`).
4. **`wct mutate update-manifest --approved-by --reason`** atómico: regenera
   el manifiesto y bendice el lock en el mismo paso (sigue siendo comando
   solo-humano).
5. **Bless exclusivamente humano con enforcement** (`hooks/guard.py`):
   hook PreToolUse bloquea `integrity lock|bless` y auto-aprobaciones en
   sesiones de agente; `integrity/engine.py::require_approval_evidence`
   exige que `--reason` cite URL o `#N`.
6. **Hash EOL-normalizado** (`integrity/engine.py`): algoritmo
   `sha256:eol-normalized` (`_digest_eol_normalized`) — inmunidad a CRLF/LF
   de checkout. Cambia el hash de TODO el lock → re-bless general.
7. **Anti-evasión `importlib`** (`archmetrics/analyzer.py::_dynamic_imports`):
   flaggea `import_module`/`__import__` de módulos del proyecto (literal) y
   los opacos (argumento no literal).
8. **G-SECRET sin churn de baseline** (`gate/runner.py::_audited_secrets`):
   ya NO se pasa `--baseline` a detect-secrets (ese flag reescribía
   `.secrets.baseline` y abortaba commits a mitad de hook); ahora se lee en
   solo-lectura y se triagea por `(filename, hashed_secret)`.
9. Backports ya convergentes (fase 21 del piloto): `review()` +
   `tracked_files` + `ausente no versionado (omitido)`; fix `--slim` de
   G-SECRET; exclusión de `TYPE_CHECKING` en archmetrics.

## Plan de sync por archivo

### Reemplazo directo (la versión del template gana; convergentes o superset)

- `tools/wct/mutate/engine.py`
- `tools/wct/accept/pipeline.py`
- `tools/wct/fmt/` (módulo NUEVO: `engine.py`, `__init__.py`)
- `tools/wct/util/git.py` (añade `staged_files`; resto convergente)
- `tools/wct/integrity/engine.py` (superset: review + EOL hash + evidence)
- `tools/wct/archmetrics/analyzer.py` (superset: TYPE_CHECKING +
  `_dynamic_imports`)
- `tools/wct/hooks/guard.py` (enforcement bless humano)

### Merge cuidadoso (NO sobrescribir: el piloto tiene adaptaciones locales)

- `tools/wct/gate/runner.py` — conservar el wiring de gates del proyecto
  (ruff del proyecto, `mypy src`, `pytest -q`, `deptry src`, whitelist
  vulture, config bandit de pyproject, G-MUT-SITES diferencial) e INCORPORAR
  `_audited_secrets` (baseline solo-lectura) y cualquier delta de reporte.
  El fix `--slim` ya está en ambos.
- `tools/wct/cli.py` — conservar wiring local e incorporar: comando `fmt`
  (`--staged` y flags asociados), args `--approved-by/--reason` de
  `update-manifest`, y la salida de warnings de `integrity check` (el piloto
  ya imprime `aviso:` desde la fase 21; reconciliar formato).

### Mantener versión piloto (adaptaciones intencionales)

- `tools/wct/selftest/redteam.py` — `SECRET_PATTERN` (el nombre `SECRET` del
  template dispara el escáner propio del proyecto, fase 13).
- `tools/wct/__init__.py` — versión local del piloto.
- `doctor/checks.py`, `dry/analyzer.py`, `ratchet/*.py`, `report/render.py`,
  `webhook.py` — diffs cosméticos (orden de imports, `re.I`); no portar.

### Tests del harness a portar (adaptando al layout del piloto)

Del template: los tests unit/integration de fmt, hooks guard, integrity
(EOL hash, approval evidence) y mutation (fingerprint AST, schema). Layout
del piloto: `tests/test_wct_*.py` (precedente: `tests/test_wct_integrity.py`
de la fase 21). NO copiar el árbol `tests/` del template tal cual (choca
con `testpaths` del proyecto, decisión de fase 13).

## Puntos de integración conocidos (el coder los resuelve y documenta)

1. **Regex del guard vs invocación del piloto**: el patrón del template
   `(?:^|\s)(?:uv\s+run\s+)?wct\s+integrity\s+(?:lock|bless)\b` NO casa con
   la forma que usa este repo: `python -m tools.wct integrity bless`
   (el `wct` va precedido de `.`). Extender el patrón para cubrir
   `python(3)? -m tools.wct ...` y `uv run python -m tools.wct ...`, con
   tests. El `integrity check` NUNCA debe ser bloqueado.
2. **`_dynamic_imports` vs shim deliberado**: `config_settings.py` usa
   `importlib.import_module` deliberadamente (fase 16). Tras portar
   `archmetrics/analyzer.py`, correr G-ARCHMETRICS; si lo flaggea, añadir la
   excepción documentada en `governance/thresholds.yaml → cycle_allowlist`
   (ruta protegida: va en el bless del mantenedor).
3. **Wiring del hook**: verificar que `.claude/settings.json` del piloto
   invoca `wct hook` de forma que el nuevo guard actúe; si necesita cambio,
   es ruta protegida → va en el bless.
4. **Manifiesto schema 2**: tras el sync, el manifiesto actual (schema 1)
   queda obsoleto; lo regenera el MANTENEDOR con el nuevo
   `update-manifest --approved-by --reason` (atómico con bless).

## División de trabajo

### Coder (`gemini/phase-23-wct-sync`)

1. Ejecutar el plan de sync por archivo tal cual está definido arriba
   (reemplazo / merge / mantener). Justificar en el log cualquier desviación.
2. Portar los tests del harness indicados, adaptados a `tests/test_wct_*.py`.
3. Resolver y documentar los 4 puntos de integración.
4. Actualizar `docs/development/maintainer-workflow.md`: bless con evidencia
   (URL/#N en `--reason`), `update-manifest` atómico, fin del churn de
   `.secrets.baseline` (G-SECRET solo-lectura).
5. Restricciones duras: NO correr `integrity bless` ni
   `mutate update-manifest` con `--approved-by` (son solo-humanos y el nuevo
   guard los bloquea en sesión de agente — si quedas bloqueado por el propio
   guard, ES la prueba de que funciona: documéntalo y pide el bless en el
   reporte). NO tocar `governance/**` salvo la entrada `cycle_allowlist` si
   aplica (y sabiendo que quedará pendiente de bless). NO tocar
   `pyproject.toml`, `uv.lock`, `.github/**`. Phase log append-only.
6. Verificación local esperada ANTES del bless: la suite completa verde y
   todos los gates verdes EXCEPTO G-META-1/integrity (que fallará por los
   archivos de `tools/wct` modificados — es lo esperado hasta el bless).
   Reportar la salida exacta.

### Mantenedor (Yosoyepa) — bless tras la verificación del planner

Comandos exactos (una línea cada uno; el planner los revalida con el número
de PR real en el handoff):

1. `git checkout gemini/phase-23-wct-sync && git pull`
2. Re-bless general con hash EOL nuevo:
   `PYTHONPATH=. uv run python -m tools.wct integrity bless --approved-by "Yosoyepa" --reason "<URL de la PR>"`
3. Regenerar manifiesto schema 2 (atómico con bless):
   `PYTHONPATH=. uv run python -m tools.wct mutate update-manifest --approved-by "Yosoyepa" --reason "<URL de la PR>"`
4. `git add governance/ .secrets.baseline && git commit -m "chore(governance): bless phase 23 WCT template sync" && git push`

### Planner (Kimi)

1. Este spec; verificación independiente post-bless (suite, gate 17/17,
   redteam 30/30, CI 5/5 con el integrity check de CI validando los hashes
   nuevos).
2. Cierre: estado COMPLETED + sección Feedback WCT.

## Criterios de salida

- Suite completa verde con los tests portados; `wct gate --tier commit`
  17/17 post-bless; redteam 30/30; CI 5/5 (el paso `integrity check` de CI
  valida los hashes EOL nuevos).
- `wct fmt --staged` operativo; manifiesto en schema 2; lock con
  `sha256:eol-normalized`.
- El guard bloquea `integrity bless` en sesión de agente (demostrado) y el
  `--reason` sin URL/#N es rechazado.
- `maintainer-workflow.md` actualizado; adaptaciones del piloto conservadas
  (demostrado: `SECRET_PATTERN` sigue, gates siguen apuntando a la config
  del proyecto).

## Riesgos y notas

- **Ventana de gates rojos**: entre el sync y el bless, G-META-1 falla en
  local y CI — esperado y acotado; el bless del mantenedor la cierra.
- **Riesgo medio-alto por superficie**: se toca el harness entero. Mitigación:
  el template llega verde de su propia casa y con tests; el piloto valida
  con suite completa + gate + redteam.
- **Sin Gherkin nuevo**: fase de tooling sin comportamiento observable por el
  usuario final (TEST-010 no aplica; G-ACCEPT sigue validando los features
  existentes).
- Si el sync destapa un bug del template, se reporta como feedback (el
  template se arregla allá; aquí se aplica el workaround mínimo documentado).

## Feedback WCT de la fase

(pendiente — se llena al cierre por el planner)
