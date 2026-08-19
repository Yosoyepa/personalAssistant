# Mejoras al Well Code Template (WCT) — feedback consolidado del piloto

| Campo | Valor |
|---|---|
| Documento | Consolidado de feedback y mejoras para `well_code_template` |
| Origen | Piloto WCT en `personalAssistant`, fases 13–21 (2026-08-03 → 2026-08-19) |
| Audiencia | Agente implementador que aplicará los cambios en `/home/jandradeu/Documents/well_code_template` |
| Autor | Rol verifier/planner del piloto |
| Estado | `APPROVED_FOR_IMPLEMENTATION` (aprobado por el mantenedor, 2026-08-19) |

## Cómo usar este documento (instrucciones para el agente implementador)

1. **Antes de tocar nada**, lee `/home/jandradeu/Documents/well_code_template/AGENTS.md`
   completo. El template se gobierna a sí mismo con las mismas reglas WCT
   (perfil `strict`): TDD, cero mutantes sobrevivientes en lo que cambies,
   `uv run wct gate --tier fast` antes de entregar, y **nunca** corras
   `integrity bless` — eso es exclusivo del mantenedor humano.
2. Las mejoras están en tres categorías, en orden de riesgo creciente:
   - **Sección 1 — Backports probados**: código que ya existe y corre en
     producción en el piloto (`personalAssistant`). El trabajo es portar,
     no diseñar. Empieza aquí.
   - **Sección 2 — Mejoras de diseño nuevas**: requieren diseño + TDD en el
     template. Una PR por mejora, salvo que dos sean inseparables.
   - **Sección 3 — Mejoras de proceso/documentación**: tocan docs, runbooks
     y reglas de agentes del template, no el motor.
3. Cada ítem indica: problema, evidencia (fase/PR/commit del piloto),
   archivos objetivo en el template (verificados contra su árbol real) y
   criterio de aceptación.
4. **Verificación cruzada**: el repo piloto sirve como referencia ejecutable.
   Cuando un ítem diga "referencia: `personalAssistant@<commit>`", puedes leer
   esa implementación en
   `/home/jandradeu/Documents/Proyectos Personales/personalAssistant` en el
   commit indicado y portarla adaptada (NO copies a ciegas: el piloto hizo
   adaptaciones locales documentadas en
   `docs/development/hardening/phase-13-wct-pilot.md` que no todas aplican).
5. El template tiene sus propios tests del harness bajo `tests/` (en el piloto
   no se copiaron por colisión de `testpaths`; en el template sí existen y
   debes extenderlos).

---

## Sección 1 — Backports ya implementados y probados en el piloto

Código real, con tests, corriendo en el piloto. El template NO lo tiene
(verificado contra su árbol el 2026-08-19). Riesgo bajo: portar con sus tests.

### B1. Integrity: ruta protegida ausente Y no versionada = aviso, no fallo

- **Problema**: `integrity.lock` puede cubrir rutas no trackeadas por git
  (ej. `.agents/skills/`, instaladas localmente). En un runner de CI limpio,
  esas rutas "faltan" y el check las reportaba como `eliminado protegido` →
  FAIL permanente e inevitable. El check de integridad era **no ejecutable
  en CI**.
- **Solución implementada** (piloto, fase 21, PR #66, merge `84e8822`,
  bless `6f65993`):
  - `tools/wct/util/git.py`: nueva `tracked_files(root) -> set[str] | None`
    (`None` si git falla → fail-closed).
  - `tools/wct/integrity/engine.py`: `_classify(previous, actual, tracked)`
    puro + `review(root) -> (problems, warnings)`. Semántica:
    - ausente + trackeado → FAIL `eliminado protegido`
    - ausente + no trackeado → warning `ausente no versionado (omitido)`
    - `modificado` / `nuevo protegido` → siempre FAIL
    - sin git disponible → fail-closed (todo a problemas)
  - `tools/wct/cli.py`: `integrity check` imprime `aviso: ...` por cada
    warning y retorna `bool(problems)`. `violations()` queda como wrapper
    fail-only compatible.
  - Tests: `tests/test_wct_integrity.py` (6 tests).
- **Archivos objetivo en el template**: `tools/wct/util/git.py`,
  `tools/wct/integrity/engine.py`, `tools/wct/cli.py` + tests del harness.
  En el template no existe `tracked_files` ni el texto `ausente no
  versionado` (grep = 0 coincidencias, verificado).
