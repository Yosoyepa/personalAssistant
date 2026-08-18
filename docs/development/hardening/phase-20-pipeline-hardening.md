# Phase 20 — Pipeline hardening (integrity en CI + fuente única de versión)

| Campo | Valor |
|---|---|
| Fase | `20 — pipeline hardening` |
| Estado | `SPEC_APPROVED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | rama coder (20B), rama discriminador (20A) |
| Fecha de inicio | `2026-08-18` |
| PR | pendiente |
| Merge commit | pendiente |

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

(pendiente — se llena al cierre por el discriminador)
