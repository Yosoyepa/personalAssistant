# Phase 20 — Pipeline hardening (integrity en CI + fuente única de versión)

| Campo | Valor |
|---|---|
| Fase | `20 — pipeline hardening` |
| Estado | `COMPLETED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `gemini/phase-20-single-version-source` (20B), `kimi/phase-20-integrity-ci` (20A), `kimi/phase-21-integrity-untracked` (21) |
| Fecha de inicio | `2026-08-18` |
| PR | #63 (spec), #64 (20A), #65 (20B), #66 (21) |
| Merge commit | `6907940` (20B), `84e8822` (21), `c7e3835` (20A) |

## Objetivo

Cerrar los dos hallazgos principales del feedback WCT de las fases 18–19:

- **20A — G-META-1 sin enforcement remoto**: añadir `wct integrity check` al
  job `quality` de CI, para que una PR que modifique rutas protegidas sin
  bless falle en remoto y no dependa solo del hook local + revisión humana.
- **20B — Versión en dos fuentes**: derivar `__version__` de
  `importlib.metadata` para que el próximo bump de release toque UN solo
  archivo (`pyproject.toml`). Origen del hallazgo: desincronización cazada por
  CI en la fase 18 (fix `cf6c7cc`).

## División de trabajo

### Coder — 20B fuente única de versión (rama `gemini/phase-20-single-version-source`)

1. `src/personal_assistant/__init__.py`: `__version__` se deriva de
   `importlib.metadata.version("personal-assistant")`.
2. Caso borde obligatorio: paquete NO instalado (`PackageNotFoundError`, p.ej.
   `PYTHONPATH=src` sin `uv sync`) — fallback documentado y con su propio
   test.
3. TDD: primero el test que expresa el comportamiento; el test existente
   `test_runtime_and_package_versions_share_the_project_source`
   (`tests/test_operational_readiness.py`) debe seguir verde sin modificarse.
4. Restricciones duras: NO tocar `pyproject.toml`, `uv.lock`, `governance/**`,
   `.github/**`; prohibido `integrity bless`. Solo el archivo de código y sus
   tests.

### Discriminador — 20A integrity check en CI (rama `kimi/phase-20-integrity-ci`)

1. `.github/workflows/ci.yml`, job `quality`, paso nuevo tras "Validate
   repository configuration": `PYTHONPATH=. uv run python -m tools.wct integrity check`.
2. Ruta protegida → tras verificar la PR, el **mantenedor** corre el bless en
   su terminal (regla dura post-fase-16).
3. Propiedad auto-demostrativa: la propia PR modifica un archivo protegido,
   así que su primera corrida de CI debe FALLAR en el paso nuevo; tras el
   bless y el push, debe pasar. Esa secuencia rojo→verde es la evidencia de
   que el gate remoto funciona.

## Criterios de salida

- CI falla ante una modificación no bendecida de rutas protegidas
  (demostrado con la propia PR de 20A).
- `__version__` deriva de la metadata instalada; el test de
  operational-readiness sigue verde; el fallback tiene test.
- Suite completa verde (≥1015 passed / 3 skipped / 396 subtests más los tests
  nuevos de 20B) y `wct gate --tier commit` 17/17 en `main` tras ambos merges.

## Riesgos y notas

- El paso de CI corre en el job `quality`, que ya hace `uv sync --frozen`:
  coste añadido despreciable; no requiere Postgres ni secretos.
- `wct integrity check` es el subcomando correcto (no `verify`).
- Tras 20B, los procedimientos de release documentados en `docs/releases/`
  asumen fuente única: el bump futuro toca solo `pyproject.toml` + `uv lock`.

## Feedback WCT de la fase

Cerrado por el discriminador tras los merges de 20A/20B y la fase 21
(prerrequisito descubierto en 20A). Blesses del mantenedor: `6f65993`
(fase 21) y `95e57793` (20A).

1. **integrity.lock cubría rutas no versionadas → check no ejecutable en CI**
   (fase 21, PR #66). El lock incluía archivos bajo `.agents/skills/` que no
   están trackeados por git; en un runner limpio el check los marcaba
   `eliminado protegido` y fallaba siempre. Fix: `integrity review()` separa
   problemas (bloquean) de avisos (`ausente no versionado (omitido)`), con
   fail-closed si git no responde. **Llevar este comportamiento al template
   WCT** (`~/Documents/well_code_template`): cualquier instalación nueva del
   harness con rutas protegidas no versionadas heredará el falso positivo.
2. **Propuesta (no implementada): hash de contenido normalizado.** El lock
   hashea bytes en disco; una copia local con CRLF de
   `.github/workflows/security.yml` exigió re-bless aunque el blob en git era
   LF. Hashear con semántica `git hash-object` (o normalizando EOL) haría al
   lock inmune a diferencias de checkout. Candidato para el template.
3. **Flake `test_concurrent_sweepers_are_disjoint_idempotent_and_ignore_foreign_events`**
   (`tests/test_delivery_adversarial_postgres.py`): falló dos veces seguidas en
   `tests (3.12)` ("sweeper did not converge after conflicts") y pasó en 3.11
   del mismo run; es sensible al timing bajo contención real de Postgres en CI.
   Candidato a presupuesto de reintentos acotado o ajuste del presupuesto de
   convergencia del test. Si reaparece, merece fase propia.
4. **El hook de pre-commit aborta el primer commit de un bless**: regenera
   `.secrets.baseline` (churn de timestamp `generated_at`) a mitad del commit.
   Patrón operativo documentado: incluir `.secrets.baseline` en el `git add`
   del bless desde el inicio.
5. **Regla de worktree compartido aplica en ambas direcciones**: el
   discriminador tampoco debe cambiar el checkout (`git checkout`) mientras el
   coder trabaja; ya causó una carrera en fases anteriores. Operaciones
   remotas (`gh pr ...`) son seguras; las de checkout, no.
6. **Comandos de bless para el mantenedor, en una sola línea**: los backslashes
   de continuación rompieron la primera entrega del comando (`wct: error:
   unrecognized arguments`). Entregar comandos copy-paste sin continuaciones.