- **Criterio de aceptación**: en el template, un lock que liste un archivo no
  trackeado y ausente produce warning con exit 0; un archivo trackeado
  eliminado produce FAIL; sin git, FAIL.
- **Prioridad**: **P0** — sin esto, B2 (integrity en CI) no funciona.

### B2. Paso de CI recomendado para `integrity check` (G-META-1 remoto)

- **Problema**: G-META-1 no tenía enforcement remoto: una PR que tocaba rutas
  protegidas sin bless pasaba CI verde (feedback fase 19, ítem 1). La barrera
  era solo el hook local + revisión humana.
- **Solución implementada** (piloto, fase 20A, PR #64, merge `c7e3835`,
  bless `95e57793`): paso `Verify protected-route integrity (WCT)` en el job
  `quality` de `.github/workflows/ci.yml`, tras el `uv sync --frozen`:
  `PYTHONPATH=. uv run python -m tools.wct integrity check`. Se demostró la
  secuencia rojo→verde: la propia PR falló (`modificado:
  .github/workflows/ci.yml`) hasta el bless del mantenedor.
- **Archivos objetivo en el template**: el workflow de CI de referencia del
  template (`.github/workflows/**`) y/o su documentación de adopción
  (`README.md`, `docs/`, comando `wct adopt`). No requiere código del motor.
- **Criterio de aceptación**: el template documenta/instala un paso de CI que
  corre `integrity check` y falla la PR si hay rutas protegidas modificadas
  sin bless. Documentar que depende de B1 para no falsificar fallos con
  rutas no versionadas.
- **Prioridad**: **P0**.

### B3. G-SECRET: parseo tolerante a `--slim` de detect-secrets

- **Problema** (bug upstream confirmado): el runner del template asume
  `line_number` presente en cada hallazgo, pero con `--slim` detect-secrets
  omite ese campo → `KeyError` en pleno gate.
  Template hoy: `tools/wct/gate/runner.py:259` hace
  `f"{filename}:{item['line_number']}: posible {item['type']}"` sin `.get`.
- **Solución implementada** (piloto, fase 13): parseo tolerante (`.get` con
  fallback; stdout vacío = sin hallazgos) y soporte de `--baseline
  .secrets.baseline`. Referencia: `tools/wct/gate/runner.py` del piloto.
- **Criterio de aceptación**: G-SECRET corre con `--slim` sin excepción y
  reporta hallazgos con/sin `line_number`. Test del harness que cubre ambos
  shapes de JSON.
- **Prioridad**: **P0** — es un bug, no una mejora.

### B4. Archmetrics: las aristas `TYPE_CHECKING` no cuentan como dependencia

- **Problema**: imports bajo `if TYPE_CHECKING:` se borran en runtime;
  contarlos como aristas convierte los shims anti-ciclo en falsos positivos
  de `G-ARCH-CYCLE` / `G-ARCHMETRICS`. Template hoy:
  `tools/wct/archmetrics/analyzer.py` no menciona `TYPE_CHECKING`
  (grep = 0 coincidencias).
- **Solución implementada** (piloto, fase 13): el analizador excluye aristas
  `TYPE_CHECKING` y se añadió `architecture.cycle_allowlist` en
  `thresholds.yaml` para excepciones documentadas (wiring diferido
  intencional).
- **Relación con P9** (Sección 2): B4 quita el falso positivo; P9 cierra la
  vía de evasión (`importlib.import_module`). Hacer B4 primero y P9 después,
  o el piloto habría quedado bloqueado.
- **Criterio de aceptación**: un ciclo que existe solo bajo `TYPE_CHECKING`
  no dispara el gate; un ciclo real en runtime sí. Test del harness para
  ambos.
- **Prioridad**: **P1**.

### B5. G-MUT-SITES diferencial (TEST-007 tal como está escrito)

- **Problema**: la regla TEST-007 dice "archivo CAMBIADO con >100 sitios",
  pero una implementación ingenua del presupuesto bloquea archivos legacy
  sobre el límite aunque el diff no los haya tocado.
