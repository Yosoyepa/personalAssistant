# Phase 23 — Sync WCT template v2 (feedback aplicado al harness)

| Campo | Valor |
|---|---|
| Fase | `23 — wct template sync` |
| Estado | `COMPLETED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `gemini/phase-23-wct-sync` (coder), planner: spec + verificación |
| Commit base | `ac43954` (main tras merge #71) |
| Fecha de inicio | `2026-08-20` |
| PR | #72 (spec), #73 (sync) |
| Merge commit | `9a2fa34` (spec), `9d76384` (sync) |
| Bless del mantenedor | `829f800` (re-bless EOL + manifiesto schema 2, reason con URL de PR) |

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

(cierre por el planner, tras verificación independiente y merge `9d76384`)

1. **La definition of done del coder funcionó**: el prompt incluyó "push + PR
   o handoff explícito" (lección de la fase 22) y el coder entregó con PR
   abierta (#73). Primera fase sin commit mecánico del planner. Validada
   como regla permanente del flujo.
2. **El coder corrió `--tier fast` en vez del commit tier pedido por el
   spec** y no detectó el fallo transitorio de `G-MUT-SITES`: con el
   manifiesto legacy (schema 1), las 1113 funciones cuentan como cambiadas y
   `telegram.py` (>100 sitios) bloquea hasta la regeneración. Era por diseño
   (fuerza el `update-manifest`), pero el reporte debía incluirlo.
   **Propuesta para el template**: cuando el manifiesto es schema legacy, el
   mensaje de G-MUT-SITES debe decirlo explícitamente ("manifiesto schema 1:
   regenera con `wct mutate update-manifest`") en vez de solo "excede
   max_sites_per_file con funciones cambiadas" — el diagnóstico actual
   induce a creer que hay que partir el archivo.
3. **El guard se auto-demostró**: la salida del redteam (30/30) muestra los
   `WCT BLOCK` actuando, y la regla de evidencia en `--reason` se aplicó al
   primer bless real sin fricción (el mantenedor usó la URL de la PR).
4. **Secuencia rojo→verde en CI como diseño**: `quality` FAIL 8s pre-bless
   → PASS 27s post-bless, con el motor nuevo validando en CI sus propios
   hashes EOL. G-META-1 remoto + integrity EOL + guard de agente = la
   gobernanza del harness queda con enforcement en las tres capas (local,
   agente, remoto).
5. **Flujo "se arregla en el template, se porta al piloto" validado**: 54
   tests netos nuevos (suite 1105), cero regresiones. La doble verificación
   (template verde en su casa + suite del piloto) bastó; no hizo falta
   revisión línea por línea de los módulos portados, solo de los puntos de
   integración conocidos.
6. **Matiz honesto sobre el churn de `.secrets.baseline`**: el fix
   `_audited_secrets` cubre el GATE (G-SECRET ya no reescribe el baseline),
   pero ningún hook del repo regeneraba el baseline en commits (`.git/hooks`
   solo tiene samples y `.pre-commit-config.yaml` no incluye detect-secrets;
   los abortos de fases 20–22 venían de ejecuciones manuales de agentes).
   El runbook actualizado debe leerse como "el gate ya no lo ensucia", no
   como garantía sobre herramientas externas al repo.

## Ejecución del coder

### 1. Inventario de sincronización

| Categoría | Archivo | Acción realizada |
|---|---|---|
| Reemplazo directo | `tools/wct/mutate/engine.py` | Sincronizado desde template: AST fingerprinting (`_fingerprint`), soporte schema 2, `update_manifest` atómico con `--approved-by/--reason`. |
| Reemplazo directo | `tools/wct/accept/pipeline.py` | Sincronizado desde template: mensajes enriquecidos en `ir_dry` (`placeholder-variant` indicando paso y escenarios en colisión). |
| Módulo nuevo | `tools/wct/fmt/engine.py`, `__init__.py` | Añadido módulo `fmt`: comando `wct fmt` con `--staged` / `--diff-only` restringido al changeset. |
| Reemplazo directo | `tools/wct/util/git.py` | Sincronizado desde template: función `staged_files` añadida. |
| Reemplazo directo | `tools/wct/integrity/engine.py` | Sincronizado desde template: algoritmo `sha256:eol-normalized`, validación `require_approval_evidence` (URL o #N obligatorios en `--reason`). |
| Reemplazo directo | `tools/wct/archmetrics/analyzer.py` | Sincronizado desde template: `_dynamic_imports` detector anti-evasión de ciclos. |
| Reemplazo directo | `tools/wct/hooks/guard.py` | Sincronizado desde template: extendida normalización de invocaciones de módulo (`python -m tools.wct`, `uv run python -m tools.wct`). |
| Merge cuidadoso | `tools/wct/gate/runner.py` | Incorporada función `_audited_secrets` (lectura de `.secrets.baseline` en solo-lectura sin `--baseline`); conservado el cableado de gates del proyecto (`ruff`, `mypy src`, `pytest -q`, `deptry src`, whitelist `vulture`, etc.). |
| Merge cuidadoso | `tools/wct/cli.py` | Añadido subcomando `fmt`, soporte de `--approved-by/--reason` en `mutate update-manifest`, conservados avisos de `integrity check`. |
| Mantenido piloto | `tools/wct/selftest/redteam.py` | Conservado `SECRET_PATTERN` para evitar falsos positivos con el escáner de artefactos del proyecto. |
| Mantenido piloto | `tools/wct/__init__.py` | Conservada versión local del piloto (`0.1.0`). |
| Mantenido piloto | `doctor/checks.py`, `dry/analyzer.py`, `ratchet/*.py`, `report/render.py`, `webhook.py` | Conservadas versiones locales (diffs cosméticos no portados). |

### 2. Tests del harness portados al layout del piloto

Se adaptaron los tests del harness a `tests/test_wct_*.py`:
- `tests/test_wct_hooks.py` (12 tests): validación de `pre_tool_use`, bloqueo de `--no-verify`, escrituras protegidas, intentos de auto-bless en todas sus variantes (`wct`, `python -m tools.wct`, `uv run python -m tools.wct`, `python3`), auto-aprobación en `update-manifest`, y permiso explícito a `integrity check` y `update-manifest` plano.
- `tests/test_wct_integrity.py` (13 tests): clasificación de drift, comportamiento de rutas no versionadas (warnings vs violaciones), fail-closed sin git, inmunidad EOL a `\r\n`, compatibilidad con locks legacy, validación de evidencia en `--reason`, y salida CLI de `integrity check`.
- `tests/test_wct_mutate.py` (11 tests): conteo de mutation sites, inmunidad de fingerprint AST ante desplazamientos de líneas y comentarios, invalidación por cambio de cuerpo, cualificación por clase, detección de manifiesto legacy (schema 1), `update_manifest` atómico con bless, y gate diferencial `G-MUT-SITES`.
- `tests/test_wct_fmt.py` (4 tests): restricción de formateo a archivos staged, changeset de trabajo, omisión sin archivos python, y error ante ausencia de ruff.
- `tests/test_wct_gate_secrets.py` (5 tests): parseo tolerante a `--slim`, lectura sin mutar `.secrets.baseline`, exclusión de hallazgos auditados y detección de secretos nuevos.
- `tests/test_wct_archmetrics.py` (10 tests): validación de Dependency Rule, ciclos en runtime vs `TYPE_CHECKING`, allowlist de ciclos, y detección de imports dinámicos no permitidos u opacos.
- `tests/test_wct_accept.py` (3 tests): parseo de IR canónico, detección de formas duplicadas, y mensajes enriquecidos de colisión `placeholder-variant`.

### 3. Puntos de integración resueltos

1. **Regex del guard y normalización de comandos**: `_normalize_module_invocation` en `tools/wct/hooks/guard.py` normaliza `python(3)? -m tools.wct ...` y `uv run python -m tools.wct ...` a `wct ...`, permitiendo que todos los patrones de `PROHIBITED_BASH` capturen intentos de auto-blessing sin importar cómo se invoque el módulo. Se verificó que `integrity check` y `update-manifest` sin `--approved-by` nunca son bloqueados.
2. **Anti-evasión `importlib` en `archmetrics`**: La ejecución de `archmetrics` identificó los imports dinámicos deliberados en `personal_assistant.infrastructure.config_settings` (fase 16) y `personal_assistant.evals.runner`. Se añadieron las excepciones documentadas a `governance/thresholds.yaml → cycle_allowlist`. Con esto, `uv run python -m tools.wct archmetrics` pasa con 0 violaciones.
3. **Wiring del hook en `.claude/settings.json`**: Se confirmó que `.claude/settings.json` ya apunta a `uv run --project "${CLAUDE_PROJECT_DIR}" python -m tools.wct hook ...`, activando directamente el nuevo guard. No requiere cambios.
4. **Manifiesto Schema 2**: El motor de mutación genera manifiestos con schema 2. La regeneración atómica queda preparada para el bless del mantenedor.

### 4. Actualización del manual del mantenedor

Se añadió la sección `## 12. Operaciones del harness WCT (Mantenedor)` a `docs/development/maintainer-workflow.md`, detallando:
- Requisito de evidencia explícita en `--reason` (URL o #N) para `integrity bless`.
- Comando atómico `mutate update-manifest --approved-by ... --reason ...`.
- Fin del churn de `.secrets.baseline` gracias a la lectura en solo-lectura de `G-SECRET`.