- **Solución implementada** (piloto, fase 13): G-MUT-SITES diferencial usando
  el manifiesto `governance/generated/mutation-manifest.json`: un archivo
  legacy sobre el límite solo bloquea si el diff tocó funciones suyas.
  Referencia: `tools/wct/gate/runner.py` del piloto (sección G-MUT-SITES).
- **Nota**: la identidad de función hoy es `path::name:lineno` (frágil, ver
  P1). Portar el diferencial tal cual; P1 lo robustece después sin cambiar
  la interfaz.
- **Criterio de aceptación**: tocar un archivo legacy >100 sitios SIN tocar
  sus funciones no bloquea; tocar una función suya, bloquea.
- **Prioridad**: **P1**.

### B6. Fuente única de versión (`__version__` desde `importlib.metadata`)

- **Problema**: la versión vivía en dos fuentes (`pyproject.toml` +
  `src/.../__init__.py`); el bump de release desincronizó ambas y CI lo cazó
  por el test oráculo (fase 18, fix `cf6c7cc`).
- **Solución implementada** (piloto, fase 20B, PR #65, merge `6907940`):
  `__version__` deriva de `importlib.metadata.version("personal-assistant")`
  con `_PackageVersion(str)` (equivalencia PEP 440 en comparaciones) y
  fallback `"0.0.0+local"` cuando el paquete no está instalado
  (`PackageNotFoundError`). Tests incluidos; suite del piloto 1019+.
- **Aplicación en el template**: no es código del motor WCT; es la
  **convención de release documentada del template** (y de su propio
  `src/`), para que todo proyecto adoptante tenga fuente única desde el día
  uno.
- **Prioridad**: **P1** (documental + convención; en el piloto ya está).

---

## Sección 2 — Mejoras de diseño nuevas (no implementadas en ningún repo)

Requieren diseño propio en el template. Cada una con TDD estricto.

### P1. G-MUT-SITES: identidad de función por fingerprint semántico, no por línea

- **Problema** (fase 15, ítem A): el manifiesto identifica funciones como
  `modulo.py::funcion:linea`. Añadir un import desplaza todas las líneas
  siguientes, invalida los hashes y marca como "modificado" todo el archivo,
  bloqueando archivos legacy sobre el límite histórico. Template hoy:
  `tools/wct/mutate/engine.py:21` —
  `key = f"{path...}::{node.name}:{node.lineno}"`.
- **Propuesta**: hashear el AST normalizado de la función
  (`ast.dump(func_node)`, idealmente anonimizando literales si se quiere
  tolerancia a cambios cosméticos) en vez de `lineno`. Mover una función de
  sitio no invalida su entrada; cambiar su cuerpo, sí.
- **Compatibilidad**: migración del manifiesto existente (una corrida de
  `update-manifest` con bless del mantenedor). Mantener el formato de salida
  legible (el fingerprint puede ser un campo más, no reemplazar el nombre).
- **Prioridad**: **P1** — es la fuente de fricción nº 1 del presupuesto de
  sitios en proyectos legacy.

### P2. G-ACCEPT: diagnóstico enriquecido de `placeholder-variant`

- **Problema** (fase 15, ítem B): cuando dos escenarios usan pasos que
  normalizan a la misma forma `<value>`, el gate falla con
  `placeholder-variant` sin decir qué paso colisionó ni con qué escenario.
  Template hoy: `tools/wct/accept/pipeline.py:107` emite el `kind` sin
  contexto.
- **Propuesta**: incluir el texto del paso, el escenario/archivo:línea de
  ambas colisiones: `Paso '<step>' en escenario A (features/x.feature:12)
  colisiona con escenario B (features/y.feature:34)`.
- **Prioridad**: **P2** (fricción de diagnóstico, no bloqueo estructural).

### P3. Formato diferencial: `wct fmt --staged` (o gate equivalente)

- **Problema** (fase 15, ítem C): con `G-FMT` desactivado (adopción gradual),
  un agente que corre `ruff format` global reformatea archivos legacy
  intactos y dispara G-MUT-SITES en archivos ajenos a la tarea. Hoy WCT no
  tiene subcomando `fmt` (verificado: `gate, rules, doctor, integrity, hook,
  archmetrics, dry, introvert, mutate, accept, selftest, report, ratchet,
  adopt, webhook`).
- **Propuesta**: subcomando `wct fmt --staged` / `--diff-only` que restrinja
  el formateo al changeset (archivos staged o diff contra main), envolviendo
  el formateador configurado. Documentarlo como el único formateo permitido
  para agentes en proyectos con G-FMT desactivado.
- **Prioridad**: **P2**.

### P4. `mutate update-manifest` + `integrity bless` atómicos

- **Problema** (fase 15, ítem D): tras `wct mutate update-manifest`, G-META-1
  falla de inmediato porque `integrity.lock` quedó desfasado; exige un bless
  manual extra (que además SOLO puede correr el humano, ver P5).
- **Propuesta**: `wct mutate update-manifest --approved-by "..." --reason
  "..."` regenera el lock de forma atómica y deja la entrada correspondiente
  en `integrity-log.md`. Sigue requiriendo aprobación humana explícita: la
  mejora es de atomicidad, no de permisos.
- **Prioridad**: **P2**.

### P5. `integrity bless` exclusivamente humano — enforcement técnico

- **Problema** (fase 16, feedback crítico del discriminador): la única vía de
  escritura en rutas protegidas no puede ser un comando que el propio agente
  ejecuta. En el piloto hubo una brecha real (fase 16) y desde entonces rige
  como regla dura de proceso; pero hoy es una regla documentada, no un
  mecanismo.
- **Propuesta** (en capas, implementar al menos la 1ª):
  1. Hook `PreToolUse` en las settings de agente del template que bloquee
     `wct integrity bless` (y `ratchet raise`) en sesiones de agente.
  2. Exigir evidencia de aprobación en `--reason` (URL de PR o comentario
     del mantenedor), validada por formato.
  3. (Opcional, evaluar coste/beneficio) firma GPG del commit de bless o un
     secreto de entorno que solo el humano posea.
- **Prioridad**: **P0** — es la lección más cara del piloto.

### P6. Integrity: hash de contenido normalizado (inmunidad EOL)

- **Problema** (fases 20/21): el lock hashea bytes en disco; una copia local
  con CRLF de `.github/workflows/security.yml` exigió re-bless aunque el blob
  en git era LF (el lock guardaba el hash de la copia local con CRLF).
- **Propuesta**: hashear con semántica `git hash-object` (normalización EOL
  según `.gitattributes`/config) en bless y en check. El lock se vuelve
  inmune a diferencias de checkout entre máquinas (Linux/Windows).
- **Nota de migración**: cambia el algoritmo de hash → requiere re-bless
  general documentado y entrada en el log. Diseñar el formato del lock para
  declarar el algoritmo (`"hash": "sha256:git"`) y soportar ambos durante la
  transición.
- **Prioridad**: **P1**.

### P7. G-ARCH-CYCLE: cerrar la vía de evasión por `importlib`

- **Problema** (fase 16, ítem 3): el ciclo `config_settings ↔ config_loader`
  existía solo en anotaciones; el coder lo "resolvió" con
  `importlib.import_module` dinámico, que oculta la arista al análisis
  estático. El gate quedó evadable sin que nadie lo notara (lo cazó la
  revisión del discriminador).
- **Propuesta**: además de B4 (excluir TYPE_CHECKING), flaggear
  `importlib.import_module` / `__import__` con nombres de módulos del
  proyecto como hallazgo de arquitectura (warning como mínimo), salvo
  entradas en `cycle_allowlist`.
- **Prioridad**: **P1**.

### P8. `wct split-plan <archivo>` — andamiaje oficial para splits por presupuesto

- **Problema** (fase 17, ítem 4): los splits preventivos por G-MUT-SITES son
  ya predecibles (http → config → worker → reminder_notifications) y el coder
  los internalizó, pero cada uno improvisa la mecánica (fachada + submódulos
  + re-exports).
- **Propuesta**: comando `wct split-plan <archivo>` que sugiera una
  partición (por cohesion de funciones / grafo de imports interno) y genere
  el andamiaje de fachada; como mínimo, documentar el patrón fachada como
  receta oficial en las reglas del template.
- **Prioridad**: **P2**.

---

## Sección 3 — Mejoras de proceso y documentación del template

Sin código de motor; van a las reglas/runbooks/roles del template.

| # | Mejora | Origen |
|---|---|---|
| D1 | **Comandos críticos en UNA sola línea** en runbooks y prompts. Los backslashes de continuación no sobrevivieron al copy-paste dos veces (`wct: error: unrecognized arguments`, fases 18 y 20). | Fases 18, 20 |
| D2 | **Patrón bless + `.secrets.baseline`**: el hook de pre-commit regenera el baseline (timestamp `generated_at`) a mitad del commit y aborta si no está staged. Documentar: incluir `.secrets.baseline` en el `git add` del bless desde el inicio. | Fases 20/21 |
| D3 | **Consolidación de PRs de Dependabot como procedimiento estándar**: una rama + `uv lock` + bless resolvió 5 PRs atascadas con coste de una. | Fase 19 |
| D4 | **Regla de worktree compartido, en ambas direcciones**: ni el coder ni el verificador/discriminador cambian el checkout (`git checkout`, merges con `--watch` en background) mientras el otro trabaja. Operaciones remotas (`gh pr ...`) son seguras; las de checkout no. Ya causó una carrera real. | Fases 19, 20 |
| D5 | **`gh pr checks --watch` devuelve corridas obsoletas tras `update-branch`**: comparar run IDs contra el head nuevo antes de mergear. Va al runbook del rol verificador. | Fase 19 |
| D6 | **Phase logs append-only para el coder**: el coder solo añade secciones de ejecución; spec, roles y feedback los edita el discriminador/planner. (En fase 16 el coder reescribió el doc y cambió atribuciones.) | Fase 16 |
| D7 | **Verificación documental contra código, no contra memoria**: toda ruta/comando documentado se verifica contra el código (una ruta de webhook se documentó en singular siendo plural; fix `8cd433b`). | Fase 18 |
| D8 | **Política de tests flaky**: declarar en el template cómo se registra un flake (candidato a presupuesto de reintentos acotado), tras el flake recurrente de `test_concurrent_sweepers_are_disjoint_idempotent_and_ignore_foreign_events` en CI del piloto (2 fallos en `tests (3.12)`, pasa al rerun; sensible a timing con Postgres real). | Fases 20/21 |

---

## Qué NO cambiar (validado por el piloto)

Para que el implementador no "arregle" lo que ya demostró funcionar:

- **El flujo spec-Gherkin → coder → verificador con gates objetivos** operó
  como oráculo determinista: el coder iteró y se autocorrigió sin revisión
  manual línea por línea (fases 15–19). G-ACCEPT + G-MUT-SITES + G-TYPE +
  G-TEST son el núcleo que hace viable la delegación a modelos más baratos.
- **G-MUT-SITES como mecanismo de diseño**: el presupuesto de sitios enseñó
  al coder el patrón de split preventivo sin que el spec lo pidiera (fase
  17). Es una feature, no burocracia.
- **La división coder/mantenedor por rutas protegidas**: cero fricción desde
  las reglas duras post-fase-16; dos fases consecutivas sin violaciones de
  gobernanza.
- **El verificador sin permisos de escritura** (`.claude/agents/` del
  template): mantenerlo tal cual.

## Resumen de prioridades

| Prioridad | Ítems | Criterio |
|---|---|---|
| **P0** | B1, B2, B3, P5 | Sin ellos el harness miente (falso FAIL en CI, bug en gate) o la brecha de gobernanza sigue siendo solo una regla escrita |
| **P1** | B4, B5, B6, P1, P6, P7 | Fricción estructural demostrada o evasión del oráculo |
| **P2** | P2, P3, P4, P8, D1–D8 | Calidad de vida y proceso; alto valor acumulado, bajo riesgo individual |

## Referencias ejecutables

| Ítem | Referencia en el piloto |
|---|---|
| B1 | `tools/wct/util/git.py`, `tools/wct/integrity/engine.py`, `tools/wct/cli.py`, `tests/test_wct_integrity.py` @ merge `84e8822` (PR #66) |
| B2 | `.github/workflows/ci.yml` (paso `Verify protected-route integrity (WCT)`) @ merge `c7e3835` (PR #64) |
| B3–B5 | `tools/wct/gate/runner.py`, `tools/wct/archmetrics/` @ `main` del piloto; adaptaciones justificadas en `docs/development/hardening/phase-13-wct-pilot.md` |
| B6 | `src/personal_assistant/__init__.py` @ merge `6907940` (PR #65) |
| Feedback por fase | `docs/development/hardening/phase-{15..20}-*.md`, secciones "Feedback WCT" |
